# 一貫性ゲート状態セクション — 差し込み先の DOM / レンダリング関数 調査

対象リポジトリ: ynitto/sandbox `tools/agent-dashboard`（ブランチ `ap/dashboard-163827`）
調査のみ。ファイルは書き換えていない。

---

## 0. 前提の訂正（重要）

タスク指示は「`tools/agent-dashboard/src/renderer/renderer.js` の概要／プロジェクト情報セクションと needs の描画箇所」を指すが、
**renderer.js は既に分割されている**（`src/renderer/sections/*.js`）。renderer.js（1781行）に残っているのは
共有 state・`openTechnicalInfo()` などの共通部分だけで、概要・プロジェクト設定・needs の描画は sections 配下にある。

読み込み順は `src/renderer/index.html:595-` の順（core=renderer.js → sections/* → features → bootstrap）。
クラシックスクリプトのグローバルスコープ共有なので、新関数は sections のどれかにトップレベル関数として足せば全域から呼べる。

テストは `test/helpers/renderer-src.js` が renderer.js + sections/* + bootstrap.js を**結合した1本の文字列**を返し、
各テストが `grab('関数名')` で関数本体を切り出して `new Function` で評価する（`test/overview-ui.test.js:11-30` が典型）。
**純関数（ビューモデル）として切り出せば既存流儀でテストできる。**

---

## 1. 「概要」タブ — `src/renderer/sections/overview.js`

| 要素 | 位置 |
|---|---|
| ペイン | `index.html:93` `<div id="tab-overview" class="tabpane active" data-feature="agent-project">` |
| エントリ | `overview.js:279 renderOverview()` — `$('tab-overview').innerHTML = ...` を一括生成 |
| 集計純関数 | `overview.js:157 overviewSummary(p, flowRuns)` → `{live, undecided, working, waiting, done, total, progress, activeRuns, headline, tone}` |
| ヒーロー | `overview.js:302-315` `<section class="summary-hero tone-…">`（現在の状態・目標・進捗バー） |
| カード3枚グリッド | `overview.js:317-348` `<div class="overview-grid">` 内に `.summary-card action-card` / `.progress-card` / `.deliveries-card` |
| 計画バージョン節 | `overview.js:207 overviewVersionsHtml(p)` → `<section class="overview-version-section">`（`renderOverview` の `:349` で連結） |
| バインド | `overview.js:352-365`（`data-summary-tab` でタブ遷移、`bindLifecycleButtons(el)`） |

**差し込み候補（推奨）**: `overview.js:349` の `${overviewVersionsHtml(p)}` の直前に
`${consistencyGateHtml(p)}` を1行足し、`overviewVersionsHtml` と同形の
「HTML を返す純関数 + `renderOverview` 末尾でイベントバインド」パターンで実装する。
`.overview-grid` の4枚目カードにする案もあるが、グリッドは3カラム前提の CSS なので節（section）として独立させる方が安全。

## 2. 「プロジェクト設定」タブ — `src/renderer/sections/overview.js`（同ファイル後半）

| 要素 | 位置 |
|---|---|
| ペイン | `index.html:101` `<div id="tab-project-settings" …>` |
| エントリ | `overview.js:368 renderProjectSettings()` |
| 「プロジェクト定義」カード | `overview.js:394-404` `<section class="project-settings-card">` + `.settings-action-grid` 内に `<button data-edit="charter.md">` … `data-edit="repos.json"` |
| 「診断」カード | `overview.js:405-410` — `#btn-project-technical-info` → `openTechnicalInfo()` |
| 危険な操作 | `overview.js:377-384` |
| バインド | `overview.js:414-420`（`data-edit` → `openProjectFile(name)`） |

`openProjectFile`（`sections/form-edit.js:16`）は `policy.md` / `repos.json` / charter を専用フォームへ振り分け、
それ以外は `openEditFile(name)` で生テキスト編集にフォールバックする。**設定 yaml へ導線を出すならここが唯一の口。**
ただし現状 `openEditFile` は **`p.dir`（状態ディレクトリ）基準**で名前解決している前提なので、
`.agent/agent-project.yaml`（= `p.workspace/.agent/`）を開くには相対パス指定の可否を要確認（範囲外の注意点、§6）。

## 3. 「詳細情報」ダイアログ — `src/renderer/renderer.js`

| 要素 | 位置 |
|---|---|
| HTML 生成 | `renderer.js:1112-1128 technicalProjectInfoHtml()` — `<section class="developer-summary">` 内の `<dl class="developer-facts">`（ワークスペース／状態ディレクトリ／実行データ／検出方法／実行エンジン） |
| 表示 | `renderer.js:1131 openTechnicalInfo()` → `$('technical-project-info').innerHTML = …` → `$('dlg-technical-info').showModal()` |
| 呼び出し元 | `overview.js:420`、`sections/history.js:41`、`sections/node-detail.js:503` |

コマンド文字列そのもの（`codd-gate verify --base …`）のような技術的な生値を出すなら、
`developer-facts` の `<dl>` に行を足すのが既存の粒度に一番合う。
概要側は「結線済み／未結線」の状態と有効化導線だけにする、という二段構えが取れる。

---

## 4. needs 側 — `src/renderer/sections/needs.js`（1706行）

### 4.1 レンダリング経路

```
renderNeeds()                       :1525  $('tab-needs') 一括描画。sig で差分再描画を抑制（:1560-1569）
 ├ needsViewModel()                 :1185  フィルタ・並び・選択の純関数
 ├ needListItemViewModel()          :1255  一覧1行のビューモデル（純関数・テスト対象）
 │   └ needListSummary()            :1250  失敗要約 or NEED_ASK を「判断すること」列へ
 ├ needListItemHtml()               :1275  <button class="need-list-item"> … .need-list-summary.failure
 └ renderNeedDetail(p, n)           :1363  <article class="need-detail-card kind-…">
     ├ commandFailureHtml()         :167
     ├ finalVerificationFailureHtml()      （needFinalVerificationFailure :357 の結果）
     ├ <section class="need-decision">     「判断すること」
     ├ <section class="need-facts">  :1406
     │   ├ needAssistActionsHtml()  :1235  AI 診断ボタン群
     │   └ renderNeedFacts(n)       :1295  ★ 検証失敗ブロックの本体
     ├ <section class="need-response">     needActionsHtml() :178 / needVerifyRevisionHtml() :466
     └ <section class="need-evidence">     spec・成果物・判断材料
bindNeedDetail(root)                :1426  data-* 属性でイベント委譲
```

### 4.2 回帰失敗要約（needs-diagnosis）の描画位置 — ここが差し込み点

**`renderNeedFacts(n)`（`needs.js:1295-1361`）冒頭**が該当。

```js
const failure = needFailureViewModel(n);          // :1211
if (failure) {
  facts.push(`<div class="need-diag"><span class="label-chip">検証失敗</span><strong>…summary…</strong></div>`);   // :1299
  facts.push(`<div class="need-resolution"><span class="label-chip">確認・対処</span>…</div>`);                     // :1301
  facts.push(`<dl class="need-failure-context">…</dl>`);                                                          // :1313
}
```

`need-failure-context` の `<dl>` は
`['分類', context.category] / ['対処対象', owner] / ['コマンド', command] / ['作業場所', workdir] / ['終了コード', exitCode] / ['確認対象', resolvedTarget||target]`
の6行（`:1304-1311`、空値は落とす）。**ここに「ゲート」行を足す・分類がゲート由来のとき文言を変える、が最小差分。**

### 4.3 ビューモデルの純関数（テストしやすい層）

- `needFailureViewModel(need)` `:1211` — `need.failureSummary` があるときだけ `{summary, resolution, context}` を返す。
  **散文の再解釈は禁止（コメント :1203-1210 に明記）**。データは producer から運ぶだけ。
- `canDiagnoseNeed(need)` `:1227` — 「AIで失敗を診断」ボタンを出すかの推測判定。
  `:1232` の正規表現が `回帰` を含む: `/(?:検証|verify|テスト|test|回帰|コマンド)[^\n]*(?:失敗|FAIL|NG|exit\s*=\s*[1-9]\d*)/i`。
  agent-project 本体が回帰失敗時に立てる `_block(… "回帰検知: グローバル検査 \`{regression_cmd}\` 失敗 — …")`
  （`tools/agent-project/agent_project/mr.py:582`）はこの正規表現にヒットする。
  **ここが推測してよい唯一の場所**というのが設計上の約束（`:1223-1226`）なので、断定表示側（`needFailureViewModel`）に
  ゲート判定を混ぜないこと。

### 4.4 codd-gate 専用の分岐は「存在しない」

`src/` 全体を `codd` で grep してヒット 0。needs の種別も 4 種のみ:

```js
const NEED_KIND_LABELS = { 'plan-review':'計画レビュー', review:'検収', milestone:'マイルストーン', blocked:'対応依頼' };  // needs.js:103
```

codd-gate の失敗は **`kind: blocked` の回帰失敗として届く**（専用 kind は無い）。
識別できるのは `failureContext.category` / `failureContext.command`（`codd-gate verify …` を含む）だけ。

`failureContext` の出所は agent-project の frontmatter（`failure-category` ほか）で、
dashboard 側は `src/features/agent-project/main/project.js:446 _failureFromFrontmatter()` がそのまま移送する。
frontmatter が無い旧記録だけ `:467 _diagnoseFailure()` がフォールバック解析し、category は
`実行環境 / パス・入力 / テスト失敗 / 検証対象なし / 検証工程 / 不明な検証失敗` の6値。
**codd-gate 用の category を dashboard 側で新設しない**（producer 側の契約。範囲外）。

---

## 5. regression_cmd / intake_cmd をどこから取るか（最大の欠落）

**現状 renderer には一切届いていない。** `src/` 全体で `regression_cmd` / `intake_cmd` のヒット 0。

読める場所は既に main 側にある:

- `src/features/agent-project/main/toolconfig.js:46 readToolConfig(baseName, baseDirs)`
  → `{ file, values }` を返す。`values` は `parseFlatYaml`（`:33`）でトップレベル `key: value` だけを拾う平坦パース。
  クォート剥がし・行末コメント除去済みなので、`regression_cmd: 'codd-gate verify …'` はそのまま `values.regression_cmd` で取れる。
- `src/features/agent-project/main/project.js:1612` で `readProject` が既に
  `readToolConfig('agent-project', [workspace, ...agentDirCandidates(workspace)])` を呼んでいる
  （現状は `state_branch` の取得にしか使っていない）。

**したがって最小差分は `readProject` の戻り値（`:1692-1727`）に1フィールド足すだけ。**
例: `toolConfig: { file: projectCfg?.file, regressionCmd: …, intakeCmd: … }`。
`projectState`（`:1715` = `project.json`）は `{charters, updated}` しか持たず、ここには**無い**ので混同しないこと。

実測（このプロジェクトの `.agent/agent-project.yaml`）:

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
```

### 有効化導線の一次資料（README と同じ文言を出すための出典）

`tools/agent-project/README.md:272-295`「一貫性ゲート（codd-gate 連携・オプション）」:

- 有効化 = `.agent/agent-project.yaml` に `regression_cmd` / `intake_cmd` の2行を書く
- `regression_cmd` だけは CLI で冪等 upsert できる:
  `python3 codd_gate_regression.py --config .agent/agent-project.yaml`
  （終了コード 0=注入済み / 1=設定ファイル無し / 2=引数誤り / 3=codd-gate が使えず何も書いていない）
- `intake_cmd` に対応する注入 CLI は**無い**（yaml を直接編集）
- 結線確認は書き込まずに `python3 codd_gate_wiring.py --config .agent/agent-project.yaml`
  → `regression_wired` / `intake_wired` と推奨コマンド文字列を JSON で返す（= sibling CLI）
- doctor の所見に載せるには `hooks:` + `  wiring: codd_gate_wiring` の2行が別途必要

---

## 6. 実装時に効く制約（後続タスク向け）

1. **`renderNeeds` の sig（`needs.js:1560-1567`）** — 再描画の署名。新しく参照する need のフィールドを
   ここに足さないと、値が変わっても画面が更新されない。プロジェクト設定由来の値を needs 側で使う場合も同様。
2. **`renderNeedFacts` は文字列連結のみ** — 戻り値は `facts.join('')`。DOM API は使えない。
   イベントが要るなら `data-*` 属性を置いて `bindNeedDetail`（`:1426`）でまとめて拾う（既存流儀）。
3. **推測してよい場所の分離を壊さない** — `needFailureViewModel`=断定（frontmatter のみ）／
   `canDiagnoseNeed`=推測（散文可）。この境界はコメントで明示された設計判断。
4. **XSS** — 全て `esc()` / `inlineMd()` / `proseHtml()` を通す。コマンド文字列は `<code>${esc(v)}</code>`
   （`needs.js:1314` が既にこの形）。
5. **設定 yaml は内蔵エディタで開けない（検証済み）** — `openProjectFile` → `openEditFile`（`form-edit.js:361`）は
   `api.readProjectFile(p.dir, name)` を呼び、main 側 `authoring.js` が
   `EDITABLE_FILES`（`:20-27` = charter.md / policy.md / rules.md / repos.{json,yaml,yml}）の**ハード許可リスト**で弾く。
   さらに `editablePath`（`:46-55`）が `path.basename(name) !== name` を拒否し、必ず `p.dir` 直下に join する。
   `.agent/agent-project.yaml` は `p.workspace/.agent/` にあるので**どちらの条件も満たさない**。
   → 内蔵編集を使うなら main 側の許可リスト拡張が必要（agent-project 本体設定の書換になるため慎重に）。
   **より軽い既存の口**: `api.openPath(絶対パス)`（`preload.js:29` → `base/main/shell-actions.js:3`。パス制限なし）。
   needs カードの `data-open` ボタン（`needs.js:1143`、バインドは `bindNeedDetail` `:1428-1430`）が既にこの形で
   絶対パスを OS の既定エディタに渡している。`readToolConfig` の戻り値 `file` がそのまま絶対パスなので、
   `<button data-open="${esc(toolConfig.file)}">設定ファイルを開く</button>` が最小コストの有効化導線になる。
6. **done 不変条件** — UI から状態を書き換えないこと（スコープ外指定）。有効化は
   「yaml を開く」か「実行すべきコマンドを提示する」に留めるのが安全。既存の
   `startAgentProject`（`overview.js:94-120`）が「CLI が無ければ人が打つコマンドをそのまま見せる」
   という前例になっている。

---

## 7. 再利用できる既存 CSS クラス

`src/renderer/styles.css` に既存: `.project-settings-card` / `.settings-action-grid` / `.summary-card` /
`.summary-kicker` / `.summary-link` / `.summary-actions` / `.overview-version-section` / `.developer-facts` /
`.label-chip` / `.need-diag` / `.need-resolution` / `.need-failure-context` / `.badge warn` / `.muted`。
新規クラスを足すより、これらの組み合わせで済ませるのが既存トーンに合う。
