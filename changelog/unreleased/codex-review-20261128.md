### software-factory と change-watch の不具合修正

- software-factory は、レビュー投稿前の MR 照合で GitLab に接続できない場合に投稿を保留する。
- GitLab Issue 調査エージェントは、引用符などを含む秘密情報を JSON に変換する前に伏せ字にする。
- change-watch は、`commit --refresh` でレコードを更新したとき、そのレコードの古い `item` キャッシュを破棄する。
