# gitguard — git/GitLab アクセスの横断サーキットブレーカー + 監視

特定のツール/スキルに依存せず、**ホスト内のすべての git/GitLab アクセスを 1 つのブレーカーと
1 本のイベントログで束ねる**小さなユーティリティ（標準ライブラリのみ・依存なし）。

設計の詳細・他ツールへの転用方法は
[docs/designs/git-gitlab-circuit-breaker-pattern.md](../../docs/designs/git-gitlab-circuit-breaker-pattern.md)。

## なにをするか

- **サーキットブレーカー**: エンドポイント（git リモートホスト / GitLab ホスト）単位で
  `closed → open → half_open` を管理。連続したインフラ障害でブレーカーを開き、障害中の無駄打ちを
  止める（cooldown 後にプローブ 1 本で復帰判定）。状態はホスト共有（`flock` + JSON）なので
  **全プロセス・全ツールで 1 つのブレーカーを共有**する。
- **監視**: アクセスのたびに NDJSON で `時刻 / endpoint / op / 結果 / レイテンシ / エラー種別 /
  ブレーカー状態` を 1 行追記。`status` / `stats` で開いているブレーカー・エラー率・レイテンシを一覧。

### 誤爆しない設計（重要）

ブレーカーを開くのは **インフラ/一過性障害のみ**：接続不可・DNS・timeout・429・5xx・407(proxy)。
**正当な失敗は数えない**＝git のマージ衝突やテスト失敗の非 0 終了、404/422 等の 4xx、認証失敗は
ブレーカーに影響しない（リトライで直らない／呼び出し側起因のため）。

## 使い方

### 1) Python から（薄い差し替え）

```python
import gitguard

# subprocess.run(["git", ...]) の drop-in に近い。失敗もそのまま CompletedProcess で返す。
p = gitguard.git(["clone", url, dest])

# GitLab REST（status, parsed_json を返す）
status, body = gitguard.gitlab_api("gitlab.example.com", "GET", "/projects/123", token=tok)
```

### 2) 任意の操作を包む（最も疎結合）

既存の独自 HTTP 呼び出し等は `guard()` で包むだけでブレーカー + 監視に乗る。

```python
ep = gitguard.endpoint_for_url("https://gitlab.example.com", "gitlab")
with gitguard.guard(ep, "GET /issues") as g:
    resp = my_existing_call()      # 例外（接続不可/timeout）は自動で INFRA 記録 + 再送出
    g.http_status(resp.status)     # or g.success() / g.infra() / g.app()
```

### 3) shell / 非 Python から（CLI シム）

```sh
git-guard clone <url> <dest>          # git をブレーカー経由で透過実行（終了コード/出力もそのまま）
git-guard fetch --prune origin
git-guard api gitlab.example.com GET /projects/123 --token "$GITLAB_TOKEN"
git-guard status                      # ブレーカー状態
git-guard stats [--since EPOCH] [--json]   # 監視集計
git-guard reset [endpoint]            # ブレーカー状態クリア（運用）
```

## 設定（環境変数）

| 変数 | 既定 | 意味 |
|------|------|------|
| `GITGUARD_ENFORCE` | `0`（監視のみ） | `1` で開状態のアクセスを fail-fast（`CircuitOpenError` / CLI は exit 75）|
| `GITGUARD_DISABLE` | `0` | `1` で完全素通り（記録もしない）|
| `GITGUARD_THRESHOLD` | `5` | 連続インフラ障害で開くまでの回数 |
| `GITGUARD_COOLDOWN` | `60` | open → half_open までの秒 |
| `GITGUARD_WINDOW` | `120` | 連続カウントを束ねる窓（秒）。窓を越えた古い連続はリセット |
| `GITGUARD_DIR` | `$TMPDIR/gitguard` | 状態/ロック/イベントログの置き場（ホスト共有）|

**既定は監視のみ**（ブロックしない）。横断導入しても既存挙動を壊さないので、まず観測して
しきい値を調整 → `GITGUARD_ENFORCE=1` で安全に効かせる、という順で運用する。

## テスト

```sh
python -m unittest discover -s tools/gitguard/tests
# または
python -m pytest tools/gitguard/tests/test_gitguard.py
```
