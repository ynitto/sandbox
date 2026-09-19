# pywinauto 基本パターン

アプリ起動・接続、要素の検索、操作、スクリーンショット、ダイアログ処理の定型コード。
スクリプトを書く段（ワークフローの Step 2）で引く。

## 目次

- [アプリ起動・接続](#アプリ起動接続)
- [要素の検索](#要素の検索)
- [操作](#操作)
- [スクリーンショット](#スクリーンショット)
- [ダイアログ処理](#ダイアログ処理)

---

## アプリ起動・接続

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

## 要素の検索

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

## 操作

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

## スクリーンショット

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

## ダイアログ処理

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
