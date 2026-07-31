# kiro-cli から windows-app-automation を使う

## 目次

- [前提](#前提)
- [基本的な呼び出し方](#基本的な呼び出し方)
- [タスク記述のコツ](#タスク記述のコツ)
- [自動実行フロー](#自動実行フロー)
- [出力のキャプチャ・後処理](#出力のキャプチャ後処理)
- [他エージェントとの連鎖](#他エージェントとの連鎖)
- [注意点](#注意点)

kiro-cli は WSL 上で動作する AI エージェント CLI。`--trust-all-tools` を付けると winauto コマンドを
自律的に呼び出してスクリプトを生成・実行できる。

## 前提

winauto の WSL インストールが完了していること。

```bash
# 確認
winauto --version   # → "winauto 1.0.0"
winauto apps        # → Windows 起動中ウィンドウ一覧
```

インストール手順: `tools/winauto/install.py`（WSL 端末で実行）

---

## 基本的な呼び出し方

```bash
# 対話モード（探索・設計フェーズ）
kiro-cli chat "Notepad を自動操作するスクリプトを作って。winauto CLI が使える。"

# 非インタラクティブ（CI・他エージェントからの連鎖呼び出し）
kiro-cli chat --no-interactive \
  "Notepad を起動してテキストを入力し保存するスクリプトを作って実行して。winauto CLI が使える。"

# ファイル操作・winauto 実行を自動承認する場合
kiro-cli chat --no-interactive --trust-all-tools \
  "Notepad を自動操作して /tmp/result.txt に結果を保存して。winauto CLI が使える。"
```

---

## タスク記述のコツ

kiro-cli へのタスク指示に以下を含めると精度が上がる：

| ポイント | 例 |
|---------|-----|
| ツールの存在を明示 | `"winauto CLI が使える"` を添える |
| 対象アプリ名を具体的に | `"Notepad"` `"Excel"` `"MyApp.exe"` など |
| 操作を箇条書きにする | `"①起動 ②テキスト入力 ③Ctrl+S 保存 ④閉じる"` |
| 成果物の場所を指定 | `"/tmp/output.txt に保存"` |

### 推奨パターン（具体的なタスク記述）

```bash
kiro-cli chat --no-interactive --trust-all-tools "
  以下の手順で Windows の Notepad を自動操作してください。winauto CLI が使えます。

  手順:
  1. winauto apps で起動中アプリ一覧を確認する
  2. Notepad が起動していなければ winauto launch notepad.exe で起動する
  3. winauto tree --app notepad で UI 要素ツリーを確認する
  4. テキスト入力エリアに「Hello from kiro-cli」と入力する
  5. winauto screenshot --app notepad --output /tmp/notepad.png でスクリーンショットを取る
  6. アプリを閉じる（保存不要）
"
```

---

## 自動実行フロー（kiro-cli が行う操作）

kiro-cli が `--trust-all-tools` で自律実行するときの典型的な流れ：

```
kiro-cli --trust-all-tools
    │
    ├─ winauto apps              ← 起動中アプリを偵察
    ├─ winauto tree --app <name> ← UI 要素を偵察（JSON or テキスト）
    ├─ [Python スクリプト生成]    ← pywinauto スクリプトをファイルに書く
    ├─ winauto run script.py     ← 実行（Windows Python が起動）
    └─ winauto screenshot        ← 結果を画像で確認
```

---

## 出力のキャプチャ・後処理

```bash
# 変数に取得して後続処理
result=$(kiro-cli chat --no-interactive --trust-all-tools \
  "winauto tree --app notepad の出力を JSON で返して" 2>&1)
echo "$result"

# ファイルに保存
kiro-cli chat --no-interactive --trust-all-tools \
  "Notepad を自動操作して結果を /tmp/automation_result.txt に書いて" \
  > /tmp/kiro_log.txt 2>&1

# 終了コードで成否確認
kiro-cli chat --no-interactive --trust-all-tools "..." && echo "成功" || echo "失敗"
```

---

## 他エージェントとの連鎖

### claude → kiro-cli: スクリプト設計を Claude、実行を kiro-cli

```bash
script=$(claude -p "Notepad 自動化の pywinauto スクリプトを作って（コードのみ出力）")
echo "$script" > /tmp/auto_notepad.py
winauto run /tmp/auto_notepad.py
```

### kiro-cli → claude: UIツリー取得を kiro-cli、次操作判断を Claude

```bash
tree=$(kiro-cli chat --no-interactive --trust-all-tools \
  "winauto tree --app notepad --output json を実行して JSON だけ出力して")
next_action=$(echo "$tree" | claude -p "このUIツリーから OK ボタンのセレクタを教えて")
echo "Next: $next_action"
```

### kiro-cli を並列実行して複数アプリを同時操作

```bash
kiro-cli chat --no-interactive --trust-all-tools \
  "Notepad を操作して /tmp/notepad.png を撮って" &
PID1=$!

kiro-cli chat --no-interactive --trust-all-tools \
  "電卓を操作して /tmp/calc.png を撮って" &
PID2=$!

wait $PID1 && wait $PID2 && echo "両方完了"
```

---

## 注意点

| 事象 | 原因 | 対処 |
|------|------|------|
| winauto コマンドが承認待ちになる | `--trust-all-tools` がない | `--trust-all-tools` を追加する |
| スクリプトの Windows パスが通らない | WSL パスを使っている | スクリプト内は `C:/...` 形式にする |
| GUI 操作の状況が stdout に出ない | 操作はデスクトップ側で発生 | `winauto screenshot` で画像確認 |
| kiro-cli がタイムアウトする | 処理が長い | `--timeout` オプションで延長 |
| `winauto apps` が空 | WSL から Windows Python を呼べていない | `winauto --version` でラッパー確認 |
