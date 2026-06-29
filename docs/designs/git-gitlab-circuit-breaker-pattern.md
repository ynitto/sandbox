# git/GitLab 横断サーキットブレーカー + 監視 パターン設計書

> 作成日: 2026-06-29
> 対象ブランチ: `claude/kiro-worktree-cost-reduction-8dlgf9`
> 初出の適用先: `tools/gitguard/`（`gitguard.py` / `git-guard`）, `.github/skills/gitlab-idd/scripts/gl.py`
> 位置づけ: **特定ツールに依存しない汎用パターン**。git/GitLab（や任意の外部エンドポイント）へ
> アクセスするあらゆるツール・スキルへ転用できる。

---

## 0. このドキュメントの使い方

git リモート（GitLab 等）へアクセスするツールが多数ある一方、リトライ・バックオフ・障害時の
fail-fast・監視は**各ツールがバラバラに実装、または未実装**になりがち。本パターンは、それらを
**ホスト内で 1 つのサーキットブレーカーと 1 本のイベントログに束ねる**横断機構を定義する。

転用したい人は **§3 の不変条件**と **§5 の API 契約**だけ守れば、§7 のチェックリストで自分の
ツールへ移植できる。実装の実体は `tools/gitguard/` にある（標準ライブラリのみ・依存なし）。

関連: clone コスト削減は [git-worktree-cache-pattern.md](git-worktree-cache-pattern.md)。本パターンは
そのアクセスの「止め方・観測の仕方」を担う（直交する別レイヤ）。

---

## 1. 背景・解決する問題

- 同一ホスト（GitLab）が一時的に不調（429/5xx/接続不可）のとき、各ツールが独立にリトライを
  繰り返し、**復旧前のサーバへ無駄打ちを浴びせて傷口を広げる**（thundering herd）。
- どのツールがどのホストにどれだけアクセスし、どこで失敗しているかの**横断的な可視性が無い**。
- リトライで直らない障害（ホスト断・レート制限）でも各タスクが個別にタイムアウトまで粘り、
  **全体のスループットが落ちる**。

→ ホスト単位でアクセスの健全性を 1 箇所に集約し、**壊れている間は素早く諦め（fail-fast）、
回復したら 1 本のプローブで戻す**。同時に**全アクセスを 1 本のログで観測可能**にする。

---

## 2. アーキテクチャ

```
   各ツール/スキル（Python import / guard() / git-guard CLI）
        │  decide(endpoint)            ┌──────── ホスト共有状態（$GITGUARD_DIR）────────┐
        ├─────────────────────────────▶│  state/<sha1(endpoint)>.json  … ブレーカー状態  │
        │  ← allowed / blocked          │  locks/<sha1(endpoint)>.lock  … flock 直列化     │
        │  report(endpoint, outcome)    │  events.ndjson                … 監視イベント     │
        └─────────────────────────────▶└──────────────────────────────────────────────────┘
                                              ▲ status / stats が読む
```

- **エンドポイント**: アクセス先の単位 = `kind:host`（例 `git:gitlab.example.com` /
  `gitlab:gitlab.example.com`）。URL/リモートから導出。
- **ブレーカー状態**: エンドポイント毎の `state / consecutive / opened_at / probe_inflight`。
  `flock` + JSON でホスト共有 → **全プロセス・全ツールで 1 つのブレーカー**を共有。
- **監視ログ**: アクセス毎に 1 行 NDJSON 追記。集計は後段（`stats`）で行う。

### 状態機械

```
            連続インフラ障害が threshold 回
   ┌────────┐ ───────────────────────────────▶ ┌──────┐
   │ CLOSED │                                   │ OPEN │
   └────────┘ ◀───────────────────────────────  └──────┘
        ▲          プローブ成功                      │ cooldown 経過
        │                                            ▼
        │           ┌────────────┐  プローブ失敗     │
        └────────── │ HALF_OPEN  │ ─────────────────▶┘
          成功で復帰 └────────────┘
                    （プローブは 1 本だけ通す／他は短絡）
```

- **CLOSED**: 通常。インフラ障害が連続 `threshold` 回（`window` 秒内）で **OPEN**。
- **OPEN**: `cooldown` 秒は短絡（enforce 時は即失敗、監視のみ時は通すが記録）。経過で **HALF_OPEN**。
- **HALF_OPEN**: **プローブ 1 本だけ**通す（他は短絡）。成功→CLOSED、失敗→OPEN（再計時）。

---

## 3. 不変条件（転用時に必ず守る）

### INV-1: トリップはインフラ/一過性障害のみ（誤爆しない）
- 数える失敗 = 接続不可・DNS・timeout・429・408・5xx・407(proxy)・`remote end hung up` 等。
- **数えない失敗** = git の正当な非 0 終了（マージ衝突・テスト失敗）、404/422 等の 4xx、認証失敗。
  これらは**リトライで直らない／呼び出し側起因**で、ブレーカーを開くと正常なホストを巻き込む。
- 分類はコード/stderr/HTTP ステータスから決定的に行う（`classify_git` / `http_status`）。

### INV-2: 状態はホスト共有・直列化
- ブレーカー状態の読み書きは **エンドポイント毎の `flock` 下**で行う（並行更新の競合排除）。
- 状態の時刻は **wall-clock**（プロセスを跨ぐため monotonic は使わない）。
- 監視ログの追記失敗・状態の破損は**本処理を止めない**（best-effort、壊れていれば既定値から再開）。

### INV-3: 既定は監視のみ（導入で既存挙動を壊さない）
- 既定はブロックしない（記録のみ）。`GITGUARD_ENFORCE=1` で初めて fail-fast。
- 横断導入の鉄則: **まず観測 → しきい値を実データで調整 → enforce 有効化**。
- `GITGUARD_DISABLE=1` で完全素通り（緊急時の避難弁）。

---

## 4. 監視（オブザーバビリティ）

- イベント 1 行: `ts / endpoint / op / outcome(success|infra_fail|app_fail|blocked) /
  latency_ms / error / state / pid`。
- `status`: エンドポイント毎の現在状態（open のものと cooldown 残・直近エラー）。
- `stats`: エンドポイント毎の件数・**infra_rate**（インフラ障害率）・レイテンシ p50/p95。
- ログは追記専用 NDJSON なので、外部の収集（fluent-bit 等）にもそのまま流せる。ローテーションは
  運用側（size/age）で行う想定。

---

## 5. API 契約（最小インターフェース）

転用先はこの 3 つのいずれかを使えば乗れる（名前は実装準拠）。

```
# (a) 任意操作を包む最も疎結合な経路
with guard(endpoint, op, enforce=None) as g:
    ...                       # 例外（接続/timeout 系）は自動で INFRA 記録 + 再送出
    g.success() / g.infra(err) / g.app(err) / g.http_status(code)
    # 何も呼ばずに抜ければ成功扱い。open & enforce なら __enter__ で CircuitOpenError。

# (b) git ラッパ（subprocess.run の drop-in に近い）
git(args, cwd=None, remote=None, timeout=..., enforce=None) -> CompletedProcess
    # ネットワーク操作(clone/fetch/pull/push/ls-remote/...)だけブレーカーを通す。
    # 失敗も CompletedProcess で返す＝呼び出し側の既存リトライを壊さない。

# (c) GitLab REST ラッパ
gitlab_api(host, method, path, token=None, data=None, params=None) -> (status, json)
```

下回り: `decide(endpoint) -> (allowed, state)` と `report(endpoint, outcome, op, latency, error)` が
本体。`guard`/`git`/`gitlab_api` はこの 2 つの薄いラッパ。

---

## 6. なぜこの設計か（代替案との比較）

| 案 | 横断性 | git | GitLab API | リスク | 採否 |
|----|-------|-----|-----------|--------|------|
| 共有ライブラリ + CLI シム（本案）| 高（段階採用）| ○ | ○ | 低 | **採用** |
| `git` を PATH で透過置換 | 最高（無改変）| ○ | ✗（REST 不可）| PATH 差替が脆い | 不採用 |
| 各ツールに個別実装 | 低 | △ | △ | 重複・ドリフト | 不採用 |
| 送信プロキシ（network層）| 最高 | ○ | ○ | インフラ重・環境依存 | 将来 |

ライブラリ + CLI なら **git も REST も**捕捉でき、Python は `import`・shell は `git-guard` で
**段階的に**（既存を壊さず）横断採用できる。

---

## 7. 採用チェックリスト（他ツール/スキルへの移植手順）

1. [ ] そのツールの git/HTTP アクセスを **`guard()` / `git()` / `gitlab_api()` のどれで包むか**決める。
2. [ ] エンドポイントキーを `endpoint_for_url(url, kind)` で導出（host 単位に正規化）。
3. [ ] **INV-1**: 失敗分類が「インフラのみトリップ」になっているか（4xx/衝突/認証を数えていないか）。
4. [ ] **INV-3**: まず監視のみで入れる。`GITGUARD_ENFORCE` は実データを見てから。
5. [ ] 既存のリトライ/バックオフは残してよい（ブレーカーは上位の安全弁）。二重に数えないよう
       「最終結果」を 1 回だけ `report` する（リトライの各失敗を個別に数えない）。
6. [ ] 依存を増やさないため、`gitguard` は **best-effort import**（無ければ素通り）にしてもよい。
7. [ ] 運用に `git-guard status` / `stats` を組み込む（cron 監視・アラート）。

---

## 8. 適用先メモ

- **`tools/gitguard/`**: 本パターンの実体（`gitguard.py` ライブラリ + `git-guard` CLI + テスト）。
- **`gl.py`（gitlab-idd）**: REST 呼び出し `api()`/`api_list()` を best-effort で `guard()` 経由にし、
  gitlab-idd 配下の全 GitLab アクセスをブレーカー + 監視へ載せる参照採用。
- **kiro-flow / kiro-autonomous**: ネットワーク git（ミラー clone/fetch・workspace push・verify 取得）を
  `gitguard.git()` 経由にすると、worktree キャッシュ（別パターン）と合わせて
  「負荷を減らし・壊れたら素早く止め・全アクセスを観測する」が揃う（段階採用）。

---

## 9. 既知の制約・非目標

- **回数の上限制御（レート制限そのもの）は目的でない**。本機構は「壊れている間止める」であって
  「平常時の流量制御」ではない（必要なら別途トークンバケットを足す）。
- ホスト共有のため**マルチホスト分散**では各ホストに別ブレーカーができる（それで十分。共有 FS 上の
  状態共有は `flock` セマンティクス的に非推奨）。
- 認証情報は持たない（git credential / トークンは呼び出し側に従う）。
- ブレーカーは**インフラ障害の連鎖**を止める安全弁であり、個々のリトライ戦略を置き換えない。
