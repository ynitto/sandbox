# S8 / S9-4 詳細設計: 板の観測・操作 UI と診断の対話化

ステータス: 詳細設計（未実装）
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S8（C10）/ S9-4（C11）
前提:
- [`2026-07-26-s9-agent-cli-layer-detailed-design.md`](2026-07-26-s9-agent-cli-layer-detailed-design.md)（S9-4 はこのレイヤの最初の利用者）
- [`2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md`](2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md)（host.yaml `repos[]` とノード局所の解決）
- [`2026-07-23-delegation-board-distributed-bidding-design.md`](2026-07-23-delegation-board-distributed-bidding-design.md)（板の契約・claim 規則）
- [`2026-07-24-single-resident-controller-implementation-plan.md`](2026-07-24-single-resident-controller-implementation-plan.md) §7 R2（板の請負 tick）

実装フェーズ: Phase 4。

**この設計の受入条件**（仕様書 S8・S9-4 より）:

| # | 受入条件 |
|---|---|
| A1 | 板で何が起きているかが dashboard から分かる（誰が引き受け、いま何をしていて、成果はどれか） |
| A2 | 人の判断（止める・引き受ける）が板へ通る。**二重落札防止の判断（claim 規則）を UI 側に複製しない** |
| A3 | 診断が 1 往復で終わらず、追加質問できる |
| A4 | 対話診断が増えても、CLI ごとの差分は `agents/<name>.json` の中だけに残る（S9 の受入条件を壊さない） |

以下の設計判断はすべてこの 4 点から導いている。

---

## 1. 現状 — 仕様策定（2026-07-25）から変わったこと

仕様書 §3 の S8 は 4 項目とも「現状の実装」を前提に書いてあるが、**そのうち 3 つの前提が既に成り立っていない**。
先に事実を並べる。

### 1.1 置き場が消えた — orchestration タブは「全体設定」になった

仕様書 S8-1 は「orchestration（全体）タブ内に board セクションを追加し」と書いている。
だが [`2026-07-19-agent-dashboard-global-settings-page-design.md`](2026-07-19-agent-dashboard-global-settings-page-design.md) 以降、
このタブは**全体設定**（`renderOrchestration`（`src/renderer/sections/orchestration.js:864`）が
app / agents / sync / routine / integrations の 5 節を描く設定画面）になっている。
タブボタンのラベルも「全体設定」（`src/renderer/index.html:94`）。

**設定画面に、刻々と動く公示の一覧を置くことはできない**（「この端末の構成を決める場所」と
「いま外で何が起きているかを見る場所」を混ぜると、どちらの目的でも探しにくくなる）。
§4 で置き場を決め直す。

### 1.2 器が既にある — 参加タブ

仕様書には出てこないが、[`2026-07-20-agent-dashboard-participation-ui-design.md`](2026-07-20-agent-dashboard-participation-ui-design.md)
で**参加タブ**が入っている。「この端末で、いま募集中の仕事を引き受ける」ための画面で、
候補（flow の未着手ノード・amigos の空きロール）を並べて「参加する」ボタンを出す
（`src/renderer/features/participation.js` / 候補生成は `src/features/participation/model.js`）。

S8-3 の「このノードで請け負う」は、**この画面の 3 つ目の候補源**として素直に載る。
新しい操作面を作る必要は無い。加えてこの feature は既に次の規律を持っており、
S8-3 が守るべきものとほぼ同じ:

- 人の明示操作でだけ起動する（ポーリング・自動判断からは起動しない）
- `agent-control` の lifecycle が pause / stop なら理由を添えて断る
- 起動前に到達性を確かめる（黙って失敗しない）

### 1.3 W1-11 は完了。待ち先は R2（板の請負 tick）

仕様書 S8-4 は「本仕様の 2・3 は常駐体の board tick（W1-11）実装後に着手する」と書くが、
W1-11（CLI とセットアップ）は完了済みで、待ち先は
[実装計画 §7 R2](2026-07-24-single-resident-controller-implementation-plan.md)（板の請負 tick）に切り出されている。
以後 **「W1-11 後」は「R2 後」と読み替える**。

### 1.4 板の請負は「もう動いている」— ただしプロジェクトのバス経由で

R2 が未着手なので「板の請負は無い」と読めるが、実際には **workload=flow の請負は今日も成立している**。
経路はこう:

```
常駐体 flow tick（5s・resident_cli.py:407 _flow_participate_tick）
  → agent-project flow-participate --project <name>
      → agent-flow participate            … この中で poll_board（agent_flow/board.py:210）
          ・板を巡回し workload=flow の公示に repos/tags 照合で入札（claim 流用）
          ・落札したら **そのプロジェクトのバスの inbox へ** submit_request
          ・板に status/<who>.json（state=dispatched）を書く
  → 受理された run-id を NodeWorkerPool へ（agent-project flow-run）
```

R2 が言う「未実装」は正確には**ノード直轄の請負**（プロジェクトを 1 つも持たないノードが落札して
実行する経路）であって、フルノードでの請負は既に回っている。この区別が §2 のスコープ判断の土台になる。

### 1.5 板参加の宣言が 2 か所に割れている（S1 の 2 層化から漏れている）

| 宣言 | 置き場 | 効くもの |
|---|---|---|
| `board` / `board_workdir` / `tags` / `agent_cli` / `repos` | `agent-project.host.yaml` | agent-project の依頼側 offload・amigos 参加 tick の板巡回・gc の板掃除 |
| `board` / `board_workdir` / `board_branch` / `board_repos` / `board_tags` / `board_lease` | **agent-flow の設定ファイル**（`tools/agent-flow/agent_flow/config.py:63-71`） | flow の請負（`poll_board` の入札選別） |

`board_repos` / `board_tags` は「このノードが担当するリポジトリ」「このノードの能力」——
**S1 が host.yaml 専有と決めたノード固有宣言そのもの**が、agent-flow 側の設定ファイルに残っている。
`_kf_base`（`tools/agent-project/agent_project/request.py:294`）は `--board` を渡さないので、
両者は互いを知らない。

### 1.6 `nodes/<node-id>.json` の書き手はまだ居ない

契約（`schemas/board.schema.json` `$defs.node`）も dataclass（`resident/status.py` の `NodeCapability`）も
揃っているが、**呼ぶ人が居ない**（grep してもテストからしか参照されない）。
仕様書の積み残し P1-a（`repos[].local` の板への転記）が止まっているのはこれが理由。

### 1.7 dashboard の板への書き込みは、`git+` 板では誰にも届かない

`board-adapter.js` の `submitPost` / `award` / `cancel` は板リポジトリの作業ディレクトリへ
**ファイルを書くだけ**で、commit も push もしない（ファイル冒頭の宣言どおり
「git 同期は board デーモン側が担う」）。ところが:

- ローカル dir の板 … そのまま真実なので成立する
- `git+<url>` の板 … 書いたファイルを push する主体が居ない。agent-project の `BoardRepo.sync_push`
  は `add -A` するので**偶然拾われることはある**が、それは「同じ PC で同じディレクトリを
  たまたま板の作業クローンに使っていたら」という条件付きで、契約ではない

つまり **`git+` 板ではキャンセルボタンが黙って効かない**。仕様書 S8-2 が「dashboard は常駐体への
指示ファイル投函に変更する」と言う本当の理由はここにあり、single-resident の原則
（板への push は常駐体のみ）はその結論を先に言っていたに過ぎない。

### 1.8 同一ノードで複数プロジェクトが板を巡回すると、同じ公示を二重に取り込む

`poll_board` の「取り込み済みか」の判定は**自分のバスだけ**を見る:

```python
if bus_local.read_inbox(did) is not None or bus_local.run_exists(did):
    continue
```

先勝ち経路では勝者が自分なら素通りする（`if w is not None and w != node_id: continue`）ので、
**プロジェクト A のバスへ取り込んだ直後、プロジェクト B の participate が同じ公示を
自分のバスへもう一度取り込む**。同一ノードでの二重実行になる。

現状これが表に出ないのは、複数プロジェクトの flow 設定に同じ `board` を書いた運用がまだ無いからで、
設計としては既に壊れている。§6 で塞ぐ。

---

## 2. スコープの決定 — R2 をどこで切るか

S8-2（キャンセル）と S8-3（手動入札）は、どちらも「常駐体へ指示を投函し、常駐体が板へ書く」形。
その受け皿（板の請負 tick）が R2 で、R2 は実装計画で **M** 規模かつ「設計が固まっていないので
意図的に手を付けていない」と書かれている。Phase 4 で R2 を丸ごとやるかが最初の判断になる。

**決定: R2 を 2 つに割り、前半だけを Phase 4 に取り込む。**

| | 内容 | フェーズ |
|---|---|---|
| **R2a** | 常駐体の **board tick**: 板の同期・`nodes/<node-id>.json` の書き出し（能力宣言・心拍）・**ノード宛て指示の取り込み**（キャンセル / 入札）・入札選別規則の agentcore 一本化 | **Phase 4** |
| **R2b** | **ノード直轄実行**: プロジェクトを 1 つも持たないワーカーノードが落札して `NodeWorkerPool` で実行する経路 | Phase 5 |

**なぜここで切れるか**: §1.4 のとおり、フルノードには既に「落札 → 取り込み → 実行」の経路がある。
手動入札が必要としているのは *bid を書くこと* であって *実行系* ではない——bid さえ板に載れば、
既存の `poll_board` が「自分が勝者」を見て取り込み、`NodeWorkerPool` が走らせる。
**未決事項 6（「操作だけ増えて実行できない状態になり得る」）は、この構造で消える**（§9-1 で決着）。

R2b が要るのは「プロジェクト 0 個のワーカーノード」だけで、そこは実機 canary（R1）が
終わっていない今、二重実行の実測ができない。急がない。

**R2a が同時に解くもの**:

| 積み残し | 解け方 |
|---|---|
| P1-a（`repos[].local` の板への転記） | `nodes/<node-id>.json` の書き手ができる（§6.2）。**完全に解ける** |
| P2-a（`unverifiable` の板への検証委譲） | 公示を出す口は開く。ただし請け負えるのはフルノードだけなので**半分**（残りは R2b） |
| §1.5 の宣言の二重 | 板参加の宣言を host.yaml へ寄せる（§6.4） |
| §1.8 の二重取り込み | 取り込み済み判定を板の `status/<who>.json` へ移す（§6.5） |

---

## 3. S9-4 — 診断の対話化

### 3.1 いま何が起きているか

失敗診断（`failure-diagnosis`）は `completeDoctor`（`src/features/agent-project/main/agent.js:589`）が
**ヘッドレス 1 発**で走る。画面スナップショットを JSON 化し（最大 120,000 字・`truncateSnapshot`）、
役割・禁止事項・7 見出しの指定を argv に、本文を stdin か spill ファイルに載せて 1 回だけ呼ぶ。
返ってきた Markdown をダイアログに描き、`## 差し戻し文面案` を抽出して回答欄へ流し込む。

**追加質問はできる**（`btn-doctor-submit` が「追加で質問する」になる）が、実体は
「同じスナップショットで、補足文を足して**もう 1 回**呼ぶ」であって会話ではない。
CLI 側に文脈は残らず（`no_session_args` で毎回捨てている）、エージェントは
**ファイルを 1 つも読めない**（`readonly_args` でツールを落としてある）。
「ログのこの行の前後を見せて」に相当することが原理的にできない。

### 3.2 変える範囲 — `failure-diagnosis` だけ

| モード | 変更 | 理由 |
|---|---|---|
| `failure-diagnosis` | **対話診断を既定にする**。ヘッドレスは「文面を生成」ボタンとして併設 | 原因究明は往復が要る。ここだけが「1 発では足りない」用途 |
| `consultation` / `plan-critique` / `delivery-rationale` | 変えない | いずれも**構造化された見出しの抽出**（`extractMarkdownSection` で差し戻し文面案を取り出す）に依存する。対話にすると抽出点が消える |

仕様書 S9-4 の「失敗診断ボタンは対話診断を開き、『文面を生成』ボタンを併設する」をそのまま採る。

### 3.3 起動 argv — S9 のレイヤをそのまま使う

```js
agentCli.interactiveCmd(spec, model, { readonly: true, noSession: true })
```

`interactiveLaunchSpec`（`agent.js:184`）が既に `readonly` / `noSession` を受けるので、
**新しい CLI 分岐は 1 つも足さない**（受入条件 A4）。
`readonly: "best-effort"` の CLI（kiro / copilot / cursor / ollama）については、
`agentCli.readonlyWarning(spec, true)` の戻りをダイアログに出してから開く——
S9 §6-1 の決着（「防御は持たない。保証できないことを人に見せる」）の初適用になる。

### 3.4 文脈の渡し方 — ここが本設計の要点

**120,000 字のスナップショットを対話セッションへ持ち込むことはできない。**
tmux への注入は `send-keys` の 1 行（`chatWindowScript`）で、改行を含められない
（含めると CLI が途中で確定する）。かといって全 CLI に「ファイルを読ませる」経路を要求すると、
読み取り専用モードでファイル読み取りごと落とす CLI（copilot の `--available-tools=`）で成立しない。

**決定: 対話診断は「ブリーフ（1 行・2,000 字上限）＋ 全文ファイルのパス」を送る。**

```
あなたは Agent Dashboard の読み取り専用の失敗診断エージェントです。ファイルを変更せず、
助言だけを返してください。対象: タスク T12「検収カードに MR リンクを載せる」が verify で失敗
（3 回目）。直近のエラー: <400 字>。作業ブランチ: task/T12。画面が持っている全文
（スナップショット JSON）は /tmp/agent-doctor-xxxx.md にあります——読めるなら読んでください。
まず原因の見立てを 3 行で述べ、そのあと確かめたいことを私に聞いてください。
```

この形が満たすもの:

- **ファイルを読めない CLI でも会話が始まる**。全文は「読めるなら」の追加資料であって前提ではない。
  ——`interactive.prompt_inject: "file"` や「読み取り専用でもファイルは読めるか」という
  新しい契約フィールドを足さずに済む（S9 のスキーマを触らない）
- **読める CLI（claude の plan モード・codex の read-only サンドボックス）は全文まで届く**
- 1 行なので既存の `chatWindowScript` の送信経路（`tmux send-keys -- <1行> Enter`）に
  そのまま乗る。送信スクリプト側の分岐が増えない

ブリーフの組み立ては `doctorPrompt` の隣に `doctorBriefPrompt(context, { file })` を新設する
（見出し指定を持たない・対象の同定と直近エラーだけを載せる・2,000 字で切る）。

**全文ファイルの置き場と後始末**: 既存の `writeSpill`（`agent.js:288`。WSL UNC 対応済み）を使う。
ただしヘッドレスと違い対話セッションは長命なので、呼び出し直後に消せない。
`writeWindowScript` が既に持っている流儀（`os.tmpdir()/agent-dashboard/` に置き、**次回起動時に
24 時間より古いものを消す**）に合わせ、置き場を `agent-dashboard/doctor/` に固定して同じ掃除に載せる。

### 3.5 セッション — 使い捨てにする

- セッション名 `agent-doctor-<digest>`。`digest = sha1(cli \0 needId \0 projectDir)[:8]`。
  `chatSessionName`（`loopProvider.js:296`）は接頭辞 `agent-chat-` が固定なので、**接頭辞を引数に足す**
  （既定は現行値なので既存呼び出しの挙動は変わらない）。
- 作業用セッション（CLIチャット・定常業務）と**名前空間を分ける**のは S9 §6-2 の決着そのもの——
  読み取り専用のつもりの窓が作業セッションに合流すると、そこから書き込みができてしまう。
- **同一 need の再診断は既存セッションへ attach し、ブリーフは送り直さない**。
  会話が続いているところへ同じブリーフを再投入すると文脈が二重になる。
  `chatWindowScript` は既に「新規作成時だけ送る」分岐（`if [ $__new -eq 1 ]`）を
  セッション開始コマンド用に持っているので、業務プロンプト側にも同じ選択肢
  （`promptOnNewOnly`）を足す。

### 3.6 cwd — 失敗したコードのあるところで開く

失敗診断はログだけでなくコードを見たい。既定を次の順で決める:

1. タスクの書込先リポジトリを、このノードの宣言（host.yaml `repos[]`）から解決したパス
   （`nodeRepos.resolveLocalRepo` — S3-4 の `chatCwdChoices` と同じ解決）
2. 解決できなければプロジェクト（状態リポジトリ）のフォルダ

実在しないパスで開くと端末が即死するので、`openInteractiveChat` と同じく事前に `isExistingDir` で弾く。

### 3.7 IPC と画面

| 層 | 変更 |
|---|---|
| main | `agent:doctorChat`（新規）。`{ dir, context, needId, mode }` を受け、全文 spill → ブリーフ組み立て → `interactiveLaunchSpec(readonly, noSession)` → `runChatWindow`。戻りは `{ session, cli, model, cwd, readonlyWarning, terminal }` |
| renderer | 失敗診断カードのボタンを「**AIと対話で診断**」（既定）＋「**文面を生成**」（従来のヘッドレス）に分ける。対話側は `buildDoctorContext()` の結果をそのまま渡す（文脈の組み立ては 1 実装のまま） |
| renderer | `readonlyWarning` が返ったらダイアログに「このCLIでは助言のみを保証できません」を出す |

診断セッションは `kiroLoop:capture`（既存の tmux 視聴）で dashboard 内からも覗ける——
セッション名が分かれば覗ける仕組みは既にあるので、追加実装は無い。

---

## 4. S8-1 — 観測 UI の置き場

§1.1 で「orchestration タブ内」が使えなくなった。**目的別画面の原則**（delegation feature の
「利用者向けの独立画面は持たない — 進捗は実行画面、判断は要対応、依頼はミッション」）に沿って、
板の情報を**人の問いごとに 3 か所へ割る**。

| 人の問い | 置き場 | 出すもの |
|---|---|---|
| 「出したこの仕事、誰か拾った？」 | **タスク（backlog）タブの `offloaded` カード** | 引き受けたノード・引き受けた時刻・板の phase・成果（result）の有無 |
| 「この端末で引き受けられる仕事はある？」 | **参加タブ** | open な公示（§5.3） |
| 「この端末は板にちゃんと参加してる？」 | **全体設定 → 同期** | 板の場所・このノードの宣言（tags / repos / agent_cli）・参加ノード一覧（心拍・契約バージョン） |

**委譲の独立タブは作らない**（既存方針の維持）。

### 4.1 タスクカードの板ステータス

`offloaded`（表示名「実行中（委任）」）のタスクは、いま `flow_run` へのリンクしか持たない
（`src/renderer/sections/backlog.js:599`）。ここに板の情報を足す。

- データ源: `board-adapter.toView` の正規化ビューを委譲 id で引く。
  タスク側の委譲 id は `agent-project` が `location: board` で公示したときの id
  （delegation §D1 の冪等キー——タスク・公示・run で同一）
- 新 IPC は作らず、`delegation:list` の board 分（`view.target === 'board'`）を使う。
  ただし現行の `delegation:list` は amigos ホームと flow バスも走査するので、
  **`{ only: 'board' }` を受けて板だけ読む分岐**を足す（一覧の走査コストをタスク画面に持ち込まない）

表示は 1 行に畳む: `委任先: pc-b（3 分前から実行中）` / `委任先: 未定（入札待ち 2 件）` /
`委任先: pc-b — 失敗（result）`。

### 4.2 参加ノード一覧（全体設定 → 同期）

`nodes/*.json` を読んで表として出す。§6.2 でこのファイルの書き手ができる前提の画面。

| 列 | 出典 | 意図 |
|---|---|---|
| ノード | `node` | 誰が板に居るか |
| 状態 | `heartbeat` + `fresh_after_sec` | 生きているか（stale は灰色） |
| 引き受けられるもの | `workloads` / `tags` / `agent_cli` | 自分の公示が拾われるかを人が判断する材料 |
| 手元にあるリポジトリ | `repos` のキー | **P1-a の成果がここに出る**（`local` そのものは出さない——他 PC の絶対パスは読み手に意味が無い） |
| 契約 | `contract_version` | 更新漏れの古いノードの表示（設計 §6） |

**このアプリは YAML パーサを持たない**制約（P1'-b / P1-c と同じ）にはここでは当たらない——
`nodes/*.json` は JSON。

### 4.3 `delegation-ui.test.js` の意図を書き直す

現行のテストは「委譲タブを置かない」「`features/delegation.js` を読み込まない」「目的別画面は残す」の 3 本。
本設計の UI は `sections/backlog.js` / 参加タブ / `sections/orchestration.js` に載るので、
**アサーションは 1 行も変えずに通る**。変えるのは名前と説明コメント——
現行の書き出し（「委譲は…利用者向けの独立タブにはしない」）は読みようによっては
「委譲の UI を一切置かない」と取れる。**禁じているのは独立タブと内部概念の露出であって、
目的別画面に板の状況が出ることではない**、と明記する。

---

## 5. S8-2 / S8-3 — 操作をノード宛て指示に一本化する

### 5.1 なぜ「常駐体へ投函」なのか（2 つの理由）

1. **`git+` 板では dashboard の直接書き込みが届かない**（§1.7）。押しても何も起きないボタンになる
2. **claim 規則を UI に複製しない**（受入条件 A2）。入札は lease と `(ts, who)` タイブレークを持つ
   プロトコルで、UI 側に 2 つ目の実装を作れば必ずずれる

### 5.2 新契約: ノード宛て指示ドロップ

**置き場**: `$AGENT_COMMANDS_DIR`（既定 `~/.agents/commands/`）。
`~/.agents/control/`（agent-control）・`~/.agents/budget/`（node-budget）と同じ**ノードスコープ**の並び。

**なぜプロジェクトの `commands/` を流用しないか**: 板はプロジェクトに属さない。
ワーカーノードにはプロジェクトが 1 つも無く、プロジェクト経由の口しか無ければ
**プロジェクトを持たない PC から板を操作できない**。

**形**はプロジェクト側（`agent_project/commands.py` の `ingest_commands`）と揃える——
利用者から見える挙動（送信済み → 受理済み → 失敗バナー）を 2 種類作らないため:

```
~/.agents/commands/
  <name>.json            … 指示（dashboard が原子的に置く）
  processed/<name>.json  … 受理レシート（常駐体が書く。dashboard の「受理済み」表示の根拠）
  <name>.json.err        … 取り込み失敗（理由つき。dashboard の失敗バナーの根拠）
```

**契約**: `schemas/agent-node-command.schema.json`（新規。dashboard(JS) が書き agent-project(Python) が読む
ツール横断のデータ契約なので、スキーマファイルを持つ既存の基準に合う）。

```jsonc
{
  "command": "board-bid",          // board-bid | board-cancel | board-award
  "board": "git+ssh://…/board.git", // 対象の板（省略時 host.yaml の board）
  "id": "dg-20260726120000-a1b2",   // 委譲 id
  "reason": "手動で引き受け",        // 任意（journal と受理レシートに残る）
  "node": "pc-b",                   // board-award のみ（落札させるノード）
  "issued_by": "agent-dashboard",
  "issued_at": "2026-07-26T03:00:00Z"
}
```

**冪等**: 同一 `(command, id)` の重複投函は後勝ちで 1 回に畳む（板側の書き込みは元々冪等なので
実害は無いが、レシートが二重に出ると画面が混乱する）。

**消費者は常駐体の board tick だけ**（§6.3）。dashboard 側の書き手は
`actions.js` の `dropCommand`（プロジェクト用）と対称な `dropNodeCommand` を新設する。

### 5.3 S8-3 手動入札 — 参加タブの 3 つ目の候補源

```
参加タブ
  候補: flow の未着手ノード（既存） / amigos の空きロール（既存） / **板の open 公示（新規）**
     ↓「引き受ける」
  ~/.agents/commands/<ts>-board-bid.json を置く
     ↓
  常駐体の board tick（30s）: 板を pull → bids/<node-id>.json を claim 規則で書く → push
     ↓
  常駐体の flow tick（5s）: agent-flow participate の poll_board が
    「自分が勝者」を見て取り込み → NodeWorkerPool が実行（既存経路）
```

候補は `model.js` に `boardCandidates(views, { nodeId })` を足して作る。
open（`phase === 'open'`）かつ未終端かつ**自分がまだ入札していない**公示だけを出す。

**手動入札は「自己抑制の上書き」**である。自動入札は `board_eligible`（担当リポジトリ・タグの照合）で
自分を抑えているので、条件を満たす公示は放っておいても拾われる。人がボタンを押す意味があるのは
**条件を満たさないが引き受けたいとき**（手元にクローンが無いリポジトリ・タグ宣言漏れ）と、
`owner-picks` の応募。よって:

> `poll_board` は、**板に自分名義の有効な bid が既にある公示については `board_eligible` を問わずに取り込む。**

これが「人が eligible を上書きした」の実装。変更は `poll_board` の分岐 2 行で済み、
claim 規則そのものには触らない。

**ボタンを出す条件**（未決 6 の実体・§9-1）:

| 条件 | 画面 |
|---|---|
| 常駐体が動いていて、板を巡回するプロジェクト経路が 1 つ以上ある | 「引き受ける」を活性で出す |
| 常駐体は動いているが取り込み先が無い（projects: 0 = ワーカーノード） | **非活性 + 理由**「この端末はまだ板の仕事を実行できません（Phase 5 / R2b）」 |
| 常駐体が動いていない | 非活性 + 理由「常駐体（agent-project serve）が動いていません」 |
| `agent-control` の `workloads.flow.lifecycle` が pause / stop | 非活性 + 理由（参加タブの既存規律をそのまま適用） |

判断の根拠は **`engine/status.json` の新 `board` ブロック 1 つ**にする（§6.6）——
dashboard が host.yaml と agent-flow の設定を読み解いて自前で判定すると、
§1.5 の二重宣言をもう 1 実装増やすことになる。

### 5.4 S8-2 キャンセル

`delegation:cancel` の `target: 'board'` 経路を、`board-adapter.cancel`（直接書き込み）から
`dropNodeCommand({ command: 'board-cancel', … })` へ差し替える。§1.7 の穴が塞がる。

**所有権について**: 板の契約では `cancelled.json` は「依頼者が書く」パスだが、
dashboard を動かしている PC が依頼者とは限らない。ここは割り切る:

> **パス単位の書き込み所有権は git でコンフリクトさせないための規約であって、認可ではない。**
> `cancelled.json` は誰が書いても板は終端として扱う（読み手は既にそう実装されている）。

止める判断は人にあり、人がどの PC の前に座っているかで可否が変わるのは筋が悪い。
ただし **誰が止めたかは残す**（`cancelled_by` にノード id を書く）。

`submitPost`（公示の投函）と `award` は今回触らない——dashboard に手動 post の UI は無く、
`award` は owner-picks 運用が実際に始まってから形が決まる。
`git+` 板で push されないことは delegation の README に明記し、積み残しに載せる。

---

## 6. R2a — 常駐体の board tick

### 6.1 位置づけ

`resident_cli.py` の Scheduler に **5 本目の tick** を足す（現行は supervise 5s / amigos 5s /
flow 5s / gc 600s）。周期は設計 §4.2 の周期表どおり **30s**、single-flight、
ステップ毎タイムアウト（git を伴うため）。

```python
Tick("board", 30.0, tick_board, timeout=_BOARD_TICK_TIMEOUT_SEC)
```

`host.board` が空なら丸ごと skip（amigos 参加 tick と同じ流儀）。

### 6.2 やること 1 — `nodes/<node-id>.json` を書く（P1-a の決着）

`NodeCapability`（実装済み・呼び手なし）を host.yaml から組み立てて書く。

| フィールド | 出典（host.yaml） |
|---|---|
| `node` | `node_id` |
| `workloads` | 固定 `["flow"]` + amigos バス設定があれば `"amigos"` |
| `tags` | `tags` |
| `agent_cli` | `agent_cli`（能力宣言の配列。`defaults.agent_cli` のスカラとは別キー） |
| `repos` | **`repos[]` を repos.schema 形へ**（`url` と `local`）← **P1-a** |
| `availability` | `availability` の宣言から `"HH:MM-HH:MM TZ"` を合成 |
| `max_concurrent` | `budget.max_concurrent` |
| `heartbeat` / `fresh_after_sec` | tick 時刻 / 周期の 4 倍 |
| `contract_version` | `resident.status.CONTRACT_VERSION` |

**push は内容が変わったときだけ**（心拍だけの更新で 30 秒ごとに commit を積まない）。
心拍は「板の中の相対的な鮮度」でなく `fresh_after_sec` との比較で読むので、
**内容不変なら書き換えない**方が板は静かになる。読み手（`nodes/*.json` を見る dashboard・
他ノード）は stale を「不在」と読むだけなので、心拍の更新頻度は 4 倍周期を割らなければよい。
→ **心拍だけの更新は 5 分に 1 回**に律速する。

`local` を板に載せることの意味は仕様書 S3-5 のとおり **速度最適化のヒント**であって、
入札可否は url ベースで決める（`local` の有無で入札を変えない）。

### 6.3 やること 2 — ノード宛て指示の取り込み

`~/.agents/commands/*.json` を読み、`board-bid` / `board-cancel` / `board-award` を実行する。

- `board-bid` … `agentcore.protocol` の claim（`renew_lease`）で `bids/<node-id>.json` を書く。
  終端（`result.json` / `cancelled.json`）済みなら `.err` へ理由付きで退避
- `board-cancel` … `cancelled.json` を書く（`cancelled_by` = 自ノード id）
- `board-award` … `award.json` を書く
- いずれも書けたら `sync_push`。成功で `processed/` へレシート、失敗で `.err`

実装は `agent_project/commands.py` の `ingest_commands` の**構造をなぞる別関数**にする
（`_read_command` / `_reject_command` / `_write_command_receipt` の 3 つは
プロジェクト id に依存しないので `agentcore` へ引き上げて共有する。
「取り込めるか」の述語が 2 実装になると、起こしたのに取り込めないパスが生まれる——
プロジェクト側が既に踏んだ罠）。

### 6.4 やること 3 — 入札選別規則を agentcore へ 1 本化（§1.5 の解消）

現状 `board_eligible` は agent-flow だけが持ち、入力の `board_repos` / `board_tags` は
agent-flow の設定ファイルから来る。

```
agentcore/board.py（新規）
  eligible(post, *, repo_urls, tags, agent_cli, contract_version) -> bool
```

- 判定規則は現行の `board_eligible` と同一（`requires.tags` の包含・`workspace.url` と
  `requires.repos` の担当・`requires.agent_cli` の OR・`requires.contract_version` の一致）
- URL の照合は `agentcore.repolocal.normalize_repo_url`（S3 で 1 本化済み）
- agent-flow の `board_eligible` はこれを呼ぶ薄い層に縮める
- **入力の供給元を host.yaml に寄せる**: `agent-project flow-participate` が host.yaml の
  `board` / `tags` / `agent_cli` / `repos[]` を `agent-flow participate` へ渡す
  （`--board` / `--board-tags` / `--board-repos`）。agent-flow 設定ファイルの
  `board_*` キーは**明示上書き**へ降格する（S9-3 で `cowork.chatCommand` に対して行った降格と同じ形）

`repos[]` は「担当」ではなく「手元にクローンがある」宣言だが、**入札資格としてはこれで足りる**
——手元にあるものは引き受けられるし、無いものは重いクローンを伴うので自己抑制するのが正しい。
現行 `_node_repo_ids` が要求する `owns` は repos.json（プロジェクトのレジストリ）由来のキーで、
host.yaml の `repos[]` は持たない。**`owns` の要求は落とし、`readonly` だけを除外条件に残す**。

### 6.5 やること 4 — 二重取り込みを塞ぐ（§1.8）

`poll_board` の「取り込み済みか」の判定を、自分のバスから**板の `status/<who>.json`** へ移す:

```python
st = read_json(os.path.join(ddir, "status", f"{_safe(node_id)}.json"))
if st and not vocab.is_terminal(st.get("state")):
    continue   # このノードは既にこの公示を引き受けている（どのバスへ流したかは問わない）
```

板が真実という原則にも合う（`_renew_dispatched_leases` / `report_board_results` は既に
この marker を「自分が落札した印」として読んでいる）。自分のバス側の判定は残す
（board 由来でない run との衝突を避けるため）。

### 6.6 やること 5 — `engine/status.json` に `board` ブロック

dashboard が「この端末は板に参加しているか・手動入札できるか」を判断する**唯一の根拠**。

```jsonc
"board": {
  "configured": true,
  "location": "git+ssh://…/board.git",
  "node_id": "pc-a",
  "contract_version": 1,
  "last_tick": "2026-07-26T03:00:00Z",
  "last_error": null,
  "intake_projects": ["example-project"],   // 落札を取り込めるプロジェクト経路
  "open_delegations": 3,                    // 参加タブのバッジ用
  "my_bids": ["dg-…-a1b2"]                  // 自分名義の有効な bid
}
```

`intake_projects` が空なら手動入札は非活性（§5.3 の 2 行目）。

---

## 7. 実装単位

| # | 対象 | 内容 |
|---|---|---|
| **S9-4** | | |
| a | `agent-dashboard` `agent.js` | `doctorBriefPrompt` 新設・`openDoctorChat`（spill → ブリーフ → `runChatWindow`）・cwd 解決（`nodeRepos` 再利用） |
| b | `agent-dashboard` `loopProvider.js` | `chatSessionName` に接頭辞引数・`chatWindowScript` に `promptOnNewOnly` |
| c | `agent-dashboard` `ipc.js` | `agent:doctorChat` |
| d | `agent-dashboard` renderer | 失敗診断カードのボタン 2 本立て・`readonlyWarning` の表示・spill 置き場の 24h 掃除 |
| **S8-1（観測）** | | |
| e | `agent-dashboard` `delegation/main/ipc.js` | `delegation:list` に `{ only: 'board' }`・`nodes/*.json` を読む `delegation:nodes` |
| f | `agent-dashboard` `sections/backlog.js` | `offloaded` カードに板ステータス 1 行 |
| g | `agent-dashboard` `sections/orchestration.js` | 全体設定 → 同期に「板への参加」＋参加ノード表 |
| h | `agent-dashboard` `test/delegation-ui.test.js` | 名前とコメントを「独立タブを置かない」へ（アサーションは不変） |
| **S8-2/3（操作）** | | |
| i | `schemas/agent-node-command.schema.json` | 新規契約 |
| j | `agent-dashboard` `actions.js` | `dropNodeCommand`（`~/.agents/commands/`）・レシート/`.err` の読み取り |
| k | `agent-dashboard` `delegation/main/ipc.js` | `delegation:cancel` の board 経路を指示投函へ差し替え |
| l | `agent-dashboard` `participation/model.js` + renderer | `boardCandidates` と「引き受ける」ボタン（可否と理由） |
| **R2a（常駐体）** | | |
| m | `agentcore/board.py` | `eligible()` — 入札選別規則の 1 実装 |
| n | `agentcore` | `_read_command` / receipt / `.err` の共有化（プロジェクト側から引き上げ） |
| o | `agent-project` `resident_cli.py` | `tick_board`（30s）: sync → `nodes/<id>.json` → ノード指示の取り込み → `engine/status.json` の `board` ブロック |
| p | `agent-project` `flow.py` / `request.py` | `flow-participate` が host.yaml の板宣言を agent-flow へ渡す |
| q | `agent-flow` `board.py` | `board_eligible` を agentcore へ委譲・自名義 bid があれば eligible を問わない・取り込み済み判定を `status/<who>.json` へ |
| r | ドキュメント | `agent-project.host.yaml.example`（「板の請負は未実装」の注記を更新）・`agents/README.md` 不要・delegation README・各 README・CHANGELOG |

---

## 8. テスト計画

**S9-4**

1. `doctorBriefPrompt`: 改行を含まない・2,000 字以内・タスク id と直近エラーを含む・全文ファイルのパスを含む
2. 対話診断の argv が `interactive.command + readonly_args + no_session_args`（kiro / claude / codex / copilot の 4 定義でゴールデン固定）
3. セッション名が `agent-doctor-` 接頭辞で、同一 need・同一 CLI・同一プロジェクトなら同じ名前になる
4. 既存セッションがあるとき、ブリーフを送らずに attach する（`promptOnNewOnly`）
5. `readonly: "best-effort"` の CLI で警告が返る / `enforced` では返らない
6. cwd 解決: 書込先リポジトリのローカル宣言があればそれ、無ければプロジェクト、実在しなければエラー
7. 回帰: 「文面を生成」（ヘッドレス）の argv と `## 差し戻し文面案` 抽出が現行と一致する
8. spill ファイルが 24 時間で掃除される（`writeWindowScript` の掃除と同じ判定）

**S8-1**

9. `delegation:list { only: 'board' }` が amigos ホーム・flow バスを走査しない
10. `offloaded` タスクの板ステータス行: 入札待ち / 実行中（ノード名）/ 成果あり / 失敗 の 4 表示
11. 参加ノード表: `heartbeat` + `fresh_after_sec` を過ぎたノードが stale 表示になる
12. `delegation-ui.test.js` が緑のまま（独立タブを置かない・内部概念を露出しない）

**S8-2/3**

13. `dropNodeCommand` が `~/.agents/commands/` に原子的に置く（`$AGENT_COMMANDS_DIR` を尊重）
14. `delegation:cancel` の board 経路が**板へ直接書かない**（`no-git-writes.test.js` と同じ流儀で、板ディレクトリが変化しないことを固定）
15. 手動入札ボタンの可否: `intake_projects` 空 / 常駐体停止 / lifecycle=pause の 3 ケースで非活性 + 理由
16. 候補生成: 終端した公示・自分が既に入札した公示を候補に出さない

**R2a**

17. `tick_board`: `nodes/<node-id>.json` が host.yaml の宣言どおりに書かれる（`repos[].local` を含む）
18. 心拍だけの更新は 5 分に 1 回に律速される（内容不変で 30s tick を 5 回回しても書き込みは 1 回）
19. ノード指示の取り込み: `board-bid` が bids を書く / 終端済み公示は `.err` へ / レシートが `processed/` に出る
20. `agentcore.board.eligible`: 現行 `board_eligible` と同一判定（既存テストを移植して両方で回す）
21. **自名義の有効な bid がある公示は `eligible=False` でも取り込まれる**（手動入札の意味）
22. **同一ノードの 2 プロジェクトが同じ公示を二重に取り込まない**（§1.8 の回帰テスト）
23. `engine/status.json` の `board` ブロックが板未設定のとき `configured: false` で出る

---

## 9. 未決事項の決着

### 9-1. 仕様書 §5-6: 手動入札の「ノード直轄ワーカーで実行」への接続

> 落札後の実行系が R2 に含まれるため、単独では操作だけ増えて実行できない状態になり得る。

**決着: 手動入札は「入札の意思表示」までを常駐体へ渡し、実行は既存の引き渡し経路
（`poll_board` → プロジェクトのバス → `NodeWorkerPool`）に合流させる。取り込み先を持たない
ノードでは、ボタンを理由付きで非活性にする。**

前提の読み替えが要った。「ノード直轄ワーカーでの実行」が無いと手動入札が成立しない、という
前提は正しくない——§1.4 のとおりフルノードには既に落札→実行の経路があり、そこに足りないのは
**bid を人の意思で書くこと**だけだった。R2b（プロジェクト 0 個のノードでの実行）が要るのは
ワーカーノードだけで、そのノードでは**ボタンを出さない**ことで「操作だけ増えて実行できない」
状態を構造的に防ぐ（§5.3 の表）。

可否の根拠を `engine/status.json` の `board.intake_projects` 1 か所に集約するのは、
dashboard が host.yaml と agent-flow 設定を自前で読み解いて判定すると §1.5 の二重宣言を
もう 1 実装増やすため。

### 9-2. 新規: 観測 UI の置き場（仕様書 S8-1 の前提が消えた）

**決着: 「board セクション 1 枚」をやめ、人の問いごとに 3 か所へ割る（§4 の表）。**

orchestration タブが全体設定になった以上、そこに動く一覧は置けない。
代わりに「出した仕事の様子はタスクカード」「引き受けるのは参加タブ」「参加構成の確認は全体設定」と
目的別画面の原則へ寄せる。委譲の独立タブを作らない方針は維持する。

### 9-3. 新規: `git+` 板への dashboard の書き込みが届かない（§1.7）

**決着: 板への書き込み（cancel / bid / award）はノード宛て指示ドロップ経由にし、板へ書くのは
常駐体だけにする。`post` は今回触らず、届かないことを README に明記して積み残しにする。**

single-resident の「板への push は常駐体のみ」は方針として先にあったが、**実際には既に壊れていた**
（押しても何も起きないボタンだった）。仕様書 S8-2 が言う「移行期のみ board-adapter の直接書き込み」は、
移行期という猶予が要るほどの互換対象を持たない——ローカル dir 板でしか動いていないので、
一度で切り替える。

### 9-4. 新規: 板の所有権規約は認可ではない（§5.4）

**決着: `cancelled.json` は依頼者でなくても書ける。パス単位の書き込み所有権は git で
コンフリクトさせないための規約であり、誰が操作してよいかの規則ではない。ただし `cancelled_by` に
書いたノードを残す。**

### 9-5. 新規: 板参加の宣言を host.yaml に寄せる（§1.5 / §6.4）

**決着: host.yaml を正典とし、agent-flow 設定ファイルの `board_*` は明示上書きへ降格する。**

`board_repos` / `board_tags` は S1 が host.yaml 専有と決めたノード固有宣言そのもので、
agent-flow 側に残っているのは S1 の取りこぼし。降格の形（空なら解決結果・値があれば明示上書き）は
S9-3 で `cowork.chatCommand` に対して採ったものと同じ。

### 9-6. 新規: 対話診断へ 120KB の文脈は持ち込めない（§3.4）

**決着: ブリーフ（1 行・2,000 字）＋ 全文ファイルのパス。全文は「読めるなら読め」の追加資料に留め、
CLI がファイルを読めることを前提にしない。**

`interactive.prompt_inject: "file"` を要求すると、読み取り専用モードでファイル読み取りごと落とす
CLI（copilot）で成立しない。「読み取り専用でもファイルは読めるか」を契約フィールドに足す案は、
S9 §6-1 の決着（防御は持たず、保証できないことを宣言して人に見せる）と揃えるなら筋は通るが、
**ブリーフだけで会話が始まる形にすれば、その宣言自体が要らなくなる**。契約を増やさない方を採る。

---

## 10. 積み残し（この設計に含めないもの）

| # | 内容 | 待ち先 |
|---|---|---|
| P4-a | **R2b: ノード直轄実行** — プロジェクトを 1 つも持たないワーカーノードが落札して `NodeWorkerPool` で実行する経路。§9-1 のとおり、これが無くても手動入札は成立するが、ワーカーノードは板の仕事を請けられないままになる | Phase 5（実機 canary R1 の後） |
| P4-b | **P2-a の後半** — 「検証不能」基準の板への検証委譲。公示を出す口は R2a で開くが、請け負えるのはフルノードだけ | R2b |
| P4-c | **`submitPost` / `award` の `git+` 板対応** — dashboard に手動 post の UI が無いので今回触らない。owner-picks 運用が実際に始まったら `board-award` 指示（契約は §5.2 で用意済み）に接続する | owner-picks を使い始めたとき |
| P4-d | **投機同時実行（speculation）** — 契約からは W0-10 で削除済み。板の P2 のまま | 必要が出たとき |
| P4-e | **push 配信（webhook / long-poll）** — 30s ポーリングで足りている。板設計 §5.3 の「加速装置」 | 遅いという申告が出たとき |
| P4-f | **`consultation` / `plan-critique` / `delivery-rationale` の対話化** — いずれも構造化見出しの抽出に依存するので、対話にすると抽出点が消える（§3.2） | 抽出をやめてよいと判断できたとき |
| P4-g | **対話診断セッションの掃除** — tmux セッションは人が閉じるまで残る。使い捨て（`no_session_args`）なので状態は残らないが、セッションは溜まる。`agent-doctor-*` を名前で一括 kill する口は付けない | セッションが溜まって困ったとき |
| P4-h | **参加ノード表の `local` 非表示** — 他 PC の絶対パスは読み手に意味が無いので出さない。「そのノードが手元に持っているリポジトリ名」までを出す（§4.2） | 意図的に残す |
