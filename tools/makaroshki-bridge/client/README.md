# client/ — Macaroni Messenger（同梱コピー）

PC-A（人間側）で使うブラウザクライアントの同梱コピー。
[vanyapr/makaroshki](https://github.com/vanyapr/makaroshki) からそのまま取り込んだもので、
このリポジトリでの変更は加えていない。

| ファイル | 内容 |
|---------|------|
| `messenger.html` | クライアント本体（単一 HTML ファイル・自己完結） |
| `index.html` | `messenger.html` へのリダイレクトページ |
| `docs/access-token.en.md` / `docs/access-token.md` | アクセストークン取得ガイド（messenger.html からリンクされている） |
| `LICENSE` | 上流のライセンス（WTFPL） |

- 取り込み元コミット: `10b21b2108eac643b0c8eb55763fafd7693131fb`（2026-06-11）
- ライセンス: [WTFPL](LICENSE)（コピー・改変自由）

## 使い方

`messenger.html` を PC-A にコピーして Chrome / Chromium / Edge で開くだけ（サーバ不要）。
セットアップ手順は [../README.md](../README.md) の「PC-A（人間側）」を参照。

## 更新方法

上流の新しい版に追従したいとき:

```bash
git clone --depth 1 https://github.com/vanyapr/makaroshki /tmp/mak
cp /tmp/mak/messenger.html /tmp/mak/index.html /tmp/mak/LICENSE .
cp /tmp/mak/docs/access-token.en.md /tmp/mak/docs/access-token.md docs/
# この README の「取り込み元コミット」を更新する
git -C /tmp/mak log -1 --format='%H (%ad)' --date=short
```
