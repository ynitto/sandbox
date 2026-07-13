# s6 調査結果: repos.json の owns/repo-dir 扱い、および --repos / --repo-dir 生成方法

## 結論（要約）

- `owns` は **codd-gate には渡らない・codd-gate 内でも読まれない**。純粋に kiro-project 側の
  ルーティング根拠（タスク→書込先ワークスペース決定）であり、codd-gate.py では `Repo.owns` に
  格納されるだけで再読されない（`grep '\.owns\b' codd-gate.py` は代入行1件のみ）。
- `--repos` の値は **常に `cfg.backlog.parent / "repos.json"`**（= kiro-project の `<root>`。
  `repo_registry_path()` と `export_repo_registry()` の既定パスが一致している）。
- `--repo-dir <name>=<dir>` の値は **常に `<repos.json のキー名>=.`**。regression_cmd /
  intake_cmd / acceptance はいずれも「解決済みワークスペースのクローンのルート」を cwd として
  実行される規約（`_task_verify_cwd`）なので、dir は常に `.` でよく、絶対パスを焼き込む必要はない。
- ただし `.` が機能するのは **`--repos` の指す repos.json が、verify 実行時の cwd（クローン
  ルート）の中に実在するとき** だけ。これは repos.json が対象リポジトリ自身に git 追跡されている
  「self-hosted / direct state-git」構成でのみ自動的に成立する（後述）。

現在 `.kiro-project/repos.json` はコミット `645d86f`（"Remove status.json configuration file
from the project"）で誤って `.kiro-project/` ディレクトリ全体ごと削除されている。これが
`codd-gate verify --repos ./.kiro-project/repos.json ...` が exit=2 で失敗する直接原因（後述の
検証結果）。**このファイルの復元は本タスク（s6・調査専任）の範囲外**とし、b2/synth 側での対応に
委ねる（見つけた問題として明記）。

---

## 1. repos.json スキーマにおける owns / dir / repo-dir の役割分担

出典: `schemas/repos.schema.json`, `tools/codd-gate/codd-gate.py`, `tools/kiro-project/kiro-project.py`

| フィールド | 誰が書く | 誰が読む | 役割 |
|---|---|---|---|
| `owns`（globs） | charter `## repos` の `- owns:` / repos.json 手書き | **kiro-project のみ**（`_owns_infer` / `_owns_matches` / `resolve_workspace`） | タスクが触るパスから書込先ワークスペースを決定論的に推定する根拠。無指定＝参照リポジトリ（読むだけ・書込先候補にしない、`_is_reference_repo`） |
| `dir`（string） | repos.json 手書きのローカル運用ヒント | codd-gate `_repos_from_data`（`s.get("dir")`） | ローカル checkout パスの**ヒント**。**CLI `--repo-dir` が常に優先**（`repo_dirs.get(name) or Path(s["dir"])`） |
| CLI `--repo-dir NAME=DIR` | 呼び出し側（regression_cmd/intake_cmd/acceptance の組み立て） | codd-gate `load_repos`/`_parse_repo_dirs` | 実行時にディレクトリを確定させる最終手段。名前一致で repos.json の該当エントリの `dir` を上書きする |

`codd-gate.py` 内で `owns` を検索すると以下の1件のみ（`_repos_from_data` での `Repo` 生成時の
代入）で、以降どこからも読まれない:

```
135:        self.owns = owns or []
192:            owns=_globs_value(s.get("owns")),
```

→ **owns は codd-gate の判定ロジック（scan/impact/verify/tasks）に一切影響しない。**
codd-gate 視点では `docs:` / `tests:` / `code:`（分類グロブの上書き）だけが効くフィールドで、
`owns` は「kiro-project から見て repos.json のこのエントリがどのタスクの書込先になるか」だけを
決める。

## 2. `--repos` パスの生成方法

`tools/kiro-project/kiro-project.py`:

```python
def repo_registry_path(cfg: "Config") -> "Path | None":
    base = cfg.backlog.parent
    for name in REPOS_FILE_NAMES:            # repos.yaml / repos.yml / repos.json
        p = base / name
        if p.is_file():
            return p
    return None

def export_repo_registry(cfg, specs, path=None):
    path = path or (cfg.backlog.parent / "repos.json")   # 既定の書き出し先も同じ
    ...
```

`cfg.backlog.parent` が kiro-project の実効ルート（`<root>`）で、`Config` に明示の `root`
フィールドは無く、`backlog: Path`（`<root>/backlog`）から逆算する規約に統一されている。
`repo_registry_path()`/`export_repo_registry()` は同じ既定パスを使っており、**手書きレジストリと
charter 自動生成レジストリのどちらでも `<root>/repos.json` に一元化**される
（`_registry_generated()` で自動生成物か手書きかを判定し、手書きが正）。

このプロジェクト（sandbox 自己ホスト構成）では `<root>` = `.kiro-project/`（sandbox リポジトリ
直下にコミットされた状態ディレクトリ）なので、`--repos` 値は必然的に `.kiro-project/repos.json`
になる。実際、削除前の実データでも手書きの `.kiro-project/repos.json` が存在していた
（`git show 321d5da:.kiro-project/repos.json`）:

```json
{
  "sandbox": {
    "base": "main",
    "desc": "ソースコード・設計書",
    "url": "https://github.com/ynitto/sandbox/",
    "owns": ["**"]
  }
}
```

`_meta.generated_from` マーカーが無い＝**手書き**。charter 側の `## repos` は空
（`.kiro-project/charter.md` の `## repos` に見出し行なし）なので、charter からの自動生成は
発動しておらず、このファイル単独が正だった。

**生成ロジックのまとめ（b2 実装向け仕様）**:

1. `rp = repo_registry_path(cfg)`。`None`（未生成）かつ charter に `repo_specs` があれば
   `load_charter(cfg)` を呼んで自動生成させてから再解決する（既存の `_apply_repo_registry` が
   charter ロードのたびに同期済みなので、通常は charter を読んだ時点で既に存在する）。
2. `--repos` の値は **`rp` を「verify 実行時の cwd（後述の vcwd）」からの相対パスに変換したもの**。
   `rp` が `vcwd` の配下にあれば相対（例 `./.kiro-project/repos.json`）、配下になければ
   `str(rp.resolve())`（絶対パス）にフォールバックする（§4 の理由）。

## 3. `--repo-dir` マッピングの生成方法

`regression_cmd` / task の `verify` は常に `_task_verify_cwd(cfg, task)` が返す `vcwd`
（＝解決済みワークスペースの**クローンのルート**）を cwd として実行される
（`tools/kiro-project/kiro-project.py:2782-2817`, 呼び出し元 `_settle_task:4931`）。
docstring 原文:

> cwd は常に **clone のルート**に取る。verify コマンドはリポジトリのルートからの相対
> （例 `cd api && yarn test`）で書かれる規約で、プランナーの生成指示・owns 突き合わせ・
> kiro-flow のワークスペースと一致する。

このワークスペース spec は `resolve_workspace(cfg, task, policy)` / 永続化済みなら
`_workspace_spec_for(cfg, task)` が返す `spec`（`spec["name"]` が repos.json のキーと一致）。

→ **`--repo-dir` は `<spec["name"]>=.` の1本で足りる。** vcwd 自体が対象リポジトリの
チェックアウトそのものなので、絶対パスを埋め込む必要がなく（埋め込むとクローン先が変わる
たびに壊れる）、これは設計書にも明記されている:

> `docs/designs/codd-gate-design.md:254-256`
> `$KIRO_BASE_REV` は kiro-project が verify / regression に渡す act 前 HEAD をそのまま使う。
> ワークスペース運用（別 repo clone 内での verify 実行）でも、タスク生成時に
> `--repo-dir <name>=.` を焼き込むことで clone 内で自己完結する。

複数リポジトリ構成で、当該タスクの書込先以外に参照専用（`owns` なし）リポジトリがある場合、
それらは `--repo-dir` を付けない（ローカル checkout を kiro-project が保証していないため）。
codd-gate 側は「未解決 repo」として `unscanned` に計上するだけで、黙って PASS 側に倒さない
（design doc 不変条件②）。`--sync` を使えば codd-gate 自身が mirror+worktree で実体化できるが、
それは kiro-project 側の生成ロジックの範囲外（codd-gate 側のオプション）。

**生成ロジックのまとめ（b2 実装向け仕様）**:

```
spec = resolve_workspace(cfg, task, policy)[0]  # 既に act 済みなら _workspace_spec_for(cfg, task)
name = spec.get("name") or spec["url"]
repo_dir_args = ["--repo-dir", f"{name}=."]
```

プロジェクト全体の acceptance（タスクに紐付かない場合）は、書込先候補が単一なら
`registry_specs(cfg, ch)` から `not _is_reference_repo(s)` でフィルタした唯一のエントリを使う
（このプロジェクトはまさにこのケース＝`sandbox` 1エントリのみ）。

## 4. 相対パスが機能する前提条件（self-hosted 構成に依存する非自明点）

`--repos ./.kiro-project/repos.json` が `vcwd`（対象リポジトリのクローンルート）から解決できる
のは、**`.kiro-project/` ディレクトリ自体がその対象リポジトリに git 追跡されているときだけ**。
`_task_verify_cwd` はワークスペース spec の `url` を毎回**新規にクローン**するため
（`_clone_repo_shallow`）、kiro-project の状態ディレクトリ（`cfg.backlog.parent`）が対象リポジトリの
外にある一般的な運用（例: 本タスクの実行環境そのもの — kiro-project 本体は
`/Users/.../sandbox-state/.kiro-project` にあり、ワーカーは別パスの worktree で作業する）では、
新規クローンの中に `.kiro-project/repos.json` は存在せず、相対パスは解決できない。

このプロジェクト（sandbox 自己ホスト）はこれが機能する特殊ケースで、`.kiro-project/` を
`.gitignore` していない（確認済み: `.gitignore` に `kiro-project` / `repos.json` の記述なし）
ことで、状態ディレクトリごと対象リポジトリにコミットし、新規クローンにも自動的に付いてくる
「direct state-git」構成を取っている（`Config.state_git` のコメント: 「ルート自体が git
クローンなら管理クローンを介さず直接コミット・push する（direct モード）」と符合）。

→ b2 の引数ビルダは、`rp`（repos.json の実パス）が `vcwd` の外にある場合は**絶対パスへ
フォールバックする分岐が必須**。相対パス固定だと self-hosted 以外の構成で `--repos` 解決が
壊れる。

## 5. 検証

- `python3 -m pytest tools/kiro-project/tests -q -k codd` → **exit 5**（`515 deselected`、
  `codd` を含むテスト名が現状ゼロ。テスト追加は t1〜t4 の担当）。
- `codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base
  "${KIRO_BASE_REV:-HEAD~1}" --strict` → **exit 2**
  （`[codd-gate] エラー: repos レジストリが見つかりません: .kiro-project/repos.json` —
  `.kiro-project/` ごと削除済みのため）。
- 上記2点は本タスク（investigation-only, `d1`/`d2`/`b2` の前段）の範囲外の実装欠落であり、
  「repos.json の owns/repo-dir 解釈」自体の疑義ではないことをサニティチェックで切り分け済み:
  一時ディレクトリに `{"sandbox": {"owns": ["**"], "url": "...", "base": "main"}}`
  （dir フィールドなし）を置き、sandbox チェックアウトを cwd にして
  `codd-gate verify --repos <tmp>/repos.json --repo-dir sandbox=. --base HEAD~1 --strict`
  を実行 → repos レジストリは正しく解決され、`sandbox:` 名前空間で scan/impact/verify が
  正常実行された（exit=1 は実リポジトリの既存ドキュメントドリフトによるもので、repos 解決とは
  無関係）。これにより §2・§3 の生成方法（`--repos <root>/repos.json` を相対/絶対で渡す、
  `--repo-dir <name>=.` を1本渡す）が実装として妥当であることを確認した。

## 6. 採用した前提・範囲外の指摘

- **前提**: 本タスク（s6, graph.json 上は依存ゼロで d1/d2/b2 が本タスクの結果に依存する
  investigation 専任ノード）は "調査・特定" が成果物であり、`.kiro-project/repos.json` の復元や
  引数ビルダの実装（b2）・regression 結線（b3）自体は行わない。ワークスペースへの変更は行って
  いない（git status クリーン）。
- **範囲外で見つけた問題**:
  1. `.kiro-project/` ディレクトリ（`repos.json` 含む）がコミット `645d86f`「Remove status.json
     configuration file from the project」で丸ごと削除されている。コミットメッセージは
     status.json 単体の削除を意図しているように読めるが、実際には backlog/charter/decisions/
     repos.json 等 40+ ファイルを含む状態ディレクトリ全体が消えており、意図と実態が食い違って
     いる可能性が高い。復元要否は synth/judge 側の判断に委ねる。
  2. `repos.json` の `owns: ["**"]` は `.kiro-project/**` 自体（backlog の md・journal 等）も
     codd-gate のスキャン対象に含めてしまう。`.kiro-project/backlog/*.md` は doc 扱いになり、
     状態ファイルが doc/code 分類ノイズを生む可能性がある（未検証・要 d1/gate での確認）。
