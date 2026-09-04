# statemachine-maker

statemachine-use スキルで動くステートマシン（`.statemachine/<識別名>/`）を、**工程列と人の操作の記録**から
組み立て・編集する Electron アプリ。agent-dashboard の定常業務作成（手順ビルダー・操作の記録）を
切り出し、単体で動くようにしたもの。

- **フォルダを登録して使う**。見に行くのは登録したフォルダの `.statemachine/` だけで、それ以外は
  読み書きしない。一覧は左に登録したフォルダ、右にそのフォルダのワークフローを並べる。
- 編集画面は **左に工程の流れ、右に選択中の設定**を置く。工程カードは短い要約だけを表示し、
  狭い画面では流れと設定を切り替えて使う。操作の記録やテストは用途別のダイアログで行う。
- **画面に出す言葉に内部の用語を入れない**。YAML の項目名・コマンドの綴り・ステートの呼び名は
  人が読む言葉に直してから出す（下の対応表）。
- 見た目は固定のニュートラル配色。色は主操作と状態の区別にだけ使う。

## 起動

```bash
cd tools/statemachine-maker
npm install
npm start
```

初回は「フォルダを登録」でステートマシンを置くフォルダ（リポジトリのルートなど）を登録する。
そのフォルダの `.statemachine/` にあるワークフローが右に並ぶ。フォルダは何個でも登録でき、左で切り替える。
登録を外してもフォルダの中身は消えない。

構成確認には Python 3 と PyYAML、statemachine-use スキル（`.github/skills/statemachine-use/scripts/`）が要る。
スキルは選んだフォルダから上へ辿って探し、見つからなければ「その他 → 実行環境」でフォルダを指定する。
AI を使う実行は agent-tools の `agent-herd harness statemachine` に統一している。候補は
`agent-herd defs --json` から取得するため、プロジェクトやユーザー環境の `agents/*.json` に追加した定義も画面へ反映される。
準備されていない場合は、リポジトリのルートで `bash tools/agent-tools/install.sh` を実行する。

## 使い方

| したいこと | 操作 |
|---|---|
| フォルダを増やす | 左の「フォルダ」の ＋。登録したフォルダだけを見に行く |
| 新しく作る | 「＋ 新しいワークフロー」→ 名前を入力 →「ワークフロー設定」で目的を入力 → 工程を追加 → 保存 |
| 人の操作から起こす | 「操作を記録」。URL を入れて開始 → 開いたブラウザで操作 →「終了して工程を作成」。別の端末で取った記録も貼り付けられる |
| 分岐を設定する | 工程を選び、「次の工程」で「もし 条件 なら 行き先」を追加する。条件は上から順に評価される |
| 完了を機械で確認する | 工程の「完了確認」にコマンドを書く。成功した場合だけ次へ進む |
| 既存を編集する | 一覧から選び、左の工程カードを選択して右パネルで編集する |
| テスト・実行 | 「テスト・実行」で「構成を確認」または「実行」を選ぶ |
| AI に補完させる | 「その他 → AIで補完」で指示文をコピーし、エージェント CLI に貼る |

## 生成する定義の形

statemachine-use の作成モードの原則に沿う（`SKILL.md` ステップ 2）:

- 1 ステート 1 工程。`action_file: actions/<id>.md`。本文の末尾は単一指示。
- 出力契約は `output_validator: startswith:<ラベル…>`（既定は `OK,FAILED`）。
- 分岐は `condition_rule`（`startswith:last_output:<ラベル>`）。`check` を宣言した工程は `equals:check_ok:true` で進む。
- 画面操作は `playwright-cli` / `windows-app-automation` スキルを本文で名指しする。記録した操作は role と名前で載せる。
- 終端は `complete`（完了）と `failed`（失敗）。どこからも行かない終端は書かない。
- `maker.json` は画面が読み戻すための写しで、実行には使わない。

## 検証

```bash
cd tools/statemachine-maker
npm test          # 往復・記録・保存・結合（スキルの run_machine.py --dry-run）・実機の起動
npm run smoke     # 実機の起動だけを xvfb 越しに走らせる（表示先の無い Linux 用）
npm run lint      # eslint（devDependencies が要る）
```

- 結合テスト（スキルの `run_machine.py --dry-run`）は python3 + PyYAML があるときだけ走る。
- **実機の起動**（`test/electron-smoke.test.js`）は Electron を本当に立ち上げ、画面が描画される
  ことまで見る。electron のバイナリ・表示先・playwright が揃っているときだけ走り、CI（実行時
  依存だけを入れる）では skip される。表示先の無い Linux では `npm run smoke` を使う。

## 次の工程

工程ごとに、行き先を上から順に並べる。決め方は 4 つ。

| 決め方 | 何を見るか | 書かれるもの |
|---|---|---|
| 回答が指定の言葉で始まる | 出力の 1 行目がその語で始まるか | `condition_rule: startswith:last_output:<語>` |
| 条件に当てはまる | その文にあてはまるか（AI が見る） | `condition: <文章>` |
| 常に | 条件なし | 条件を付けない |
| 詳細条件 | 読み込んだ式をそのまま | `condition_rule: <式>` |

何も足さなければ「できた → 次へ」「できなかった → 中止」になる。行き先は次の工程・完了・中止のほか、
**定義が持つ終わり方**（承認 / 差し戻し / 判別できない…）や、前の工程へ戻ることも選べる。

第 1 行の出力契約を書くのは、行き先が**すべて**回答の先頭で決まるときだけ。文章による条件が混ざる工程に
契約を足すと、名前で始まらない出力が失敗扱いになり、元の定義では通っていた道が通らなくなる。

## 記録がうまくいかないとき

記録は `playwright-cli`（ブラウザ）と `winauto`（Windows アプリ）を、この端末から直接呼ぶ。
「その他 → 実行環境 → 接続を確認」でどちらが呼べるかを先に確かめる。

| 症状 | 見るところ |
|---|---|
| ブラウザが開かない | 記録に使うブラウザは**既定で Chrome**。入っていなければ `playwright-cli install-browser chrome` を実行するか Chrome を入れる |
| 「操作の記録に未対応」と出る | 古い版には `recording-start` / `recording-stop` が無い。`npm install -g @playwright/cli@latest` で更新し、もう一度「接続を確認」を実行する |
| 「呼べません」と出る | `npm install -g @playwright/cli@latest`。Windows では npm が `playwright-cli.cmd` を置くので、アプリは PATHEXT を補って探す |
| 操作したのに 0 件になる | 記録するのは**アプリが開いたブラウザ**の中の操作だけ。別に開いていたブラウザで操作しても入らない |
| 画面の無い環境 | 別のパソコンで記録を取り、貼り付けで受ける |

記録の途中で `.playwright-cli/`（画面の写しとログ）がフォルダに作られる。消してかまわない。

## 画面の言葉

内部の綴りをそのまま出さない。画面には次の言葉を使う。

| 内部 | 画面 |
|---|---|
| ステートの ID | 工程ID（「詳細設定」の中） |
| 識別名・フォルダ名 | 保存名 |
| `output_validator` の第 1 行 / 出力契約 | 回答が指定の言葉で始まる |
| `check` / 終了コード 0 | 完了確認／確認できたら |
| `check_retries` | 再試行回数 |
| transitions / 遷移 | 次の工程 |
| 既定の OK / FAILED | できた／できなかった |
| `{{key}}` / 入力パラメータ | 毎回変わる値 |
| `--dry-run` | 構成を確認 |
| `--agent-cli`（agent-tools の定義名） | 使う AI |
| 自然言語の条件（`condition`） | 条件に当てはまる |
| 無条件の遷移 | 常に |
| `condition_rule`（読み込んだ式） | 詳細条件 |
| 終端ステート | 終わり方（いくつあっても行き先に選べる） |

`test/app.test.js` が、画面の言葉に内部の綴りが混ざっていないことを検査する。実機の煙試験も
描画された文字を見る。

### 画面を直すときの落とし穴

renderer で **`const api = …` のように preload が公開した名前を宣言してはいけない**。
`contextBridge` が置く `window.api` は再定義できないので、宣言するとスクリプトの実行前に
`Identifier 'api' has already been declared` で落ち、**画面が真っ白**になる（自分のログも出ない）。
ブラウザに `window.api` を代入して描く確認ではこの事故は再現しないため、
`test/preload-contract.test.js`（静的検査）と実機の起動の 2 つで押さえている。

## 設計

[docs/plans/2026-09-04-statemachine-maker-design.md](../../docs/plans/2026-09-04-statemachine-maker-design.md)

[docs/plans/2026-09-05-statemachine-maker-ui-redesign-design.md](../../docs/plans/2026-09-05-statemachine-maker-ui-redesign-design.md)
