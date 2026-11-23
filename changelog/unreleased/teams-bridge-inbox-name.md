### teams-sharepoint-bridge-agent: 受信フォルダの依頼を、Windows の道ではなく相対の名前で渡す

- `check-file`・`submit`・`record-invalid`・`build-prompt` に、`--path` の代わりの `--inbox`（受信フォルダ）と `--name`（受信フォルダからの相対の名前）を足す。Dagu の例はこちらで `${CHANGE.record.rel}` を渡す。Windows の道（`C:\TeamsBridge\inbox\tm-1.json`）を Dagu に展開させると、途中の `\t` などが制御文字に化け、`check` が存在しない道を読んで `missing` を出し続け、提出に進まないことがあった。
- `check-file` が `missing` を出すとき、読めなかった道を標準エラーに書く。道に制御文字が入っていれば、そう書く。
- Dagu の例を本物の change-watch・ブリッジ・受け箱で流し、依頼が 1 回ずつ提出され、次の依頼へ進むことを確かめるテストを足す。
