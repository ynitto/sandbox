# agent-loop 設計書

> 最終更新: 2026-08-06（ループ拡張 4 設計・8 文書を本書へ統合）
> 実装: `tools/agent-loop/`（`agent_loop` パッケージ）。旧系統 `tools/kiro-loop/` は残置（付録 B）
> 関連: [agent-tools 改称方針](./agent-tools-rename-design.md) ／
> [slash プロパティ設計](./agent-loop-slash-property-design.md) ／
> 実装リファレンス `tools/agent-loop/DESIGN.md`（クラス構成・処理フローの詳細）
>
> 旧 `kiro-loop-{event-hook,agent-messaging,gitlab-webhook,adaptive-interval}-design.md` と、
> その agent-loop クローン 4 件（計 8 文書）は本書へ統合した。本文の名称は移行先の
> `agent-loop` に統一し、現行運用系である `kiro-loop` 系統との差分は付録 B にまとめる。

## TL;DR

agent-loop は、tmux 上にエージェント CLI（kiro-cli / claude 等）のセッションを常駐させ、YAML で定義したプロンプトを定期送信するデーモンです。素の agent-loop は「N 分ごとに固定文面を送る」だけの装置ですが、本書はそれを実運用に耐えさせるための 4 つの拡張の設計正典です。

1. **イベントフック（pull 型）** — スケジュール発火のたびに Python フックの `check()` を呼び、「今送るべきか・何を送るか」をデータ駆動で決める。**実装済み**。
2. **汎用 inbound Webhook（push 型）** — 外部システムからの HTTP POST を受け、フックの `handle(ctx)` でパースしてプロンプトに変換する。GitLab は一具体例で、コアは provider 非依存。**実装済み**。
3. **エージェント間メッセージング** — エージェントごとのファイルベース inbox に他エージェントがメッセージを投函し、受信側デーモンがプロンプトとして処理する。**実装済み**。
4. **動的インターバル（adaptive interval）** — 無風時はポーリング間隔を幾何級数的に伸ばし、イベント到来で即座に最短へ戻す。**未実装の提案**。

全体を貫く原則は 3 つです。第一に、拡張は既存ループへの**挿入だけ**で載せ、既存の送信・排他・死活監視の機構は変えません。第二に、送信元固有の知識（GitLab のヘッダ名や payload 構造）は**フックスクリプトに閉じ**、コアを汎用に保ちます。第三に、実際の tmux への送信は**既存スケジューラの背圧機構**（セッション準備・セマフォ）へ一本化し、HTTP スレッドや inbox 監視スレッドから直接送信しません。

読むべき人は、agent-loop / kiro-loop を運用する人、フックスクリプトを書く人、そして本設計を別フォークへ移植する人です。定期プロンプトの前にスラッシュコマンドを送る `slash` プロパティは、フォーク展開用に自己完結で書かれた[別文書](./agent-loop-slash-property-design.md)を参照してください。

## 背景と課題

素の agent-loop の駆動方式は `interval_minutes`（または `cron`）による固定スケジュールだけで、送る文面も YAML に書いた固定テキストだけでした。この単純さは美点ですが、実運用では 4 つの限界に当たります。

| 限界 | 症状 | 対応する拡張 |
|---|---|---|
| 文面もタイミングも固定 | 「新しい issue があるときだけ、その内容で」ができない | ①イベントフック |
| 外部イベントに即応できない | MR が開かれてから次のポーリングまで最大 1 周期待つ | ②Webhook |
| エージェント同士が会話できない | オーケストレータ→ワーカーの委譲手段が無い | ③メッセージング |
| 無風でも一定頻度で叩き続ける | 深夜・週末に GitLab API を 288 回/日 無駄叩き | ④動的インターバル |

### 目標

- スケジュール・フック・Webhook・inbox という複数の入力経路を、**1 つの送信経路**（スケジューラの dispatch）に合流させる
- 既存 YAML・既存フックの**後方互換を壊さない**（未指定なら従来挙動）
- GitLab / GitHub / 自作システムのどれが相手でも、コア実装を書き換えずにフックの差し替えで対応できる

### 非目標

- メッセージや Webhook の at-least-once 配送保証。キューはインメモリ（inbox のみファイル永続）で、取りこぼしを許容できないイベントはポーリング併用で冪等に取りに行く運用とします
- エージェント CLI 自体の改造。agent-loop はあくまで「テキストを tmux ペインへ送る」装置に徹します
- LLM による適応判断。動的インターバルの知能はヒューリスティクス（統計・状態機械）に限定します

## 全体像 — 4 つの入力経路

```
                    ┌────────────────────────── agent-loop デーモン ──────────────────────────┐
                    │                                                                          │
 固定スケジュール ──▶ PeriodicScheduler ─┬─ event_hook あり → check() を呼ぶ（pull・①）        │
                    │   (_run_loop)      │                                                     │
 外部システム ──HTTP─▶ WebhookServer ────┼─ handle(ctx) → テンプレート注入 → 外部キュー投函（②）│
                    │                    │                                                     │
 他エージェント ─file─▶ InboxWatcher ────┴─ メッセージ整形 → dispatch（③）                     │
                    │                                                                          │
                    │        ▼ いずれも ensure_session + セマフォ + _dispatch_prompt に合流     │
                    └───────────────────────────▼──────────────────────────────────────────────┘
                                        tmux ペイン（エージェント CLI）
```

| | ①イベントフック | ②Webhook | ③メッセージング | ④動的インターバル |
|--|--|--|--|--|
| 起点 | agent-loop（スケジュール発火） | 外部システム（HTTP） | 他エージェント（CLI） | agent-loop（発火結果の観測） |
| 方向 | pull | push | push（ファイル経由） | —（頻度制御） |
| フック契約 | `check() -> str \| None` | `handle(ctx) -> dict \| None` | なし（JSON スキーマ） | `check()` の dict 戻り値拡張 |
| 実行スレッド | scheduler | HTTP サーバ | InboxWatcher | scheduler |
| 永続性 | フック自身の状態ファイル | インメモリ deque（at-most-once） | ファイル（`.processed/` 移動まで未処理扱い） | 状態ファイルに永続化（案） |
| 状態 | 実装済み | 実装済み | 実装済み | 未実装の提案 |

## 主要な設計判断

### 1. 拡張は既存ループへの「挿入」だけで載せる

**判断**: どの拡張も、`_run_loop` への処理ブロック挿入と新規クラス・新規メソッドの追加だけで実装し、既存メソッドの中身は変更しない。

**文脈**: agent-loop は複数フォーク（kiro-loop 系・agent-loop 系、さらに外部フォーク）で並行に生きており、内部のメソッド名や行構成は一致しません。既存コードを書き換える設計は、フォークごとの差分に埋もれて移植できなくなります。

**トレードオフ**: 挿入点が増えると `_run_loop` が分岐の連なりになります。代わりに、各拡張が独立に有効化・無効化でき、フォークへの移植が「同じ挿入を自分の等価物に行う」作業に還元されます。Webhook 設計はこれを推し進め、host に求める能力を統合コントラクト（§機能 2）として抽象化しました。

**確信度**: 高い。event_hook・webhook・messaging の 3 拡張がこの方式で実装済みです。

### 2. pull と push を対称のフック契約にする

**判断**: pull 型は `check() -> str | None`（完成プロンプトを返す）、push 型は `handle(ctx) -> dict | None`（パース結果の辞書を返す）とし、どちらも「None なら何もしない」「モジュールは `importlib` + mtime キャッシュでロード」という同じ規約に載せる。

**文脈**: pull ではフックが自分でデータを取りに行くため文面まで組み立てられますが、push では受信 payload の解釈（フックの仕事）と文言（設定の仕事)を分けたい、という非対称があります。

**選択肢と却下理由**: push でも完成プロンプトを返させる案は、文言を変えるたびにフックスクリプトの編集が要り、パースと文言の責務が混ざるため却下。逆に pull を辞書返しに揃える案は、既存フックの後方互換を壊すため却下しました。

**トレードオフ**: 契約が 2 種類になりますが、`_load_hook_module`（mtime 監視・変更時のみ再ロード、複数スレッド対応のため `_hook_cache_lock` で保護）は共用できています。

**確信度**: 高い。

### 3. provider 固有の知識はフックに閉じ、コアは汎用に保つ

**判断**: イベント種別の判定（`X-Gitlab-Event` 等のヘッダ名）、HMAC 署名検証、payload 構造の知識はすべてフックスクリプト側に置く。コアが持つのは HTTP 受信・ルーティング・汎用共有シークレット照合・キュー・テンプレート注入だけ。

**文脈**: GitLab 前提で作ると、GitHub（`X-Hub-Signature-256`）や Slack（body 内の `type`）を足すたびにコアへ分岐が増えます。

**トレードオフ**: フック作者の責務は増えます（署名検証も自前）。代わりにコアは送信元を一切知らず、GitLab を参照しない最小フック（`ctx.payload` をそのまま返す `generic-webhook.py`）が同じコアで動くことが汎用性の検証になっています。

**確信度**: 高い。ユーザー確認済みの確定事項です（2026-07-10）。

### 4. 実送信は scheduler の背圧機構へ一本化する

**判断**: HTTP スレッド・InboxWatcher から tmux やセマフォを直接触らず、「キューへ積む／dispatch を依頼する」ところまでで手を放す。セッション準備・同時実行数制御・保留と再試行は scheduler 側の既存機構が一手に担う。

**文脈**: Webhook は GitLab のタイムアウト（遅い応答はリトライ嵐を招く）があるため受信スレッドをブロックできず、inbox は「処理できるまで保留」が必要です。どちらも自前で排他を持つと、セマフォの二重管理と競合が生まれます。

**選択肢と却下理由**: Webhook の宛先を InboxWatcher に向けて毎回エフェメラルなペインで処理する案は、「所定の固定セッションへ流す」という要件に合わず却下（ユーザー確認済み、2026-07-09）。

**トレードオフ**: 即時性は 1 ポーリングサイクル（1 秒）ぶん犠牲になりますが、fresh_context・cwd・セマフォ等のエントリ属性がどの経路でもそのまま効きます。

**確信度**: 高い。

### 5. 頻度の適応はヒューリスティクスだけで行い、観測のための追加リクエストを増やさない（提案）

**判断**: 動的インターバルの判断材料を「フックがどのみち取得したデータの副産物」（hit/miss/error、フックの状態ファイル、過去の発火履歴）に限定し、適応のために新たな API 呼び出しをしない。LLM も使わない。

**文脈**: 目的が「GitLab サーバ負荷の削減」なので、賢く決める処理自体が負荷を生んでは本末転倒です。

**確信度**: 中。設計としては完結していますが未実装で、実運用の裏付けがありません（§機能 4）。

---

## 機能 1: イベントフック（pull 型） — 実装済み

スケジュール（`interval_minutes` / `cron`）が発火したタイミングで Python フックの `check()` を呼び、タイミングと送信内容をスクリプトで制御します。スケジュールは廃止せず、「発火してよいかの最終判断」をフックに委ねる形です。

### フック契約

```python
def check() -> str | None:
    """スケジュール発火のたびに scheduler スレッドから呼ばれる。

    Returns:
        str  : エージェント CLI に送信するプロンプトテキスト（YAML の prompt を上書き）
        None : このサイクルをスキップ（何も送らない）
    """
```

- 引数なし。フック内の module-level 変数で状態を保持できます（scheduler の単一スレッドから呼ばれるため競合なし）
- `check` が存在しない・戻り値が不正・例外発生は、いずれも WARNING/ERROR ログの上スキップ（デーモンは止めない）
- モジュールは mtime 監視付きでロードし、変更時のみ再ロード

### フォールバック送信

「発火すべきイベントが無い場合に、フィルター条件に合致する対象をランダムに 1 件選んで送る」挙動を per-prompt の `event_hook_fallback: true` で有効化できます。本体はこのフラグを環境変数 `AGENT_LOOP_EVENT_HOOK_FALLBACK`（`1`/`0`）としてフックへ渡すだけで、フォールバックの実体は `check()` 内の自己判断です。`check()` のシグネチャは変わりません。

### 設定

```yaml
prompts:
  - name: "GitLab Issue ワーカー"
    prompt: |
      （省略可。check() が str を返した場合はそちらを優先）
    event_hook: ~/sandbox/tools/agent-loop/hooks/gitlab-issue-hook.py
    event_hook_fallback: false
    interval_minutes: 5
    enabled: true
```

`event_hook` は省略可で、省略時は従来どおり YAML の `prompt` をそのまま送信します。

### 同梱フック

`hooks/gitlab-issue-hook.py` / `hooks/gitlab-mr-hook.py` を同梱しています。状態ファイル（`~/.agents/hooks/gitlab-issue-state.json` 等）に `iid -> updated_at` を保存して新規・更新を検知し、ラベルに応じてプロンプト文面を切り替え、更新が無くフォールバック有効ならランダム送信します。

### キューイングは未実装

当初設計にあった「セマフォ上限到達時にプロンプトを `_queued_prompt` へ保持し、次サイクルでキュー優先処理する」機構は実装していません。スロット上限時は従来どおり次サイクルへ持ち越します。なお、フックがイベントを先に既読化してからスロット不足で破棄するとイベントが恒久消失するバグがあり、修正済みです（[2026-08-02 監査](../reviews/2026-08-02-agent-tools-family-bug-audit.md) L3）。

### 注意点

- `check()` は scheduler スレッドで実行されるため、長時間ブロックすると他エントリの発火が遅延します。ネットワーク呼び出しには短い timeout（同梱例では 15 秒）を設定すること
- `exec_module` はモジュールのトップレベルを実行します。副作用は `check()` 内に閉じること

---

## 機能 2: 汎用 inbound Webhook（push 型） — 実装済み

外部システムからの HTTP POST を受けてプロンプトに変換します。設計の主眼は「provider 非依存の汎用 inbound webhook コア」であり、GitLab はその上に載るフックの一具体例です。

### 責務分界

| レイヤ | 責務 | provider 依存 |
|--------|------|:---:|
| **コア**（`WebhookServer`） | HTTP 受信 / `<name>` ルーティング / 汎用共有シークレット検証 / ボディサイズ制限 / キュー投函 / テンプレート注入 | ✗ |
| **フック**（例: GitLab） | イベント種別の判定・フィルタ / 署名の独自検証 / payload パース → key-value 辞書 | ✓ |
| **テンプレート**（エントリの `prompt`） | 文言（辞書キーを `{key}` で参照） | ✗ |

### ルーティング

`POST /hooks/<name>` の `<name>` を、既存 `prompts` エントリの `name`（URL-safe 化後）に一致させて解決します。つまり **webhook の宛先 = 既存の名前付きセッション**です。ルート表は持たず、毎リクエスト `scheduler.resolve_webhook_route(name)` で最新エントリから引くため、設定リロード後もルートが陳腐化しません。一致しなければ `404`。

### フック契約と `WebhookContext`

```python
def handle(ctx) -> dict | None:
    """webhook 受信時に HTTP サーバスレッドから呼ばれる。

    Returns:
        dict : プロンプトテンプレートへ注入する key-value パラメータ
        None : 無視（何も送らず 200 を返す）
    """
```

`ctx` は生に近いコンテキストで、`name`（ルート名）/ `method` / `headers`（小文字キーの全ヘッダ）/ `query` / `raw`（生ボディ）/ `payload`（best-effort の JSON パース結果、非 JSON なら `{}`）を持ちます。`ctx.event` のような provider 固有の属性は意図的に持たせず、GitLab フックなら `ctx.headers.get("x-gitlab-event")` を自分で読みます。

`ThreadingHTTPServer` のため `handle` は複数スレッドから同時に呼ばれ得ます。状態を持つならフック側でロックするか、ステートレスに設計してください。

### テンプレート注入

フックが返した辞書は、基本キー `name` を補完したうえでエントリの `prompt` テンプレートへ `str.format_map(_SafeDict(params))` で注入します。`_SafeDict` は未定義キーを `{key}` のまま残すため、テンプレートの誤記やフックの欠損キーで `KeyError` クラッシュしません。テンプレート本文に `{ }` を書く場合は `{{ }}` でエスケープします。

`webhook.hook` を省略したエントリは、フック呼び出しをスキップして受信 JSON ボディをそのまま注入パラメータに使う**パススルー**になります。provider 固有の加工が不要な単純通知にはフックすら不要です。

### 受信フロー

```
POST /hooks/<name>
  ├─ ① メソッド判定       GET（/hooks/_health 以外）→ 405
  ├─ ② ルート解決         resolve_webhook_route(name) → 無し → 404
  ├─ ③ シークレット検証    secret_header の値 ≠ secret → 401（未設定なら素通り + 起動時 WARNING）
  ├─ ④ ボディ読取         サイズ超過 → 413 / JSON パースは best-effort
  ├─ ⑤ handle(ctx)        None → 200（ignored）。フック例外・handle 不在も 200 で握る
  ├─ ⑥ テンプレート注入    prompt.format_map(_SafeDict(params))
  ├─ ⑦ 外部キューへ投函    enqueue_external(name, prompt_text)
  └─ ⑧ 202 Accepted を即返す（tmux への送信完了は待たない）
```

フック例外を `500` でなく `200` で握るのは、GitLab が 5xx にリトライを重ねて「毎回同じ例外で嵐」になるのを避けるためです（確定事項）。

### 外部キューとドレイン

キューはエントリ dict の中ではなく、**scheduler が `name` をキーに独立保有**する bounded deque（`_external_queues`、上限超過は古いものから破棄 + 警告）です。エントリ dict に持たせると、設定リロード（エントリ全置換）のたびに未処理 webhook が捨てられ、`_run_loop` の浅いコピーとも競合するためです。enqueue（HTTP スレッド）と drain（scheduler スレッド）は同一ロック下でのみ deque を操作します。

実 dispatch は `_run_loop` 先頭の `_drain_external_one()`（1 サイクル 1 件）が行い、セッション未準備・スロット上限なら `appendleft` で積み直します。

webhook 専用エントリはスケジュール（cron / interval）無しで定義でき、その場合 `next_run_at = math.inf` の sentinel でスケジュール発火パスから外れます。発火するのはキュードレイン経由のみです。event_hook との併用（webhook + interval）も可能です。

### 設定

```yaml
# グローバル
webhook:
  enabled: true
  host: 127.0.0.1          # 既定 localhost。外部公開はリバースプロキシ経由を推奨
  port: 8899               # enabled 時は明示必須（複数インスタンスの衝突防止）
  path_prefix: /hooks
  secret: ""               # 汎用共有シークレット。空なら検証せず起動時 WARNING
  secret_header: X-Gitlab-Token   # 照合するヘッダ名。未指定時の既定も X-Gitlab-Token
  max_body_bytes: 1048576

# エントリごと
prompts:
  - name: mr-reviewer        # ← POST /hooks/mr-reviewer に対応
    prompt: |
      [MR webhook] {project} !{mr_iid}（{action}）
      タイトル: {title}
      URL: {url}
      この MR をレビューして、指摘があれば MR にコメントしてください。
    webhook:
      hook: ~/sandbox/tools/agent-loop/hooks/gitlab-mr-webhook.py
      secret: ""             # ルート個別（省略時グローバル）
      secret_header: ""      # ルート個別（省略時グローバル）
```

イベント種別のフィルタ（「MR だけ」等）はコア設定に持ちません。provider 固有なので、フックが `ctx.headers` を見て対象外を `None` で弾きます。

### セキュリティ

- bind 既定は `127.0.0.1`。SaaS（gitlab.com 等）からの受信にはトンネルかリバースプロキシ（TLS 終端）が必須で、設計上は「公開・TLS は前段に任せる」割り切りです
- 共有シークレットの比較は `hmac.compare_digest`（timing-safe）
- HMAC 署名方式（GitHub の `X-Hub-Signature-256` 等）は単純照合では守れないため、フックが `ctx.raw` から署名を再計算して検証します
- ボディサイズ上限（既定 1MB）で簡易 DoS 緩和。ヘルスチェックは `GET /hooks/_health`

### ライフサイクルと耐久性

`main()` で `webhook.enabled` かつ `port > 0` のとき起動し、bind 失敗（`address in use` 等）は WARNING で本体継続します。停止は `_cleanup` / `_signal_handler` から `stop()`（`shutdown()` + `server_close()`）を配線済みです。

キューはインメモリのみで、再起動・クラッシュで未処理 webhook は失われます。送信元は `202` を受けた時点で再送しないため実質 **at-most-once** です。取りこぼせないイベントは event_hook（ポーリング）併用で冪等に取りに行く運用を推奨します。

### 移植コントラクト（フォーク向け）

本設計はフォーク非依存を目標にしており、webhook 機能が host に求めるのは次の能力だけです。実現方法（メソッド名・スレッド構成）はフォーク任せで、C1〜C5 の等価物が無いフォークは先にそこを用意します。

| # | 能力 | agent-loop での実体 |
|---|------|---------------------|
| C1 | 常駐ループの存在（処理を差し込める） | `PeriodicScheduler._run_loop` |
| C2 | 名前付き送信先の解決 | エントリ `name`/`id` → `SessionManager` ペイン |
| C3 | プロンプト送信 API（session 準備・排他制御込み） | `ensure_session` + `_acquire_slot` + `_dispatch_prompt` |
| C4 | 設定の正規化フック（`webhook` フィールドを通せる） | `_set_entries` |
| C5 | 起動/停止の配線 | `main()` / `_cleanup()` |
| C6 | モジュール動的ロード（任意） | `_load_hook_module` |

移植時の注意: ペインを遅延起動するフォークでは、初回 webhook が session 準備待ちで一度保留されます（ドレインが積み直すので消失はしません）。agent-loop は設定読み込み時にペインを先行起動するため、この問題がありません。

### 既知の課題

- エントリの disable / リネーム / 削除でキューが宙に浮く場合、drain 時に「対応エントリ不在なら破棄 + 警告」するのが設計意図ですが**未実装**です（`agent_loop/scheduler.py` のドレイン処理）。bounded deque なのでメモリは有界ですが、キーが事実上のリークとして残ります
- agent-loop 系統の E2E テストは未整備です（kiro-loop 系統には実 HTTP・SessionManager スタブによる 22 ケース通過の記録あり。付録 B）

---

## 機能 3: エージェント間メッセージング — 実装済み

agent-loop を使ったエージェント間の非同期メッセージングです。各エージェントは名前付きの受信ボックス（inbox）を持ち、他エージェントから投函されたメッセージをエージェント CLI へのプロンプトとして処理します。

```
orchestrator                             worker1
┌───────────────┐                      ┌───────────────────┐
│ agent-loop     │  agent-loop msg      │ agent-loop         │
│ (agent_name:   │ ───────────────────▶│ InboxWatcher       │
│  orchestrator) │  ~/.kiro/agents/     │ (agent_name:       │
└───────────────┘   worker1/inbox/      │  worker1)          │
        ▲                               │  ↓ CLI へ prompt   │
        │ agent-loop msg --to orchestrator                   │
        └────────────────────────────────────────────────────┘
```

既存の `send` サブコマンド（tmux セッションへ送信後すぐ返る）とは補完関係で、`send` はデバッグ・手動操作、`msg` は非同期のエージェント間通信に使い分けます。

### ファイル構造とメッセージスキーマ

inbox は `~/.kiro/agents/<agent_name>/inbox/` 配下のファイルで、処理済みは `.processed/` へ移動します。**このパスは意図的に kiro-loop 系統と共有**しており、新旧デーモンをまたいだメッセージ交換ができます（付録 B）。

```json
{
  "id": "a1b2c3d4…",
  "from": "orchestrator",
  "to": "worker1",
  "created_at": 1716460000.0,
  "subject": "feature X の実装依頼",
  "body": "src/feature_x.py を実装してください。…",
  "reply_to": "9f8e7d6c…",
  "correlation_id": "conv-2026-05-23-001",
  "cwd": "/home/user/projects/myapp"
}
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|:---:|------|
| `id` | str | ✅ | メッセージ固有 ID（UUIDv4 hex） |
| `from` | str | ✅ | 送信元エージェント名 |
| `to` | str | ✅ | 宛先エージェント名 |
| `created_at` | float | ✅ | 作成日時（Unix timestamp） |
| `subject` | str | — | 件名 |
| `body` | str | ✅ | 本文（CLI へ渡すプロンプトのベース） |
| `reply_to` | str | — | 返信元**メッセージ ID**（`--reply-to` 未指定時は `null`。エージェント名へのフォールバックはしない） |
| `correlation_id` | str | — | 会話スレッド追跡 ID |
| `cwd` | str | — | 送信元の作業ディレクトリ |

`reply_to` はメッセージ ID 専用です。返信先のエージェント名には別フィールドの `from` を使います（受信側のプロンプト整形は `from` で返信コマンドを組み立てる）。かつて kiro-loop 実装が未指定時にエージェント名へフォールバックする非互換を持っていましたが、解消済みです（[2026-08-02 監査](../reviews/2026-08-02-agent-tools-family-bug-audit.md) D2）。

### InboxWatcher

グローバル設定 `agent_name` を設定したデーモンだけが InboxWatcher スレッドを起こし、`inbox_poll_seconds`（既定 5 秒）ごとに inbox をポーリングします。各メッセージは「セッション準備 → セマフォ取得 → 送信」を試み、失敗ならファイルを保持したまま次のポーリングで再試行します。**`.processed/` へ移動するまで処理済みとみなさない**のが保留・再試行の要です。

受信メッセージは次の形式でプロンプト化されます。

```
[エージェント {from} からのメッセージ]
件名: {subject}

{body}

---
返信する場合: agent-loop msg --to {from} --reply-to "{id}" "返答内容"
```

### CLI

```bash
# 送信（body がファイルとして存在すればその内容を本文に使う）
agent-loop msg --to worker1 --from orchestrator --subject "実装依頼" "feature_x.py を実装してください"

# 返信
agent-loop msg --to orchestrator --from worker1 --reply-to "a1b2c3d4…" "実装が完了しました"

# 登録済みエージェントと inbox 状態の一覧
agent-loop agents
```

### 今後の拡張（未実装のロードマップ）

- **P1**: メッセージ優先度、TTL（有効期限）、配送確認（ACK）、`inbox --watch`、broadcast（`--to "*"`）、ペイロード添付
- **P2**: 名前解決レジストリ（リモートエージェント対応）、SQLite 等によるメッセージストア、WebSocket/gRPC への移行

---

## 機能 4: 動的インターバル（adaptive interval） — 未実装の提案

> 本節は設計案です。`tools/agent-loop/` にも `tools/kiro-loop/` にも `adaptive` 関連のコードは存在しません。

固定インターバルは、活発時には反応が遅く（5 分固定なら最悪 5 分待ち）、無風時には無駄叩き（288 回/日）になります。この提案は、発火結果の観測から次の発火タイミングを動的に決めます。

### 二層構成

どちらか一方だけでも成立し、併用すると精度が上がります。

| 層 | 決定主体 | 使う情報 | フック改変 |
|---|---|---|---|
| **Layer 1: コア適応** | scheduler | `check()` の hit/miss（送信したか否か）だけ | 不要 |
| **Layer 2: フック明示** | `check()` | 既に取得済みの GitLab データ（backlog 数・最終更新時刻・ラベル） | 戻り値を dict へ拡張 |

### 適応アルゴリズム（Layer 1）

「miss で乗算増加・hit で即リセット」を採用します。無風時は幾何級数的に伸ばしつつ、イベント到来時は 1 発で最小へ戻すことで取りこぼし（stall）を防ぎます。

| 結果 | 判定 | インターバル更新 | 意図 |
|---|---|---|---|
| **hit** | プロンプトを実際に送信できた | `min_interval` へ即リセット | 活発 → 最速で追従 |
| **miss** | 送るべきものが無かった | `× backoff_factor`（`max_interval` で頭打ち） | 無風 → 幾何級数的に間引き |
| **error** | GitLab 不達・タイムアウト | 据え置き + `retry_interval` で短時間リトライ | 障害を無風と誤認して max へ飛ばさない |

error を独立クラスにするのが要です。「更新なし」と「ネットワークエラー」を両方 miss に潰すと、GitLab が数分落ちただけでインターバルが max（例 120 分）まで膨らみ、復帰後の本物のイベントを 2 時間見逃します。

次回発火時刻には `±jitter`（既定 ±10%）を掛け、複数デーモンの同時ポーリング（thundering herd）を分散します。適応状態はエントリ単位のファイル（`~/.agents/loop-adaptive/<entry-id>.json`）へ永続化し、再起動でインターバルが min へリセットされて無風の深夜に叩き直すのを防ぎます。

### 設定案

```yaml
prompts:
  - name: "GitLab Issue ワーカー (event_hook)"
    event_hook: ~/sandbox/tools/agent-loop/hooks/gitlab-issue-hook.py
    interval_minutes: 5          # adaptive 有効時は初期値として使う
    adaptive:
      enabled: true
      min_interval_minutes: 2    # hit 時に戻る下限（既定: interval_minutes）
      max_interval_minutes: 120  # miss バックオフの上限
      backoff_factor: 1.6
      retry_interval_minutes: 1  # error 時の短時間リトライ
      jitter: 0.1
```

`adaptive` 未指定のエントリと `cron` エントリ（固定スケジュールが意味を持つ）は完全に従来挙動です。バリデーション（`min < max`、`backoff_factor > 1.0`、`min >= 1`）に落ちたエントリは WARNING の上、固定インターバルへフォールバックします。

### フック戻り値の dict 拡張（Layer 2）

`check()` の戻り値に、後方互換の dict 形式を追加します。

```python
def check() -> str | None | dict:
    # 従来:  str → 送信（hit） / None → スキップ（miss）
    # 追加:  {"prompt": str | None,
    #         "status": "hit" | "miss" | "error",   # 省略時は prompt から推定
    #         "next_interval_minutes": float | None} # 明示指定。コア適応より優先（min〜max にクランプ）
```

これで表現できるようになるのは次の 3 つです。

- **障害の申告**: `{"prompt": None, "status": "error"}` — バックオフさせず短時間リトライ
- **フォールバック送信の分離**: `{"prompt": <fallback>, "status": "miss"}` — 「プロンプトは送るがポーリング頻度は上げない」。これが無いとフォールバック有効エントリは毎サイクル hit 扱いになりバックオフが効きません
- **データに基づく明示指定**: 既に取得済みの issues から「backlog 空なら 120 分」「critical ラベルありなら 3 分」等を追加リクエストなしで返せます

### 詰まらせないための不変条件

1. hit で即 min 復帰（バックオフ中でもイベント 1 発で最速へ）
2. error はバックオフしない（短時間リトライ）
3. `min >= 1 分`・`min < max`・`backoff > 1.0` をバリデーションで保証
4. スロット busy 由来の延期（+30 秒）と適応バックオフを二重計上しない（適応は miss パスのみ）
5. jitter で複数デーモンを分散
6. cron エントリは不可侵
7. 再起動時の復帰ガード（前回から時間が経ちすぎていれば 1 段だけ縮めて安全側へ）は任意

### 期待効果

`min=2, max=120, backoff=1.6` の event_hook エントリ 1 本を無風の週末に走らせた場合、1 日あたりのフックのリクエスト数は固定 5 分の **288 回**に対し**約 20〜30 回**（約 1/10）。イベント到来時は hit で即 2 分へ戻るため、平時の反応速度はむしろ向上します。

### 段階導入

(1) コア AIMD だけ有効化 → (2) 同梱フックを dict 戻り値へ更新 → (3) EWMA による max の動的クランプ等の高度化。既存フック（`str | None`）は無改変で動き、dict 戻り値はオプトインです。

---

## slash プロパティ — 実装済み・別文書

定期プロンプトの本文より前にスラッシュコマンド（`/name` 形式）を独立送信として前置する `slash` プロパティは、fork 先へ単体で展開できるよう自己完結で書かれた [`agent-loop-slash-property-design.md`](./agent-loop-slash-property-design.md) を正とします。送信順は `/clear`（fresh_context）→ `slash` 要素を宣言順 → 本文で、実装は `agent_loop/scheduler.py`、テストは `test/test_slash_property.py` にあります。

---

## 検証状況

| 機能 | agent-loop 系統 | kiro-loop 系統 |
|---|---|---|
| イベントフック | 実装済み。`test/test_event_hook.py` | 実装済み |
| Webhook | 実装済み。E2E 未整備 | 実装済み。E2E 22 ケース通過の記録（実 HTTP・SessionManager スタブ） |
| メッセージング | 実装済み。`test/test_inbox_dispatch.py` | 実装済み。`test/test_messaging.py` |
| 動的インターバル | 未実装 | 未実装 |
| slash | 実装済み。`test/test_slash_property.py` | 未実装（移植ガイドは別文書 §3） |

---

## 付録

### A. 実装後に更新すべきドキュメント

新しい拡張（動的インターバル等）を実装する際は、本書の該当節の「未実装」表記に加えて次を更新します。

- `tools/agent-loop/DESIGN.md` — クラス構成・`_run_loop` フロー・「新しいプロンプトオプションを追加する」節
- `tools/agent-loop/agent-loop.yaml.example` — 設定サンプル
- `tools/agent-loop/README.md` — 利用者向け概要
- 同梱フックの docstring — 契約変更（dict 戻り値等）がある場合

### B. kiro-loop 系統との差分と移行

`kiro-loop → agent-loop` は[クローン移行方針](./agent-tools-rename-design.md)に基づく改称系統で、クローン移行は実施済み、旧系統 `tools/kiro-loop/` は**削除せず残置**します（改称方針 §3 の維持リスト）。設計の正典は本書（agent-loop 名称）に一本化し、kiro-loop 側の読者は次の対応で読み替えます。

| 項目 | agent-loop | kiro-loop |
|---|---|---|
| 実装形態 | `agent_loop/` パッケージ（`scheduler.py` / `webhook.py` / `inbox.py` / `sendcmd.py` 等に分割） | 単一スクリプト `kiro-loop.py` |
| 設定・状態ホーム | `~/.agents/`（`agent-loop.yaml`, `agent-loop.log`, `hooks/`） | `~/.kiro/`（`kiro-loop.yaml`, `kiro-loop.log`, `hooks/`） |
| フォールバック環境変数 | `AGENT_LOOP_EVENT_HOOK_FALLBACK` | `KIRO_LOOP_EVENT_HOOK_FALLBACK` |
| メッセージング inbox | `~/.kiro/agents/<name>/inbox/`（**共有**） | 同左（**共有**） |
| 適応状態ファイル（案） | `~/.agents/loop-adaptive/` | `~/.kiro/loop-adaptive/` |

inbox を共有しているため、新旧系統のデーモンは相互にメッセージを送り合えます。逆に言えば、メッセージスキーマ（特に `reply_to` の意味）の変更は両系統に同時に効く相互運用上の変更点であり、片側だけの改変は過去に非互換バグを生みました（2026-08-02 監査 D2、解消済み）。

新機能・設計更新は agent-loop 系統へ寄せます。kiro-loop 側にだけある資産（webhook の E2E テスト群）は、必要になった時点で agent-loop 側へ移植します。

### C. 統合した旧文書

以下の 8 文書（作成日はいずれも kiro-loop 版）を 2026-08-06 に本書へ統合し、削除した。

| 旧文書（kiro-loop 版 / agent-loop クローン版） | 作成日 | 本書の節 |
|---|---|---|
| `kiro-loop-event-hook-design.md` / `agent-loop-event-hook-design.md` | 2026-05-12 | 機能 1 |
| `kiro-loop-agent-messaging-design.md` / `agent-loop-agent-messaging-design.md` | 2026-05-23 | 機能 3 |
| `kiro-loop-gitlab-webhook-design.md` / `agent-loop-gitlab-webhook-design.md` | 2026-07-09 | 機能 2 |
| `kiro-loop-adaptive-interval-design.md` / `agent-loop-adaptive-interval-design.md` | 2026-07-05 | 機能 4 |

統合にあたり、実装検証で追記されていた確定事項（フック例外は 200 で握る・`secret_header` の既定値・パススルー挙動・`reply_to` の意味の統一 等）は agent-loop クローン版の記述を正として採り、コードの行番号参照（モジュール分割で陳腐化）と実装当時の変更量見積り表は落とした。
