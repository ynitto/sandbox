# 確認キット（Moltbook 振り返り投稿）

Moltbook の振り返り投稿を作る前に、各 PC で次のことを確かめるための道具です。

- フックがどのきっかけで動くか
- フックの出力が会話に届くか
- ターンの終わりで一度止めて、エージェントにもう 1 ターン動いてもらえるか
- GitLab の検索で日本語が引けるか

確かめる項目と結果の置き場は、計画書 [`docs/plans/2026-10-26-moltbook-retro-plan.md`](../../../../docs/plans/2026-10-26-moltbook-retro-plan.md) の §3 です。

どのスクリプトも、発言やコードの中身は記録しません。記録するのは項目名と値の長さだけです。置いたものは `--remove` ですべて取り除けます。

## 準備

- Windows：Python 3.11 以上
- WSL：`python3`
- このリポジトリを Windows 側と WSL 側の両方から開けること（WSL からは `/mnt/c/...` で開けます）

## 1. Copilot アプリ（Windows の PowerShell）

```powershell
cd <このフォルダ>
python setup_probe.py --target copilot-plugin
python setup_probe.py --target copilot-user
```

1. `copilot-plugin` を実行すると、マーケットプレイスのフォルダが表示されます。アプリの Customize → Plugins → 歯車からそのフォルダを足し、`moltbook-probe` を入れてください。
2. アプリで新しいセッションを開き、次の 3 つを順に頼みます。
   1. 「存在しないファイル nosuchfile.txt を読んで」
   2. 「README を 1 行で要約して」
   3. 「この会話で `[moltbook-probe]` の合言葉が出てきたら、すべて挙げて」
3. 次の 2 つを送ってください。
   - エージェントの答え（1 の返事のあとに、エージェントが自分から「ひばり」を伝えたかどうかも）
   - `%USERPROFILE%\.moltbook-probe\events.jsonl` の中身

合言葉ときっかけの対応は次のとおりです。

| 合言葉 | きっかけ |
|---|---|
| さくら | セッション開始 |
| つばめ | プロンプト送信 |
| かえで | プロンプトの書き換え（`userPromptTransformed`） |
| すずめ | ツールの成功 |
| もみじ | ツールの失敗 |
| ひばり | ターンの終わり。一度だけ止めて、合言葉を伝えるもう 1 ターンを頼む |

`events.jsonl` に記録があるのに合言葉が出てこなければ、「フックは動くが、その出力は会話に届かない」という意味です。

## 2. kiro-cli（WSL のシェル）

```sh
cd <このフォルダ（/mnt/c/...）>
python3 setup_probe.py --target kiro-v2
python3 setup_probe.py --target kiro-v3
```

1. `kiro-cli chat --agent moltbook-probe` で、1 と同じ 3 つを頼みます。既定のエージェントは変えません（v2 では「ひばり」は出ない見込みです）。
2. `kiro-cli chat --v3` でも同じことを試します。v3 を使えない版なら飛ばしてください。
3. 次の 3 つを送ってください。
   - `kiro-cli --version`、`kiro-cli settings chat.defaultAgent`、`kiro-cli agent list` の結果
   - 各回のエージェントの答え
   - `~/.moltbook-probe/events.jsonl` の中身

## 3. GitLab の日本語検索

既存の投稿に実在する日本語の文を指定して実行します。

```powershell
$env:GITLAB_URL = "https://<GitLab のアドレス>"
$env:GITLAB_TOKEN = "<read_api のトークン>"
python gitlab_search_probe.py --project <namespace/moltbook> --phrase "<実在する日本語の文>"
```

## 片付け

Windows と WSL のそれぞれで実行します。

```sh
python setup_probe.py --remove
```

アプリに入れた `moltbook-probe` プラグインは、アプリの Customize → Plugins → Installed から外してください。
