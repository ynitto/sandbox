# laya（CPU・日本語）の導入

[laya](https://huggingface.co/convaiinnovations/laya)（Convai Innovations、Apache-2.0）は、本家 Jev と同じ
`/v1/systemone` で答える判断モデル。agent-herd の選択・振り分けの第 1 段を、API キーなしで手元の PC に
任せられる。ここにあるのは、それを **GPU なし・日本語対応**で動かすための導入スクリプトと小さなサーバ。

要るのは Python 3.10 以上だけ（Windows / macOS / Linux）。

```bash
# macOS / Linux
sh tools/laya/install-laya.sh
~/.agents/laya/laya-serve.sh            # 起動（この PC からだけ受け付ける）
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File tools\laya\install-laya.ps1
& "$env:USERPROFILE\.agents\laya\laya-serve.cmd"   # 起動
```

導入スクリプトは次をまとめて行う。

1. `~/.agents/laya` に専用の仮想環境を作る
2. CPU 版の PyTorch・laya・推論に要る部品だけを入れる（GPU 版の PyTorch は落とさない）
3. 入った部品のライセンスを一覧にして `~/.agents/laya/licenses.json` に残す（コピーレフトが紛れていたら止まる）
4. 多言語版のモデル（日本語を読める。約 650 MB）だけを `~/.agents/laya/models/multilingual` に置く
5. Hugging Face へつながない状態で、日本語の問いを 1 つ解いて確かめる
6. agent-herd が入っていれば、第 1 段を laya に向ける（`agent-herd config set select.jev.backend laya`）

消すときは `~/.agents/laya` を消すだけ。agent-herd を WSL で使っているなら、導入も WSL の中で行う
（agent-herd と同じ側に置く）。

## オプション

| オプション | 用途 |
|---|---|
| `--dry-run` | 何をするかだけ出す |
| `--port <番号>` | 待ち受けを変える（agent-herd の接続先も合わせる） |
| `--home <フォルダ>` | 入れる場所を変える |
| `--no-configure` | agent-herd の設定を書き換えない |
| `--torch-index-url <URL>` | PyTorch の配布元を差し替える（社内ミラーなど） |
| `--hf-endpoint <URL>` | モデルを Hugging Face の代わりにミラーから落とす |
| `--model-from <zip か フォルダ>` | モデルを手元のファイルから入れる |
| `--export-model <zip>` | 入れ終えたモデルを 1 つの zip にまとめる |
| `--find-links <フォルダ>` | PyPI にもつながらないとき、部品の wheel をこのフォルダだけから入れる |

## Hugging Face へつながらない PC

起動したサーバはいつも手元のモデルだけを読み、Hugging Face へはつながない。つながらないのは
導入のときだけの問題で、モデルを持ち込めば入る。

```bash
# つながる PC で（導入を済ませてから）モデルを 1 つの zip にまとめる（約 650 MB）
python tools/laya/install_laya.py --export-model laya-multilingual.zip
# つながらない PC で、その zip から入れる
python tools/laya/install_laya.py --model-from laya-multilingual.zip
```

zip の代わりに、`convaiinnovations/laya` の `multilingual` フォルダ（`rl_agent_config.json`・
`model.safetensors`・`tokenizer/`・`encoder/`）をブラウザで落として `--model-from <フォルダ>` を
渡してもよい。

### ミラー

`--hf-endpoint` には、Hugging Face と同じ API を話すミラーを渡せる。

| ミラー | 向き | 注意 |
|---|---|---|
| JFrog Artifactory / Sonatype Nexus の Hugging Face リモートリポジトリ | 社内で使うならこれ。管理者が一度つなげば各 PC は社内の URL だけで落とせる | 管理者の設定が要る |
| [hf-mirror.com](https://hf-mirror.com) | 誰でも使える公開ミラー（`--hf-endpoint https://hf-mirror.com`） | 有志の運営で、公式ではない。社外へ出られない PC からは使えない |
| ModelScope | 中国の公開モデル置き場 | Hugging Face と API が違うので `--hf-endpoint` には使えない。laya が置かれているかも未確認 |

社外に一切出られないなら、ミラーより上の zip の持ち込みが確実。

## 軽くしたいとき: ONNX Runtime と INT8（未実装の選択肢）

いまは PyTorch でモデルを動かしている。ONNX Runtime（MIT）と、INT8 に量子化した多言語版の
組み合わせにすると、次のように軽くなる見込み。

- 各 PC から PyTorch（数百 MB）が要らなくなる（ONNX Runtime・tokenizers・NumPy だけ）
- モデルが約 650 MB から約 170 MB 前後に縮み、メモリも減る
- CPU での推論はこの種のモデルでおおむね 2 倍前後速くなる

ただし laya 0.3.21 付属の ONNX 対応のままでは PyTorch が要る（ONNX 版の実行部も、PyTorch を読み込む
共通部品を使っている）ので、次の手間がかかる。

1. つながる PC で一度だけ、多言語版を ONNX に書き出して INT8 に量子化する（ここだけ PyTorch が要る。
   公式の ONNX 版・INT8 版は見当たらず、有志が量子化したものは出所を確かめられない）
2. ONNX Runtime だけで動く小さな実行部を用意する（laya の前処理と確率の計算は PyTorch を使わないので
   借りられる。Apache-2.0 なので出典を残す）
3. 日本語の問いのセットで元のモデルと答えを比べ、確度の較正が崩れていないことを確かめてから切り替える

導入の容量や起動の速さが問題になったら、この順で進める。

## ライセンス

laya 本体と多言語版のモデルは Apache-2.0。実行に欠かせない部品のうち、PyTorch・NumPy などは BSD 系、
certifi・tqdm は MPL-2.0（改変しなければ義務なし）で、それ以外は Apache-2.0 / MIT。導入スクリプトは
入った部品をすべて `licenses.json` に書き出し、GPL・LGPL・AGPL などが紛れていたら止まる。

## サーバ

`laya_server.py` は標準ライブラリだけで書いた `/v1/systemone` と `/health` のサーバ（laya 付属のサーバ用の
追加部品は使わない）。いつも CPU・多言語版で答え、推論は 1 本ずつ通す。`LAYA_API_KEY` を付けて起動すると
`Authorization: Bearer` が要る（agent-herd 側は `select.jev.api_key`）。起動にはモデルの読み込みで十数秒、
1 問に数百ミリ秒〜数秒かかる。

## テスト

```bash
python -m unittest discover -s tools/laya
```

実物の laya・PyTorch・モデルは使わず、偽の Router を差して往復・日本語・認証・入力検査・導入手順を確かめる。
