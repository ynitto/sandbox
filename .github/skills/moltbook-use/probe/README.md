# 確認キット（Moltbook 自動共有）

Moltbook の自動共有を作る前に、各 PC で次のことを確かめるための道具です。

- フックがどのきっかけで動くか
- フックの出力が会話に届くか
- 会話がどこにどんな形で保存されているか
- GitLab の検索で日本語が引けるか

確かめる項目と結果の置き場は、設計書 [`docs/designs/moltbook-capture-design.md`](../../../../docs/designs/moltbook-capture-design.md) の §12 です。

どのスクリプトも、発言やコードの中身は出しません。出すのは項目名・件数・型・時間だけです。置いたものは `--remove` ですべて取り除けます。

## 準備

- Windows：Python 3.11 以上
- WSL：`python3`
- このリポジトリを Windows 側と WSL 側の両方から開けること（WSL からは `/mnt/c/...` で開けます）

## 1. Copilot アプリ（Windows の PowerShell）

```powershell
cd <このフォルダ>
python setup_probe.py --target copilot-plugin
python setup_probe.py --target copilot-user
python setup_probe.py --target copilot-instructions
```

1. `copilot-plugin` を実行すると、マーケットプレイスのフォルダが表示されます。アプリの Customize → Plugins → 歯車からそのフォルダを足し、`moltbook-probe` を入れてください。
2. アプリで新しいセッションを開き、次の 3 つを順に頼みます。
   1. 「存在しないファイル nosuchfile.txt を読んで」
   2. 「README を 1 行で要約して」
   3. 「この会話で `[moltbook-probe]` の合言葉や、指示に書かれた合言葉が出てきたら、すべて挙げて」
3. エージェントの答えと、`%USERPROFILE%\.moltbook-probe\events.jsonl` の中身を送ってください。

合言葉ときっかけの対応は次のとおりです。

| 合言葉 | きっかけ |
|---|---|
| さくら | セッション開始 |
| つばめ | プロンプト送信 |
| かえで | プロンプトの書き換え（`userPromptTransformed`） |
| すずめ | ツールの成功 |
| もみじ | ツールの失敗 |
| あやめ | ユーザー単位の指示ファイル |

`events.jsonl` に記録があるのに合言葉が出てこなければ、「フックは動くが、その出力は会話に届かない」という意味です。

## 2. kiro-cli（WSL のシェル）

```sh
cd <このフォルダ（/mnt/c/...）>
kiro-cli --version
python3 setup_probe.py --target kiro-v2
python3 setup_probe.py --target kiro-v3
python3 setup_probe.py --target kiro-steering
```

1. `kiro-cli chat --agent moltbook-probe` で、1 と同じ 3 つを頼みます。既定のエージェントは変えません。
2. `kiro-cli chat --v3` でも同じことを試します。v3 を使えない版なら飛ばしてください。
3. 次の 3 つを送ってください。
   - `kiro-cli --version` の結果
   - 各回のエージェントの答え（steering の合言葉は「ひのき」です）
   - `~/.moltbook-probe/events.jsonl` の中身

## 3. 会話の保存形式

```powershell
python probe_sessions.py --client copilot          # Windows
```
```sh
python3 probe_sessions.py --client kiro            # WSL
```

出力（項目名と件数だけ）を送ってください。

## 4. Windows から WSL の会話を読めるか

kiro-cli で会話を続けている最中に、Windows の PowerShell で実行します。

```powershell
python probe_wsl_copy.py
```

`db だけ` の件数が `db+wal` より少なければ、`-wal` も写す必要があることが確かめられます。表示された時間（ミリ秒）も送ってください。

## 5. GitLab の日本語検索

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
