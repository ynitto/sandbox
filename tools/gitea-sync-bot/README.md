# gitea-sync-bot

Gitea ⇄ GitLab のコードを**安全に双方向同期**する調停ロボット。
LAN 内の Gitea を Issue/MR の管理面にしつつ、コードを GitLab と同期する構成のための同期エンジン。

> 設計正典: [`docs/designs/gitea-gitlab-sync-design.md`](../../docs/designs/gitea-gitlab-sync-design.md)（§3 が本実装）
> Windows セットアップ: [`docs/guides/gitea-windows-setup.md`](../../docs/guides/gitea-windows-setup.md)

## 何をするか

同期対象（allowlist）の各 ref について、両側の HEAD を比べて **fast-forward できるときだけ**同期する。

| 状態 | アクション |
|---|---|
| 両側同一 | 何もしない |
| Gitea だけ進行 | GitLab へ **ff push** |
| GitLab だけ進行 | Gitea へ **ff push** |
| 双方進行（分岐） | **自動同期しない**。Gitea に統合ブランチを作り MR を起票して人手へ |

- **`--force` は絶対にしない**。どちらのコミットも消さない。分岐は人手（MR）で解決。
- **Gitea 発の作業ブランチ（`feature/*` 等）は GitLab へ push しない**（allowlist 方式・§3.6）。

## GitLab 負荷を抑える工夫（§3.7）

- **webhook 主導**。無変化のときは GitLab に接続しない。
- GitLab の HEAD 確認は軽量な `git ls-remote <ref>` のみ（履歴を転送しない）。キャッシュ一致なら fetch を省略。
- fetch は**対象 ref だけ**に限定（全 ref 総なめ禁止）。
- 連続イベントは **debounce** でまとめる。429/5xx は**指数バックオフ**。cron は**長間隔バックストップ**のみ。

## 依存

- `git`（PATH 上）
- Python 3.9+
- YAML 設定を使う場合のみ `PyYAML`（`pip install pyyaml`）。JSON 設定なら不要・pip 依存ゼロ。

## 使い方

```sh
# 1 回だけ allowlist 全 ref を同期（動作確認・cron 併用にも）
python3 gitea_sync_bot.py --config config.yaml --once

# 特定リポジトリ・特定 ref だけ、push せず予定だけ確認
python3 gitea_sync_bot.py --config config.yaml --repo myproject --ref refs/heads/main --once --dry-run

# webhook 待受 + cron バックストップ（常駐運用）
python3 gitea_sync_bot.py --config config.yaml --serve
```

設定は `config.yaml.example` をコピーして作成。トークンは環境変数展開（`${GITLAB_TOKEN}`）で渡す。

## webhook の登録

`--serve` で待ち受ける HTTP エンドポイント（既定 `:9000`）に、Gitea と GitLab の両方から
**push イベント**を送るよう設定する。secret を設定すると署名検証する
（Gitea: `X-Gitea-Signature` の HMAC-SHA256 / GitLab: `X-Gitlab-Token`）。

- Gitea: リポジトリ → Settings → Webhooks → Gitea 種別、URL に `http://<bot-host>:9000/`、Secret を設定。
- GitLab: Project → Settings → Webhooks、URL に同上、Secret Token を設定、Push events を有効化。

## 動作の要点（安全性）

- push は常に `<src>:<dst>` の明示 refspec で行い **`--force` を付けない**ため、
  非 fast-forward は git 側が拒否する（＝分岐を誤って上書きしない二重の安全弁）。
- 分岐時に作る統合ブランチ（`sync/*`）は allowlist の exclude に入れてあるので、
  **GitLab へは決して伝播しない**（人手のマージ結果だけが後で GitLab に ff で乗る）。

## テスト

```sh
python3 -m unittest tests.test_gitea_sync_bot -v
```

判定コア（純粋関数）に加え、Gitea/GitLab を模した 2 つのローカル bare repo で
「双方向 ff」「分岐時に GitLab を動かさない」「feature ブランチを push しない」を end-to-end 検証する
（git のみで完結・ネットワーク不要）。

## 制約

- 完全リアルタイムの双方向同期は保証しない（fast-forward のみ自動・分岐は人手）。
  同一ブランチへの同時 write が多いほど統合 MR が増えるため、`main` は Gitea 側マージのみ等の
  運用ルール（設計書 §3.5）で write 方向を分けると安定する。
- LFS / サブモジュールは追加検証が必要。
- Issue/MR の同期は本ボットの対象外（設計書 §5 に将来設計の概要）。
