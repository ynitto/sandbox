# agent-flow 複数リポジトリ（workset）設計 — 「1 run = 1 workspace」の見直しと影響範囲

> 作成 2026-09-05 ／ 対象: `tools/agent-flow`（主）・`tools/agent-project`・`tools/agent-dashboard`・
> `tools/agent-tools/agentcore`・`schemas/`・`.github/skills/flow-*`
>
> 位置づけ: **検討書**。現行の不変条件「1 run = 1 workspace（唯一の書込先）」を
> 「1 run = 1 **workset**（書込先の集合。既定は 1 要素）」へ改める案と、その影響範囲・段階導入を
> まとめる。実装は含まない。関連正典: [agent-flow 設計書](../designs/agent-flow-design.md) §5、
> [agent-flow 仕様書](../specs/agent-flow-spec.md) §3.1 / §3.8、
> [`tools/agent-project/ROUTING.md`](../../tools/agent-project/ROUTING.md)。

## 1. 結論

- 現行の「1 run = 1 workspace」は、**書込先の判断を agent-project に集約し、agent-flow は渡された 1 つを
  厳格に掌握する**という役割分担の帰結である。この役割分担そのものは変えない。
- 変えるのは**書込先の「数」だけ**。run が受け取る書込先を 1 つの spec から **順序付き集合（workset）**
  にし、集合の各要素に対して既存の規律（同名の作業ブランチ・commit/push の掌握・publication 記録・
  復旧 ref・base-sync・force-complete・CI 取り込み）を**要素ごとに同じ形で適用**する。
- **ノードは既定で repo を知らない（repo-blind）ままにする。** planner / user plan / 動的 fan-out の
  ノード契約は変えず、worker が workset 全体を用意し、エージェントが編集した repo だけを
  agent-flow が finalize する。ノード単位の絞り込み（`workspaces: [name]`）は任意の後付けにする。
- 集合が 1 要素のときは**現行と 1 バイトも挙動を変えない**（`workspace` / `delivery` / `publication` の
  形も既存のまま）。複数要素のときだけ、追加キー `workspaces[]` / `deliveries[]` が現れる。
- 影響は agent-flow 本体（中）、agent-project（大: ルーティング・MR・納品）、dashboard（中: 投函 UI と
  公開表示）、契約（小〜中: 加算的な追記＋検証計画の版上げ）。gitlab executor と 板（board）は
  初期段階では**複数 workset を fail-close で断り**、後段で対応する。

## 2. 問題

現行で複数リポジトリにまたがる仕事は、次の 2 経路だけで満たしている。

1. agent-project が **repo 別タスクへ分割**し `after` で順序付ける（ROUTING.md 原則 1）。
2. dashboard の **一括投函**で行ごとに別 run を投函する（2026-08-31 流用実行設計 §非目標）。

どちらも「1 つの変更が複数 repo で同時に成立して初めて検証できる」仕事には合わない。

- **同時成立が要る変更**: API を提供する repo と、それを呼ぶ repo を同時に変える。片方だけの run は
  検証（統合テスト・型チェック）が通らないか、通っても意味が無い。順序付けると、前段の run が
  マージされるまで後段は検証できず、前段は「壊れた中間状態」を target へ入れることになる。
- **横断検証の置き場が無い**: 検証計画 `plan.workspace` は 1 文字列で digest に含まれ、実行場所も
  1 つの clone（仕様書 §3.3）。2 repo を並べて動かす検証コマンドを書く場所が無い。
- **ルーティングが「決められない」で止まる**: `owns` が複数 repo にヒットするタスクは
  `_owns_infer` が `None`（読み取り専用 run）へ倒す（`request.py:481-489`）。「両方に書く」を表現できない。
- **既存文書も見直し条件を残している**: remote 公開復旧設計の再評価条件に
  「複数 remote への同時公開が必要になった場合」（2026-08-15 §Decision Record）と明記されている。

## 3. 現状の不変条件がどこに埋まっているか（棚卸し）

「唯一の書込先」は実装の偶然ではなく、複数箇所で明文化された契約である。変更時に触る場所を
先に固定するため、層ごとに列挙する。

### 3.1 agent-flow 本体（`tools/agent-flow/agent_flow/`）

| 箇所 | 単一前提の中身 |
|---|---|
| `cli.py:112-118` | `--workspace` は append でない単一引数。`--reference` だけが append |
| `bus.py:55-111, 173-177` | `meta.workspace` は dict か None。`Bus.run_workspace()` が唯一のアクセサ |
| `bus.py:801-804` | 世代交代 `_seed_from` が 1 つの workspace を複製し `base` を旧 `af/<old-id>` へ差す |
| `bus.py:1091-1121` | inbox `submit_request` も同じ単一形 |
| `workspace.py:12-13, 206-240` | clone キャッシュ `(url,path,base)`、temp root はプロセスに 1 つ |
| `workspace.py:90-97` | 作業ブランチ `af/<run-id>`、復旧 ref `refs/agent-flow/recovery/<run-id>` は **run-id だけ**が鍵 |
| `workspace.py:275-291` | base-sync ノード id は固定文字列 `base-sync`、1 run に 1 つ |
| `workspace.py:359-444` | `finalize_workspace` は 1 clone・1 branch・1 target_rev・1 delivery |
| `workspace.py:460-484` | `workspace_instruction` の文面「唯一の書込先リポジトリ」 |
| `work.py:285-340, 397-405` | worker は `run_workspace()` を 1 つ取り、`data.delivery` / `data.publication` を 1 つ書く |
| `agent.py:1444-1448` | エージェントの cwd は workspace clone、無ければ最初の local 参照 |
| `orchestrate.py:453-456, 548, 616` | run 作成と再計画で単一 workspace から base-sync を注入 |
| `verifyplan.py:231-272` | 検証計画は 1 clone（vcwd）・1 result_rev・1 receipt |
| `ci.py:98-124, 149-190` | publication ごとに `AGENT_CI_*` を渡す構造は既に反復だが、run 集約の `url` は先頭勝ち |
| `recovery.py:9-23, 49-72` | force-complete は publication ごとに remote 検証（構造は N 対応可） |
| `cleanup.py:245-249` | gc が `meta.workspace` を dict として読み復旧 ref を 1 つ消す |
| `executors/gitlab.py:295-312, 797-803, 936-940` | 起票先プロジェクトと MR 期待 target を **1 つの workspace URL** から解決。park 記録も `expected_target` 1 つ |
| `patterns.py:84-102, 841-849` / `orchestrate.py:207-225` | ノード契約に repo/workspace キーは無い（planner も出さない） |
| `continuation.py` | 動的追加ノードは `readonly` だけ継承し repo を持たない |

### 3.2 外部の生産者・消費者

| 層 | 箇所 | 単一前提の中身 |
|---|---|---|
| agent-project ルーティング | `request.py:518-551` `resolve_workspace` | 5 段の解決がちょうど 1 つを返す。`owns` 複数ヒットは None |
| 〃 | `request.py:492-515` auto-route | プロンプトが「1 つだけ選べ」、応答 `{"workspace": "<name>"}` |
| 〃 | `plan.py:261-299` `assign_plan_workspace` | 生成タスクに workspace を 1 つ強制し他を `refs` へ降格 |
| 〃 | `request.py:570-609` | `--workspace` を 1 つだけ組む。`task_reference_specs` は 1 つの ws_url を除外 |
| agent-project 納品 | `config.py:397-414, 450-511` | `_task_work_branch` は 1 組の (target, branch)。`delivery_entries` は list だが write は 1 件 |
| 〃 | `mr.py:254-300, 444-529` | MR/PR は 1 本、`mr_url` / `mr_iid` はスカラ。finalize は 1 target へ merge |
| 〃 | `verify.py:127-170, 584-608` | 検証 cwd は 1 clone。plan の `workspace` は URL 文字列 1 つ、`integration.target` 1 つ |
| agent-project 板 | `board.py:462-498` | 委譲封筒の `workspace` は 1 object。URL が入札資格の鍵 |
| agentcore | `board.py:351-357` | 入札資格は `workspace.url` 照合（`requires.repos` の AND 列は存在するが未使用） |
| 〃 | `verifycontract.py:122-132, 384` | plan/receipt の `workspace` は文字列で **digest 対象** |
| 〃 | `repolocal.py:195-206` | `merge_local(spec)` は 1 spec |
| agent-dashboard 投函 | `adhoc.js:966-982, 1230-1261` | フォルダ選択は 1 つ。references は design run でのみ同じフォルダ |
| 〃 | `reuse.js:414-445` | 一括投函は「行ごとに別 run」で単一前提を保つ |
| agent-dashboard 表示 | `renderer/features/adhoc-flow.js:392-460` | publication の全フォールバックが `run.workspace` を参照 |
| 〃 | `sections/flow.js:497,512,688`、`node-detail.js:307` | GitLab 照合の repoUrl は `run.workspace.url` |
| 〃 | `sections/needs.js:727-740` | `workspaceDelivery` は 1 要素の配列（描画側 `798-852` は N 対応） |
| 〃 | `sections/backlog.js:2653-2680` | 投入フォームの workspace は `<select>` 1 つ |
| schemas | `task.schema.json:123` `workspace` string / `delegation.schema.json:39` object / `board.schema.json:110-130` `result.branch` string / `verification-plan.schema.json:11` string | いずれも単数 |
| codd-gate | `codd-gate.py:794-801` | 横断 followup は `workspace: <1 repo>` を出す生産者。走査自体は N repo |
| flow-planner / flow-worker | `plan.py:232`、`prompt.py:20-21,231-232` | ノードに repo 無し。worker は `repo_instruction` を素通し |

### 3.3 現行設計が単一に固定した理由（尊重すべき点）

1. **判断の一元化**: どの repo に書くかは agent-project（charter `owns` / `route:` / auto-route）が決め、
   agent-flow は決めない。planner の LLM 出力に repo 割当を持たせないことで、分解と書込先の責任が
   混ざらない。
2. **公開の完了条件**: 「remote への push 成功まで」が run の完了条件。1 repo なら成功/失敗が二値。
3. **gitlab executor**: 起票先プロジェクト・MR の期待 target を workspace から決定的に解決。
4. **検証の再現性**: plan digest に workspace を含めることで「別の場所で検証した pass」を弾く。
5. **世代交代**: `af/<run-id>` を base に差し替えて done の commit を保つ操作が 1 ブランチ前提で単純。

本設計はこれらを「要素ごとに同じ規律を適用する」形で保つ。判断の一元化は「集合の中身を決めるのは
agent-project」と読み替え、公開の完了条件は「全要素の publication が published」に拡張する。

## 4. 検討した案

| 案 | 内容 | 利点 | 難点 | 推奨 |
|---|---|---|---|---|
| A. 現状維持（分割＋一括投函） | 変更なし | 実装ゼロ | 同時成立が要る変更・横断検証を表現できない（§2） | ☆ |
| B. ノード単位 repo 割当 | 各ノードに `workspace: <name>` を持たせ、planner が割り当てる | clone を最小化。ノードの担当が明確 | planner 契約が変わる（flow-planner / user plan / 動的 fan-out の全経路）。LLM に repo 判断を持たせることになり §3.3(1) に反する。小型モデルの誤割当が増える | ★☆ |
| C. workset（run 単位の書込先集合、ノードは repo-blind） | run が N 個の書込先を受け取り、worker が全部を用意。編集された repo だけ finalize | ノード契約不変。判断は agent-project のまま。N=1 で現行と同一 | 各ノードが全 repo を用意する（共有ミラーで増分のみ）。エージェントが誤った repo を触る余地 | ★★★ |
| D. 論理モノレポ化（N repo を 1 つの作業ツリーへ合成、submodule / subtree） | agent-flow 側は 1 workspace のまま | 実装は薄い | 公開先が合成 repo になり、元 repo への push・MR・復旧 ref が別途必要。git 履歴操作を持ち込む | ☆ |

**案 C を採り、B の「ノード単位の絞り込み」を任意の追加（§5.6）として後段に置く。**

## 5. 採用案: workset

### 5.1 用語と不変条件

- **workset**: run が書き込んでよいリポジトリの順序付き集合。要素は repos.schema.json の 1 エントリの
  射影 `{name, url, local, path, base, target, branch, desc}`。先頭要素を **primary** と呼ぶ。
- 要素の同一性は従来どおり `(url, path, base)`。**同じ url を持つ要素は base が等しくなければならない**
  （同 url・別 base に同名ブランチ `af/<run-id>` を作れば起点が矛盾する。明示 `branch` で分ける場合だけ許す）。
  同 url・同 base・別 path は **1 clone を共有し、path の和集合**を変更許可範囲にする。
- references と workset の url が重なる場合は workset が勝ち、reference から落とす（読み取り専用の
  注記が書込先と矛盾しないため）。
- 新しい不変条件:
  1. **書込先の集合を決めるのは依頼側（agent-project / dashboard / 板）**。agent-flow の planner は
     集合を増減しない。
  2. **workset の各要素に対して同じ規律を適用する**（作業ブランチ・commit/push・publication・復旧 ref・
     base-sync・force-complete・CI）。要素ごとの記録を持ち、集約はそこから導出する。
  3. **N=1 のとき既存契約と一致する**。`workspace` / `delivery` / `publication` の形と意味は変えない。
  4. **run の完了条件は「全要素の publication が published（変更ゼロの要素は not-required）」**。
     1 要素でも失敗すれば run は failed で、成功した要素の publication はそのまま残す（半公開状態を
     隠さない。§5.5）。

### 5.2 契約（inbox / meta / result）

加算的な追記で表現し、既存キーの意味は変えない。

```jsonc
// inbox/<run-id>.json（投入側が書く）
{
  "workspace":  {"name": "api", "url": "...", "base": "main", "target": "develop", "path": ""},
  "workspaces": [ {"name": "api", ...}, {"name": "web", "url": "...", "base": "main", "path": "apps/web"} ],
  "references": [ ... ]
}
```

- `workspaces` があれば正典。無ければ `[workspace]`（1 要素）として扱う。`workspaces[0]` と
  `workspace` が食い違えば投入時に断る（rc=2、黙って直さない）。
- `name` は repos レジストリのエントリ名。省略時は url から導出（`_repo_name`）。ノード内・記録内の
  repo 参照は常にこの name を使う（codd-gate の `repo名:相対パス` と同じ語彙）。
- `meta.json` も同じ 2 キーを持つ。`Bus.run_workspace()` は **primary を返す**（旧読み手互換）。新設
  `Bus.run_workset()` が全要素を返す。
- 世代交代（`_seed_from`）は要素ごとに `base` を旧 `af/<old-id>` へ差し替える。
- 再投入時の「無い → 有る」補充は workset 全体で行い、既存要素の差し替えはしない（現行規則の踏襲）。

```jsonc
// results/<id>.json の data（worker が書く）
{
  "deliveries": [
    {"name": "api", "url": "...", "branch": "af/<run-id>", "commit": "...", "target": "develop",
     "path": "", "publication": {"state": "published", ...}},
    {"name": "web", "url": "...", "branch": "af/<run-id>", "publication": {"state": "not-required", ...}}
  ],
  "delivery":    { ...deliveries のうち primary の記録（primary に変更があったときだけ）... },
  "publication": {"state": "published", "repositories": ["api"], ...}   // 集約。最悪状態を採る
}
```

- 集約 `publication.state` は `failed > published-manually > published > not-required` の順で最悪を採る。
- `final.json` は node ごとの `deliveries` を束ね、run 全体の `publications[]`（要素ごと）と
  `ci`（要素ごとの配列＋最悪状態）を持つ。

### 5.3 worker の実行モデル

```
ensure_workset(workset, run_id)
  各要素 e: provision_tree(e.url, [branch, e.base], <ws-root>/<e.name>, local=e.local)
           → branch = e.branch or af/<run-id>（要素ごとに同名。横断 MR の相関鍵にもなる）
  返り値: [ {...e, clone, branch}, ... ]
```

- エージェントの cwd は **primary の clone**（現行どおり）。他要素は絶対パスで指示ブロックに列挙する。
  cwd を親ディレクトリにしない理由は、cwd を「そのプロジェクトのルート」と解釈する CLI（aider 等）が
  あるため。1 要素なら現行と同じ。
- 指示ブロック（`workspace_instruction`）は要素ごとに「name / clone / 変更してよい path / ブランチ /
  役割」を並べ、「以下以外の場所は変更しない」を明示する。「唯一の書込先」の文言は N=1 のときだけ出す。
- `read_allocation[].path` と `operation.scope` のパスは `name:相対パス` 接頭辞を許す。無接頭辞は
  primary 相対（現行と同じ）。
- **finalize は要素ごと**: `finalize_workspace(e, run_id, node_id)` を順に呼び、変更のある要素だけ
  commit/push する。1 要素の push 失敗は `WorkspacePublishError` を要素名付きで上げ、**残りの要素も
  finalize を試みてから**ノードを failed にする（部分公開を記録に残すため。§5.5）。
- 復旧 ref は要素ごとに `e.local` へ `refs/agent-flow/recovery/<run-id>`（同名でよい。repo が違う）。
- 決定的な範囲検査（任意・推奨）: staged パスが要素の `path` 外なら finalize で失敗させる。現行は指示
  だけで機構は無い。複数 repo を開くと誤編集の余地が増えるので、ここで機械的に止める価値が上がる。
- park（承認待ち）・cleanup・claim 喪失時の後始末は `cleanup_workspace()` が workset 全体を消す
  （現行の all-or-nothing を維持）。

### 5.4 base-sync と検証

- base-sync は **要素ごとに 1 ノード** `base-sync:<name>`（`branch != target` の要素だけ）。root は
  全 base-sync に依存する。`base-sync` という固定 id を使う文字列比較（`work.py:301,332`、
  `workspace.py:374`、`verifyplan.py:281-287` の `base-sync-<n>`）は kind 判定へ寄せる。
- **検証計画 version 3**: `workspaces: [name...]`、`commands[].cwd: <name>`（省略は primary）、
  `integration.targets: {name: target}`。digest は version 3 の canonical JSON で取り直す
  （条件が変わったので別 plan になるのは正しい）。version 2 の plan は 1 要素 workset でのみ受理し、
  N>1 の run に version 2 が来たら inconclusive（「検証場所が不足」）に倒す。
- runner は全要素を用意し、環境変数 `AGENT_WORKSET_ROOT` と `AGENT_REPO_<NAME>=<clone>` を渡す。
  横断の統合テストはこれで 2 repo を参照できる。`result_rev` は要素ごと（receipt に `revisions{name: rev}`）。
- 終端 `verify` ノードの意味は変わらない（graph の完了条件）。

### 5.5 公開失敗と半公開状態

複数 remote への push は原子的にできない。方針は「隠さず、要素ごとに復旧できるようにする」。

- 要素 A が published、要素 B が failed のノード: ノードは failed、A の publication は published のまま
  残す。run は failed。再開（resume）は failed ノードを pending へ戻し、A は差分ゼロで not-required、
  B だけ再 push される（作業ツリーは同じ作業ブランチから作り直すため、A の commit は remote にある）。
- `force-complete` は publication ごとの検証（現行構造）で足りる。要素名を監査記録へ足す。
- 復旧 ref の gc（`cleanup.py`）は workset の全要素を回る。

### 5.6 任意の後付け: ノード単位の絞り込み

- user plan / planner 出力に `workspaces: [name...]` を**任意**で許す。指定があれば worker はその要素
  だけを用意し、指示ブロックもそれだけを出す。未指定は全要素（既定）。
- 動的追加ノード（split→map / replan）は親ノードの指定を継承する。
- flow-planner への追加は `operation.scope.write` に `name:` 接頭辞を認めるだけに留め、repo 選択の
  指示はプロンプトへ入れない（判断を LLM に持たせない方針の維持）。

### 5.7 gitlab executor と板

- **gitlab executor**: 起票先プロジェクトを 1 URL から決める構造のため、初期段階では
  `len(workset) > 1` を **fail-close で断る**（明確なエラー。黙って primary だけに起票しない）。後段で
  「要素ごとに 1 イシュー（同じ task_token に要素名を足す）＋ 要素ごとの `expected_target`」へ広げる。
  park 記録は `issues[]` / `expected_targets{}` を持つ形に版上げする。
- **板（agent-board）**: 委譲封筒に `workspaces[]` を足し、`requires.repos` に全要素の url を入れる
  （既存の AND 列を使う）。入札側 `agentcore.board.eligible` は `requires.repos` を照合する経路が
  既にあるので、**`workspaces` を知らない旧ノードは `requires.repos` の照合で自然に落ちる**わけでは
  ない（`workspace.url` だけ見て入札しうる）。したがって N>1 の公示は `contract_version: 2` を付け、
  version 1 のノードは fail-close で入札しない。

## 6. 影響範囲

規模: S=数十行、M=数百行、L=それ以上。互換: ○=加算的・旧読み手そのまま、△=版上げ・切替手順が要る。

### 6.1 agent-flow

| 箇所 | 変更 | 規模 | 互換 |
|---|---|---|---|
| `cli.py` `--workspace` | append 化。`verify-plan --workspace` も繰り返し可 | S | ○（1 回指定は同じ） |
| `bus.py` `ensure_run` / `submit_request` / `_seed_from` / `run_workset` | `workspaces` の保存・補充・世代交代。`run_workspace()` は primary | M | ○ |
| `workspace.py` | `ensure_workset`、要素ごと finalize、`base-sync:<name>`、指示ブロック、path 範囲検査、cleanup | M | ○ |
| `work.py` | workset の用意と要素ごと finalize、`deliveries[]` / 集約 `publication`、base-sync の kind 判定 | M | ○ |
| `agent.py` | cwd は primary、`name:` 接頭辞のパス解決、readonly の参照 cwd は変更なし | S | ○ |
| `orchestrate.py` / `continuation.py` | base-sync の複数注入、`workspaces` 絞り込みの継承（§5.6 は後段） | S〜M | ○ |
| `verifyplan.py` + `agentcore.verifycontract` | plan v3（cwd / targets / revisions）、要素ごと clone、環境変数 | M | △（digest が変わる。v2 は 1 要素のみ） |
| `ci.py` | run 集約を要素ごとの配列へ。URL 先頭勝ちの解消 | S | ○ |
| `recovery.py` / `cleanup.py` | 要素名付きの監査、gc の全要素走査 | S | ○ |
| `status.py` | `deliveries` の描画 | S | ○ |
| `executors/gitlab.py` / `waits.py` | 初期: N>1 を fail-close。後段: 要素ごと起票・`expected_targets` | S → L | △（park 記録の版上げ） |
| `board.py` | `workspaces` の取り込み、`merge_local` を要素ごとに | S | △（contract_version 2） |
| `patterns.py` | 任意キー `workspaces` の受理（§5.6） | S | ○ |
| `tests/` | `test_workspace` / `test_run` / `test_daemon` / `test_verifyplan` / `test_executor` / `test_board` へ N=2 のケース追加。既存ケースは変更なしで通ること | M | — |
| 文書 | 設計書 §5.1 の書き換え、仕様書 §3.1 / §3.3 / §3.7 / §3.8 の追記、README の「唯一の書込先」 | M | — |

### 6.2 agentcore / schemas

| 箇所 | 変更 | 規模 | 互換 |
|---|---|---|---|
| `schemas/repos.schema.json` | 変更なし（`name` はマッピングのキーとして既にある） | — | ○ |
| `schemas/task.schema.json` `workspace` | string に加えて string[] を許す（順序＝primary） | S | ○ |
| `schemas/delegation.schema.json` | `workspaces[]` 追記、`requires.repos` の使い方を明記、`contract_version` | S | △ |
| `schemas/board.schema.json` `result` | `deliveries[]`（name / branch / commit）を追記。`branch` は primary | S | ○ |
| `schemas/verification-plan.schema.json` / `-receipt` | version 3 の形 | S | △ |
| `schemas/delivery.schema.json`（amigos 用） | 変更なし。amigos は `workspace.repo` を素通しのまま | — | ○ |
| `agentcore/verifycontract.py` | v3 の build / validate / receipt 投影 | M | △ |
| `agentcore/repolocal.py` | `merge_local` を list にも適用する薄い関数 | S | ○ |
| `agentcore/board.py` | `contract_version` 2 の受理と `workspaces` 照合 | S | △ |

### 6.3 agent-project（最も大きい）

| 箇所 | 変更 | 規模 | 互換 |
|---|---|---|---|
| `request.py` `resolve_workspace` | 戻り値を list に。明示 `- workspace: a, b`、`route: ... -> a+b`、`owns` 複数ヒットは設定 `multi_workspace: true` のときだけ全ヒットを採る（既定は現行の None） | M | △（設定でオプトイン） |
| `request.py` auto-route | 変更なし（1 つだけ選ぶ。複数は人か決定論ルールで） | — | ○ |
| `plan.py` `assign_plan_workspace` | verify コマンドの操作パスが複数 repo の owns に跨るときだけ list を許す。他は従来どおり 1 つ | M | ○ |
| `request.py` `_workspace_cmd_args` / `task_reference_specs` | `--workspace` を N 回、references から全要素を除外 | S | ○ |
| `verify.py` | plan v3 の生成、検証 cwd を N clone に | M | △ |
| `config.py` `_task_work_branch` / `delivery_entries` | (target, branch) を要素ごとに。write エントリを N 件（描画側は既に N 対応） | M | ○ |
| `mr.py` | MR/PR を要素ごとに作成・merge。`mr_url` → `mr_urls[]`（`mr_url` は primary を残す）。settle の target 統合判定を要素ごとに | L | △ |
| `board.py` `task_to_delegation` | `workspaces[]` と `requires.repos`、contract_version 2 | S | △ |
| `needs.py` / `commands.py` | delivery の N write 件、`delivery_missing_branch_ack` の要素化 | S | ○ |
| `model.py` / `task.schema.json` | `workspace` の list 受理 | S | ○ |
| ROUTING.md / README / 仕様書 | 原則 1 の書き換え | S | — |

### 6.4 agent-dashboard

| 箇所 | 変更 | 規模 | 互換 |
|---|---|---|---|
| `adhoc.js` 投函 / 再投函 | 対象フォルダを複数選択、`workspaces[]` を書く、検証計画 v3 を組む | M | ○ |
| `reuse.js` 一括投函 | 変更なし（行＝run のまま。行に複数フォルダを持たせるのは任意） | — | ○ |
| `renderer/features/adhoc-flow.js` publication 表示 | `deliveries[]` を要素ごとに描画。フォールバックは `run.workspaces` | M | ○ |
| `sections/flow.js` / `node-detail.js` GitLab 照合 | primary を使い、複数なら要素ごとに切替 | S | ○ |
| `sections/needs.js` | `workspaceDelivery` を N 要素に | S | ○ |
| `sections/backlog.js` 投入フォーム | `<select>` を複数選択に | S | ○ |
| `delegation/*` アダプタ | `workspaces` の素通し | S | ○ |
| テスト | `adhoc-flow` / `delivery-review` / `delegation` / `reuse-rerun`（「1 run = 1 workspace は崩さない」の断定を「行＝run」に言い換え） | S | — |

### 6.5 変更しないもの

- flow-planner の出力契約（`name:` 接頭辞の受理を除く）、flow-worker のプロンプト組立、
  `git_worktree.py`（URL 単位で既に N 対応）。
- 共有ミラー・worktree の provisioning（`gitcache.py` / `agentcore.transport` は repo 単位で N 対応済み）。
- claim / lease / 静止判定 / 再計画 / cancel / 孤児回収（repo を知らない層はそのまま）。
- codd-gate（既に N repo。横断 followup が `workspace` を list で出せるようになるだけ）。
- agent-amigos の `mission.workspace.repo`（素通しのまま。checkout を実装する時点で repos エントリ形へ揃える既存方針に従う）。

## 7. 段階導入

| 段 | 内容 | 完了条件 | 状況 |
|---|---|---|---|
| **P0 契約** | 仕様書 §3.1 / §3.8 に `workspaces` / `deliveries` を追記。verification-plan v3 と delegation `contract_version` の schema。ROUTING.md 原則 1 の書き換え | schema の contract test が新旧両方を通す | 済 |
| **P1 agent-flow 本体** | §5.2〜5.5。gitlab executor は N>1 を fail-close。板は `workspaces` 付き公示を受け取らない（version 1 のまま） | N=1 の既存テスト全通過 ＋ N=2 の e2e（stub executor で 2 repo に push・片方 push 失敗で半公開が記録される・resume で失敗側だけ再 push） | 済 |
| **P2 agent-project** | ルーティングの list 化（オプトイン）、plan v3、MR 要素ごと、delivery N write、板封筒 | 既存プロジェクト（1 repo）の loop テストが無変更で通る。2 repo プロジェクトの act → verify → review → done が回る | 済（板は封筒だけ用意し、公示は P4 まで fail-close） |
| **P3 dashboard** | 投函の複数フォルダ、公開表示、GitLab 照合、検収 | 1 repo run の画面が変わらない。2 repo run で要素ごとの publication と差分が見える | 未 |
| **P4 gitlab executor / 板** | 要素ごと起票と `expected_targets`、板 contract_version 2 | 2 repo の委譲が起票・自動マージ・決着まで回る | 未 |

**配布順序**: agent-flow を全 PC で P1 へ上げてから、agent-project / dashboard が `workspaces` を
出し始める。旧 agent-flow は `workspaces` を未知キーとして無視し primary だけに書く（静かな部分実行）
ため、P2 の切替は `multi_workspace: true` のオプトインと、`agent-flow --version` の下限確認
（`doctor` に所見を足す）で守る。

**実装時の逸脱（3 点）**:

- base-sync のノード id は `base-sync:<name>` ではなく **`base-sync@<name>`**。ノード id は
  `tasks/<id>.json` と `claims/<id>/` のパスになり、`:` は Windows のファイル名として不正で、
  全 PC へ配る run 状態がそのノードでだけ壊れるため（`base-sync-<n>` とも衝突しない）。
- 板は P2 では**封筒だけ**用意した（`workspaces[]` / `requires.repos` /
  `requires.contract_version`）。入札選別の契約版は完全一致なので、`contract_version: 2` の
  公示はフリートを一斉に上げるまで誰も入札できず「無言の停止」になる。そこで依頼側が
  **N>1 の公示を出さない**（`workset_offload_blocked` でローカル実行へ倒す）。P4 で
  `agentcore.board.CONTRACT_VERSION` を 2 へ上げると門が開く。
- `resolve_workspace` の戻り値は list に変えず、集合版 `resolve_workset` を足して
  `resolve_workspace` は primary を返す形にした（agent-flow の `run_workspace()` /
  `run_workset()` と同じ分け方。旧い呼び出しと読み手を版で分岐させないため）。

## 8. 非目標

- ノードを PC 間で repo ごとに配る scheduler（run が分散単位であることは変えない）。
- 複数 remote への原子的な公開（できない。半公開を記録し要素ごとに復旧する）。
- auto-route（LLM）に複数 repo を選ばせること。複数は人の明示か決定論ルールだけ。
- N repo を 1 つの合成 repo に見せること（案 D）。
- agent-amigos の workspace 実装、GitLab 以外のフォージ対応。

## 9. リスクと代償

| リスク | 手当 |
|---|---|
| 各ノードが N repo を用意するコスト | 共有ミラー＋worktree で初回 1 回＋増分。§5.6 のノード単位絞り込みで clone を減らせる |
| エージェントが意図しない repo / path を編集する | 指示ブロックの列挙に加え、finalize の決定的な path 範囲検査で機械的に止める |
| 半公開状態の見落とし | ノード failed ＋ 要素ごとの publication を残す。dashboard は要素ごとに状態を出す |
| 検証計画の digest 変更で旧 receipt が使えない | v3 は別 plan として扱う（条件が変わった検証は別、という既存規則どおり）。1 要素は v2 のまま動く |
| 旧ノード（agent-flow / 板）が `workspaces` を無視して primary だけに書く | 配布順序、`multi_workspace` オプトイン、板 contract_version 2、doctor の版下限所見 |
| gitlab executor の起票先が 1 つに固定 | P1 では fail-close。P4 で要素ごと起票 |
| `af/<run-id>` を全 repo で同名にする衝突 | 同 url は同 base を要求（§5.1）。明示 `branch` で分ける経路は残す |

## 10. 再評価条件

- ノード単位の絞り込み（§5.6）を planner に**常時**要求したくなった場合（判断の一元化の再検討）。
- 横断 MR を 1 つの単位として承認・マージしたい要求が出た場合（フォージ側の機能に依存）。
- run 単位の単一 publish phase へ移す判断が出た場合（2026-08-15 設計の再評価条件と同じ）。
