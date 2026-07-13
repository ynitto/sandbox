# s4 調査結果: codd-gate CLI インターフェース（verify / tasks / --debt / stdout / 終了コード）

出典: `tools/codd-gate/codd-gate.py`（1118行, `main()` L966-1029・`_run()` L1032-1101・
`_emit_tasks()` L1104-1114・`_die()` L89-91）, `tools/codd-gate/README.md`,
`schemas/repos.schema.json`, `schemas/task.schema.json`, `docs/designs/codd-gate-design.md`。
`/Users/nitto/.local/bin/codd-gate` にインストール済みの実体は `tools/codd-gate/codd-gate.py` と
バイト同一（`diff` で確認済み）。以下は実行確認込みで記載。

## 結論（要約）

- サブコマンドは `scan / impact / verify / tasks / check` の5つ。**`verify` と `tasks` はどちらも
  「差分モード」と「`--debt`（全体棚卸しモード）」の2系統を持ち、`--debt` の有無で入力（差分 vs
  マップ全体）が切り替わるだけで、出力の形（stdout フォーマット）と終了コード規約は共通**。
- **終了コード規約は3値**: `0`=PASS/正常終了, `1`=ゲート NG（`verify` のみ。`--debt` 込み）,
  `2`=環境・設定エラー（`_die()` および argparse 自身のエラー）。`tasks`/`scan`/`check`(PASS時) は
  常に `0`。
- 出力は「人間向けテキスト（既定）」と「`--json`（所見の正）」の二択。`tasks` は標準出力の JSON
  配列、または `--inbox <dir>` でタスクごと1ファイルの JSON 書き出しに切り替わる。

---

## 1. 共通引数（`parents=[common]` で `scan/impact/verify/tasks/check` 全てに付く）

| 引数 | 型 | 既定 | 意味 |
|---|---|---|---|
| `--repos FILE` | str | `None` | repos レジストリファイル（`schemas/repos.schema.json`。省略時は `--config` の `repos:` → `--repo-dir` のみ → cwd を単一 repo `default` 扱い、の順にフォールバック。`load_repos()` L199-223） |
| `--config CONFIG` | str | `None` | 設定ファイル（`.kiro/codd-gate.{yaml,json}`。`repos_file:`/`repos:`/`repo_dirs:`/`sync:`/`map:` を持てる） |
| `--repo-dir NAME=DIR` | str（`append`） | `[]` | repo名→ローカル checkout の対応。複数指定可。`NAME=` を省略すると `default`。**常に repos.json の `dir` より優先**（`_parse_repo_dirs()` L937-947） |
| `--sync` | flag | `False` | `dir` 未解決で `url` を持つ repo を共有ミラー+worktree で実体化 |
| `--map MAP_PATH` | str | `None` | マップ書き出し先（`scan` 専用。既定 `.codd-gate/map.json`） |
| `--json` | flag | `False` | JSON 出力に切り替える（**所見の正は JSON 側**。テキストは要約） |

## 2. `verify` 固有引数

```
codd-gate verify [common引数...] [--base BASE] [--repo REPO] [--strict] [--strict-cross]
                  [--debt] [--max-broken N] [--max-undocumented N] [--max-untested N]
```

| 引数 | 型 | 既定 | 意味 |
|---|---|---|---|
| `--base BASE` | str | `$KIRO_BASE_REV`（未設定なら後述の通りエラー） | 差分の基準 rev。**`--debt` 指定時は不要**（指定しても無視される。差分モードに入る前に `--debt` 分岐で return するため） |
| `--repo REPO` | str | `None` | 差分対象の repo 名。repo が複数あり曖昧なときは必須（`_select_target()`、無指定かつ曖昧なら `_die("差分を判定する repo が曖昧です")` で exit 2） |
| `--strict` | flag | `False` | GRAY（未接続の変更）も NG にする |
| `--strict-cross` | flag | `False` | FOLLOWUP（別 repo 側の追随待ち）も NG にする |
| `--debt` | flag | `False` | 差分でなく**全体負債**をしきい値と突合するモードに切り替える |
| `--max-broken N` | int | `None`（未指定＝チェックしない） | `--debt`: 壊れた参照の許容件数 |
| `--max-undocumented N` | int | `None` | `--debt`: 未文書化 code の許容件数 |
| `--max-untested N` | int | `None` | `--debt`: 未テスト code の許容件数 |

### 2.1 モード分岐（`_run()` L1054-1101）

- **`--debt` あり** → `mapdata`（`build_map()` の棚卸し結果）の `broken_refs` / `orphans.undocumented`
  / `orphans.untested` の件数を、指定された `--max-*` としきい値突合。`--max-*` を1つも指定しなければ
  常に PASS（`findings=[]`）。
- **`--debt` なし（差分モード）** → `--base`（or `$KIRO_BASE_REV`）必須。無ければ
  `_die("差分の基準 rev がありません（--base か $KIRO_BASE_REV。全体負債は --debt）")` で **exit 2**。
  `classify_impact()` で GREEN/AMBER/GRAY/FOLLOWUP に分類し、
  `ng = bool(amber) or (--strict and bool(gray)) or (--strict-cross and bool(followup))` で判定。

### 2.2 標準出力フォーマット

**テキスト（既定）**:
```
差分: sandbox HEAD~1..作業ツリー（4 ファイル）
  [AMBER] tools/kiro-flow/kiro-flow.py が変更されたが sandbox:CHANGELOG.md が未更新（根拠: CHANGELOG.md:1088 (inline)）
  [GRAY] ...
NG: ドリフトあり — `codd-gate tasks` で修復タスクを生成できる
```
（PASS 時は最終行が `OK: 一貫性ゲート通過`。`--debt` の場合は `_print_summary()` の集計行
`ノード: doc N / code N / test N ／ エッジ: N` と `負債: 壊れた参照 N / 未文書化 N / 未テスト N` の後に
`NG: <label> <count> 件 > 許容 <limit>` を findings ごとに列挙。実行確認済み↓）

```
$ codd-gate verify --repos repos.json --repo-dir sandbox=. --debt --max-broken 0
ノード: doc 445 / code 353 / test 39 ／ エッジ: 521
負債: 壊れた参照 1333 / 未文書化 245 / 未テスト 331
  - sandbox:.github/instructions/common.instructions.md 行54: ~/.copilot/skill-registry.json が解決できない
  ...（先頭10件のみ）
NG: 壊れた参照 1333 件 > 許容 0
```

**`--json`（所見の正）**: 差分モードは `classify_impact()` の戻り値そのまま
`{"base","repo","changed","green","amber","gray","followup"}` を1行 JSON で出力。`--debt` モードは
`{"debt": {"broken","undocumented","untested"}, "findings": [...]}`。いずれも `sort_keys=True`、
改行区切り1オブジェクト（複数行にはならない）。**`--json` 指定時、`--strict`/`--strict-cross` は
終了コード判定には影響するが、出力 JSON 自体に `ng` フラグは含まれない**（呼び出し側が exit code で
判定する設計。実行確認済み: `verify --json` は amber を含んでいても exit=0 になり得る点に注意
— 下記終了コード表参照）。

### 2.3 終了コード

| 状況 | exit |
|---|---|
| ゲート PASS（差分: amber 無し かつ strict 系条件も無し／debt: 全 findings 空） | `0` |
| ゲート NG（差分: amber 有り、または strict 条件成立／debt: いずれかの `--max-*` 超過） | `1` |
| 環境・設定エラー（repos ファイル不在・空・解釈不可／対象 repo dir 未解決／base rev 未指定・差分取得不可／repo 曖昧／スキャン可能 repo 皆無） | `2`（`_die()`） |
| argparse 自体のエラー（未知の引数・subcommand 省略等） | `2`（argparse 既定。実行確認済み） |

**重要な非対称性**: `verify`（差分モード、`--debt` なし）の `--json` 出力は `imp` 辞書のみで
`ng`（gate 判定結果）を含まない。JSON を読む側（例: 将来の `tasks --debt` 連携や外部 CI）が
「amber が1件でもあれば NG」を自前で再実装する必要は無く、**判定そのものは exit code に一本化**
されている（`--json` は所見の中身を見るためのもので、ゲート可否は exit code を見る、という分離）。

## 3. `tasks` 固有引数

```
codd-gate tasks [common引数...] [--base BASE] [--repo REPO] [--debt] [--priority N]
                [--max N] [--cohort] [--inbox DIR]
```

| 引数 | 型 | 既定 | 意味 |
|---|---|---|---|
| `--base BASE` | str | `$KIRO_BASE_REV` | 差分モードの基準 rev（`--debt` 時は不要） |
| `--repo REPO` | str | `None` | 差分対象 repo 名（曖昧なら必須。verify と同じ解決規則） |
| `--debt` | flag | `False` | 全体負債からタスク化（既定は差分の amber/gray/followup からタスク化） |
| `--priority N` | int | `1` | 生成タスクの `priority` 値（gray/未文書化/未テスト系は `max(priority-1, 0)` に下げる） |
| `--max N` | int | `20` | `--debt`: 種別（壊れた参照/未文書化/未テスト）ごとの上限件数 |
| `--cohort` | flag | `False` | `--debt`: 未文書化/未テストを repo 単位の cohort（pilot-then-batch）にまとめる |
| `--inbox DIR` | str | `None` | 標準出力でなく `<dir>/<id>.json` へ1タスク1ファイルで書き出す |

### 3.1 モード分岐（`_run()` L1073-1085）

- **`--debt` あり** → `tasks_from_debt(mapdata, priority, max, cohort)`。`scan`/`--debt` 系の棚卸し
  結果からタスク化（`--base` 不要・指定しても無視）。
- **`--debt` なし** → `--base` 必須（無ければ `verify` と同じ `_die` で exit 2）。
  `tasks_from_impact(classify_impact(...), priority)` で amber/gray/followup をタスク化
  （green は対象外＝タスク化されない）。

### 3.2 標準出力フォーマット

- **`--inbox` 未指定（既定）**: タスク仕様の配列を **1つの JSON（`indent=1`）** として標準出力に
  出力（`json.dumps(specs, ..., indent=1)`）。1行1オブジェクトの JSON Lines ではなく、
  `[ {...}, {...} ]` という単一の JSON 配列である点に注意（`intake_cmd` 等で読む側は
  `json.load(stdout)` で配列として受け取る）。
- **`--inbox DIR` 指定時**: 標準出力には `"{len(specs)} タスクを {dir} へ書き出しました"` という
  1行サマリのみを出し、実体は `dir/<id>.json`（1タスク1ファイル、`indent=1`、末尾改行あり）に
  書く。`id` は `_task_id()` による決定的ハッシュ付き slug（`codd-<kind>-<slug>-<hash6>`、
  48字以内）で、**同じ発見からは常に同じファイル名** = 再実行しても重複投入されない冪等キー。
- 個々のタスク仕様は `schemas/task.schema.json` 準拠のオブジェクト。実際に生成される代表キー:
  `id`, `title`, `verify`（多くは `codd-gate check ...` を埋め込んだ自己検証コマンド）,
  `paths`, `priority`, `note`, `expect: "changes"`（doc-stale/broken-ref 系のみ）,
  `accept`/`workspace`（followup 系のみ。他 repo 側の作業なので `verify` でなく自然言語の
  `accept` と `workspace` 指定）, `cohort_items`（`--cohort` 時のみ）。

実行確認（差分モード、`--base HEAD~1`、amber 50件）:
```json
{
 "id": "codd-doc-github-skills-kiro-flow-SKIL-708aed",
 "title": "tools/kiro-flow/kiro-flow.py の変更をドキュメント .github/skills/kiro-flow/SKILL.md へ反映する（repo sandbox）",
 "verify": "codd-gate check --repo-dir sandbox=. --doc .github/skills/kiro-flow/SKILL.md --code tools/kiro-flow/kiro-flow.py --fresh",
 "paths": ".github/skills/kiro-flow/SKILL.md",
 "priority": 1,
 "expect": "changes",
 "note": "tools/kiro-flow/kiro-flow.py が変更されたが sandbox:.github/skills/kiro-flow/SKILL.md が未更新（根拠: ...）"
}
```
`tasks --debt --max 2 --inbox <dir>` は実際に `<dir>/codd-doc-...json` 等6ファイルを書き出すことを
実行確認済み（壊れた参照/未文書化/未テストの3種 × 上限2件のバッチ分だけファイルが生成される）。

### 3.3 終了コード

**`tasks` は所見の有無にかかわらず常に `0`**（`_emit_tasks()` は例外時以外 `return 0` 固定。
L1104-1114）。「NG（ドリフトあり）」の判定は `verify` の役割で、`tasks` は変換・出力に徹する
という設計（design doc の鉄則4「修復の知能は kiro-project へ委譲。本体は分類とタスク生成まで」）。
異常系（`--base` 未指定・repos 未解決等）は `verify` と同じ `_die()` 経由で **exit 2**。

## 4. `--debt` の意味論まとめ（`verify`/`tasks` 共通の横断仕様）

- `--debt` は「差分（`--base` 基準の変更点）」ではなく「**現在のマップ全体の既存負債**」を対象にする
  フラグで、`verify`/`tasks` どちらに付けても「入力を差分からマップ全体棚卸しに切り替える」という
  同じ意味を持つ。`--base`/`--repo` は `--debt` 時は評価前に無視される（差分モード分岐の外側で
  `--debt` 分岐が先に return するため、`--base` を明示的に渡してもエラーにはならないが読まれない）。
- `verify --debt` は `--max-broken`/`--max-undocumented`/`--max-untested` という**ラチェット**
  （既存負債の上限固定）としてのみ機能する。しきい値を1つも指定しなければ何を検出しても
  無条件 PASS（brownfield で「今ある負債では止めない」という design doc 鉄則3の直接実装）。
- `tasks --debt` は同じ棚卸し結果を修復タスクへ変換する側で、`--max`（種別ごとの件数上限。
  `verify` の `--max-*` とは別物＝こちらは「一度に出すタスク数」の上限であって「許容負債件数」
  ではない）と `--cohort`（pilot-then-batch のバッチ化）を持つ。

## 5. 終了コード規約（全サブコマンド横断・最終まとめ）

| exit | 意味 | 該当箇所 |
|---|---|---|
| `0` | 正常終了。`scan`/`tasks`/`check`(PASS)/`verify`(PASS) 共通 | — |
| `1` | `verify` のみ: ゲート NG（差分 amber、strict 系条件、または debt しきい値超過） | `_run()` L1071, L1101 |
| `2` | 環境・設定・引数エラー（`_die()` 全箇所、argparse 自身のエラー） | `_die()` L89-91、`argparse` 既定動作 |

`_die()` 呼び出し一覧（＝ exit 2 になる具体的状況）: repos がマッピング形式でない／repos ファイル
不在・空・解釈不可／`check` で `--refs`/`--covered`/(`--doc`+`--code`) いずれも無指定／設定ファイル
解釈不可／スキャン可能 repo が皆無／`--base` 未指定（`--debt` 時を除く）／差分取得できない
（rev 不正）／repo dir 未解決／repo 名が曖昧。

## 6. 検証内容と結果

- ソース読解: `tools/codd-gate/codd-gate.py` の `main()`/`_run()`/`_emit_tasks()`/`_die()`/
  `classify_impact()`/`tasks_from_impact()`/`tasks_from_debt()` を全読（該当行は各節に記載）。
  インストール済みバイナリ (`/Users/nitto/.local/bin/codd-gate`) とリポジトリ内ソースが
  バイト同一であることを `diff` で確認済み。
- `codd-gate verify --help` / `codd-gate tasks --help` を実行し、argparse 定義（本書 §2/§3 の表）
  と一致することを確認。
- 一時ディレクトリに手書き `repos.json`（`{"sandbox": {"base": "main", "url": "...", "owns": ["**"]}}`）
  を作り、このリポジトリ自身（`sandbox`）に対して以下を実行し、本書に記載した出力例・終了コードを
  すべて実測で裏取りした（読み取りのみ・作業ツリーへの書き込みなし。作成した一時ファイルは
  スクラッチパッド配下）:
  - `verify --base HEAD~1`（テキスト） → amber 50件検出、`NG: ドリフトあり...`、**exit=1**
  - `verify --base HEAD~1 --json` → `{"amber":[...],...}` 1行 JSON、**exit=0**（`--json` はゲート
    判定を出力に含めない非対称性を実測で確認。§2.2 に記載）
  - `verify --debt --max-broken 0 --json` → `{"debt":{...},"findings":[...]}`、**exit=1**
  - `tasks --base HEAD~1` → JSON 配列50件、**exit=0**
  - `tasks --debt --max 2 --inbox <dir>` → `<dir>` に6ファイル書き出し、**exit=0**
  - `codd-gate`（subcommand省略）/ `codd-gate verify --unknown-flag` → argparse エラー、**exit=2**
- `python3 -m pytest tools/codd-gate/tests -q` → **30 passed**（codd-gate 自身の単体テスト。
  本タスクの調査内容と齟齬なし）。
- 完了条件のシェルコマンドをそのまま実行して確認（下記「未解決事項」参照）。

## 7. 採用した前提・未解決事項・範囲外で見つけた問題

**前提**:
- 本タスクは「CLI インターフェースの調査・列挙」が成果物であり、コード変更は行っていない
  （`git status` クリーン）。`--repo`/`--sync`/`--map`/`--config`/`check` の詳細は依頼文の
  対象引数（`--repos`/`--repo-dir`/`--base`/`--strict`）ではないため簡潔な言及に留めた。

**未解決事項（完了条件シェルコマンドについて）**:
- 完了条件 `python3 -m pytest tools/kiro-project/tests -q -k codd && codd-gate verify --repos
  ./.kiro-project/repos.json --repo-dir sandbox=. --base "${KIRO_BASE_REV:-HEAD~1}" --strict` を
  この worktree でそのまま実行すると、**両方失敗する**:
  - `pytest -k codd` → **exit 5**（`515 deselected`。`tools/kiro-project/tests` に `codd` を
    含む名前のテストが現状ゼロ）。
  - `codd-gate verify --repos ./.kiro-project/repos.json ...` → **exit 2**
    （`[codd-gate] エラー: repos レジストリが見つかりません: .kiro-project/repos.json`）。
- 原因は本タスクの範囲外: `.kiro-project/`（`repos.json` 含む）はコミット `645d86f`
  “Remove status.json configuration file from the project” で丸ごと削除されており、この worktree
  には存在しない。また kiro-project 側に codd-gate 検出用のテスト・自動配線ロジックはまだ実装
  されていない（同 run の並行タスク `s1`/`s6` の調査でも同じ根本原因・同じ exit コードを独立に
  確認済み）。**この2点の解消（`.kiro-project/repos.json` の復元・生成ロジック実装、および
  `tools/kiro-project/tests` への `codd` テスト追加）は本タスク（CLI I/F 調査）の対象ではなく、
  同 run の実装系タスク（規約結線・自動検出実装）の責務**と判断し、本タスクでは着手していない。
  本書の CLI 仕様（引数・出力・終了コード）自体は、上記とは独立に §6 の実測で裏取り済みであり、
  後続の実装タスクの入力として使える状態にある。
- 範囲外で見つけた問題として記録のみ: `verify`（差分モード）の `--json` 出力に判定結果
  （`ng`/PASS-NG）が含まれず、exit code でしか判定できない（§2.2/§2.3）。外部 CI や
  `regression_cmd` 以外の経路で JSON 出力だけを見て判定しようとする実装があれば、この非対称性を
  踏まえる必要がある。
