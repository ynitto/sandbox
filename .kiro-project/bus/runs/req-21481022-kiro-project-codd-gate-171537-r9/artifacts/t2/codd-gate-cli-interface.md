# t2 実測結果: codd-gate CLI インターフェース（verify / --debt / tasks）

対象: `codd-gate verify` / `codd-gate verify --debt` / `codd-gate tasks`（`--debt` 込み）の
引数・終了コード・標準出力フォーマット（JSON か否か）を実機で実測し記録する。

出典: `tools/codd-gate/codd-gate.py`（worktree
`/var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/kiro-flow-ws-64414-6e3megvt/sandbox`、
HEAD `6224bd197536fb63d8c99fb7ae383ee459e4d57c`）。`/Users/nitto/.local/bin/codd-gate` に
インストール済みの実体はこのソースと `diff` で差分ゼロ（バイト同一）。

## 結論（要約）

- サブコマンドは `scan / impact / verify / tasks / check` の5つ。**`verify` と `tasks` は
  「差分モード（既定）」と「`--debt`（全体棚卸しモード）」の2系統を持ち、`--debt` の有無で
  入力（差分 vs マップ全体）が切り替わるだけで、出力形と終了コード規約は共通の枠組みに乗る**。
- **終了コードは3値**: `0`=PASS/正常終了、`1`=ゲート NG（`verify` のみ。`--debt` 込み）、
  `2`=環境・設定・argparse エラー。`tasks`/`scan`/`check`(PASS時) は常に `0`。
- 出力は「人間向けテキスト（既定）」と「`--json`（所見の正）」の二択。`tasks` は標準出力への
  単一 JSON 配列、または `--inbox <dir>` でタスク1件1ファイルの JSON 書き出しに切り替わる。

---

## 1. `verify` 固有引数（実測: `codd-gate verify --help`, exit 0）

```
codd-gate verify [--repos FILE] [--config CONFIG] [--repo-dir NAME=DIR] [--sync]
                  [--map MAP_PATH] [--json] [--base BASE] [--repo REPO]
                  [--strict] [--strict-cross] [--debt]
                  [--max-broken N] [--max-undocumented N] [--max-untested N]
```

| 引数 | 型 | 既定 | 意味 |
|---|---|---|---|
| `--repos FILE` | str | `None` | repos レジストリファイル（`schemas/repos.schema.json`）。未指定時は `--config` の `repos:` → `--repo-dir` のみ → cwd を単一 repo `default` 扱い、の順にフォールバック |
| `--repo-dir NAME=DIR` | str（複数可） | `[]` | repo 名→ローカル checkout。`--repos` の `dir` より常に優先 |
| `--base BASE` | str | `$KIRO_BASE_REV` | 差分の基準 rev。**`--debt` 指定時は不要**（渡しても無視） |
| `--repo REPO` | str | `None` | 差分対象 repo。repo が複数で曖昧なら必須 |
| `--strict` | flag | `False` | GRAY（未接続の変更）も NG にする |
| `--strict-cross` | flag | `False` | FOLLOWUP（別 repo 側の追随待ち）も NG にする |
| `--debt` | flag | `False` | 差分でなく全体負債をしきい値と突合するモードに切替 |
| `--max-broken/-undocumented/-untested N` | int | `None`（未指定＝その種別は検査しない） | `--debt` 時のラチェット上限 |

## 2. `verify` 実測（stdout・終了コード）

対象 repo: `--repos ./.kiro-project/repos.json --repo-dir sandbox=.`（`.kiro-project/repos.json`
は今回の worktree に実在。中身は `{"sandbox": {"base": "main", "url": "...", "dir": "."}}`）。

### 2.1 差分モード・テキスト（`--base HEAD~1`）— PASS 実例

```
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1
差分: sandbox HEAD~1..作業ツリー（2 ファイル）
  [GREEN] tools/kiro-project/codd_gate_invoke.py（接続 1 本・整合）
  [GREEN] tools/kiro-project/tests/test_codd_gate_invoke.py（参照は全て解決）
OK: 一貫性ゲート通過
EXIT=0
```

### 2.2 差分モード・`--json`（同条件）

```
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1 --json
{"amber": [], "base": "HEAD~1", "changed": {"tools/kiro-project/codd_gate_invoke.py": "A",
 "tools/kiro-project/tests/test_codd_gate_invoke.py": "A"}, "followup": [], "gray": [],
 "green": [...], "repo": "sandbox"}
EXIT=0
```

1行 JSON（`sort_keys=True`）。**`ng`（ゲート判定）フィールドは含まれない** — 判定は exit code
に一本化されている（`--strict`/`--strict-cross` は exit code のみに影響し、JSON の中身は変わらない）。
今回の差分は amber/gray/followup がすべて空だったため、この非対称性自体は今回の実測では
NG 側のケースを取れていない（前回 run の実測では amber 50件で `--json` が exit=0 かつ JSON に
`amber` 配列が入る、というケースを確認済み。ロジック上 exit code は
`ng = bool(amber) or (strict and bool(gray)) or (strict_cross and bool(followup))` で決まり、
`--json` の有無とは独立なので今回の PASS ケースと矛盾しない）。

### 2.3 `--debt` モード・テキスト（しきい値未指定 → 常に PASS）

```
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --debt
ノード: doc 494 / code 359 / test 43 ／ エッジ: 627
負債: 壊れた参照 1361 / 未文書化 251 / 未テスト 333
  - sandbox:.github/instructions/common.instructions.md 行54: ~/.copilot/skill-registry.json が解決できない
  ...（先頭10件のみ）
EXIT=0
```

`--max-*` を1つも指定しなければ `findings=[]` となり無条件 PASS（brownfield の既存負債では
止めない設計どおり）。

### 2.4 `--debt --json`（しきい値未指定）

```
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --debt --json
{"debt": {"broken": 1361, "undocumented": 251, "untested": 333}, "findings": []}
EXIT=0
```

### 2.5 `--debt --max-broken 0`（しきい値超過 → NG）

```
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --debt --max-broken 0 --json
{"debt": {"broken": 1361, "undocumented": 251, "untested": 333},
 "findings": ["壊れた参照 1361 件 > 許容 0"]}
EXIT=1
```

テキストモードも同条件で末尾に `NG: 壊れた参照 1361 件 > 許容 0` を出し **EXIT=1** を実測。

## 3. `tasks` 固有引数（実測: `codd-gate tasks --help`, exit 0）

```
codd-gate tasks [--repos FILE] [--config CONFIG] [--repo-dir NAME=DIR] [--sync]
                 [--map MAP_PATH] [--json] [--base BASE] [--repo REPO] [--debt]
                 [--priority N] [--max N] [--cohort] [--inbox DIR]
```

| 引数 | 型 | 既定 | 意味 |
|---|---|---|---|
| `--base BASE` | str | `$KIRO_BASE_REV` | 差分モードの基準 rev（`--debt` 時は不要） |
| `--repo REPO` | str | `None` | 差分対象 repo（曖昧なら必須） |
| `--debt` | flag | `False` | 全体負債からタスク化（既定は差分の amber/gray/followup） |
| `--priority N` | int | `1` | 生成タスクの priority（gray/未文書化/未テスト系は `max(priority-1,0)`） |
| `--max N` | int | `20` | `--debt`: 種別ごとの上限件数（「一度に出すタスク数」の上限。verify の `--max-*` とは別物） |
| `--cohort` | flag | `False` | `--debt`: 未文書化/未テストを repo 単位の cohort にまとめる |
| `--inbox DIR` | str | `None` | 標準出力でなく `<dir>/<id>.json` へ1タスク1ファイルで書く |

`--json` フラグは `tasks` の出力形には影響しない（`tasks` は常に task スキーマの JSON を出す。
`--json` は scan/impact/verify 用の「テキストか JSON か」の切替であり、`tasks` の出力形式は
`--inbox` の有無だけで決まる）。

## 4. `tasks` 実測

### 4.1 差分モード（`--base HEAD~1`）

```
$ codd-gate tasks --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base HEAD~1
[]
EXIT=0
```

今回の差分は amber/gray/followup が空（§2.1 参照）のため空配列 `[]`。**空でも常に有効な JSON
配列**（`json.dumps([], indent=1)` は `[]` の1行）であり、`json.load(stdout)` する側が失敗しない
ことを確認。所見がある場合は `[{...}, {...}]`（`schemas/task.schema.json` 準拠、`indent=1` の
複数行整形）になる（前回 run の実測: amber 50件で `id`/`title`/`verify`/`paths`/`priority`/
`note`/`expect` を持つオブジェクトの配列）。

### 4.2 `--debt --max 2 --inbox <dir>`

```
$ codd-gate tasks --repos ./.kiro-project/repos.json --repo-dir sandbox=. --debt --max 2 --inbox <dir>
6 タスクを <dir> へ書き出しました
EXIT=0
```

`<dir>` に6ファイルが実際に生成されることを確認（壊れた参照/未文書化/未テストの3種 ×
`--max 2` で各種別2件ずつ = 6件）:

```
codd-doc-github-skills-api-designer-s-052829.json
codd-doc-github-skills-bruno-e2e-buil-516946.json
codd-ref-github-instructions-common-i-7151f7.json
codd-ref-github-instructions-common-i-eb3752.json
codd-test-github-skills-agent-reviewer-1f8097.json
codd-test-github-skills-agent-reviewer-a134aa.json
```

`--inbox` 指定時、**標準出力はサマリ1行のみ**（JSON ではない）で、実体は各ファイル（1タスク1
JSON オブジェクト、`indent=1`、末尾改行あり）。サンプル1件:

```json
{
 "id": "codd-doc-github-skills-api-designer-s-052829",
 "title": ".github/skills/api-designer/scripts/validate_openapi.py を文書化する（repo sandbox）",
 "verify": "codd-gate check --repo-dir sandbox=. --covered .../validate_openapi.py --need doc",
 "paths": ".github/skills/api-designer/scripts/validate_openapi.py",
 "priority": 0,
 "note": "接続マップ上でどのドキュメントからも参照されていない"
}
```

`id` は決定的ハッシュ付き slug（`codd-<kind>-<slug>-<hash6>`）で、同じ発見からは常に同じ
ファイル名 = 再実行しても重複投入されない冪等キー（`intake_cmd` からの周期実行を想定した設計）。

## 5. 終了コード規約（横断・実測で裏取り）

| exit | 状況 | 実測コマンド例 |
|---|---|---|
| `0` | `verify` PASS（差分 amber 無し／debt 全 findings 空）、`tasks`（所見の有無に関わらず常に）、`--help` | §2.1, §2.3, §4.1, §4.2, `--help` |
| `1` | `verify` NG（差分 amber 有り、または debt しきい値超過） | §2.5（`--debt --max-broken 0`） |
| `2` | 環境・設定エラー（`--base` 未指定・repos ファイル不在等）／argparse 自体のエラー | 下記 §6 |

`tasks` は `_emit_tasks()` が例外時以外 `return 0` 固定 — 「NG（ドリフトあり）」の判定は
`verify` の役割で、`tasks` は変換・出力に徹する設計（今回の実測でも空配列 `[]`・6ファイル書き出し
いずれも exit=0 で一致）。

## 6. エラー系実測

```
$ codd-gate
usage: codd-gate [-h] [--version] {scan,impact,verify,tasks,check} ...
codd-gate: error: the following arguments are required: cmd
EXIT=2

$ codd-gate verify --unknown-flag
usage: codd-gate [-h] [--version] {scan,impact,verify,tasks,check} ...
codd-gate: error: unrecognized arguments: --unknown-flag
EXIT=2

$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=.   # --base 無し・--debt 無し
[codd-gate] エラー: 差分の基準 rev がありません（--base か $KIRO_BASE_REV。全体負債は --debt）
EXIT=2

$ codd-gate verify --repos ./.kiro-project/does-not-exist.json --repo-dir sandbox=. --debt
[codd-gate] エラー: repos レジストリが見つかりません: .kiro-project/does-not-exist.json
EXIT=2
```

いずれも stderr へメッセージを出し `argparse` 標準エラー / `_die()`（`codd-gate.py` L89-91）
経由で exit 2。stdout は空。

## 7. 完了条件シェルコマンドの実行結果（参考・本タスクの対象そのものではないが実測として記録）

```
$ python3 -m pytest tools/kiro-project/tests -q -k codd
...............................................                       [100%]
47 passed, 579 deselected, 3 subtests passed in 0.05s
$ codd-gate verify --repos ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict
差分: sandbox HEAD~1..作業ツリー（2 ファイル）
  [GREEN] tools/kiro-project/codd_gate_invoke.py（接続 1 本・整合）
  [GREEN] tools/kiro-project/tests/test_codd_gate_invoke.py（参照は全て解決）
OK: 一貫性ゲート通過
COMBINED_EXIT=0
```

この worktree（HEAD `6224bd1`）では `.kiro-project/repos.json` が既に存在し、`tools/kiro-project/tests`
にも `codd` を含むテスト（`test_codd_gate_invoke.py` / `test_codd_gate_detect.py` /
`test_codd_gate_routing.py`）が既に実装済みだったため、完了条件コマンドはそのまま両方とも成功した
（過去 run の同種調査ではこの2点が未実装で失敗していたが、本 run 時点では別タスクにより解消済み）。

## 8. 採用した前提・未解決事項・範囲外で見つけた問題

**前提**:
- 本タスクは「`verify`/`--debt`/`tasks` の引数・終了コード・stdout フォーマットの実測記録」が
  成果物であり、コード変更は行っていない（worktree 内でファイルへの書き込みは一切していない。
  `--inbox` の出力先はスクラッチパッド配下の一時ディレクトリで、ワークスペース外）。
- `--repos`/`--repo-dir`/`--base`/`--json`/`--strict`/`--debt`/`--max-*`/`--inbox`/`--max`/
  `--cohort`/`--priority` を実測対象の中心とし、`scan`/`impact`/`check`/`--sync`/`--map`/
  `--config`/`--repo`/`--strict-cross` は依頼文の対象外（`verify`/`--debt`/`tasks` に閉じる）
  ため深追いしていない。

**未解決事項**: なし。完了条件のシェルコマンドは実測時点で exit 0 で成功しており、後続作業を
要するブロッカーは見つからなかった。

**範囲外で見つけた問題（参考記録のみ・本タスクでは対応しない）**:
- `verify`（差分モード、`--debt` なし）の `--json` 出力には `ng`（ゲート判定結果）フィールドが
  無く、判定は exit code でしか分からない。外部 CI やツールが JSON の中身だけを見て「NG かどうか」
  を再実装しようとすると、この非対称性を踏まえないと誤判定しうる（§2.2）。
- `--debt` 時、`verify`/`tasks` とも `--base`/`--repo` を渡してもエラーにならず黙って無視される
  （`--debt` 分岐が差分モード分岐より先に return するため）。誤って両方指定したときに気づきにくい。
