# 境界一致の敵対的検証（gate）

対象: t2/t3 の実装、t4/t5 の手順記述、t7 の設計書 §4.1
判定: **fail**（1件・doctor 導線の記述と実装の不一致）

## 判定の要点

README の一貫性ゲート節は「結線できているかは `doctor` が見る」と書くが、**書いてあるとおりに
設定しても doctor は何も出さない**。`hooks: {wiring: codd_gate_wiring}` を書いたときだけ到達する。
同じ run の t7 が設計書にこの条件を明記したのに、正本である README と GUIDE が追随していない。

## (a) 自動配線の残存 — pass

| 確認 | 結果 |
|---|---|
| `git grep -E '(import\|from) codd_gate\|_apply_codd_gate\|_codd_gate' -- agent_project` | 0 hit |
| README の `自動配線` / `build_config` | 0 hit |
| GUIDE の同語 | 0 hit |
| `_apply_codd_gate_auto_wiring` の残り | `tests/test_agent_project.py:4015` の不在アサーションのみ（回帰ガード。正しい） |
| 設計書:287 の言及 | 「以前は…削除した」という過去形の記述。現存機能としては書いていない |

`codd_gate_regression.py:32,204` / `codd_gate_routing.py:17` / `codd_gate_status.py:12` の
「自動配線」は、いずれも**不在を説明する**文脈。残存ではない。

## (b) README / GUIDE のコマンド実測 — 1件 fail

`/tmp/vfy` に `root:` と `agent_cli:` だけの yaml を用意して記載どおり実行した。

| 記述 | 実測 | 一致 |
|---|---|---|
| `--dry-run` は書かずに結果だけ | ファイル未変更・JSON 出力 | ○ |
| `--repos` 省略時は `root:` から推定 | `.agent-project/repos.json` | ○ |
| 冪等 upsert | 2回目 `changed:false`、rc=0 | ○ |
| 既存コメント保持・`agent_cli:` 直前へ挿入 | そのとおり | ○ |
| rc 0/1/2/3 | 不在 config で `rc=1` かつ**ファイル未作成** | ○ |
| `codd_gate_wiring.py` CLI が JSON を出す | `regression_wired:true` / `intake_wired:false` | ○ |
| **結線できているかは `doctor` が見る** | **所見 0 件** | **×** |

doctor の実測（`agent_project` を直に叩いて確認）:

```
_hook_scan_siblings(('detect_wiring',))   -> None
_hook_scan_siblings(('doctor_findings',)) -> None
doctor_wiring_findings(hooks=None)        -> []
```

`codd_gate_wiring.py` は契約名を `def` で定義せず末尾で別名公開しているため、sibling 走査の
前置フィルタ `^def <属性名>(` に一致しない（設計書:306-308 が明記する意図的な設計）。
つまり `hooks:` を書かない限り doctor は永久に無所見で、しかも**警告も出ない**
（`_hook_misconfig_findings` は hooks 未指定時に常に空）。利用者から見ると「結線したのに
doctor が何も言わない＝結線できていないのか、doctor が壊れているのか」が判別できない。

`hooks:` の記述は README にも GUIDE にも 0 hit。唯一の言及は
`agent-project.yaml.example:186` のコメントアウト行で、その直前 182-183 行は
「未指定なら sibling を走査して自動検出する／通常は書かなくてよい」と書いており、
`codd_gate_wiring` に限っては成り立たない案内になっている。

## (c) 設計書 §4.1 の「現在地」 — pass

現物のコードで抜き取り検査。すべて一致した。

| 設計書の記述 | 実装 |
|---|---|
| `HOOK_CAPABILITIES`: `wiring.detect`→`detect_wiring` / `wiring.findings`→`doctor_findings` | `agent_project/hooks.py:16-19` 同一 |
| 末尾で `detect_wiring = probe_wiring` / `doctor_findings = render_findings` | `codd_gate_wiring.py` 末尾に存在 |
| doctor 側の到達点は `doctor_wiring_findings` | `agent_project/doctor.py:313` |
| 終了コード 0/1/2/3 | 実測一致（(b) の表） |
| `--config` は実在必須 | 実測 rc=1・未作成 |
| `codd_gate_base.resolve_base_rev()` は誰も自動では掴まない | 契約名を持たず走査対象外。docstring も更新済み |

t4/t5/t7 の3者のうち、実装に追随できているのは設計書だけ。README/GUIDE が取り残された。

## (d) スコープ — pass

- `agent_project/` パッケージ配下の差分: **0 ファイル**（再結合なし）
- dashboard 関連の差分: **0 ファイル**
- `tools/agent-project/` 外の差分: `docs/designs/codd-gate-design.md` の1ファイルのみ。
  これは元要求の hints が「§4.1 の『現在地』も実装に合わせて更新」と明示的に指示した対象で、
  t7 の担当範囲。逸脱ではない。

## テスト

- `test_codd_gate_*.py` → **111 tests OK**（backlog の verify コマンドそのまま）
- 全体 844 tests → **failures=2**。両方とも再現・内容を確認し、本 run と無関係と判定:
  - `TestDaemonRouting::test_kf_base_passes_flow_config` — macOS の `/var` → `/private/var` symlink
  - `TestJournalRotation::test_rotation_archives_and_starts_fresh` — アーカイブ連番の辞書順ソート
  どちらも `codd_gate_*` を import しない。ワーカー3者の報告と一致する。

## 修正指示

**必須**

1. `tools/agent-project/README.md:287-288` — 「結線できているかは `doctor` が見る」の前に、
   doctor 経路が opt-in であることを足す。具体的には `.agent/agent-project.yaml` へ
   `hooks:` / `  wiring: codd_gate_wiring` の2行を書いたときだけ所見が出る旨と、
   設定を増やしたくないなら `python3 codd_gate_wiring.py --config …` を直接叩けば
   同じ判定が JSON で得られる旨（こちらは設定不要）を併記する。
2. `tools/agent-project/GUIDE.md:194-196` — 同じ欠落。README を正本として参照する方針は
   維持しつつ、「`hooks:` を書いたときだけ」の一語を足す（手順の複製は不要）。

**minor**

3. `tools/agent-project/agent-project.yaml.example:182-183` — 「未指定なら…自動検出する。
   通常は書かなくてよい」は汎用説明としては正しいが、直下の唯一の例 `codd_gate_wiring` が
   自動検出されない。「別名公開のプロバイダは走査に載らないので明示が要る」を1行足す。
4. `docs/designs/codd-gate-design.md:262` のモジュール表（`codd_gate_wiring.py` 行）に
   `recommend_regression_cmd` が無い。`codd_gate_regression.infer_default_repos_path` と
   ともに CLI から使われる公開名なので、表に載せると読者が入口を数え切れる。
