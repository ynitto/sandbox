# hermes-gitlab-gateway — GitLab イシューを Hermes の gateway にする（案B: プル型）

GitLab イシューを [Hermes](https://github.com/NousResearch/hermes-agent) の入出力チャネルに
する構成のうち、検討メモ
[docs/plans/2026-06-10-hermes-gitlab-issue-gateway.md](../../docs/plans/2026-06-10-hermes-gitlab-issue-gateway.md)
の **案B（ポーリング → loopback webhook 投函）** の実装です。

- インバウンドポートの公開も、GitLab 側の webhook 設定権限も不要（WSL / NAT 裏で動く）
- 検知（毎サイクル・LLM トークン消費ゼロ）と実行（イベント時のみエージェント起動）を分離
- **Hermes 本流へのパッチは不要**。自作物は本ディレクトリのポーリングスクリプト 1 本のみ

```
hermes cron（every 5m, --no-agent --script gitlab-issue-watch.sh）
   │ gl.py list-issues → 状態ファイル（iid → updated_at）と比較
   │ 新着なし → 何もせず終了
   ▼ 新着あり
POST http://127.0.0.1:8644/webhooks/gitlab-issues   ※ GitLab Issue Hook 互換ペイロード
   │ Hermes webhook route: secret 検証 → prompt テンプレート → skills 添付
   ▼
AIAgent（gitlab-idd ワーカーロール）
   │ claim（status::todo → status::doing）→ 実装 → ブランチ/MR
   ▼
イシューコメント + status::review ラベル（gl.py による書き戻し）
```

## 前提条件

- Hermes がインストール済みで `hermes gateway` が動くこと
- [gitlab-idd スキル](../../.github/skills/gitlab-idd/) を Hermes のスキルディレクトリに配置済み
  （例: `~/.hermes/skills/gitlab-idd/`）
- GitLab パーソナルアクセストークン（`api` 権限）。gl.py の `connections.yaml`
  または環境変数 `GITLAB_TOKEN` で設定

## セットアップ

### 1. gl.py の接続確認

```bash
python ~/.hermes/skills/gitlab-idd/scripts/gl.py --label-conn default configure \
  --url https://gitlab.example.com/group/repo --token glpat-xxxx
cd ~/projects/target-repo
python ~/.hermes/skills/gitlab-idd/scripts/gl.py project-info   # 接続確認
```

### 2. Hermes に webhook route を追加

`~/.hermes/config.yaml`（書式は本流 `website/docs/user-guide/messaging/webhooks.md` 準拠。
バージョンにより異なる場合があるので適用時に要確認）:

```yaml
platforms:
  webhook:
    extra:
      port: 8644
      routes:
        gitlab-issues:
          secret: "change-me"            # ラッパーの HERMES_GLGW_SECRET と同じ値
          events: ["Issue Hook"]
          skills: ["gitlab-idd"]
          deliver: log
          prompt: |
            GitLab イシューイベントを受信しました。gitlab-idd スキルの
            ワーカーロールでこのイシューを処理してください。手順:
            1. get-issue で最新状態を確認し、ラベルが status::todo で
               なければ何もせず終了する（多重起動・エコー防止）。
            2. 着手宣言として update-issue でラベルを status::doing に
               付け替える（claim）。
            3. イシューの受け入れ条件に沿って実装・調査する。
            4. 結果をブランチ + add-comment で報告し、ラベルを
               status::review に付け替える。
            iid: {object_attributes.iid}
            title: {object_attributes.title}
            action: {object_attributes.action}
            url: {object_attributes.web_url}
```

loopback 専用なら `secret: "INSECURE_NO_AUTH"` も使えますが、secret を置く方が無難です。

### 3. ラッパースクリプトを配置

```bash
mkdir -p ~/.hermes/scripts
cp gitlab-issue-watch.sh.example ~/.hermes/scripts/gitlab-issue-watch.sh
chmod +x ~/.hermes/scripts/gitlab-issue-watch.sh
# パス・フィルター・secret を編集する
```

### 4. 既存イシューを既読化（初回フラッディング防止）

```bash
~/.hermes/scripts/gitlab-issue-watch.sh --init
# [glgw] 初期化: N 件を既読として記録しました（投函なし）
```

既存の `status::todo` イシューも処理させたい場合は `--init` を飛ばして構いません
（`HERMES_GLGW_MAX_POSTS`、既定 3 件/サイクルで少しずつ流れます）。

### 5. 動作確認 → cron 登録

```bash
# gateway を起動した状態で、対象 GitLab に status::todo のテストイシューを作成し:
~/.hermes/scripts/gitlab-issue-watch.sh --dry-run   # 検知の確認
~/.hermes/scripts/gitlab-issue-watch.sh             # 実際に投函

# 問題なければ定期実行を登録
hermes cron create "every 5m" --no-agent --script ~/.hermes/scripts/gitlab-issue-watch.sh
hermes cron list
```

`--no-agent --script` は stdout をそのまま配信し、**空出力はサイレント**になるため、
新着が無いサイクルは通知も出ず LLM トークンも消費しません。

## 運用ルール: ラベルによる claim（重要）

このゲートウェイの多重実行・エコー防止は **`status::*` ラベル運用**で成立しています。

| ラベル | 意味 | 付け替える人 |
|---|---|---|
| `status::todo` | 着手待ち（**ポーリング対象**） | リクエスター / レビュアー |
| `status::doing` | エージェントが着手済み（claim） | エージェント（着手時に即付け替え） |
| `status::review` | 結果報告済み・人間レビュー待ち | エージェント（報告時） |

- ポーリングは `HERMES_GLGW_ISSUE_LABELS="status::todo"` で **todo のみ**を見る。
  エージェントが claim した時点でイシューはフィルターから外れるため、
  エージェント自身のコメント・ラベル変更（updated_at 更新）では再トリガーしない。
- レビューで差し戻すときは `status::review → status::todo` に付け替えるだけでよい
  （updated_at が変わるので新規イベントとして検知される）。
  [obsidian-gitlab-issues のインラインレビュー → 再作業ループ](../../docs/plans/2026-06-08-gitlab-inline-review-comments.md)
  がそのまま使えます。
- 万一の二重投函（応答タイムアウト後の再送など）も、prompt 手順 1〜2 の
  「todo でなければ終了 + 着手時 claim」で吸収されます。

## 環境変数リファレンス

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `HERMES_GLGW_GL_PY` | `scripts/gl.py` | gl.py のパス |
| `HERMES_GLGW_GL_CWD` | （未設定） | gl.py の作業ディレクトリ（git remote / connections.yaml 解決用） |
| `HERMES_GLGW_PYTHON` | 実行中の python | gl.py 用インタプリタ |
| `HERMES_GLGW_GL_TIMEOUT` | `20` | gl.py のタイムアウト（秒） |
| `HERMES_GLGW_ISSUE_STATE` | `opened` | イシュー状態フィルター |
| `HERMES_GLGW_ISSUE_LABELS` | （空） | ラベルフィルター（AND、`status::todo` 推奨） |
| `HERMES_GLGW_ISSUE_EXCLUDE_LABELS` | （空） | 除外ラベル（OR） |
| `HERMES_GLGW_ISSUE_ASSIGNEE` | （空） | 担当者フィルター |
| `HERMES_GLGW_WEBHOOK_URL` | `http://127.0.0.1:8644/webhooks/gitlab-issues` | 投函先 route |
| `HERMES_GLGW_SECRET` | （空） | route の secret（`X-Gitlab-Token` で送信） |
| `HERMES_GLGW_CONNECT_TIMEOUT` | `10` | 接続タイムアウト（秒）。失敗時は次サイクルで再送 |
| `HERMES_GLGW_POST_TIMEOUT` | `30` | 応答待ち（秒）。超過は「送達済み」扱い（下記参照） |
| `HERMES_GLGW_MAX_POSTS` | `3` | 1 サイクルの最大投函数 |
| `HERMES_GLGW_STATE_FILE` | `~/.hermes/gitlab-issue-gateway/state.json` | 状態ファイル |

### 応答タイムアウトを「送達済み」とみなす理由

Hermes の webhook（agent mode）は**エージェント完走後に 200 を返す同期型**で、
処理に数分かかることがあります。リクエスト送信が完了していれば Hermes 側で処理は
始まっているため、応答待ちのタイムアウトは成功扱いにして既読化します
（接続失敗・401/404 などは未送達として次サイクルで再送）。実行結果は
HTTP 応答ではなく、エージェントがイシューに書き戻すコメント / ラベルで確認します。

## トラブルシューティング

- **`接続失敗: Connection refused`** — gateway が起動していない、または webhook
  アダプタ/ポートが無効。`hermes gateway` の起動と `port: 8644` を確認。
- **`401`** — route の `secret` と `HERMES_GLGW_SECRET` の不一致。
- **`404`** — route 名（URL 末尾）と `config.yaml` の routes キーの不一致。
- **同じイシューが何度も投函される** — エージェントが claim（ラベル付け替え）せずに
  終了している。route の prompt に手順 1〜2 が入っているか、gitlab-idd スキルが
  ロードされているか（`skills` 設定と配置先）を確認。
- **何も検知されない** — `--dry-run` で確認。`HERMES_GLGW_ISSUE_LABELS` が
  イシューのラベルと一致しているか（`::` 含め完全一致）に注意。

## 将来の移行（案A: GitLab webhook 直結）

投函ペイロードは GitLab Issue Hook 互換（`object_attributes` 下にイシュー、
`X-Gitlab-Event` / `X-Gitlab-Token` ヘッダ）なので、Hermes ホストへ GitLab から
HTTP が届くようになったら、**route 設定はそのまま**に GitLab のプロジェクト
webhook（Issues events + 同じ Secret token）を向けるだけで案A へ移行できます。
その場合は GitLab の 10 秒タイムアウト対策（検討メモ参照）を併せて検討してください。
