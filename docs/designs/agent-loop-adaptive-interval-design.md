# agent-loop 動的インターバル（adaptive interval）設計案


> **由来**: `docs/designs/kiro-loop-adaptive-interval-design.md` を置換せずクローンし、`agent-loop` 名称へ改称した系統。
> 改称方針: [`docs/designs/agent-tools-rename-design.md`](agent-tools-rename-design.md)。

> 作成日: 2026-07-05
> 対象ファイル: `tools/agent-loop/agent-loop.py`, `tools/agent-loop/hooks/*.py`
> 関連: `docs/designs/agent-loop-event-hook-design.md`（event_hook 実装の前提）

---

## 1. 背景・目的

現状の agent-loop は各プロンプトエントリを **固定の `interval_minutes`**（または `cron`）で発火する。
`_next_run_at_for_entry()` が常に `interval_minutes * 60` 秒後を返すだけの単純なスケジューラである。

固定インターバルには次の弱点がある。

| 状況 | 5 分固定の挙動 | 問題 |
|---|---|---|
| Issue/MR が活発に動いている | 5 分ごと | 反応が遅い。1〜2 分で拾いたい |
| 深夜・週末・連休で無風 | 5 分ごと | GitLab API を **288 回/日** 無駄叩き。負荷・レート制限・ログノイズ |
| GitLab が一時的にダウン | 5 分ごとに失敗 | エラーで無駄叩き、復帰検知も遅い |

**目的**: フックや本体スケジューラが「ログ・観測データ」から**次に動かすべきタイミングを動的に決める**。

**方針（本タスクの制約）**:

1. **LLM を使わない** — 適応の知能はすべてヒューリスティクス（観測データ上の単純な統計・状態機械）で実現する。
2. **詰まらせない（no-stall）** — イベントが来たら即座に最小インターバルへ復帰し、取りこぼさない。バックオフ中に本物のイベントを長時間見逃す事故を作らない。
3. **GitLab サーバ負荷を減らす** — 無風時はインターバルを幾何級数的に伸ばし、ポーリング回数を大幅削減する。**追加の GitLab リクエストを一切増やさない**ことを設計の絶対条件とする。

---

## 2. 設計方針（全体像）

### 2.1 二層構成

「hooks や本体の entry で決める」という要求に対し、**コア層とフック層の 2 段**で構成する。どちらか一方だけでも成立し、併用すると精度が上がる。

| 層 | 決定主体 | 使う情報 | フック改変 | 役割 |
|---|---|---|---|---|
| **Layer 1: コア適応** | `agent-loop.py` の scheduler | `check()` の hit/miss（＝プロンプトを送ったか）だけ | 不要 | 既定のバックオフ。全エントリに無改造で効く |
| **Layer 2: フック明示** | `event_hook` の `check()` | GitLab から**既に取得済み**のデータ（backlog 数・最終更新時刻・ラベル） | 戻り値を拡張 | データを見た上でインターバルを上書き（例: critical は即・空 backlog は max） |

コア層は「送信したか否か」という既存シグナルだけで動くため、**フックを一切変えずに導入できる**。
フック層は、フックがどのみち 1 回叩いている GitLab レスポンスを再利用して精度を上げる（**追加リクエスト 0**）。

### 2.2 対象と非対象

- **対象**: `interval_minutes` で駆動するエントリ。
- **非対象**: `cron` エントリ（固定スケジュールが意味なので触らない）。`adaptive` 未設定エントリは**完全に従来挙動**（後方互換）。

### 2.3 負荷削減の肝 — 「観測はタダのデータだけ」

適応判断のために **新たな GitLab API を呼ばない**。判断材料は以下に限定する。

- `check()` が返した hit/miss/error（フックが既に叩いた結果の副産物）。
- event_hook の状態ファイル（`gitlab-issue-state.json` 等の `iid -> updated_at`）＝ GitLab 活動の**ローカルログ**。
- 本体ログ `~/.kiro/agent-loop.log`（補助）。
- 適応状態ファイル（後述）に貯めた**過去の発火履歴**。

これにより「賢く決める」処理そのものがサーバ負荷を生まない。

---

## 3. 適応アルゴリズム（Layer 1: コア）

TCP の AIMD ではなく、**イベント駆動に適した「miss で乗算増加・hit で即リセット」**を採用する。無風時に指数的に伸ばしつつ、イベント到来時は 1 発で最小へ戻すことで no-stall を担保する。

### 3.1 状態遷移

各エントリは per-entry の適応状態 `interval`（現在の分）を持つ。発火結果に応じて次を更新する。

```
                       ┌──────────── hit ───────────┐
                       │  (check()が str を返した／  │
                       ▼   通常 prompt を送信した)    │
   ┌────────────────────────┐                        │
   │ interval = min_interval │◀───────────────────────┘
   └───────────┬────────────┘
               │ miss (check()が None、または status="miss")
               ▼
   interval = min(interval * backoff_factor, max_interval)
               │
               │ error (GitLab 到達不可 / status="error")
               ▼
   interval は据え置き、次回は retry_interval（短時間）で再挑戦
```

次回発火時刻:

```
next_run_at = now + interval * 60 * (1 ± jitter)
```

### 3.2 3 つの結果クラス

| 結果 | 判定 | インターバル更新 | 意図 |
|---|---|---|---|
| **hit** | 実際にプロンプトを送信できた（本物のイベント） | `min_interval` へリセット | 活発 → 最速で追従（no-stall） |
| **miss** | 送るべきものが無かった | `× backoff_factor`（`max_interval` で頭打ち） | 無風 → 幾何級数的に間引き（負荷減） |
| **error** | GitLab 不達・タイムアウト | 据え置き + `retry_interval` で短時間リトライ | 障害で max へ吹き飛ばして復帰を遅らせない |

### 3.3 なぜ error を分離するか（no-stall の要）

現状フックは「更新なし」も「ネットワークエラー」も一様に `None` を返す。
これを両方 miss として扱うと、**GitLab が数分落ちただけでインターバルが max（例 120 分）まで膨らみ、復帰後に本物のイベントを 2 時間見逃す**。これは「詰まり」の典型。
そこで error を独立クラスにし、**バックオフさせず短時間リトライ**する。実装は §6 のフック戻り値拡張で `status:"error"` を伝える。

### 3.4 fallback（ランダム送信）との整合

`event_hook_fallback: true` は「更新が無くてもランダムに 1 件送る」挙動で、放っておくと**毎サイクル hit 扱い**になりバックオフが効かない。
適応を有効にする場合、**フォールバック送信は「送信したが本物のイベントではない」＝ miss として扱う**。
これは §6 の構造化戻り値で `{"prompt": <fallback text>, "status": "miss"}` と返すことで自然に表現できる（＝「プロンプトは送るが、ポーリング頻度は上げない」を分離できる）。

### 3.5 jitter — thundering herd 回避（詰まり対策）

複数の agent-loop デーモン（複数ワークスペース）が同じ GitLab を見ている場合、全員が同時刻に max→min へ揃うと同時ポーリングが刺さる。
`next_run_at` に `±jitter`（既定 ±10%）の乱数を掛け、発火時刻を分散させる。

---

## 4. データソース（＝「ログ」）と永続化

### 4.1 適応状態ファイル

`~/.kiro/loop-adaptive/<entry-id>.json` にエントリ単位で保存する。

```json
{
  "id": "…uuid…",
  "interval_minutes": 48.0,        // 現在の適応インターバル
  "last_hit_at": 1751690000.0,     // 最後に本物イベントを拾った時刻
  "consecutive_misses": 5,         // 連続 miss 回数
  "recent_gaps_ewma_min": 37.4,    // 本物イベント間隔の指数移動平均（分）
  "updated_at": 1751693600.0
}
```

**永続化する理由（負荷削減に直結）**: プロセス内メモリだけだと**再起動のたびに min へリセット**し、無風の深夜に agent-loop が再起動しただけで GitLab を叩き直す。ファイルに退避しておけば、再起動後も伸びたインターバルを引き継げる（ただし `last_hit_at` から一定以上経っていれば安全側で 1 段だけ縮める、等のガードは任意）。

### 4.2 EWMA によるインターバル上限の自己調整（任意・高度化）

`recent_gaps_ewma_min` は「本物イベントの平均到来間隔」の推定。hit のたびに

```
gap = now - last_hit_at
ewma = alpha * gap + (1 - alpha) * ewma       # alpha 既定 0.3
```

で更新する。これを使うと `max_interval` を「観測された平均間隔の 2 倍まで」等に**動的にクランプ**でき、イベントが平均 10 分間隔で来るエントリを 120 分まで伸ばして取りこぼす、といった過剰バックオフを防げる。初期実装では固定 `max_interval` で十分。EWMA は Layer 2（フック）で使うほうが素直（§6）。

### 4.3 GitLab 活動ログの再利用

event_hook の状態ファイル（`iid -> updated_at`）は、GitLab の更新履歴そのもの。フック層はこれを見て「直近 N 分に更新された対象があるか」を**追加リクエストなし**で判断し、あるなら min を要求する（§6）。

---

## 5. 設定スキーマ

`prompts[].adaptive` を新設する。**未指定なら従来の固定インターバル**（後方互換）。

```yaml
prompts:
  - name: "GitLab Issue ワーカー (event_hook)"
    event_hook: ~/sandbox/tools/agent-loop/hooks/gitlab-issue-hook.py
    interval_minutes: 5          # adaptive 有効時は「初期値」として使う
    adaptive:
      enabled: true
      min_interval_minutes: 2    # hit 時に戻る下限（既定: interval_minutes）
      max_interval_minutes: 120  # miss バックオフの上限
      backoff_factor: 1.6        # miss 1 回ごとの乗数（>1.0）
      retry_interval_minutes: 1  # error 時の短時間リトライ（既定: min）
      jitter: 0.1                # 発火時刻の揺らぎ ±10%
    enabled: true
```

グローバル既定を `~/.kiro/agent-loop.yaml` トップレベルに置けるようにしてもよい（`max_concurrent` 等と同じ読み方）:

```yaml
adaptive_defaults:
  min_interval_minutes: 2
  max_interval_minutes: 120
  backoff_factor: 1.6
  jitter: 0.1
```

**バリデーション**: `min < max`、`backoff_factor > 1.0`、`min >= 1`。不正なら WARNING を出して当該エントリは adaptive 無効（固定へフォールバック）。

---

## 6. フックインターフェース拡張（Layer 2）

### 6.1 `check()` の戻り値を後方互換で拡張

現行 `check() -> str | None` に、**dict 戻り値**を追加する。既存フックはそのまま動く。

```python
def check() -> str | None | dict:
    """
    従来:
      str  → そのテキストを送信（hit）
      None → 今回スキップ（miss）

    追加 (dict):
      {
        "prompt": str | None,          # 送信テキスト。None ならスキップ
        "status": "hit" | "miss" | "error",  # 適応の結果クラス（省略時は prompt から推定）
        "next_interval_minutes": float | None # 明示指定。コア適応より優先
      }
    """
```

コア側の解釈規則:

- `str` を返した → `{"prompt": str, "status": "hit"}` とみなす。
- `None` を返した → `{"prompt": None, "status": "miss"}` とみなす。
- `dict` を返した → そのまま使う。`status` 省略時は `prompt` の有無で hit/miss を推定。
- `next_interval_minutes` があれば **コアの AIMD を上書き**（`min`〜`max` にクランプ）。無ければコアが §3 で計算。

### 6.2 フックが GitLab データからインターバルを決める例

フックは `check()` の中で**既に取得した** issues/MRs を使い、追加リクエストなしで次を返せる。

```python
def check():
    issues = _get_issues()            # 既存の 1 リクエスト
    if issues is None:
        return {"prompt": None, "status": "error"}   # ← 障害。バックオフさせない

    prev = _load_state(); curr = {...}
    changed = [i for i in issues if prev.get(id) != i["updated_at"]]
    _save_state(curr)

    now = _utcnow()
    if changed:
        # 本物の更新 → 最速で追従
        return {"prompt": _format(changed[0]), "status": "hit",
                "next_interval_minutes": 2}

    if not issues:
        # backlog 空 → 深くバックオフ
        return {"prompt": None, "status": "miss", "next_interval_minutes": 120}

    # 直近 15 分以内に動いた対象がある → まだ活発。短め維持
    fresh = any(_age_minutes(i["updated_at"]) < 15 for i in issues)
    if fresh:
        return {"prompt": None, "status": "miss", "next_interval_minutes": 5}

    # critical ラベルが backlog にある → 空きを見て早めに拾う
    if any("priority:critical" in i.get("labels", []) for i in issues):
        return {"prompt": None, "status": "miss", "next_interval_minutes": 3}

    # 完全に無風 → コアの AIMD に任せる（next_interval_minutes を返さない）
    if fallback_enabled:
        return {"prompt": _format(random.choice(issues)), "status": "miss"}  # 送るが miss
    return {"prompt": None, "status": "miss"}
```

ポイント:

- `status:"error"` により**障害時にバックオフさせない**（no-stall）。
- fallback 送信を `status:"miss"` にすることで「送信はするがポーリング頻度は上げない」を実現（§3.4）。
- `next_interval_minutes` を返さなければコアの AIMD が働く。フックは**必要なときだけ**上書きする。

---

## 7. コード変更点（`agent-loop.py`）

既存 event_hook 実装の「挿入だけ・既存メソッド不変更」の思想を踏襲する。中心は `_next_run_at_for_entry()` と `_run_loop()` の hit/miss 分岐。

### 7.1 新規クラス `AdaptiveInterval`（状態保持＋永続化）

```python
class AdaptiveInterval:
    """エントリ単位の適応インターバル状態。~/.kiro/loop-adaptive/<id>.json に永続化。"""
    def __init__(self, entry_id, cfg):        # cfg: min/max/backoff/retry/jitter
        ...
        self._load()                          # 起動時に前回値を復元
    def record_hit(self) -> None:             # interval = min, last_hit_at 更新, EWMA 更新
    def record_miss(self) -> None:            # interval = min(interval*backoff, max)
    def record_error(self) -> None:           # interval 据え置き（retry は next_delay 側で）
    def override(self, minutes: float) -> None:  # フックの明示値でクランプ設定
    def next_delay_seconds(self, *, error: bool) -> float:
        base = self._retry if error else self._interval
        return base * 60 * self._apply_jitter()
```

### 7.2 `_set_entries()` — `adaptive` 設定の正規化（追加のみ）

normalized dict に `adaptive`（正規化済み cfg または None）を持たせ、`AdaptiveInterval` を生成して `entry["_adaptive"]` に格納。`min` の既定は `interval_minutes`。バリデーション失敗時は None（従来挙動）。

### 7.3 `_next_run_at_for_entry()` — 適応対応（1 メソッド差し替え）

```python
def _next_run_at_for_entry(self, entry, *, result="miss"):
    cron_str = entry.get("cron")
    if cron_str:                              # cron は従来通り（非対象）
        return CronExpression(cron_str).next_run(...).timestamp()

    adaptive = entry.get("_adaptive")
    if adaptive is None:                      # 従来の固定挙動（後方互換）
        return time.time() + max(int(entry.get("interval_minutes", 1)), 1) * 60

    if result == "hit":   adaptive.record_hit()
    elif result == "error": adaptive.record_error()
    else:                 adaptive.record_miss()
    return time.time() + adaptive.next_delay_seconds(error=(result == "error"))
```

`result` 引数を足すだけで、既存呼び出し（引数なし）は `miss` 既定として従来と同じ計算に落ちる。

### 7.4 `_call_hook_check()` — dict 戻り値の解釈（拡張）

戻り値を `(prompt: str|None, status: str, override: float|None)` の 3 つ組に正規化して返すよう変更。`str`/`None`/`dict` を §6.1 の規則でマップ。`override` があれば `entry["_adaptive"].override(...)` を呼ぶ。

### 7.5 `_run_loop()` — hit/miss/error を `_next_run_at_for_entry` へ伝える

現状 `_run_loop` は既に「`check()` が None ならスキップ、str なら送信」の分岐を持つ。ここを result 付きに変えるだけ。

```python
if entry.get("event_hook"):
    prompt_text, status, override = self._call_hook_check(entry)   # 拡張
    if override is not None:
        entry["_adaptive"] and entry["_adaptive"].override(override)
    if prompt_text is None:
        self._update_entry(prompt_id,
            next_run_at=self._next_run_at_for_entry(entry, result=status))  # miss/error
        continue
    entry["prompt"] = prompt_text
# … 送信成功後 …
self._update_entry(entry["id"],
    next_run_at=self._next_run_at_for_entry(entry, result=status_or_hit))
```

event_hook を持たない素の prompt エントリは、送信＝常に hit。`adaptive` を付けても実質バックオフしない（毎回 min）ので、adaptive は event_hook 併用を推奨とドキュメント明記する。

### 変更量サマリ

| 対象 | 種別 | 目安 |
|---|---|---|
| `AdaptiveInterval` クラス新規 | 追加 | +80 |
| `_set_entries()` 正規化 | 追加 | +25 |
| `_next_run_at_for_entry()` | 差し替え | ±15 |
| `_call_hook_check()` 戻り値拡張 | 変更 | ±20 |
| `_run_loop()` result 伝播 | 変更 | ±10 |
| 同梱フック 2 本を dict 戻り値へ更新 | 変更 | 各 +20 |
| `agent-loop.yaml.example` / `README.md` / `DESIGN.md` | ドキュメント | — |

---

## 8. 「詰まらせない」ための不変条件（設計チェックリスト）

1. **hit で即 min 復帰** — バックオフ中でもイベント 1 発で最速へ戻る。
2. **error はバックオフしない** — 障害を無風と誤認して max へ飛ばさない。短時間リトライ。
3. **min の下限は 1 分**、`min < max`、`backoff > 1.0` をバリデーションで保証。
4. **セマフォとの分離** — 「前回が未完了（slot busy）」による +30 秒延期は従来通り。適応バックオフは **miss パスのみ**に適用し、busy 由来の遅延と二重計上しない。
5. **jitter** で複数デーモンの同時ポーリングを分散。
6. **cron は不可侵** — 固定スケジュールの意味を壊さない。
7. **永続状態の復帰ガード** — 再起動時、前回から時間が経ちすぎていれば 1 段だけ縮めて安全側に寄せる（任意）。

---

## 9. GitLab 負荷の定量イメージ

`min=2, max=120, backoff=1.6` の event_hook エントリを 1 本、無風の週末に走らせた場合の 1 日あたりリクエスト数（フックの `list-issues` 呼び出し回数）:

| 方式 | 無風 24h のリクエスト数 | 備考 |
|---|---|---|
| 固定 5 分 | **288** | 常時一定 |
| 適応（無風で 2→120 分へ収束） | **約 20〜30** | 立ち上がり数回 + 120 分間隔 |

イベント到来時は hit で即 2 分へ戻るため、**平時の反応速度はむしろ向上**しつつ、アイドル負荷を約 1/10 に削減できる。複数ワークスペース・複数エントリ運用ではこの差が線形に効く。

---

## 10. 後方互換・移行

- `adaptive` 未指定エントリ・既存 YAML は**挙動不変**。
- 既存フック（`str | None` を返す）は**無改変で動作**。dict 戻り値はオプトイン。
- `_next_run_at_for_entry(result=...)` は既定 `miss` で従来計算に一致。
- 段階導入: (1) コア AIMD だけ有効化 → (2) 同梱フックを dict 戻り値へ更新 → (3) EWMA 動的クランプ等の高度化。

---

## 11. テスト方針

- **`AdaptiveInterval` 単体**: hit→min、miss→乗算増加と max クランプ、error→据え置き+retry、jitter レンジ、永続化ラウンドトリップ。
- **`_next_run_at_for_entry`**: cron 非対象・adaptive None の後方互換・result 別の next_run_at。
- **`_call_hook_check`**: str/None/dict の正規化、不正 status のフォールバック、override クランプ。
- **結合（フェイク時計）**: 無風で幾何級数的に伸びること、hit 注入で 1 発復帰すること、error 連発で max へ行かないこと。
- 既存 `tests/test_kiro_loop.py` に adaptive ケースを追加。

---

## 12. 既存ドキュメントの更新

実装時に以下も更新する。

- `tools/agent-loop/DESIGN.md` §3.4「PeriodicScheduler」に `adaptive` / `AdaptiveInterval` / result 伝播を追記。「新しいプロンプトオプションを追加する」に adaptive を追記。
- `tools/agent-loop/agent-loop.yaml.example` に `adaptive:` ブロックのサンプルを追記。
- `tools/agent-loop/README.md` に動的インターバルの概要と設定例。
- 同梱フック（`gitlab-issue-hook.py` / `gitlab-mr-hook.py`）の docstring に dict 戻り値と status の規約を追記。
