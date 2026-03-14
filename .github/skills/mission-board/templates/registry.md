# Registry — 参加端末レジストリ

ミッションボードに参加する端末の一覧。各端末が自分自身の行を管理する（SSOT）。

## フォーマット

| hostname | agent | status | last-seen | capabilities |
| -------- | ----- | ------ | --------- | ------------ |
| [hostname コマンドの出力] | [表示名・短縮名] | 🟢 active | YYYY-MM-DDTHH:MM | [カンマ区切りの能力リスト] |

### フィールド説明

| フィールド | 説明 | 例 |
| ---------- | ---- | -- |
| `hostname` | `hostname` コマンドの出力（SSOT） | `my-pc`, `MacBook-Pro` |
| `agent` | メッセージの `from`/`to` で使う識別子（短く一意に） | `PC-A`, `server-01` |
| `status` | 🟢 active / 🟡 idle / 🔴 offline | `🟢 active` |
| `last-seen` | Heartbeat の最終更新時刻（ISO 8601） | `2026-03-14T10:00` |
| `capabilities` | この端末が担当できる作業種別（タスクアサインの参考） | `shell,browser,docker` |

### capabilities の例

| 値 | 意味 |
| -- | ---- |
| `shell` | シェルコマンドの実行 |
| `browser` | ブラウザ操作・Web 確認 |
| `docker` | Docker / コンテナ操作 |
| `gpu` | GPU を使う処理 |
| `windows` | Windows 固有の操作 |
| `deploy` | 本番環境へのデプロイ権限 |

---

## 端末一覧

| hostname | agent | status | last-seen | capabilities |
| -------- | ----- | ------ | --------- | ------------ |
| example-host | PC-A | 🟢 active | 2026-01-01T00:00 | shell |
