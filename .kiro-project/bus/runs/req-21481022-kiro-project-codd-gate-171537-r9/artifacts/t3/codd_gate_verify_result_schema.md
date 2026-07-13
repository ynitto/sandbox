# codd-gate `verify` 結果 — 構造化スキーマと変換可能フィールド

対象: `tools/kiro-project/codd_gate_status.py`（起点）と、その no-op 縮退が包む
`codd-gate verify` の実出力（`tools/codd-gate/codd-gate.py` の `classify_impact`/`_run`）。
`codd_gate_status.py` 自体は「codd-gate を使ってよいか」の判定（`CoddGateStatus.usable/reason`）
だけを持ち、verify の PASS/FAIL・ドリフト内容は一切保持しない——その生データは
`codd-gate verify [--json]` の標準出力（`codd_gate_invoke.CoddGateResult.stdout` に未パースで
入る文字列）にある。本書はその出力を構造化スキーマとして列挙し、理由テキスト／backlog タスクへ
変換できるフィールドを対応づける。

---

## 1. `verify` の2モードと出力スキーマ

`codd-gate verify` は `--debt` の有無で出力形式が完全に別物になる（`_run`
tools/codd-gate/codd-gate.py:1054-1101）。**JSON 自体に `pass`/`fail` フィールドは無く**、
PASS/FAIL は exit code とドリフト有無の判定式でのみ表現される。

### 1-A. 差分モード（既定・`codd-gate verify --base <rev> --json`）

`classify_impact()`（tools/codd-gate/codd-gate.py:660-722）の戻り値をそのまま出力。

```json
{
  "base": "<rev>",
  "repo": "<target repo 名>",
  "changed": { "<relpath>": "A|M|D", "...": "..." },
  "green":    [ { "node": "repo:path", "detail": "..." } ],
  "amber":    [ { "type": "doc-stale|broken-ref|dangling-ref",
                  "node": "repo:path", "counterpart": "repo:path", "detail": "..." } ],
  "gray":     [ { "type": "unmapped", "node": "repo:path", "detail": "..." } ],
  "followup": [ { "type": "doc-stale-cross",
                  "node": "repo:path", "counterpart": "repo:path", "detail": "..." } ]
}
```

| フィールド | 型 | 意味 |
|---|---|---|
| `base` | string | 差分基準 rev（`--base` または `$KIRO_BASE_REV`） |
| `repo` | string | 差分対象 repo 名（`_select_target` が解決） |
| `changed` | object | `{相対パス: git status文字}`（A=追加, M=変更, D=削除。rename/copy は D+A に展開済み） |
| `green[]` | array | 整合済み変更（`node`, `detail` のみ。`type` 無し） |
| `amber[]` | array | **ドリフト（NG 確定要因）**。`type` は `doc-stale`/`broken-ref`/`dangling-ref` の3種 |
| `gray[]` | array | 未接続の変更（`--strict` 時のみ NG に昇格） |
| `followup[]` | array | 別 repo 側で検証が要る追随（`--strict-cross` 時のみ NG に昇格） |

**PASS/FAIL 判定式**（tools/codd-gate/codd-gate.py:1097-1101。JSON外・exit code由来）:
```python
ng = bool(amber) or (args.strict and bool(gray)) or (args.strict_cross and bool(followup))
exit_code = 1 if ng else 0
```
非 `--json` 実行時は標準出力の最終行が `"NG: ドリフトあり — ..."` / `"OK: 一貫性ゲート通過"`。

### 1-B. 負債モード（`codd-gate verify --debt --json`）

`--base` 不要。全体負債をしきい値と突合する（tools/codd-gate/codd-gate.py:1054-1071）。

```json
{
  "debt": { "broken": <int>, "undocumented": <int>, "untested": <int> },
  "findings": [ "<ラベル> <件数> 件 > 許容 <上限>", "..." ]
}
```

| フィールド | 型 | 意味 |
|---|---|---|
| `debt.broken` | int | 壊れた参照の総数（`mapdata["broken_refs"]` 長） |
| `debt.undocumented` | int | 未文書化 code の総数（`orphans["undocumented"]` 長） |
| `debt.untested` | int | 未テスト code の総数（`orphans["untested"]` 長） |
| `findings[]` | array\<string\> | しきい値超過時のみ追加される**自由文**（`--max-broken`/`--max-undocumented`/`--max-untested` を指定した項目のみ判定対象。未指定項目はしきい値なし＝常に非超過） |

**PASS/FAIL 判定式**: `findings` が空なら exit 0（PASS）、1件以上で exit 1（FAIL）。
`debt.*` の3件数は `findings` の有無に関わらず常に出力される（しきい値未指定でも棚卸し値として出る）。

---

## 2. 呼び出し側（kiro-project）の既存ラッパー階層と、埋まっていない箇所

| モジュール | 保持する値 | verify のドリフト詳細を持つか |
|---|---|---|
| `codd_gate_status.CoddGateStatus` | `usable`, `findings`（env/config 種別のfinding。verify結果とは無関係）, `reason` | ✗（「使ってよいか」のみ） |
| `codd_gate_invoke.CoddGateResult` | `status`(ok/failed/skipped), `exit_code`, `stdout`（生テキスト）, `reason` | △（`stdout` に生 JSON 文字列が**未パースのまま**入るだけ） |
| `codd_gate_debt.DriftItem`/`DebtParseResult` | `title`, `id`, `fields` | ✗（これは `codd-gate tasks`/`task.schema.json` 形式のパーサであり、`verify --json` の `green/amber/gray/followup` や `debt/findings` とは**別スキーマ**。対象外） |

→ **`verify --json`（1-A/1-B いずれの形式も）を構造化データとして受け取るパーサは現時点で存在しない。**
`CoddGateResult.stdout` に入る生 JSON 文字列を `json.loads` する層が未実装（`codd_gate_debt.py` は
`tasks` 出力専用で流用不可）。

---

## 3. 理由テキスト・backlog タスクへ変換できるフィールド

差分モードの `amber`/`followup`/`gray` は、`tasks_from_impact()`
（tools/codd-gate/codd-gate.py:746-787）が実装済みの変換ロジックを持つ——つまり
「backlog タスク化できる」設計はスキーマ側に既に織り込まれている。

| 元フィールド | 変換先（`schemas/task.schema.json`） | 用途 |
|---|---|---|
| `amber[].type == "doc-stale"` | `task.title` の文言分岐 | 「{path} の変更をドキュメント {doc} へ反映する（repo {repo}）」 |
| `amber[].node`（`repo:path`） | `task.paths`／`task.id` の元（sha1先頭6桁で決定的 id 化） | 対象パス特定・冪等キー |
| `amber[].counterpart`（`repo:doc`） | `task.title` 内の反映先ドキュメント名 | doc-stale の反映先 |
| `amber[].detail` | `task.note` | 人間可読の根拠。journal 転記にもそのまま使える |
| `amber[].type in ("broken-ref","dangling-ref")` | `task.title` 分岐 | 「{path} の壊れた参照を修正する（repo {repo}）」 |
| `followup[].node` | `task.title` の変更元表記 | どの変更が別 repo への追随を要求したか |
| `followup[].counterpart`（`repo:doc`） | `task.workspace`（反映先 repo）／`task.paths`（doc） | 別 repo 宛タスクのルーティング先 |
| `followup[].detail` | `task.accept`（`task.verify` ではなく自然文） | 別 repo 側は verify コマンドを合成できないため accept へ落とす設計 |
| `gray[].node`/`detail` | `task.title`／`task.note`、`task.priority = max(priority-1, 0)` | 未接続コードの文書化・cohort 化候補（優先度は通常より1段低い） |
| `changed`（dict） | 理由テキストの件数根拠 | 「{len(changed)} ファイルの差分」等サマリ文に使える（専用カウントフィールドは無く配列長で数える） |

負債モードは `findings` が既に文字列化済みで、対応するタスク化ロジックは無い
（`tasks_from_debt()` は `verify --debt` の `findings` 文字列を経由せず、`mapdata` の
`broken_refs`/`orphans` を直接参照して独自にタスクを組み立てる——`--debt` の verify 出力と
tasks 出力は生成元は同じ `mapdata` だが、変換パスは共有していない）。理由テキストへは
`debt.broken`/`debt.undocumented`/`debt.untested` の件数と `findings[]` をそのまま
journal へ転記すれば足りる。

**件数系（reason text 向け集計）**:
- 差分モード: `len(changed)`, `len(green)`, `len(amber)`, `len(gray)`, `len(followup)`
  — 専用カウントフィールドは無く、全て配列長から O(1) で導出する設計。
- 負債モード: `debt.broken`/`debt.undocumented`/`debt.untested` は件数として最初から提供済み。

---

## 検証内容と結果

- 対象ファイルを実読了: `codd_gate_status.py`（151行）, `codd_gate_base.py`（55行）,
  `codd_gate_invoke.py`（86行）, `codd_gate_detect.py`（182行）, `codd_gate_routing.py`（83行）,
  `codd_gate_debt.py`（101行）、および verify の実装元 `tools/codd-gate/codd-gate.py`（全1118行、
  特に `classify_impact` 660-722, `tasks_from_impact` 746-787, `_run` 1032-1101）。
- `tools/codd-gate/tests/test_codd_gate.py` の `DebtVerifyTests`（`--max-broken`/`--max-untested`
  のしきい値と exit code の対応）、および `classify_impact` を直接呼ぶテスト群
  （`test_amber_doc_stale`/`test_green_coherent_change`/`test_gray_unmapped_new_code`/
  `test_amber_broken_ref_in_changed_doc`/`test_amber_dangling_ref_on_delete`/
  `test_cross_repo_followup`）で、1-A のスキーマ形状と `--strict`/`--strict-cross` の exit code
  分岐を裏取りした。
- 完了条件コマンドのうち実行可能な部分を試行: `python3 -m pytest tools/kiro-project/tests -q -k codd`
  → **50 passed**（現状のワークツリーで成功）。一方 `grep -rq "codd_gate"
  tools/kiro-project/kiro_project/` は **該当ディレクトリが存在せず失敗**（後述）。
- 本タスクは調査のみのため作業ツリーへの変更なし。

## 前提・未解決事項・範囲外で見つけた問題

- 前提: タスク文中の「codd_gate_status.py を読み」は文字通りには同ファイル単体を指すが、
  verify 結果の構造化スキーマは `codd_gate_status.py` 自体には存在しない（同ファイルは
  no-op 縮退の判定のみを担う）。そのため同一 run で新設された姉妹モジュール群
  （detect/invoke/routing/debt）と、verify の実装元である `tools/codd-gate/codd-gate.py` まで
  調査範囲を広げた。これを「codd_gate_status.py を起点に読む」の妥当な解釈と判断した。
- 未解決事項: **`verify --json`（green/amber/gray/followup 形式・debt/findings 形式）を構造化
  パースするモジュールは未実装。** `codd_gate_debt.py` は `codd-gate tasks` 出力（既に
  `task.schema.json` 形式）専用のパーサであり、`verify` の出力形式とは別スキーマのため流用できない。
  差分ゲート・受入判定側で verify の内訳（amber の型・件数等）を journal やタスクへ渡したいなら、
  本書 2節の表が示す「未パース」の穴を埋める `parse_verify_output()` 相当のモジュール新設が要る
  ——これは本タスク（スキーマ列挙）の範囲外であり、設計・実装は別タスクの責務とする。
- 範囲外で見つけた問題（このタスクでは修正しない）: 完了条件コマンドの
  `grep -rq "codd_gate" tools/kiro-project/kiro_project/` が参照する
  `tools/kiro-project/kiro_project/`（package ディレクトリ）は実在せず、`codd_gate_*.py` は
  すべて `tools/kiro-project/` 直下にフラットに置かれている。加えて `kiro-project.py` 本体は
  `codd_gate_*` モジュールを一切 import しておらず（"codd-gate" という文字列がコメント・docstring
  中の例示コマンドとして5箇所出るのみ）、3フック（regression/acceptance/enqueue）への結線
  （b1-b3/c1-c2/e1-e2）はまだ行われていない——各 `codd_gate_*.py` の docstring が明記する通り、
  これは意図的に別タスクへ切り出された責務であり、本タスクの守備範囲外。したがって本 run の
  完了条件コマンド全体（pytest && grep && codd-gate verify）は現時点では通らないが、これは
  本タスク単体の未達ではなく、結線タスクが未着手であることに起因する。
