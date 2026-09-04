# statemachine-maker

statemachine-use スキルで動くステートマシン（`.statemachine/<識別名>/`）を、**工程列と人の操作の記録**から
組み立て・編集する Electron アプリ。agent-dashboard の定常業務作成（手順ビルダー・操作の記録）を
切り出し、単体で動くようにしたもの。

- 画面はライトトーン。中央に工程のフロー（各工程が **何をするか** と **どう遷移するか** を 1 枚ずつ）、
  右に選んだ工程の編集。
- 成果物は `workflow.yaml` + `actions/*.md`。**このアプリが無くても** statemachine-use スキルの
  スクリプト（`run_machine.py` / `next_state.py`）や agent-loop のヘッドレスハーネスで動く。
- 既存の `.statemachine/<識別名>/workflow.yaml` を開いて編集できる（画面で表せない遷移は原文のまま保持）。
- Windows と WSL の行き来はしない。道具（`playwright-cli` / `winauto` / `python`）はこの端末の PATH から
  直接呼ぶ。生成する定義は OS に依らない形（`/` 区切り・LF・シェルを介さない）。

## 起動

```bash
cd tools/statemachine-maker
npm install
npm start
```

初回は「フォルダを選ぶ」でステートマシンを置くフォルダ（リポジトリのルートなど）を選ぶ。
`.statemachine/*/workflow.yaml` が一覧に出る。「workflow.yaml を開く」で既存の定義を直接指すこともできる。

検証・実行には Python 3 と PyYAML、statemachine-use スキル（`.github/skills/statemachine-use/scripts/`）が要る。
スキルは選んだフォルダから上へ辿って探し、見つからなければ「道具」タブの設定でフォルダを指定する。

## 使い方

| したいこと | 操作 |
|---|---|
| 新しく作る | 「＋ 新しいステートマシン」→ 名前・目的 → 工程を種類（ブラウザ / Windows アプリ / スキル / コマンド / AI）で足す → 保存 |
| 人の操作から起こす | 「記録」タブ。ブラウザは URL を入れて記録を開始（`playwright-cli` が見える形で開く）→ 操作 → 終了。Windows アプリは Windows 上で `winauto record` を起こす。別の端末で取った記録（`recording-stop` の出力 / JSONL）は貼り付けで受ける |
| 分岐を書く | 工程の「判断」にラベル（第 1 行の語）と行き先（次へ / 完了 / 失敗 / 工程 n）を並べる。分岐は transitions に書かれ、本文には書かれない |
| 完了を機械で確かめる | 工程の「完了の確認コマンド」に argv を書く。`check` として宣言され、通ったときだけ次へ進む（`equals:check_ok:true`） |
| 既存を編集する | 一覧から選ぶ。`maker.json` があれば工程列が正確に戻る。無くても YAML から起こし、自然言語条件や無条件遷移の工程は「遷移は原文のまま」として保持する |
| 検証・実行 | 「実行」タブ。`run_machine.py --dry-run` と `--agent <cli>` の実行を、出力を流しながら行う。手で動かすコマンドも表示する |
| AI に補完させる | 「AI 補完」タブの指示文（statemachine-use の作成モード向け）をコピーしてエージェント CLI に貼る。待機・読み取り・想定外の画面の扱いを足してもらう用途 |

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
npm test          # 往復・記録・保存・結合（スキルの run_machine.py --dry-run）
npm run lint      # eslint（devDependencies が要る）
```

結合テストは python3 + PyYAML があるときだけ走る（無ければ skip）。

## 設計

[docs/plans/2026-09-04-statemachine-maker-design.md](../../docs/plans/2026-09-04-statemachine-maker-design.md)
