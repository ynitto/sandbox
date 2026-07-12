# codd-gate 自動検出 — 判定条件設計（d1）

**差別化の切り口**: 実在・バージョン・schemas 互換の3判定を単一の bool（使える/使えない）に
潰さず、`doctor_env_findings`（`tools/kiro-project/kiro-project.py:6907`）と同じ
`{category, severity, title, evidence, fix, fix_action}` 形の finding を返す4項目の
**独立した短絡パイプライン**として設計する。新しい判定機構を発明せず、この codebase に
既にある「実在確認は `shutil.which` + explicit→PATH→同梱パスの解決連鎖」（`resolve_kiro_flow`
`kiro-project.py:3033`）と「決定的チェックは finding 配列で返し、実行は自動・報告は
doctor に委ねる」という2パターンへそのまま接続する。結果として bit系の分岐は生まれず、
各失敗は severity 別に独立して劣化する。

## 1. 前提（完了条件の解釈）

このタスクは判定条件の**設計・文書化**が成果物であり、実装（b系）ではない。ワークスペースの
コードは変更していない（`git status` クリーン。調査のみの前タスク s4/s5/s6 と同じ扱い）。
完了条件のシェルコマンドは s4/s5/s6 が独立に確認した通り、`.kiro-project/` 削除
（コミット `645d86f`）と kiro-project 側の `codd` テスト未実装により現状失敗する。これは
本タスクの範囲外（実装系タスクの責務）であり、本書はその実装が読む入力として書く。

## 2. 判定条件（4項目・短絡順）

以下は上から順に評価し、**前段が失敗したら後段は評価しない**（無駄な subprocess 呼び出しを
避ける。s4 で確認した通り `codd-gate` 自体は git 呼び出しを個別タイムアウトで有界化する設計
なので、kiro-project 側の検出もそれに倣い有界・短絡にする）。

### 2.1 CLI 実在判定（PATH 上の実行可能性）

`resolve_kiro_flow` と同型の解決連鎖:

```
explicit = cfg.codd_gate                      # 新設フィールド。cfg.kiro_flow と対の明示指定
found = explicit or shutil.which("codd-gate")
if not found:
    local = Path(__file__).resolve().parent.parent / "codd-gate" / "codd-gate.py"
    found = str(local) if local.exists() else None
```

- `tools/codd-gate/install.sh` の既定インストール先は `~/.local/bin/codd-gate`（PATH 上）。
  同梱運用（sandbox のような同一リポジトリ構成）では `tools/codd-gate/codd-gate.py` が
  kiro-project の隣にある。この2経路をカバーすれば `resolve_kiro_flow` と対称になる。
- 判定結果: 見つからなければ `category=env, severity=info`（＝git や agent_cli ほど必須では
  ない。codd-gate 連携は任意機能なので、無くても kiro-project 単体は動く）。
- **ここで失敗したら 2.2/2.3 は評価しない**（バイナリが無いのにバージョンを聞いても無意味）。

### 2.2 バージョン取得

```
proc = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=5)
```

- `codd-gate --version` は argparse の `action="version"`（`codd-gate.py:983`）で
  `codd-gate {VERSION}` を stdout に出し **exit 0 で即終了**（サブコマンド未指定の通常エラー
  exit 2 とは別経路。s4 の終了コード表はサブコマンド実行時のものでこの分岐には当てはまらない
  ため、ここだけは exit 0 前提で読んでよい）。
  `timeout=5` は既存コード（`kiro-project.py:1158`、`wslpath` 存在確認と同種の軽量プローブ）
  に合わせた値。
- 正規表現 `r"codd-gate (\d+)\.(\d+)\.(\d+)"` でパースし `(1, 0, 0)` のようなタプル化。
- **失敗の扱い（timeout / exit≠0 / stdout がパターン不一致）は「バージョン不明」とし、
  「取得できた」側には倒さない**。s4 の非対称性（`--json` がゲート判定を含まない）と同じ
  教訓で、「分からない」を「大丈夫」に丸めない。
- `MIN_SUPPORTED_VERSION = (1, 0, 0)`（現行 `codd-gate.py:56` の `VERSION` と同値。将来
  codd-gate 側が破壊的変更で bump したときの下限をここに置く、という**置き場所**を決める
  ことが今回の設計の要点で、値自体は現状全てを許容する）。取得したタプルが未満なら NG。

### 2.3 schemas 互換判定

s5 の結論（`schemas/` 実データにバージョンフィールドは存在せず、互換性は
「追加のみ可・未知キー無害」という構造規約で担保される）を踏まえ、**semver 比較ではなく
2種類の構造チェックに分解する**。両者は独立に評価してよい（互いの前提にならない）。

| チェック | 対象 | タイミング | 失敗の意味 |
|---|---|---|---|
| (a) 出力契約チェック | kiro-project がこれから書き出す `repos.json`（`export_repo_registry` の出力）が `repos.schema.json` の最小要件（トップレベル object、`_` 始まり以外の値が object）を満たすか | 起動時・静的（自己診断） | **codd-gate ではなく kiro-project 側のバグ** |
| (b) 入力契約チェック | codd-gate `tasks` が返す各要素が `task.schema.json` の唯一の `required`（`title`: string）を満たすか | 実行時・`tasks --debt`/`intake_cmd` を実際に呼んだ都度 | 個々のタスクの生成不備（codd-gate 側 or 中間の破損） |

- (a) は「明日 codd-gate をバージョンアップしても壊れないか」ではなく「今この kiro-project
  自身が契約を満たす出力を作っているか」の自己点検。バージョン比較の代替にはならないが、
  唯一実装可能な静的チェックがこれ（実データにバージョンが無いため）。
- (b) は静的に検出できない（`tasks` は差分/負債という実データ依存の出力を返すため、事前に
  「この codd-gate バージョンなら title が付く」と決め打てない）。したがって **起動時の
  一括判定には含めず、実行時の防御的パースとして e 系（負債取り込み）に実装責務を渡す**。
  d1 としては「(b) はここで検証する」という置き場所を明記するに留める。
- schemas の将来 non-additive 改訂を検知する手段は、実データにバージョンが無い以上
  存在しない（s5 が指摘した通り）。d1 でもこれを**明示的に対象外**とする。README の
  「追加のみ可」運用規約の遵守に委ねる。

## 3. いずれか失敗時のフォールバック方針

| 判定 | 失敗の種類 | 連携の可否 | finding | fix |
|---|---|---|---|---|
| 2.1 実在 | PATH にも同梱パスにも無い | **連携しない**（regression_cmd/intake_cmd を自動配線しない。手動設定があればそちらが正＝上書きしない） | `env / info` | 「codd-gate をインストールするか `--codd-gate` で指定（任意機能）」 |
| 2.2 バージョン | timeout・exit≠0・パース不能（＝不明） | **連携しない**（安全側に倒す。「分からない」を「大丈夫」に丸めない） | `env / warn` | 「`<found> --version` が失敗する。codd-gate のインストールを確認」 |
| 2.2 バージョン | 取得できたが `MIN_SUPPORTED_VERSION` 未満 | **連携しない** | `env / warn` | 「codd-gate を `MIN_SUPPORTED_VERSION` 以上へ更新」 |
| 2.3(a) 出力契約 | kiro-project 自身の repos.json 生成物が構造要件を満たさない | **連携しない**（かつ通常の repos 解決自体も壊れている可能性が高い） | `config / critical` | 「`export_repo_registry` の出力を確認（kiro-project 側の不具合）」 |
| 2.3(b) 入力契約 | 個別の task に `title` が無い | **その1件だけ棄却**。連携全体は止めない | （journal のみ。doctor finding は起こさない） | `run_intake` の既存規約（例外は journal に残してループを継続）に合流 |

方針として貫くのは2点:

1. **静的判定（2.1〜2.3a）は不明・不足を全て「連携しない」側に倒す。** 自動配線は
   任意の利便機能であり、判定が怪しいまま黙って有効化して後段（regression_cmd の
   exit code をゲート判定として使う）で argparse エラー（exit 2）とゲート NG（exit 1）が
   混同されるリスクを避ける（s4 が指摘した「未知の引数は exit 2」＝環境エラーと、
   「amber あり」＝exit 1 の区別を、古い codd-gate に新しいフラグを渡すことで壊さない）。
2. **動的判定（2.3b）だけは1件単位の局所棄却にする。** 全体を止めると
   「codd-gate 由来のノイズ1件で intake ループが死ぬ」という、design doc の
   「常駐は無害・有限」不変条件に反する事態になる。

明示設定（`cfg.codd_gate` に実行体を指定、または `regression_cmd`/`intake_cmd` を手動設定
済み）がある場合はこの自動判定パイプライン自体をスキップする。ユーザーが明示した前提は
自動検出の推測より常に優先する。

## 4. b 系実装への申し送り（設計のみ・未実装）

- 新設 config フィールド案: `codd_gate: "str | None" = None`（`cfg.kiro_flow` と対称）。
  自動配線の可否は「`regression_cmd`/`intake_cmd` が未設定であること」を前提条件にする
  （既存の明示設定を上書きしない）。
- 検出関数の置き場所案: `resolve_kiro_flow` の隣に `resolve_codd_gate(explicit) -> str | None`
  （実在のみを返す）、判定全体は `doctor_env_findings` と同じシグネチャ規約で
  `doctor_codd_gate_findings(cfg, which=shutil.which, run=subprocess.run) -> list[dict]`
  （`which`/`run` を差し替え可能にするのは `doctor_env_findings(cfg, which=shutil.which)`
  が既にテスト容易性のために取っている引数注入パターンに合わせるため）。
- `--repos`/`--repo-dir` の実引数生成は b2 の管轄（s6 の申し送り通り、自己ホスト構成では
  相対パス、それ以外は絶対パスへフォールバック）。d1 はその手前の「そもそも codd-gate を
  使ってよいか」の判定にのみ責務を持つ。

## 5. 検証内容と結果

- `tools/kiro-project/kiro-project.py` の既存パターンを実地確認: `doctor_env_findings`
  （L6907-6942, finding 形状と `which` 注入）、`resolve_kiro_flow`（L3033-3040, 解決連鎖）、
  `run_intake`（L7913 付近, 「例外は無視してループを継続」不変条件）。
- `tools/codd-gate/codd-gate.py` で `VERSION`（L56）・`--version` の argparse 定義（L983）を
  実地確認。`tools/codd-gate/install.sh` で既定インストール先（`~/.local/bin/codd-gate`）を
  確認。
- 依存タスク s4（CLI I/F・終了コード規約）・s5（schemas にバージョンフィールドが無いという
  結論・`title` 必須という結論）・s6（`--repos`/`--repo-dir` 生成方法、自己ホスト前提の
  脆さ）を読み、本書の判定条件・フォールバック方針が3者の実測結果と矛盾しないことを確認した。
- 本タスクはコード変更を伴わないため `pytest`/`codd-gate verify` は実行していない
  （完了条件のシェルコマンドが要求する `.kiro-project/repos.json` 復元と
  `tools/kiro-project/tests` への `codd` テスト追加は s4/s5/s6 と同じ理由で本タスクの
  範囲外）。ワークスペースは無変更（`git status` クリーン）。

## 6. 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 「schemas 互換判定」を semver 比較ではなく構造チェック2種に分解した（s5 の
  結論に基づく）。もし synth/gate 側が「バージョン番号での比較」を必須要件として期待して
  いるなら、その実装は不可能（比較対象のフィールドが実データに存在しない）ため、本書の
  分解案を採用するか対象を明示的に諦めるかの判断が必要。
- **未解決事項**: `MIN_SUPPORTED_VERSION` の具体的な運用（誰がいつ上げるか）は、
  codd-gate 側に破壊的変更が入るまで実質的な意味を持たない。現状は「置き場所を用意した」
  段階に留まる。
- **範囲外で見つけた問題**: なし（本タスクは既存コードの変更を伴わない設計文書のみ）。
  s6 が指摘した `.kiro-project/` 削除（コミット `645d86f`）は本書の判定条件設計とは独立の
  問題として追跡を委ねる。
