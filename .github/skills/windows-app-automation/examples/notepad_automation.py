#!/usr/bin/env python
"""
notepad_automation.py — Notepad automation example

Demonstrates basic Windows app automation patterns:
- Launch app
- Wait for window
- Type text
- Use menu (File > Save As)
- Handle a dialog
- Close app

Run:
    python notepad_automation.py
    # or with app_launcher.py:
    python scripts/app_launcher.py --app notepad.exe -- python examples/notepad_automation.py
"""

import time
import tempfile
import os
from pathlib import Path
from pywinauto import Application
from pywinauto.keyboard import send_keys


BACKEND = "uia"
SAVE_PATH = str(Path(tempfile.gettempdir()) / "winauto_test.txt")
TEST_TEXT = "Hello from winauto!\nThis is a Windows automation test.\n"


def main():
    # ── Launch Notepad ──────────────────────────────────────────────────────
    print("Launching Notepad...")
    app = Application(backend=BACKEND).start("notepad.exe")

    # Wait until the main window is ready
    win = app.window(title_re=".*Notepad.*")
    win.wait("ready", timeout=10)
    print(f"Connected: {win.window_text()!r}")

    # ── Type text ───────────────────────────────────────────────────────────
    # Reconnaissance: find the edit area
    # win.print_control_identifiers()  # uncomment to see element tree

    edit = win.child_window(control_type="Document")
    if not edit.exists():
        # Fallback for older Notepad (Win32 Edit control)
        edit = win.child_window(class_name="Edit")

    edit.set_focus()
    edit.type_keys(TEST_TEXT, with_spaces=True)
    print("Typed test text.")
    time.sleep(0.5)

    # ── Save As (via keyboard shortcut) ─────────────────────────────────────
    print(f"Saving to: {SAVE_PATH}")
    send_keys("^+s")  # Ctrl+Shift+S = Save As in modern Notepad
    time.sleep(0.8)

    # Handle Save As dialog
    dlg = app.window(title_re=".*Save As.*")
    if not dlg.exists(timeout=5):
        # Older Notepad uses File > Save As menu
        win.menu_select("File->Save As")
        dlg = app.window(title_re=".*Save As.*")

    dlg.wait("ready", timeout=10)
    print("Save As dialog opened.")

    # Type the save path into the filename field
    filename_field = dlg.child_window(auto_id="1001")  # standard file dialog field
    if not filename_field.exists():
        filename_field = dlg.child_window(class_name="Edit")

    filename_field.set_focus()
    filename_field.set_text(SAVE_PATH)
    time.sleep(0.3)

    # Click Save button
    save_btn = dlg.child_window(title="Save", control_type="Button")
    save_btn.click_input()
    time.sleep(0.5)

    # Handle "Replace?" confirmation if file exists
    confirm_dlg = app.window(title_re=".*Confirm.*|.*Replace.*|.*Notepad.*")
    if confirm_dlg.exists(timeout=2) and confirm_dlg != win:
        yes_btn = confirm_dlg.child_window(title_re="Yes|Replace", control_type="Button")
        if yes_btn.exists():
            yes_btn.click_input()
            time.sleep(0.3)

    # ── Verify file was saved ───────────────────────────────────────────────
    if os.path.exists(SAVE_PATH):
        content = Path(SAVE_PATH).read_text(encoding="utf-8", errors="replace")
        print(f"Saved {len(content)} chars to {SAVE_PATH}")
    else:
        print(f"WARNING: File not found at {SAVE_PATH}")

    # ── Take screenshot ─────────────────────────────────────────────────────
    screenshot_path = str(Path(tempfile.gettempdir()) / "notepad_screenshot.png")
    win.set_focus()
    time.sleep(0.3)
    win.capture_as_image().save(screenshot_path)
    print(f"Screenshot saved: {screenshot_path}")

    # ── Close Notepad ───────────────────────────────────────────────────────
    send_keys("%{F4}")  # Alt+F4
    time.sleep(0.5)

    # Handle "Save changes?" prompt if it appears
    close_dlg = app.window(title_re=".*Notepad.*|.*Save.*")
    if close_dlg.exists(timeout=2) and close_dlg != win:
        dont_save = close_dlg.child_window(title_re="Don't Save|No", control_type="Button")
        if dont_save.exists():
            dont_save.click_input()

    print("Done.")


if __name__ == "__main__":
    main()
