#!/usr/bin/env python
"""
app_launcher.py — Launch a Windows app and wait until it's ready, then run a command.

Pattern mirrors webapp-testing's with_server.py — manages lifecycle so automation
scripts don't need to handle launch/teardown themselves.

Usage:
    # Launch notepad, run automation, auto-close
    python app_launcher.py --app notepad.exe -- python my_automation.py

    # Launch with custom wait, keep app open
    python app_launcher.py --app "C:/MyApp/app.exe" --wait 3 --keep -- python test.py

    # Use Win32 backend
    python app_launcher.py --app myapp.exe --backend win32 -- python test.py

Environment variables set for the child command:
    WINAUTO_APP_PID        PID of the launched application
    WINAUTO_APP_TITLE      Window title of the launched application
    WINAUTO_BACKEND        Backend used (uia or win32)

Requirements:
    pip install pywinauto
"""

import sys
import os
import time
import signal
import argparse
import subprocess


def wait_for_window(app, timeout: float = 30.0) -> str:
    """Wait until app has a visible top-level window. Returns window title."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            windows = app.windows()
            for win in windows:
                try:
                    title = win.window_text()
                    if title:
                        return title
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"App window did not appear within {timeout}s")


def main():
    parser = argparse.ArgumentParser(
        description="Launch a Windows app and run a command against it"
    )
    parser.add_argument("--app", required=True, help="Path to executable or app name")
    parser.add_argument("--backend", choices=["uia", "win32"], default="uia")
    parser.add_argument("--wait", type=float, default=1.5,
                        help="Extra seconds to wait after window appears (default: 1.5)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Max seconds to wait for app window (default: 30)")
    parser.add_argument("--keep", action="store_true",
                        help="Keep app running after command finishes")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Command to run (after --)")
    args = parser.parse_args()

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("No command specified. Add -- python your_script.py")

    try:
        from pywinauto import Application
    except ImportError:
        print("ERROR: pywinauto not installed. Run: pip install pywinauto", file=sys.stderr)
        sys.exit(1)

    print(f"[winauto] Launching: {args.app}", file=sys.stderr)
    app = Application(backend=args.backend).start(args.app)

    try:
        title = wait_for_window(app, timeout=args.timeout)
        print(f"[winauto] Window ready: {title!r} (PID={app.process})", file=sys.stderr)
    except TimeoutError as e:
        print(f"[winauto] ERROR: {e}", file=sys.stderr)
        try:
            app.kill()
        except Exception:
            pass
        sys.exit(1)

    if args.wait > 0:
        time.sleep(args.wait)

    env = os.environ.copy()
    env["WINAUTO_APP_PID"] = str(app.process)
    env["WINAUTO_APP_TITLE"] = title
    env["WINAUTO_BACKEND"] = args.backend

    print(f"[winauto] Running: {' '.join(args.command)}", file=sys.stderr)
    exit_code = 0
    try:
        result = subprocess.run(args.command, env=env)
        exit_code = result.returncode
    except KeyboardInterrupt:
        print("\n[winauto] Interrupted.", file=sys.stderr)
        exit_code = 130
    finally:
        if not args.keep:
            print("[winauto] Closing app...", file=sys.stderr)
            try:
                app.kill()
            except Exception:
                pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
