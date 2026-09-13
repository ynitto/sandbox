"""agentcore.commandrun — 宣言された argv を 1 回実行する（LLM を起こさない）。

## なぜこれがあるか

定期実行のうち LLM が要らない仕事（資源制御・使用量較正・記憶の索引再構築・巡回）は、
これまでイベントフックの `check()` の中で `subprocess.run` を回し、最後に `None` を
返して「今回は送らない」と申告していた。フック契約は「**送るかどうかと本文を決める**」
ためのもので「送らずに実行する」ためではないので、次の食い違いが出ていた——`check()` は
デーモンのプロセス内で 30 秒を超えると隔離される（フック側の既定は 300 秒）、`None` は
無風なので仕事をしても idle と数えられる、実行の記録がどこにも残らない、同時実行の枠の
外で走る、手で回す口が無い。

ここはその受け皿で、成否を**終了コード**に置く。`statemachine-use` の `check:` と同じ
考え方（モデルの自己申告ではなく機械の観測で決める）を、アクションの無い定期実行へ
そのまま当てる。

## 呼ぶ側

常駐デーモン（agent-loop の scheduler）、`agent-loop command --entry`、agent-app の
「今すぐ実行」（`repository_ui`）。宣言の読み方は `agentcore.loopentry.command_spec`、
`{…}` の補完は `agentcore.loopentry.render_argv` で、どちらも 1 実装を共有する。

設計: docs/plans/2026-09-13-agent-loop-command-entry-design.md
仕様: docs/specs/agent-loop-spec.md §2.3.2
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from agentcore import stopreason
from agentcore.harness.toolloop import _tl_append_log, _tl_decode, _tl_progress

# 実行ログ・結果へ載せる出力の上限。全文は jsonl 側に残す（ペインへ流すとバッチの
# 出力で埋まり、進行表示が読めなくなる）。
OUTPUT_LIMIT = 4000


class CommandRunError(Exception):
    """実行そのものを始められない（宣言が壊れている・作業ディレクトリが無い）。"""


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _missing_paths(paths, work_dir: str) -> "list[str]":
    """`skip_if_missing` に挙がったもののうち、実際に無いものを返す。

    相対パスは作業ディレクトリから読む（宣言の `cwd` と同じ基準にする）。
    """
    missing: "list[str]" = []
    for raw in paths or []:
        path = os.path.expanduser(str(raw))
        if not os.path.isabs(path):
            path = os.path.join(work_dir, path)
        if not os.path.exists(path):
            missing.append(path)
    return missing


def _terminate(proc) -> None:
    """タイムアウトした子を、その子が起こした孫ごと止める。

    `start_new_session=True` で別のプロセスグループにしてあるので、グループへ送る。
    スクリプトが起こした孫（pytest・node・git）を残すと、次の実行と重なる。
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_command(spec: dict, *, cwd: str, log_file: str = "", env: "dict | None" = None,
                tag: str = "command") -> dict:
    """`command_spec()` が返した宣言を 1 回実行する。

    戻り値: `{ok, status, stopReason, stdout, stderr, argv, durationSec, logFile}`
    （`statemachine` / `run` と同じ「結果は 1 つの dict」の作法。`stdout` は上限まで）。

    `skip_if_missing` に挙げたパスが 1 つでも無ければ、**コマンドを起こさずに**
    `command_skipped` を返す（未導入のノードで「回っているつもりで何もしていない」を
    後から見つけられるよう、jsonl には 1 行残す）。

    成否は終了コードで決まる。`allow_status`（既定 `[0]`）に挙がっている番号なら成功で、
    「この段は 1 で正常」を持つコマンドはそこへ書く。タイムアウトは終了コードを持たない
    ので、`allow_status` に何を書いても失敗のまま。

    `shell` を持つ宣言は 1 つのシェルへ渡すスクリプトで、argv はその起動形。行をまたぐ
    状態が効く代わりに、上限も終了コードもスクリプト全体のものになる。

    `commands` を持つ宣言は**コマンドの列**で、段を上から順に実行し、最初の失敗で止める
    （段はそれぞれ 1 つの宣言と同じ形を持つので、この関数を段ごとに呼び直すだけでよい）。

    例外を投げるのは実行を**始められなかった**ときだけ。始まった実行の失敗
    （許していない終了コード・タイムアウト）は `ok: False` で返す——呼ぶ側はどちらも同じ
    「失敗として記録する」に落とすが、記録に残す理由が違う。
    """
    if spec.get("commands"):
        started = time.monotonic()
        stdout, stderr = "", ""
        for index, step in enumerate(spec["commands"], 1):
            _tl_progress(f"コマンド {index}/{len(spec['commands'])}", tag)
            try:
                result = run_command(step, cwd=cwd, log_file=log_file, env=env, tag=tag)
            except CommandRunError as exc:
                result = {"ok": False, "status": None, "stopReason": stopreason.COMMAND_ERROR,
                          "stdout": "", "stderr": str(exc), "error": str(exc),
                          "argv": step.get("argv") or [], "logFile": log_file}
            stdout = (stdout + result.get("stdout", ""))[-OUTPUT_LIMIT:]
            stderr = (stderr + result.get("stderr", ""))[-OUTPUT_LIMIT:]
            if not result["ok"]:
                result["error"] = f"{index} 行目のコマンドが失敗しました: {result.get('error') or result.get('stopReason')}"
                break
        return {**result, "stdout": stdout, "stderr": stderr,
                "durationSec": round(time.monotonic() - started, 3),
                "completedCommands": index if result["ok"] else index - 1}
    argv = [str(token) for token in (spec or {}).get("argv") or []]
    if not argv:
        raise CommandRunError("command が空です")
    work_dir = os.path.realpath(os.path.expanduser(str(cwd or os.getcwd())))
    if not os.path.isdir(work_dir):
        raise CommandRunError(f"作業ディレクトリがありません: {work_dir}")
    try:
        timeout = max(1, int(spec.get("timeout_sec") or 0))
    except (TypeError, ValueError):
        raise CommandRunError("command.timeout_sec が不正です") from None

    allow_status = spec.get("allow_status") or [0]

    missing = _missing_paths(spec.get("skip_if_missing"), work_dir)
    if missing:
        detail = "、".join(missing)
        if log_file:
            _tl_append_log(log_file, {"event": "command_skipped", "argv": argv,
                                      "cwd": work_dir, "missing": missing})
        _tl_progress(f"未導入のため飛ばしました: {detail}", tag)
        return {"ok": True, "status": None, "stopReason": stopreason.COMMAND_SKIPPED,
                "stdout": "", "stderr": "", "error": "", "argv": argv,
                "durationSec": 0.0, "logFile": log_file}

    child_env = dict(os.environ)
    child_env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
    child_env.update({str(k): str(v) for k, v in (env or {}).items()})

    if log_file:
        _tl_append_log(log_file, {"event": "command_start", "argv": argv,
                                  "cwd": work_dir, "timeout_sec": timeout})
    script = str(spec.get("shell") or "")
    if script:
        lines = [line for line in script.splitlines() if line.strip()]
        _tl_progress(f"シェルで実行します: {lines[0]}"
                     + (f" …（全 {len(lines)} 行）" if len(lines) > 1 else ""), tag)
    else:
        _tl_progress(f"コマンドを実行します: {' '.join(argv)}", tag)

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, cwd=work_dir, env=child_env, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        # 実行ファイルが無いのは**宣言の誤り**だが、reload をまたいで直せるので
        # 例外にせず失敗として返す（デーモンを落とさない）。
        detail = f"実行ファイルが見つかりません: {argv[0]}"
        if log_file:
            _tl_append_log(log_file, {"event": "command_error", "argv": argv,
                                      "error": detail})
        _tl_progress(f"ERROR: {detail}", tag)
        return {"ok": False, "status": None, "stopReason": stopreason.COMMAND_ERROR,
                "stdout": "", "stderr": detail, "error": detail, "argv": argv,
                "durationSec": 0.0, "logFile": log_file}
    except OSError as exc:
        raise CommandRunError(f"コマンドを起こせません: {exc}") from exc

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(proc)
        out, err = b"", b""
        try:
            out, err = proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
    duration = round(time.monotonic() - started, 3)
    stdout = _tl_decode(out or b"")
    stderr = _tl_decode(err or b"")
    status = None if timed_out else proc.returncode
    ok = status in allow_status

    if timed_out:
        stop = stopreason.COMMAND_TIMEOUT
        error = f"コマンドがタイムアウトしました ({timeout}s)"
    elif ok:
        stop = stopreason.COMMAND_EXIT
        error = ""
    else:
        stop = stopreason.COMMAND_EXIT
        # スクリプトの stdout は成果の出力なので、失敗の理由には使わない（`-e` で落ちた
        # ときの最後の 1 行は、たいてい直前の成功した行が出したものになる）。
        error = (_first_line(stderr) or (_first_line(stdout) if not script else "")
                 or f"status={status}")

    if log_file:
        _tl_append_log(log_file, {
            "event": "command_result", "argv": argv, "ok": ok, "status": status,
            "timedOut": timed_out, "durationSec": duration,
            "stdout": stdout[-OUTPUT_LIMIT:], "stderr": stderr[-OUTPUT_LIMIT:]})
    _tl_progress(
        (f"完了しました（{duration:.1f} 秒）" if ok
         else f"失敗しました: {error}（{duration:.1f} 秒）"), tag)

    return {"ok": ok, "status": status, "stopReason": stop,
            "stdout": stdout[-OUTPUT_LIMIT:], "stderr": stderr[-OUTPUT_LIMIT:],
            "error": error, "argv": argv, "durationSec": duration,
            "logFile": log_file}
