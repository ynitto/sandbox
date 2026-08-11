# agent-loop 設計書

> 最終更新: 2026-08-09（`slash` 統合、Phase 1 / Phase 2・`agent-tuning` の実装を反映）
> 実装: `tools/agent-loop/`（`agent_loop` パッケージ）。旧系統 `tools/kiro-loop/` は退役済み（付録 B）
> 関連: [agent-tools 改称方針](./agent-tools-rename-design.md) ／
> [段階的機能拡張](../plans/2026-08-08-agent-loop-phased-enhancement-design.md) ／
> 実装リファレンス `tools/agent-loop/DESIGN.md`（クラス構成・処理フローの詳細）
>
> 旧 `kiro-loop-{event-hook,agent-messaging,gitlab-webhook,adaptive-interval}-design.md` と、
> その agent-loop クローン 4 件、および `agent-loop-slash-property-design.md`（計 9 文書）は本書へ統合した。本文の名称は移行先の
> `agent-loop` に統一し、退役した `kiro-loop` 系統との差分は移行記録として付録 B に残す。

## TL;DR

agent-loop は、YAML で定義したプロンプトを定期実行するデーモンです。既定では tmux 上にエージェント CLI（kiro-cli / claude 等）のセッションを常駐させて送信し、対話ペインを持たない CLI や使い捨て実行では実行のたびに subprocess を起こします。本書は、固定送信を実運用に耐えさせる 6 つの機能と、すべての入力を同じ配送判定へ通す実行基盤の設計正典です。

1. **イベントフック（pull 型）** — スケジュール発火のたびに Python フックの `check()` を呼び、「今送るべきか・何を送るか」をデータ駆動で決める。**実装済み**。
2. **汎用 inbound Webhook（push 型）** — 外部システムからの HTTP POST を受け、フックの `handle(ctx)` でパースしてプロンプトに変換する。GitLab は一具体例で、コアは provider 非依存。**実装済み**。
3. **エージェント間メッセージング** — エージェントごとのファイルベース inbox に他エージェントがメッセージを投函し、受信側デーモンがプロンプトとして処理する。**実装済み**。
4. **動的インターバル（adaptive interval）** — 無風時はポーリング間隔を幾何級数的に伸ばし、イベント到来で即座に最短へ戻す。**実装済み**。
5. **エージェント CLI の差し替え** — 駆動する CLI（kiro-cli / claude / codex / aider 等）を `agents/<name>.json` の共通契約で、全体設定と定期プロンプトごとに差し替える。待機状態の判定方法が CLI ごとに違う点は契約側の宣言（`ready_pattern` / `busy_pattern` / `idle_quiet_sec`）で吸収し、対話ペインを持たない CLI は headless 経路で動かす。ツールループを内蔵しない CLI（`headless_autonomy: single-shot`）へは限定ツール契約でツール実行を供給し、done は受入条件（`acceptance`）で機械検証する。**実装済み**（判定層と tmux 可視化を除く）。
6. **`slash` プロパティ** — 本文の前に CLI コマンドを独立送信し、CLI ごとの行頭記号へ送信直前に変換する。**実装済み**。

全体を貫く原則は 3 つです。第一に、公開 YAML・フック・inbox の契約を保ったまま、schedule / event hook / webhook / inbox / CLI send を **PeriodicScheduler の dispatch gate** へ一本化します。第二に、送信元固有の知識（GitLab のヘッダ名や payload 構造）は**フックスクリプトに閉じ**、コアを汎用に保ちます。第三に、実際の tmux への送信はスケジューラの背圧機構（lifecycle・preflight・セッション準備・セマフォ・ready 判定）だけを通し、HTTP スレッドや inbox 監視スレッドから直接送信しません。

読むべき人は、agent-loop を運用する人、フックスクリプトを書く人、そして本設計を別フォークへ移植する人です。利用者向けの設定例は `tools/agent-loop/README.md`、内部クラスと処理フローは `tools/agent-loop/DESIGN.md` を参照してください。

## 背景と課題

素の agent-loop の駆動方式は `interval_minutes`（または `cron`）による固定スケジュールだけで、送る文面も YAML に書いた固定テキストだけでした。この単純さは美点ですが、実運用では 4 つの限界に当たります。

| 限界 | 症状 | 対応する拡張 |
|---|---|---|
| 文面もタイミングも固定 | 「新しい issue があるときだけ、その内容で」ができない | ①イベントフック |
| 外部イベントに即応できない | MR が開かれてから次のポーリングまで最大 1 周期待つ | ②Webhook |
| エージェント同士が会話できない | オーケストレータ→ワーカーの委譲手段が無い | ③メッセージング |
| 無風でも一定頻度で叩き続ける | 深夜・週末に GitLab API を 288 回/日 無駄叩き | ④動的インターバル |

### 目標

- スケジュール・フック・Webhook・inbox・CLI send を、**1 つの送信経路**（スケジューラの dispatch）に合流させる
- 既存 YAML・既存フックの**後方互換を壊さない**（未指定なら従来挙動）
- GitLab / GitHub / 自作システムのどれが相手でも、コア実装を書き換えずにフックの差し替えで対応できる

### 非目標

- メッセージや Webhook の at-least-once 配送保証。キューはインメモリ（inbox のみファイル永続）で、取りこぼしを許容できないイベントはポーリング併用で冪等に取りに行く運用とします
- エージェント CLI 自体の改造。agent-loop はあくまで「テキストを tmux ペインへ送る」装置に徹します
- LLM による適応判断。動的インターバルの知能はヒューリスティクス（統計・状態機械）に限定します

## 全体像 — 5 つの入力経路と 1 つの dispatch gate

```
                    ┌──────────────────────── agent-loop デーモン ────────────────────────┐
 固定スケジュール ──▶ schedule prompt                                                     │
 event hook ─────────▶ schedule 発火 → check()（pull・①）                                 │
 外部システム ──HTTP─▶ WebhookServer ── handle(ctx) → 外部 deque（②）                     │
 他エージェント ─file─▶ InboxWatcher ── JSON file（③）                                   │
 CLI send ──────file─▶ send-requests ── atomic claim                                      │
                    │                   ▼                                                  │
                    │       request ID 付き共通 dispatch queue                            │
                    │                   ▼                                                  │
                    │ lifecycle → preflight → session → slot → ready → _dispatch_prompt   │
                    └───────────────────▼──────────────────────────────────────────────────┘
                                    tmux ペイン（エージェント CLI）
```

| | ①イベントフック | ②Webhook | ③メッセージング | ④動的インターバル |
|--|--|--|--|--|
| 起点 | agent-loop（スケジュール発火） | 外部システム（HTTP） | 他エージェント（CLI） | agent-loop（発火結果の観測） |
| 方向 | pull | push | push（ファイル経由） | —（頻度制御） |
| フック契約 | `check(config?) -> str \| dict \| None` | `handle(ctx) -> dict \| None` | なし（JSON スキーマ） | scheduler は activity / idle を観測（error は未接続） |
| 実行スレッド | timeout 付き hook thread | HTTP サーバ | InboxWatcher | scheduler |
| 永続性 | フック自身の状態ファイル | インメモリ deque（at-most-once） | ファイル（`.processed/` 移動まで未処理扱い） | `~/.agents/loop-adaptive/` |
| 状態 | 実装済み | 実装済み | 実装済み | 実装済み |

## 主要な設計判断

### 1. 公開契約を保ち、内部配送を 1 つの dispatch gate に畳む

**判断**: schedule / event hook / webhook / inbox / CLI send を共通の dispatch request に正規化し、`PeriodicScheduler` を唯一の配送判定箇所にする。既存の YAML・フック・ファイル契約は維持する。

**文脈**: 入力元ごとに busy・slot・lifecycle の判定が分かれていたため、要求消失と完了誤判定の原因になっていました。Phase 1 は公開面を変えず、内部だけを共通化しました。

**選択肢と却下理由**: 呼び出し箇所ごとの個別強化は判定の重複を残し、汎用 workflow engine への全面改造は agent-loop の責務を越えるため却下。

**トレードオフ**: daemon が request をメモリキューへ受理した直後の crash には永続再送せず、既存の at-most-once 境界を維持します。

**確信度**: 高い。Phase 1 / Phase 2 と入力経路別の回帰テストで固定しています。

### 2. pull と push を対称のフック契約にする

**判断**: pull 型は `check(config?) -> str | dict | None`（完成プロンプト、または `prompt` / `cwd` / `vars`）、push 型は `handle(ctx) -> dict | None`（パース結果の辞書）とし、どちらも「None なら何もしない」「モジュールは `importlib` + mtime キャッシュでロード」という同じ規約に載せる。

**文脈**: pull ではフックが自分でデータを取りに行くため文面まで組み立てられますが、push では受信 payload の解釈（フックの仕事）と文言（設定の仕事)を分けたい、という非対称があります。

**選択肢と却下理由**: push でも完成プロンプトを返させる案は、文言を変えるたびにフックスクリプトの編集が要り、パースと文言の責務が混ざるため却下。pull の dict は既存の文字列戻り値を残した後方互換の追加に限定しました。

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

### 5. 頻度の適応はヒューリスティクスだけで行い、観測のための追加リクエストを増やさない

**判断**: 動的インターバルの判断材料を「通常の dispatch で得られる送信成功・スキップと過去の発火履歴」に限定し、適応のために新たな API 呼び出しをしない。LLM も使わない。

**文脈**: 目的が「GitLab サーバ負荷の削減」なので、賢く決める処理自体が負荷を生んでは本末転倒です。

**確信度**: 高い。明示 opt-in・状態永続化・cron 除外を実装し、単体テストで状態遷移を固定しています（§機能 4）。

---

## 機能 1: イベントフック（pull 型） — 実装済み

スケジュール（`interval_minutes` / `cron`）が発火したタイミングで Python フックの `check()` を呼び、タイミングと送信内容をスクリプトで制御します。スケジュールは廃止せず、「発火してよいかの最終判断」をフックに委ねる形です。

### フック契約

```python
def check(config=None) -> str | dict | None:
    """スケジュール発火のたびに timeout 付き worker thread から呼ばれる。

    Returns:
        str  : エージェント CLI に送信する完成プロンプト
        dict : {"prompt": str, "cwd"?: str, "vars"?: dict}
        None : このサイクルをスキップ
    """

def ack() -> None:
    """任意。tmux への送信成功後だけ呼ばれる。"""
```

- 引数なしの既存 `check()` はそのまま有効。1 引数を受ける場合はエントリ名・ID・fallback・個別設定・cwd・workspace を渡します
- dict の `cwd` は実在ディレクトリだけを受理し、`vars` は `prompt.format_map` へ渡します
- `check` が存在しない・戻り値が不正・例外発生は、いずれも WARNING/ERROR ログの上スキップ（デーモンは止めない）
- 30 秒で timeout したフックは完了まで隔離し、同じフックの thread を増殖させません
- モジュールは mtime 監視付きでロードし、変更時のみ再ロード

### フォールバック送信

「新規イベントが無ければ未完了イベントを cooldown 後に replay し、それも無ければ候補を 1 件選ぶ」挙動を per-prompt の `event_hook_fallback: true` で有効化できます。コアはフラグと設定を渡すだけで、優先順位と provider 固有の選択は同梱フックに閉じます。イベントの既読化は `check()` ではなく、送信成功後の `ack()` で確定します。

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

### 配送保持と ACK

schedule はエントリごとに最大 1 件を保留します。発火を queue へ受理した時点で `next_run_at` を進め、busy・slot 上限では同じ request を保留し、次の発火は 1 件へ coalesce します。event hook は tmux 送信成功後だけ `ack()` を呼ぶため、受付前の延期でイベントを既読化しません。daemon がメモリキューへ受理した後の crash まで永続再送する保証は持ちません。

### 注意点

- `check()` は 30 秒で scheduler の待機を打ち切りますが、Python thread 自体は強制終了できません。ネットワーク呼び出しにはそれより短い timeout を設定すること
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

`_drain_external_to_pending()` は 1 tick あたりエントリごとに 1 件を共通 dispatch queue へ移し、以後は他の入力と同じ lifecycle・preflight・slot・ready 判定を通します。queue へ受理できなければ deque の先頭へ戻します。

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
| C3 | プロンプト送信 API（session 準備・排他制御込み） | `ensure_session` + `_try_acquire_slot` + `_dispatch_prompt` |
| C4 | 設定の正規化フック（`webhook` フィールドを通せる） | `_set_entries` |
| C5 | 起動/停止の配線 | `main()` / `_cleanup()` |
| C6 | モジュール動的ロード（任意） | `_load_hook_module` |

移植時の注意: ペインを遅延起動するフォークでは、初回 webhook が session 準備待ちで保留されます。dispatch queue から消さず、準備完了後に同じ request を再試行してください。

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

グローバル設定 `agent_name` を設定したデーモンだけが InboxWatcher スレッドを起こし、`inbox_poll_seconds`（既定 5 秒）ごとに inbox をポーリングします。各メッセージは request ID 付きで共通 dispatch gate へ投入し、lifecycle・preflight・セッション準備・セマフォ・ready 判定を通します。受付・送信に失敗した場合はファイルを保持し、**tmux 送信成功後に `.processed/` へ移動するまで処理済みとみなしません**。

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

## 機能 4: 動的インターバル（adaptive interval） — 実装済み

固定インターバルの無風時の無駄叩きを減らすため、発火結果から次の発火時刻を決めます。暗黙には有効化せず、`adaptive.enabled: true` のエントリだけを対象にします。固定時刻に意味がある `cron` エントリには適用しません。

### 適応アルゴリズム

| 結果 | インターバル更新 | 意図 |
|---|---|---|
| **activity** | `min_interval_seconds` へ即リセット、idle 回数を 0 | 活発時は最速へ戻す |
| **idle** | `idle_threshold` 回の連続後に `× backoff_factor` | 無風時だけ間引く |
| **error** | `min × backoff_factor` の短時間 retry。idle 回数は増やさない | 遷移関数は実装済み。scheduler との接続は未実装 |

更新値は `max_interval_seconds` で頭打ちにし、`jitter` で複数デーモンの同時ポーリングを分散します。状態は `~/.agents/loop-adaptive/<entry-id>.json` へ atomic write し、再起動をまたいで継続します。

### 設定

```yaml
prompts:
  - name: "GitLab Issue ワーカー (event_hook)"
    event_hook: ~/sandbox/tools/agent-loop/hooks/gitlab-issue-hook.py
    interval_minutes: 5
    adaptive:
      enabled: true
      min_interval_seconds: 60
      max_interval_seconds: 1800
      backoff_factor: 1.5
      idle_threshold: 3
      jitter: 0.2
```

`adaptive` 未指定・`enabled: false`・`cron` のエントリは従来どおりです。schedule 受付時は idle として次回時刻を進め、同じエントリの保留は 1 件に coalesce します。送信成功時だけ activity として最短へ戻します。

`next_adaptive_interval()` は error 遷移を持ちますが、現行の `check()` 戻り値には状態指定がなく、フックの例外・timeout・`None` はいずれも scheduler で idle として扱われます。障害と無風の分離、フックによる次回間隔の明示指定、LLM / EWMA による高度な頻度予測は未実装です。

---

## 機能 5: エージェント CLI の差し替え — 実装済み

agent-loop が駆動するエージェント CLI を、kiro-cli 固定からファミリー共通の [`agents/<name>.json` 契約](./agent-cli-plugin-design.md)による差し替え式にします。設定は全体の 2 キーと、定期プロンプトごとの任意指定です。

```yaml
agent_cli: claude            # 省略時は従来どおり kiro-cli（kiro_options）
agent_cli_options:
  model: claude-sonnet-5     # 定義の {model} / model_flag に渡す（省略可）
  readonly: false            # 読み取り専用フラグで起動（既定 false）
  extra_args: []             # argv 末尾への追加フラグ

prompts:
  - name: 設計レビュー
    agent_cli: codex         # 任意。省略すると上の全体設定
    model: gpt-5.6-terra     # 任意。片方だけの指定も可
    session: keep            # keep（既定・対話ペインを保つ）| per-run（実行ごとに使い捨て）
```

解決順は **control.json の `workloads.routine`（予算枯渇時の `degraded` 差し替えを含む）> entry > 全体設定 > 既定**。entry を管理面より上に置かないのは、上書きできると「予算が枯れても degrade が効かない entry」ができるためです。

### 設計判断

- **定義の解決と argv 組み立ては agentcore.agentcli へ委譲**します。「ローダは言語ごとに 1 実装」の不変条件（agent-cli-plugin 設計 §4）を守るため、agent-loop に第二のローダを書きません。zipapp インストールでは `install.sh` が agentcore を同梱し、リポジトリ直接実行では相対探索で解決します。agentcore が見つからない環境でも従来の kiro-cli 固定経路は動きます（`agent_cli` 指定だけが使えない）。
- **未知・壊れた定義は fail fast**。デーモン起動時に明示エラーで停止し、黙って kiro へ倒しません（同設計の明示エラー原則）。`send` などの補助コマンドだけは cowork の定常業務と同じ「黙らないフォールバック」（WARNING + 従来判定で続行）です。
- **`agent_cli` 未指定の挙動は 1 ビットも変わりません**。定義ファイルが 1 つも配布されていない環境でも従来どおり動きます（後方互換）。

### 2 つの実行経路 — 分岐点はツールループを誰が持つか

対話ペインで駆動する経路（`interactive`）に加えて、**実行のたびに subprocess を起こす headless 経路**を持ちます。tmux の対話セッションは会話を人に見せるための可視化であって、実行の必須要件ではありません。

分岐点は `interactive` の有無**ではなく**、定義の `headless_autonomy` が申告する「ツールループを CLI が内蔵するか」です。対話経路で agent-loop が薄くて済んでいたのは、探索・編集・コマンド実行が CLI の中で回っていたからで、ループを持たない CLI へ同じ扱いをすると着手すらしません（aider は「チャットに入っているファイルしか編集しない」）。

| 層 | 定義の申告 | 例 | agent-loop の扱い |
|---|---|---|---|
| 層2 | `tool-loop` | claude / codex / copilot / cursor / kiro / opencode / ollama | ヘッドレス argv を 1 回実行して exit code で完了検知 |
| 層3 | `single-shot` | aider / ollama-json / ollama-list | 限定ツール契約でツール実行を供給しながら完遂させる |

経路の既定は**従来どおり対話キープ**で、headless は `session: per-run` の opt-in です（既存の設定ファイルが無改変で従来と同じ挙動になることを優先）。ただし `interactive` 節を持たない定義は保てないので、`keep` 指定でも per-run へ倒して警告します。

headless では ensure_session / ready 判定 / SlotMonitor を通りません（判定する相手のペインが無い）。スロットは合成キー（`headless:<root_id>`）で取り、解放時にノード予算へ記帳されます——保持時間が実行時間そのものなので、対話経路の「送信 → 完了検知」による近似より正確です。semaphore・cooldown・lifecycle は従来と同じ契約で効きます。ralph 多段と external target は headless で扱えないため、その組み合わせは起動時に明示エラーで断ります。

### 層3 の限定ツール契約と受入条件

ツールループを持たない CLI へは `read_files` / `write_files` / `run` / `final` の 4 つだけを許す契約でツール実行を供給します。実装は statemachine 実行ハーネスと**共用**で、パス正規化・シェル禁止・実行ファイルの所在限定・JSON パーサ・コンテキスト節約・小型モデル向けのプロンプト規律（作業していないのに完了を主張させない等）を 1 実装に保ちます。

ツールループを供給しても、**受入条件が無ければ done を機械検証できません**。定期プロンプトは各 state が出力契約を持つステートマシンと違い、ゴールだけあって受入条件がないためです。そこで定期プロンプトに `acceptance`（自然文チェックリスト）を持たせます。語彙は統一 verify の `task_acceptance_criteria` に揃え、新しい書式を作りません。

```yaml
prompts:
  - name: ログ要約
    prompt: agent-audit で取得したログから重要な情報を抽出し、要約してください。
    acceptance:
      - "`reports/audit-digest.md` が今回の実行で更新されている"
      - 直近 24 時間のエラーが発生元ごとに件数付きで列挙されている
```

決定的シェルコマンドを人に書かせる方式は採りません（環境差で大半が失敗し、「たまたま通る劣化した検証」を人が見抜けないため）。人は自然文だけを書きます。

証跡ゲートは受入条件を入力に取ります。基準文のバッククォート内にあるプロジェクト内パスを抽出し、**実在・この実行で触れたか・実際に変わったか**を LLM を介さず照合します（フェイルクローズ）。ここに LLM を挟むと自己承認の穴が戻ります。副産物として、基準文が名指ししたファイルはツールループの初期割付にも使われ、「ファイルを渡さないと着手しない」問題が受入条件を書くことで解けます。

`acceptance` の無い層3 の entry は**警告して実行し、結果を「検証なし」として記録**します（done の根拠にしない）。起動は止めません——移行のためです。層2 では警告しません（従来どおり自由文で動くため、新方式に従わないこと自体は問題ではない）。段の降格で層2 から層3 へ落ちた entry も同じ扱いです。

### CLI / モデルの差し替えはセッション境界で効く

差し替えの適用点は**セッション境界**です。境界は既存のものを使い、新設しません。

| セッション設定 | 境界 | 差し替えが効くタイミング |
|---|---|---|
| `oneshot` / `session: per-run`（headless） | 毎回 | 次の実行 |
| `clean_session: N` | N 回成功ごと | 次の建て直し |
| 無限キープ（`persistent`・`clean_session` 無し） | デーモン再起動のみ | 再起動後 |

無限キープで実行中に切り替わらないことは受け入れます。会話文脈を保つと選んだ以上、途中で実行主体が入れ替わる方が害が大きいためです。agent-loop は終了時に全ペインを畳む（`SessionManager.stop()` を `atexit` とシグナルハンドラから呼ぶ）ので、再起動が確実な境界になります。

既存ペインと要求内容が食い違っても**実行は捨てません**。判定は `launch_fingerprint`（CLI 名 + argv + cwd）で行い、モデル単独比較では拾えない CLI の切り替えも検出します。食い違いは警告（セッションごとに 1 回）と status の `restart_required` で「境界待ち」として伝え、dashboard の「設定の反映」列に出します。`revision_applied` は**実際に解決へ使った** revision を報告します（ファイルの最新値を applied と報告すると、まだ適用していない設定が「反映済み」に見えます）。

### 待機状態の監視・判定 — CLI ごとに方法が違う

agent-loop は「送信してよいか」「処理が終わったか」をペイン画面から判定します（送信前チェック・SlotMonitor のスロット解放・起動待ち）。従来はプロンプト記号の正規表現 1 本（`_PROMPT_RE`）でしたが、**この判定方法は CLI ごとに違います**。

| CLI のタイプ | 例 | 有効な判定 |
|---|---|---|
| 処理中はプロンプトが消える | kiro-cli | ready の消失 = 処理中（従来ヒューリスティクス） |
| 入力欄を出したまま処理する TUI | claude（`(esc to interrupt)`）/ codex | **ready が消えない**ため、処理中マーカーの検出（`busy_pattern`）が判定の正 |
| 安定したマーカーを持たない | 素朴な REPL | 画面が N 秒変化しない = 待機（`idle_quiet_sec` の静穏判定） |

そこで契約の `interactive` に `busy_pattern`（処理中の正の検出、可視画面全体・大文字小文字無視）と `idle_quiet_sec`（静穏判定）を追加し、agent-loop 側は 1 つの判定器（`CliProfile`）に畳みます。判定の優先順位は **busy_pattern マッチ → 処理中 ＞ ready_pattern マッチ（末尾 3 行） → 待機 ＞ 静穏 → 待機 ＞ それ以外 → 処理中**。ready/busy パターンは grep 方言の ERE が契約なので、POSIX 文字クラス（`[[:space:]]` 等）を Python 正規表現へ写像してからコンパイルし、壊れたパターンは WARNING の上で組み込み既定へフォールバックします。

SlotMonitor の状態遷移は従来の「プロンプト消失 → processing → 再出現 → 解放」から「非待機 → processing → 待機 → 解放」へ一般化されます。legacy プロファイル（`agent_cli` 未指定）ではこの 2 つは同じ判定です。

### 送信テキストの作法も定義に従う

- **コンテキスト破棄**: fresh_context が送るコマンドは `interactive.clear_command`（既定 `/clear`、codex は `/new`）。空文字は「クリア手段なし」の宣言で、警告の上クリアだけスキップします。
- **スラッシュコマンドの行頭記号**: `slash` プロパティとセッション開始コマンド（chat モード）の行頭 `/` は、送信直前に定義の `skill_command_prefix` へ差し替えます（codex は `$name`。既定 `/` の CLI は素通し）。

### 制約

- **kiro 以外では slot-release stop hook を注入しません**（stop hook は kiro-cli の agents 機構）。スロット解放は SlotMonitor のペイン監視だけで行います。headless 経路では subprocess の exit code が完了検知なので、この制約は掛かりません。
- `startup_timeout` は従来どおり agent-loop の設定を正とし、定義の `ready_timeout_sec` は他の消費者（対話診断等）向けのままです。
- `external_panes[].agent_cli` は外部 pane の ready / busy 判定だけを選び、起動 CLI は変更しません。
- **headless 経路では対話前提の機能を黙って劣化させません**。fresh_context のコンテキスト破棄と `slash` は WARNING の上でスキップし、ralph 多段と external target は起動時に明示エラーで断ります。
- **証跡ゲートは機械層だけが動いています**。宣言されたファイルの実在・touched・変化は決定的に照合しますが、パスを含まない自然文の基準を検証エージェントが証跡付きで判定する層は未実装です。そのため受入条件を書いても、機械が確かめているのは「成果物が出来て変わったか」までです。
- 実行ログは JSONL（`~/.agents/runs/headless/`）へ出ますが、それを追う tmux ウィンドウの自動起動は未実装で、様子を見るにはログを人が開く必要があります。

---

## 機能 6: `slash` プロパティ — 実装済み

定期プロンプトの本文より前に、対話 CLI のコマンドを独立送信します。コマンドを本文へ埋め込まず、YAML の構造として分離することで、本文を変えずにコマンドだけを差し替えられます。

### 設定と正規化

```yaml
prompts:
  - name: "定期点検"
    slash: ["healthcheck", "report --lang ja"]
    prompt: "結果を 3 行で"
    interval_minutes: 240

  - name: "コンテキスト整理だけ"
    slash: compact
    interval_minutes: 120
```

- 型は文字列または文字列配列。`prompt` を省いた `slash` 単独エントリも有効
- 各要素は `<name> [args]`。名前は `^[a-z0-9][a-z0-9._-]*$`
- 先頭の `/` は不要。付いていれば警告して剥がす
- 不正要素はその要素だけを警告して捨て、エントリ全体は無効化しない
- `prompt` / `slash` / `event_hook` のいずれも無いエントリだけを無効とする

### 送信順と CLI 差異

送信順は **fresh context の clear command → `slash` を宣言順に 1 件ずつ → `prompt` 本文**です。各コマンドは本文へ連結せず独立入力とし、失敗した時点で後続コマンドと本文の送信を止めます。clear 後は 2 秒、`slash` 間は 1 秒だけ空け、応答完了は待ちません。`event_hook` 併用時は、フックがプロンプトを返して実際に dispatch される場合だけ `slash` も送ります。

内部では `/name` へ正規化し、送信直前に `CliProfile.skill_command_prefix` へ書き換えます。既定は `/`、codex は `$` です。clear command と `slash` 自体には `agent-tuning` の prompt 注入を適用しません。

`slash` 未指定時の挙動は変わりません。CLI からエントリを追加する `prompt-add --slash` は設けず、YAML 編集を設定の正とします。

---

## 共通実行基盤: Phase 1 / Phase 2 — 実装済み

2026-08-08 の[段階的機能拡張](../plans/2026-08-08-agent-loop-phased-enhancement-design.md)で、個別入力経路の公開契約を保ったまま内部配送と実行形態を拡張しました。Phase 2 の設定・状態遷移・失敗境界は[詳細設計](../plans/2026-08-08-agent-loop-phase2-detailed-design.md)を正とします。

### Phase 1 — Core Reliability

| 領域 | 確定した境界 |
|---|---|
| 配送 | 全入力を request ID 付きの共通 dispatch queue へ合流。priority / FIFO / schedule 1 件 coalesce / 短時間重複排除を適用 |
| CLI send | daemon 稼働時は `~/.agents/send-requests/` へ atomic 投函。`--wait` は同じ request ID の busy→ready / failure / timeout だけを待つ |
| hook / preflight | event hook は 30 秒 timeout と送信後 `ack()`、preflight は 15 秒 timeout・例外時 fail-open。`--force` だけが preflight を迂回可能 |
| lifecycle / reload | `pause` / `resume` / `cancel` / `drain` と transactional reload。不正設定時は稼働中の設定・pane を維持 |
| 回復 / 診断 | dead pane・stale slot は常時回復。input / freeze / RSS / memory 回復は安全境界または opt-in を守り、`doctor [--json] [--fix]` は非破壊の修復だけを行う |

daemon が request をメモリキューへ受理した直後の crash は永続再送しません。重複実行を避けるため at-most-once を維持し、配送保証が必要な event hook と inbox はそれぞれ `ack()` とファイル移動で受理前の消失を防ぎます。

### Phase 2 — Execution Extensions

新しい workflow engine は作らず、通常 request を作る **dispatch adapter** と、pane の再利用・破棄を切り替える **session policy** として追加します。

| 分類 | 実装済み機能 |
|---|---|
| 実行 | 有界反復の Ralph、warm-up と実行後破棄を行う oneshot、成功 N 回ごとの clean session |
| ad-hoc send | `--model`、detached worktree の `--sandbox`、ready / preflight だけを限定迂回する `--force` |
| 外部 pane | agent-loop が起動・再起動・cleanup・slot 管理をしない登録済み tmux pane への配送 |
| hook | event replay / fallback、GitLab 接続先解決、追加・変更・削除を検知する file watch |
| 配布 | secret 値を prompt に含めない environment handoff、zipapp 限定の検証付き `update` |

Ralph の daemon 再起動後の途中再開、任意 workflow、dirty sandbox の自動削除、source / pip インストールの自己更新は非目標です。

### `agent-tuning`（資源効率計画 S11）

`$AGENT_TUNING_DIR`（既定 `~/.agents/tuning/`）の `tuning.json` を共通契約とし、エントリの `tuning_profile` で prompt 注入と pane 起動環境を選びます。注入は `session_start` / `every_prompt`、起動環境は PATH 前置と環境変数を宣言でき、engine / workload / agent CLI 条件で絞り込みます。設定不在・破損・`enabled: false` は定常送信を止めず no-op です。

外向き成果物用の `external-facing` は、設定ファイルに注入 ID が誤記されても読み手側で必ず注入を空へ丸めます。PATH・環境変数は文体に影響しないため、同プロファイルで明示されたものを維持します。fresh context 後は次の業務 prompt だけ `session_start` 注入を再適用します。

---

## 検証状況

| 機能 | agent-loop 系統 | 旧系統（退役時の記録） |
|---|---|---|
| イベントフック | 実装済み。`test/test_event_hook.py` / `test/test_hook_hardening.py` | 実装済み |
| Webhook | 実装済み。`test/test_webhook_http.py`（実 HTTP E2E） | 実装済み。E2E 22 ケース通過の記録（実 HTTP・SessionManager スタブ） |
| メッセージング | 実装済み。`test/test_inbox_dispatch.py` | 実装済み。`test/test_messaging.py` |
| 動的インターバル | 実装済み。`test/test_adaptive_interval.py` | 未実装 |
| slash | 実装済み。`test/test_slash_property.py` | 未実装 |
| CLI 差し替え | 実装済み。`test/test_cli_profile.py`（+ agentcore 側 `test_agentcli.py`） | 未実装（kiro-cli 固定のまま） |
| Phase 1 / Phase 2 | 実装済み。dispatch・lifecycle・実行形態ごとの専用テスト | 未実装 |
| agent-tuning | 実装済み。`test/test_tuning.py` | 未実装 |

---

## 付録

### A. 実装後に更新すべきドキュメント

新しい拡張を実装する際は、本書の該当節の状態表記に加えて次を更新します。

- `tools/agent-loop/DESIGN.md` — クラス構成・`_run_loop` フロー・「新しいプロンプトオプションを追加する」節
- `tools/agent-loop/agent-loop.yaml.example` — 設定サンプル
- `tools/agent-loop/README.md` — 利用者向け概要
- 同梱フックの docstring — 契約変更（dict 戻り値等）がある場合

### B. kiro-loop 系統との差分と移行

`kiro-loop → agent-loop` は[クローン移行方針](./agent-tools-rename-design.md)に基づく改称系統で、移行と旧実装の退役は完了済みです（改称方針 §6、手順は[資源効率計画](../plans/2026-08-08-agent-tools-resource-efficiency-plan.md) F13）。設計の正典は本書で、次表は旧設定を移行するときの対応記録です。

| 項目 | agent-loop | kiro-loop |
|---|---|---|
| 実装形態 | `agent_loop/` パッケージ（`scheduler.py` / `webhook.py` / `inbox.py` / `sendcmd.py` 等に分割） | 単一スクリプト `kiro-loop.py` |
| 設定・状態ホーム | `~/.agents/`（`agent-loop.yaml`, `agent-loop.log`, `hooks/`） | `~/.kiro/`（`kiro-loop.yaml`, `kiro-loop.log`, `hooks/`） |
| フォールバック環境変数 | `AGENT_LOOP_EVENT_HOOK_FALLBACK` | `KIRO_LOOP_EVENT_HOOK_FALLBACK` |
| メッセージング inbox | `~/.kiro/agents/<name>/inbox/`（**共有**） | 同左（**共有**） |
| 適応状態ファイル | `~/.agents/loop-adaptive/` | 未実装 |

inbox は旧系統と共有していました。メッセージスキーマ（特に `reply_to` の意味）の片側だけの改変は、過去に非互換バグを生みました（2026-08-02 監査 D2、解消済み）。

新機能・設計更新は agent-loop 系統だけで行います。旧系統にだけあった
`stub/kiro-cli-stub.py` とその `test/test_stub.py` は Phase 0 / S2 で agent-loop へ退避済みです。
`setup-token-reduction.py` は移植せず、汎用注入契約へ畳んで退役させます（計画 F9）。

### C. 統合した旧文書

ループ拡張の 8 文書を 2026-08-06 に、`slash` の 1 文書を 2026-08-09 に本書へ統合し、削除した。

| 旧文書（kiro-loop 版 / agent-loop クローン版） | 作成日 | 本書の節 |
|---|---|---|
| `kiro-loop-event-hook-design.md` / `agent-loop-event-hook-design.md` | 2026-05-12 | 機能 1 |
| `kiro-loop-agent-messaging-design.md` / `agent-loop-agent-messaging-design.md` | 2026-05-23 | 機能 3 |
| `kiro-loop-gitlab-webhook-design.md` / `agent-loop-gitlab-webhook-design.md` | 2026-07-09 | 機能 2 |
| `kiro-loop-adaptive-interval-design.md` / `agent-loop-adaptive-interval-design.md` | 2026-07-05 | 機能 4 |
| `agent-loop-slash-property-design.md` | 2026-08-06 | 機能 6 |

統合にあたり、実装検証で追記されていた確定事項（フック例外は 200 で握る・`secret_header` の既定値・パススルー挙動・`reply_to` の意味の統一 等）は agent-loop クローン版の記述を正として採り、コードの行番号参照（モジュール分割で陳腐化）と実装当時の変更量見積り表は落とした。
