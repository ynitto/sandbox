#!/usr/bin/env python
"""
element_inspector.py — Windows UI element tree inspector

Dumps the UI element tree of a running Windows application.
Useful for identifying element selectors before writing automation scripts.

Usage:
    # Inspect by window title (partial match)
    python element_inspector.py --app notepad

    # Inspect by PID
    python element_inspector.py --app 12345

    # Inspect with Win32 backend
    python element_inspector.py --app notepad --backend win32

    # Inspect sub-tree starting from an element
    python element_inspector.py --app notepad --selector "name:=Edit"

    # Limit depth
    python element_inspector.py --app notepad --depth 5

    # Output JSON for machine processing
    python element_inspector.py --app notepad --json

    # List all visible top-level windows
    python element_inspector.py --list

Requirements:
    pip install pywinauto
"""

import sys
import json
import argparse


def list_apps(backend: str):
    from pywinauto import Desktop
    desktop = Desktop(backend=backend)
    print(f"{'PID':>8}  {'CLASS':<32}  TITLE")
    print("-" * 72)
    for win in desktop.windows():
        try:
            title = win.window_text()
            if not title:
                continue
            pid = win.process_id()
            cls = win.class_name()
            print(f"{pid:>8}  {cls:<32}  {title[:50]}")
        except Exception:
            continue


def connect(app_spec: str, backend: str):
    from pywinauto import Application
    if app_spec.isdigit():
        return Application(backend=backend).connect(process=int(app_spec))
    try:
        return Application(backend=backend).connect(title_re=f".*{app_spec}.*")
    except Exception:
        return Application(backend=backend).connect(path=app_spec)


def parse_selector(win, selector: str):
    """Parse chained selector and return target element."""
    parts = [p.strip() for p in selector.split(">>")]
    current = win
    for part in parts:
        if ":=" not in part:
            current = current.child_window(title=part)
            continue
        key, _, value = part.partition(":=")
        key = key.strip().lower()
        value = value.strip()
        kwargs = {}
        if key in ("name", "title"):
            kwargs["title"] = value
        elif key == "auto_id":
            kwargs["auto_id"] = value
        elif key == "class":
            kwargs["class_name"] = value
        elif key == "control":
            kwargs["control_type"] = value
        elif key == "index":
            kwargs["found_index"] = int(value)
        current = current.child_window(**kwargs)
    return current


def print_tree(elem, indent: int = 0, depth: int = 4, current_depth: int = 0):
    """Print element tree in a readable format with selector hints."""
    prefix = "  " * indent
    try:
        info = elem.element_info
        ctrl_type = getattr(info, "control_type", "?")
        name = getattr(info, "name", "") or ""
        auto_id = getattr(info, "automation_id", "") or ""
        class_name = getattr(info, "class_name", "") or ""

        # Build selector hint
        hints = []
        if auto_id:
            hints.append(f'auto_id:={auto_id}')
        if name:
            hints.append(f'name:="{name}"')
        if ctrl_type:
            hints.append(f'control:={ctrl_type}')

        selector_hint = " | ".join(hints[:2]) if hints else ""

        try:
            rect = elem.rectangle()
            rect_str = f"@({rect.left},{rect.top})"
        except Exception:
            rect_str = ""

        line = f"{prefix}[{ctrl_type}]"
        if selector_hint:
            line += f"  ← {selector_hint}"
        if class_name and class_name not in ctrl_type:
            line += f"  class:{class_name}"
        if rect_str:
            line += f"  {rect_str}"

        print(line)

        if current_depth < depth:
            try:
                for child in elem.children():
                    print_tree(child, indent + 1, depth, current_depth + 1)
            except Exception:
                pass
    except Exception as e:
        print(f"{prefix}[ERROR: {e}]")


def elem_to_dict(elem, depth: int = 4, current_depth: int = 0) -> dict:
    try:
        info = elem.element_info
        d = {
            "control_type": getattr(info, "control_type", None),
            "name": getattr(info, "name", None),
            "auto_id": getattr(info, "automation_id", None),
            "class_name": getattr(info, "class_name", None),
        }
        try:
            r = elem.rectangle()
            d["rect"] = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
        except Exception:
            d["rect"] = None

        if current_depth < depth:
            try:
                d["children"] = [elem_to_dict(c, depth, current_depth + 1) for c in elem.children()]
            except Exception:
                d["children"] = []
        return d
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Windows UI element tree inspector")
    parser.add_argument("--app", metavar="NAME_OR_PID", help="App to inspect")
    parser.add_argument("--selector", metavar="SELECTOR", help="Start from sub-element")
    parser.add_argument("--depth", type=int, default=4, help="Tree depth (default: 4)")
    parser.add_argument("--backend", choices=["uia", "win32"], default="uia")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--list", action="store_true", help="List running apps and exit")
    args = parser.parse_args()

    if args.list:
        list_apps(args.backend)
        return

    if not args.app:
        parser.error("--app is required (or use --list to list apps)")

    app = connect(args.app, args.backend)
    win = app.top_window()
    win.wait("ready", timeout=10)

    print(f"\nApp: {win.window_text()!r}  (PID={app.process}, backend={args.backend})")
    print(f"Depth: {args.depth}  Selector: {args.selector or '(root)'}\n")

    root = parse_selector(win, args.selector) if args.selector else win

    if args.json:
        result = elem_to_dict(root, depth=args.depth)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Selector hints:")
        print("  Use auto_id:=... for stable IDs (preferred)")
        print("  Use name:=... for labels, use control:=... for types")
        print("  Chain with >> for nested elements\n")
        print_tree(root, depth=args.depth)


if __name__ == "__main__":
    main()
