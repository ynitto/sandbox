### copilot-app-jobs・teams-sharepoint-bridge-agent: 振り先の表を受け箱の targets.json 1 つにまとめる

- 受け箱の振り先の表を `targets.txt` から `targets.json`（`{"targets": {名前: {"dir": …, "description": …}}}`）に替える。`targets.txt` は読まなくなる。使っていたら、`target add --to <フォルダ> --name <名前>` で登録し直す。
- `target add` に `--name`・`--description` を足し、`remove` は名前でも外せるようにする。ほかの項目（Teams のブリッジの宛先ごとの設定）は残したまま書き換える。`run --to` には振り先の名前も書ける。
- Teams のブリッジの宛先表を同じファイルにし、ハブの受け箱（`<ハブ>/.copilot-jobs/targets.json`）に置く。置いた受け箱のフォルダをハブとみなすので `hub` は書かなくてよく、宛先を足すときに振り先を別に登録する手間がなくなる。`gitlab_project` も `mode` も無い振り先は Teams の振り分けに使わない。
