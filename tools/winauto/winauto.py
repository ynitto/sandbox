#!/usr/bin/env python
"""
winauto - Windows Native App Automation CLI

Playwright-like CLI for Windows native app automation powered by pywinauto.

Commands:
  doctor      Diagnose the WSL/Windows automation bridge
  apps        List running automatable applications
  launch      Launch an application
  tree        Print UI element tree
  inspect     Interactive element inspector (REPL)
  click       Click an element by selector
  type        Type text into an element
  keys        Send key sequence to an element
  get-text    Get text content of an element
  screenshot  Capture a screenshot of the app window
  wait        Wait for an element to appear
  codegen     Generate a Python automation script template
  run         Execute an automation script

Selector syntax:
  name:=Submit               Match by window/control title or name
  auto_id:=ButtonOK          Match by AutomationID (most stable)
  class:=Edit                Match by class name
  control:=Button            Match by control type
  text:=Click me             Match by visible text
  index:=0                   Match by position index
  name:=Panel >> control:=Button >> text:=OK   Chained selectors

Examples:
  winauto doctor
  winauto apps
  winauto tree --app notepad
  winauto click "name:=OK" --app notepad
  winauto type "control:=Edit" "Hello World" --app notepad
  winauto screenshot --app notepad --output /tmp/notepad.png
  winauto codegen notepad.exe --output test_notepad.py
  winauto run test_notepad.py
"""

import sys
import os
import json
import time
import shutil
import argparse
import textwrap
import subprocess
from contextlib import contextmanager
from pathlib import Path


def _require_windows():
    if sys.platform != "win32":
        print("ERROR: winauto requires Windows. Current platform:", sys.platform, file=sys.stderr)
        sys.exit(1)


def _is_wsl() -> bool:
    """WSL 上の Linux Python で動いているか。"""
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False


def _import_pywinauto():
    try:
        import pywinauto
        return pywinauto
    except ImportError:
        print("ERROR: pywinauto not installed. Run: pip install pywinauto", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Desktop lock — GUI 操作の直列化
# ---------------------------------------------------------------------------
#
# Windows デスクトップは 1 セッションに 1 つしかない共有排他資源。agent-flow の
# 並列ワーカー（--workers N）のように複数プロセスが同時に set_focus() や
# click_input() を撃つと、フォーカスとマウス位置を奪い合って相互に壊れる。
# そこで入力・フォーカスを奪うコマンドをファイルロックで直列化する。
#
# WSL からの呼び出しもラッパー経由で Windows Python に収束するため、Windows 側の
# ロックファイル 1 本で「WSL 発」「Windows ネイティブ発」の双方を直列化できる。
# 分散実行（agent-flow --git）では PC ごとにデスクトップが別なので、PC 単位で
# ロックが閉じるこの構造が意味論的にも正しい。

LOCK_ENV_DISABLE = "WINAUTO_NO_LOCK"
LOCK_ENV_TIMEOUT = "WINAUTO_LOCK_TIMEOUT"
LOCK_ENV_FILE = "WINAUTO_LOCK_FILE"
DEFAULT_LOCK_TIMEOUT = 300.0

# デスクトップの状態を変える（フォーカス・マウス・キーボードを奪う）コマンドだけを
# ロックする。読み取り専用の apps / tree / get-text / wait / doctor はロックしない
# ——長い wait がロックを占有して他の発行を止めてしまわないようにするため。
LOCKED_COMMANDS = {"launch", "click", "type", "keys", "screenshot", "run", "inspect", "codegen"}


def _lock_path() -> Path:
    override = os.environ.get(LOCK_ENV_FILE)
    if override:
        return Path(override)
    tmp = (os.environ.get("TEMP") or os.environ.get("TMP")
           or os.environ.get("TMPDIR") or "/tmp")
    return Path(tmp) / "winauto-desktop.lock"


def _owner_path(lock: Path) -> Path:
    return lock.with_name(lock.name + ".owner")


def _try_lock(fh) -> bool:
    """ファイルハンドルに非ブロッキングで排他ロックを取る。取れたら True。"""
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _write_owner(lock: Path, command: str) -> None:
    """保持者情報を残す（待たされた側・doctor が「誰が握っているか」を言えるように）。"""
    try:
        _owner_path(lock).write_text(json.dumps({
            "pid": os.getpid(),
            "command": command,
            "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }), encoding="utf-8")
    except OSError:
        pass


def _clear_owner(lock: Path) -> None:
    try:
        _owner_path(lock).unlink()
    except OSError:
        pass


def _owner_hint(lock: Path) -> str:
    try:
        info = json.loads(_owner_path(lock).read_text(encoding="utf-8"))
        return f"pid={info.get('pid')} command={info.get('command')} since={info.get('since')}"
    except (OSError, ValueError):
        return "保持者不明"


@contextmanager
def desktop_lock(command: str, timeout: float, enabled: bool = True):
    """GUI を触るコマンドの間だけデスクトップを占有する。"""
    if not enabled or command not in LOCKED_COMMANDS:
        yield None
        return

    lock = _lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    fh = open(lock, "a+b")
    acquired = False
    try:
        deadline = time.time() + timeout if timeout > 0 else None
        notified = False
        while True:
            fh.seek(0)
            if _try_lock(fh):
                acquired = True
                break
            if not notified:
                # stdout はエージェントが読む結果なので、待機の告知は stderr に出す。
                print(f"[winauto] 他のプロセスが GUI を操作中です。待機します "
                      f"({_owner_hint(lock)})", file=sys.stderr)
                notified = True
            if deadline is not None and time.time() >= deadline:
                raise RuntimeError(
                    f"GUI ロックを {timeout:.0f} 秒以内に取得できませんでした "
                    f"({lock})。保持者: {_owner_hint(lock)}. "
                    f"待ち時間は --lock-timeout / {LOCK_ENV_TIMEOUT} で調整でき、"
                    f"--no-lock で無効化できます（並列 GUI 操作は相互に壊れます）"
                )
            time.sleep(0.5)

        _write_owner(lock, command)
        yield lock
    finally:
        if acquired:
            _clear_owner(lock)
            fh.seek(0)
            _unlock(fh)
        fh.close()


# ---------------------------------------------------------------------------
# Selector parser
# ---------------------------------------------------------------------------

SELECTOR_KEYS = {"name", "auto_id", "class", "control", "text", "index", "title"}

CONTROL_TYPE_MAP = {
    "button": "Button", "edit": "Edit", "text": "Static", "static": "Static",
    "checkbox": "CheckBox", "radio": "RadioButton", "listbox": "ListBox",
    "listitem": "ListItem", "combobox": "ComboBox", "tree": "Tree",
    "treeitem": "TreeItem", "menu": "Menu", "menuitem": "MenuItem",
    "toolbar": "ToolBar", "tab": "Tab", "tabitem": "TabItem",
    "pane": "Pane", "dialog": "Dialog", "window": "Window",
    "groupbox": "GroupBox", "scrollbar": "ScrollBar", "slider": "Slider",
    "spinner": "Spinner", "image": "Image", "progressbar": "ProgressBar",
    "calendar": "Calendar", "datepicker": "DataItem", "table": "Table",
    "header": "Header", "custom": "Custom",
}


def parse_selector_part(part: str) -> dict:
    """Parse a single selector like 'name:=Submit' into kwargs."""
    part = part.strip()
    if ":=" not in part:
        # Bare string = match by name/title
        return {"title": part}

    key, _, value = part.partition(":=")
    key = key.strip().lower()
    value = value.strip()

    if key not in SELECTOR_KEYS:
        raise ValueError(f"Unknown selector key '{key}'. Valid keys: {', '.join(sorted(SELECTOR_KEYS))}")

    kwargs = {}
    if key == "name" or key == "title":
        kwargs["title"] = value
    elif key == "auto_id":
        kwargs["auto_id"] = value
    elif key == "class":
        kwargs["class_name"] = value
    elif key == "control":
        ct = CONTROL_TYPE_MAP.get(value.lower(), value)
        kwargs["control_type"] = ct
    elif key == "text":
        kwargs["title"] = value
    elif key == "index":
        kwargs["found_index"] = int(value)
    return kwargs


def resolve_selector(root, selector: str):
    """
    Resolve a (possibly chained) selector string against a pywinauto element or app.
    Returns the matched element.
    """
    parts = [p.strip() for p in selector.split(">>")]
    current = root
    for part in parts:
        kwargs = parse_selector_part(part)
        try:
            current = current.child_window(**kwargs)
        except Exception as e:
            raise RuntimeError(f"Cannot resolve selector part '{part}': {e}") from e
    return current


# ---------------------------------------------------------------------------
# Element tree formatter
# ---------------------------------------------------------------------------

def format_element_info(elem, indent: int = 0, depth: int = 3, current_depth: int = 0) -> list[str]:
    """Recursively format element tree as lines of text."""
    lines = []
    prefix = "  " * indent
    try:
        info = elem.element_info
        ctrl_type = getattr(info, "control_type", "?")
        name = getattr(info, "name", "") or ""
        auto_id = getattr(info, "automation_id", "") or ""
        class_name = getattr(info, "class_name", "") or ""
        rect = elem.rectangle()
        rect_str = f"({rect.left},{rect.top})-({rect.right},{rect.bottom})"

        parts = [f"{prefix}[{ctrl_type}]"]
        if name:
            parts.append(f'name:="{name}"')
        if auto_id:
            parts.append(f"auto_id:={auto_id}")
        if class_name:
            parts.append(f"class:={class_name}")
        parts.append(rect_str)
        lines.append(" ".join(parts))

        if current_depth < depth:
            try:
                children = elem.children()
                for child in children:
                    lines.extend(format_element_info(
                        child, indent + 1, depth, current_depth + 1
                    ))
            except Exception:
                pass
    except Exception as e:
        lines.append(f"{prefix}[ERROR reading element: {e}]")
    return lines


def element_to_dict(elem, depth: int = 3, current_depth: int = 0) -> dict:
    """Recursively convert element tree to dict for JSON output."""
    try:
        info = elem.element_info
        d = {
            "control_type": getattr(info, "control_type", None),
            "name": getattr(info, "name", None),
            "auto_id": getattr(info, "automation_id", None),
            "class_name": getattr(info, "class_name", None),
        }
        try:
            rect = elem.rectangle()
            d["rect"] = {"left": rect.left, "top": rect.top,
                         "right": rect.right, "bottom": rect.bottom}
        except Exception:
            d["rect"] = None

        if current_depth < depth:
            try:
                d["children"] = [
                    element_to_dict(c, depth, current_depth + 1)
                    for c in elem.children()
                ]
            except Exception:
                d["children"] = []
        return d
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# App connection helpers
# ---------------------------------------------------------------------------

def connect_app(pywinauto, app_spec: str | None, backend: str):
    """Connect to a running app by process name, window title, or PID."""
    if app_spec is None:
        raise ValueError("--app is required. Use 'winauto apps' to list running apps.")

    Application = pywinauto.Application

    # Try as PID
    if app_spec.isdigit():
        return Application(backend=backend).connect(process=int(app_spec))

    # Try as window title
    try:
        return Application(backend=backend).connect(title_re=f".*{app_spec}.*")
    except Exception:
        pass

    # Try as process name
    try:
        return Application(backend=backend).connect(path=app_spec)
    except Exception:
        pass

    raise RuntimeError(
        f"Cannot find application '{app_spec}'. "
        "Check 'winauto apps' for running processes."
    )


def get_top_window(app):
    """Get the top-level window of an application."""
    try:
        return app.top_window()
    except Exception:
        windows = app.windows()
        if not windows:
            raise RuntimeError("No windows found for this application.")
        return windows[0]


# ---------------------------------------------------------------------------
# Command: apps
# ---------------------------------------------------------------------------

def cmd_apps(args):
    _require_windows()
    pyw = _import_pywinauto()

    from pywinauto import Desktop
    fmt = args.output

    apps = []
    desktop = Desktop(backend=args.backend)
    for win in desktop.windows():
        try:
            info = {
                "pid": win.process_id(),
                "title": win.window_text(),
                "class": win.class_name(),
                "handle": win.handle,
            }
            apps.append(info)
        except Exception:
            continue

    if fmt == "json":
        print(json.dumps(apps, ensure_ascii=False, indent=2))
    else:
        print(f"{'PID':>8}  {'CLASS':<30}  TITLE")
        print("-" * 70)
        for a in apps:
            title = a["title"][:50] if a["title"] else ""
            print(f"{a['pid']:>8}  {a['class']:<30}  {title}")


# ---------------------------------------------------------------------------
# Command: launch
# ---------------------------------------------------------------------------

def cmd_launch(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = pyw.Application(backend=args.backend).start(args.app_path)
    time.sleep(args.wait)

    win = get_top_window(app)
    info = {
        "pid": app.process,
        "title": win.window_text(),
        "class": win.class_name(),
        "backend": args.backend,
    }

    if args.output == "json":
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"Launched: {args.app_path}")
        print(f"PID: {info['pid']}")
        print(f"Window: {info['title']} ({info['class']})")
        print(f"Backend: {args.backend}")


# ---------------------------------------------------------------------------
# Command: tree
# ---------------------------------------------------------------------------

def cmd_tree(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)

    if args.selector:
        root = resolve_selector(win, args.selector)
        try:
            root.wait("exists", timeout=5)
        except Exception:
            pass
    else:
        root = win

    if args.output == "json":
        print(json.dumps(element_to_dict(root, depth=args.depth), ensure_ascii=False, indent=2))
    else:
        lines = format_element_info(root, depth=args.depth)
        print("\n".join(lines))


# ---------------------------------------------------------------------------
# Command: inspect (interactive REPL)
# ---------------------------------------------------------------------------

INSPECT_HELP = """
winauto inspect REPL — type commands to explore the UI tree.

Commands:
  tree [depth=N]            Print element tree (default depth=3)
  find SELECTOR             Find element by selector
  info SELECTOR             Show element properties
  click SELECTOR            Click element
  type SELECTOR TEXT        Type text into element
  keys SELECTOR KEY_COMBO   Send key combo (e.g. ^a, {ENTER})
  text SELECTOR             Get element text
  screenshot [PATH]         Save screenshot
  refresh                   Reload top window
  help                      Show this help
  quit / exit               Exit inspector

Selector examples:
  name:=OK
  auto_id:=textBox1
  control:=Button >> index:=0
"""

def cmd_inspect(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)

    print(f"Connected to: {win.window_text()} (backend={args.backend})")
    print("Type 'help' for commands, 'quit' to exit.\n")

    while True:
        try:
            line = input("winauto> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting inspector.")
            break

        if not line:
            continue

        tokens = line.split(None, 2)
        cmd = tokens[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(INSPECT_HELP)
            elif cmd == "refresh":
                win = get_top_window(app)
                print(f"Refreshed: {win.window_text()}")
            elif cmd == "tree":
                depth = 3
                if len(tokens) > 1 and tokens[1].startswith("depth="):
                    depth = int(tokens[1].split("=")[1])
                lines = format_element_info(win, depth=depth)
                print("\n".join(lines))
            elif cmd == "find":
                if len(tokens) < 2:
                    print("Usage: find SELECTOR")
                    continue
                elem = resolve_selector(win, tokens[1])
                elem.wait("exists", timeout=5)
                print(f"Found: {elem.window_text()!r} ({elem.friendly_class_name()})")
            elif cmd == "info":
                if len(tokens) < 2:
                    print("Usage: info SELECTOR")
                    continue
                elem = resolve_selector(win, tokens[1])
                elem.wait("exists", timeout=5)
                info = element_to_dict(elem, depth=0)
                print(json.dumps(info, ensure_ascii=False, indent=2))
            elif cmd == "click":
                if len(tokens) < 2:
                    print("Usage: click SELECTOR")
                    continue
                elem = resolve_selector(win, tokens[1])
                elem.wait("enabled", timeout=5)
                elem.click_input()
                print("Clicked.")
            elif cmd == "type":
                if len(tokens) < 3:
                    print("Usage: type SELECTOR TEXT")
                    continue
                elem = resolve_selector(win, tokens[1])
                elem.wait("enabled", timeout=5)
                elem.set_text(tokens[2])
                print(f"Typed: {tokens[2]!r}")
            elif cmd == "keys":
                if len(tokens) < 3:
                    print("Usage: keys SELECTOR KEY_COMBO")
                    continue
                elem = resolve_selector(win, tokens[1])
                elem.wait("enabled", timeout=5)
                elem.type_keys(tokens[2])
                print(f"Sent keys: {tokens[2]!r}")
            elif cmd == "text":
                if len(tokens) < 2:
                    print("Usage: text SELECTOR")
                    continue
                elem = resolve_selector(win, tokens[1])
                print(repr(elem.window_text()))
            elif cmd == "screenshot":
                path = tokens[1] if len(tokens) > 1 else "/tmp/winauto_screenshot.png"
                win.capture_as_image().save(path)
                print(f"Screenshot saved: {path}")
            else:
                print(f"Unknown command: {cmd!r}. Type 'help' for help.")
        except Exception as e:
            print(f"ERROR: {e}")


# ---------------------------------------------------------------------------
# Command: click
# ---------------------------------------------------------------------------

def cmd_click(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)
    elem = resolve_selector(win, args.selector)
    elem.wait("enabled", timeout=args.timeout)
    elem.click_input()

    if args.output == "json":
        print(json.dumps({"status": "ok", "selector": args.selector}))
    else:
        print(f"Clicked: {args.selector}")


# ---------------------------------------------------------------------------
# Command: type
# ---------------------------------------------------------------------------

def cmd_type(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)
    elem = resolve_selector(win, args.selector)
    elem.wait("enabled", timeout=args.timeout)

    if args.clear:
        elem.set_text("")
    if args.method == "set":
        elem.set_text(args.text)
    else:
        elem.type_keys(args.text, with_spaces=True)

    if args.output == "json":
        print(json.dumps({"status": "ok", "selector": args.selector, "text": args.text}))
    else:
        print(f"Typed into '{args.selector}': {args.text!r}")


# ---------------------------------------------------------------------------
# Command: keys
# ---------------------------------------------------------------------------

def cmd_keys(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)

    if args.selector:
        elem = resolve_selector(win, args.selector)
        elem.wait("enabled", timeout=args.timeout)
        elem.type_keys(args.keys)
    else:
        win.type_keys(args.keys)

    if args.output == "json":
        print(json.dumps({"status": "ok", "keys": args.keys}))
    else:
        print(f"Sent keys: {args.keys!r}")


# ---------------------------------------------------------------------------
# Command: get-text
# ---------------------------------------------------------------------------

def cmd_get_text(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)
    elem = resolve_selector(win, args.selector)
    elem.wait("exists", timeout=args.timeout)

    text = elem.window_text()

    if args.output == "json":
        print(json.dumps({"selector": args.selector, "text": text}))
    else:
        print(text)


# ---------------------------------------------------------------------------
# Command: screenshot
# ---------------------------------------------------------------------------

def cmd_screenshot(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)

    output_path = args.output or "/tmp/winauto_screenshot.png"
    win.set_focus()
    time.sleep(0.3)
    img = win.capture_as_image()
    img.save(output_path)

    if args.fmt == "json":
        print(json.dumps({"status": "ok", "path": output_path}))
    else:
        print(f"Screenshot saved: {output_path}")


# ---------------------------------------------------------------------------
# Command: wait
# ---------------------------------------------------------------------------

def cmd_wait(args):
    _require_windows()
    pyw = _import_pywinauto()

    app = connect_app(pyw, args.app, args.backend)
    win = get_top_window(app)
    elem = resolve_selector(win, args.selector)

    states = args.state.split(",")
    try:
        elem.wait(",".join(states), timeout=args.timeout)
        result = {"status": "found", "selector": args.selector, "state": args.state}
    except Exception as e:
        result = {"status": "timeout", "selector": args.selector, "error": str(e)}

    if args.output == "json":
        print(json.dumps(result))
    else:
        print(f"[{result['status'].upper()}] {args.selector} ({args.state})")
        if result["status"] == "timeout":
            sys.exit(1)


# ---------------------------------------------------------------------------
# Command: codegen
# ---------------------------------------------------------------------------

CODEGEN_TEMPLATE = '''\
#!/usr/bin/env python
"""
Auto-generated by: winauto codegen {app_path}
Backend: {backend}

Edit this script to complete your automation.
Run with: python {output_name}
"""

import time
from pywinauto import Application, Desktop
from pywinauto.keyboard import send_keys


BACKEND = "{backend}"
APP_PATH = r"{app_path}"
TIMEOUT = 10  # seconds


def main():
    # --- Launch or connect ---
    # Option A: Launch a new instance
    app = Application(backend=BACKEND).start(APP_PATH)
    time.sleep(1)  # wait for app to be ready

    # Option B: Attach to running instance (comment out Option A above)
    # app = Application(backend=BACKEND).connect(title_re=r".*{title_hint}.*")

    win = app.top_window()
    win.wait("ready", timeout=TIMEOUT)
    print(f"Connected: {{win.window_text()!r}}")

    # --- UI Element Tree (uncomment to inspect) ---
    # win.print_control_identifiers()

    # --- Automation steps ---
    # TODO: Add your automation steps here.
    #
    # Examples:
    #   win.child_window(title="OK", control_type="Button").click_input()
    #   win.child_window(auto_id="textBox1").set_text("Hello World")
    #   win.child_window(control_type="Edit").type_keys("Hello{{ENTER}}")
    #   win.capture_as_image().save("/tmp/screenshot.png")
    #   send_keys("^s")   # Ctrl+S
    #
{tree_comment}

    # --- Cleanup ---
    # app.kill()   # Uncomment to close the app when done


if __name__ == "__main__":
    main()
'''

def cmd_codegen(args):
    _require_windows()
    pyw = _import_pywinauto()

    output_path = args.output or f"automate_{Path(args.app_path).stem}.py"

    # Try to get window title for hint
    title_hint = Path(args.app_path).stem
    tree_lines = []
    try:
        app = pyw.Application(backend=args.backend).start(args.app_path)
        time.sleep(args.wait)
        win = get_top_window(app)
        title_hint = win.window_text() or title_hint
        tree_lines = format_element_info(win, depth=3)
        if not args.keep_open:
            app.kill()
    except Exception as e:
        print(f"Warning: Could not inspect running app: {e}", file=sys.stderr)

    tree_comment_lines = ["    # Element tree (captured at codegen time):"]
    for line in tree_lines[:60]:
        tree_comment_lines.append(f"    # {line}")
    if len(tree_lines) > 60:
        tree_comment_lines.append(f"    # ... ({len(tree_lines) - 60} more elements)")
    tree_comment = "\n".join(tree_comment_lines)

    script = CODEGEN_TEMPLATE.format(
        app_path=args.app_path,
        backend=args.backend,
        output_name=output_path,
        title_hint=title_hint,
        tree_comment=tree_comment,
    )

    Path(output_path).write_text(script, encoding="utf-8")

    if args.output_fmt == "json":
        print(json.dumps({"status": "ok", "output": output_path}))
    else:
        print(f"Generated: {output_path}")
        print(f"Edit the script, then run: python {output_path}")


# ---------------------------------------------------------------------------
# Command: run
# ---------------------------------------------------------------------------

def cmd_run(args):
    _require_windows()

    script = args.script
    if not Path(script).exists():
        print(f"ERROR: Script not found: {script}", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, script] + args.script_args
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Command: doctor
# ---------------------------------------------------------------------------
#
# 「WSL から Windows を触れる状態になっているか」を 1 コマンドで端から端まで確認する。
# Windows 側でも WSL 側でも動く（唯一 _require_windows() を呼ばないコマンド）。
# WSL で実行すると、ラッパー経由で Windows 側の doctor を呼び出して結果を合流させる
# ——「WSL から呼べる」ことの確認は、実際に WSL から呼んでみる以外に方法がないため。

DOCTOR_CHILD_ENV = "WINAUTO_DOCTOR_CHILD"

_STATUS_LABEL = {"ok": "OK", "warn": "WARN", "error": "ERROR", "skip": "SKIP"}


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _parse_json_tail(text: str):
    """出力に混じるノイズ（cmd.exe の UNC 警告など）を跨いで JSON 本体を拾う。"""
    start = text.find("{")
    while start != -1:
        try:
            return json.loads(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
    return None


def _doctor_lock_check() -> dict:
    lock = _lock_path()
    if not lock.exists():
        return _check("lock", "ok", f"未使用（{lock}）")
    try:
        fh = open(lock, "a+b")
    except OSError as e:
        return _check("lock", "warn", f"ロックファイルを開けません: {e}")
    try:
        fh.seek(0)
        if _try_lock(fh):
            fh.seek(0)
            _unlock(fh)
            return _check("lock", "ok", f"空き（{lock}）")
        return _check("lock", "warn", f"他プロセスが GUI を保持中: {_owner_hint(lock)}（{lock}）")
    finally:
        fh.close()


def doctor_windows() -> list:
    """Windows 側（実際に GUI を触る側）の診断。"""
    checks = [_check("platform", "ok",
                     f"Windows / Python {sys.version.split()[0]}（{sys.executable}）")]

    try:
        import pywinauto
    except ImportError as e:
        checks.append(_check(
            "pywinauto", "error",
            f"未インストール（{e}）。pip install pywinauto Pillow pywin32 comtypes"))
        return checks
    checks.append(_check("pywinauto", "ok",
                         f"バージョン {getattr(pywinauto, '__version__', '不明')}"))

    try:
        from pywinauto import Desktop
        titled = [w for w in Desktop(backend="uia").windows() if w.window_text().strip()]
        if titled:
            checks.append(_check("desktop", "ok",
                                 f"対話デスクトップに到達（可視ウィンドウ {len(titled)} 個）"))
        else:
            checks.append(_check(
                "desktop", "warn",
                "ウィンドウが 1 つも見えません。画面ロック中・RDP 切断中・未ログオンの"
                "セッションでは GUI 操作もスクリーンショットも失敗します"))
    except Exception as e:
        checks.append(_check("desktop", "error", f"デスクトップに到達できません: {e}"))

    checks.append(_doctor_lock_check())
    return checks


def doctor_wsl() -> list:
    """WSL 側の診断 — interop → ラッパー → Windows 側 doctor まで一気通貫で確認する。"""
    checks = [_check("platform", "ok", f"WSL / Python {sys.version.split()[0]}")]

    if os.environ.get(DOCTOR_CHILD_ENV):
        checks.append(_check(
            "bridge", "error",
            "ラッパーが Windows Python ではなく WSL 側の Python を呼び戻しています。"
            "python tools/winauto/install.py を実行し直してください"))
        return checks

    try:
        r = subprocess.run(["cmd.exe", "/c", "echo", "winauto-interop-ok"],
                           capture_output=True, text=True, timeout=20)
        if "winauto-interop-ok" in r.stdout:
            checks.append(_check("wsl_interop", "ok",
                                 "cmd.exe を起動できます（Windows 相互運用 有効）"))
        else:
            checks.append(_check("wsl_interop", "error",
                                 f"cmd.exe の応答が不正です: {(r.stdout + r.stderr).strip()[:120]}"))
    except Exception as e:
        checks.append(_check("wsl_interop", "error",
                             f"cmd.exe を起動できません（interop 無効の可能性）: {e}"))

    wrapper = shutil.which("winauto")
    if not wrapper:
        checks.append(_check(
            "wrapper", "error",
            "winauto が PATH にありません。python tools/winauto/install.py を実行してください"))
        return checks
    checks.append(_check("wrapper", "ok", wrapper))

    env = dict(os.environ)
    env[DOCTOR_CHILD_ENV] = "1"
    try:
        r = subprocess.run([wrapper, "doctor", "--output", "json"],
                           capture_output=True, text=True, timeout=180, env=env)
    except Exception as e:
        checks.append(_check("bridge", "error", f"ラッパーを実行できません: {e}"))
        return checks

    payload = _parse_json_tail(r.stdout)
    if payload is None:
        detail = (r.stderr or r.stdout).strip()[:200] or f"終了コード {r.returncode}"
        checks.append(_check(
            "bridge", "error",
            f"Windows 側 doctor の応答を解釈できません（{detail}）。Windows 側に配置された "
            "winauto.py が古い可能性があります。install.py を再実行してください"))
        return checks

    checks.append(_check("bridge", "ok", "ラッパー経由で Windows 側 winauto に到達できます"))
    for c in payload.get("checks", []):
        checks.append(_check(f"windows:{c['name']}", c["status"], c["detail"]))
    return checks


def cmd_doctor(args):
    if sys.platform == "win32":
        scope, checks = "windows", doctor_windows()
    elif _is_wsl():
        scope, checks = "wsl", doctor_wsl()
    else:
        scope = "unsupported"
        checks = [_check("platform", "error",
                         f"{sys.platform} は非対応です。pywinauto は Windows 専用で、"
                         "WSL からは Windows 側 Python 経由で呼び出します")]

    failed = [c for c in checks if c["status"] == "error"]

    if args.output == "json":
        print(json.dumps({"scope": scope, "ok": not failed, "checks": checks},
                         ensure_ascii=False, indent=2))
    else:
        print(f"winauto doctor — {scope}")
        print("-" * 68)
        for c in checks:
            print(f"[{_STATUS_LABEL[c['status']]:>5}] {c['name']}: {c['detail']}")
        print("-" * 68)
        print("すべて到達可能です。" if not failed else f"{len(failed)} 件の問題があります。")

    sys.exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="winauto",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="winauto 1.1.0")
    # GUI を触るコマンドはデスクトップを排他する（LOCKED_COMMANDS 参照）。
    # サブコマンドより前に置く: winauto --no-lock click ...
    parser.add_argument("--no-lock", action="store_true",
                        help="デスクトップ排他ロックを取らない（並列 GUI 操作は相互に壊れます）")
    parser.add_argument("--lock-timeout", type=float, default=None, metavar="SEC",
                        help=f"ロック待ちの上限秒（既定 {DEFAULT_LOCK_TIMEOUT:.0f}、0 で無限待ち）")

    sub = parser.add_subparsers(dest="command", required=True)

    # --- doctor ---
    p = sub.add_parser("doctor", help="Diagnose the WSL/Windows automation bridge")
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- apps ---
    p = sub.add_parser("apps", help="List running automatable applications")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- launch ---
    p = sub.add_parser("launch", help="Launch an application")
    p.add_argument("app_path", help="Path to executable or app name")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--wait", type=float, default=1.0, help="Seconds to wait after launch")
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- tree ---
    p = sub.add_parser("tree", help="Print UI element tree")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, window title, or PID")
    p.add_argument("--selector", metavar="SELECTOR", help="Start tree from this element")
    p.add_argument("--depth", type=int, default=3, help="Tree depth (default: 3)")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- inspect ---
    p = sub.add_parser("inspect", help="Interactive element inspector REPL")
    p.add_argument("--app", metavar="NAME_OR_PID", required=True, help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")

    # --- click ---
    p = sub.add_parser("click", help="Click an element")
    p.add_argument("selector", help="Element selector")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- type ---
    p = sub.add_parser("type", help="Type text into an element")
    p.add_argument("selector", help="Element selector")
    p.add_argument("text", help="Text to type")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--clear", action="store_true", help="Clear field before typing")
    p.add_argument("--method", choices=["set", "keys"], default="set",
                   help="set=set_text (fast), keys=type_keys (simulated keystrokes)")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- keys ---
    p = sub.add_parser("keys", help="Send key sequence")
    p.add_argument("keys", help="Key combo (e.g. ^a, {ENTER}, ^s)")
    p.add_argument("--selector", metavar="SELECTOR", help="Target element (optional, default=window)")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- get-text ---
    p = sub.add_parser("get-text", help="Get text content of an element")
    p.add_argument("selector", help="Element selector")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- screenshot ---
    p = sub.add_parser("screenshot", help="Capture screenshot of app window")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--output", metavar="PATH", default=None, help="Output file path (default: /tmp/winauto_screenshot.png)")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--fmt", choices=["text", "json"], default="text")

    # --- wait ---
    p = sub.add_parser("wait", help="Wait for an element state")
    p.add_argument("selector", help="Element selector")
    p.add_argument("--state", default="exists",
                   help="Comma-separated states: exists,visible,enabled,ready,active (default: exists)")
    p.add_argument("--app", metavar="NAME_OR_PID", help="Process name, title, or PID")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--output", choices=["text", "json"], default="text")

    # --- codegen ---
    p = sub.add_parser("codegen", help="Generate automation script template")
    p.add_argument("app_path", help="Path to executable to launch for inspection")
    p.add_argument("--output", metavar="SCRIPT.py", default=None, help="Output script path")
    p.add_argument("--backend", choices=["uia", "win32"], default="uia")
    p.add_argument("--wait", type=float, default=1.0, help="Seconds to wait after launch")
    p.add_argument("--keep-open", action="store_true", help="Don't close app after inspection")
    p.add_argument("--output-fmt", choices=["text", "json"], default="text")

    # --- run ---
    p = sub.add_parser("run", help="Run an automation script")
    p.add_argument("script", help="Path to Python automation script")
    p.add_argument("script_args", nargs=argparse.REMAINDER, help="Arguments passed to script")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMAND_MAP = {
    "doctor": cmd_doctor,
    "apps": cmd_apps,
    "launch": cmd_launch,
    "tree": cmd_tree,
    "inspect": cmd_inspect,
    "click": cmd_click,
    "type": cmd_type,
    "keys": cmd_keys,
    "get-text": cmd_get_text,
    "screenshot": cmd_screenshot,
    "wait": cmd_wait,
    "codegen": cmd_codegen,
    "run": cmd_run,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    handler = COMMAND_MAP.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")

    lock_enabled = not (args.no_lock or os.environ.get(LOCK_ENV_DISABLE) == "1")
    if args.lock_timeout is not None:
        lock_timeout = args.lock_timeout
    else:
        try:
            lock_timeout = float(os.environ.get(LOCK_ENV_TIMEOUT, DEFAULT_LOCK_TIMEOUT))
        except ValueError:
            lock_timeout = DEFAULT_LOCK_TIMEOUT

    try:
        with desktop_lock(args.command, lock_timeout, lock_enabled):
            handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        if os.environ.get("WINAUTO_DEBUG"):
            import traceback
            traceback.print_exc()
        else:
            print(f"ERROR: {e}", file=sys.stderr)
            print("Set WINAUTO_DEBUG=1 for full traceback.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
