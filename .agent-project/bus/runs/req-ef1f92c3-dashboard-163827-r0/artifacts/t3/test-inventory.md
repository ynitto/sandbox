# t3 棚卸し: needs-diagnosis.test.js / overview-ui.test.js の既存アサーションと差し込み点

対象リポジトリ: ynitto/sandbox（ブランチ ap/dashboard-163827）
対象: `tools/agent-dashboard/test/needs-diagnosis.test.js`, `tools/agent-dashboard/test/overview-ui.test.js`
本タスクはコード変更なし（調査のみ）。ワークツリーは未変更。

---

## 0. 結論（後続タスクが最初に読む3行）

1. 一貫性ゲート（regression_cmd / intake_cmd / codd-gate）に関する記述は `tools/agent-dashboard/src/` にも `test/` にも **1件も無い**。完全な新規追加であり、既存アサーションと衝突する語は現状ゼロ。
2. `parseNeeds` は既に frontmatter から `failure-class` / `failure-phase` / `failure-chain` / `verify-verdict` を読んで `need.failureClass` 等に載せているが、**テストも描画も一切これを使っていない**（死んだデータ）。codd-gate / 回帰失敗の要約表示は、ここに接ぐのが最短。
3. 差し込み点は 2 つ。needs 側 = `needs-diagnosis.test.js` に `card()` の frontmatter 版ヘルパを足して `parseNeeds` の新フィールドを検証。概要側 = `overview-ui.test.js` の `grab()` + `new Function` 方式で、`src/renderer/sections/overview.js` に置いた **トップレベル `function` 宣言**を検証。

ベースライン確認（変更前の状態）:

```
$ node test/needs-diagnosis.test.js   → 11 passed
$ node test/overview-ui.test.js       → overview-ui: all tests passed
```

---

## 1. needs-diagnosis.test.js

### 1.1 ロード方式・スタブ

- `require('../src/main/project')` のみ。`src/main/project.js` は 4 行の互換シムで、実体は `src/features/agent-project/main/project.js`。
- **fs アクセスなし・renderer 非依存・スタブ一切なし**。純粋な `project.parseNeeds(text, id)` の単体テスト。
- 自前のミニランナー:

  ```js
  let passed = 0;
  function test(name, fn) { fn(); passed += 1; console.log(`ok - ${name}`); }
  ```

  **同期関数のみ**（`await` 不可）。例外が出たらそこで停止して非ゼロ終了。`node --test` ではない。

- 入力生成は `card(why, detail)` ヘルパ 1 本。生成される票の形:

  ```
  ---
  kind: blocked
  task-id: T-1
  ---

  # 要対応: T-1 — 何かをする

  ## Context and Problem Statement

  - なぜ: ${why}
  - 状態: blocked（agent-project の判断待ち）

  ## 判断材料（成果物の所在・差分・検証）
  ${detail}
  ```

  重要: `card()` は `failure-summary` を frontmatter に出さない。よって既存 11 件は全て **旧記録用フォールバック `_diagnoseFailure`（散文解釈）** の経路を通る。frontmatter 経路 `_failureFromFrontmatter` は本ファイルでは未検証。

### 1.2 壊してはならない期待値（全 11 件）

| # | テスト名 | 固定されている値 |
|---|---|---|
| 1 | 検証コマンドが対象を見つけられない失敗を要約する | `failureSummary` が `/tools\/x\/tests/` と `/見つけられませんでした/` に match |
| 2 | 見つからない相対パスは…実行条件と対処を提示する | `failureContext.category === 'パス・入力'`、`.owner === '検査設定・実行環境'`、`.workdir === '/work/project-agent-state/.agent-project'`、`.resolvedTarget === '/work/project-agent-state/.agent-project/.agent-project/repos.json'`、`failureResolution` が `/相対パス/` と `/絶対パス/` に match |
| 3 | 連鎖の途中で沈黙した工程は「失敗した工程」として名指しされる | `failureSummary` が `/grep -rq codd_gate/`・`/それより前の工程は成功/` |
| 4 | 旧形式でも「テストの失敗ではない」ことは言う | `failureSummary` が `/テストは 29 件成功/`・`/後段の工程/` |
| 5 | テストの失敗件数を要約する | `failureSummary === 'テストが 4 件失敗しました。'`（**完全一致**） |
| 6 | コマンド不在を要約する | `failureSummary` が `/codd-gate/`・`/見つかりません/` |
| 7 | 解釈できない失敗は終了コードだけ添える | `failureSummary === '検証コマンドが失敗しました（終了コード 2）。'`（**完全一致**） |
| 8 | 手掛かりが無ければ要約しない | `failureSummary === ''`（**空を保つ＝推測を足さない契約**） |
| 9 | 差分を成果物と内部の実行記録に分ける | `diff.artifacts` を `deepStrictEqual` で完全一致（2 要素）、`diff.internal.length === 3`、`evidenceThin === false` |
| 10 | 差分が内部記録だけなら痩せた判断材料 | `diff.artifacts` が `[]`、`diff.internal.length === 3`、`diff.truncated === 2`、`evidenceThin === true` |
| 11 | 差分リストは次のセクションで終わる | `diff.artifacts` が `['src/app.js']`、`diff.internal.length === 0` |

不変条件として特に重い 3 つ:
- **#8「解釈できないものは空文字」** — 新しいゲート判定を足すとき、ここで何かを埋めてはいけない。
- **#5 / #7 の完全一致** — 要約文へ接頭辞（例「[codd-gate] 」）を無条件に足すと落ちる。ゲート由来の情報は別フィールドに載せる。
- **#9〜#11 の `_splitDiff`** — `diff` は `{artifacts, internal, truncated, hasDiff}`。`artifacts` は `deepStrictEqual` なので **要素の追加も形の変更も不可**。

### 1.3 差し込み点（needs 側）

**(A) frontmatter 経路のヘルパを追加する** — 既存 `card()` は触らず、新しく `cardFm(fields, why, detail)` を足すのが安全（既存 11 件の入力が 1 バイトも変わらない）:

```js
function cardFm(extra, why, detail) {
  const fm = Object.entries(extra).map(([k, v]) => `${k}: ${v}`).join('\n');
  return card(why, detail).replace('kind: blocked', `kind: blocked\n${fm}`);
}
```

**(B) 検証できる既存フィールド（実装済み・テスト 0 件）** — `src/features/agent-project/main/project.js` の `parseNeeds` 末尾で既に設定済み:

| `need` のプロパティ | frontmatter キー | 現状 |
|---|---|---|
| `failureClass` | `failure-class` | 文字列。テスト無し・描画無し |
| `failurePhase` | `failure-phase` | 文字列。テスト無し・描画無し |
| `failureChain` | `failure-chain` | カンマ区切り→配列。テスト無し・描画無し |
| `verifyVerdict` | `verify-verdict` | 文字列。テスト無し・描画無し |
| `failureSummary` / `failureResolution` / `failureContext` | `failure-summary` ほか（`_failureFromFrontmatter`） | 描画あり（needs.js）だがこのファイルでは frontmatter 経路が未検証 |

`_failureFromFrontmatter` が読むキー全量: `failure-summary` / `failure-resolution` / `failure-category` / `failure-owner` / `failure-command` / `failure-workdir` / `failure-exit` / `failure-target`（`context.target` と `context.resolvedTarget` は同じ値）。`failure-summary` が非空のときだけ frontmatter 経路になり、それ以外は散文フォールバック。

→ **回帰失敗／codd-gate の要約は、新しい解析ロジックを書くより `failureClass` 等を検証・描画に載せるのが最小差分。** 「解析は producer（agent-project 本体）に一本化し、dashboard は運ぶだけ」という既存コメント（project.js parseNeeds 内、needs.js `needFailureViewModel` 冒頭）の方針とも一致する。

**(C) frontmatter パーサの制約**（新キーを足す場合の前提）: `parseNeeds` の frontmatter 走査は `/^([\w-]+):\s*(.*)$/` の 1 行 1 キー。**ネスト・複数行・リスト記法は読めない**。ゲート情報を票に載せるならフラットな `gate-*: 値` 形式にすること。

---

## 2. overview-ui.test.js

### 2.1 ロード方式・スタブ

3 系統の入力を使う。

1. **renderer 全文（文字列）**: `require('./helpers/renderer-src').read()`
   結合順 = `src/renderer/renderer.js` → `src/renderer/sections/{overview, backlog, authoring, form-edit, needs, flow, node-detail, gitlab, history, amigos, orchestration, cowork, kiro-loop}.js` → `src/renderer/bootstrap.js`。
   ⚠ **`src/renderer/features/*.js`（participation.js など）は結合対象外**。新しい表示コードをそこに置くと `grab()` も `renderer.includes()` も見つけられない。`sections/` に置くか、`test/helpers/renderer-src.js` の `SECTION_ORDER` を拡張する必要がある。
2. **生ファイル文字列**: `src/renderer/index.html`, `src/renderer/styles.css` を `fs.readFileSync` して正規表現で検査。
3. **関数の切り出し実行**: `grab(name)` + `new Function`。

`grab(name)` の仕様と制約:

```js
const at = renderer.indexOf(`function ${name}(`);   // 最初の一致。コメント中の同綴りも拾う
// 直後の '{' から波括弧を数えて対応する '}' までを切り出す
```

- 対象は **トップレベルの `function 名(` 宣言のみ**。`const f = (…) => …` / オブジェクトのメソッドは切り出せない。
- 文字列・正規表現リテラル中の裸の `{` `}` も数える単純カウンタ。テンプレートリテラルの `${}` は釣り合うので問題ないが、**片方だけの波括弧を含むリテラル（例 `/\}/`）を新関数に書くと切り出しが壊れる**。
- 依存は `new Function` の引数として注入する（sloppy mode で実行されるため、未注入のグローバル参照は呼び出し時に ReferenceError）。既存の注入例:
  - `new Function('coworkPathKey', \`${grab('coworkVisibleEntries')}; return …\`)(coworkPathKey)`
  - `new Function('esc','orchBadge','amigosWorkloadLabel','state', \`${grab('orchSkillRowHtml')}; ${grab('orchInstructionsPanelHtml')}; return orchInstructionsPanelHtml;\`)(escStub, orchBadgeStub, wlLabelStub, stateStub)` — **複数関数をまとめて切り出して 1 つの `new Function` に入れる**パターンあり。
  - スタブは素朴な関数リテラル（`esc` は最小限のエスケープ、`orchBadge` は `<span>` を返すだけ、`state` はプレーンオブジェクト）。DOM も `api` も出てこない。

### 2.2 壊してはならない期待値

**(a) `overviewSummary(project, flowRuns)`** — フィクスチャは `project` 定数（liveness running、未決 needs 1 件、byStatus doing2/offloaded1/ready3/inbox1/proposed1、claims 1、archive 2、backlog 5 件）+ runs 3 件（running/done/failed）。

`headline === '1 件の確認を待っています'`, `working === 3`, `waiting === 5`, `done === 2`, `total === 7`, `progress === 29`, `activeRuns === 1`。

⚠ **フィールド単位の `strictEqual` のみ。戻り値オブジェクト全体の `deepStrictEqual` は無い** → `overviewSummary` に新フィールド（例 `gates`）を足しても既存は落ちない。**ここが概要側の一番安い差し込み点。**

**(b) `appDoctorSummary(...)`** — `assert.deepStrictEqual(appSummary, { projects: 2, running: 1, needs: 3 })`。**戻り値の形が凍結**。キー追加不可。

**(c) `workspaceFeatureModel(...)`** — 2 件とも `deepStrictEqual` で `{ agentProject, cowork, defaultTab }`。**戻り値の形が凍結**。

**(d) cowork 系** — `coworkPathKey`（WSL UNC / POSIX / /mnt/c / Windows パスの正規化 4 件）、`coworkVisibleEntries`（選択プロジェクト絞り込み・index 保持・未選択で 0 件）、`coworkHasProjectConfig`、`amigosForProject`（homes/missions/errors の絞り込み、configFile null のホームは非表示）。ゲート表示とは無関係。

**(e) index.html への正規表現アサーション**（抜粋・全て維持必須）
- 存在: `id="dlg-cowork-history"` / `data-tab="overview"…>概要` / `data-feature="agent-project"` / `data-tab="backlog"…>タスク` / `data-tab="flow"…>実行` / `data-tab="project-settings"…>プロジェクト設定` / `class="nav-group"…aria-labelledby="projects-group-title"` / `id="projects-group-title"…>プロジェクト` / `id="project-list"` / `id="btn-refresh"…aria-label="表示を更新"` / `id="project-meta"…aria-live="polite"`
- **不在（追加禁止語）**: `id="btn-mode"` / `id="btn-project-settings"` / `class="doctor-tools"` / `id="btn-git-pull"` / `id="btn-git-heal"`
- 位置制約: `class="sidebar-header"` ブロック内に `id="btn-doctor"` `id="btn-refresh"` `id="btn-settings"` が有り `id="btn-new-project"` は無い / `id="projects-group-title"`〜`id="project-list"` の間に `id="btn-new-project"` が有る。**サイドバー周辺の DOM を動かすとスライス位置がずれて落ちる。**

**(f) renderer 文字列へのアサーション**
- 存在: `id="btn-sync-now"` / `共有先と同期` / `同期を修復` / `共有先確認:` / `remoteCheckedAt` / `refreshAll({ sync: false })` / `reloadProject({ refreshRemoteHealth: sync })` / `api.gitHealth(project.dir, refreshRemoteHealth)` / `orchInstructionsPanelHtml(overview)` / `api.orchestrationInstructionsSave` / `data-cowork-history` / `個別のrunを止める操作ではありません` ほか orchestration 系多数
- **不在（追加禁止語）**: `すべてのプロジェクトを表示`
- ラベル存在ループ（**概要の文言契約。ここが拡張ポイント**）:

  ```js
  for (const label of ['現在の状態', 'あなたの対応', '進捗', '成果', '対応する', 'タスクを見る', '実行を見る', '成果を見る'])
    assert.ok(renderer.includes(label), `概要に「${label}」が必要です`);
  ```

**(g) styles.css** — `button:focus-visible` / `@media (max-width: 680px)` / `.sidebar-actions button,[\s\S]*?min-width: 44px; height: 44px;` の 3 本。新しい CSS を `.sidebar-actions` セレクタと `min-width: 44px` の**間に**挿入すると 3 本目が壊れる可能性あり（`[\s\S]*?` の最短一致なので通常は安全だが、同セレクタ群を分割しないこと）。

**(h) `orchInstructionsPanelHtml` / `orchStatusPanelHtml`** — HTML 断片への `includes` と `(out.match(/…/g)||[]).length` による件数一致（`class="orch-skill-row"` が 1、`<tr>` が 2、`data-orch-wl="flow"` が 2）。**行数を数えるアサーションなので、これらのパネルに行を足すと落ちる。ゲート表示をここに混ぜないこと。**

### 2.3 差し込み点（概要側）

**(A) 表示モデル: `overviewSummary` に追記** — 戻り値の全体比較が無いので、`return { …, gates }` の形でフィールド追加が可能。テスト側は既存 `project` フィクスチャに `gates` 相当の入力を足し、`assert.deepStrictEqual(summary.gates, {…})` を新規行として追加できる（既存 7 本の `strictEqual` は無傷）。

**(B) 描画: `src/renderer/sections/overview.js` に純関数を新設** — 既存の `overviewVersionsHtml(p)` / `lifecycleCardHtml(p)` と同じ「`p` を受けて HTML 文字列を返すトップレベル `function`」にすれば、そのまま `grab()` できる。テスト側の書き方は `orchInstructionsPanelHtml` の節（overview-ui.test.js 196〜234 行）をそのまま雛形にできる:

```js
const panel = new Function('esc', /* 必要な依存だけ */,
  `${grab('overviewGatesHtml')}; return overviewGatesHtml;`)(escStub /*, … */);
const out = panel({ gates: { … } });
assert.ok(out.includes('…'), '…');
```

依存で注意するもの: `esc`（renderer.js 定義・要スタブ）、`proseHtml`（同上）、`state` / `api` / `$` / `switchTab`（DOM・IPC。**新関数からは呼ばない設計にしておくと `new Function` 一行でテストできる**）。

**(C) 呼び出し配線のアサーション** — 既存の `assert.match(renderer, /orchInstructionsPanelHtml\(overview\)/)` と同じ形で、`renderOverview()` の `el.innerHTML` テンプレートに差し込んだ呼び出し（例 `/overviewGatesHtml\(p\)/`）を検査できる。`renderOverview` 内の挿入位置候補は `${overviewVersionsHtml(p)}`（overview.js:349）の直前後、または「あなたの対応」カード（overview.js:318〜326）の隣。

**(D) 文言契約の拡張** — (f) のラベルループに新ラベル（例 `一貫性ゲート`, `有効化する`）を足すのが、概要に新表示が存在することを一行で保証する最小手段。

**(E) 有効化導線のデータ源** — `regression_cmd` / `intake_cmd` は現状どこからも読まれていない。`readProject()` は既に `readToolConfig('agent-project', [workspace, ...agentDirCandidates(workspace)])` を呼んでおり（`src/features/agent-project/main/project.js:1612`）、その `values` から現在使っているのは `state_branch` のみ。**同じ 1 回の読み込み結果から `regression_cmd` / `intake_cmd` / 設定ファイルパスを拾って戻り値に載せるのが最小差分**（読み込みの追加は不要）。
⚠ ただし `readToolConfig` の YAML パーサは `parseFlatYaml`（`src/features/agent-project/main/toolconfig.js`）= **トップレベルのフラットなキーだけ・インデント行は無視・行内 `#` 以降を切り落とす・空値はキーごと落とす**。`regression_cmd` がネストや複数行で書かれていると読めない。実運用の書式をこの制約と突き合わせて確認すること。
なお main 側の戻り値追加は `overview-ui.test.js` からは検証できない（このファイルは `src/main/*` を require しない）。main 側の検証は別ファイルが要る。

---

## 3. 範囲外だが後続で踏みやすい地雷

- **`needFailureViewModel` の戻り値は凍結されている。** `test/detail-tabs-ui.test.js:301` が `deepStrictEqual` で `{summary, resolution, context}` を完全一致検査。codd-gate 情報をこの戻り値に足すと **本タスク対象外のテストが落ちる**。ゲート情報は別の表示モデル関数を新設して運ぶこと。
- **needs 画面側の描画アサーションは `test/needs-layout-ui.test.js` / `test/detail-tabs-ui.test.js` にある**（`needs-diagnosis.test.js` は解析層のみ）。needs 上に codd-gate 表示を出すなら、検証はそちら側に足すのが既存の役割分担に沿う。
- `package.json` の `test` スクリプトは全テストを `&&` で直列に並べた 1 行。**新規テストファイルを足したらここへの追記が必要**（追記しないと CI から漏れる）。
- `src/renderer/features/` 配下は `renderer-src.js` の結合対象外（前述 2.1）。

---

## 4. 採用した前提・未解決事項

- 前提: 本タスクは「棚卸しと差し込み点の特定」であり、テストの追記・実装は後続タスクの担当と解釈した。よってワークツリーは一切変更していない。
- 前提: 「新表示」= 概要／プロジェクト情報での regression_cmd・intake_cmd の可視化と、needs での codd-gate／回帰失敗要約。手がかり文が途中で切れていた（「needs の codd-gate / 回帰失敗要約（needs-di…」）ため、`needs-diagnosis.test.js` の担当範囲＝解析層と読み替えて棚卸しした。
- 未解決: `regression_cmd` / `intake_cmd` の**実際の設定書式**（`.agent/agent-project.yaml` 内でフラットか、ネストか）は本ワークスペースに現物が無く未確認。`parseFlatYaml` の制約（2.3-E）に合致するかは agent-project 本体側の書式で要確認。
- 未解決: 「README と同じ有効化導線」の README は agent-dashboard の README ではない（`tools/agent-dashboard/README.md` に regression/intake/codd の記述は 0 件）。agent-project 本体の README を指していると解釈したが、現物は未参照。

## 5. 検証内容と結果

- `node test/needs-diagnosis.test.js` → `11 passed`（変更前ベースライン）
- `node test/overview-ui.test.js` → `overview-ui: all tests passed`（変更前ベースライン）
- `grep -rn "regression_cmd|intake_cmd|regressionCmd|intakeCmd" src/ test/` → 0 件
- `grep -rn "codd" src/` → 0 件（`test/needs-diagnosis.test.js` 内にはテストデータの文字列としてのみ登場）
- `grep -rn "failureClass|failurePhase|failureChain|verifyVerdict" test/ src/renderer/` → 0 件（main では設定済み＝未使用データであることを確認）
- `grep -rn "needFailureViewModel" src/renderer/ test/` → 定義 1・renderer 内呼び出し 4・`detail-tabs-ui.test.js` ほか 3 ファイルから参照されることを確認
- コード変更なしのため lint／型チェックは非該当。

@followup agent-project 本体側で needs 票の frontmatter に `failure-class` / `failure-phase` を codd-gate 由来の値で埋める運用が無いと、dashboard 側に表示を作っても常に空になる。書き手（本体）側の出力有無の確認を別タスクに。
@followup `parseNeeds` が読んでいる `failure-class` / `failure-phase` / `failure-chain` / `verify-verdict` は現在どこからも使われていない。表示に載せないなら削除、載せるなら描画とテストを付ける、の判断を別タスクに。
