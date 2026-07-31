#!/usr/bin/env python
"""
file_dialog_handling.py — File dialog automation patterns

Covers the most common file dialog scenarios:
- Open dialog (single file)
- Save As dialog
- Multi-select Open dialog
- Folder picker

These patterns work with most Windows apps that use standard Win32/Common dialogs.
For apps using custom dialogs, inspect with element_inspector.py first.

Usage:
    python examples/file_dialog_handling.py
"""

import time
from pywinauto import Application
from pywinauto.keyboard import send_keys


BACKEND = "uia"


# ─────────────────────────────────────────────────────────────────
# Helper: connect to standard file dialog
# ─────────────────────────────────────────────────────────────────

def get_file_dialog(app, title_pattern: str = ".*", timeout: float = 10.0):
    """Wait for and return a standard Windows file dialog."""
    dlg = app.window(title_re=title_pattern)
    dlg.wait("ready", timeout=timeout)
    return dlg


def set_filename(dlg, path: str):
    """Type a file path into the filename field of a standard dialog."""
    # Modern dialogs (Vista+): ComboBox with auto_id="1001"
    filename_box = dlg.child_window(auto_id="1001")
    if filename_box.exists(timeout=2):
        filename_box.set_focus()
        filename_box.set_text(path)
        return

    # Fallback: bare Edit control
    edit = dlg.child_window(class_name="Edit")
    if edit.exists(timeout=2):
        edit.set_focus()
        edit.set_text(path)
        return

    # Last resort: type the path using keyboard
    send_keys(path)


def click_dialog_button(dlg, title: str):
    """Click a button in a dialog by title."""
    btn = dlg.child_window(title=title, control_type="Button")
    btn.wait("enabled", timeout=5)
    btn.click_input()


# ─────────────────────────────────────────────────────────────────
# Pattern 1: Open file dialog
# ─────────────────────────────────────────────────────────────────

def open_file_in_notepad(file_path: str):
    """Open a specific file in Notepad via File > Open dialog."""
    app = Application(backend=BACKEND).start("notepad.exe")
    win = app.window(title_re=".*Notepad.*")
    win.wait("ready", timeout=10)

    # Open the dialog
    win.menu_select("File->Open")
    # or: send_keys("^o")

    dlg = get_file_dialog(app, title_re=".*Open.*")
    print(f"Open dialog: {dlg.window_text()!r}")

    # Navigate to the file
    set_filename(dlg, file_path)
    time.sleep(0.3)

    click_dialog_button(dlg, "Open")
    time.sleep(0.5)

    # Verify file is loaded
    new_title = app.top_window().window_text()
    print(f"Loaded: {new_title}")
    return app


# ─────────────────────────────────────────────────────────────────
# Pattern 2: Save As dialog
# ─────────────────────────────────────────────────────────────────

def save_as_notepad(app, save_path: str):
    """Save current Notepad content to a specific path."""
    win = app.top_window()
    win.menu_select("File->Save As")

    dlg = get_file_dialog(app, title_re=".*Save As.*")
    print(f"Save As dialog: {dlg.window_text()!r}")

    set_filename(dlg, save_path)
    time.sleep(0.3)
    click_dialog_button(dlg, "Save")
    time.sleep(0.5)

    # Handle "Replace existing file?" confirmation
    confirm = app.window(title_re=".*Confirm.*|.*Replace.*")
    if confirm.exists(timeout=2) and confirm.window_text() != win.window_text():
        click_dialog_button(confirm, "Yes")
        time.sleep(0.3)

    print(f"Saved to: {save_path}")


# ─────────────────────────────────────────────────────────────────
# Pattern 3: Navigate folder tree in dialog
# ─────────────────────────────────────────────────────────────────

def navigate_to_folder(dlg, folder_path: str):
    """
    Navigate to a folder by typing the path in the address bar.
    Works for most standard dialogs.
    """
    # Click address bar (or use keyboard shortcut)
    send_keys("%d")  # Alt+D = focus address bar
    time.sleep(0.3)
    send_keys(folder_path + "{ENTER}")
    time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────
# Pattern 4: Read tree view in dialog (folder picker)
# ─────────────────────────────────────────────────────────────────

def get_folder_picker_selection(app, dialog_title_re: str = ".*Browse.*") -> str:
    """
    Interact with a folder picker dialog and return selected path.
    Works with BrowseForFolder and similar dialogs.
    """
    dlg = get_file_dialog(app, title_re=dialog_title_re)

    # Try tree view navigation
    tree = dlg.child_window(control_type="Tree")
    if tree.exists(timeout=3):
        tree.set_focus()
        # Navigate using keyboard or expand nodes
        items = tree.children(control_type="TreeItem")
        for item in items:
            print(f"  TreeItem: {item.window_text()!r}")

    # Get the selected folder text
    try:
        folder_text = dlg.child_window(auto_id="1119").window_text()
        return folder_text
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────
# Pattern 5: Handle "File already exists" / "Access denied" errors
# ─────────────────────────────────────────────────────────────────

def safe_save_as(app, save_path: str, overwrite: bool = True):
    """Save with error handling for common dialog error cases."""
    win = app.top_window()
    send_keys("^+s")  # Save As shortcut
    time.sleep(0.5)

    dlg = get_file_dialog(app, title_re=".*Save.*")
    set_filename(dlg, save_path)
    time.sleep(0.3)
    click_dialog_button(dlg, "Save")
    time.sleep(0.5)

    # Check for error/confirmation dialogs
    for _ in range(3):
        for title_re, action_btn in [
            (".*Replace.*|.*Confirm.*", "Yes"),    # overwrite confirmation
            (".*Access Denied.*", None),            # access denied error
            (".*Invalid.*", None),                  # invalid path error
        ]:
            err_dlg = app.window(title_re=title_re)
            if err_dlg.exists(timeout=1) and err_dlg != win:
                if action_btn and overwrite:
                    click_dialog_button(err_dlg, action_btn)
                else:
                    # Close the error and report
                    click_dialog_button(err_dlg, "OK")
                    raise RuntimeError(f"Dialog appeared: {err_dlg.window_text()!r}")
                time.sleep(0.3)
                break
        else:
            break  # No dialog appeared

    print(f"Saved: {save_path}")


# ─────────────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────────────

def main():
    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.gettempdir())
    test_file = tmp_dir / "winauto_dialog_test.txt"
    test_file.write_text("File dialog test content", encoding="utf-8")

    print("=== File Dialog Handling Demo ===\n")

    # Pattern 1: Open a file
    print("Pattern 1: Open file in Notepad")
    app = open_file_in_notepad(str(test_file))
    time.sleep(0.5)

    # Pattern 2: Save As
    print("\nPattern 2: Save As")
    save_path = str(tmp_dir / "winauto_saved.txt")
    save_as_notepad(app, save_path)

    # Close
    from pywinauto.keyboard import send_keys
    send_keys("%{F4}")
    time.sleep(0.5)

    close_dlg = app.window(title_re=".*Notepad.*|.*Save.*")
    if close_dlg.exists(timeout=2):
        try:
            btn = close_dlg.child_window(title_re="Don't Save|No", control_type="Button")
            btn.click_input()
        except Exception:
            pass

    print("\nDone.")


if __name__ == "__main__":
    main()
