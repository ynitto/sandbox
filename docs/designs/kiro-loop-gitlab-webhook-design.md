# kiro-loop GitLab Webhook（inbound）設計案

> 作成日: 2026-07-09
> 対象ファイル: `tools/kiro-loop/kiro-loop.py`, `tools/kiro-loop/kiro-loop.yaml.example`
> 参照フォーク: 本リポジトリ `@ b2ca89d`（`kiro-loop.py` 最終更新 2026-06-02）
> 関連設計: [event_hook](kiro-loop-event-hook-design.md) / [agent-messaging](kiro-loop-agent-messaging-design.md)

---

## 0. 移植ガイド（他フォークへの適用）— 先に読む

本設計は **フォーク非依存**を目標にする。kiro-loop は複数フォークで内部が分岐しており、
メソッド名・行番号・クラス構成は一致しないことがある。そこで本書では:

- 設計を **統合コントラクト（下記）** として抽象的に定義する。webhook 機能が host に
  求めるのは以下の能力だけで、その実現方法（メソッド名・スレッド構成）はフォーク任せ。
- 本文中の `kiro-loop.py:NNNN` や `_set_entries` 等の具体名は **参照フォーク（`@ b2ca89d`）
  での実体**を示す例示にすぎない。**規範ではない**。自フォークの等価物へ読み替える。

### 0.1 統合コントラクト（host が満たすべき能力）

| # | 能力 | 説明 | 参照フォークでの実体 |
|---|------|------|---------------------|
| C1 | **常駐ループの存在** | プロセス生存中に回り続けるスレッド/ループがあり、そこへ処理を差し込める | `PeriodicScheduler._run_loop`（`:1768`） |
| C2 | **名前付き送信先の解決** | 設定上の一意名 → 送信対象（tmux ペイン等セッション）を引ける | エントリ `name`/`id` → `SessionManager` ペイン |
| C3 | **プロンプト送信 API** | 「この送信先へこのテキストを送る」1 関数（session 準備・排他制御込みが望ましい） | `ensure_session`+`_acquire_slot`+`_dispatch_prompt` |
| C4 | **設定の正規化フック** | 設定読込時に各エントリを正規化する箇所（新フィールド `webhook` を通せる） | `PeriodicScheduler._set_entries`（`:1490`） |
| C5 | **起動/停止の配線** | デーモン起動時にスレッドを start、終了時に stop できる箇所 | `main()`（`:3012`）/ `_cleanup()`（`:2111`） |
| C6 | **モジュール動的ロード**（任意） | hook スクリプトを importlib で読む仕組み（無ければ新規に用意） | `_load_hook_module`（`:1650`） |

上記が揃っていれば、webhook 追加物（WebhookServer・外部キュー・`resolve_webhook_route`）は
**host 内部を書き換えず「差し込み」で載る**。C1〜C5 の等価物が無いフォークは、まずそこを
用意してから本設計を適用する。

### 0.2 移植時のチェックリスト

- [ ] C1 の常駐ループに「外部キューのドレイン」を 1 ブロック挿入できるか（§6）
- [ ] C4 の正規化に `webhook` フィールド＋非スケジュール sentinel を足せるか（§7）
- [ ] C5 に WebhookServer の start/stop を配線できるか（§8/§12）
- [ ] C3 が「session 未準備・排他上限で保留（積み直し）」を表現できるか。できなければ
      §6 のドレインで自前ガードする
- [ ] hook を持たない最小構成（テンプレートのみ）でも動くか（§4.2 の基本キー補完）

---

## 1. 背景・目的

既存の `event_hook` は kiro-loop 側からの **ポーリング型**（`check()` を定期呼び出し）。
本拡張はその逆で、GitLab から kiro-loop への **プッシュ型 webhook** を受ける。

- kiro-loop 起動中だけ HTTP サーバを常駐させる。
- GitLab の Webhook を `POST /hooks/<name>` で受ける。**パスの `<name>`** で
  どのセッションに流すかを決める。
- 受信ペイロード（+ヘッダ+クエリ）を **hook スクリプトでパースして整形**し、
  「後段」= 所定のセッションへプロンプトとして送る。

対比表:

| | event_hook | webhook（本拡張） |
|--|-----------|------------------|
| 起点 | kiro-loop（スケジュール発火） | GitLab（HTTP リクエスト） |
| 方向 | pull | push |
| フック関数 | `check() -> str \| None`（完成プロンプト） | `handle(ctx) -> dict \| None`（パラメータ辞書） |
| プロンプト整形 | フック内で完結 | フックは**辞書**を返し、エントリの `prompt` テンプレートへ注入 |
| ルーティング | プロンプトエントリ自身 | パスの `<name>` → エントリ |
| 実行スレッド | scheduler スレッド | HTTP サーバスレッド |

> **フックとテンプレートの分離**: パース（=GitLab ペイロードから何を取り出すか）は
> hook スクリプトの責務、文言（=どう伝えるか）はエントリの `prompt` テンプレートの
> 責務、と分ける。hook は key-value の辞書を組み立てて返すだけで、最終プロンプト文は
> テンプレート側で管理する。

---

## 2. 設計方針

- **依存追加なし**: Python 標準の `http.server.ThreadingHTTPServer` を使う。
- **常駐ライフサイクルは既存に相乗り**: `main()` で InboxWatcher の隣に
  `WebhookServer.start()`。`webhook.enabled` かつ port 指定時のみ起動。停止は
  `_cleanup` で `server.shutdown()`。
- **HTTP スレッドはブロックしない**: 受信→パース→hook→**キューへ投函**まで行い、
  即 `202 Accepted` を返す。tmux への送信完了は待たない（GitLab の webhook
  タイムアウト／リトライ嵐を避ける）。
- **後段は既存のバックプレッシャ機構に載せる**: 送信先セッションの準備・セマフォ・
  リトライは `PeriodicScheduler` が既に持つ。webhook は「対象エントリのキューに
  積む」だけにして、実 dispatch はスケジューラループに任せる（§6 参照）。
- **フック契約は event_hook と対称**: `check()` に対する `handle(ctx)`。
  `importlib` + mtime キャッシュのロード機構（`_load_hook_module`）を流用する。

---

## 3. ルーティング — パスの `<name>`

```
POST /hooks/<name>
        └──────── prompts エントリの name（sanitize 後）に一致させる
```

- `<name>` は既存 `prompts` エントリの `name` を URL-safe 化したキーで解決する。
  つまり **webhook の宛先 = 既存の名前付きセッション**。「所定のセッション」= その
  エントリのペイン。event_hook と同じセッションを共有できる。
- 一致するエントリが無ければ `404`。
- webhook を受けるエントリは **スケジュール不要**にできる（§7 の設定緩和）。

> 代替案: `<name>` を `agent_name`（InboxWatcher）に向け、毎回エフェメラルな
> `inbox-<id>` ペインへ流す方式もある。ただし「所定の（固定の）セッション」という
> 要件には、既存エントリのペインへ流す本方式が素直。→ §11 で比較。

---

## 4. hook スクリプトのインターフェース（後段の実体）

> ファイル例: `tools/kiro-loop/hooks/gitlab-mr-webhook.py`

hook は **パース結果の辞書（key-value）** を返す。最終プロンプト文は生成しない。

```python
def handle(ctx) -> dict | None:
    """GitLab webhook 受信時に HTTP サーバスレッドから呼ばれる。

    Returns:
        dict : プロンプトテンプレートへ注入する key-value パラメータ
        None : 無視（何も送らず 200 を返す）
    """
    ...
```

### 4.1 `ctx`（WebhookContext）

hook に渡すパース済みコンテキスト。dict でも軽量 dataclass でもよい。

| 属性 | 型 | 内容 |
|------|-----|------|
| `ctx.name` | str | ルート名（パスの `<name>`） |
| `ctx.event` | str | `X-Gitlab-Event`（例: `"Merge Request Hook"`） |
| `ctx.payload` | dict | パース済み JSON ボディ |
| `ctx.headers` | dict | リクエストヘッダ（小文字キー） |
| `ctx.query` | dict | クエリ文字列のパース結果 |
| `ctx.raw` | bytes | 生ボディ（署名検証など向け） |

- 引数は 1 つ（`ctx`）。hook の module-level 変数で状態保持可（HTTP は
  ThreadingHTTPServer なので複数スレッドあり得る → 状態を持つなら hook 側で
  ロックする、あるいは状態を持たない設計にする）。
- `handle` が無い / callable でない場合は WARNING を出して `500`（または `204`）。
- 戻り値が `dict` でも `None` でもない場合は WARNING を出してスキップ。
- 例外は握って `500`。ログに `exc_info` 付きで記録。

### 4.2 辞書 → テンプレート注入

hook が返した辞書は、エントリの `prompt` テンプレートへ **`{key}` プレースホルダ**で
差し込む。event_hook の `gitlab-issue-hook.py` が使っていた `.format()` 方式と同系だが、
テンプレートを YAML 側（`prompt`）に外出しして文言とパースを分離する。

```python
# scheduler 側（enqueue 時 or dispatch 前）の擬似コード
params = {"event": ctx.event, "name": ctx.name, **hook_result}  # 基本キーを補完
prompt_text = entry["prompt"].format_map(_SafeDict(params))
```

- **注入は `str.format_map(_SafeDict(...))`**。`_SafeDict` は未定義キーを
  `{key}` のまま残す `dict` サブクラス（`__missing__` 実装）。テンプレートの
  誤記や hook の欠損キーで `KeyError` クラッシュさせない。
- **基本キーを常に補完**: `event` / `name`（必要なら `payload_json`）は kiro-loop 側で
  用意し、hook が返すキーとマージする。hook はドメイン固有キーの抽出に専念できる。
- テンプレート本文が JSON 例など `{ }` を含む場合は `{{` `}}` でエスケープ（`str.format`
  の一般則）。多用するなら `string.Template`（`$key`）へ切替も可 —— 実装時に確定。

### 4.3 モジュールキャッシュ

event_hook の `_load_hook_module(hook_path)` をそのまま流用（mtime 監視、変更時のみ再ロード）。

### 4.4 フック実装イメージ（MR レビュー）

```python
def handle(ctx):
    if "Merge Request" not in ctx.event:
        return None
    a = ctx.payload.get("object_attributes", {})
    if a.get("action") not in ("open", "reopen", "update"):
        return None
    proj = ctx.payload.get("project", {})
    # ── パースして key-value を組み立てて返すだけ（文言はテンプレート側）──
    return {
        "project": proj.get("path_with_namespace", "?"),
        "mr_iid": a.get("iid"),
        "title": a.get("title", ""),
        "url": a.get("url", ""),
        "action": a.get("action", ""),
        "source_branch": a.get("source_branch", ""),
        "target_branch": a.get("target_branch", ""),
    }
```

対応する YAML テンプレート:

```yaml
prompts:
  - name: mr-reviewer
    prompt: |
      [GitLab MR webhook] {project} !{mr_iid}（{action}）
      タイトル: {title}
      {source_branch} → {target_branch}
      URL: {url}
      この MR をレビューして、指摘があれば MR にコメントしてください。
    webhook:
      hook: ~/.kiro/hooks/gitlab-mr-webhook.py
      events: ["Merge Request Hook"]
```

---

## 5. 受信フロー（HTTP ハンドラ）

```
GitLab ──POST /hooks/<name>──▶ WebhookServer (daemon thread)
   │
   ├─ ① メソッド判定           POST 以外 → 405
   ├─ ② ルート解決             scheduler.resolve_webhook_route(name) → 無し → 404
   ├─ ③ 認証                   X-Gitlab-Token ≠ secret → 401
   ├─ ④ イベントフィルタ        X-Gitlab-Event ∉ route.events → 204（無視）
   ├─ ⑤ ボディ読取/JSON パース   サイズ超過 → 413 / パース失敗 → 400
   ├─ ⑥ hook.handle(ctx)→dict   None → 200（ignored）
   ├─ ⑦ テンプレート注入         entry["prompt"].format_map(_SafeDict(params))
   ├─ ⑧ scheduler へ enqueue    対象エントリのキューに完成プロンプトを積む
   └─ ⑨ 202 Accepted を即返す
```

- ② **ルート表は持たない**。`scheduler.resolve_webhook_route(name)` で毎リクエスト
  最新エントリから `{prompt_template, hook, events, secret}` を引く。これで
  `set_entries` によるリロード後もルートが陳腐化しない（#5）。WebhookServer は
  scheduler 参照だけ保持する。
- ③〜⑤ は hook を呼ぶ前のゲート（認証・フィルタ・パース）。hook には
  **正当かつパース済み**のリクエストだけ渡す。
- ⑥ hook は key-value 辞書を返す。⑦ で基本キー（`event`/`name`）を補完しつつ
  エントリの `prompt` テンプレートへ注入して完成プロンプト文にする。
- ⑧ の enqueue はノンブロッキング（キューへ積むだけ、tmux は触らない）。
  テンプレート注入（⑦）を HTTP 側で行うか scheduler 側（dispatch 直前）で行うかは
  実装選択。**HTTP 側で完成文にしてから積む**方が scheduler 変更を辞書非依存に保てる。

---

## 6. 後段への受け渡し — scheduler キュー投函

HTTP スレッドから tmux/セマフォを直接触らず、`PeriodicScheduler` に橋渡しする。

> **重要（実装確認済み）**: 現行 `_run_loop`（`kiro-loop.py:1768`）には event_hook 設計で
> 触れられていた `_queued_prompt` ドレインが **存在しない**（当時「未実装」のまま）。
> よって webhook キューのドレインは **完全新規**で `_run_loop` に挿入する。既存キュー機構に
> 相乗りはできない。

### 6.1 キューはエントリ dict でなく scheduler が name キーで保有する

`_run_loop` は毎周 `entries = [e.copy() for e in self._entries]` と **浅いコピー**を作り、
`_set_entries`（リロード）は `self._entries` を**新 dict で全置換**する。したがって
キューをエントリ dict の中（`entry["_external_queue"]`）に置くと:

- コピー側との参照共有・`_update_entry` の `update()` と競合しうる（#3）。
- **リロードのたびに未処理 webhook が捨てられる**（#4）。

対策として、キューは **scheduler が name をキーに独立保有**する。エントリ全置換の影響を
受けず、ロック境界も 1 か所に閉じる。

```python
# PeriodicScheduler.__init__
self._external_queues: dict[str, collections.deque[str]] = {}   # name -> prompts
# self._lock で保護（既存の entries ロックを共用）

def enqueue_external(self, name: str, prompt_text: str) -> bool:
    """外部（webhook 等）から name 宛の完成プロンプトを積む。
    scheduler スレッドが次サイクルで session 準備 + セマフォ込みで dispatch する。
    戻り値 False = 該当エントリ無し（HTTP 側で 404）。
    """
    with self._lock:
        entry = self._find_entry_by_name(name)   # sanitize 一致
        if entry is None:
            return False
        q = self._external_queues.setdefault(
            name, collections.deque(maxlen=_WEBHOOK_QUEUE_MAX))
        q.append(prompt_text)
        return True
```

- webhook は短時間に複数届き得るため **name ごとに bounded `deque`**（`maxlen` 超過は
  古いものから捨て、警告ログ）。溢れを厳密拒否したいなら enqueue で満杯判定して
  429 を返す選択も可。
- **ドレイン**は `_run_loop` の各エントリ処理内に新規ブロックを 1 か所追加。
  スケジュール発火の判定より前に、`self._lock` 下で `self._external_queues[name]` から
  1 件 `popleft`（無ければ通常のスケジュール処理へ）。取り出したプロンプトを
  既存 `ensure_session` + `_acquire_slot` + `_dispatch_prompt` にそのまま通す。
  セッション未準備・スロット上限なら **積み直して**次サイクルへ（`appendleft`）。
- enqueue（HTTP スレッド）と drain（scheduler スレッド）は **同一 `_lock` 下**でのみ
  deque を操作する。これで #3 の並行性を排除。

### 6.2 なぜ InboxWatcher ではなくスケジューラか

- 「所定の（固定の）名前付きセッション」へ流したい → そのセッションを保有するのは
  スケジューラ。InboxWatcher は `inbox-<id>` のエフェメラルペインを都度作るため、
  「所定のセッション」要件に合わない。
- セマフォ・fresh_context・cwd などエントリ属性を dispatch にそのまま活かせる。

### 6.3 耐久性の限界（#10）

キューは **インメモリのみ**。kiro-loop 再起動・クラッシュで未処理 webhook は失われる
（agent-messaging の inbox がファイル永続だったのとは対照的）。GitLab は 202 を受けた時点で
再送しないため、実質 **at-most-once**。取りこぼしを許容できない重要イベントは、GitLab 側の
手動再送か、`event_hook`（ポーリング）併用で冪等に取りに行く運用を推奨。

---

## 7. 設定スキーマ

### 7.1 グローバル（`kiro-loop.yaml`）

```yaml
webhook:
  enabled: true
  host: 127.0.0.1          # 既定 localhost。外部公開はリバースプロキシ経由を推奨
  port: 8899
  path_prefix: /hooks      # 既定 /hooks
  secret: ""               # X-Gitlab-Token 検証値。空なら検証せず起動時 WARNING
  max_body_bytes: 1048576  # 1MB。超過は 413
```

### 7.2 エントリごと（`prompts[]`）

```yaml
prompts:
  - name: mr-reviewer        # ← POST /hooks/mr-reviewer に対応
    enabled: true
    # webhook 専用エントリはスケジュール不要にできる（下記緩和）
    webhook:
      hook: ~/.kiro/hooks/gitlab-mr-webhook.py
      events: ["Merge Request Hook", "Note Hook"]   # 省略時は全イベント許可
      secret: ""             # ルート個別 secret（省略時グローバル）
```

**スケジュール要件の緩和 + 非スケジュール化（#2）**: 現状 `_set_entries` は cron/interval が
無いエントリをスキップする（`kiro-loop.py:1503,1519-1526`）。`webhook` ブロックを持つ
エントリは cron/interval 無しでも `normalized` に通す。ただし **interval=0 をそのまま
使うと `next_run_at≈now` で `_run_loop` が毎秒空プロンプトを送ってしまう**ため、
webhook 専用（スケジュール無し）エントリは:

- `next_run_at = math.inf`（sentinel）にしてスケジュール発火パスから外す。
- `_run_loop` のスケジュール判定 `now < next_run_at` が常に真 → **自動発火しない**。
  発火するのは §6 のキュードレイン経由（webhook 受信時）のみ。

**セッションの事前起動**: 参照フォークでは `_set_entries` → `sync_entries`
（`kiro-loop.py:1340`）が各エントリに対し `_start_pane` を**先行実行**するため、webhook
専用エントリも設定読み込み時点でペインが用意され、初回 webhook 到達時に session が温まって
いる。**移植注意**: ペインを遅延起動するフォークではこの前提が成り立たず、初回 webhook が
session 準備待ちで一度保留される（§6 のドレインが積み直すので消失はしない）。
event_hook との併用（webhook + interval）も可（その場合は通常どおりスケジュール発火）。

---

## 8. WebhookServer コンポーネント

```
class WebhookServer:
    def __init__(self, scheduler, host, port, path_prefix,
                 secret, max_body_bytes, routes): ...
    def start(self): ...     # ThreadingHTTPServer を daemon thread で serve_forever
    def stop(self):  ...     # server.shutdown() + server_close()
```

- **ルート表は持たない**（#5 対策）。`__init__` は `scheduler` 参照のみ受け取り、
  `do_POST` 内で `scheduler.resolve_webhook_route(name)` を都度呼ぶ。リロードで
  ルートが陳腐化しない。
- ハンドラは `BaseHTTPRequestHandler` を継承し `do_POST` を実装。`log_message` を
  握りつぶして標準の stderr ノイズを抑制。
- hook ロード/キャッシュは `_load_hook_module` を再利用（scheduler 側に置き、
  `resolve_webhook_route` の中でロード or ロード用ヘルパを共有）。
- **起動失敗のハンドリング（#7）**: `HTTPServer((host, port), ...)` が `OSError`
  （`address in use` 等）を投げたら、WARNING を出して **webhook 無効のまま本体は継続**。
  例外を握らないと kiro-loop 全体が起動不能になる。

---

## 9. セキュリティ

- **bind 既定は `127.0.0.1`**。LAN/公開時のみ `0.0.0.0`＋リバースプロキシ（TLS 終端）
  をユーザ責任で。設計上は平文 HTTP（TLS は前段に任せる）。
- **`X-Gitlab-Token` 検証**を既定の関門にする。比較は `hmac.compare_digest`（timing-safe、
  `==` は使わない, #8）。`secret` 未設定なら起動時に WARNING を出し、検証をスキップ（開発用）。
- **到達性の前提（#9）**: localhost bind では GitLab SaaS（gitlab.com）からのインバウンドは
  届かない。自ホスト GitLab が同一 LAN なら `0.0.0.0`+FW、SaaS 連携ならトンネル
  （ngrok/cloudflared 等）かリバースプロキシ経由が必須。設計は「公開・TLS は前段」の割り切り。
- **ボディサイズ上限**（既定 1MB）で簡易 DoS 緩和。
- POST のみ許可。ヘルスチェックが要るなら `GET /hooks/_health` を別途 200 で返す。
- hook 例外・不正 JSON はセッションに波及させない（HTTP レイヤで 4xx/5xx 完結）。
  ただし hook のバグで **500 を返すと GitLab がリトライ→毎回同じ例外で嵐**になる。
  hook 例外は 500 でなく **200/204 で握って WARNING ログ**にする方が安全（#11、要判断）。

---

## 10. コード変更範囲（見積り）

| ファイル | 変更 | 追加行数 | 既存変更 |
|---------|------|---------|---------|
| `kiro-loop.py` | 定数（`_WEBHOOK_*`）追加 | +3 | 0 |
| `kiro-loop.py` | `WebhookServer` + ハンドラ クラス | +~120 | 0 |
| `kiro-loop.py` | `_SafeDict`（未定義キー保持）+ テンプレート注入 | +~10 | 0 |
| `kiro-loop.py` | `enqueue_external` / `_find_entry_by_name` / `resolve_webhook_route` + `_external_queues` | +~45 | 0 |
| `kiro-loop.py` | `_run_loop` に外部キュー ドレイン挿入 | +~10 | 0 |
| `kiro-loop.py` | `_set_entries` に `webhook` 正規化 + 非スケジュール sentinel | +~8 | ~2 |
| `kiro-loop.py` | `main()` に WebhookServer 起動、`_cleanup` に stop 配線 | +~18 | ~2 |
| `kiro-loop.yaml.example` | `webhook:` セクション追記 | +~12 | 0 |
| `hooks/gitlab-mr-webhook.py` | 新規フック例 | +~40 | — |
| `hooks/gitlab-push-webhook.py` | 新規フック例（任意） | +~30 | — |

既存メソッドへの変更は `_set_entries` のスケジュール緩和のみ（他は挿入・新規）。

---

## 11. 主要な設計判断（確認したい分岐）

| # | 判断点 | 確定 | 備考 |
|---|--------|------|------|
| A | `<name>` の宛先 | **既存 prompts エントリ（固定セッション）へ scheduler 経由で送る** | 「所定のセッション」= エントリのペイン |
| B | 後段の変換 | **hook は `dict` を返し、エントリの `prompt` テンプレートへ `{key}` 注入** | パース（hook）と文言（テンプレート）を分離 |
| C | 起動条件 | `webhook.enabled` かつ port 指定時のみ | 常時起動はしない |

A・B はユーザー確認済み（2026-07-09）。本設計はこの確定を反映済み。

---

## 12. 実装時の注意点

- **ThreadingHTTPServer の並行性**: hook は複数スレッドから同時に呼ばれ得る。
  hook 側で状態を持つ場合はロックが必要。ステートレス設計を推奨。
- **enqueue のスレッド安全性**: `_external_queues` の enqueue/drain は必ず scheduler の
  `_lock` 下で行う（#3）。
- **停止処理の配線（#6）**: `_cleanup`（`kiro-loop.py:2111`）は現状 scheduler/slot_monitor/
  session_mgr のみ stop し、**InboxWatcher すら stop していない**（daemon 任せ）。WebhookServer
  も `serve_forever` は daemon で放置すると `shutdown()`/`server_close()` されず、ソケットが
  TIME_WAIT に残り即再起動で `address in use`。`_webhook_server_ref` を追加して `_cleanup`/
  `_signal_handler` で `stop()` を呼ぶ（ついでに inbox_watcher の stop 漏れも直すと一貫）。
- **ポート衝突**: 同一ホストで複数 kiro-loop（別 cwd/インスタンス）を動かす運用がある
  （`_find_running_daemon`）。webhook を使うインスタンスは **port を明示・分離**する。
  既定ポートを固定配布すると衝突が常態化するため、`webhook.enabled` 明示時のみ起動し
  port は必須扱いにする。
- **再読込追従**: ルート表を持たず毎リクエスト `resolve_webhook_route` で解決するため、
  `set_entries` 後のルートは自動追従（#5 解決済み）。キューは name キーで別管理のため
  リロードで消えない（#4 解決済み）。ただし enable/disable/リネームでキューが宙に浮く
  余地はあるので、drain 時に対応エントリ不在なら破棄+警告する。
- **DESIGN.md 追記**: 実装後、`tools/kiro-loop/DESIGN.md` に `webhook` オプションと
  `WebhookServer` を追記する。
