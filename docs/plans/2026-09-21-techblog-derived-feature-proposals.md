# テックブログ 245 本から拾った、agent-app / agent-tools に入れると効く機能

日付: 2026-09-21 / 対象: `tools/agent-app` `tools/agent-tools` `tools/agent-audit` / 状態: **提案**

## 0. 結論

9 月の記事から借りられたのは、新しい仕掛けではなく**いま黙って抜けている穴の塞ぎ方**だった。
提案を 5 観点で 29 件出し、3 レンズ（既存実装・与件と原則・効果）で反証したところ、元の形のまま
残ったものは 1 件も無い。反証側が「ここまで削れば残る」と名指しした最小形が次の 7 件で、
効果の大きさと規模の小ささで並べてある。

| # | 入れるもの | 場所 | 柱 | 規模 |
|---|---|---|---|---:|
| 1 | ~~route の問い文に converse の境界を足す~~ **測って却下（2026-09-22）** | `agentcore/route.py` | 柱2 | S |
| 2 | 振り分けの結果を打ち切らずに 1 行記帳する | `agent-app/src/main/{ipc,audit}.js` | 柱2・柱3 | S |
| 3 | 受信箱に「何時間待たせているか」を出す | `agent-app/src/main/attention.js` | 柱2 | XS |
| 4 | answer の依頼を readonly 非保証の CLI に配らない | `agent-app/src/main/ipc.js` | 柱2 | S |
| 5 | 共有の再投函上限を心拍途絶にも効かせる | `agent-app/src/main/share/requester.js` | 柱1 | XS |
| 6 | 差分の常設基準を経路によらず効かせる | `agentcore/verifycontract.py` | 柱2 | S |
| 7 | node-budget の窓のフェイルオープンを塞ぐ | `agentcore/nodebudget.py` | 柱3 | XS |

2026-09-22 に 1 から 7 まで順に実装した。**1 だけが実測で落ちた**（3 通りの文面すべてが
32/40 を下回り、文面を戻した。記録は `2026-09-21-agent-app-judge-request-routing-design.md` §6.3）。
2 から 7 は実装してテストを足した。

却下した主要な案は 3 つ。**margin を hold の第 2 軸にする**（保管済み台帳で 56 通り掃引して
結果が 1 行も動かなかった）、**readonly が効いているかを実機 probe で測る**（readonly_args が
モデルへの指示でもあるため測定が交絡する）、**verify に ID 引用義務を足す**（`quality-evaluation.js`
に実装済み）。詳細は §3。

---

## 1. 何を読んだか

19 フィードから 245 本（2026-09 中心）を読み、関連度 2 以上の 77 本を本文まで読んだ。

| テーマ | 件数 |
|---|---:|
| その他 | 59 |
| AIエージェント・コーディング支援 | 43 |
| クラウド・インフラ | 41 |
| 開発プロセス・チーム | 28 |
| セキュリティ | 25 |
| LLM運用・評価・観測 | 21 |
| データ・DB | 19 |
| フロントエンド・UI | 5 |
| ローカルLLM・推論効率 | 4 |
| 合計 | 245 |

この 9 月の特徴が 2 つある。1 つは判定専用モデル Jev の記事が一気に増えたこと。もう 1 つは
事業会社が「AI に任せる範囲の線引き」を実測付きで書き始めたことである。

### LLM 運用・評価・観測

- [Jevは学習なしに使えるカテゴリ分類器だ！ということでMNISTやらせてみたらダメダメだった件](https://qiita.com/segavvy/items/b08eee93a0abe0c0726d) — 同じ 100 枚を 16 条件で回し、確度しきい値の掃引と「一度も予測されない候補」の検出を並べた。表現を替えると正解率が 0.16 から 0.38 まで散る。
- [Jevを、自然言語で分類基準を渡せる識別モデルとして使う](https://zenn.dev/cybernetics/articles/4f3762bd470fc4) — 候補名だけでは分類基準が決まらない。候補の説明文に境界を書き下すのが使う側の仕事だと述べる。
- [【TypeSafe】出力を絞ればLLMも速くなる？](https://dev.classmethod.jp/articles/jev-vs-gemini-flash-cross-match/) — 生成 LLM の自己申告確率は 107 件中 中間帯が 1 件しかなく、しきい値で人に回す仕分けが成立しない。
- [ADKマルチエージェントでkintone承認を自動化した設計と自動評価（eval）の実践](https://engineering.dena.com/blog/2026/08/kintone-approve-agent-adk-eval/) — 27 件中 7 件の FAIL が全部「採点軸の選び違い」だった。判定の中身がツール引数に乗るとき、最終応答の文面一致は使えない。

### セキュリティ

- [Write() で拒否しても、Claude Code は12回とも書き込んだ](https://qiita.com/suwa_nobu/items/e867493a5cbcdfaa40c9) — 宣言した権限が実際に効いているかを書き込み経路の総当たりで測った。12 試行すべて素通りし、拒否イベントは 0 件。
- [Claude Code が AGENTS.md を読むようになった。ただし1回目のセッションでは読まれない](https://qiita.com/suwa_nobu/items/c795cf89d0fd4091c9cb) — 指示ファイルの注入を合言葉で外から測る手口。初回セッションで静かに読まれない窓がある。
- [決済プラットフォームに常駐する自律AIエージェントの設計と運用](https://engineering.mercari.com/blog/entry/20260630-28a5eee688/) — 安全判定は fail-closed、計測は fail-open と失敗方針を逆にする。認証情報はエージェントに渡さず境界で注入する。
- [OpenAI、モデルのミスアライメントを報告する枠組みを公表](https://gihyo.jp/article/2026/09/openai-model-misalignment) — 引き継ぎ要約が注入経路になった事例を含む 6 件。対処は文言修正でなく環境側を塞ぐ手に寄っている。

### 開発プロセス・チーム

- [DBの性能検証を人の判断に委ねない](https://techblog.zozo.com/entry/stored-procedure-plan-check-ci) — 機械が閾値付きで証跡を抽出し、LLM は生の証跡を一次ソースとして読み、出力は enum の verdict に固定する。判定のブロック作用だけをフラグで切り離して段階導入する。
- [既存システムのAI分析ワークフローを作り直す](https://techblog.zozo.com/entry/screen-analyze-v2) — 機械が対象に ID を振り、引用を義務づけ、未参照 ID を数える。「精度が上がった」でなく「精度を確認できるようになった」が成果だと書く。
- [チームの人数が減っても仕事を回す](https://techblog.zozo.com/entry/wear-web-fe-adjust-workflow) — 危険なパスは規則で Approve 2 件に固定し、残りだけ AI がリスクを見る。定時催促でマージまで 30 時間超が 20 時間以内になった。
- [「AIに任せる」を決めるのではなく、問い続ける](https://www.wantedly.com/companies/wantedly/post_articles/1090447) — 線引きを由来で 3 つに分ける。最初から引いた線、試して引き直した線、そもそも引けない線。

### クラウド・インフラ

- [AI コストの DeNA 的管理手法](https://engineering.dena.com/blog/2026/08/ai-token-cost-report/) — 制限を消極的（知らせる）と積極的（止める）の二段に分ける。事実を出すだけでは人は動かないと明言する。
- [全社に OpenCode + LiteLLM を導入してコストを抑えつつ AI 活用を進めている話](https://zenn.dev/jtcc/articles/7e74fef42580a1) — 総額は月で決め、実効の枠は 7 日でローリングリセットする。モデル名を見せず Low/Mid/High/XHigh の 4 段だけ出す。
- [【AWS】x402でAIエージェントにじゃぶじゃぶ課金させよう！](https://zenn.dev/ncdc/articles/ec72bc1882d597) — 上限額と期限を作成時に固定して後から変えられない支払い枠。枠を切る役と使う役を別ロールに分ける。
- [生成 AI 機能開発を加速するゴールデンパス](https://engineering.dena.com/blog/2026/09/golden-path-for-generative-ai-platform/) — 詰まりを `ApproximateAgeOfOldestMessage` の 1 指標で見る。長期鍵をコンテナに置かず OIDC に寄せる。

### AI エージェント・コーディング支援

- [Migrating the GitHub Copilot runtime to Rust, using Copilot](https://github.blog/ai-and-ml/generative-ai/migrating-the-github-copilot-runtime-to-rust-using-copilot/) — 43 万行の移植を 128 PR に刻んだ。1 PR で新実装を入れて旧実装を同じ PR で消すので main が常に出荷可能。
- [Project HydraFusion](https://github.blog/ai-and-ml/github-copilot/project-hydrafusion-frontier-quality-via-multi-model-orchestration/) — 費用を下書きだけで数えず、査読・改稿・エスカレーション・再試行・フォールバックの全レグで合算する。
- [AIとの会話がクロストークにならないようにfirstmateで1体とだけ話す](https://zenn.dev/inapvision/articles/firstmate_agent_crew) — 窓口エージェントは対象プロジェクトに読み取り専用。監視は LLM でなく bash のウォッチャーが行い、判断が要るときだけ声をかける。
- [Anthropic、Claudeの「Projects」を刷新](https://gihyo.jp/article/2026/09/claude-code-new-projects-feature) — 1 つのメインチャットに投げた依頼を Claude が新規または既存スレッドへ割り当てる。対応待ちだけを Overview に集める。

### データ・DB、フロントエンド、ローカル LLM

- [Claude Codeとの会話を、チームの記憶にする](https://techblog.zozo.com/entry/memory-manager-context-reuse) — 正本は Markdown、索引だけ SQLite。約 7,000 件で 3 位以内に入るのは 68.9%、1 件も返らないのは 2.7%。
- [AI に実装を任せるための仕様と検証ループ](https://engineering.dena.com/blog/2026/09/spec-and-verify-loop-proxysql-eks/) — 要件と確かめ方を対にして書く。環境ごとに変わる実測値は人が計測して仕様に定数として渡し、AI に推定させない。
- [Modern Web Guidance、Codex向けプラグインも追加](https://gihyo.jp/article/2026/09/modern-web-guidance-plugin-for-codex) — ガイド本文を常駐させず、索引を検索して該当 1 本だけ取り出す。ガイド 1 本ごとに判定スクリプトを置き、有無の A/B で効果を測る。
- [Ternary Bonsai 2 27BをM1Pro・16GBで動かす](https://zenn.dev/okame_rara/articles/bonsai_2_27b_m1) — 三値量子化で 27B を 7.21GB に落とし 9.6 tok/s。ただし ollama でも本家 llama.cpp でも読めず fork が要る。

### 反証中に取り直した実測

保管済みの台帳（`tools/agent-tools/eval/results/archive/20260921-route-calibration-real/ledger.jsonl`、
160 セル）を新規推論ゼロで再集計した。提案 1 と 2 の土台になる。

| 測ったもの | 結果 |
|---|---|
| hold 掃引 0.6（止めた / 正しい / 誤って止めた / 止め損ね） | 10 / 9 / 1 / 1 |
| hold 掃引 0.75 | 8 / 8 / 0 / 2 |
| hold 掃引 0.9 | 8 / 8 / 0 / 2 |
| confidence × margin の 2 次元掃引（56 通り） | 到達する結果は上の 3 通りだけ。margin 列は全行で無変化か悪化 |
| handling の正解 | 32 / 40 |
| handling の誤り 8 件のうち gold=converse | 5 件（うち確度 0.83 以上が 3 件） |
| RT2 で一度も予測されなかった候補 | 0 件（8 候補すべて 1 回以上） |
| RT3 skill の boolean | 39 / 40、yes 9 件中 8 正解、見落とし 0 |
| 較正（Brier / ECE） | 0.110 / 0.017 |
| 1 セルの所要（p50 / p90 / 160 セル合計） | 5.0 秒 / 7.6 秒 / 14.9 分 |

---

## 2. 提案

### 1. route の問い文に converse の境界を 1 行足す（却下。2026-09-22 に実測）

> **結果**: 3 通りの文面を RT1 の 40 セルで引き直し、30 / 30 / 29 と 3 通りとも現行の 32/40 を
> 下回ったので文面を戻した。説明文を伸ばした選択肢が磁石になり、converse を取ると task と answer を
> 食う。文面を戻した再測が誤り 8 件の顔ぶれまで前回と一致したので、これは揺れではない。
> 詳細は `2026-09-21-agent-app-judge-request-routing-design.md` §6.3。以下は却下前の提案内容。


**入れる場所** `tools/agent-tools/agentcore/agentcore/route.py` の `HANDLINGS`。**柱2 / 規模 S**
（production は 2 行。検収は RT1 の 40 セルで 3〜4 分）。

**問題。** handling の誤り 8 件のうち 5 件は gold=converse で、依頼の前半が候補タスクに似ているために
task や answer へ引っ張られている。n17「コミットメッセージを書いてコミットして」、n24「PR の説明文を
書いて gh で PR を作って」、n18「README を書き直して」がその形。確度 0.83 以上が 3 件あるので、
しきい値でも margin でも除けない。現在の `HANDLING_CONVERSE` は「会話の中で実行させる（ファイル編集、
コマンド実行、単発作業）」としか書いておらず、「候補タスクが似た仕事をしていても実行依頼なら converse」
という境界を持たない。設計書 `2026-09-21-agent-app-judge-request-routing-design.md` §6.2 の結びが、
次の一手としてこの問い文を名指ししている。

**機能。** `HANDLING_CONVERSE` と `HANDLING_TASK` の説明文に境界を書き足す。converse 側は「依頼が実行や
変更（書く、直す、コミットする、作る）を含むなら、候補タスクが似た仕事をしていても converse」。task 側は
「入力値だけ替えて同じ仕事をもう一度回すことだけを求めているとき」。宣言の欄も新しい候補も増やさない。
材料不足の受け皿は既存の `HANDLING_OTHER` が担う。

**効果。** 同じ標本 40 件で handling が 32/40 から動くかが 3〜4 分で出る。RT2 から RT4 は問い文を触らない
ので引き直さない。決定的な 3 段目を足さずに、文面で取れる分を先に取る。

**根拠**: [Jevを、自然言語で分類基準を渡せる識別モデルとして使う](https://zenn.dev/cybernetics/articles/4f3762bd470fc4)（候補名では分類基準が決まらない。重なりは説明文の側でどちらへ寄せるか定義する）、
[Jevは学習なしに使えるカテゴリ分類器だ](https://qiita.com/segavvy/items/b08eee93a0abe0c0726d)（入力表現を替えると正解率が 0.16 から 0.38 まで散る）。

**反証と答え。** 元案は 4 本立てだった。margin の 2 軸化は保管済み台帳で 56 通り掃引して結果が 1 行も
動かず、高確度の誤り 5 件は margin も高いので捕まらない。確度帯ごとの件数と正解率は
`readout_eval.calibration_report` が既に出している。予測回数 0 の候補は 0 件で直す故障が無い。
問いごとの state 削りは RT3 が 39/40 で削り代が 1 セルしかなく、接頭辞キャッシュを壊す損は既測
（batched 23.5s 対 interleaved 70.2s）。残った 1 本も「利用者が全タスクの宣言に境界を手書きする」形では
柱3 が禁じる「モデル代の節約を人の時間で払う」になるので、`route.py` の問い文 1 か所へ縮めた。
margin が動かなかった事実は設計書 §6.2 に 1 行残して閉じる。

### 2. 振り分けの結果を打ち切らずに 1 行記帳する

**入れる場所** `tools/agent-app/src/main/ipc.js` と `src/main/audit.js`。**柱2・柱3 / 規模 S**。

**問題。** `ipc.js` は `routed` を `preparing` の表示・`heldMessage`・`information` にしか使わず、この経路に
`audit.feed` の呼び出しが 1 つも無い。そのため hold 下限 0.75 の根拠は 1 人が書いた合成標本 40 件のままで、
実会話の確度分布が増えない。判断段（route・select・extract）の壁時計も記帳されておらず、0.27.0 で足した
extract は計上外の純増になっている。

**機能。** `routed` が確定した直後に、hold の真偽にかかわらず `audit.feed` を 1 回呼ぶ。行は
`event: 'routing_decision'` を持つ観測行にする。載せるのは `workload: 'routing'`、purpose（route / select /
extract）、handling の choice と confidence、1 位と 2 位の差、target の id、hold の真偽、所要秒。依頼文は
載せない。`status` は使わない。人がどうしたか（追従・押し切り・無視）は列にしない。

**効果。** 実会話の確度分布と所要秒が purpose 別に溜まる。集計は `GROUP_KEYS` を触らず既存の
`usage --by purpose` がそのまま使える。押し切りは「routing 行の直後に同じ ref で routing を切った chat 行」
として既存の行の並びから引ける。

**根拠**: [How we make AI coding more cost efficient](https://github.blog/ai-and-ml/github-copilot/how-we-make-ai-coding-more-cost-efficient-without-sacrificing-task-quality/)（費用の単位を応答 1 回からタスク 1 本へ移す。出力圧縮ツールが局所では短くなってタスク全体では増えた例を挙げる）、
[AI コストの DeNA 的管理手法](https://engineering.dena.com/blog/2026/08/ai-token-cost-report/)（測る層と止める層を分ける）。

**反証と答え。** 元案は「人の決着を `status` 列に持たせる」形だった。`audit.js` の `row()` は `status` を
done / failed / cancelled / escalate に丸めるので followed も overridden も全部 failed に落ちる。さらに消費 0 の
行は `event` を持たない限り `collect.py` で ledger 扱いになり、`usage` の runs を水増しして `stats.pass_rate` と
`aggregate_ratings` の `outcome_ok` を下げる。その格付けは 0.21.0 で自動モデル選択の入力になっているので、
柱2 の可視化のために柱3 の実測基盤を汚すことになる。加えて「押し切り率で hold 下限を引き直す」という
中心の効果は成立しない。案内が出るのは確度が 0.75 を超えたときだけなので、下限を下げる方向の材料
（止め損ね）は構造的に観測できない。判断段のトークンで本実行との比を出す案も、台帳 465 行すべてで
トークンが未実測、`rates.per_cli` は ollama 系 4 キーだけで default も無く、分母が 0 になる。

そこで `status` を使わず `event` 行に落とし、決定の 3 値も列にしない。node-budget の ledger には書かず
audit-feed 1 本に絞る。比はトークンでなく秒で出す。画面の「先週：止めた N 件 / 押し切り M 件」は、行が
溜まって読めるようになってから足す。下限そのものを動かしたいなら、この記帳と並行して
`store.listSessions` から実会話の依頼を抜き `corpus.json` に足して `route_cells.py --hold-sweep` を回す。
止め損ねまで数えられるのはこちらだけである。

### 3. 受信箱に「何時間待たせているか」を出す

**入れる場所** `tools/agent-app/src/main/attention.js` と `src/renderer/renderer.js`。**柱2 / 規模 XS**。

**問題。** `attention.js` の `workflowSources` は open な interaction から id と mode と prompt だけを写し、
`automation/agent-flow.js` の `interactionsOf` が既に読んでいる `createdAt` を捨てている。そのため
ワークフローの人待ちが何時間止まっているかが画面に出ず、受信箱は要対応と未読の 2 列を更新順に
並べるだけになっている。

**機能。** `workflowSources` が interaction に `createdAt` を 1 フィールド載せる。`project()` が action の項目に
経過時間を付け、action 内を長い順に並べる。画面は `attentionStatus()` の語尾に「（3 時間待ち）」を足す。
`classify` は触らない。しきい値は新設せず、agent-flow が既に持つ `expiresAt` を過ぎたときだけ印を変える。

**効果。** 人待ちの滞留が画面から読める。`attention.js` は純粋関数のままなので、既存の投影テストに
ケースを 2 つ足せば検査できる。

**根拠**: [チームの人数が減っても仕事を回す](https://techblog.zozo.com/entry/wear-web-fe-adjust-workflow)（経過時間つきの催促でマージまで 30 時間超が 20 時間以内になった）、
[firstmate で 1 体とだけ話す](https://zenn.dev/inapvision/articles/firstmate_agent_crew)（監視は bash のウォッチャーが行い、判断が要るときだけ人に声をかける）。

**反証と答え。** 元案は会話の要対応にも経過時間を出し、督促を 1 日 1 回、ホームに件数 1 行、一覧に
実行中の区分を足す形だった。会話の attention は `tmux.js` の `setPhase` が時刻を持たずメモリ上だけなので、
「3 時間待ち」はアプリ再起動で 0 に戻る。一番測りたい長い待ちで一番外す。督促は `notify.js` が発生時に
既に OS 通知を出しており、受信箱ボタンにも件数バッジが常時出ているので 2 本目の通知経路になる。
ホームの行と実行中区分は `renderer.js` が既に「· 応答中」を出している。そこで、正典が時刻を持つ
ワークフローの interaction 1 点に絞った。会話側に経過時間を出したいなら、台帳側に時刻を作るのが先で、
それは agent-app の変更ではない。

### 4. answer と判定した依頼を、readonly を保証できない CLI に配らない

**入れる場所** `tools/agent-app/src/main/ipc.js`（`routed.handling.choice === 'answer'` の分岐）。**柱2 / 規模 S**。

**問題。** `ipc.js` は route が answer を返すと `requested = { ...requested, readonly: true, answerOnly: true }` を
立てる。ところが `agents/*.json` の readonly 宣言は自己申告で、実機の 8 定義のうち 3 つが best-effort である。

| 定義 | readonly | readonly_args |
|---|---|---|
| claude | enforced | `--permission-mode plan` |
| codex | enforced | `--sandbox read-only` |
| aider | enforced | `--dry-run` |
| ollama | enforced | `--think on`（道具を渡さない） |
| vscode-copilot | enforced | `[]`（既定が読み取り） |
| copilot | best-effort | `--available-tools=view,grep,glob` |
| cursor | best-effort | `--mode ask` |
| kiro | best-effort | `--trust-tools=fs_read` |

`agentcli.readonly_warning()` は自分の docstring で「このレイヤは宣言どおりの argv を組み立てるだけで、
フラグを無視する CLI への防御は持たない」と書いている。いま出るのは画面の警告だけで、実行は止まらない。

**機能。** answer 経路で readonly を立てるとき、解決された spec の readonly が enforced でなければ、その
依頼を answer として配らない。enforced な CLI へ降格するか、使い捨ての作業ツリーで回す。判定は宣言を
読むだけなので決定的で、モデルに訊かない。

**効果。** 「申告が嘘なら静かに書き込む」経路が、実測を待たずに閉じる。probe を作らずに事故が止まる。

**根拠**: [Write() で拒否しても、Claude Code は12回とも書き込んだ](https://qiita.com/suwa_nobu/items/e867493a5cbcdfaa40c9)（宣言した防御が実際に走っているかは別に確かめないと分からない）、
[「ハッキングコンテストはAIによって終わった」](https://atmarkit.itmedia.co.jp/ait/articles/2609/20/news004.html)（サンドボックス前提が崩れた。危害を認識したら止める層が要る）。

**反証と答え。** 元案は「readonly が効いているかを実機 probe で測り、verified の割合を `--ratings` に流す」
形だった。この測り方は交絡している。readonly_args はモデルへの指示でもあるので、「1 バイトも増えなかった」
は権限層が止めたのかモデルが従っただけなのかを分離できない。記事が綺麗に測れたのは
`--permission-mode acceptEdits` でモデルを書く気にさせたうえで deny ルールの綴りだけを変数にしたから。
このリポジトリに deny ルールは 1 件も無く、readonly_args はモード指定か道具許可リストなので、提案の
3 経路は「その道具が一覧にあるか」の 1 問に潰れる。8 定義のうち新情報が出るのは 3 件だけで、24 本の
実 CLI ターンは柱3 が守る個人枠を実際に食う。そこで probe を丸ごと落とし、未計測を verified に塗る代わりに、
未保証を書き込めない場所へ置く形にした。0 クレジットで決定的に効く。

### 5. 共有の再投函上限を心拍途絶にも効かせる

**入れる場所** `tools/agent-app/src/main/share/requester.js` の `watchdog()`。**柱1 / 規模 XS**。

**問題。** 失敗経路は `MAX_ATTEMPTS = 2` で止まる。ところが `watchdog()` は心拍が途絶えた依頼を
`state = 'open'` に戻すだけで、`attempts` を増やさず上限も見ない。心拍を落とし続ける執行者の間を依頼が
無限に巡回でき、1 周ごとに誰かの CLI 時間が最大 leaseMs 分消える。

**機能。** `watchdog()` で列へ戻す前に `attempts` を 1 つ増やし、`MAX_ATTEMPTS` を超えたら列へ戻さず
`error_class: 'lost'` で依頼者へ返す。`RETRY_CLASSES` は `['quota','transient']` のままなので `lost` は
そのまま終端する。あわせて participant 側が、心拍 2 回途絶による停止を台帳の既存語彙 `'lost'` で書く
（`ledger.js` は `lost` を status の 4 値に持ちながら誰も書いておらず、`today()` は既に件数から除いている）。

**効果。** 1 件の依頼が焼く他人の枠に、宣言された有限の上限が付く。空振りは新しい列を足さずに `lost` の
行数で数えられる。

**根拠**: [【x402】AI「支払いは任せろー」私「やめて!」](https://zenn.dev/ncdc/articles/93cd19a1d882f7)（タイムアウト 30 秒に対し生成が数分かかり、リトライ 4 回で 0.1 が 0.4 になって成果物はゼロ）、
[【AWS】x402でAIエージェントにじゃぶじゃぶ課金させよう！](https://zenn.dev/ncdc/articles/ec72bc1882d597)（払い済みで成果物なしの記録を残し、その行がある間は次の購入前に人の確認を強制する）。

**反証と答え。** 元案は `requires.platform` と `requires.commands` の追加、`outcome` 列の新設、再投函上限の
新設の 3 本立てだった。共有で走るのは `readonly: true` を固定した CLI 1 ターンだけで、参加者が依頼者の
ビルドコマンドを走らせる口が無いので、「素の Windows 機が xvfb-run を起動して落ちる」という情景が存在
しない。CLI 本体の PATH 実在は `offeredClis()` が `listAgents` の結果と交差させて既に絞っている。`outcome`
列は `status` の 4 値と重なる 2 本目の語彙になる。再投函上限は同じ既定値 2 で実装済みである。そこで
3 本のうち 2 本を落とし、上限の本物の穴 1 点だけを塞いだ。参加者タブの「空振りで消えた枠」は、実機
2 台の往復が通って行が溜まってから作る。

### 6. 差分の常設基準を経路によらず効かせる

**入れる場所** `tools/agent-tools/agentcore/agentcore/verifycontract.py` の `build_plan`。**柱2 / 規模 S**。

**問題。** `DIFF_CRITERION`（「このタスクの差分が、上の基準の対象範囲に実在すること」）を足しているのは
agent-project の `build_task_verification_plan` だけで、agentcore の `build_plan` は足さない。agent-project を
通らずに組んだ plan には差分の常設基準が入らず、「何も変えずに全 pass を返す」道が残る。

**機能。** 正典の文言を agentcore 側へ移し、自然文基準がある plan には `build_plan` が常設基準を最後尾に
足す 1 実装にする。agent-project 側は自前で足すのをやめて呼ぶだけにする。

**効果。** 経路によらず「差分が基準の対象範囲に実在するか」が既存のフェイルクローズに載る。「誰も触れて
いない変更があるタスク」の件数は、receipt の末尾基準が fail か inconclusive だった run を agent-audit で
数えれば出る。新しい列もプロンプトへの diff 同梱も要らない。

**根拠**: [既存システムのAI分析ワークフローを作り直す](https://techblog.zozo.com/entry/screen-analyze-v2)（機械が対象を列挙し、未参照を数える。不具合が出たらプロンプトでなく計算側を疑う）、
[GPU上で動く高級言語「Bend 2」公開](https://gihyo.jp/article/2026/09/bend-2)（条件を書いたファイルは人の持ち物で AI に触らせない）。

**反証と答え。** 元案は「基準と変更に ID を振り、判定行に引用を義務づけ、未参照を uncovered として receipt に
載せる」形だった。この機構は `tools/agent-app/src/main/quality-evaluation.js` に実装・テスト・コミット済みで、
仕様も spec §19 補足にある。さらに「検証役が全項に触れたかを数えていない」は事実に反し、`verifyplan.py` の
`_vp_judge_criteria` と agent-project の `normalize_verification` はどちらも plan 側の基準を回して未回答を fail へ
倒すので、被覆率は定義上つねに 1.0 になる。uncovered を pass のまま並べるのは、いま機械が fail にしている
ものを非ゲートの列へ移す緩和で、C5 の逆を向く。全 ID の引用義務は出力を長い自由記述 JSON へ寄せる形で、
この機の実測では 1/5 に落ちる。そこで ID 契約も uncovered 列も人の検収ルーティングも落とし、常設基準の
置き場を 1 か所へ寄せるだけにした。C7 の「同じ判定を 2 実装しない」もこれで守られる。

### 7. node-budget の窓のフェイルオープンを塞ぐ

**入れる場所** `agentcore/nodebudget.py` の `_period_prefix` と `agent-audit/agent_audit/usage.py` の
`aggregate_agent_limits`。**柱3 / 規模 XS**（2 ファイル各 1 行、テスト 1 本）。

**問題。** `_period_prefix` は未知の period に空文字を返し、`ledger_paths` が前方一致のフィルタを外す。結果と
して上限の窓が total 相当まで黙って広がる。正規の書き手は不正値を弾くが、config.json を手で編集すれば
到達する。もう 1 つ、`aggregate_agent_limits` は period を day / month / total に固定しているのに `_period_floor`
は既に week を計算でき、画面にも `--period week` にも week がある。CLI 別の畳み込みだけが day に落ちている。

**機能。** `_period_prefix` の頭で未知値を `"day"`（最も狭い窓）へクランプする。`aggregate_agent_limits` の
許容値に `"week"` を足す。

**効果。** 設定の打ち間違いが上限の消失にならない。既に week を受けている画面と CLI の取りこぼしが埋まる。

**根拠**: [全社に OpenCode + LiteLLM を導入して…](https://zenn.dev/jtcc/articles/7e74fef42580a1)（総額は月で決め、実効の枠は 7 日で切る）、
[AI コストの DeNA 的管理手法](https://engineering.dena.com/blog/2026/08/ai-token-cost-report/)（消極的制限と積極的制限を分ける）。

**反証と答え。** 元案は period に week を通し、soft の手前に warn_ratio を足す形だった。ベンダの回復窓は
UTC 月曜ではない。`collect.py` は Claude の `Current week` の `Resets Aug 14 at 7am (Asia/Tokyo)` を読んでおり、
リポジトリはこれを実測の `reset_at`（`reset_source: "observed"`）として持ち、導いた境界は画面で意図的に
「—」と描いている。UTC 月曜を新しい推測として置き直すのは C9 に反する。効果の側も崩れている。実機の
`config.json` は tokens も execution_minutes も各ワークロードの max_tokens も 0 なので `has_limits` が False で
縮退が一度も発火しない。台帳の直近 6 日 465 行すべてでトークンが未実測、クラウド 4 CLI のレートが
`rates.per_cli` に無く default も無いので、週の窓を足しても数えるものが 0 になる。warn の 1 行も `audit.js` が
90% で「残りわずか」を既に出している。そこで週の窓の新設と warn 層を落とし、フェイルオープン 1 行と
集計の取りこぼし 1 行だけ残した。上限そのものを効かせたいなら順序が逆で、まず
`rates.default_tokens_per_second` を実測から入れてクラウド 4 CLI の消費が 0 でなくなること、次に tokens に
非 0 を入れて soft が実際に立つことを確かめる。

---

## 3. 見送った案

- **margin を hold の第 2 軸にする** — 保管済み台帳で 56 通り掃引して結果が 1 行も動かず、高確度の誤り 5 件は margin も高いので捕まらない。
- **候補カバレッジの診断を足す** — RT2 の 8 候補すべてが 1 回以上予測されており、直す故障が存在しない。
- **確度帯ごとの件数と正解率を出す** — `readout_eval.calibration_report` が既に buckets と Brier と ECE を出している。
- **問いごとに state を削って A/B する** — RT3 が 39/40 で削り代が 1 セル。接頭辞キャッシュを壊す損は既測。
- **棄権したとき上位 2 件を返す** — 0.6 から 0.75 の帯は既に decided で画面に候補名が出ている。hold しない全件に出すと 32 件発火・正解 2 件で、無関係なタスク名を 30 件出す。
- **定型物を候補・選択・採用の 3 つで数え、共有先へ還す** — `usage --by ref` が既に呼ばれた回数を出す。候補上限 8/8/6 は `ranked()` が relevance で先に絞るので効いておらず、選択率 0 で候補から外すと片道ラチェットになる。公開実績は 0 件。
- **判断の決着を台帳の一級の列にする** — `status` が 4 値に丸めて全部 failed になり、消費 0 の行が pass_rate と自動選択の格付けを汚す。押し切り率は hold の正誤の真値ではない。
- **判断段のトークンで本実行との比を出す** — 台帳 465 行すべてでトークンが未実測、レートも無いので比の分母が 0 になる。
- **readonly が効いているかを実機 probe で測る** — readonly_args がモデルへの指示でもあるため verified が交絡し、24 本の実ターンが個人枠を食う。
- **公開の直前に二段スキャンを置く** — 公開対象 785 ファイルに既存パターンを掛けると 38 成果物がヒットし、サンプルした全件が偽陽性だった。本物のトークン形は 0 件。決定的スキャンは `_head.py` と `privacy_gate.py` に既に 2 本ある。
- **引き継ぎ要約の指示を 3 類型で弾く** — 落とす対象と残す対象が文として同一で、利用者本人の常設指示（確認せず進めてよい）を消してしまう。extract 側は値が画面に出て人が実行ボタンを押す。
- **共有に OS とコマンドの条件を足す** — 共有は readonly 1 ターン固定で、参加者が依頼者のコマンドを走らせる口が無い。
- **node-budget に week の窓と warn_ratio を足す** — 実機は上限が 0 で縮退が一度も発火せず、週次で回るクラウド 4 CLI のトークンが 1 行も実測されていない。
- **受信箱に督促とホームの件数行と実行中の区分を足す** — 要対応は発生時に OS 通知が飛び、バッジも常時出ている。会話の attention は時刻を持たず再起動で 0 に戻る。
- **verify に ID 引用義務と uncovered 列を足す** — 引用の機械照合は `quality-evaluation.js` に実装済みで、未回答基準は既に fail へ倒れている。

統合の段階で落とした案も残す。**残量を見て共有へ回すのを自動で勧める**（前提の警告層が未実装で、3 条件が
揃う頻度の実測が無い）、**下書きが通る割合で段を分ける**（小型モデルの worker 受入が既測 2/21 で、判断段の
消費が台帳に載るまで比較の土台が無い）、**判定役の腕を差し替えて比べる**（比べる相手が決まっていない）、
**共通指示の注入経路を画面に出す**（「必要になるまで入力欄を出さない」に反する）、**ブラウザ添付の往復を
減らす**（根拠が記事 1 件で、往復の回数を数えていない）。

---

## 4. 実装の結果（2026-09-22）

| # | 状態 | 検査 |
|---|---|---|
| 1 | **却下**（3 文面とも悪化。文面は現行に戻した） | RT1 40 セル × 4 回（変更 3 回・戻して 1 回） |
| 2 | 実装（`audit.feedRouting` と `ipc.js` の 1 呼び出し） | `test/audit.test.js` に観測行の形 1 本 |
| 3 | 実装（`workflowSources` が `createdAt` を写し、`project` が経過時間で並べる） | `test/attention.test.js` に並びと締め切り 1 本 |
| 4 | 実装（answer は enforced な CLI へ寄せ、無ければ約束を取り下げる） | `test/herd.test.js` の照合を更新 |
| 5 | 実装（`watchdog` が試行を数え、上限超過は `lost` で終端） | `test/share-e2e.test.js` に上限 1 本 |
| 6 | 実装（`DIFF_CRITERION` の正典と付加規則を agentcore へ） | `test_verifycontract.py` に規則 1 本、既存 3 本を新契約へ |
| 7 | 実装（未知 period を day へクランプ、usage が week を受ける） | `test_nodebudget.py` に窓 2 本 |

次は 1 の残る手、つまり **実会話から依頼を抜いて `corpus.json` に足し、手で正解を付けて
`route_cells.py --hold-sweep` を回す**。hold 下限を実際に動かせる経路はここだけで、止め損ねまで
数えられるのもここだけである。2 で溜まり始める `routing_decision` の行がその標本の入口になる。
