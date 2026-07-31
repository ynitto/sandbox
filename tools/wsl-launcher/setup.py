#!/usr/bin/env python3
"""
WSL Terminal Launcher - セットアップウィザード

使用方法:
  python setup.py            # セットアップウィザードを起動
  python setup.py --status   # 登録状況を確認
  python setup.py --unregister  # スタートアップ登録を解除
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    import winreg

# UTF-8 コンソール出力 (Windows の Shift-JIS 環境対策)
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

# ── パス定義 ─────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent
LAUNCHER_PATH = SCRIPT_DIR / "launch.pyw"
CONFIG_PATH   = SCRIPT_DIR / "config.json"
TASK_NAME     = "WslTerminalLauncher"
WARMUP_TASK   = "WslWarmup"
REG_RUN_PATH  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
REG_VAL_LAUNCHER  = "WslTerminalLauncher"
REG_VAL_AUTOSTART = "WSLAutostart"


# ── UI ヘルパー ──────────────────────────────────────────────
W = 55

def header(title: str) -> None:
    print(f"\n{'=' * W}\n  {title}\n{'=' * W}")

def step(msg: str)  -> None: print(f"[*] {msg}")
def ok(msg: str)    -> None: print(f"[OK] {msg}")
def warn(msg: str)  -> None: print(f"[!]  {msg}")
def err(msg: str)   -> None: print(f"[NG] {msg}")

def ask_yn(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        ans = input(f"{question} {hint}: ").strip().lower()
        if not ans:       return default
        if ans in ("y",): return True
        if ans in ("n",): return False
        print("  y か n を入力してください。")

def ask_input(prompt: str, default: str = "") -> str:
    if default:
        ans = input(f"{prompt} [{default}]: ").strip()
        return ans or default
    while True:
        ans = input(f"{prompt}: ").strip()
        if ans:
            return ans
        print("  値を入力してください。")


# ── Task Scheduler (schtasks.exe) ────────────────────────────
def _current_user() -> str:
    return os.environ.get("USERDOMAIN", ".") + "\\" + os.environ["USERNAME"]

def _make_task_xml(cmd: str, args: str, work_dir: str,
                   user_id: str, delay_sec: int = 0) -> str:
    delay = f"<Delay>PT{delay_sec}S</Delay>" if delay_sec else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <Triggers>\n'
        '    <LogonTrigger>\n'
        '      <Enabled>true</Enabled>\n'
        f'      <UserId>{user_id}</UserId>\n'
        f'      {delay}\n'
        '    </LogonTrigger>\n'
        '  </Triggers>\n'
        '  <Actions Context="Author">\n'
        '    <Exec>\n'
        f'      <Command>{cmd}</Command>\n'
        f'      <Arguments>{args}</Arguments>\n'
        f'      <WorkingDirectory>{work_dir}</WorkingDirectory>\n'
        '    </Exec>\n'
        '  </Actions>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <AllowStartIfOnBatteries>true</AllowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '  </Settings>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '      <RunLevel>LeastPrivilege</RunLevel>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '</Task>'
    )

def _register_task(task_name: str, xml_content: str) -> bool:
    xml_path = Path(os.environ["TEMP"]) / f"{task_name}.xml"
    try:
        xml_path.write_bytes(xml_content.encode("utf-16"))
        r = subprocess.run(
            ["schtasks.exe", "/Create", "/XML", str(xml_path), "/TN", task_name, "/F"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            err(f"schtasks エラー: {r.stderr.strip()}")
        return r.returncode == 0
    except Exception as e:
        err(f"タスク登録例外: {e}")
        return False
    finally:
        xml_path.unlink(missing_ok=True)

def _delete_task(task_name: str) -> bool:
    r = subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── config.json ──────────────────────────────────────────────
def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "settings": {
            "terminalApp": "wt",
            "delayBetweenLaunchesMs": 500,
            "defaultDistro": "Ubuntu",
            "wslWaitEnabled": True,
            "wslWaitTimeoutSeconds": 60,
        },
        "terminals": [],
    }

def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ok("config.json を保存しました。")

def _print_terminals(cfg: dict) -> None:
    print("\n  現在のターミナル設定:")
    if not cfg.get("terminals"):
        print("    (未登録)")
    else:
        for i, t in enumerate(cfg["terminals"], 1):
            s = "有効" if t.get("enabled") else "無効"
            print(f"    [{i}] {t['name']} ({s})  {t['wslPath']} -> {t['command']}")
    print()


# ── STEP 1: 前提条件チェック ─────────────────────────────────
def check_prerequisites() -> bool:
    header("STEP 1: 前提条件チェック")
    result = True

    step("launch.pyw の確認...")
    if LAUNCHER_PATH.exists():
        ok("launch.pyw が見つかりました。")
    else:
        err(f"launch.pyw が見つかりません: {LAUNCHER_PATH}")
        result = False

    step("WSL の確認...")
    if shutil.which("wsl.exe"):
        ok("wsl.exe が見つかりました。")
    else:
        err("wsl.exe が見つかりません。WSL をインストールしてください。")
        result = False

    step("WSL ディストロの確認...")
    try:
        r = subprocess.run(["wsl.exe", "--list", "--quiet"],
                           capture_output=True, timeout=10)
        distros = [
            ln for ln in r.stdout.decode("utf-16-le", errors="replace").splitlines()
            if ln.strip()
        ]
        if distros:
            ok("利用可能なディストロ:")
            for d in distros:
                print(f"       - {d}")
        else:
            warn("WSL ディストロが見つかりません。wsl --install で導入してください。")
    except Exception as e:
        warn(f"ディストロ一覧の取得に失敗: {e}")

    step("Windows Terminal (wt.exe) の確認...")
    if shutil.which("wt.exe"):
        ok("wt.exe が見つかりました。")
    else:
        warn("wt.exe が見つかりません。wsl.exe の個別ウィンドウで起動します。")

    print()
    return result


# ── STEP 2: config.json 編集 ─────────────────────────────────
def edit_config() -> None:
    header("STEP 2: ターミナル設定 (config.json)")
    cfg     = _load_config()
    changed = False

    _print_terminals(cfg)

    while True:
        print("  操作を選択してください:")
        print("    a) ターミナルを追加")
        print("    t) ターミナルを切替 (有効/無効)")
        print("    d) ターミナルを削除")
        print("    s) 保存して次へ")
        print()
        choice = input("  選択 [a/t/d/s]: ").strip().lower()

        if choice == "a":
            name   = ask_input("    表示名")
            path   = ask_input("    WSL パス (例: /home/user/myproject)")
            cmd    = ask_input("    実行コマンド (例: npm run dev)")
            distro = ask_input("    ディストロ名", cfg["settings"].get("defaultDistro", "Ubuntu"))
            keep   = ask_yn("    コマンド終了後もシェルを維持しますか?")
            cfg["terminals"].append({
                "name": name, "wslPath": path, "command": cmd,
                "distro": distro, "keepOpen": keep, "enabled": True,
            })
            ok(f"'{name}' を追加しました。")
            changed = True

        elif choice == "t":
            if not cfg["terminals"]:
                warn("ターミナルが登録されていません。"); continue
            _print_terminals(cfg)
            n = int(ask_input(f"    切り替える番号 (1-{len(cfg['terminals'])})")) - 1
            if 0 <= n < len(cfg["terminals"]):
                t = cfg["terminals"][n]
                t["enabled"] = not t.get("enabled", True)
                ok(f"'{t['name']}' を {'有効' if t['enabled'] else '無効'} にしました。")
                changed = True
            else:
                warn("無効な番号です。")

        elif choice == "d":
            if not cfg["terminals"]:
                warn("ターミナルが登録されていません。"); continue
            _print_terminals(cfg)
            n = int(ask_input(f"    削除する番号 (1-{len(cfg['terminals'])})")) - 1
            if 0 <= n < len(cfg["terminals"]):
                name = cfg["terminals"][n]["name"]
                if ask_yn(f"    '{name}' を削除しますか?", default=False):
                    cfg["terminals"].pop(n)
                    ok(f"'{name}' を削除しました。")
                    changed = True
            else:
                warn("無効な番号です。")

        elif choice == "s":
            if changed:
                _save_config(cfg)
            print()
            return

        if changed and choice in ("a", "t", "d"):
            _print_terminals(cfg)


# ── STEP 3: WSL 自動起動設定 ────────────────────────────────
def _register_autostart_registry(vbs_path: Path) -> None:
    """wsl-autostart の start.vbs をレジストリ Run キーに登録する"""
    reg_data = f'wscript "{vbs_path}"'
    for hive, hive_name in [
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
        (winreg.HKEY_CURRENT_USER,  "HKCU"),
    ]:
        try:
            with winreg.OpenKey(hive, REG_RUN_PATH, 0, winreg.KEY_WRITE) as k:
                winreg.SetValueEx(k, REG_VAL_AUTOSTART, 0, winreg.REG_SZ, reg_data)
            ok(f"{hive_name} Run に登録しました。")
            print(f"  起動コマンド  : {reg_data}")
            print(f"  サービス追加  : {vbs_path.parent}\\commands.txt を編集してください")
            return
        except OSError:
            if hive == winreg.HKEY_LOCAL_MACHINE:
                warn("HKLM への書き込みに失敗 (管理者権限が必要)。HKCU にフォールバックします...")
            else:
                err("レジストリ登録に失敗しました。")

def _setup_wsl_autostart_oss() -> None:
    """troytse/wsl-autostart を GitHub からダウンロードしてレジストリに登録する"""
    install_dir = Path(ask_input("  インストール先", r"C:\wsl-autostart"))
    zip_url     = "https://github.com/troytse/wsl-autostart/archive/refs/heads/master.zip"
    temp_zip    = Path(os.environ["TEMP"]) / "wsl-autostart.zip"
    temp_dir    = Path(os.environ["TEMP"]) / "wsl-autostart-extract"

    # 既存インストールがあればスキップ可能
    vbs_path = install_dir / "start.vbs"
    if install_dir.exists() and vbs_path.exists():
        if not ask_yn(f"  {install_dir} に既にインストールされています。再インストールしますか?", False):
            _register_autostart_registry(vbs_path)
            return

    step(f"ダウンロード中: {zip_url}")
    try:
        urllib.request.urlretrieve(zip_url, temp_zip)
        ok("ダウンロード完了")
    except Exception as e:
        err(f"ダウンロードに失敗しました: {e}")
        warn("手動ダウンロード先: https://github.com/troytse/wsl-autostart")
        return

    step("展開中...")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    try:
        with zipfile.ZipFile(temp_zip) as zf:
            zf.extractall(temp_dir)
    except Exception as e:
        err(f"展開に失敗しました: {e}"); return

    src = temp_dir / "wsl-autostart-master"
    if not src.exists():
        err("展開先に wsl-autostart-master が見つかりません"); return

    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.copytree(src, install_dir)
    ok(f"インストール先: {install_dir}")

    commands_path = install_dir / "commands.txt"
    if not commands_path.exists():
        commands_path.write_text("", encoding="utf-8")
    ok(f"commands.txt: {commands_path}")
    print("  (起動したい Linux サービスがあれば 1 行 1 コマンドで追記してください)")

    vbs_path = install_dir / "start.vbs"
    if not vbs_path.exists():
        warn("start.vbs が見つかりません。リポジトリの構成を確認してください。"); return

    _register_autostart_registry(vbs_path)

def _register_wsl_warmup_task() -> None:
    """ログオン時に wsl.exe を先行実行して WSL をウォームアップするタスクを登録する"""
    cfg    = _load_config()
    distro = cfg.get("settings", {}).get("defaultDistro", "")
    args   = f'-d "{distro}" --exec echo warmup' if distro else "--exec echo warmup"

    xml = _make_task_xml("wsl.exe", args, str(SCRIPT_DIR), _current_user())
    if _register_task(WARMUP_TASK, xml):
        ok(f"タスク '{WARMUP_TASK}' を登録しました。")
        if distro:
            print(f"  対象ディストロ: {distro}")
    else:
        err("管理者権限で実行してください。")

def setup_wsl_autostart() -> None:
    header("STEP 3: WSL 自動起動設定 (オプション)")
    print("  WT がタブを開く前に WSL を起動しておくことで")
    print("  初回ログイン時の遅延・エラーを根本から解消できます。")
    print()
    if not ask_yn("  WSL 自動起動を設定しますか?"):
        print("  スキップしました。"); print(); return

    print()
    print("  方式を選択してください:")
    print("    1) wsl-autostart (troytse/wsl-autostart) [OSS]")
    print("       GitHub からダウンロード。Linux サービスの起動にも対応。")
    print("    2) タスクスケジューラ ウォームアップ (追加ダウンロードなし)")
    print()
    method = ""
    while method not in ("1", "2"):
        method = input("  選択 [1/2]: ").strip()

    if method == "1":
        _setup_wsl_autostart_oss()
    else:
        _register_wsl_warmup_task()
    print()


# ── STEP 4: スタートアップ登録 ───────────────────────────────
def _find_pythonw() -> str:
    """pythonw.exe のフルパスを返す"""
    candidate = Path(sys.executable).parent / "pythonw.exe"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("pythonw.exe")
    if found:
        return found
    err("pythonw.exe が見つかりません。Python を再インストールしてください。")
    return "pythonw.exe"

def _register_wt_settings() -> None:
    """WT settings.json に startOnUserLogin と startupActions を設定する"""
    candidates = [
        Path(os.environ["LOCALAPPDATA"])
        / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json",
        Path(os.environ["LOCALAPPDATA"])
        / "Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json",
    ]
    settings_path = next((p for p in candidates if p.exists()), None)
    if not settings_path:
        warn("settings.json が見つかりません。WT を一度起動してから再実行してください。")
        return
    print(f"  設定ファイル: {settings_path}")

    cfg       = _load_config()
    terminals = [t for t in cfg.get("terminals", []) if t.get("enabled")]
    default   = cfg.get("settings", {}).get("defaultDistro", "Ubuntu")
    if not terminals:
        warn("有効なターミナルが設定されていません。"); return

    # startupActions を構築
    parts = []
    for i, t in enumerate(terminals):
        distro    = t.get("distro") or default
        keep_open = t.get("keepOpen", True)
        safe_cmd  = t["command"].replace("'", "'\\''")
        inner     = f"bash -c '{safe_cmd}; exec bash'" if keep_open else f"bash -c '{safe_cmd}'"
        tab = f'new-tab --title "{t["name"]}"'
        if i == 0:
            tab += ' --tabColor "#0078D4"'
        tab += f' -- wsl.exe -d "{distro}" --cd "{t["wslPath"]}" -- {inner}'
        parts.append(tab)
    startup_actions = " ; ".join(parts)

    # バックアップ
    backup = settings_path.with_suffix(f".json.bak.{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy(settings_path, backup)
    ok(f"バックアップ: {backup}")

    # JSONC コメントを除去してパース
    raw = settings_path.read_text(encoding="utf-8")
    raw = re.sub(r"//[^\r\n]*", "", raw)
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    settings = json.loads(raw)

    settings["startOnUserLogin"] = True
    settings["startupActions"]   = startup_actions
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    ok("settings.json を更新しました。")
    print("  startOnUserLogin: true")
    print(f"  startupActions  : {startup_actions[:80]}...")
    print()
    warn(f"元に戻す場合はバックアップから復元してください: {backup}")

def _register_hkcu_run() -> None:
    """HKCU Run に pythonw.exe launch.pyw を登録する (コンソールなし)"""
    pythonw  = _find_pythonw()
    reg_data = f'"{pythonw}" "{LAUNCHER_PATH}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, REG_VAL_LAUNCHER, 0, winreg.REG_SZ, reg_data)
        ok("HKCU Run に登録しました。")
        print(f"  起動コマンド: {reg_data}")
    except OSError as e:
        err(f"レジストリ登録に失敗しました: {e}")

def _register_task_scheduler() -> None:
    """Task Scheduler に launch.pyw を AtLogon で登録する"""
    delay   = int(ask_input("  ログオン後の起動遅延秒数", "10"))
    pythonw = _find_pythonw()
    xml = _make_task_xml(
        cmd=pythonw, args=f'"{LAUNCHER_PATH}"',
        work_dir=str(SCRIPT_DIR), user_id=_current_user(), delay_sec=delay,
    )
    if _register_task(TASK_NAME, xml):
        ok(f"タスク '{TASK_NAME}' を登録しました。")
        ok(f"次回ログイン時 ({delay}秒後) に自動起動します。")
    else:
        err("管理者権限で実行してください。")

def register_startup() -> None:
    header("STEP 4: スタートアップ登録")
    print("  登録方式を選択してください:")
    print("    1) Windows Terminal 自動起動設定 (推奨・管理者権限不要)")
    print("       settings.json に startOnUserLogin と startupActions を設定します。")
    print("    2) HKCU Run (管理者権限不要)")
    print("       pythonw.exe で launch.pyw をコンソールなしで起動します。")
    print("    3) タスクスケジューラ (管理者権限が必要・遅延設定可能)")
    print()
    method = ""
    while method not in ("1", "2", "3"):
        method = input("  選択 [1/2/3]: ").strip()
    {"1": _register_wt_settings, "2": _register_hkcu_run, "3": _register_task_scheduler}[method]()
    print()


# ── STEP 5: 動作テスト ──────────────────────────────────────
def test_run() -> None:
    header("STEP 5: 動作テスト")
    if not ask_yn("  今すぐターミナルを起動してテストしますか?"):
        print("  テストをスキップしました。"); print(); return
    step("launch.pyw を実行します...")
    try:
        subprocess.Popen([_find_pythonw(), str(LAUNCHER_PATH)])
        ok("起動要求を送信しました。ターミナルウィンドウが開くことを確認してください。")
    except Exception as e:
        err(f"テスト実行に失敗しました: {e}")
    print()


# ── --status / --unregister ──────────────────────────────────
def show_status() -> None:
    header("登録状況")

    # Task Scheduler
    for task in [TASK_NAME, WARMUP_TASK]:
        r = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", task, "/FO", "LIST"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if r.returncode == 0:
            ok(f"タスク '{task}' は登録されています。")
        else:
            print(f"  タスク '{task}' は未登録です。")

    # Registry
    for hive, name in [
        (winreg.HKEY_CURRENT_USER,  "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    ]:
        for val in [REG_VAL_LAUNCHER, REG_VAL_AUTOSTART]:
            try:
                with winreg.OpenKey(hive, REG_RUN_PATH) as k:
                    data, _ = winreg.QueryValueEx(k, val)
                ok(f"{name} Run[{val}] = {data}")
            except FileNotFoundError:
                pass
            except OSError:
                pass

def unregister() -> None:
    header("スタートアップ登録解除")

    # Task Scheduler
    for task in [TASK_NAME, WARMUP_TASK]:
        if _delete_task(task):
            ok(f"タスク '{task}' を削除しました。")
        else:
            print(f"  タスク '{task}' は未登録またはアクセス不可でした。")

    # Registry
    for hive, name in [
        (winreg.HKEY_CURRENT_USER,  "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    ]:
        for val in [REG_VAL_LAUNCHER, REG_VAL_AUTOSTART]:
            try:
                with winreg.OpenKey(hive, REG_RUN_PATH, 0, winreg.KEY_WRITE) as k:
                    winreg.DeleteValue(k, val)
                ok(f"{name} Run[{val}] を削除しました。")
            except FileNotFoundError:
                pass
            except OSError:
                pass

    ok("登録解除完了。")


# ── メイン ───────────────────────────────────────────────────
def main() -> None:
    if sys.platform != "win32":
        print("このスクリプトは Windows 上で実行してください。")
        sys.exit(1)

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   WSL Terminal Launcher セットアップウィザード   ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print("  STEP 1. 前提条件チェック")
    print("  STEP 2. ターミナル設定 (config.json)")
    print("  STEP 3. WSL 自動起動設定 (wsl-autostart / タスクスケジューラ)")
    print("  STEP 4. スタートアップ登録 (WT settings.json / HKCU Run / タスクスケジューラ)")
    print("  STEP 5. 動作テスト")
    print()

    if not check_prerequisites():
        err("前提条件を満たしていません。上記の問題を解決してから再実行してください。")
        input("\nEnterキーで終了")
        sys.exit(1)

    edit_config()
    setup_wsl_autostart()

    if ask_yn("スタートアップに登録しますか?"):
        register_startup()

    test_run()

    header("セットアップ完了")
    print()
    ok("セットアップが完了しました。")
    print()
    print("  手動起動  : launch.pyw をダブルクリック (コンソールなし)")
    print("  状況確認  : python setup.py --status")
    print("  登録解除  : python setup.py --unregister")
    print()
    input("Enterキーで終了")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WSL Terminal Launcher セットアップ")
    parser.add_argument("--status",     action="store_true", help="登録状況を確認")
    parser.add_argument("--unregister", action="store_true", help="スタートアップ登録を解除")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.unregister:
        unregister()
    else:
        main()
