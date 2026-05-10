---
name: windows-app-automation
description: pywinauto と winauto CLI を使って Windows ネイティブアプリ（Win32・WPF・WinForms・UWP）を自動化するスキル。アプリ起動、要素探索、クリック・入力・キー送信、スクリーンショット取得、ファイルダイアログ操作に対応する。「Windows アプリを自動化して」「Windowsの画面を操作して」「デスクトップアプリをテストして」「Notepad を自動操作して」「ファイルダイアログを操作して」などのリクエストで発動する。Claude Code・GitHub Copilot・Kiro の各エージェント環境、および WSL 端末から呼び出し可能。Windows 専用スキル。
metadata:
  version: 1.1.0
  tier: experimental
  category: implementation
  tags:
    - windows
    - native-app
    - pywinauto
    - ui-automation
    - win32
    - wpf
    - winforms
    - desktop-automation
    - e2e-testing
    - wsl
    - copilot
    - kiro
    - cross-agent
---

# windows-app-automation

Windows ネイティブアプリを自動化するときは、Python + pywinauto で自動化スクリプトを作成する。

## 対応エージェント環境

| 環境 | 実行場所 | winauto コマンド |
|------|---------|----------------|
| **Claude Code** (Windows) | PowerShell / CMD ターミナル | `winauto` または `python winauto.py` |
| **GitHub Copilot** (VS Code) | VS Code 統合ターミナル（PowerShell） | `winauto` または `python winauto.py` |
| **Kiro** (AWS IDE) | Kiro 統合ターミナル（PowerShell） | `winauto` または `python winauto.py` |
| **WSL** (WSL2 端末) | bash/zsh from VS Code / Kiro / Windows Terminal | `winauto`（ラッパー経由で Windows Python を呼ぶ） |

> **注意**: pywinauto は Windows 専用。WSL から呼ぶ場合は Windows 側 Python で実行される。

---

## セットアップ（初回のみ）

### Windows ネイティブ（Claude Code / Copilot / Kiro 共通）

```powershell
# 依存ライブラリと winauto コマンドのインストール
python tools/winauto/install.py

# インストール確認
winauto --version
winauto apps
```

### WSL 端末から使う場合

```bash
# Windows 側 Python を自動検出してインストール
python tools/winauto/install.py

# インストール確認（新しい端末で）
winauto --version
winauto apps
```

インストーラーが Windows 側 Python を見つけられない場合：
```bash
# Windows Python のパスを確認
cmd.exe /c where python

# 手動で Windows 側に pip インストール
cmd.exe /c python -m pip install pywinauto Pillow pywin32 comtypes
```

### 依存ライブラリのみインストール（既存 Python 環境に追記）

```powershell
pip install pywinauto>=0.6.9 Pillow>=9.0.0 pywin32>=306 comtypes>=1.4.0
```

---

## 環境別の実行方法

### Claude Code / GitHub Copilot / Kiro（Windows ターミナル）

これらはすべて Windows 上で動作するため、同じコマンドが使える。

```powershell
# winauto CLI（インストール済みなら直接呼べる）
winauto apps
winauto tree --app notepad
winauto click "name:=OK" --app notepad

# またはリポジトリから直接
python tools/winauto/winauto.py apps
python tools/winauto/winauto.py tree --app notepad

# ヘルパースクリプトも同様
python .github/skills/windows-app-automation/scripts/element_inspector.py --list
```

**Copilot（VS Code）固有の注意点:**
- VS Code のターミナルが PowerShell の場合、`python` コマンドが正しく通るか確認する
- `python --version` で Python 3.9 以上が表示されることを確認する

**Kiro 固有の注意点:**
- Kiro の統合ターミナルから実行する
- Kiro エージェントは直接 `!winauto tree --app notepad` のようにシェルコマンドを呼べる
- スキルの自動化スクリプトを Kiro に書かせた後、Kiro のターミナルで実行する

### WSL 端末（VS Code Remote / Kiro WSL / Windows Terminal）

```bash
# winauto ラッパー経由（インストール済みなら直接呼べる）
winauto apps
winauto tree --app notepad

# パスは WSL パス形式で指定可能（ラッパーが wslpath 変換を行う）
winauto screenshot --app notepad --output /tmp/screenshot.png

# スクリプトを WSL から実行（Windows Python が呼ばれる）
winauto run my_automation.py

# ヘルパースクリプトは Windows Python 経由で実行
cmd.exe /c python .github\\skills\\windows-app-automation\\scripts\\element_inspector.py --list
```

**WSL 固有の注意点:**
- `winauto run` に渡すスクリプト内のファイルパスは Windows パス（`C:\...`）で書く
- スクリーンショットの保存先は `C:\Users\<name>\` 配下か `/mnt/c/...` の WSL パスを使う
- WSL ターミナルに出力は返ってくるが、GUI 操作の対象は Windows デスクトップ上のウィンドウ

---

## 利用可能な補助スクリプト

- `scripts/element_inspector.py` — UIツリーの探索・セレクタの特定（最初に必ず実行）
- `scripts/app_launcher.py` — アプリ起動・待機・コマンド実行・終了を一括管理
- `tools/winauto/winauto.py` — Playwright 風の統合 CLI（inspect/click/type/screenshot/codegen）

**最初に `--help` を実行して利用方法を確認する。必要になるまでスクリプト本体は読まない。**

## 進め方の判断フロー

```
依頼内容 → 対象アプリは何か？
    ├─ Win32 (MFC / VCL / Delphi / 古いC++) → backend=win32 を優先
    ├─ WPF / UWP / WinForms / Qt            → backend=uia を使う
    └─ 不明                                  → uia から試し、失敗なら win32

アプリは起動済みか？
    ├─ No  → Application.start(app_path) で起動
    └─ Yes → Application.connect(title_re / process) でアタッチ

要素が見つかるか？ → 必ず先に element_inspector.py で探索する
    ├─ auto_id あり → child_window(auto_id="...") ← 最優先
    ├─ name + type  → child_window(title="...", control_type="...")
    └─ class あり   → child_window(class_name="...") ← Win32 向け
```

## ワークフロー: 調査 → 生成 → 実行

### Step 1: 要素ツリーを探索する

```bash
# アプリ一覧を確認
python scripts/element_inspector.py --list

# UIツリーを探索（depth は 3〜5 が目安）
python scripts/element_inspector.py --app notepad --depth 4

# 特定の子要素から探索
python scripts/element_inspector.py --app notepad --selector "control:=Document"

# JSON で出力（大きなツリーの解析に便利）
python scripts/element_inspector.py --app notepad --json > tree.json
```

winauto CLI でも同等の操作が可能：

```bash
python tools/winauto/winauto.py apps
python tools/winauto/winauto.py tree --app notepad --depth 4
python tools/winauto/winauto.py inspect --app notepad   # 対話REPL
```

### Step 2: スクリプトを生成する

```bash
# codegen でテンプレートを生成（アプリを起動してツリーを取得）
python tools/winauto/winauto.py codegen notepad.exe --output test_notepad.py

# 生成されたスクリプトを確認・編集して完成させる
```

または、手動で直接スクリプトを書く（Step 1 で取得したセレクタを使う）。

### Step 3: 実行する

```bash
# 単体実行
python my_automation.py

# app_launcher.py 経由（アプリ起動・終了を自動管理）
python scripts/app_launcher.py --app notepad.exe -- python my_automation.py

# winauto CLI でワンライナー実行
python tools/winauto/winauto.py click "name:=OK" --app notepad
```

---

## pywinauto 基本パターン

### アプリ起動・接続

```python
from pywinauto import Application

BACKEND = "uia"  # Win32 アプリなら "win32"

# 起動
app = Application(backend=BACKEND).start("notepad.exe")
app = Application(backend=BACKEND).start(r"C:\MyApp\app.exe --arg1 value")

# 実行中のアプリにアタッチ
app = Application(backend=BACKEND).connect(title_re=".*Notepad.*")
app = Application(backend=BACKEND).connect(process=12345)  # PID
app = Application(backend=BACKEND).connect(path="notepad.exe")

# トップウィンドウを取得
win = app.top_window()
win = app.window(title_re=".*Notepad.*")
win.wait("ready", timeout=10)
```

### 要素の検索

```python
# auto_id（最も安定）
btn = win.child_window(auto_id="btnSubmit")

# タイトル + コントロールタイプ
btn = win.child_window(title="OK", control_type="Button")

# クラス名（Win32 向け）
edit = win.child_window(class_name="Edit")

# インデックス（最終手段）
first_edit = win.child_window(control_type="Edit", found_index=0)

# チェーン（階層的な指定）
ok_btn = win.child_window(auto_id="mainPanel") \
            .child_window(control_type="Button", title="OK")

# 存在確認
if btn.exists(timeout=3):
    btn.click_input()

# 状態待機
btn.wait("enabled", timeout=10)
btn.wait("exists,visible", timeout=10)
```

### 操作

```python
from pywinauto.keyboard import send_keys

# クリック（確実な方法）
btn.click_input()

# テキスト入力（高速、IME 非経由）
edit.set_text("Hello World")

# キーストローク入力（IME・特殊キー対応）
edit.type_keys("Hello World", with_spaces=True)
edit.type_keys("{CTRL}a{DEL}")  # Ctrl+A → Delete

# ウィンドウ全体にキー送信
send_keys("^s")     # Ctrl+S
send_keys("%{F4}")  # Alt+F4
send_keys("{ENTER}")

# テキスト取得
text = edit.window_text()
all_texts = [c.window_text() for c in win.children()]

# メニュー操作
win.menu_select("File->Save As")
win.menu_select("Edit->Find->Find Next")

# スクロール
list_box.scroll("down", "page")
list_box.scroll("up", "line", count=3)

# ドラッグ＆ドロップ
src.drag_mouse_input(dst)
```

### スクリーンショット

```python
# ウィンドウ全体
win.set_focus()
img = win.capture_as_image()
img.save("/tmp/screenshot.png")

# 要素のみ
elem = win.child_window(auto_id="mainPanel")
img = elem.capture_as_image()
img.save("/tmp/element.png")
```

### ダイアログ処理

```python
# ダイアログが開くまで待機
dlg = app.window(title_re=".*Save As.*")
dlg.wait("ready", timeout=10)

# ファイルパスを入力
filename_field = dlg.child_window(auto_id="1001")  # 標準ファイルダイアログ
if not filename_field.exists(timeout=2):
    filename_field = dlg.child_window(class_name="Edit")  # フォールバック
filename_field.set_text(r"C:\output\result.txt")

# ボタンクリック
dlg.child_window(title="Save", control_type="Button").click_input()

# 上書き確認ダイアログ
confirm = app.window(title_re=".*Confirm.*|.*Replace.*")
if confirm.exists(timeout=2):
    confirm.child_window(title="Yes", control_type="Button").click_input()
```

---

## コントロールタイプ別 Tips

### リストボックス / コンボボックス

```python
# ListBox: アイテム選択
lb = win.child_window(control_type="ListBox")
lb.select("Item Name")                    # 名前で選択
lb.get_item(0).click_input()              # インデックスで選択

# ComboBox: ドロップダウン
cb = win.child_window(control_type="ComboBox")
cb.select("Option 1")                     # 項目を選択
items = cb.item_texts()                   # 全項目取得

# ListBox のアイテムを全取得
items = [item.window_text() for item in lb.children()]
```

### ツリービュー

```python
tv = win.child_window(control_type="Tree")
# ルートアイテム取得
roots = tv.children(control_type="TreeItem")
# 子アイテム展開
root = roots[0]
root.expand()
children = root.children(control_type="TreeItem")
# パスで選択
tv.get_item(r"\Root\Child\Grandchild").select()
```

### チェックボックス / ラジオボタン

```python
cb = win.child_window(title="Enable feature", control_type="CheckBox")
cb.check()    # チェック ON
cb.uncheck()  # チェック OFF
is_checked = cb.get_toggle_state() == 1

rb = win.child_window(title="Option A", control_type="RadioButton")
rb.select()
```

### タブコントロール

```python
tab = win.child_window(control_type="Tab")
tab.select("Settings")   # タブ名で選択
tab.select(1)            # インデックスで選択
```

---

## よくある落とし穴と対処法

| 症状 | 原因 | 対処 |
|------|------|------|
| `ElementNotFoundError` | セレクタが間違い / UIが未描画 | `element_inspector.py` で再確認; `wait()` を追加 |
| 操作が失敗・無応答 | 要素が disabled / focus なし | `wait("enabled")` → `set_focus()` → `click_input()` |
| テキスト入力が文字化け | IME / Unicode 問題 | `type_keys()` の代わりに `set_text()` を使う |
| `click()` が効かない | 座標クリックが必要 | `click_input()` を使う（より低レベル） |
| ダイアログが検出できない | タイトルの一致パターンが違う | `title_re=".*キーワード.*"` で部分一致 |
| `backend=uia` で要素が見えない | アプリが UIA 非対応 | `backend=win32` に切り替える |
| 高速実行で要素見つからない | UI描画遅延 | `win.wait("ready")` / `elem.wait("exists")` を使う |
| 管理者権限アプリを操作できない | UAC 分離 | スクリプト自体を管理者権限で実行 |
| **WSL**: `pywinauto` が import できない | Linux Python に入っている | Windows Python で実行する: `cmd.exe /c python script.py` |
| **WSL**: `winauto` コマンドが見つからない | インストール未完 / PATH 未設定 | `python tools/winauto/install.py` を再実行; `source ~/.bashrc` |
| **WSL**: `winauto apps` が空 | WSL から Windows デスクトップが見えない | Windows Python 経由で実行されているか確認: `winauto --version` |
| **Copilot**: ターミナルで `python` が見つからない | PATH に Python が未登録 | VS Code の Python インタープリタ設定を確認; `where python` でパスを確認 |
| **Kiro**: エージェントが winauto を実行できない | Kiro がシェルコマンドをブロック | Kiro の設定で `trustTools: true` を確認; 手動でターミナル実行 |

---

## ベストプラクティス

- **必ず先に explorer で探索する**: `element_inspector.py --app <name>` でセレクタを確認してからスクリプトを書く
- **`auto_id` を最優先にする**: 開発者が設定した AutomationID は最も安定したセレクタ
- **`wait()` を省略しない**: `click_input()` の前に必ず `wait("enabled")` か `wait("exists")` を入れる
- **`click_input()` を使う**: `click()` より確実。マウスイベントを直接送信する
- **`set_text()` を使う**: `type_keys()` は IME 問題が起きやすい。値を確定させるだけなら `set_text()` が確実
- **`app_launcher.py` でライフサイクル管理**: アプリの起動・終了をスクリプト内に書かず、`app_launcher.py` に任せる
- **スクリーンショットで状態確認**: エラー時は `capture_as_image().save()` で状態を記録する

---

## 参照

- `references/selector-syntax.md` — セレクタ構文の詳細リファレンス
- `examples/notepad_automation.py` — Notepad 自動化の完全サンプル
- `examples/file_dialog_handling.py` — ファイルダイアログ操作パターン集
- `tools/winauto/winauto.py` — winauto CLI（`--help` で各コマンド確認）
- [pywinauto Documentation](https://pywinauto.readthedocs.io/)
- [Windows UI Automation](https://learn.microsoft.com/windows/win32/winauto/entry-uiauto-win32)
