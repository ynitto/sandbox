# Hermes agent の gateway として GitLab イシューを使う

GitLab イシューを [Hermes](https://github.com/NousResearch/hermes-agent) の入出力チャネルにする
検討メモ。イシューの作成・更新・コメントで Hermes が起動し、結果をイシューコメントと
ラベル付け替えで返す——つまり gitlab-idd の「プロンプトトリガー」を Hermes gateway で
自動化するのがゴール。

## 前提: Hermes 本流に既にある機構（パッチ不要）

kiro-acp のときと違い、今回は **本流の機能だけで成立する**。

1. **webhook アダプタ** — `hermes gateway` の messaging アダプタ群に汎用 Webhooks
   アダプタがあり（既定ポート 8644、`POST /webhooks/<route>`）、**GitLab を一級サポート**
   している:
   - 認証: `X-Gitlab-Token` ヘッダの完全一致検証（route ごとの `secret`）
   - イベントフィルタ: `X-Gitlab-Event`（`Issue Hook` / `Note Hook` など）
   - ペイロードの dot-notation テンプレートで送信プロンプトを組み立て
     （例 `{object_attributes.iid}` `{object_attributes.title}`）
   - route ごとに `skills` を添付してエージェント実行
   - 冪等性: 配送 ID ヘッダで重複 POST を `{"status":"duplicate"}` として吸収
2. **cron** — gateway 内スケジューラ（60 秒ティック）。`hermes cron create "every 5m" ...
   --skill gitlab-idd` でエージェント実行、`--no-agent --script foo.sh` でスクリプトのみ実行。
3. **書き戻し** — webhook route の `deliver` ターゲットに GitLab は **無い**
   （`github_comment` はあるが GitLab 版は未実装）。よって書き戻しは
   **エージェント自身に gitlab-idd スキル（`scripts/gl.py`）でやらせる**。
   route 側は `deliver: log` で足りる。これは既存のラベル運用
   （`status::*` の付け替え → obsidian-gitlab-issues のレビュー UI → 再作業）と
   そのまま接続できるという利点でもある。

## 案A: GitLab webhook → Hermes webhook アダプタ（プッシュ型）

```
GitLab (Issue/Note event)
   │ POST + X-Gitlab-Token
   ▼
hermes gateway :8644/webhooks/gitlab-issues
   │ route: events フィルタ → prompt テンプレート → skills 添付
   ▼
AIAgent（gitlab-idd ワーカーロール）
   │ gl.py で claim（ラベル付け替え）→ 実装 → ブランチ/MR
   ▼
イシューコメント + status ラベル更新（gl.py による書き戻し）
```

設定はコード 0 行。`~/.hermes/config.yaml`（書式は本流 `webhooks.md` 準拠、適用時に要再確認）:

```yaml
platforms:
  webhook:
    extra:
      port: 8644
      routes:
        gitlab-issues:
          secret: "<GitLab 側 Secret token と同じ文字列>"
          events: ["Issue Hook", "Note Hook"]
          skills: ["gitlab-idd"]
          deliver: log
          prompt: |
            GitLab イシューイベントを受信しました。gitlab-idd スキルの
            ワーカーロールで処理してください。対象外（自分の投稿への
            エコー、status::doing 以降のラベル等）なら何もせず終了。
            project: {project.path_with_namespace}
            iid: {object_attributes.iid}
            title: {object_attributes.title}
            action: {object_attributes.action}
```

GitLab 側はプロジェクトの Settings → Webhooks で URL・Secret token・
Issues events / Comments events をチェックするだけ。

**注意点**

- **同期応答**: Hermes はエージェント完走後に 200 を返す。GitLab の webhook
  タイムアウト（self-managed 既定 10 秒）には確実に間に合わず、GitLab 側は
  失敗扱い → 再送 → 失敗続きで webhook 自動無効化のリスクがある。
  対策は (1) 即 200 を返して転送する薄いリレーを挟む、(2) Hermes の冪等性
  ガード + gitlab-idd の claim（最初にラベルを付け替えて取得宣言）で再送を
  無害化する、のどちらか。実運用では (1) を推奨。
- **到達性**: gitlab.com → 自宅 WSL は直接届かない。cloudflared / Tailscale Funnel
  などのトンネルが要る。LAN 内 self-managed GitLab なら直接で OK。

## 案B: ポーリング → loopback webhook 投函（プル型・WSL 向け推奨）

インバウンド公開も GitLab 側の webhook 設定権限も不要にする構成。
kiro-loop の `hooks/gitlab-issue-hook.py`（新規/更新検知 + 状態ファイル）の検知ロジックを
ほぼそのまま流用できる。

```
hermes cron（every 5m, --no-agent --script gitlab-issue-watch.sh）
   │ gl.py list-issues → 前回 updated_at と比較
   │ 新着なしなら何もせず終了（LLM トークン消費ゼロ）
   ▼ 新着あり
curl POST http://127.0.0.1:8644/webhooks/gitlab-issues  ← 案A と同じ route
   ▼
AIAgent（gitlab-idd ワーカーロール）→ gl.py で書き戻し
```

- loopback なので `secret: "INSECURE_NO_AUTH"` も許容されるが、secret を置く方が無難。
- 検知（無料・毎 5 分）と実行（イベント時のみエージェント起動）が分離されるのが利点。
- 欠点はポーリング間隔ぶんのレイテンシのみ。

## 案C: cron 直接（最小構成・まず試す用）

```bash
hermes cron create "every 10m" \
  "gitlab-idd スキルのワーカーロールを実行し、status::todo の未着手イシューを 1 件
   処理してください。対象が無ければ NO_TASK とだけ返して終了。" \
  --skill gitlab-idd --workdir ~/projects/target-repo
```

コード 0 行・設定 1 コマンドで今日から動くが、**対象が無くても毎サイクル
エージェントが起動して判断する**ためトークンを常時消費する。動作確認・小規模運用向け。

## 共通設計: 多重実行ガードとループ防止

- **claim パターン**: ワーカーは着手時に必ず `status::todo → status::doing` を付け替えてから
  作業する（gitlab-idd 既存運用）。これが webhook 再送・cron 重複発火・複数ノード競合の
  すべてに対する防御になる。
- **エコー抑止**: Note Hook を購読する場合、Hermes 自身が投稿したコメントで再起動しない
  よう「ボット自身のユーザー名のノートは無視」をプロンプト（またはリレー側フィルタ）に
  明記する。
- **レビューア人間ループ**: 結果はコメント + `status::review` で返し、
  obsidian-gitlab-issues のインラインレビュー → `status::todo` 付け替えで再作業させる
  （docs/plans/2026-06-08-gitlab-inline-review-comments.md のループにそのまま乗る）。

## 推奨

| 状況 | 推奨案 |
|---|---|
| LAN 内 self-managed GitLab / Hermes ホストへ HTTP が届く | **案A**（+薄いリレー） |
| WSL・NAT 裏 / gitlab.com / webhook 設定権限なし | **案B** |
| とりあえず今日試したい | **案C** → 後で B/A に移行 |

いずれも Hermes 本流へのパッチは不要で、必要な自作物は最大でも
「ポーリングスクリプト 1 本（kiro-loop hook の流用）」または「リレー 1 本」に収まる。

## 参考

- 本流 docs: `website/docs/user-guide/messaging/webhooks.md` / `features/cron.md`
  （https://hermes-agent.nousresearch.com/docs/）
- リポジトリ内の既存資産: `.github/skills/gitlab-idd/`（gl.py・ラベル運用）、
  `tools/kiro-loop/hooks/gitlab-issue-hook.py`（検知ロジック）、
  `tools/issue-mailbox/`（ポーリング実装の参考）
