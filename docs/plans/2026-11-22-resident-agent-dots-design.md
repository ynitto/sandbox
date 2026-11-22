# 常駐型の自律エージェント（Muse / dots 型）を Copilot アプリと kiro-cli で組む — 導線の検討

> 作成 2026-11-22 ／ 状態: 検討（未実装・承認待ち）。既存の OSS を土台にせず自作で組むことは決定済み（§10）
> 対象: 新規（仮称 `agent-dots`）と、既存の `copilot-app-jobs` / `agent-loop` / `change-watch` / Dagu /
> `statemachine-use` / `ltm-use` / `moltbook-use` / `gitlab-issue-hub-agent` / `agent-app`
> 上位文書: [`agent-tools-concept.md`](../designs/agent-tools-concept.md)（柱 2「人の判断を最少で最良に」、柱 3「資源効率」）

## TL;DR

Muse（Meta, 2026-09）と dots（OpenAI, 2026-09）の芯は「**目標と境界を一度渡すと、裏で見張り・調べ・
動き、変化か承認が要るときだけ戻ってくる**」ことにある。この芯の部品は、ほぼ全部がこのリポジトリに
既にある。足りないのは、それらを 1 つの目標の上に束ねる**薄い層**だけである。

- **1 つの dot = 1 つの目標フォルダ。** 目標・境界・計画・自分へのメモ・受け箱を 1 フォルダに置く。
  Copilot アプリは自動実行がプロジェクト（フォルダ）単位なので、フォルダ = プロジェクト = 自動実行 1 本で
  そのまま合う。kiro-cli は agent-loop の 1 エントリに当てる。
- **起こすかどうかは LLM を使わずに決める。** Dagu の定期実行で `change-watch get` と目標フォルダの状態を
  見て、変化・期限・承認の返事が無ければ何もしない（トークン 0）。
- **外への作用は LLM に直接させない。** エージェントは「やること」を提案として書き、決定的なゲート
  （`dot act`）が境界と照らして、実行・人に聞く・捨てるに振り分ける。Muse の Sentinel に当たるのはこれ。
  補助として、各 CLI の `preToolUse` フックで境界外のツールを止める。
- **自発的な調べもの（proactive research）は読み取り専用。** dots と同じく、人が触っていない間の調べ物は
  読み取り権限のプロファイルで、1 日の回数に上限を付けて回す。
- **人との接点は 1 か所。** 知らせと承認は GitLab のハブの issue（→ 通知メール・Power Automate → Teams）に
  寄せ、返事は change-watch で拾う。画面（agent-app）は一覧と承認を見せるだけ。

## 1. Muse / dots を部品に分ける

| # | 機能（Muse / dots の言い方） | 中身 | このリポジトリで当てる部品 | 足りないもの |
|---|---|---|---|---|
| F1 | 目標を渡すと計画に割って進める | 目標 → 計画 → 複数日にまたがる実行 | `statemachine-use`（plan → confirm → apply）、`agent-project`（バックログ・receipt 検算） | 目標 1 つ分の器（フォルダの契約） |
| F2 | 変化があったら戻ってくる | 見張り → 変化のときだけ起こす | `change-watch get` / `commit`、Dagu の定期実行・`preconditions` | 「起こす理由」をまとめる決定的な判定 |
| F3 | 手が空いているときに調べる（proactive research） | 読み取り専用の調べ物、結果はメモへ | `copilot-app-jobs`（`--stay` で居座り）、`agents/*.json` の読み取り専用モード、`copilot365-bridge`（組織の文脈） | 1 日の枠と、調べる対象の許可リスト |
| F4 | 自分で動くか聞くかの規則（dots の rules / Muse の Sentinel） | 作用ごとの可否判定 | `agents/*.json` の権限軸、`change-watch call`（書き込み口） | 境界（`policy`）と、それを強制するゲート |
| F5 | 専用の計算機・ブラウザ | 隔離された実行場所 | Copilot アプリ（Windows のプロジェクト）、WSL の kiro-cli、`webapp-testing` / Playwright | なし（範囲は目標フォルダに閉じる） |
| F6 | 自分へのメモ・記憶 | 目標ごとの作業メモと、横断の長期記憶 | `ltm-use`、`moltbook-use` の retro | 目標フォルダ内のメモの形 |
| F7 | スマホ・Slack/Teams から話しかける | 知らせ・承認・追加の指示 | `gitlab-issue-hub-agent`（ハブの issue ↔ メール・Power Automate）、`teams-use` / `outlook-use` | 承認待ちの issue と返事の対応づけ |
| F8 | 数千のアプリにつながる | コネクタ | MCP サーバ、`gl.py` / `redmine-use` / `ms365-copilot-use` などのスキル | なし（境界で許可したものだけ使う） |

Muse は、エージェントが利用者の許可なくメールや私的なメッセージを読みに行ったという報告と、権限を細かく
決めたいという要望を受けている。ここから引く教訓は 2 つで、本書の F3・F4 の決め方に効いている。
**読む対象も境界で許可したものだけにする**、**許可は作用ごと・相手ごとに分ける**。

## 2. 全体の形

```text
                 人（スマホのメール / Teams / agent-app）
                        ▲ 知らせ・承認の依頼        │ 返事・追加の指示
                        │                           ▼
                 ┌──── ハブの issue（GitLab）────────┐
                 │  gitlab-issue-hub-agent の仕組み │
                 └──────────────▲───────────────────┘
                                │ change-watch get/commit（返事を拾う） / call（書く）
 Dagu 定期実行（例: 15 分おき）  │
   └─ dot tick ── 起こす理由はあるか？（LLM 不使用）
        │  ・見張り先の変化（change-watch get）
        │  ・計画の期限・待ち時間の満了
        │  ・承認の返事が来た
        │  ・自発の調べ物の枠が残っていて、人が触っていない
        │
        ├─ 理由なし → 終わり（トークン 0）
        └─ 理由あり → 依頼文を決定的に組む（目標・境界・計画の位置・メモ・新しい出来事）
                 │
                 ├─ Copilot アプリ: copilot_app_job.py submit（受け箱 → 自動実行が拾う）
                 └─ kiro-cli    : agent-loop / agent-send でプロンプト投入（WSL の tmux）
                         │
                         ▼ エージェントは「提案」を書いて終わる（outbox/*.json）
   dot act ── 境界（policy）と照らす（LLM 不使用）
        ├─ auto   → 実行（change-watch call・スクリプト）→ 結果を journal へ
        ├─ ask    → ハブの issue に承認の依頼を書く → 返事を次の tick が拾う
        └─ deny   → 捨てて journal に残す
```

**境目は 2 本だけ引く。** 「起こすかどうか」（`dot tick`）と「外に作用するかどうか」（`dot act`）。どちらも
LLM を使わない決定的な判定で、エージェントはその間で考えて提案するだけにする。これは
gitlab-issue-agent / gitlab-mr-review-agent が「検出は change-watch、条件は Dagu、エージェントは判断だけ」と
した分け方と同じで、done を機械の検算に置く agent-project の不変条件とも揃う。

## 3. 目標フォルダの契約（1 dot = 1 フォルダ）

```text
dots/<名前>/
  goal.md            目標・完了の条件・期限（人が書く。最初の会話でエージェントが下書きする）
  policy.yaml        境界：読んでよい先、auto でやってよい作用、ask にする作用、禁止、1 日の枠
  plan.yaml          計画（statemachine-use の YAML。plan → confirm → apply の骨格を流用）
  state.json         いまのステート・次に起きる時刻・待っている承認（dot だけが書く）
  notes/             自分へのメモ（目標の中だけで使う。横断の知見は ltm-use / moltbook へ）
  inbox/             新しい出来事（tick が change-watch の結果と返事を置く）
  outbox/            エージェントの提案（1 件 1 ファイル、決まった JSON）
  journal.jsonl      やったこと・聞いたこと・捨てたことの記録（追記のみ）
  .copilot-jobs/     Copilot アプリの受け箱（copilot-app-jobs と同じ）
```

- フォルダをそのまま **Copilot アプリのプロジェクト**として開き、自動実行を 1 本作る（`copilot_app_job.py prompt
  --dir dots/<名前> --stay 50`）。kiro-cli なら agent-loop の作業ディレクトリにする。
- 複数 PC で動かすなら、`dots/` を git の状態リポジトリに置き、agent-project の CAS（同時に 1 ノードだけが
  その dot を進める）を借りる。最初は 1 台で始め、この節は後回しでよい。

### 3.1 境界（policy.yaml）の形

```yaml
read:                    # 読んでよい先（ここに無いものは読まない。Muse の教訓）
  - gitlab: group/repo   # issue・MR
  - folder: C:/work/specs
write:
  auto:                  # 聞かずにやってよい
    - gitlab.comment: group/repo
    - file.write: dots/<名前>/notes
  ask:                   # 毎回聞く
    - gitlab.issue.create: group/repo
    - mail.send: "*"
  deny:
    - git.push: "*"
budget:
  wake_per_day: 24       # 起きてよい回数
  proactive_per_day: 3   # 自発の調べ物の回数（読み取り専用）
quiet_hours: "22:00-07:00"   # この間は ask を溜めて朝にまとめて出す
```

作用の名前（`gitlab.comment` など）は `dot act` が知っている決まった一覧に限る。一覧に無い作用は `deny`
扱いにする（未知を通さない）。

### 3.2 提案（outbox）の形

```json
{"kind": "act",  "action": "gitlab.comment", "target": "group/repo#12", "body": "…", "why": "…"}
{"kind": "ask",  "question": "見積りの前提を A と B のどちらで進めますか", "options": ["A", "B"]}
{"kind": "note", "body": "…"}
{"kind": "wait", "until": "2026-11-25T09:00", "why": "先方の返事待ち"}
{"kind": "done", "evidence": ["…"]}
```

`done` は `goal.md` の完了の条件に対応する検査（`statemachine-deterministic-check` の `check`）が通ったときだけ
受け付ける。エージェントの自己申告では完了にしない。

## 4. 起こし方（tick）の決め方

| 理由 | 拾い方 | 起こすときの権限 |
|---|---|---|
| 見張り先の変化 | `change-watch get`（受け手は dot ごと） | `policy` どおり |
| 承認の返事 | ハブの issue のコメントを `change-watch get`。issue の番号と承認依頼の ID で対応づける | `policy` どおり |
| 期限・待ちの満了 | `state.json` の `next_wake` | `policy` どおり |
| 自発の調べ物 | 上のどれも無い・人が触っていない・`proactive_per_day` が残っている | **読み取り専用に固定**（`agents/*.json` の読み取りモード）。結果は `notes/` と `ask` だけ |
| 何も無い | — | 起こさない |

- 同じ dot を同時に 2 つ起こさない（Dagu の排他と、copilot-app-jobs の 1 フォルダ 1 件ずつ、をそのまま使う）。
- 枠を使い切ったら起こさず、ハブの issue に 1 行だけ知らせる（柱 3: 枯渇時は必ず止まり、止まったことが分かる）。
- 依頼文は短く保つ。目標・境界の要約・計画の現在地・新しい出来事・メモの直近 N 件だけを渡し、
  会話の履歴は持ち越さない（毎回まっさらなセッション。継続性はフォルダが持つ）。

## 5. Copilot アプリと kiro-cli の使い分け

| | Copilot デスクトップアプリ | kiro-cli |
|---|---|---|
| 動く場所 | Windows。PC が起きていてアプリが開いている間だけ | WSL。tmux の中で常駐できる |
| 起こし方 | 外から始める口が無い → **受け箱**（`copilot-app-jobs`）。`--stay` で居座らせて遅れを消す | `agent-loop` / `agent-send` で即時に投入。ACP（`kiro-cli acp`）でも駆動できる |
| フック | `sessionStart` / `preToolUse` / `agentStop` / `sessionEnd`（moltbook-retro で実績） | `agentSpawn` / `preToolUse` / `userPromptSubmit` / `stop` |
| 向いている dot | Office の文書・Windows 側のフォルダ・社内の Web（Playwright）・自発の調べ物 | コード・GitLab の issue / MR・イベントで即応したいもの |
| 権限の絞り方 | 自動実行のプロジェクトを dot フォルダに限る + `preToolUse` で境界外を止める | `agents/kiro.json` の読み取りモード + `preToolUse` |

**同じ目標フォルダの契約の上で、走らせる体を dot ごとに選べる**形にする（`policy.yaml` か `goal.md` の頭に
`runner: copilot-app | kiro`）。体の差は `dot` の起こし方の 2 分岐だけに閉じ込め、目標・境界・提案・ゲートは共通にする。
これは `agents/*.json` で CLI を差し替える既存の考え方（[`agent-cli-plugin-design.md`](../designs/agent-cli-plugin-design.md)）の延長である。

## 6. 人から見た導線

1. **目標を渡す。** agent-app で「新しい目標を教える」か、ハブの issue に書く。エージェントは 1 回の会話で
   `goal.md`（完了の条件つき）と `policy.yaml` の下書き、最初の計画を出す。
2. **一度だけ承認する。** 見るのは「完了の条件」と「聞かずにやってよいこと」の 2 つだけ。以後の細かい確認は
   境界で決まっているので来ない（柱 2: 人の判断は最少の回数で）。
3. **放っておく。** dot は変化・期限・返事のときだけ起き、手が空けば読み取りで調べてメモを溜める。
4. **聞かれたときだけ答える。** ハブの issue のコメント（＝メール・Teams の通知）に、選択肢つきで 1 問ずつ来る。
   返事をコメントすれば次の tick が拾う。夜間は溜めて朝にまとめる。
5. **終わりを確かめる。** `done` は完了の条件の検査が通ったときだけ出る。報告には根拠（journal の該当行）を付ける。
6. **振り返る。** 終わりに moltbook の retro（バグ・知見の共有）と ltm-use への保存を 1 回だけ行う。

### 6.1 画面のイメージ（承認待ち。CLAUDE.md の規則により、承認まで実装しない）

借りる作法: 一覧は「概要」の定期実行と同じ `.execution-card` + `.execution-card-head`、新規は「新しいワークフローを
教える」と同じ `.blank.teaching-create`、承認待ちの問いは `.composer-shell` の会話と同じ形。

```text
┌ 目標 ─────────────────────────────────────────────────────────────┐
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ 見積り資料の更新                                   [承認待ち 1]│ │
│ │ 次に起きる: 15:30 ・ 今日 5/24 回                     [開く]   │ │
│ ├──────────────────────────────────────────────────────────────┤ │
│ │ 前提を A と B のどちらで進めますか                             │ │
│ │ [A で進める]  [B で進める]  [あとで]                          │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ リリース前の MR の見張り                             [見張り中]│ │
│ │ 最後に起きた: 14:00 ・ 変化なし                       [開く]   │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                  [新しい目標]     │
└───────────────────────────────────────────────────────────────────┘

「新しい目標」（.blank.teaching-create と同じ並び）
  新しい目標
  何を、いつまでに終わらせたいですか
  完了の条件と、聞かずにやってよいことを、このあと一緒に決めます。
  ┌──────────────────────────────────────────┐
  │                                          │
  └──────────────────────────────────────────┘
  [始める]  動かす場所: Copilot アプリ ▾
```

主ボタンは各面に 1 つ（承認待ちの面では選択肢の先頭を `.primary` にしない。選ばせる問いなので並列）。
画面に仕組みの説明は置かず、README に書く。

## 7. 新しく作るもの（最小）

| もの | 中身 | 規模の目安 |
|---|---|---|
| `tools/agent-dots/dot.py`（仮） | `tick`（起こす判定と依頼文の組み立て）・`act`（境界のゲート）・`answer`（返事の取り込み）・`status` | 標準ライブラリのみ。copilot-app-jobs と同じ作り |
| 目標フォルダの雛形と `schemas/dot-*.schema.json` | `goal` / `policy` / `outbox` の JSON Schema | — |
| Dagu の例 | `tick` を定期実行し、`act` を後続に置く DAG（change-watch の例と同じ形） | 1 ファイル |
| `preToolUse` フック | `policy.yaml` の `deny` と読み取り先の外を止める。moltbook-retro のフックと同じ置き方 | 1 スクリプトを両 CLI で共有 |

再利用するもの（作らない）: 受け箱（copilot-app-jobs）、tmux の駆動（agent-loop）、見張りと書き込み口
（change-watch）、計画の実行（statemachine-use）、ハブの issue（gitlab-issue-hub-agent）、記憶（ltm-use）、
振り返り（moltbook-use）、画面（agent-app）。

## 8. 段階

| 段 | やること | 確かめること |
|---|---|---|
| D0 | 目標フォルダの契約と `dot tick` / `dot act` だけ。体は Copilot アプリ 1 本、作用は `notes` への書き込みと GitLab のコメントだけ | 変化が無い tick でセッションが始まらない。境界外の提案が `deny` になる |
| D1 | `ask` とハブの issue での承認。返事の取り込み、夜間の溜め置き | 承認から実行までが人の操作 1 回で済む |
| D2 | 自発の調べ物（読み取り専用・1 日の枠） | 枠を超えて起きない。書き込み系のツールがフックで止まる |
| D3 | kiro-cli を体に足す（`runner: kiro`） | 同じ目標フォルダが体を替えても同じに進む |
| D4 | agent-app に「目標」の画面（6.1 の承認後） | 実機のスクリーンショットで作法の一致を確認 |
| D5 | 複数 PC（状態リポジトリと CAS）、agent-board への委譲 | 2 台で同じ dot を同時に進めない |

## 9. 決めてほしいこと

1. **誰のための dot か。** Muse / dots は個人の秘書だが、このファミリーはチーム利用が柱 1。まず個人（自分の
   PC・自分の利用枠）で D0〜D2 を作り、チームへの委譲は D5 に回す案でよいか。
2. **人との接点。** ハブの issue（→ GitLab の通知メール → Power Automate → Teams）に寄せる案でよいか。
   Teams に直接返事を書けるようにするなら、Power Automate 側の受け口が別に要る。
3. **最初の体。** Copilot アプリから始める案（外から起こせない制約を受け箱で解く実績がある）か、kiro-cli から
   始める案（即応・フックが揃う）か。
4. **名前。** 仮称 `agent-dots`。他社製品名を避けるなら `agent-resident` などに変える。

## 10. 既存の OSS を土台にしない（2026-11-22 決定）

常駐の体を [Hermes](https://github.com/NousResearch/hermes-agent) に任せる案を比べたうえで、**自作で組む**と決めた。

Hermes を使えば、常駐（gateway）・定期実行（cron と `--no-agent --script`）・窓口（メールなど）・記憶
（`MEMORY.md`）は手に入る。それでも採らない理由は次のとおり。

| 理由 | 中身 |
|---|---|
| Copilot デスクトップアプリを動かせない | Hermes の copilot-acp が動かすのは Copilot の CLI。アプリには外から始める口が無く、受け箱（copilot-app-jobs）は Hermes を入れても残る |
| kiro-cli が推論の中身に下がる | `kiro-acp` は本流に無い自作パッチ（`tools/hermes-kiro-acp`）で、追従の手間が続く。Kiro 自身のツールとフックを禁じるので、moltbook-retro と同じフックの置き方が使えない |
| 肝心の部分は結局自作になる | 作用ごと・相手ごとの境界のゲート（`dot act`）と、完了の機械検査は Hermes に無い |
| 同じ役が 2 つになる | 記憶（Hermes の `MEMORY.md` と ltm-use）、スキル（Hermes の自作スキルと git-skill-manager）の正が割れる |
| 使える道具の制約 | kiro-cli / Copilot / MS365 Copilot だけが使える環境（copilot365-bridge の前提）でも同じ形で動かせる |

Hermes から借りるのは考え方だけにする。検出はスクリプトで回してトークンを使わない（`--no-agent --script` と
同じ）、作用の口を 1 つに絞る、の 2 点で、これは本書の `dot tick` / `dot act` にすでに入っている。

## 参考

- Meta の Muse: [Introducing Muse](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/)、
  [Built In の解説](https://builtin.com/articles/meta-muse)（専用 VM、外への作用を別エージェント Sentinel が承認、権限の細かさへの懸念）
- OpenAI の dots: [Introducing dots](https://openai.com/index/introducing-dots/)、
  [TechCrunch](https://techcrunch.com/2026/10/26/how-openai-turned-its-coding-tools-into-dots-an-always-on-personal-assistant/)、
  [Digital Trends](https://www.digitaltrends.com/computing/openais-dots-push-ai-beyond-chatbots-with-always-on-agents-that-can-investigate-build-and-act-across-4000-apps/)
  （目標と境界を渡す、専用の計算機とブラウザ、自発の調べ物は読み取り専用、自分で動くか聞くかの規則）
- Hermes: [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs)、
  リポジトリ内の検討 [`2026-06-10-hermes-gitlab-issue-gateway.md`](./2026-06-10-hermes-gitlab-issue-gateway.md)
