# t1 調査結果 — dashboard から一貫性ゲート（codd-gate）を可視化するための既存経路

対象リポジトリ: `https://github.com/ynitto/sandbox`
作業ツリー: `/var/folders/8c/.../agent-flow-ws-23399-msltwa86/sandbox`
調査のみ。ファイル変更なし。

---

## 0. 結論（後続タスクが最初に読む3行）

1. `regression_cmd` / `intake_cmd` は **既存の設定リーダーでそのまま読める**（トップレベルのクォート付きスカラ）。パーサ追加は不要。
2. 挿し込み点は `readProject()`（`project.js:1596`）— **既に `projectCfg` を手元に持っている**（`project.js:1612`）。返り値オブジェクト（`project.js:1692-1727`）へキーを足すだけで renderer まで届く。
3. **既存の「設定編集」導線では `agent-project.yaml` を開けない**（allowlist ＋ root 相対の二重制約）。ここだけは新規実装が要る。詳細は §5。

---

## 1. agent-project 設定を読む既存経路

### 1.1 リーダー本体

`tools/agent-dashboard/src/features/agent-project/main/toolconfig.js`

| 位置 | 要素 | 内容 |
|---|---|---|
| `toolconfig.js:46` | `readToolConfig(baseName, baseDirs)` | `baseDirs` を順に走査し `<dir>/<baseName>.{yaml,yml,json}` の**最初に見つかった1件**を `{ file, values }` で返す。末尾に `agentHomeDir()`（`~/.agents` or `~/.agent`）を必ず足す |
| `toolconfig.js:33` | `parseFlatYaml(text)` | **トップレベルの `key: value` 行だけ**を拾う。インデント行（ネスト）・`#` 始まり・空値は捨てる。正規表現は `/^([A-Za-z_][\w-]*):\s*(.*)$/` |
| `toolconfig.js:24` | `stripQuotes(s)` | `"…"` / `'…'` を剥がす。行内コメント `\s+#.*$` も除去（`toolconfig.js:39`） |
| `toolconfig.js:70` | `lookupScalar(key, baseDirs)` | `agent-project` → `agent-flow` の順にキーを探す薄いラッパ |

### 1.2 探索ディレクトリ

`agentDirCandidates(base)` = `[<base>/.agents, <base>/.agent]`
（`src/base/main/agent-home.js:40`。`AGENT_HOME='.agents'` / `AGENT_HOME_LEGACY='.agent'`、新しい方が先）

project.js の既存 3 呼び出しはすべて同じ引数形:

```js
readToolConfig('agent-project', [workspace, ...agentDirCandidates(workspace)])
```

| 位置 | 関数 | 読んでいるキー |
|---|---|---|
| `project.js:1248` | `resolveProjectRoot()` | `root` / `state_branch` |
| `project.js:1576` | `resolveBusDir()` | `bus` |
| `project.js:1612` | `readProject()` | `state_branch`（変数名 `projectCfg`） |

**基準は `workspace`（登録フォルダ）であって `dir`（状態ルート）ではない。** `readProject()` は先頭で両方を確定済み（`project.js:1597-1598`）。

### 1.3 regression_cmd / intake_cmd が実際に読めることの実測

実プロジェクトの設定（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project/.agent/agent-project.yaml`）の該当行:

```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
```

- 行頭カラム0のトップレベルスカラ → `parseFlatYaml` の正規表現に一致する。
- 外側がシングルクォート → `stripQuotes` が剥がす。
- 値の中の `--base "$KIRO_BASE_REV"` はダブルクォートだが**外側がシングルなので影響なし**。
- ただし `toolconfig.js:39` の行内コメント除去 `replace(/\s+#.*$/, '')` は**クォート内も無差別に切る**。現行の 2 コマンドに ` #` は含まれないので実害なしだが、値に ` #` を含む設定を書かれると末尾が欠ける（§7 の範囲外所見 A）。

→ **`readToolConfig` に手を入れず、`cfg.values.regression_cmd` / `cfg.values.intake_cmd` をそのまま参照できる。**

### 1.4 参考: 設定ファイルの実パスを探す別経路

`actions.js:217` `findProjectConfig(...dirs)` — CLI 委譲時に `--config` へ渡す **yaml の実パス**を返す。
探索は `dir` / `agentDirCandidates(dir)` / `path.dirname(dir)` / `agentDirCandidates(path.dirname(dir))` の順で `agent-project.yaml|yml` を探す（json は見ない）。
「設定ファイルをエディタで開く／パスを表示する」導線を作るならこれが既存の正解。`readToolConfig` の戻り値 `.file` でも同じ実パスが取れる。

---

## 2. renderer へ渡すプロジェクト情報ペイロードの生成箇所

### 2.1 生成 → 配送

| 段 | 位置 | 内容 |
|---|---|---|
| 生成 | `main/project.js:1596` `readProject(workspaceDir, cfg)` | 1 プロジェクトの完全スナップショット。返り値リテラルは **`project.js:1692-1727`** |
| IPC | `main/ipc.js:69` | `dashboard:project` ハンドラ → `project.readProject(dir, loadConfig())` |
| preload | `preload.js:9` | `readProject: (invoke) => (dir) => invoke('dashboard:project', { dir })` |
| 受信 | `renderer/renderer.js:820` | `state.project = await api.readProject(state.selectedDir)` |

**ペイロードに1キー足す作業は `project.js:1692-1727` の1箇所のみ。** IPC / preload はスキーマ非依存の素通しなので変更不要。

### 2.2 公式契約 needs / inbox / commands のペイロード上の所在

| 契約 | ペイロードキー | 生成位置 | 補足 |
|---|---|---|---|
| **needs** | `needs` (`project.js:1706`) | `project.js:1601-1620` | `listMdDir(needsDir, parseNeeds)` → `synthesizeNeedsFromBacklog()`（`project.js:751`、needs ファイルの無い review/blocked/proposed を backlog status から補完）→ `attachDeliveryHintsFromBacklog()`（`project.js:810`）→ `commandFailure` 付与（`project.js:1607-1611`）→ `_repairStateDeliveryPaths()`（`project.js:1618`） |
| **inbox** | `inboxFiles` (`project.js:1697`) | `project.js:1646-1648` | `<root>/inbox/` の `.json/.md/.markdown/.txt` の**ファイル名配列のみ**（内容は読まない） |
| **commands**（書き） | ペイロードに無し（write-only） | `actions.js:183` `dropCommand()` | `<root>/commands/viewer-<action>-<slug>-<ts>.json` を `.tmp` → `rename` でドロップ。本体の `ingest_commands` が拾う |
| **commands**（読み・失敗） | `needs[].commandFailure` | `project.js:1607` `listCommandFailures(dir)` | `commands/*.err` を task-id で needs カードへ紐付け。決着済み（`need.decided`）には出さない |
| **commands**（読み・replan） | `replanPending` (`project.js:1698`) | `project.js:1210` `replanRequestPending(dir)` | `commands/*.json` の `command:'replan'` または `<root>/.replan.request` マーカー |

その他の主要キー: `dir`(状態ルート) / `workspace`(登録フォルダ) / `name` / `charter` / `charters` / `policy` / `backlog` / `archive` / `byStatus` / `claims` / `specs` / `rules` / `decisions` / `journal` / `runLog` / `delivery` / `projectState` / `reposFile` / `repos` / `autonomy` / `liveness` / `busDir` / `hasBus` / `busSource` / `busCandidates`。

---

## 3. 表示側（可視化の受け皿になる既存 UI）

### 3.1 概要タブ／プロジェクト設定

`src/renderer/sections/overview.js`

- `renderOverview()` (`overview.js:279`) — hero（現在の状態）＋ `overview-grid` の3カード（あなたの対応 / 進捗 / 成果）。設定値を出す場所ではない。
- `renderProjectSettings()` (`overview.js:368`) — 「プロジェクト設定」タブ。
  - 「基本設定」に `data-edit="charter.md|policy.md|rules.md|repos.json"` の4ボタン → `openProjectFile(name)`。
  - 「診断」セクション（`overview.js:405-410`）に `#btn-project-technical-info` → `openTechnicalInfo()`。**一貫性ゲートの結線状態を出すならここが既存の枠に最も素直に収まる。**

### 3.2 詳細情報ダイアログ（技術情報）

`renderer/renderer.js:1086` `technicalProjectInfoHtml()` / `renderer.js:1131` `openTechnicalInfo()`

`<dl class="developer-facts">`（`renderer.js:1116-1122`）が既に `dt/dd` の事実列になっている:

```
ワークスペース / 状態ディレクトリ / 実行データ / 検出方法 / 実行エンジン
```

`regression_cmd` / `intake_cmd`（有無・値・未結線バッジ）を足すなら**この `<dl>` に行を2つ増やすのが最小差分**。呼び出し元は overview.js:420・history.js:41・node-detail.js:503 の3箇所だが、HTML 生成は `technicalProjectInfoHtml()` の1箇所に集約済み。

### 3.3 needs の失敗要約

| 層 | 位置 | 内容 |
|---|---|---|
| 生成 | `project.js:587` `parseNeeds(text, id)` | frontmatter から `kind/date/status/task-id/risk/mr-url/delivery` を拾う |
| 生成 | `project.js:681-690` | 失敗の構造化。**frontmatter `failure-summary` があれば `_failureFromFrontmatter()`（`project.js:446`）を使い、無い旧票だけ `_diagnoseFailure()`（`project.js:467`）にフォールバック**。付与されるキー: `failureSummary` / `failureResolution` / `failureContext{category,owner,command,workdir,exitCode,target,resolvedTarget}` / `failureClass` / `failureChain` / `failurePhase` / `verifyVerdict` |
| 表示 | `needs.js:1212` `needFailureViewModel(need)` | **解析済みの事実だけを運ぶ。`failureSummary` が空なら null を返して何も断定しない**（コメント `needs.js:1203-1210` に「解析は producer に一本化」の設計意図が明記されている） |
| 表示 | `needs.js:1230` `canDiagnoseNeed(need)` | AI 診断ボタンを出すかの推測判定。散文に `(検証|verify|テスト|test|回帰|コマンド).*(失敗|FAIL|NG|exit=[1-9])` があれば true |

**重要な設計規約**: 失敗の解釈は producer（`main/project.js`）に一本化されており、renderer は運ぶだけ。回帰失敗の要約を足すなら `_failureFromFrontmatter` / `_diagnoseFailure` 側（producer）に入れ、`needs.js` で正規表現解析を増やさないこと。

### 3.4 codd-gate 固有の扱いは**現状ゼロ**

```
grep -rn "codd" tools/agent-dashboard/src tools/agent-dashboard/README.md → 0 hits
grep -rn "regression_cmd|intake_cmd|regressionCmd|intakeCmd" tools/agent-dashboard/ → 0 hits
```

dashboard 側は一貫性ゲートを一切認識していない。**全面的に新規追加**になる。

---

## 4. 有効化導線の正典（README）

`tools/agent-project/README.md:262-300`「一貫性ゲート（codd-gate 連携・オプション）」が正典。要点:

- 有効化は **`.agents/agent-project.yaml` へ2行**書く（`regression_cmd` ＋ `intake_cmd`）。
- `regression_cmd` だけは注入 CLI がある:
  `python3 codd_gate_regression.py --config .agents/agent-project.yaml`（この1キーだけを冪等 upsert。`--dry-run` あり）
  終了コード: `0`=注入済み(no-op も 0) / `1`=設定ファイルが無い・読めない / `2`=引数誤り / `3`=codd-gate が使えず何も書いていない。
- **`intake_cmd` に対応する注入 CLI は無い。yaml 直接編集のみ。**
- 結線判定は `python3 codd_gate_wiring.py --config .agents/agent-project.yaml` が JSON で返す:
  `regression_wired` / `intake_wired` / `recommended_regression_cmd` / `recommended_intake_cmd`
  （`tools/agent-project/codd_gate_wiring.py:75,80,85,94,105-116,143-155`。doctor 所見の文面は同ファイル `:205-216`）
- doctor 経路で所見を出すには `.agents/agent-project.yaml` に `hooks:` ＋ `  wiring: codd_gate_wiring` の2行が別途必要。

sibling CLI 群（すべて `tools/agent-project/` 配下・**本タスクの書込対象外**）:
`codd_gate_base.py` / `codd_gate_debt.py` / `codd_gate_detect.py` / `codd_gate_regression.py` / `codd_gate_routing.py` / `codd_gate_status.py` / `codd_gate_wiring.py`

---

## 5. 書込先スコープの確認と、そこから出た制約

### 5.1 スコープ確認

許可: `tools/agent-dashboard/**` のみ。
上で特定した挿し込み点はすべてこの配下に収まる:

- `src/features/agent-project/main/project.js`（読み取り＋ペイロード生成）
- `src/renderer/renderer.js` / `src/renderer/sections/overview.js`（表示）
- `test/*.test.js` ＋ `package.json` の `test` スクリプト（テスト追加時）

`tools/agent-project/**`（本体・sibling CLI）と `tools/codd-gate/**` は**読むだけで一切変更しない**。この前提は成立する。

### 5.2 制約 A — 既存の「設定編集」導線では agent-project.yaml を開けない

`authoring.js:46` `editablePath(dir, name)` に二重の制約がある:

```js
if (!isEditable(name)) throw new Error(`編集できないファイルです: ${name}`);
if (path.basename(name) !== name) throw new Error(`不正なファイル名です: ${name}`);
return path.join(dir, name);
```

1. **allowlist**: `EDITABLE_FILES`（`authoring.js:20-27`）は `charter.md` / `policy.md` / `rules.md` / `repos.json` / `repos.yaml` / `repos.yml` の6件のみ。`agent-project.yaml` は無い。
2. **root 相対 ＋ サブパス禁止**: `path.join(dir, name)` の `dir` は**プロジェクトルート（状態の置き場）**。`path.basename(name) !== name` で `.agents/agent-project.yaml` のようなサブパスは弾かれる。

一方 `agent-project.yaml` の実在場所は **`<workspace>/.agents/`**（= 状態ルートではない別ディレクトリ）。
→ 「設定編集」導線を作るなら `authoring.js` に **workspace 基準の編集対象**という新しい概念を入れるか、`readToolConfig().file` / `findProjectConfig()` が返す実パスを使って**外部エディタで開く**（既存の `data-ext` / shell-open 経路）かの二択。**後者のほうが allowlist の不変条件を壊さず小さい。**

### 5.3 制約 B — 状態ルート側の設定を読んでいる可能性

`readToolConfig('agent-project', [workspace, ...agentDirCandidates(workspace)])` は `workspace` 基準。
実測環境では workspace 直下ではなく `<workspace>/.agent/agent-project.yaml` に設定があり、`agentDirCandidates` の第2候補（`.agent`）で拾えている。
ただし `readToolConfig` は末尾に `agentHomeDir()`（`~/.agents`）を足すため、**プロジェクトに設定が無いとグローバル設定を拾う**。`resolveProjectRoot()` はこれを嫌って `cfg.file` が workspace 配下かを検査している（`project.js:1249-1251` の `fromWorkspace`）。
→ 「このプロジェクトで結線されているか」を判定するときは、**同じ `fromWorkspace` 検査を必ず通すこと**。通さないと、グローバル設定の `regression_cmd` を見て「結線済み」と誤表示する。

---

## 6. 検証内容と結果

コード変更なし（調査タスク）のため、実施したのは**主張の実地確認**のみ。

| 確認したこと | 方法 | 結果 |
|---|---|---|
| dashboard に codd-gate / regression_cmd の既存参照があるか | `grep -rn "codd\|regression_cmd\|intake_cmd\|regressionCmd\|intakeCmd" tools/agent-dashboard/` | **0 hits**（全面新規と確定） |
| `parseFlatYaml` が実物の `regression_cmd` 行を拾えるか | 実設定ファイル（`.agent/agent-project.yaml`）を読み、`toolconfig.js:33-43` の正規表現・`stripQuotes` と行単位で突合 | 一致。値の内側ダブルクォートは無害。行内コメント除去のみ潜在的な穴（§7-A） |
| `readToolConfig` の探索ディレクトリ | `src/base/main/agent-home.js:15-16,40-42` を読み `AGENT_HOME/.agents`・`AGENT_HOME_LEGACY/.agent` を確認。実ファイルが `.agent/` 側にあることを `ls` で確認 | 一致（第2候補で解決される） |
| ペイロードが renderer へ届く経路 | `ipc.js:69` → `preload.js:9` → `renderer.js:820` を各行で確認 | スキーマ非依存の素通し。中間層の変更不要 |
| 既存の編集導線で agent-project.yaml が開けるか | `authoring.js:20-27`（allowlist）と `authoring.js:46-55`（`editablePath`）を読解 | **開けない**（§5.2）。新規実装が必要 |
| 書込先が `tools/agent-dashboard/**` に収まるか | 特定した全挿し込み点のパスを列挙して照合 | 収まる。`tools/agent-project/**`・`tools/codd-gate/**` は読み取りのみ |

**実行していない検証**: `npm test` は未実行（コード変更が無く、`package.json` の `test` は 55 本の逐次実行で数分規模。調査タスクの成果に対して回帰リスクが無い）。
代替の確認方法: 後続の実装タスクで `cd tools/agent-dashboard && npm test` を必ず1回通すこと。UI 文字列の断定に依存するテストが多い（`overview-ui.test.js` / `user-centered-ui.test.js` / `needs-layout-ui.test.js` / `needs-diagnosis.test.js`）ため、表示追加は既存アサーションを壊しやすい。

---

## 7. 採用した前提・未解決事項・範囲外の所見

### 採用した前提

1. **「プロジェクト情報ペイロード」= `readProject()` の返り値**と解釈した。task 文の「公式契約 needs/inbox/commands」がすべてこの関数（および同じ `<root>` を基準にする `actions.dropCommand`）に集約されているため。`discover()`（`project.js:1492-1536`、サイドバー一覧用の軽量サマリ）は対象外とした——一覧に設定値を出す要求ではないと読んだ。
2. **結線状態の判定は yaml の2キーを直接読む方針を既定とした**。sibling CLI `codd_gate_wiring.py` を shell out すれば `recommended_*_cmd` まで得られるが、(a) dashboard から `tools/agent-project/` の相対パス依存が生まれる、(b) python3 実行が UI スレッドのレイテンシに乗る、(c) 「有無の可視化」には2キーの存在判定で足りる、の3点から**既存 `readToolConfig` の再利用が最小**と判断した。推奨コマンド文字列まで画面に出す要求なら CLI 経路に切り替える必要があり、そこは実装タスクの判断に委ねる。
3. **有効化導線は「外部エディタで設定ファイルを開く」を既定とした**（§5.2）。dashboard 内蔵エディタで編集させるには `authoring.js` の allowlist と root 相対の不変条件を両方緩める必要があり、`agent-project` 本体の設定を UI から書き換えることになる。これはスコープ外の「done 不変条件を破る UI からの状態書換」に接近するため、既定では選ばない。

### 未解決事項（実装タスクへの申し送り）

- `regression_cmd` / `intake_cmd` を出す先を「詳細情報ダイアログの `developer-facts`」（§3.2）にするか、「プロジェクト設定タブに専用カード」（§3.1）にするかは未決。前者が最小差分、後者が有効化導線を置くには自然。
- needs 側の回帰失敗要約は、agent-project 本体が `failure-*` frontmatter に何を書くかに依存する。**codd-gate 由来の失敗に固有の frontmatter キーがあるかは本調査では確認できていない**（needs ファイルの実サンプルが手元の worktree に無い）。`_failureFromFrontmatter`（`project.js:446`）が拾うのは `failure-category/owner/command/workdir/exit/target/summary/resolution` の8キー。実装前に実 needs ファイルを1件確認すること。

### 範囲外で見つけた問題（直していない）

- **A. `toolconfig.js:39` の行内コメント除去がクォート内も切る**
  `stripQuotes(m[2].replace(/\s+#.*$/, ''))` は値のクォートを見ずに ` #` 以降を落とす。`regression_cmd: 'cmd --opt "a #b"'` のような設定を書かれると値が静かに欠け、dashboard だけが誤った結線状態を表示する（本体の python 側は正しく読む）。現行の実設定には該当せず実害は無い。修正するなら `stripQuotes` を先に適用してからコメント除去する順序入れ替えだが、`bus:` / `root:` など既存キーの挙動に波及するため別タスク扱いが妥当。
- **B. `resolveBusDir()` / `readProject()` / `resolveProjectRoot()` が同じ設定ファイルを3回読む**
  `readToolConfig` はキャッシュを持たず、`readProject()` 1回の呼び出しで同一 yaml を最低3回 `readFileSync` する。プロジェクト一覧の定期リフレッシュで N プロジェクト×3 回。今回キーを足しても呼び出し回数は増えない（`project.js:1612` の `projectCfg` を再利用できる）ので**新たな悪化はしない**が、既存の非効率として記録しておく。
- **C. `readProject()` が 130 行超の単一関数になっている**
  返り値リテラルだけで 36 行（`project.js:1692-1727`）。キーを足す作業自体は安全だが、可読性の劣化は続く。分割は今回の目的と無関係なので手を付けない。
