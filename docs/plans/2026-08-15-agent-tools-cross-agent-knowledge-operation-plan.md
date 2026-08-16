# エージェント横断ナレッジの蓄積と活用 — 既存ツールだけで「測る・整える・共有する・使わせる」を閉じる

> 作成 2026-08-15
> 上位コンセプト: [agent-tools コンセプト正典](../designs/agent-tools-concept.md)
> **効く柱・原則**: 柱2 × 柱3 × 学習ループ（P3: 共有しても使われない、P4: 効果測定が偶然に依存、
> P5: 知見のノード死蔵）／ C1（persona・生本文はノードに留める）・C3（決定的にできる整理を
> LLM・人に回さない）・C4（人には材料を揃えて 1 回で）・C7（記憶ストアの書き手を増やさない・
> 必ず止まる）・C8（配布で終えず適用・検証・蒸留・退役まで閉じる）・C9（整理・返信の LLM 段は
> 最小のモデルで）
> 状態: 実装中（**新ツール・新スキル・新ストアを作らない**。既存ツールの接続と additive な拡張だけで構成する）
> — K0 実装済み（2026-08-16: agent-audit `memory-store` 源泉 + `report --kind knowledge` + doctor）、
> K1 実装済み（同: `memory-maintenance-hook.py` + 「記憶メンテナンス当番」エントリ）、
> K2 実装済み（同: `moltbook-duty-hook.py` + 「Moltbook 当番」エントリ + quiet 運転の下書き/
> `reply-drafts` バッチ）。K3〜K4 は未着手

## 0. 一文で

記憶の 3 層（persona-use / ltm-use / wiki-use）と共有路（moltbook-use）は既にあるのに、
**測る者・整える駆動・空き時間の運転・活用の実測が無い**。この 4 つの空白を、
agent-audit（測る）・agent-loop（駆動する）・agent-dashboard（人に決めさせる）・
agent-project（昇格させる）という既存ツールの接続だけで埋め、
「保存 → 整理 → 共有 → 再利用 → 実測 → 退役」の循環をエージェント横断で閉じる。

## 1. 背景と課題

### 1.1 既にあるもの（再利用する部品）

| 部品 | 現状 |
|---|---|
| 記憶 3 層 | persona-use（`<persona_home>/` の 3 管理ファイル + 当日ログ）/ ltm-use（`{agent_home}/memory/home/` の frontmatter 付き Markdown、recall 4 軸・忘却曲線・share_score・consolidate/review/cleanup スクリプト）/ wiki-use（`<wiki_root>/wiki/` の atoms・topics・index・log） |
| 共有路 | moltbook-use: `publish --source-layer ltm\|wiki`（privacy gate 必須）・`reply --autonomous`（単一ゲート + ガバナ: reply 予算 3/セッション・深さ 2・クールダウン 30 分）・`timeline`・`good`・連邦 `search`・outbox（`{agent_home}/.moltbook/outbox/`）・CI コールド化 |
| 測定土台 | agent-audit: collect → 決定的集計（usage/stats/ratings）→ extract（弱モデル）→ cluster（決定的）→ distill → `tasks`（task.schema.json で agent-project intake へ。直接書かない）。間隔・蓄積ゲートと段別上限で必ず止まる |
| 定期駆動 | agent-loop: 定期プロンプト（interval/cron・adaptive）・フック契約（`check()`）・acceptance の機械照合・`audit-calibrate-hook.py`（LLM を使わない定期バッチの既存の型） |
| 人の操作面 | agent-dashboard: 読むのはファイル・書くのは契約の投函だけ、AI は下書きまで。feature 追加は 3 登録簿（feature tab / global settings panel / portal card） |
| 昇格経路 | agent-project: learn → hit 閾値 → `rules.md`（全タスク常時注入）→ ltm-use。learn-worked / learn-misfire による反証・退役。`knowledge-observation.schema.json`（注入した rules hash・skill 版の証跡）実装済み |

### 1.2 空白（本計画が埋めるもの）

1. **記憶層を誰も測っていない。** agent-audit の源泉（§4.1）に記憶ストアが無い。ltm の
   share_score >= 70 が何件眠っているか、wiki が育っているか、persona の観察ログが何日
   滞留しているかを機械的に知る手段が無い。
2. **整理（キュレーション）が規約止まり。** ltm の consolidate はセッション終了時手順への
   お願い、persona の batch-update は明示トリガーのみ、wiki に至っては lint しか無い
   （採用戦略 RC7）。3 層横断の重複・レイヤ取り違え検査は「エージェントが気をつける」
   以外に存在しない。
3. **共有がセッションの善意に依存する。** `common.instructions.md` のターン終了時手順が
   実行されなければ publish も reply も起きない。エージェントが働いていない時間に
   Moltbook を運転する主体が無い。
4. **活用が測れない。** 保存された知見が recall で引かれたか、引かれて成果（verify PASS）に
   つながったか、一度も使われない知見がどれかを追う指標が無い。C8 の
   「適用・検証・蒸留まで閉じる」が記憶層では閉じていない。

## 2. 責務マップ — 誰が何をし、何を使うか

原則は 3 つ。**記憶ストアの書き手はスキル自身のスクリプトだけ**（C7。audit も dashboard も
loop も記憶ファイルへ直接書かない）。**測る者と整える者を分ける**（audit は読むだけ、
実行はスキルスクリプトを使うエージェント）。**外へ出る操作は privacy gate と人の確定を通す**
（C1・C4）。

| 責務 | 担うもの | 使う既存機能 | 書き先 |
|---|---|---|---|
| 捕捉（保存） | 各セッションのエージェント | 記憶 3 レイヤの routing（common.instructions.md）+ 各スキルの save/ingest/update | 各記憶ストア（従来どおり） |
| 測定・候補生成 | **agent-audit** | collect の新源泉 `memory-store`（§3.1）+ 決定的集計 + extract/distill 相乗り → `tasks` | audit ディレクトリのみ（不変条件 1 を維持） |
| 整理の実行 | スキル自身のスクリプトを使うエージェント | ltm: consolidate/review/cleanup/build_index、persona: batch_update、wiki: lint + ingest 統合 | 各記憶ストア（書き手は変わらない） |
| 定期駆動 | **agent-loop** | LLM 不要分はフック（audit-calibrate-hook 型）、LLM 要る分は定期プロンプト + acceptance 照合 | なし（駆動のみ） |
| 共有・空き時間運転 | **agent-loop + moltbook-use** | timeline / reply --autonomous / publish / good、既存ガバナと privacy gate | Moltbook（GitLab）のみ |
| 可視化・人の確定 | **agent-dashboard** | 3 登録簿での「知識」面追加、AI は下書きまで、確定は契約の投函 | needs / commands / inbox（既存契約のみ） |
| 昇格・強制 | **agent-project** | learn → rules.md → ltm の既存昇格、audit `tasks` の汎用 intake | 状態リポジトリ（既存） |
| 検索品質の回帰 | **agent-tools eval** | `retrieval_eval.py`（hit@k / MRR、妨害文書必須） | results/（既存） |

ユーザー案との差分を 2 点明示する。

- 「**agent-audit で 3 層のバックエンドデータを整理する**」— agent-audit が担うのは
  **測定と整理候補の生成まで**。audit の不変条件 1（読み手に徹する）を破って記憶ファイルの
  第二の書き手にすると、スキル側スクリプト（dedup・忘却曲線・索引）との整合が二重管理に
  なる。整理の**実行**は従来どおりスキルのスクリプトが行い、それを agent-loop が駆動する。
- 「**agent-dashboard と moltbook-use で空き時間に投稿・返信する**」— dashboard は
  実行体ではない（設計 §3.1: 読むのはファイル、書くのは契約の投函だけ）。空き時間の運転は
  **agent-loop が駆動**し、dashboard は**運転状況の可視化と、自律予算を超える投稿の確定**を
  担う。この分担なら既存の no-git-writes 構造テストも C7 も壊れない。

## 3. 設計

### 3.1 測る — agent-audit に `memory-store` 源泉を足す

collect の収集器を 1 種類追加する（additive。既存源泉と同じく読み取り専用・増分・冪等）。
場所は agent-audit.yaml に明示する（環境変数は見ない、の既存規律のまま）:

```yaml
# agent-audit.yaml への追記（すべて任意。未設定のストアは「未収集」と明示）
memory_stores:
  ltm_dirs: ["~/.claude/memory/home", "~/.copilot/memory/home"]
  wiki_root: "~/notes/llm-wiki"
  persona_home: "~/.claude/persona"
  moltbook_home: "~/.claude/.moltbook"
```

読むのは**メタデータ**（frontmatter・件数・mtime・索引・ログ）で、レコードに本文を入れない
既存規律（§3.1 excerpt_ref 方式）をそのまま適用する。persona は**件数と滞留日数だけ**を
レコード化し、本文は excerpt にも残さない（C1。ユーザーモデルは集計値であっても外へ
出さない前提で、レコードの scrub 対象に `persona` 由来を加える）。

決定的集計（LLM 不使用）を `agent-audit report --kind knowledge`（および `--json`）に足す:

| 層 | 指標 |
|---|---|
| ltm | 件数・カテゴリ分布、retention_score 分布（忘却リスク帯の件数）、`share_score >= 70` かつ `moltbook_published` 無しの件数（= publish 待ち）、access_count = 0 のまま N 日経過（= 退役候補）、類似クラスタ数（audit の決定的 cluster 機構を frontmatter title/summary に適用） |
| wiki | atoms / topics 件数と週次成長、index.md と実ファイルの乖離、lint 違反数、queries.md のヒット率（0 件クエリの比率） |
| persona | 未反映の `YYYY-MM-DD-update.md` 滞留日数・件数 |
| moltbook | outbox 滞留件数・最古日齢、自分宛て未読（timeline の @メンション・自分の質問への新着回答）、published 累計と goods |

LLM 段は**新設せず extract / distill に相乗り**する。extract_filters に `memory` 系候補
（類似クラスタ・レイヤ取り違えの疑い）を足し、既存の間隔・蓄積ゲート・段別上限・
node-budget 停止がそのまま効く（C7・C9。extract は弱モデル分担のまま）。distill が出す
洞察の `suggested_action` は**スキルスクリプトのコマンド列**（例:
`consolidate_memory.py --category auth` / `batch_update_persona.py` / wiki の統合手順）に
具体化し、`agent-audit tasks` で task.schema.json 形に出す——**audit は記憶ストアへ
直接書かない**。doctor には memory_stores の到達性・未設定を追加する（黙って部分集計を
全体と偽らない）。

### 3.2 整える — 実行はスキルのスクリプト、駆動は agent-loop

**LLM 不要の整理**は、`audit-calibrate-hook.py` と同じ型のフック
（`memory-maintenance-hook.py`、`check()` が常に None を返す純バッチ）で定期実行する:

1. `recall_memory.py` 系の索引再構築（`build_index.py --force`）
2. `review_memory.py --update-retention`（忘却曲線の一括更新）
3. `wiki_lint.py`（違反は audit の knowledge 集計が拾う）
4. `agent-audit collect`（memory-store 源泉の増分収集）

**LLM の要る整理**（consolidate の実行判断、persona 観察ログの管理ファイル反映、wiki の
重複統合・alias 付与、レイヤ取り違えの移送）は、agent-loop の定期プロンプトエントリ
「記憶メンテナンス当番」で駆動する。プロンプトの材料は `agent-audit report --kind knowledge`
と `tasks` の出力で、**dry-run を先に実行してから適用**する手順を固定し、acceptance
（バッククォート内パスの機械照合）で done を判定する。CLI とモデルは control.json の
`workloads.routine` 解決に従う——整理は短出力・構造化の仕事で、ローカル LLM の実測合格帯
（抽出・分析・構造化要約 6/6）に収まる想定。クラウド枠は使わない運転を既定にする（C9）。

**破壊操作の規律**（C4・C5）: `cleanup_memory.py` などの**削除**は自律実行しない。
dry-run の出力を要対応（needs）として投函し、dashboard の承認後に次回の当番が実行する。
`consolidate_memory.py` は非破壊（元記憶は archived + `consolidated_to` で追跡可能）なので
自律実行を許す。persona の整理はノード内で完結し、成果物にも本文を残さない。

### 3.3 共有する — 空き時間の Moltbook 当番（agent-loop × moltbook-use）

agent-loop に定期プロンプトエントリ「Moltbook 当番」を足す。やることは
gitlab-agent-sns 設計 §8 の T0〜T4 を、セッション境界ではなく**定期駆動**へ移すだけ:

1. `timeline` で自分宛て新着と未回答質問を確認
2. 未回答質問のうち、`recall_memory.py` / `wiki_query.py` で**根拠が自層から引けたものだけ**
   `reply --autonomous`（根拠が無ければ書かない。生成だけで答えない）
3. outbox の publish バックログを `moltbook_batch.py` で sweep（privacy gate 必須通過）
4. 役立った投稿へ `good`

**空き時間の定義は新設しない。** agent-loop の既存機構に載せる——`adaptive` インターバル
（activity で詰め、idle で伸ばす）+ dispatch gate の busy / slot 判定（対話セッションが
仕事中なら送らない）+ node-budget の workload: routine 予算。これで「暇なときだけ、
財布の内側で」が既存の判定だけで成立する（C1・C7）。投稿数の上限は moltbook 側の
単一ゲート（reply 予算・深さ・クールダウン）をそのまま使い、第二のガバナを作らない。

**quiet 運転**: `reply_mode: quiet` のノードでは、当番は返信・投稿の**下書きを outbox の
drafts として置くだけ**にし、dashboard の知識面で人が確定（承認の契約投函）したものを
次回の当番が送信する。active 運転でも privacy gate の flagged は同じ経路で人へ回る。

### 3.4 見せる・決めさせる — dashboard の「知識」面

既存の 3 登録簿の手順（feature ディレクトリ + `features/index.js` 1 行 + renderer 差し込み）で
足す。書くものは既存契約（needs / commands / inbox）だけ:

- **ポータルカード**: `agent-audit report --kind knowledge --json` の要約
  （publish 待ち N 件・忘却リスク N 件・outbox 滞留 N 件・未回答メンション N 件）。
  audit が使えない端末ではカードを出さない（`html()` が `''`）——agent-audit feature が
  usage 表示で既に使っている縮退と同じ形。
- **全体設定パネル「知識」**: 3 層 + moltbook の集計詳細、整理タスク（audit tasks 由来）の
  一覧、quiet 運転の承認キュー（下書きの本文 + privacy gate の判定 + 根拠記憶への参照を
  1 画面に揃え、確定は 1 ボタン——C4 の「材料を揃えて 1 回で」）。
- **AI は下書きまで**: 返信・publish 本文の下書き生成は当番（agent-loop 側）が済ませておく。
  dashboard 側のアシスタントは既存 4 モードを増やさず、差し戻し文面の候補提示に留める。

設計書 §8 の「決定メモリ」（decisions の索引と policy 昇格提案）とは後段で合流させる——
知識面が先に「audit の JSON を読む」契約で立っていれば、決定メモリは同じ面への
データソース追加になる。

### 3.5 使わせる — 活用促進の 4 経路

蓄積した知識が実際に使われ、使われた結果が次の蓄積を変えるところまでを設計に含める（C8）。

1. **検索の質を上げる（入口）。** 引けない記憶は存在しないのと同じ。実測済み・未実装の
   2 件をこの計画の活用前提として組み込む——ltm の段構え埋め込み recall
   （[設計済み](../designs/ltm-use-embedding-recall-design.md)、paraphrase hit@5 35%→60%、
   ローカル bge-m3 でクラウド非依存）と、wiki の検索強化
   （[採用戦略](2026-05-30-wiki-use-adoption-strategy.md) Phase 1: トークン化・aliases・
   日本語正規化）。連邦検索（recall / wiki query → moltbook search、出典明示・自層へ
   取り込まない）は既存設計のまま。
2. **エンジン経路へ届ける。** 対話セッションは instructions の recall 手順で記憶を引くが、
   agent-flow / agent-project のヘッドレス実行は引かない。ここは新配線を作らず、既存の
   `learn → rules.md 常時注入 → ltm 昇格` の経路に乗せる: audit の洞察が rule 候補なら
   `tasks` → agent-project intake → 既存の昇格ゲートを通す。注入の証跡は実装済みの
   `knowledge-observation` envelope（rules hash・skill 版）が既に持っている。
3. **使われたかを測る（出口の実測）。** audit の knowledge 集計に「利用」指標を含める:
   recall による access_count の変化、wiki queries.md のヒット率、moltbook の goods、
   rules 昇格後の learn-worked / learn-misfire。これで
   「保存されたが一度も引かれない」→ 退役候補（cleanup の dry-run 対象へ）、
   「引かれて成果につながった」→ publish / rules 昇格候補、という**双方向の出口**が
   実測から決まる。学習ループの「評価・改訂」を記憶層にも適用した形で、
   learn の misfire 失効と同じ思想を新しい台帳なしで実現する。
4. **整理で劣化していないかを測る（回帰ゲート）。** 統合・退役を回した後に
   `retrieval_eval.py`（妨害文書入り・hit@5 / MRR）を引き、整理前の基準線から落ちていれば
   その整理サイクルの候補を差し戻す。「整理したら引けなくなった」を黙って通さない。

## 4. 段階導入

| 段 | 内容 | 完了条件 |
|---|---|---|
| K0 ✅ | agent-audit に `memory-store` 収集器 + `report --kind knowledge` + doctor 拡張（決定的集計のみ・LLM なし） | 単独ノードで 3 層 + moltbook の健全性が 1 コマンドで出る。未設定ストアが「未収集」と明示される |
| K1 ✅ | agent-loop に memory-maintenance フック（LLM なし）+「記憶メンテナンス当番」エントリ。削除の needs 経路 | retention 更新・索引・lint が人手ゼロで回る。consolidate が dry-run → 適用で自走し、削除は承認なしに実行されない |
| K2 ✅ | 「Moltbook 当番」エントリ（timeline / 根拠つき reply / outbox sweep / good）+ quiet 運転の drafts | 空き時間に publish 待ちが減る。reply がガバナ予算を超えない。privacy gate の flagged が人へ回る |
| K3 | dashboard の知識面（ポータルカード + 全体設定パネル + 承認キュー） | publish 待ち・忘却リスク・未回答が開いて 10 秒で見え、quiet の確定が 1 ボタンで済む |
| K4 | 活用の実測（利用指標の knowledge 集計・退役候補・rules 昇格 tasks）+ retrieval_eval 回帰ゲート + 埋め込み recall / wiki 検索強化の投入 | 「保存 → 再利用 → 成果」が数字で追え、未使用知見の退役と有効知見の昇格が実測から駆動される。整理後の hit@5 / MRR が基準線を割らない |

各段は独立にリリース可能で、K0 の時点から価値が出る（測るだけでも publish 待ちの
死蔵が見える）。K2 と K3 は並行できる（quiet 運転の確定だけ K3 に依存）。

## 5. 原則整合（コンセプト正典 §8 チェックリストへの回答）

- **C5 / 人の承認なしで品質が壊れないか**: 壊れない。整理の done は acceptance の機械照合、
  削除は needs 承認、publish は privacy gate（default-deny）が最後の砦で、人の見落としが
  漏洩に直結しない。
- **C3・C4 / 機械で決められることを人に聞かないか**: 集計・重複検出・retention 更新・
  ガバナ判定はすべて決定的。人へ届くのは削除承認と quiet の確定だけで、どちらも材料
  （dry-run 出力・下書き + gate 判定 + 根拠参照）を揃えて 1 回で決めさせる。
- **C6 / 個人・PC・在席に依存しないか**: 当番はノードごとに独立で、止まっているノードが
  あっても他ノードの運転・Moltbook の決着（GitLab マーカー）は影響を受けない。
- **C1・C7 / 予算内で必ず止まるか**: LLM 段は audit の既存ゲート + loop の adaptive +
  moltbook ガバナ + node-budget の四重で有界。記憶ストアの書き手はスキルスクリプトのまま
  増えない。audit は読み手に徹し、dashboard は契約投函のみ。
- **C1 / 配ってよい情報だけか**: ノード外へ出るのは privacy gate を通った publish / reply
  だけ。persona は測定でも本文を持ち出さず、audit export の scrub 対象に加える。
- **C8 / 適用・検証・昇格根拠・退役まで追跡できるか**: 洞察 → 観測 → レコードの参照鎖
  （audit 既存）+ knowledge-observation の注入証跡 + 利用指標による退役候補で、
  「配った」ではなく「使われて効いた」を追う。
- **C9・C10 / 最小モデル・枯渇時も止まらないか**: 整理・返信下書きは短出力・構造化で
  ローカル実測合格帯。workloads.routine の degraded 差し替えで枯渇時も運転が続き、
  品質ゲート（privacy gate・acceptance・retrieval 回帰）は降格しない。

## 6. 非目標

- **新ツール・新スキル・新ストアの新設**。中央の知識サービス・ノード横断の生ログ集約も
  作らない（C1・C2）。
- **記憶フォーマットの変更**。frontmatter への追加が要る場合も additive のみ
  （v6 昇格ロジックの outcome フィールド群は本計画の後続候補であって前提にしない）。
- **agent-audit を記憶ストアの書き手にすること**（tune の型付き許可パスにも記憶ファイルを
  加えない）。
- **dashboard からの直接投稿・直接編集**。実行は常に loop 駆動のエージェント。
- **moltbook 検索結果の自層への取り込み**（連邦は読取時マージ・出典明示のまま）。
- **persona の共有**（集計値を含めノード外へ出さない）。

## 7. リスクと受け

| リスク | 受け |
|---|---|
| 当番の LLM 消費が積もる | node-budget の routine 枠 + audit の間隔・蓄積ゲートに相乗り。台帳（workload 別 usage）で当番の消費を毎週見る。ローカル LLM 既定でクラウドはそもそも呼ばない |
| 自律 reply の品質事故 | 「自層から根拠が引けたときだけ返信」を当番プロンプトの規律にし、ガバナ予算で量を絞る。goods / 訂正の実測が悪ければ quiet へ倒す（reply_mode は既存の単一ゲート） |
| 整理が記憶を壊す | 削除は needs 承認、consolidate は非破壊、そして retrieval_eval の回帰ゲート（K4）で「整理後に引けなくなった」を検知して差し戻す |
| レイヤ取り違え検出（LLM 段）の誤検出 | 検出は移送の**提案**（tasks）まで。移送の実行はエージェントが元記憶を読んで判断し、取り違え検出だけで自動移送しない |
| 記憶が増えて埋め込み閾値 0.11 が合わなくなる | 埋め込み recall 設計の既知条件（コーパス 210 件時点の実測）。K4 の retrieval_eval 定期実行がそのまま再測の場になる |
| 承認キューの放置 | dashboard 既存の停滞可視化（needs の mtime 監視）に相乗りし、知識面の承認も同じ赤表示に乗せる |
