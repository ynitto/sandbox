# agent-loop 設計書

> 最終更新: 2026-08-12（設計書として再編。判断を核に据え、設定の書き方と実装内部は下記 2 文書へ寄せた）
> 実装: `tools/agent-loop/`（`agent_loop` パッケージ）。旧系統 `tools/kiro-loop/` は退役済み（付録 C）
> 設定の書き方は `tools/agent-loop/README.md`、クラス構成と処理フローは `tools/agent-loop/DESIGN.md`
> 関連: [agent-tools 改称方針](./agent-tools-rename-design.md) ／
> [段階的機能拡張](../plans/2026-08-08-agent-loop-phased-enhancement-design.md) ／
> [Phase 2 詳細設計](../plans/2026-08-08-agent-loop-phase2-detailed-design.md)
>
> 本書は agent-loop の設計正典です。旧 kiro-loop 系 4 文書とその agent-loop クローン 4 件、
> `agent-loop-slash-property-design.md` の計 9 文書を統合しています（付録 D）。

## TL;DR

agent-loop は、YAML に書いたプロンプトを定期送信するデーモンです。定期発火だけでなく、フックの判断・外部システムからの HTTP POST・他エージェントのメッセージ・CLI からの単発 send も、同じ 1 本の配送経路でエージェント CLI へ流します。

主要な決定は次の 3 つです。

- 入力経路が 5 つあっても、**送ってよいかを決める場所は `PeriodicScheduler` の dispatch gate ただ 1 か所**にする
- 送信元固有の知識（GitLab のヘッダ名や payload 構造）はフックスクリプトに閉じ、コアは受信とルーティングと配送しか知らない
- 実行経路の分かれ目は tmux の有無ではなく、**ツールループを CLI が内蔵するかどうか**。内蔵しない CLI には 4 種のツールだけを供給し、完了は受入条件で機械照合する

却下した主要案は、汎用 workflow engine への作り替え（agent-loop の責務を越える）、webhook を使い捨てペインで処理する案（所定の固定セッションへ流す要件に合わない）、頻度や状態遷移を LLM に決めさせる案です。

読むべき人は、agent-loop を運用する人、フックを書く人、本設計を別フォークへ移植する人です。設定の書き方だけ知りたいなら README で足ります。

### 本書が扱う 7 機能

| # | 機能 | 固定送信に何を足すか |
|---|------|---------------------|
| 1 | イベントフック（pull 型） | スケジュール発火のたびに `check()` を呼び、送信可否と文面をスクリプトで決める |
| 2 | 汎用 inbound Webhook（push 型） | 外部システムの HTTP POST を `handle(ctx)` でプロンプトへ変換する |
| 3 | エージェント間メッセージング | ファイルベースの inbox 経由で非同期の依頼と返信を配送する |
| 4 | 動的インターバル | 無風時はポーリング間隔を伸ばし、イベント到来で最短へ戻す |
| 5 | エージェント CLI の差し替え | 駆動する CLI を `agents/<name>.json` 契約で選び、headless CLI も同じ枠で動かす |
| 6 | `slash` プロパティ | 本文の前に CLI コマンドを独立送信する |
| 7 | ステートマシンハーネス | headless CLI に `statemachine-use` の定型業務を完走させる |

いずれも実装済みです。テストと未接続部分は付録 A にまとめました。

## 背景と課題

素の agent-loop の駆動方式は `interval_minutes`（または `cron`）による固定スケジュールだけで、送る文面も YAML に書いた固定テキストだけでした。この単純さは美点ですが、実運用では次の 4 つに当たります。

| 限界 | 症状 | 対応する拡張 |
|---|---|---|
| 文面もタイミングも固定 | 「新しい issue があるときだけ、その内容で」ができない | 機能 1 |
| 外部イベントに即応できない | MR が開かれてから次のポーリングまで最大 1 周期待つ | 機能 2 |
| エージェント同士が会話できない | オーケストレータからワーカーへの委譲手段が無い | 機能 3 |
| 無風でも一定頻度で叩き続ける | 深夜や週末に GitLab API を 288 回/日 無駄叩きする | 機能 4 |

拡張を足すほど「どこから来た送信か」で分岐が増え、busy 判定と slot 管理が入力経路ごとに散らばります。解くべき問いは、公開契約を壊さずに配送判定を 1 か所へ寄せられるか、でした。

### 目標

- スケジュール・フック・Webhook・inbox・CLI send を 1 つの送信経路（スケジューラの dispatch）に合流させる
- 既存 YAML と既存フックの後方互換を壊さない（未指定なら従来挙動のまま）
- GitLab / GitHub / 自作システムのどれが相手でも、コアを書き換えずフックの差し替えで対応できる

### 非目標

- メッセージや Webhook の at-least-once 配送保証。キューはインメモリ（inbox のみファイル永続）で、取りこぼせないイベントはポーリング併用で冪等に取りに行く運用とします
- エージェント CLI 自体の改造。agent-loop はテキストを送り、結果を見えるところに置くだけに徹します
- LLM による適応判断。動的インターバルの知能はヒューリスティクスに限定します

## 全体像

この節の抽象度はコンポーネントです。5 つの入力が 1 つの dispatch gate に合流し、そこから先は経路によらず同じ判定を通ります。

```
                    ┌──────────────────────── agent-loop デーモン ────────────────────────┐
 固定スケジュール ──▶ schedule prompt                                                     │
 event hook ─────────▶ schedule 発火 → check()（pull・機能 1）                             │
 外部システム ──HTTP─▶ WebhookServer ── handle(ctx) → 外部 deque（機能 2）                 │
 他エージェント ─file─▶ InboxWatcher ── JSON file（機能 3）                                │
 CLI send ──────file─▶ send-requests ── atomic claim                                      │
                    │                   ▼                                                  │
                    │       request ID 付き共通 dispatch queue                            │
                    │                   ▼                                                  │
                    │ lifecycle → preflight → session → slot → ready → _dispatch_prompt   │
                    └───────────────────▼──────────────────────────────────────────────────┘
                                    tmux ペイン（エージェント CLI）
```

| | 機能 1 フック | 機能 2 Webhook | 機能 3 メッセージング | 機能 4 動的インターバル |
|--|--|--|--|--|
| 起点 | agent-loop（スケジュール発火） | 外部システム（HTTP） | 他エージェント（CLI） | agent-loop（発火結果の観測） |
| 方向 | pull | push | push（ファイル経由） | 頻度制御のみ |
| フック契約 | `check(config?) -> str \| dict \| None` | `handle(ctx) -> dict \| None` | なし（JSON スキーマ） | なし（activity / idle を観測） |
| 実行スレッド | timeout 付き hook thread | HTTP サーバ | InboxWatcher | scheduler |
| 永続性 | フック自身の状態ファイル | インメモリ deque（at-most-once） | ファイル（`.processed/` 移動まで未処理） | `~/.agents/loop-adaptive/` |

## 主要な設計判断

### 1. 公開契約は保ったまま、配送判定を 1 か所へ畳む

**判断**: schedule / event hook / webhook / inbox / CLI send を request ID 付きの共通 dispatch request へ正規化し、`PeriodicScheduler` を唯一の配送判定箇所にする。YAML・フック・ファイルの公開契約は変えない。

**文脈**: 入力元ごとに busy・slot・lifecycle の判定が分かれていたため、要求の消失と完了の誤判定が起きていました。Phase 1 では公開面に手を触れず、内部だけを共通化しています。

**選択肢と却下理由**: 呼び出し箇所ごとの個別強化は判定の重複を残します。汎用 workflow engine への全面改造は、定期送信という agent-loop の責務を越えます。

**トレードオフ**: daemon が request をメモリキューへ受理した直後にクラッシュしても永続再送はせず、従来の at-most-once 境界を維持します。

**確信度**: 高い。入力経路ごとの回帰テストで固定しています。

### 2. pull と push を対称の契約にし、provider 固有はフックへ閉じる

**判断**: pull 型は `check(config=None) -> str | dict | None`、push 型は `handle(ctx) -> dict | None`。どちらも「None なら何もしない」「モジュールは `importlib` と mtime キャッシュでロードする」という同じ規約に載せる。イベント種別の判定、HMAC 署名検証、payload 構造の知識はすべてフック側に置き、コアが持つのは HTTP 受信・ルーティング・汎用共有シークレット照合・キュー・テンプレート注入だけにする。

**文脈**: pull はフックが自分でデータを取りに行くので文面まで組み立てられますが、push では受信 payload の解釈（フックの仕事）と文言（設定の仕事）を分けたい、という非対称があります。加えて GitLab 前提で作ると、GitHub の `X-Hub-Signature-256` や Slack の body 内 `type` を足すたびにコアへ分岐が増えます。

**選択肢と却下理由**: push でも完成プロンプトを返させる案は、文言を変えるたびにフックスクリプトの編集が要り、パースと文言の責務が混ざるため却下しました。pull の dict 戻り値は、既存の文字列戻り値を残した後方互換の追加に留めています。

**トレードオフ**: 契約が 2 種類になり、フック作者は署名検証まで自前で書きます。代わりにコアは送信元を一切知らず、GitLab を参照しない最小フック（`ctx.payload` をそのまま返す `generic-webhook.py`）が同じコアで動くことが汎用性の検証になっています。

**確信度**: 高い。provider 非依存はユーザー確認済みの確定事項です（2026-07-10）。

### 3. 実送信は scheduler の背圧機構だけを通す

**判断**: HTTP スレッドと InboxWatcher は tmux やセマフォを直接触らず、キューへ積むところで手を放す。セッション準備・同時実行数制御・保留と再試行は scheduler 側の既存機構が一手に担う。

**文脈**: Webhook は GitLab のタイムアウトがあるため受信スレッドをブロックできず、inbox は処理できるまで保留する必要があります。どちらも自前で排他を持つと、セマフォの二重管理と競合が生まれます。

**選択肢と却下理由**: Webhook の宛先を InboxWatcher に向け、毎回エフェメラルなペインで処理する案は、所定の固定セッションへ流すという要件に合わず却下しました（ユーザー確認済み、2026-07-09）。

**トレードオフ**: 即時性は 1 ポーリングサイクル（1 秒）ぶん犠牲になりますが、fresh_context・cwd・セマフォといったエントリ属性がどの経路でもそのまま効きます。

**確信度**: 高い。

### 4. 実行経路は tmux の有無ではなく、ツールループの所在で分ける

**判断**: 対話ペインで駆動する経路に加えて、実行のたびに subprocess を起こす headless 経路を持つ。分岐点は定義の `headless_autonomy` が申告する「探索・編集・コマンド実行のループを CLI が内蔵するか」であり、`interactive` 節の有無ではない。tmux はどちらの経路でも、送る手段と見る手段として同じように使う。

**文脈**: 対話経路で agent-loop が薄くて済んでいたのは、ループが CLI の中で回っていたからです。ループを持たない CLI に同じ扱いをすると着手すらしません（aider はチャットに入っているファイルしか編集しません）。

**選択肢と却下理由**: 対話ペインを必須にする案は、ペインを持たない CLI を締め出します。逆に全部を headless へ寄せる案は、会話文脈を保つ既存運用を壊します。そこで既定は従来どおり対話キープのままにし、headless は `session: per-run` の opt-in にしました。

**トレードオフ**: 経路が 2 つに増えます。headless では fresh_context のコンテキスト破棄と `slash` が効かず（警告のうえスキップ）、ralph 多段と external target は起動時に明示エラーで断ります。黙って劣化させないことを優先しました。

**確信度**: 高い。デーモンの headless 枝と単発の `run` サブコマンドが同じ 1 実装を通るので、経路差による証跡ゲートの抜けは生まれません。

### 5. 完了は自然文の受入条件から機械照合する

**判断**: ツールループを持たない CLI（層 3）の done を、自然文の受入条件 `acceptance` に書かれたバッククォート内のプロジェクト内パスから決める。実在するか・この実行で触れたか・実際に変わったかを LLM を介さず照合し、確かめられなければ失敗側へ倒す。

**文脈**: 各 state が出力契約を持つステートマシンと違い、定期プロンプトはゴールだけあって受入条件がありません。ツール実行を供給しても、これだけでは done を機械検証できません。

**選択肢と却下理由**: 決定的なシェルコマンドを人に書かせる方式は、環境差で大半が失敗するうえ、たまたま通る劣化した検証を人が見抜けません。合否を LLM に判定させる案は自己承認の穴が戻ります。人は自然文だけを書き、語彙は統一 verify の `task_acceptance_criteria` に揃えて新しい書式を作りませんでした。

**トレードオフ**: 機械が確かめられるのは、宣言された成果物が出来て変わったかまでです。パスを含まない基準は未検証のまま残ります。副産物として、基準文が名指ししたファイルはツールループの初期割付にも使われ、ファイルを渡さないと着手しない問題が受入条件を書くことで解けました。

**確信度**: 中くらい。機械層は動いていますが、自然文の基準を証跡付きで判定する層が未実装なので、検証範囲は成果物の有無と変化までに留まります。

---

## 機能 1: イベントフック（pull 型）

この節はフック作者向けの契約です。スケジュール（`interval_minutes` / `cron`）が発火したタイミングで `check()` を呼び、発火してよいかの最終判断をスクリプトへ委ねます。スケジュール自体は廃止しません。

```python
def check(config=None) -> str | dict | None:
    """str=送信する完成プロンプト / dict={"prompt", "cwd"?, "vars"?} / None=このサイクルはスキップ"""

def ack() -> None:
    """任意。tmux への送信成功後だけ呼ばれる。"""
```

- 引数なしの既存 `check()` はそのまま有効です。1 引数を受ける場合はエントリ名・ID・fallback 可否・個別設定・cwd・workspace を渡します
- dict の `cwd` は実在ディレクトリだけを受理し、`vars` は `prompt.format_map` へ渡します
- `check` が無い、戻り値が不正、例外が出た、のいずれもログを残してスキップします。デーモンは止めません
- 30 秒で timeout したフックは完了まで隔離し、同じフックの thread を増殖させません
- モジュールは mtime 監視付きでロードし、変更時だけ再ロードします

既読化の境界が要点です。イベントを既読にするのは `check()` ではなく、tmux 送信が成功した後の `ack()` です。schedule はエントリごとに最大 1 件を保留し、発火を queue へ受理した時点で `next_run_at` を進め、busy や slot 上限では同じ request を保留して次の発火を 1 件へ coalesce します。こうしておくと、受付前の延期でイベントを取りこぼしません。

`event_hook_fallback: true` を立てると、新規イベントが無ければ未完了イベントを cooldown 後に replay し、それも無ければ候補を 1 件選ぶ挙動になります。優先順位と provider 固有の選択は同梱フックに閉じ、コアはフラグと設定を渡すだけです。同梱の `hooks/gitlab-issue-hook.py` と `hooks/gitlab-mr-hook.py` は、状態ファイルに `iid -> updated_at` を保存して新規と更新を検知し、ラベルに応じて文面を切り替えます。

注意点が 2 つあります。`check()` は 30 秒で scheduler 側の待機を打ち切りますが、Python thread 自体は強制終了できないので、ネットワーク呼び出しにはそれより短い timeout を設定してください。`exec_module` はモジュールのトップレベルを実行するため、副作用は `check()` の中に閉じてください。

設定例は [README の event_hook 節](../../tools/agent-loop/README.md)にあります。

---

## 機能 2: 汎用 inbound Webhook（push 型）

この節はコンポーネントの責務分界と受信フローです。設計の主眼は provider 非依存の汎用 inbound webhook コアであり、GitLab はその上に載るフックの一具体例にすぎません。

| レイヤ | 責務 | provider 依存 |
|--------|------|:---:|
| コア（`WebhookServer`） | HTTP 受信 / `<name>` ルーティング / 共有シークレット検証 / ボディサイズ制限 / キュー投函 / テンプレート注入 | ✗ |
| フック（例: GitLab） | イベント種別の判定とフィルタ / 署名の独自検証 / payload パースから key-value 辞書へ | ✓ |
| テンプレート（エントリの `prompt`） | 文言（辞書キーを `{key}` で参照） | ✗ |

`POST /hooks/<name>` の `<name>` は、既存 `prompts` エントリの `name` を URL-safe 化したものに一致させて解決します。つまり webhook の宛先は既存の名前付きセッションそのものです。ルート表は持たず、毎リクエスト `scheduler.resolve_webhook_route(name)` で最新エントリから引くので、設定リロード後もルートが陳腐化しません。一致しなければ 404 を返します。

フックが受け取る `ctx` は生に近く、`name` / `method` / `headers`（小文字キー）/ `query` / `raw` / `payload`（best-effort の JSON パース、非 JSON なら `{}`）を持ちます。`ctx.event` のような provider 固有の属性は意図的に持たせません。GitLab フックなら `ctx.headers.get("x-gitlab-event")` を自分で読みます。`ThreadingHTTPServer` なので `handle` は複数スレッドから同時に呼ばれ得ます。状態を持つならフック側でロックしてください。

フックが返した辞書は、基本キー `name` を補完したうえで `str.format_map(_SafeDict(params))` でテンプレートへ注入します。`_SafeDict` は未定義キーを `{key}` のまま残すので、テンプレートの誤記やフックの欠損キーで `KeyError` クラッシュしません。テンプレート本文に波括弧を書く場合は `{{ }}` でエスケープします。`webhook.hook` を省いたエントリはパススルーになり、受信 JSON ボディをそのまま注入パラメータに使います。

```
POST /hooks/<name>
  ├─ ① メソッド判定       GET（/hooks/_health 以外）→ 405
  ├─ ② ルート解決         resolve_webhook_route(name) が無ければ 404
  ├─ ③ シークレット検証    secret_header の値 ≠ secret → 401（未設定なら素通り + 起動時 WARNING）
  ├─ ④ ボディ読取         サイズ超過 → 413。JSON パースは best-effort
  ├─ ⑤ handle(ctx)        None → 200（ignored）。フック例外と handle 不在も 200 で握る
  ├─ ⑥ テンプレート注入    prompt.format_map(_SafeDict(params))
  ├─ ⑦ 外部キューへ投函    enqueue_external(name, prompt_text)
  └─ ⑧ 202 Accepted を即返す（tmux への送信完了は待たない）
```

フック例外を 500 でなく 200 で握るのは、GitLab が 5xx にリトライを重ねて毎回同じ例外で嵐になるのを避けるためです（確定事項）。

キューはエントリ dict の中ではなく、scheduler が `name` をキーに独立保有する bounded deque（`_external_queues`、上限超過は古いものから破棄して警告）です。エントリ dict に持たせると、設定リロードでエントリを全置換するたびに未処理 webhook が捨てられ、`_run_loop` の浅いコピーとも競合します。enqueue（HTTP スレッド）と drain（scheduler スレッド）は同一ロック下でのみ deque を操作します。`_drain_external_to_pending()` は 1 tick あたりエントリごとに 1 件を共通 dispatch queue へ移し、以後は他の入力と同じ判定を通します。queue へ受理できなければ deque の先頭へ戻します。

webhook 専用エントリはスケジュール無しで定義でき、その場合 `next_run_at = math.inf` の sentinel でスケジュール発火パスから外れます。発火するのはキュードレイン経由だけです。event_hook との併用もできます。

```yaml
webhook:                   # グローバル
  enabled: true
  host: 127.0.0.1          # 既定は localhost。外部公開はリバースプロキシ経由
  port: 8899               # enabled 時は明示必須（複数インスタンスの衝突防止）
  path_prefix: /hooks
  secret: ""               # 空なら検証せず起動時 WARNING
  secret_header: X-Gitlab-Token   # 照合するヘッダ名。未指定時の既定も同じ
  max_body_bytes: 1048576

prompts:
  - name: mr-reviewer      # POST /hooks/mr-reviewer に対応
    prompt: "[MR webhook] {project} !{mr_iid}（{action}）: {title} / {url}"
    webhook:
      hook: ~/sandbox/tools/agent-loop/hooks/gitlab-mr-webhook.py
      secret: ""           # ルート個別（省略時グローバル）
      secret_header: ""    # ルート個別（省略時グローバル）
```

イベント種別のフィルタはコア設定に持ちません。provider 固有なので、フックが `ctx.headers` を見て対象外を `None` で弾きます。

### セキュリティと耐久性

bind の既定は `127.0.0.1` です。SaaS からの受信にはトンネルかリバースプロキシが要り、公開と TLS 終端は前段に任せる割り切りにしています。共有シークレットの比較は `hmac.compare_digest`、HMAC 署名方式はフックが `ctx.raw` から再計算して検証します。ボディサイズ上限（既定 1MB）で簡易な DoS 緩和を行い、ヘルスチェックは `GET /hooks/_health` です。起動は `main()` で `webhook.enabled` かつ `port > 0` のときだけ行い、bind 失敗は WARNING で本体を継続します。

キューはインメモリだけなので、再起動やクラッシュで未処理 webhook は失われます。送信元は 202 を受けた時点で再送しないため、実質 at-most-once です。取りこぼせないイベントは event_hook のポーリング併用で冪等に取りに行ってください。

### 移植コントラクト（フォーク向け）

webhook 機能が host に求めるのは次の能力だけです。実現方法はフォーク任せで、等価物が無いフォークは先にそこを用意します。

| # | 能力 | agent-loop での実体 |
|---|------|---------------------|
| C1 | 常駐ループの存在（処理を差し込める） | `PeriodicScheduler._run_loop` |
| C2 | 名前付き送信先の解決 | エントリ `name` / `id` から `SessionManager` ペイン |
| C3 | プロンプト送信 API（session 準備と排他制御込み） | `ensure_session` + `_try_acquire_slot` + `_dispatch_prompt` |
| C4 | 設定の正規化フック（`webhook` フィールドを通せる） | `_set_entries` |
| C5 | 起動と停止の配線 | `main()` / `_cleanup()` |
| C6 | モジュール動的ロード（任意） | `_load_hook_module` |

ペインを遅延起動するフォークでは、初回 webhook が session 準備待ちで保留されます。dispatch queue から消さず、準備完了後に同じ request を再試行してください。

---

## 機能 3: エージェント間メッセージング

この節はデータ契約です。各エージェントは名前付きの inbox を持ち、他エージェントが投函したメッセージをエージェント CLI へのプロンプトとして処理します。既存の `send` はデバッグと手動操作、`msg` は非同期のエージェント間通信、と使い分けます。

inbox は `~/.kiro/agents/<agent_name>/inbox/` 配下のファイルで、処理済みは `.processed/` へ移します。このパスは kiro-loop 系統と意図的に共有していました（付録 C）。

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

必須は `id`（UUIDv4 hex）・`from`・`to`・`created_at`（Unix timestamp）・`body` の 5 つ、任意は `subject`・`reply_to`・`correlation_id`・`cwd` です。`reply_to` は返信元の**メッセージ ID 専用**で、`--reply-to` 未指定なら `null` です。返信先のエージェント名には `from` を使います。かつて kiro-loop 実装が未指定時にエージェント名へフォールバックする非互換を持っていましたが、解消済みです（[2026-08-02 監査](../reviews/2026-08-02-agent-tools-family-bug-audit.md) D2）。

グローバル設定 `agent_name` を持つデーモンだけが InboxWatcher スレッドを起こし、`inbox_poll_seconds`（既定 5 秒）ごとにポーリングします。各メッセージは request ID 付きで共通 dispatch gate へ入り、lifecycle から ready 判定までを通ります。受付や送信に失敗したらファイルを保持し、tmux 送信が成功して `.processed/` へ移すまで処理済みとみなしません。受信側のプロンプトは `[エージェント {from} からのメッセージ]` に件名と本文を続け、末尾に `agent-loop msg --to {from} --reply-to "{id}"` の返信コマンドを付ける形へ整形します。

未実装のロードマップとして、優先度・TTL・配送確認・`inbox --watch`・broadcast・添付（P1）、名前解決レジストリ・SQLite ストア・WebSocket / gRPC への移行（P2）を置いています。

---

## 機能 4: 動的インターバル

無風時の無駄叩きを減らすため、発火結果から次の発火時刻を決めます。暗黙には有効化せず、`adaptive.enabled: true` のエントリだけを対象にします。固定時刻に意味がある `cron` エントリには適用しません。

| 結果 | インターバル更新 | 意図 |
|---|---|---|
| activity | `min_interval_seconds` へ即リセットし、idle 回数を 0 に戻す | 活発時は最速へ |
| idle | `idle_threshold` 回続いたら `× backoff_factor` | 無風時だけ間引く |
| error | `min × backoff_factor` の短時間 retry。idle 回数は増やさない | 遷移関数は実装済み。scheduler との接続は未実装 |

更新値は `max_interval_seconds` で頭打ちにし、`jitter` で複数デーモンの同時ポーリングを散らします。状態は `~/.agents/loop-adaptive/<entry-id>.json` へ atomic write し、再起動をまたいで継続します。

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

判断材料は通常の dispatch で得られる送信成功とスキップ、それに過去の発火履歴だけに限り、適応のために新しい API 呼び出しを増やしません。LLM も使いません。目的が GitLab サーバの負荷削減なので、賢く決める処理自体が負荷を生んでは本末転倒です。

現状 `next_adaptive_interval()` は error 遷移を持ちますが、`check()` の戻り値に状態指定が無いため、フックの例外・timeout・`None` はいずれも scheduler で idle として扱われます。障害と無風の分離、フックによる次回間隔の明示指定、EWMA などによる高度な予測は未実装です。

---

## 機能 5: エージェント CLI の差し替え

本書で最も厚い節です。駆動する CLI を kiro-cli 固定から、ファミリー共通の [`agents/<name>.json` 契約](./agent-cli-plugin-design.md)による差し替え式にします。設定は全体の 2 キーと、定期プロンプトごとの任意指定です（記法は README）。解決順は control.json の `workloads.routine`（予算枯渇時の `degraded` 差し替えを含む）、entry、全体設定、既定の順です。entry を管理面より上に置かないのは、上書きできると予算が枯れても degrade が効かない entry ができてしまうためです。

定義の解決と argv 組み立ては agentcore.agentcli へ委譲します。ローダは言語ごとに 1 実装という不変条件（agent-cli-plugin 設計 §4）を守るため、agent-loop に第二のローダを書きません。未知や壊れた定義はデーモン起動時に明示エラーで止め、黙って kiro へ倒しません。`send` などの補助コマンドだけは WARNING を出して従来判定で続行します。`agent_cli` 未指定時の挙動は 1 ビットも変わらず、定義ファイルが 1 つも配布されていない環境でも従来どおり動きます。

### 2 つの実行経路

| 層 | 定義の申告 | 例 | agent-loop の扱い |
|---|---|---|---|
| 層 2 | `tool-loop` | claude / codex / copilot / cursor / kiro / opencode / ollama | ヘッドレス argv を 1 回実行し、exit code で完了を検知 |
| 層 3 | `single-shot` | aider / ollama-json / ollama-list | 限定ツール契約でツール実行を供給しながら完遂させる |

既定は従来どおり対話キープで、headless は `session: per-run` の opt-in です。`interactive` 節を持たない定義は keep を保てないので、`keep` 指定でも per-run へ倒して警告します。

tmux を使うかどうかは、この層の話とは独立です。tmux はコマンドを送る手段であり結果を見る手段なので、headless でも見せ方は従来と同じで、変わるのはウィンドウの中で走るものだけです。デーモンの外から 1 回だけ走らせる口として `run` サブコマンドを持ち、dashboard の定常業務やアドホック起動はこれを tmux ウィンドウで起こします。`run` は終了時に `RESULT {json}` を 1 行出力し、それが呼び出し側との結果契約になります（`statemachine` と同じ形）。層の分岐はデーモンの headless 枝と同じ 1 実装を通るので、デーモン経由なら証跡ゲートが効くが単発だと効かない、という経路差は生まれません。

headless では ensure_session と ready 判定と SlotMonitor を通りません（判定する相手のペインが無いからです）。スロットは合成キー（`headless:<root_id>`）で取り、解放時にノード予算へ記帳します。保持時間が実行時間そのものなので、対話経路の送信から完了検知までによる近似より正確です。semaphore と cooldown と lifecycle は従来と同じ契約で効きます。

### 層 3 の限定ツール契約と受入条件

ツールループを持たない CLI へは `read_files` / `write_files` / `run` / `final` の 4 つだけを許す契約でツール実行を供給します。実装はステートマシンハーネス（機能 7）と共用で、パス正規化・シェル禁止・実行ファイルの所在限定・JSON パーサ・コンテキスト節約・小型モデル向けのプロンプト規律を 1 実装に保ちます。違いはゴールの与え方だけで、機能 7 は state のアクション 1 つ、ここは定期プロンプト 1 件を渡します。

完了の機械検証には受入条件が要ります（主要な設計判断 5）。定期プロンプトに `acceptance` として自然文のチェックリストを持たせます。

```yaml
prompts:
  - name: ログ要約
    prompt: agent-audit で取得したログから重要な情報を抽出し、要約してください。
    acceptance:
      - "`reports/audit-digest.md` が今回の実行で更新されている"
      - 直近 24 時間のエラーが発生元ごとに件数付きで列挙されている
```

`acceptance` の無い層 3 の entry は、警告して実行したうえで結果を検証なしとして記録します（done の根拠にはしません）。移行のため起動は止めません。層 2 では警告しません。従来どおり自由文で動くので、新方式に従わないこと自体は問題ではないからです。段の降格で層 2 から層 3 へ落ちた entry も同じ扱いです。

### 差し替えはセッション境界で効く

適用点はセッション境界で、境界は既存のものを使い新設しません。

| セッション設定 | 境界 | 差し替えが効くタイミング |
|---|---|---|
| `oneshot` / `session: per-run`（headless） | 毎回 | 次の実行 |
| `clean_session: N` | N 回成功ごと | 次の建て直し |
| 無限キープ（`persistent`・`clean_session` 無し） | デーモン再起動のみ | 再起動後 |

無限キープで実行中に切り替わらないことは受け入れます。会話文脈を保つと選んだ以上、途中で実行主体が入れ替わる方が害が大きいからです。agent-loop は終了時に全ペインを畳む（`SessionManager.stop()` を `atexit` とシグナルハンドラから呼ぶ）ので、再起動が確実な境界になります。

既存ペインと要求内容が食い違っても実行は捨てません。判定は `launch_fingerprint`（CLI 名 + argv + cwd）で行い、モデル単独の比較では拾えない CLI の切り替えも検出します。食い違いは警告（セッションごとに 1 回）と status の `restart_required` で境界待ちとして伝え、dashboard の設定反映列に出します。`revision_applied` は実際に解決へ使った revision を報告します。ファイルの最新値を applied と報告すると、まだ適用していない設定が反映済みに見えてしまうためです。

### 待機状態の判定は CLI ごとに違う

agent-loop は、送信してよいかと処理が終わったかをペイン画面から判定します（送信前チェック、SlotMonitor のスロット解放、起動待ち）。従来はプロンプト記号の正規表現 1 本でしたが、有効な判定方法は CLI ごとに違います。

| CLI のタイプ | 例 | 有効な判定 |
|---|---|---|
| 処理中はプロンプトが消える | kiro-cli | ready の消失を処理中とみなす（従来ヒューリスティクス） |
| 入力欄を出したまま処理する TUI | claude（`(esc to interrupt)`）/ codex | ready が消えないので、処理中マーカー（`busy_pattern`）の検出が正 |
| 安定したマーカーを持たない | 素朴な REPL | 画面が N 秒変化しなければ待機（`idle_quiet_sec`） |

そこで契約の `interactive` に `busy_pattern`（可視画面全体、大文字小文字は無視）と `idle_quiet_sec` を追加し、agent-loop 側は `CliProfile` という 1 つの判定器に畳みます。優先順位は busy_pattern マッチで処理中、次に ready_pattern マッチ（末尾 3 行）で待機、次に静穏で待機、それ以外は処理中です。ready / busy パターンは grep 方言の ERE が契約なので、POSIX 文字クラスを Python 正規表現へ写像してからコンパイルし、壊れたパターンは WARNING のうえ組み込み既定へフォールバックします。SlotMonitor の状態遷移も、プロンプト消失から再出現までではなく、非待機から待機までへ一般化しました。`agent_cli` 未指定の legacy プロファイルでは、この 2 つは同じ判定になります。

送信テキストの作法も定義に従います。fresh_context が送るコマンドは `interactive.clear_command`（既定 `/clear`、codex は `/new`）で、空文字はクリア手段なしの宣言として警告のうえクリアだけスキップします。`slash` とセッション開始コマンドの行頭 `/` は、送信直前に定義の `skill_command_prefix` へ差し替えます（codex は `$name`、既定 `/` の CLI は素通し）。

### 制約

- kiro 以外では slot-release stop hook を注入しません（stop hook は kiro-cli の agents 機構です）。スロット解放は SlotMonitor のペイン監視だけで行います。headless 経路では subprocess の exit code が完了検知なので、この制約は掛かりません
- `startup_timeout` は従来どおり agent-loop の設定を正とし、定義の `ready_timeout_sec` は他の消費者向けのままです
- `external_panes[].agent_cli` は外部 pane の ready / busy 判定だけを選び、起動 CLI は変更しません
- headless 経路では対話前提の機能を黙って劣化させません。fresh_context のコンテキスト破棄と `slash` は WARNING のうえスキップし、ralph 多段と external target は起動時に明示エラーで断ります
- 証跡ゲートは機械層だけが動いています。宣言されたファイルの実在と touched と変化は決定的に照合しますが、パスを含まない自然文の基準を検証エージェントが証跡付きで判定する層は未実装です
- 実行ログは JSONL（`~/.agents/runs/headless/`）へ出ますが、それを追う tmux ウィンドウの自動起動は未実装で、様子を見るにはログを人が開く必要があります

---

## 機能 6: `slash` プロパティ

定期プロンプトの本文より前に、対話 CLI のコマンドを独立送信します。コマンドを本文へ埋め込まず YAML の構造として分離することで、本文を変えずにコマンドだけ差し替えられます。書き方は [README の slash 節](../../tools/agent-loop/README.md)にあります。

設計として決めたのは、失敗の粒度と送信順です。型は文字列または文字列配列で、各要素は `<name> [args]`、名前は `^[a-z0-9][a-z0-9._-]*$`。先頭の `/` は不要で、付いていれば警告して剥がします。不正な要素はその要素だけを捨ててエントリ全体は無効化しません（タイポで定期駆動が止まらないようにするためです）。無効にするのは `prompt` / `slash` / `event_hook` のどれも無いエントリだけです。

送信順は、fresh context の clear command、`slash` を宣言順に 1 件ずつ、`prompt` 本文、の順です。各コマンドは本文へ連結せず独立入力とし、失敗した時点で後続コマンドと本文の送信を止めます。clear 後は 2 秒、`slash` 間は 1 秒だけ空け、応答完了は待ちません。`event_hook` 併用時は、フックがプロンプトを返して実際に dispatch される場合だけ `slash` も送ります。内部では `/name` へ正規化し、送信直前に `CliProfile.skill_command_prefix` へ書き換えます。clear command と `slash` 自体には `agent-tuning` の prompt 注入を適用しません。

CLI からエントリを追加する `prompt-add --slash` は設けず、YAML 編集を設定の正としました。

---

## 機能 7: ステートマシンハーネス

`statemachine-use` は、対話 CLI がスキルを読み、コマンドを実行し、状態を進めることを前提にしたスキルです。aider のように対話セッションもツール実行ループも持たない headless CLI へ実行文をそのまま送っても、スキルの読み込みもコマンド実行も起きません。そこで、状態遷移はスキル側の正典に委ねたまま、足りないツール実行だけを狭い契約で補うサブコマンドを持ちます。終了時の `RESULT {json}`（`ok` / `stdout` / `finalState` / `logFile` / `files`）が dashboard との結果契約です（[設計](../plans/2026-08-11-agent-dashboard-routine-aider-tmux-harness-design.md)）。

- **状態遷移を LLM に選ばせません**。ワークフロー検証（`run_machine.py --dry-run`）・初期状態・遷移確定（`next_state.py`）は `statemachine-use` のスクリプトを正典として呼び、ハーネスは現在のアクション 1 つの実行だけを受け持ちます。LLM へ渡すのは現在のアクションと条件の真偽判定だけで、次の状態も後続のアクションも見せません
- CLI とモデルの解決は agentcore.agentcli へ委譲します（機能 5 と同じ不変条件）。`--model` は実行ごとの指定で、省略時は定義の `default_model` です
- ツール契約は 4 種に限定します。cwd は作業フォルダへ固定、相対パスは正規化して `..` とシンボリックリンクによる逸脱を拒否、実行ファイルは PATH 上かロード済みスキル配下に限り、シェル文字列は受け付けません。timeout とツール往復回数に固定上限を置き、実行した argv・cwd・終了コード・所要時間を JSONL の監査ログへ残します。**この実装は機能 5 と共用**です。同じ護りを 2 実装に分けると、片方だけ穴が塞がった状態が静かに生まれます
- ローカルモデル向けに文脈を絞ります。ワークフロー全体ではなく現在のアクションと必要なスキルだけを渡し、大きい入力はプロンプト本文へ展開せず CLI の読み取りフラグ（aider の `--read`）で渡し、コマンド出力は末尾の要約とログパスだけを次の往復へ載せます

v1 の制約は 3 つです。受理するのは `statemachine-use` の 1 経路だけで、2 つ目のスキルを載せるまで汎用のプラグイン登録基盤は作らず、同じ入力と結果契約へハンドラを足せる関数境界だけを保ちます。OS レベルの副作用隔離は持たず、argv・cwd・実行ファイル・パスの検証と監査ログを境界とします（強制隔離が要るようになった時点で OS sandbox を足します）。そしてハーネスはツール不足を補うものであり、小型モデルの文脈理解や長文生成能力そのものは保証しません。

---

## 共通実行基盤: Phase 1 / Phase 2

2026-08-08 の[段階的機能拡張](../plans/2026-08-08-agent-loop-phased-enhancement-design.md)で、個別入力経路の公開契約を保ったまま内部配送と実行形態を拡張しました。設定・状態遷移・失敗境界は [Phase 2 詳細設計](../plans/2026-08-08-agent-loop-phase2-detailed-design.md)を正とします。

| Phase 1 の領域 | 確定した境界 |
|---|---|
| 配送 | 全入力を request ID 付きの共通 dispatch queue へ合流。priority / FIFO / schedule 1 件 coalesce / 短時間重複排除 |
| CLI send | daemon 稼働時は `~/.agents/send-requests/` へ atomic 投函。`--wait` は同じ request ID の遷移だけを待つ |
| hook / preflight | event hook は 30 秒 timeout と送信後 `ack()`、preflight は 15 秒 timeout・例外時 fail-open。`--force` だけが preflight を迂回できる |
| lifecycle / reload | `pause` / `resume` / `cancel` / `drain` と transactional reload。不正設定時は稼働中の設定と pane を維持 |
| 回復 / 診断 | dead pane と stale slot は常時回復。input / freeze / RSS / memory 回復は安全境界か opt-in を守り、`doctor` は非破壊の修復だけを行う |

Phase 2 では新しい workflow engine を作らず、通常 request を作る dispatch adapter と、pane の再利用と破棄を切り替える session policy として追加しました。実行形態は有界反復の Ralph・warm-up と実行後破棄の oneshot・成功 N 回ごとの clean session、ad-hoc send は `--model` と detached worktree の `--sandbox` と限定迂回の `--force`、ほかに外部 pane への配送、event replay や GitLab 接続先解決や file watch といったフック、secret 値を prompt に含めない environment handoff と zipapp 限定の `update` です。Ralph の daemon 再起動後の途中再開、任意 workflow、dirty sandbox の自動削除、source / pip インストールの自己更新は非目標としました。

`agent-tuning`（資源効率計画 S11）は `$AGENT_TUNING_DIR`（既定 `~/.agents/tuning/`）の `tuning.json` を共通契約とし、エントリの `tuning_profile` で prompt 注入と pane 起動環境を選びます。注入は `session_start` と `every_prompt`、起動環境は PATH 前置と環境変数を宣言でき、engine / workload / agent CLI の条件で絞り込めます。設定不在・破損・`enabled: false` は定常送信を止めない no-op です。外向き成果物用の `external-facing` は、設定ファイルに注入 ID が誤記されても読み手側で必ず注入を空へ丸めます。PATH と環境変数は文体に影響しないので、同プロファイルで明示されたものは維持します。fresh context の後は、次の業務 prompt にだけ `session_start` 注入を再適用します。

---

## 付録

### A. 実装状況とテスト

7 機能と共通実行基盤はすべて実装済みです。旧 kiro-loop 系統に存在したのはイベントフック・Webhook・メッセージングの 3 つだけで、残りは agent-loop でのみ実装しました（Webhook は退役時点で実 HTTP の E2E 22 ケース通過の記録があります）。

| 機能 | テスト |
|---|---|
| イベントフック | `test/test_event_hook.py` / `test/test_hook_hardening.py` |
| Webhook | `test/test_webhook_http.py`（実 HTTP E2E） |
| メッセージング | `test/test_inbox_dispatch.py` |
| 動的インターバル | `test/test_adaptive_interval.py`（error 遷移は関数のみ。scheduler と未接続） |
| CLI 差し替え | `test/test_cli_profile.py`（+ agentcore 側 `test_agentcli.py`） |
| `slash` | `test/test_slash_property.py` |
| ステートマシンハーネス | `test/test_statemachine.py`（パス逸脱・ツール契約・スタブ CLI での完走） |
| Phase 1 / Phase 2 | dispatch・lifecycle・実行形態ごとの専用テスト |
| agent-tuning | `test/test_tuning.py` |

未接続・未実装として残っているのは、adaptive の error 遷移、自然文基準の証跡判定層、headless 実行ログを追う tmux ウィンドウの自動起動です。

### B. 実装後に更新すべきドキュメント

新しい拡張を実装するときは、本書の該当節に加えて次を更新します。

- `tools/agent-loop/DESIGN.md` — クラス構成、`_run_loop` フロー、「新しいプロンプトオプションを追加する」節
- `tools/agent-loop/agent-loop.yaml.example` — 設定サンプル
- `tools/agent-loop/README.md` — 利用者向け概要
- 同梱フックの docstring — 契約変更がある場合

### C. kiro-loop 系統との差分と移行

`kiro-loop` から `agent-loop` へは[クローン移行方針](./agent-tools-rename-design.md)に基づいて改称し、移行と旧実装の退役は完了しています（改称方針 §6、手順は[資源効率計画](../plans/2026-08-08-agent-tools-resource-efficiency-plan.md) F13）。次表は旧設定を移行するときの対応記録です。

| 項目 | agent-loop | kiro-loop |
|---|---|---|
| 実装形態 | `agent_loop/` パッケージ（`scheduler.py` / `webhook.py` / `inbox.py` / `sendcmd.py` 等） | 単一スクリプト `kiro-loop.py` |
| 設定・状態ホーム | `~/.agents/`（`agent-loop.yaml`, `agent-loop.log`, `hooks/`） | `~/.kiro/`（`kiro-loop.yaml`, `kiro-loop.log`, `hooks/`） |
| フォールバック環境変数 | `AGENT_LOOP_EVENT_HOOK_FALLBACK` | `KIRO_LOOP_EVENT_HOOK_FALLBACK` |
| メッセージング inbox | `~/.kiro/agents/<name>/inbox/`（共有） | 同左（共有） |
| 適応状態ファイル | `~/.agents/loop-adaptive/` | 未実装 |

inbox は旧系統と共有していたので、メッセージスキーマ（特に `reply_to` の意味）を片側だけ変えると壊れました。実際に非互換バグを生んでいます（2026-08-02 監査 D2、解消済み）。新機能と設計更新は agent-loop 系統だけで行います。旧系統にだけあった `stub/kiro-cli-stub.py` と `test/test_stub.py` は Phase 0 / S2 で agent-loop へ退避済みで、`setup-token-reduction.py` は移植せず汎用注入契約へ畳んで退役させました（計画 F9）。

### D. 統合した旧文書

ループ拡張の 8 文書を 2026-08-06 に、`slash` の 1 文書を 2026-08-09 に本書へ統合し、削除しました。

| 旧文書（kiro-loop 版 / agent-loop クローン版） | 作成日 | 本書の節 |
|---|---|---|
| `kiro-loop-event-hook-design.md` / `agent-loop-event-hook-design.md` | 2026-05-12 | 機能 1 |
| `kiro-loop-agent-messaging-design.md` / `agent-loop-agent-messaging-design.md` | 2026-05-23 | 機能 3 |
| `kiro-loop-gitlab-webhook-design.md` / `agent-loop-gitlab-webhook-design.md` | 2026-07-09 | 機能 2 |
| `kiro-loop-adaptive-interval-design.md` / `agent-loop-adaptive-interval-design.md` | 2026-07-05 | 機能 4 |
| `agent-loop-slash-property-design.md` | 2026-08-06 | 機能 6 |

統合にあたり、実装検証で追記されていた確定事項（フック例外は 200 で握る、`secret_header` の既定値、パススルー挙動、`reply_to` の意味の統一など）は agent-loop クローン版の記述を正として採り、コードの行番号参照と実装当時の変更量見積り表は落としました。
