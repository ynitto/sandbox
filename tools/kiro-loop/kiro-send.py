#!/usr/bin/env python3
"""
kiro-send.py — ディレクトリと md ファイルを受け取り、シングルトン tmux セッションで
kiro-cli を起動してファイルの内容を指示として送信する。

依存:
  - tmux      (apt install tmux)
  - kiro-cli  (PATH に存在すること)

動作環境: WSL (Ubuntu) / Linux

使い方:
  python3 kiro-send.py <directory> <md_file>
  python3 kiro-send.py --dir <directory> --md <md_file>
  python3 kiro-send.py --dir ~/projects/app --md ~/notes/task.md --session my-kiro
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
STARTUP_TIMEOUT = 60    # kiro-cli 起動待ちタイムアウト（秒）
RESPONSE_TIMEOUT = 300  # 応答待ちタイムアウト（秒）

# kiro-cli が入力待ちのとき末尾に現れるプロンプト行パターン
_PROMPT_RE = re.compile(r"^\s*[>?❯›]\s*$", re.MULTILINE)

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


# ---------------------------------------------------------------------------
# セッション管理
# ---------------------------------------------------------------------------

def ensure_session(session: str, work_dir: Path, kiro_bin: str) -> bool:
    """シングルトンセッションを確保し、必要なら kiro-cli を起動/再起動する。

    状態ごとの挙動:
      - セッション未存在                         → 新規作成して kiro-cli を起動
      - セッション存在 + プロンプト中 + cwd 一致  → そのまま再利用
      - セッション存在 + プロンプト中 + cwd 不一致 → 新しい cwd で再起動
      - セッション存在 + kiro-cli 無応答          → 同 cwd または新 cwd で再起動
    """
    kiro_cmd = shlex.join([kiro_bin, "chat", "--trust-all-tools"])
    cwd_str = str(work_dir)

    if not _session_exists(session):
        # ── 新規セッション作成 ──────────────────────────────────────────────
        print(
            f"[kiro-send] tmux セッション '{session}' を作成します (cwd={cwd_str})",
            file=sys.stderr,
        )
        r = _tmux("new-session", "-d", "-s", session, "-c", cwd_str, kiro_cmd)
        if r.returncode != 0:
            print(
                f"[kiro-send] ERROR: セッション作成に失敗しました: {r.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return _wait_startup(session)

    # ── 既存セッション ──────────────────────────────────────────────────────
    pane_cwd = _get_pane_cwd(session)
    kiro_alive = _has_prompt(_capture_pane(session))

    if kiro_alive and pane_cwd == cwd_str:
        print(
            f"[kiro-send] 既存セッション '{session}' を再利用します (cwd={cwd_str})",
            file=sys.stderr,
        )
        return True

    # 再起動が必要
    if kiro_alive:
        reason = f"cwd 変更 ({pane_cwd} → {cwd_str})"
    else:
        reason = "kiro-cli が終了していました"
    print(f"[kiro-send] kiro-cli を再起動します ({reason})", file=sys.stderr)

    r = _tmux("respawn-pane", "-k", "-t", session, "-c", cwd_str, kiro_cmd)
    if r.returncode != 0:
        print(
            f"[kiro-send] ERROR: respawn-pane に失敗しました: {r.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return _wait_startup(session)


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
    return _wait_for_prompt(session, RESPONSE_TIMEOUT, "応答")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    # 依存チェック
    if shutil.which("tmux") is None:
        print("[kiro-send] ERROR: tmux が見つかりません (sudo apt install tmux)", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="md ファイルの内容をシングルトン tmux セッションの kiro-cli に送信する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使い方:
  python3 kiro-send.py <directory> <md_file>
  python3 kiro-send.py --dir ~/projects/app --md ~/notes/task.md
  python3 kiro-send.py --dir ~/projects/app --md ~/notes/task.md --session my-kiro

セッション名を省略した場合は "kiro-send" を使用します。
同一セッションが既に存在してプロンプト待ち中かつ cwd が同じであれば再利用します。
""",
    )
    parser.add_argument("--dir", "-d", metavar="DIR", help="作業ディレクトリ")
    parser.add_argument("--md", "-m", metavar="FILE", help="送信する md ファイルのパス")
    parser.add_argument(
        "--session", "-s",
        default=DEFAULT_SESSION,
        metavar="NAME",
        help=f"tmux セッション名 (デフォルト: {DEFAULT_SESSION})",
    )
    parser.add_argument(
        "positional",
        nargs="*",
        metavar="ARG",
        help="位置引数: [directory] [md_file]（--dir / --md の代替）",
    )
    args = parser.parse_args()

    # オプション優先、なければ位置引数
    dir_arg = args.dir or (args.positional[0] if len(args.positional) > 0 else None)
    md_arg  = args.md  or (args.positional[1] if len(args.positional) > 1 else None)

    if not dir_arg:
        parser.error("作業ディレクトリを指定してください (--dir DIR または第1位置引数)")
    if not md_arg:
        parser.error("md ファイルを指定してください (--md FILE または第2位置引数)")

    work_dir = Path(dir_arg).expanduser().resolve()
    if not work_dir.is_dir():
        print(f"[kiro-send] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
        sys.exit(1)

    md_file = Path(md_arg).expanduser().resolve()
    if not md_file.is_file():
        print(f"[kiro-send] ERROR: ファイルが存在しません: {md_file}", file=sys.stderr)
        sys.exit(1)

    kiro_bin = shutil.which("kiro-cli")
    if kiro_bin is None:
        print(
            "[kiro-send] ERROR: kiro-cli が PATH に見つかりません。インストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)

    md_content = md_file.read_text(encoding="utf-8").strip()
    if not md_content:
        print(f"[kiro-send] ERROR: md ファイルが空です: {md_file}", file=sys.stderr)
        sys.exit(1)

    # ── シングルトンセッション確保 ──────────────────────────────────────────
    if not ensure_session(args.session, work_dir, kiro_bin):
        sys.exit(1)

    # ── md の内容を指示として送信 ────────────────────────────────────────────
    print(f"[kiro-send] プロンプトを送信します ({md_file.name})", file=sys.stderr)
    if send_prompt(args.session, md_content):
        print(f"[kiro-send] 完了しました", file=sys.stderr)
    else:
        print(f"[kiro-send] WARN: 応答待ちがタイムアウトしました", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
