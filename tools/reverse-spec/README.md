# reverse-spec

**既存のソースコードから、根拠つきの仕様書を起こすステートマシン。** 仕様書が無い・古い・中身が分からない
システムを、エージェントが読める Markdown の仕様書にする。

> 設計: [`docs/designs/reverse-spec-design.md`](../../docs/designs/reverse-spec-design.md)
>
> [aws-samples/sample-reverse-spec-flow](https://github.com/aws-samples/sample-reverse-spec-flow)（Kiro・Claude Code 向け）の
> 考え方（分母を使い切る・すべての記述に `パス:行` の根拠・別の目で検証・最後の検査）を借り、
> [codd-agent](../codd-agent/README.md) と同じく「`step` で 1 段ずつ回し、検査はスクリプトが行う」形で組み直したもの。

- **エージェントを選ばない。** シェルでコマンドを動かせるものなら何でも回せる（Claude Code・Codex・GitHub Copilot・
  Kiro・Gemini など）。入口は `AGENTS.md` の 1 つで、エージェントごとの読み込み先は `init.py` が書く
- **長い作業を前提にする。** 状態はすべてファイルにあり、段を進めるたびにコミットする。会話が切れても、
  要約されても、別のエージェントに替えても、`step` で今の指示を出し直せば続きから進む
- **無人で回せる。** `run` が段ごとに新しいエージェントのプロセスを起こすので、会話が長くなって崩れることがない。
  人の判断が要る段で止まり、進まない段が続けば止まる
- **モデルの申告を信じない。** 分母の数え上げ・根拠の行が実在するか・単位のファイルをすべて読んだか・
  検証で確かめる根拠の選び方・検証のあとに変えていないか、はスクリプトが測る
- 依存は python3（3.9 以上）と git だけ。ソースは読むだけで、何も書き込まない

## 流れ

```
scope → units → confirm → ┌ draft → verify ┐ → synthesize → audit → done
  範囲    単位に   分け方を   │  書く   確かめる │    全体を      最後の
         分ける   確かめる   │    ↑    NG ↓    │    書く       検査
                            │    └── fix ←┘    │
                            └─ 単位ごとに繰り返す ┘
検査で続けて落ちたら stuck（やり直す / その単位を止めて次へ / やめる）
```

| 段 | エージェントがすること | スクリプトが確かめること |
|---|---|---|
| scope | 数え上げの要約を見て、除外を足し、目的と対象を書く | 数え上げがある・目的と対象が書いてある |
| units | ソースのファイルを単位（module / api / data / behavior / screen / batch / out-of-scope）に分ける | すべてのファイルがどれかの単位に入っている・1 単位が大きすぎない |
| confirm | 分け方を利用者に見せて答えを待つ | — |
| draft | 1 つの単位のファイルを読み、ひな形の見出しを埋める | 根拠の行が実在する・単位のファイルをすべて根拠に挙げた・見出しが揃い、どの節にも根拠か UNKNOWN がある |
| verify | 選ばれた根拠ごとに、その行から主張が言えるかを判定する（できれば検証用のサブエージェントで） | 判定がすべて埋まった。NG があれば fix へ |
| fix | NG の記述を直す | 否とされた記述がそのまま残っていない。直したら別の根拠を選び直して検証し直す |
| synthesize | 全体像と横断の関心事を書く | 見出し・根拠・仕様書への参照 |
| audit | （自動） | 下の最後の検査 |

## 置き方

```bash
# ~/work/app のソースから ~/work/app-spec に仕様書の作業場を作る
python3 tools/reverse-spec/init.py ~/work/app
# 置き場所・エージェントの入口・除外を指定する
python3 tools/reverse-spec/init.py ~/work/app --out ~/work/app-spec --host claude,codex --exclude 'generated/**'
```

作業場はソースとは別の git リポジトリになる。もう一度 `init.py` を動かすと、駆動スクリプト・段の指示・ひな形・
エージェントの入口を入れ替え、設定（`rspec.json`）と進み具合は残す。

| 書くもの | 読むエージェント |
|---|---|
| `AGENTS.md` | Codex・GitHub Copilot・Kiro など（いつも書く） |
| `CLAUDE.md`（`@AGENTS.md`）と `.claude/agents/rspec-verifier.md` | Claude Code |
| `GEMINI.md`（`@AGENTS.md`） | Gemini CLI |
| `.github/agents/rspec-verifier.agent.md` | GitHub Copilot の検証用サブエージェント |
| `.kiro/agents/rspec-verifier.json` | Kiro の検証用サブエージェント |

## 使い方

### 対話で進める

作業場のフォルダでエージェントを開き、「reverse-spec を進めて」と頼む。エージェントは `AGENTS.md` に従って
`python3 .rspec/rspec.py step` で指示を受け取り、終えたら `step "OK"` で渡す。単位の分け方の確認と、止まったときの相談では
利用者に答えを訊く。会話を新しくしたときも同じ頼み方でよい（続きから進む）。

```bash
python3 .rspec/rspec.py step          # 今の段の指示
python3 .rspec/rspec.py step "OK"     # 今の段の出力を渡す（confirm は OK / NG 指摘 / STOP、stuck は RETRY / SKIP / STOP）
python3 .rspec/rspec.py status        # 進み具合
python3 .rspec/rspec.py check         # 書いている単位を検査だけする
```

### 無人で回す

```bash
python3 .rspec/rspec.py run --agent claude    # codex / copilot / kiro / gemini、または rspec.json の agents の名前
```

`run` は 1 段ごとに新しいエージェントのプロセスを起こし、「`step` で指示を読み、1 段だけ進めて終了する」よう頼む。
段が進んだかは状態のファイルで確かめ、モデルの返事は見ない。

- 人の判断が要る段（confirm・stuck）で止まり、終了コード 3 を返す。答えを `step` で渡してから `run` をもう一度動かす
- 同じ段で続けて進まなければ（`--retries`、既定 3 回）止まり、終了コード 4 を返す。ログは `.rspec/logs/`
- 1 段の上限は `--timeout`（既定 3600 秒）。超えたらプロセスを止め、同じ段をやり直す
- 夜通し回すなら `rspec.json` を `"confirm": "auto"`・`"on_stuck": "skip"` にする。分け方の確認を省き、
  単位で止まったらその単位を止めて次へ進む。止めた単位は最後の検査で挙がるので、朝に `step "RETRY"` か
  `requeue --blocked` で書き直しに回す

エージェントの CLI の引数は版によって変わる。合わないときは `rspec.json` の `agents` で上書きする
（`{prompt}` は指示の本文、`{prompt_file}` はそれを書いたファイル、`{source}` はソースのフォルダに置き換わる）。

```json
{
  "agents": {
    "claude": ["claude", "-p", "{prompt}", "--dangerously-skip-permissions", "--add-dir", "{source}"],
    "my-agent": "my-agent --non-interactive --prompt-file {prompt_file}"
  }
}
```

### ソースが変わったら

```bash
python3 .rspec/rspec.py drift           # 変わった・増えた・消えたファイルと、作り直しが要る単位
python3 .rspec/rspec.py drift --apply   # 数え上げ直し、単位の段から進め直す（変わらない単位は検証済みのまま）
python3 .rspec/rspec.py requeue orders  # 単位を指定して書き直しに回す
```

## 書き方の決まり

- 根拠はソースのフォルダからの相対パスで `` `src/orders.py:40` `` か `` `src/orders.py:40-52` `` と書き、バッククォートで囲む
- 分からないことは `UNKNOWN[blocking]`（人に訊かないと進めない）・`UNKNOWN[deferred]`（後回し）・
  `UNKNOWN[out-of-scope]`・`UNKNOWN[undecidable]`（コードからは決められない）・
  `UNKNOWN[partial] 確認した範囲: `パス:行`` で書く
- ほかの単位の仕様書は `[[SPEC-種類-単位]]` で指す（例 `[[SPEC-module-orders]]`）
- 見出し（`##`）の名前と順番はひな形のまま。書くことが無い節は「なし」（「依存」「未確定事項」など一部の節だけ）

## 最後の検査

`python3 .rspec/rspec.py gates` でいつでも動かせる。結果は `extraction/gate-results.md`。

| 検査 | 通る条件 |
|---|---|
| G-INVENTORY | ソースを数え上げ、版を固定した |
| G-FRESH | ソースが固定した版から変わっていない |
| G-DENOM | 数え上げたファイルを、すべてどれかの単位か対象外で受け持った |
| G-SPEC | 単位の仕様書が、見出し・根拠・UNKNOWN の書き方・ファイルの読み漏れの検査を通る |
| G-VERIFY | すべての単位を検証し、そのあと仕様書を変えていない |
| G-BLOCKED | 止めた単位が無い |
| G-SYNTH | 全体像と横断の関心事が検査を通る |
| G-LINK | 仕様書の中のリンクが切れていない |
| G-INDEX | 索引（`specs/index.md`・`specs/source-index.md`）を作り直した |

## 作業場の中身

```
<作業場>/
  AGENTS.md  CLAUDE.md  GEMINI.md   エージェントの入口
  rspec.json                        設定
  .rspec/                           駆動スクリプト・段の指示・ひな形・状態（state.json）・ログ
  extraction/
    inventory.tsv  inventory-summary.md   数え上げ（スクリプトが書く）
    scope.md  units.txt                   範囲と単位（エージェントが書く）
    verify/<単位>-<回>.md                 検証の用紙
    worklog.md  gate-results.md  report.md
  specs/
    overview.md  cross-cutting.md         全体
    modules/ api/ data/ behavior/ screens/ batch/   単位ごと
    index.md  source-index.md             索引（スクリプトが書く）
```

## 設定（rspec.json）

| 項目 | 既定 | 意味 |
|---|---|---|
| `source` | — | ソースのフォルダ（作業場からの相対か絶対） |
| `exclude` | `.git/**` など | 数え上げから外すパターン。変えたら `ingest` で数え直す |
| `confirm` | `human` | `auto` なら単位の分け方を確かめずに進む |
| `on_stuck` | `ask` | `skip` なら単位で止まったとき、その単位を止めて次へ進む |
| `max_unit_lines` / `max_unit_files` | 2500 / 30 | 1 単位の大きさの上限（1 つの会話で読み切れる大きさ） |
| `read_chunk` | 600 | これより長いファイルは区切って読むよう指示する |
| `verify_sample` | 8 | 1 回の検証で確かめる根拠の数 |
| `max_verify_rounds` | 3 | 検証し直す上限。超えたら stuck |
| `retries` | 2 | 検査で落ちたとき、同じ段をやり直す回数 |
| `auto_commit` | true | 段を進めるたびに作業場へコミットする |
| `agents` | — | `run` で使うエージェントのコマンド |

## テスト

```bash
python3 -m unittest discover -s tools/reverse-spec/tests
```
