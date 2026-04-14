#!/usr/bin/env python3
"""
kiro-send.py — プロンプトファイルを受け取り、シングルトン tmux セッションで
kiro-cli を起動してファイルの内容を指示として送信する。

依存:
  - tmux      (apt install tmux)
  - kiro-cli  (PATH に存在すること)

動作環境: WSL (Ubuntu) / Linux

使い方:
  python3 kiro-send.py <prompt_file>
  python3 kiro-send.py <prompt_file> --dir ~/projects/app
  python3 kiro-send.py C:\\Users\\user\\task.md --dir ~/projects/app --session my-kiro

  python3 kiro-send.py clean                        # 60分以上アイドルなセッションを削除
  python3 kiro-send.py clean --timeout 30           # 30分以上アイドルなセッションを削除
  python3 kiro-send.py clean --dry-run              # 削除対象を確認するだけ（削除しない）
  python3 kiro-send.py clean --prefix kiro-send     # 対象セッション名プレフィックスを指定
"""

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DEFAULT_SESSION = "kiro-send"
DEFAULT_IDLE_TIMEOUT = 60   # clean コマンドのデフォルトアイドル閾値（分）
STARTUP_TIMEOUT = 60        # kiro-cli 起動待ちタイムアウト（秒）
RESPONSE_TIMEOUT = 300      # 応答待ちタイムアウト（秒）

# kiro-cli が入力待ちのとき末尾に現れるプロンプト行パターン
_PROMPT_RE = re.compile(r"^\s*[>?❯›]\s*$", re.MULTILINE)

# tmux セッション環境変数名（最終送信時刻を記録）
_ENV_LAST_ACTIVE = "KIRO_LAST_ACTIVE"

# ---------------------------------------------------------------------------
# パス変換
# ---------------------------------------------------------------------------

def win_to_wsl_path(path_str: str) -> str:
    """Windows形式のパス（C:\\... または C:/...）をWSL形式（/mnt/c/...）に変換する。
    既にWSL/Linux形式のパスはそのまま返す。
    """
    m = re.match(r'^([A-Za-z]):[\\\/](.*)', path_str)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return path_str


# ---------------------------------------------------------------------------
# tmux ヘルパー
# ---------------------------------------------------------------------------

def _tmux(*args: str) -> subprocess.CompletedProcess:
    """tmux コマンドを実行して CompletedProcess を返す。"""
    return subprocess.run(["tmux"] + list(args), capture_output=True, text=True)


def _session_exists(session: str) -> bool:
    return _tmux("has-session", "-t", session).returncode == 0


def _capture_pane(session: str) -> str:
    r = _tmux("capture-pane", "-p", "-t", session)
    return r.stdout if r.returncode == 0 else ""


def _has_prompt(content: str) -> bool:
    """ペイン末尾にプロンプト行が含まれるか確認する。"""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    return bool(_PROMPT_RE.search("\n".join(lines[-3:])))


def _get_pane_cwd(session: str) -> str:
    """tmux ペインのカレントディレクトリを返す。"""
    r = _tmux("display-message", "-p", "-t", session, "#{pane_current_path}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _wait_for_prompt(session: str, timeout: int, label: str) -> bool:
    """プロンプトが現れるまでポーリングする。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _session_exists(session):
            print(f"[kiro-send] ERROR: セッション '{session}' が消えました", file=sys.stderr)
            return False
        if _has_prompt(_capture_pane(session)):
            return True
        time.sleep(0.5)
    print(
        f"[kiro-send] WARN: {label} がタイムアウトしました ({timeout}秒)",
        file=sys.stderr,
    )
    return False


def _list_sessions(prefix: str) -> list[str]:
    """指定プレフィックスで始まる tmux セッション名のリストを返す。"""
    r = _tmux("list-sessions", "-F", "#{session_name}")
    if r.returncode != 0:
        return []
    return [name for name in r.stdout.splitlines() if name.startswith(prefix)]


# ---------------------------------------------------------------------------
# アイドル時刻管理（tmux セッション環境変数）
# ---------------------------------------------------------------------------

def _set_last_active(session: str) -> None:
    """現在時刻を tmux セッション環境変数 KIRO_LAST_ACTIVE に記録する。"""
    _tmux("set-environment", "-t", session, _ENV_LAST_ACTIVE, str(int(time.time())))


def _get_last_active(session: str) -> int:
    """セッションの最終活動時刻（Unix epoch）を返す。

    取得優先順:
      1. KIRO_LAST_ACTIVE 環境変数（kiro-send が書き込んだ正確な送信時刻）
      2. tmux の #{session_activity}（フォールバック: tmux が記録した最終入出力時刻）
      3. 現在時刻（取得できない場合 — 削除しない側に倒す）
    """
    # 1. KIRO_LAST_ACTIVE 環境変数
    r = _tmux("show-environment", "-t", session, _ENV_LAST_ACTIVE)
    if r.returncode == 0:
        parts = r.stdout.strip().split("=", 1)
        if len(parts) == 2:
            try:
                return int(parts[1])
            except ValueError:
                pass

    # 2. tmux の session_activity（フォールバック）
    r = _tmux("display-message", "-p", "-t", session, "#{session_activity}")
    if r.returncode == 0:
        try:
            return int(r.stdout.strip())
        except ValueError:
            pass

    # 3. 取得失敗 → 現在時刻（削除しない側に倒す）
    return int(time.time())


def _fmt_elapsed(seconds: int) -> str:
    """経過秒数を人間が読みやすい形式に変換する。"""
    if seconds < 60:
        return f"{seconds}秒"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}分{s}秒"
    h, m = divmod(m, 60)
    return f"{h}時間{m}分"


# ---------------------------------------------------------------------------
# セッション管理
# ---------------------------------------------------------------------------

def ensure_session(session: str, work_dir: Path | None, kiro_bin: str) -> bool:
    """シングルトンセッションを確保し、必要なら kiro-cli を起動/再起動する。

    work_dir が None の場合はディレクトリを変更しない（既存セッションはそのまま再利用、
    新規セッションはホームディレクトリで起動）。

    状態ごとの挙動:
      - セッション未存在                          → 新規作成して kiro-cli を起動
      - セッション存在 + プロンプト中 + cwd 一致  → そのまま再利用
      - セッション存在 + プロンプト中 + cwd 不一致 → 新しい cwd で再起動
      - セッション存在 + kiro-cli 無応答           → 同 cwd または新 cwd で再起動
    """
    kiro_cmd = shlex.join([kiro_bin, "chat", "--trust-all-tools"])
    cwd_str = str(work_dir) if work_dir else None

    if not _session_exists(session):
        # ── 新規セッション作成 ──────────────────────────────────────────────
        effective_cwd = cwd_str or str(Path.home())
        print(
            f"[kiro-send] tmux セッション '{session}' を作成します (cwd={effective_cwd})",
            file=sys.stderr,
        )
        r = _tmux("new-session", "-d", "-s", session, "-c", effective_cwd, kiro_cmd)
        if r.returncode != 0:
            print(
                f"[kiro-send] ERROR: セッション作成に失敗しました: {r.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        ok = _wait_startup(session)
        if ok:
            _set_last_active(session)
        return ok

    # ── 既存セッション ──────────────────────────────────────────────────────
    pane_cwd = _get_pane_cwd(session)
    kiro_alive = _has_prompt(_capture_pane(session))

    if kiro_alive and (cwd_str is None or pane_cwd == cwd_str):
        print(
            f"[kiro-send] 既存セッション '{session}' を再利用します (cwd={pane_cwd})",
            file=sys.stderr,
        )
        return True

    # 再起動が必要
    if kiro_alive:
        reason = f"cwd 変更 ({pane_cwd} → {cwd_str})"
    else:
        reason = "kiro-cli が終了していました"
    print(f"[kiro-send] kiro-cli を再起動します ({reason})", file=sys.stderr)

    effective_cwd = cwd_str or pane_cwd or str(Path.home())
    r = _tmux("respawn-pane", "-k", "-t", session, "-c", effective_cwd, kiro_cmd)
    if r.returncode != 0:
        print(
            f"[kiro-send] ERROR: respawn-pane に失敗しました: {r.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    ok = _wait_startup(session)
    if ok:
        _set_last_active(session)
    return ok


def _wait_startup(session: str) -> bool:
    print(f"[kiro-send] kiro-cli 起動待ち...", file=sys.stderr)
    if not _wait_for_prompt(session, STARTUP_TIMEOUT, "起動"):
        return False
    print(f"[kiro-send] kiro-cli 起動完了", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# プロンプト送信
# ---------------------------------------------------------------------------

def send_prompt(session: str, text: str) -> bool:
    """テキストを kiro-cli に送信して応答を待つ。

    kiro-cli の対話入力は 1 行単位なので、複数行テキストは空白で結合して正規化する。
    応答完了後に KIRO_LAST_ACTIVE を更新する。
    """
    single_line = " ".join(text.splitlines()).strip()
    short = single_line[:80] + ("..." if len(single_line) > 80 else "")
    print(f"[kiro-send] 送信: {short}", file=sys.stderr)

    r = _tmux("send-keys", "-t", session, single_line, "Enter")
    if r.returncode != 0:
        print(
            f"[kiro-send] ERROR: send-keys に失敗しました: {r.stderr.strip()}",
            file=sys.stderr,
        )
        return False

    # 送信直後に前のプロンプトを誤検出しないよう少し待つ
    time.sleep(2.0)
    ok = _wait_for_prompt(session, RESPONSE_TIMEOUT, "応答")
    if ok:
        # 応答完了 = kiro-cli が再びアイドル状態になったので時刻を更新
        _set_last_active(session)
    return ok


# ---------------------------------------------------------------------------
# clean サブコマンド
# ---------------------------------------------------------------------------

def cmd_clean(prefix: str, timeout_minutes: int, dry_run: bool) -> None:
    """アイドル状態の kiro-send セッションを安全に削除する。

    削除条件（両方満たす場合のみ）:
      1. kiro-cli がプロンプト待ち中（処理中でない）
      2. 最終活動時刻から timeout_minutes 分以上経過している
    """
    sessions = _list_sessions(prefix)
    if not sessions:
        print(f"[kiro-send clean] 対象セッションが見つかりません (prefix={prefix!r})")
        return

    now = int(time.time())
    threshold_sec = timeout_minutes * 60
    killed = 0

    print(f"[kiro-send clean] セッション数: {len(sessions)}, アイドル閾値: {timeout_minutes}分"
          + (" [dry-run]" if dry_run else ""))

    for session in sessions:
        at_prompt = _has_prompt(_capture_pane(session))
        last_active = _get_last_active(session)
        elapsed = now - last_active

        status = "prompt" if at_prompt else "busy "
        elapsed_str = _fmt_elapsed(elapsed)

        if at_prompt and elapsed >= threshold_sec:
            # ── 削除対象 ────────────────────────────────────────────────────
            if dry_run:
                print(f"  [dry-run] 削除予定  [{status}] {session}  (最終活動: {elapsed_str}前)")
            else:
                r = _tmux("kill-session", "-t", session)
                if r.returncode == 0:
                    print(f"  削除しました      [{status}] {session}  (最終活動: {elapsed_str}前)")
                    killed += 1
                else:
                    print(
                        f"  ERROR: 削除失敗   [{status}] {session}  {r.stderr.strip()}",
                        file=sys.stderr,
                    )
        else:
            # ── スキップ ────────────────────────────────────────────────────
            if not at_prompt:
                reason = "kiro-cli 処理中"
            else:
                remaining = threshold_sec - elapsed
                reason = f"あと {_fmt_elapsed(remaining)} でタイムアウト"
            print(f"  スキップ          [{status}] {session}  (最終活動: {elapsed_str}前, {reason})")

    if not dry_run:
        print(f"[kiro-send clean] 完了: {killed}/{len(sessions)} セッションを削除しました")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    # 依存チェック
    if shutil.which("tmux") is None:
        print("[kiro-send] ERROR: tmux が見つかりません (sudo apt install tmux)", file=sys.stderr)
        sys.exit(1)

    # サブコマンド判定（第1引数が "clean" なら clean モード）
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        _main_clean(sys.argv[2:])
    else:
        _main_send()


def _main_send() -> None:
    """send モード（デフォルト）のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="プロンプトファイルの内容をシングルトン tmux セッションの kiro-cli に送信する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  python3 kiro-send.py <prompt_file>
  python3 kiro-send.py <prompt_file> --dir ~/projects/app
  python3 kiro-send.py C:\\Users\\user\\task.md --dir ~/projects/app --session my-kiro

  prompt_file は Windows 形式（C:\\...）でも WSL 形式（/mnt/...）でも指定可能。
  --dir を省略した場合はカレントディレクトリを変更しない。

アイドルセッションの削除:
  python3 kiro-send.py clean --help
""",
    )
    parser.add_argument(
        "prompt_file",
        metavar="PROMPT_FILE",
        help="送信するプロンプトファイルのパス（Windows形式 C:\\... も指定可能）",
    )
    parser.add_argument(
        "--dir", "-d",
        metavar="DIR",
        default=None,
        help="作業ディレクトリ（省略時はディレクトリを変更しない）",
    )
    parser.add_argument(
        "--session", "-s",
        default=DEFAULT_SESSION,
        metavar="NAME",
        help=f"tmux セッション名 (デフォルト: {DEFAULT_SESSION})",
    )
    args = parser.parse_args()

    # Windows パスを WSL パスに変換してからファイルを開く
    prompt_path_str = win_to_wsl_path(args.prompt_file)
    prompt_file = Path(prompt_path_str).expanduser().resolve()
    if not prompt_file.is_file():
        print(f"[kiro-send] ERROR: ファイルが存在しません: {prompt_file}", file=sys.stderr)
        sys.exit(1)

    # --dir が指定された場合のみ作業ディレクトリを解決する
    work_dir: Path | None = None
    if args.dir:
        work_dir = Path(args.dir).expanduser().resolve()
        if not work_dir.is_dir():
            print(f"[kiro-send] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
            sys.exit(1)

    kiro_bin = shutil.which("kiro-cli")
    if kiro_bin is None:
        print(
            "[kiro-send] ERROR: kiro-cli が PATH に見つかりません。インストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)

    prompt_content = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt_content:
        print(f"[kiro-send] ERROR: プロンプトファイルが空です: {prompt_file}", file=sys.stderr)
        sys.exit(1)

    # シングルトンセッション確保
    if not ensure_session(args.session, work_dir, kiro_bin):
        sys.exit(1)

    # プロンプトファイルの内容を送信
    print(f"[kiro-send] プロンプトを送信します ({prompt_file.name})", file=sys.stderr)
    if send_prompt(args.session, prompt_content):
        print(f"[kiro-send] 完了しました", file=sys.stderr)
    else:
        print(f"[kiro-send] WARN: 応答待ちがタイムアウトしました", file=sys.stderr)
        sys.exit(2)


def _main_clean(argv: list[str]) -> None:
    """clean サブコマンドのエントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="kiro-send.py clean",
        description="アイドル状態の kiro-send tmux セッションを安全に削除する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
削除条件（両方満たす場合のみ）:
  1. kiro-cli がプロンプト待ち中（処理中でない）
  2. 最終活動時刻から --timeout 分以上経過している

最終活動時刻の取得:
  kiro-send が送受信のたびに tmux セッション環境変数 KIRO_LAST_ACTIVE に記録する。
  未記録の場合は tmux の session_activity にフォールバックする。

使い方:
  python3 kiro-send.py clean
  python3 kiro-send.py clean --timeout 30
  python3 kiro-send.py clean --dry-run
  python3 kiro-send.py clean --prefix kiro-send --timeout 120
""",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=DEFAULT_IDLE_TIMEOUT,
        metavar="MINUTES",
        help=f"アイドル閾値（分, デフォルト: {DEFAULT_IDLE_TIMEOUT}）",
    )
    parser.add_argument(
        "--prefix", "-p",
        default=DEFAULT_SESSION,
        metavar="PREFIX",
        help=f"対象セッション名のプレフィックス (デフォルト: {DEFAULT_SESSION})",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="削除対象を表示するだけで実際には削除しない",
    )
    args = parser.parse_args(argv)

    if args.timeout < 1:
        parser.error("--timeout は 1 以上の整数を指定してください")

    cmd_clean(
        prefix=args.prefix,
        timeout_minutes=args.timeout,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
