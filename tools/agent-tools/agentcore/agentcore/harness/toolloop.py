"""agentcore.harness.toolloop — 限定ツール契約のハーネス（本文の唯一の置き場）。

ツールループを持たない headless CLI（`agents/<name>.json` の `headless_autonomy:
single-shot`）へ、read_files / write_files / run / final の 4 つだけを許す契約で
ツール実行を供給する。層の判定（`run_prompt`）もここが持つ。

**入口は 2 つ、実装は 1 つ。**

- `agent-herd harness run …` … tmux もデーモンも設定ファイルも無しに直接呼ぶ
- `agent-loop run …` … `agent_loop/toolloop.py` がこのモジュールへ**委譲**する

かつては agent_loop 側に本文の写しがあり、次に「本文をデータファイル
（`_toolloop_body.py`）にして両者が exec する」形を経た。どちらも agent-loop の
テストが共有名前空間を差し替えていたためで、そのテストをこちらへ移した今は
**ただの import** で足りる（経緯は :mod:`agentcore.harness`、設計は
docs/plans/2026-08-25-agent-herd-unified-entry-design.md §5）。

agent-loop 固有の継ぎ目（記帳・control 解決）は下の前置きが `_borrowed` 経由で引く。
host が :func:`agentcore.harness.set_hooks` で差し込むまで、記帳は起こらない。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from agentcore.harness import _borrowed

# 本文が stdlib 以外に借りる名前はこの 3 つだけ（agent-loop の断片だった頃から不変）。
# ここで同じ綴りへ束ねるので、本文は host の事情を知らないまま書ける。
agent_home_subdir = _borrowed.agent_home_subdir
_import_agentcli = _borrowed.import_agentcli


def _node_budget_record(*args, **kwargs):
    """記帳は差し替え可能なフック経由（既定は何もしない）。呼ぶたびに現在値を引く。"""
    return _borrowed.node_budget_record(*args, **kwargs)


_TL_MAX_TOOL_ROUNDS = 8
_TL_MAX_TOOL_TIMEOUT_SEC = 300
_TL_MAX_AUTO_READ_BYTES = 32768
_TL_HARNESS_TIMEOUT_SEC = 30
# CLI 定義（agents/<name>.json の timeout）が黙っているときの共通上限。ローカル推論の
# 実測に合わせて 600 秒——gemma4:e4b は 1 周 50〜90 秒、判定役の gemma4:12b はさらに遅く、
# サーバが他リクエストで塞がっていれば queue 待ちがそこへ積み上がる（ollama は塞がっている
# 間、応答ヘッダすら返さない）。180 秒では正常に進んでいる実行を切っていた。
# **この 1 個だけが fallback**。周ごと・変種ごとに別の既定を置くと、どの上限で切られたのかを
# 読む側が追えなくなる。個別に伸ばしたい CLI は定義の `timeout` で宣言する（C7）。
#
# **これは無進捗（idle）の上限であって壁時計ではない**（`_tl_run_watched`）。エージェント
# CLI の 1 呼び出しは、ローカル推論では数十分かかることが正常で、順番待ちならさらに伸びる
# ——壁時計で切ると正常な実行を殺す。切ってよいのは「進んでいない」ときだけ。
_TL_DEFAULT_AGENT_TIMEOUT_SEC = 600
# 子が「自分は生きている」と刻み続ける**灯台**の置き場を、この環境変数で子へ渡す。
# ヘッドレスのローカル推論は**終わるまで stdout に 1 バイトも出さない**ので、出力だけを
# 見ていると正常な長考も順番待ちも無進捗に見える。知っている子（agent-ollama）は進捗
# イベントを出すたびにここを叩き、知らない子は無視する（その場合は出力だけが進捗の証拠に
# なる＝従来どおり）。子の**記録**（会話の JSONL）ではないので、実行が終わったら捨てる
# ——記録の置き場は子が決める（`--status` / `--replay` はそちらを見る）。
_TL_PROGRESS_BEACON_ENV = "AGENT_PROGRESS_BEACON"
# 無進捗の見張りが様子を見る間隔。
_TL_WATCH_TICK_SEC = 2.0
# 病理を止めるためだけの天井（4 時間）。無進捗の上限だけだと、内部で回り続けて出力を
# 出し続ける子（リトライループに落ちた CLI）を誰も止められない。正常な 1 呼び出しが
# ここへ届くことは無い——届くならそれは待ち方の問題ではなく設計の問題として見る。
_TL_AGENT_WALL_CEILING_SEC = 4 * 60 * 60
# ponytail: 上限は固定値。経路ごとに変えたくなるまで設定にしない。
_TL_SHELLS = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe",
              "powershell", "powershell.exe", "pwsh"}
# 拡張子ごとの固定インタプリタ。.py は _tl_python_command()（3.10 以上を選ぶ）。
_TL_SCRIPT_INTERPRETERS = {".js": ("node",), ".sh": ("bash", "sh")}
# 制御応答の一時障害だけを再試行する。恒久的な失敗（契約違反・設定ミス）は即座に上げる。
_TL_CONTROL_RETRIES = 2
_TL_TRANSIENT_RE = re.compile(
    r"タイムアウト|timeout|一時|rate.?limit|too many requests|空の応答|"
    r"connection|接続|temporar|unavailable|overload|50[234]\b", re.I)
# statemachine-use のスクリプトが要求する Python（README の動作環境と同じ 3.10 以上）。
_TL_SKILL_PYTHON_MIN = (3, 10)
_TL_PYTHON_CANDIDATES = ("python3", "python", "python3.14", "python3.13",
                         "python3.12", "python3.11", "python3.10")
_TL_SKILL_PYTHON = ""


class ToolLoopError(RuntimeError):
    """ツールループの実行失敗（検証違反・契約不成立・環境不足）。

    `transient` は agents/<name>.json の errors[] 分類（class: transient）の持ち越し。
    定義が hint で本文を差し替えると、メッセージ文字列から一時障害と読めなくなる
    ——分類は定義が正典なので、文字列の再推測ではなくフラグで運ぶ。
    """

    def __init__(self, message: str = "", *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def _tl_inside(root: str, file: str) -> bool:
    rel = os.path.relpath(file, root)
    return rel == "." or not (rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel))


def _tl_project_path(cwd: str, value) -> str:
    """作業フォルダ内へ正規化した絶対パス。`..`・シンボリックリンクの逸脱は拒否。"""
    root = os.path.realpath(str(cwd))
    raw = str(value or "").strip()
    if not raw or "\0" in raw:
        raise ToolLoopError("空または不正なファイルパスです")
    requested = os.path.abspath(os.path.join(str(cwd), raw))
    parent = requested
    while not os.path.exists(parent):
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        parent = nxt
    real_parent = os.path.realpath(parent)
    target = os.path.abspath(os.path.join(real_parent, os.path.relpath(requested, parent)))
    if not _tl_inside(root, target):
        raise ToolLoopError(f"作業フォルダ外のパスは使えません: {raw}")
    return target


def _tl_source_root() -> str:
    """リポジトリ実行時のスキル探索ルート（.github/skills を持つ親）。zipapp では ''。"""
    try:
        here = Path(__file__).resolve()
    except (NameError, OSError):
        return ""
    d = here if here.is_dir() else here.parent
    for _ in range(10):
        if (d / ".github" / "skills").is_dir():
            return str(d)
        if d.parent == d:
            break
        d = d.parent
    return ""


def _tl_skill_search_dirs(cwd: str) -> "list[str]":
    """スキル探索ディレクトリ（優先順）。エラー表示にも同じ一覧を使う（2 実装にしない）。"""
    return [d for d in [
        os.path.join(cwd, ".github", "skills"),
        os.path.join(_tl_source_root(), ".github", "skills") if _tl_source_root() else "",
        os.path.join(os.path.expanduser("~"), ".agents", "skills"),
        os.path.join(os.path.expanduser("~"), ".codex", "skills"),
    ] if d]


def _tl_resolve_skill(name: str, cwd: str) -> "dict | None":
    for base in _tl_skill_search_dirs(cwd):
        root = os.path.join(base, name)
        if os.path.isfile(os.path.join(root, "SKILL.md")):
            return {"name": name, "root": root, "skill_file": os.path.join(root, "SKILL.md")}
    return None


def _tl_action_skill_names(text: str) -> "list[str]":
    # backtick は任意。「wiki-useスキルを使って」のような素の表記も定期プロンプトの
    # 実態として普通にあり、拾わないとモデルはスキル名をコマンドとして実行しようとする
    # （実測: run wiki-use → 「PATH 上に実行ファイルがありません」の却下ループ）。
    # 誤検出しても、ここ由来の名前は実在するスキルだけが解決される（無ければ素通し）。
    out: list[str] = []
    for m in re.finditer(r"`?([A-Za-z0-9_.-]+)`?\s*スキル", str(text or "")):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _tl_action_project_files(text: str, cwd: str) -> "list[str]":
    files: list[str] = []
    for m in re.finditer(r"`([^`\n]+)`", str(text or "")):
        raw = m.group(1).strip()
        if not raw or raw.startswith("-") or re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
            continue
        try:
            file = _tl_project_path(cwd, raw)
        except ToolLoopError:
            continue   # コマンド例や作業フォルダ外の参照は割り当てない
        if os.path.isfile(file) and file not in files:
            files.append(file)
    return files


def _tl_skill_scripts(skill: dict) -> "list[str]":
    directory = os.path.join(skill["root"], "scripts")
    try:
        return [os.path.join(directory, n) for n in sorted(os.listdir(directory))
                if re.search(r"\.(?:py|js|sh)$", n, re.I)]
    except OSError:
        return []


def _tl_skill_declared_scripts(skill: dict) -> "list[str]":
    """SKILL.md が名指ししているスクリプトだけ。

    scripts/ に置いてあること自体は実行してよい根拠にならない（内部の下請けや実験物が
    混ざる）。何を呼んでよいかを決めるのはスキル自身の SKILL.md——アクションが
    スキルへ移譲するとき、入口はそこに書かれたものに限る。
    """
    try:
        text = Path(skill["skill_file"]).read_text(encoding="utf-8")
    except (OSError, KeyError, TypeError):
        return []
    return [s for s in _tl_skill_scripts(skill) if os.path.basename(s) in text]


def _tl_executable_on_path(command: str) -> str:
    return shutil.which(str(command)) or ""


def _tl_validate_command(command, cwd: str, skill_dirs: "list[str]") -> str:
    raw = str(command or "").strip()
    if not raw or re.search(r"[\s\0]", raw):
        raise ToolLoopError("run.command は単一の実行ファイル名が必要です")
    if os.path.basename(raw).lower() in _TL_SHELLS:
        raise ToolLoopError(f"シェルの実行は許可されていません: {raw}")
    if not os.path.isabs(raw) and "/" not in raw and "\\" not in raw:
        if not _tl_executable_on_path(raw):
            # スキル名をコマンドとして実行しようとする失敗は実測で頻出。汎用の
            # 「PATH にない」だけ返すと同じ要求を繰り返すので、次の一手を教える。
            roots = {os.path.basename(d): d for d in skill_dirs}
            if raw in roots:
                scripts = _tl_skill_scripts({"root": roots[raw]})
                raise ToolLoopError(
                    f"{raw} はスキル名であり実行ファイルではありません。"
                    + (f"スキルのスクリプトを実行してください: {', '.join(scripts)}"
                       if scripts else "SKILL.md の手順に従ってください。"))
            raise ToolLoopError(f"PATH 上に実行ファイルがありません: {raw}")
        return raw
    for root in [cwd, *skill_dirs]:
        try:
            resolved = _tl_project_path(root, raw)
        except ToolLoopError:
            continue
        if os.path.exists(resolved):
            return resolved
    raise ToolLoopError(f"実行ファイルは作業フォルダまたはロード済みスキル内に限定されます: {raw}")


def _tl_validate_arg_paths(args: "list[str]", cwd: str, skill_dirs: "list[str]") -> None:
    for arg in args:
        if "\0" in arg:
            raise ToolLoopError("run.args に NUL は使えません")
        if re.match(r"^[a-z][a-z0-9+.-]*://", arg, re.I) or arg.startswith("-"):
            continue
        if os.path.isabs(arg) or ".." in re.split(r"[\\/]", arg):
            candidate = arg if os.path.isabs(arg) else os.path.abspath(os.path.join(cwd, arg))
            allowed = False
            for root in [cwd, *skill_dirs]:
                try:
                    _tl_project_path(root, candidate)
                    allowed = True
                    break
                except ToolLoopError:
                    continue
            if not allowed:
                raise ToolLoopError(f"作業フォルダ外の引数パスは使えません: {arg}")


def _tl_script_interpreter(command: str) -> str:
    """スクリプトを動かす固定インタプリタ。実行ファイルなら空文字。

    拡張子つきのスクリプトを直接 exec させない。実行ビットや shebang に従うと、
    どのシェル・どのランタイムが走るかを**スクリプト側**が決めることになり、
    「任意のシェルは使わせない」という契約がファイル 1 個で迂回される。
    """
    ext = os.path.splitext(str(command or ""))[1].lower()
    if ext == ".py":
        return _tl_python_command()
    if ext not in _TL_SCRIPT_INTERPRETERS:
        return ""
    names = _TL_SCRIPT_INTERPRETERS[ext]
    for name in names:
        found = _tl_executable_on_path(name)
        if found:
            return found
    raise ToolLoopError(
        f"{ext} を実行するインタプリタが PATH にありません: {' / '.join(names)}")


def _tl_validate_tool_request(raw, cwd: str, skills: "list[dict]") -> dict:
    if not isinstance(raw, dict):
        raise ToolLoopError("ツール要求が JSON オブジェクトではありません")
    kind = str(raw.get("type") or "")
    skill_dirs = [s["root"] for s in skills if s.get("root")]
    if kind in ("read_files", "write_files"):
        paths = raw.get("paths")
        if (not isinstance(paths, list) or not paths
                or any(not isinstance(p, str) for p in paths)):
            raise ToolLoopError(f"{kind}.paths は1件以上の文字列配列が必要です")
        return {"type": kind, "paths": [_tl_project_path(cwd, p) for p in paths]}
    if kind == "run":
        args = raw.get("args")
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            raise ToolLoopError("run.args は文字列配列が必要です")
        args = [str(a) for a in args]
        _tl_validate_arg_paths(args, cwd, skill_dirs)
        command = _tl_validate_command(raw.get("command"), cwd, skill_dirs)
        interpreter = _tl_script_interpreter(command)
        if interpreter:
            args = [command, *args]
            command = interpreter
        try:
            timeout_sec = int(float(raw.get("timeout_sec") or 0)) or 60
        except (TypeError, ValueError):
            timeout_sec = 60
        return {"type": kind, "command": command, "args": args,
                "timeout_sec": max(1, min(timeout_sec, _TL_MAX_TOOL_TIMEOUT_SEC))}
    if kind == "final":
        return {"type": kind, "output": str(raw.get("output") or "").strip()}
    raise ToolLoopError(f"許可されていないツール要求です: {kind or '(空)'}")


def _tl_parse_json_object(text) -> "dict | None":
    """本文中の JSON オブジェクトを括弧の釣り合いで走査し、最後の 1 個を返す。"""
    value = str(text or "")
    found: list[dict] = []
    start = -1
    depth = 0
    quoted = False
    escaped = False
    for i, char in enumerate(value):
        if start < 0:
            if char == "{":
                start = i
                depth = 1
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(value[start:i + 1])
                    if isinstance(parsed, dict):
                        found.append(parsed)
                except ValueError:
                    pass   # Aider の説明中にある JSON 風テキストは無視
                start = -1
    return found[-1] if found else None


def _tl_parse_tool_request(text) -> dict:
    request = _tl_parse_json_object(text)
    if not request or not request.get("type"):
        raise ToolLoopError(
            f"ツール要求を JSON として読めません: {str(text)[:160]}")
    return request


def _tl_append_log(log_file: str, event: dict) -> None:
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _dt.datetime.now(_dt.timezone.utc).isoformat(), **event},
                           ensure_ascii=False) + "\n")


# デーモンの headless 実行スレッドが設定する進行表示の振り向け先（テキストファイルのパス）。
# 単発コマンド（run / statemachine）は stdout が実行ペインそのものなので print が正しいが、
# デーモン内スレッドの stdout はコントロールペイン——そこへ流すと実行の様子が
# controller のログに混ざる。スレッドごとに向き先を切り替える。
_TL_PROGRESS_LOCAL = threading.local()


def _tl_progress_view_file(log_file: str) -> str:
    """進行表示のテキスト版のパス（jsonl と同名で拡張子だけ .log）。

    ログペインはこちらを tail する。jsonl を直接見せると argv 全文入りの生 JSON が
    流れて、dashboard 定常業務の「今すぐ実行」ペイン（`[run] …` の進行表示）と
    見え方が揃わない。中身は print と同じ `[tag] message` 行。
    """
    return os.path.splitext(str(log_file))[0] + ".log"


def _tl_progress(message: str, tag: str = "statemachine") -> None:
    """tmux ウィンドウ（人が見る画面）への進行表示。ログとは別に短く出す。"""
    view_file = getattr(_TL_PROGRESS_LOCAL, "view_file", None)
    if view_file:
        try:
            with open(view_file, "a", encoding="utf-8") as f:
                f.write(f"[{tag}] {message}\n")
        except OSError:
            pass   # 進行表示が書けなくても実行は落とさない
        return
    print(f"[{tag}] {message}", flush=True)


def _tl_decode(data: bytes) -> str:
    """子の出力をテキストにする。改行の扱いを `text=True`（universal newlines）に揃える
    ——`@agent-usage` の拾い出しなど、読む側はどれも行単位で見ている。"""
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _tl_beacon_stamp(path: str) -> "tuple | None":
    """灯台の (mtime, size)。刻まれたかどうかだけを見るので中身は読まない。"""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _tl_run_watched(argv: "list[str]", *, cwd: str, env: dict, stdin: "str | None",
                    idle_sec: float, beacon_path: str) -> dict:
    """子を回し、**無進捗**でだけ打ち切る（壁時計では切らない）。

    `subprocess.run(timeout=…)` との違いはそこだけで、戻り値の形は同じ。ローカル推論の
    1 呼び出しは数十分かかることが正常で、サーバが他リクエストで塞がっていれば順番待ちが
    そこへ積み上がる——壁時計で切ると、正常に進んでいる実行と、順番を待っているだけの
    実行を、ハングと同じ扱いで殺す。しかも殺した側は理由を残せない（子は SIGKILL される
    ので「queue で待っていた」という証跡はどこにも出ない）。

    進捗＝次のどちらか。
      (a) stdout / stderr に 1 バイトでも来た。
      (b) 子が `AGENT_PROGRESS_BEACON` の灯台を刻んだ（心拍を含む）。
    (b) が要るのは、ヘッドレスのローカル推論が**終わるまで何も出力しない**ため。
    (b) では心拍も進捗として数える——ここが見張るのは「子が生きているか」で、
    「推論が前に進んでいるか」は子自身が持つ（decode stall / queue の生存確認）。
    二重に判定すると、内側が待つと決めた実行を外側が理由も残さず殺すことになる。

    天井（`_TL_AGENT_WALL_CEILING_SEC`）だけは壁時計で見る。出力を出し続けたまま
    回り続ける病理（リトライループ）は、無進捗では捕まえられないため。
    """
    result = {"status": None, "stdout": "", "stderr": "", "error": ""}
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    chunks: "dict[str, list]" = {"stdout": [], "stderr": []}
    activity = {"at": time.monotonic()}
    lock = threading.Lock()

    def drain(stream, name: str) -> None:
        # os.read を使う（BufferedReader.read(n) は n バイト揃うまで返らないので、
        # 「1 バイト来た」を活動として拾えない）。
        try:
            fd = stream.fileno()
        except (OSError, ValueError):
            return
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks[name].append(chunk)
            with lock:
                activity["at"] = time.monotonic()
        try:
            stream.close()
        except (OSError, ValueError):
            pass

    def feed() -> None:
        try:
            proc.stdin.write(stdin.encode("utf-8"))
        except (OSError, ValueError, AttributeError):
            pass
        try:
            proc.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass

    threads = [threading.Thread(target=drain, args=(proc.stdout, "stdout"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "stderr"), daemon=True)]
    if stdin is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for t in threads:
        t.start()

    started = time.monotonic()
    stamp = _tl_beacon_stamp(beacon_path)
    tick = max(0.1, min(_TL_WATCH_TICK_SEC, idle_sec / 10.0))
    while True:
        try:
            result["status"] = proc.wait(timeout=tick)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        current = _tl_beacon_stamp(beacon_path)
        if current != stamp:
            stamp = current
            with lock:
                activity["at"] = now
        with lock:
            idle = now - activity["at"]
        if idle >= idle_sec:
            # 文言に「タイムアウト」を残す。制御応答の再試行判定（_TL_TRANSIENT_RE）が
            # ここを読むので、無進捗の打ち切りは従来の壁時計打ち切りと同じく一時障害として
            # 拾われる必要がある（出力も心拍も無いまま固まった＝再試行に意味がある形）。
            result["error"] = (f"{argv[0]} が {idle:.0f} 秒進まないため打ち切りました"
                               f"（無進捗タイムアウト・上限 {idle_sec:.0f} 秒）")
        elif (now - started) >= _TL_AGENT_WALL_CEILING_SEC:
            # こちらは意図的に一時障害と読ませない。天井に当たる子は「動いてはいるが
            # 終わらない」ので、同じ入力で再試行すればもう 4 時間焼くだけになる。
            result["error"] = (f"{argv[0]} が {(now - started) / 3600:.1f} 時間続いたため"
                               f"打ち切りました（天井 "
                               f"{_TL_AGENT_WALL_CEILING_SEC / 3600:.0f} 時間）")
        else:
            continue
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        break

    for t in threads:
        t.join(timeout=5)
    result["stdout"] = _tl_decode(b"".join(chunks["stdout"]))
    result["stderr"] = _tl_decode(b"".join(chunks["stderr"]))
    return result


def _tl_exec_argv(command: str, args: "list[str]", *, cwd: str, timeout_sec: float,
                  env: "dict | None" = None, stdin: "str | None" = None,
                  output_file: "str | None" = None, log_file: str,
                  idle: bool = False) -> dict:
    """子を 1 つ回す。

    `idle=False`（既定）は `timeout_sec` を**壁時計**として使う。モデルが要求した
    `run` のような「宣言した時間で終わるべきもの」はこちら——`sleep 9999` は宣言どおり
    切られるべきで、出力が無いことは正しく失敗である。

    `idle=True` は `timeout_sec` を**無進捗の上限**として使う（エージェント CLI の
    呼び出し）。理由は `_tl_run_watched` に書いた。
    """
    started = time.time()
    argv = [command, *args]
    _tl_append_log(log_file, {"event": "start", "argv": argv, "cwd": cwd,
                              "timeoutMs": int(timeout_sec * 1000),
                              **({"idleTimeout": True} if idle else {})})
    merged_env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "1000",
                  **(env or {})}
    result = {"status": None, "stdout": "", "stderr": "", "error": ""}
    beacon_path = ""
    if idle and log_file:
        # ハーネスのログの隣に置く。log_file が無い経路では作らない——置き場を発明する
        # より、出力だけを進捗として見る（＝従来どおり）方が正しい。
        beacon_path = f"{log_file}.beacon-{os.getpid()}-{time.monotonic_ns()}"
        merged_env[_TL_PROGRESS_BEACON_ENV] = beacon_path
    try:
        if idle:
            result = _tl_run_watched(argv, cwd=cwd, env=merged_env, stdin=stdin,
                                     idle_sec=max(1.0, float(timeout_sec)),
                                     beacon_path=beacon_path)
        else:
            proc = subprocess.run(
                argv, cwd=cwd, input=stdin, env=merged_env,
                capture_output=True, text=True, errors="replace",
                timeout=max(1.0, float(timeout_sec)))
            result["status"] = proc.returncode
            result["stdout"] = proc.stdout or ""
            result["stderr"] = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        result["stdout"] = (exc.stdout.decode("utf-8", "replace")
                            if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        result["stderr"] = (exc.stderr.decode("utf-8", "replace")
                            if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
        result["error"] = f"{command} がタイムアウトしました"
    except OSError as exc:
        result["error"] = str(exc)
    if beacon_path:
        try:
            os.unlink(beacon_path)
        except OSError:
            pass
    if output_file:
        try:
            with open(output_file, "r", encoding="utf-8", errors="replace") as f:
                result["stdout"] = f.read() or result["stdout"]
        except OSError:
            pass   # stdout fallback
        try:
            os.unlink(output_file)
        except OSError:
            pass
    _tl_append_log(log_file, {
        "event": "finish", "argv": argv, "cwd": cwd,
        "durationMs": int((time.time() - started) * 1000),
        "status": result["status"], "error": result["error"],
        "stdout": result["stdout"], "stderr": result["stderr"],
    })
    return result


def _tl_record_usage(agent: dict, result: dict, log_file: str) -> None:
    """CLI が stderr に出した実測 usage（`@agent-usage`）をログと台帳へ渡す。

    headless 経路は自分で subprocess を回すので、tmux 経路と違って**実測が取れる**
    （agent-aider / agent-ollama / agent-opencode が出す）。出さない CLI は素通り——
    推定で埋めない。失敗した実行も記帳する（rc が非 0 でもトークンは焼けている）。

    記帳するのはトークンだけで、秒は入れない。実行時間はセマフォのスロット保持で既に
    1 行入っており、ここで足すと同じ実行を二重に数える。
    # ponytail: rates（時間からのトークン推定）を設定している運用では、スロット行の
    # 推定とこの実測が二重に載る。実測が出る CLI はその rate を外すのが正しい直し方。
    """
    tokens_in, tokens_out = agent["agentcli"].parse_usage(result.get("stderr") or "")
    if tokens_in is None and tokens_out is None:
        return
    _tl_append_log(log_file, {"event": "usage", "cli": agent["cli"], "model": agent["model"],
                              "tokensIn": tokens_in, "tokensOut": tokens_out})
    _node_budget_record(0, agent_cli=str(agent["cli"] or ""), model=str(agent["model"] or ""),
                        tokens_in=tokens_in, tokens_out=tokens_out)


def _tl_failure_hint(agent: dict, detail: str) -> "tuple[str, bool]":
    """失敗分類 → (利用者向け文言, 一時障害か)。quota 観測もここで一度だけ行う。

    class は定義（errors[]）が正典。hint が本文を差し替えるとメッセージ文字列から
    transient と読めなくなるので、分類結果をフラグでも返して呼び出し側の再試行判定に使う。
    """
    classified = agent["agentcli"].classify_error(
        agent["spec"], detail, detailed=True, now=time.time())
    if not classified:
        return "", False
    quota_kind = classified.get("quota_kind")
    if quota_kind:
        extra = {"event": "quota", "quota_kind": quota_kind}
        if classified.get("reset_at"):
            extra["reset_at"] = classified["reset_at"]
        _node_budget_record(0, agent_cli=str(agent["cli"] or ""),
                            model=str(agent["model"] or ""), extra=extra)
    return str(classified.get("hint") or ""), str(classified.get("class") or "") == "transient"


def _tl_run_agent(agent: dict, prompt: str, *, cwd: str, readonly: bool,
                  read_files: "list[str]", files: "list[str]", log_file: str,
                  allow_empty: bool = False) -> str:
    """エージェント CLI（aider 等）を headless で 1 回呼び、応答本文を返す。

    allow_empty: 空の stdout を正常な空結果として返す。編集の周では「黙って直した」が
    普通にあり、そこを失敗にすると成果物ができているのに実行が落ちる。制御の周
    （次の一手の JSON を求める周）は空＝答えが無いので既定のまま失敗にする。
    """
    mod = agent["agentcli"]
    built = mod.headless_cmd(agent["spec"], agent["model"], prompt,
                             readonly=readonly, no_session=True,
                             read_files=read_files, files=files)
    argv = built["argv"]
    timeout_sec = float(built.get("timeout") or 0) or _TL_DEFAULT_AGENT_TIMEOUT_SEC
    result = _tl_exec_argv(argv[0], argv[1:], cwd=cwd, timeout_sec=timeout_sec,
                           env=built.get("env") or {}, stdin=built.get("stdin"),
                           output_file=built.get("output_file"), log_file=log_file,
                           idle=True)
    _tl_record_usage(agent, result, log_file)
    if result["status"] != 0 or result["error"]:
        detail = "\n".join(x for x in (result["error"], result["stderr"], result["stdout"]) if x)
        hint, transient = _tl_failure_hint(agent, detail)
        raise ToolLoopError(hint or detail or f"{argv[0]} が失敗しました",
                            transient=transient)
    output = str(result["stdout"] or "").strip()
    if not output and not allow_empty:
        raise ToolLoopError("エージェントが空の応答を返しました")
    return output


def _tl_run_control(agent: dict, prompt: str, *, cwd: str, read_files: "list[str]",
                    log_file: str) -> str:
    """制御応答（次の一手の JSON）を求める周。一時障害だけ限定回数で再試行する。

    ローカルモデルはタイムアウト・過負荷・空応答をときどき返す。そこで実行ごと落とすと
    人が張り付いて再投入することになる（柱2）。一方で契約違反や設定ミスまで再試行すると
    同じ失敗をクレジット分だけ繰り返すので、一時障害と読める失敗だけに絞る。
    """
    for attempt in range(_TL_CONTROL_RETRIES + 1):
        try:
            return _tl_run_agent(agent, prompt, cwd=cwd, readonly=True,
                                 read_files=read_files, files=[], log_file=log_file)
        except ToolLoopError as exc:
            # 定義の分類（class: transient）が第一。hint が本文を差し替えた後の文字列には
            # 「タイムアウト」等が残らないことがあるので、正規表現は補助に落とす。
            transient = getattr(exc, "transient", False) or bool(
                _TL_TRANSIENT_RE.search(str(exc)))
            if attempt >= _TL_CONTROL_RETRIES or not transient:
                raise
            _tl_progress(f"制御応答の一時障害を再試行 "
                         f"({attempt + 1}/{_TL_CONTROL_RETRIES}): {exc}")
            _tl_append_log(log_file, {"event": "control_retry", "attempt": attempt + 1,
                                      "error": str(exc)})
    raise ToolLoopError("制御応答を取得できません")   # 到達しない（ループが必ず返すか投げる）


_TL_FAILURE_RE = re.compile(r"^(?:[A-Z][A-Z0-9_]*_)?(?:FAILED|ERROR)\b", re.I)


def _tl_final_evidence_error(output, cwd: str, evidence: set, executed: set) -> str:
    text = str(output or "").strip()
    if _TL_FAILURE_RE.match(text):
        return ""
    m = re.search(r"^path:\s*(.+?)\s*$", text, re.I | re.M)
    if not m:
        return ""
    try:
        file = _tl_project_path(cwd, m.group(1))
    except ToolLoopError as exc:
        return str(exc)
    if not os.path.isfile(file):
        return f"成功出力のファイルがありません: {m.group(1)}"
    if file not in evidence:
        return f"このステートで確認・生成していないファイルです: {m.group(1)}"
    if file not in executed:
        return f"この実行で生成・検証していないファイルです: {m.group(1)}"
    return ""


def _tl_file_stamp(file: str) -> str:
    """更新時刻ではなく内容そのものの指紋を返す。

    エディタ CLI は変更を加えなくてもファイルへ書き戻し、mtime だけを更新することがある。
    それを成果物の更新として扱うと、受入条件を満たしていない実行が done になる。
    """
    try:
        digest = hashlib.sha256()
        with open(file, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _tl_python_ok(command: str) -> bool:
    try:
        proc = subprocess.run(
            [command, "-c", f"import sys; sys.exit(0 if sys.version_info >= {_TL_SKILL_PYTHON_MIN!r} else 1)"],
            capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _tl_python_command() -> str:
    """statemachine-use のスクリプトを動かせるインタプリタ。

    **sys.executable をそのまま使わない。** スキルのスクリプト（engine.py）は match 文を
    使うので Python 3.10 以上が要る。一方 agent-loop 自身は 3.9 でも動き、zipapp の
    shebang は `/usr/bin/env python3` ——macOS ではこれが標準搭載の 3.9 に当たるため、
    自分の実行系を渡すと engine.py の import が `SyntaxError: invalid syntax` で落ちる
    （実機で踏んだ）。ここでは「スキルが動く方」を選ぶ。
    """
    global _TL_SKILL_PYTHON
    if os.environ.get("PYTHON"):
        return os.environ["PYTHON"]
    if sys.version_info >= _TL_SKILL_PYTHON_MIN and sys.executable:
        return sys.executable
    if not _TL_SKILL_PYTHON:
        for name in _TL_PYTHON_CANDIDATES:
            found = _tl_executable_on_path(name)
            if found and _tl_python_ok(found):
                _TL_SKILL_PYTHON = found
                break
        else:
            raise ToolLoopError(
                "statemachine-use のスクリプトには Python "
                f"{_TL_SKILL_PYTHON_MIN[0]}.{_TL_SKILL_PYTHON_MIN[1]} 以上が必要です"
                f"（agent-loop の実行系は {sys.version.split()[0]}）。"
                "新しい python を PATH へ通すか、PYTHON 環境変数で明示してください。")
    return _TL_SKILL_PYTHON


def _tl_resolve_agent(cli_name: str, model: str, cwd: str) -> dict:
    """agents/<name>.json 契約から headless 実行エージェントを解決する。"""
    mod = _import_agentcli()
    if mod is None:
        raise ToolLoopError(
            "agentcore（agents/<name>.json 定義ローダ）を解決できません。"
            "install.sh の再実行を検討してください。")
    name = str(cli_name or "aider").strip() or "aider"
    try:
        spec = mod.load_cli(name, project_dir=cwd)
    except mod.AgentCliError as exc:
        raise ToolLoopError(str(exc)) from exc
    return {"cli": name, "spec": spec,
            "model": str(model or "").strip() or None, "agentcli": mod}


def _tl_control_agent(agent: dict, cwd: str) -> dict:
    """制御応答（ツール要求 JSON）を出させるエージェント。

    ツール契約の 1 周は「次に何をするか」を JSON で言わせるだけで、編集能力は要らない。
    それを編集用の CLI（aider）にやらせると、材料が揃った瞬間にモデルは JSON をやめて
    成果物の本文を書き始める——しかも制御の周は readonly（`--dry-run`）なので、その本文は
    捨てられる。1 周 50〜90 秒を捨てることになる（実測）。

    定義が用途別の変種（`variants`）に "planner" を申告していれば、制御の周だけ
    そちらへ振り替える（agent-flow の planner と同じ「次に何をするか JSON で言わせる」
    契約なので、同じ用途キーを引く）。JSON モードの起動形は本文を返しようがないので、
    この失敗自体が起きない。申告が無い CLI・解決に失敗した場合は元のエージェントのまま
    （設定ミスで実行を殺さない——agentcli の方針と同じ）。役割の性質で振り替える口は
    agentcore が持っており、agent-flow / agent-project は既に使っている。ここは同じ口を
    使うだけで、新しい設定面を人に書かせない（C7・柱3）。モデルは元のエージェントの
    指定をそのまま持ち越す（呼び出し元が明示解決したモデルを、この振り替えは変えない）。
    """
    mod = agent.get("agentcli")
    name = str(agent.get("cli") or "")
    if mod is None or not name:
        return agent
    try:
        variant = mod.resolve_variant(name, "planner", cwd)
        return agent if not variant else _tl_resolve_agent(
            variant["agent_cli"], agent.get("model") or "", cwd)
    except (ToolLoopError, AttributeError):
        return agent


# ---------------------------------------------------------------------------
# 受入条件（acceptance）を入力にした証跡ゲート
# ---------------------------------------------------------------------------
#
# statemachine の従来ゲート（_tl_final_evidence_error）は、モデルの最終出力本文から
# `path: ...` 行を正規表現で拾って照合していた。opt-in の慣習なので、モデルが書き忘れると
# 何も検証しないし、成果物を作らないゴールと書き忘れを機械が区別できない。
#
# ここでは根拠を「人が承認した宣言」へ移す。受入条件の自然文からバッククォートで書かれた
# プロジェクト内パスを抽出し、その実在・この実行で触れたか・実際に変わったかを **LLM を
# 介さず** 照合する。パスを含まない基準は機械では判定できないので、判定層（検証エージェント）
# へ回す。二層の構造は backlog-verifier（verification_commands = 決定的 /
# task_acceptance_criteria = 自然文判定）と同じ（C7）。


def _tl_path_like(raw: str) -> bool:
    """バッククォート内の表記が「ファイルパス」か。

    受入条件の地の文にはコマンド名も同じ記法で出る（例: 「`agent-audit` の出力に無い…」）。
    それをパスとして拾うと、実在しない成果物として**永久に満たせない**条件になり、
    実際の成果物を書き終えた実行まで fail する。区別は形だけで足りる——区切り（/）か
    拡張子を持つものだけをパスとみなす。空白を含む断片はコマンド行なので除く。
    """
    return bool(raw) and not re.search(r"\s", raw) and (
        "/" in raw or "\\" in raw or bool(re.search(r"\.[A-Za-z0-9_]{1,10}$", raw)))


def acceptance_paths(acceptance: "list[str]", cwd: str) -> "list[str]":
    """受入条件の自然文から、機械照合できるプロジェクト内パスを抽出する。

    バッククォートで囲まれた表記だけを拾う（散文中の語をパスと誤認しない）。
    実在しないパスも返す——「作られるはずの成果物」は照合対象そのものなので、
    ここで落とすと未生成を検知できない。
    """
    # 呼び出し側が渡す cwd はシンボリックリンク経由のことがある（macOS の /var は
    # /private/var への symlink）。ここで realpath へ揃えないと、抽出したパスと
    # 実行中に集めた touched 集合が同じファイルを指しながら文字列として一致しない。
    root = os.path.realpath(str(cwd))
    found: list[str] = []
    for text in acceptance or []:
        for m in re.finditer(r"`([^`\n]+)`", str(text or "")):
            raw = m.group(1).strip()
            if (not _tl_path_like(raw) or raw.startswith("-")
                    or re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I)):
                continue
            try:
                file = _tl_project_path(root, raw)
            except ToolLoopError:
                continue   # コマンド例や作業フォルダ外の参照は照合対象にしない
            if file not in found:
                found.append(file)
    return found


def acceptance_evidence_errors(acceptance: "list[str]", *, cwd: str, touched: "set",
                               stamps_before: "dict") -> "list[str]":
    """機械層の判定。満たせない基準の理由を並べて返す（空リスト = 機械層は pass）。

    フェイルクローズ——宣言されたファイルが「実在し」「この実行で触れられ」「実際に
    変わった」の 3 つを全部満たさなければ fail。1 つでも欠ければ done の根拠にしない。
    """
    root = os.path.realpath(str(cwd))
    # touched は run 側が集める。realpath へ揃えてから突き合わせる（同上）。
    seen = {os.path.realpath(f) for f in (touched or set())}
    errors: list[str] = []
    for file in acceptance_paths(acceptance, root):
        rel = os.path.relpath(file, root)
        if not os.path.exists(file):
            errors.append(f"受入条件のファイルがありません: {rel}")
            continue
        if file not in seen:
            errors.append(f"この実行で生成・検証していないファイルです: {rel}")
            continue
        if _tl_file_stamp(file) == stamps_before.get(file, ""):
            errors.append(f"この実行で変更されていません: {rel}")
    return errors


def _tl_verified(acceptance: "list[str]", cwd: str) -> bool:
    """この実行で機械層が実際に何かを照合したか。

    受入条件が**書いてあること**と、それが**機械で照合できること**は別。バッククォートの
    プロジェクト内パスを 1 つも含まない基準（「*.md が作成されていること」等）は、判定層が
    入るまで誰も判定しない。ここを「条件があるか」で立てると、何も照合していない実行が
    「検証済み」として残り、C5 が言う偽 done そのものになる。
    """
    return bool(acceptance_paths(list(acceptance or []), cwd))


def acceptance_stamps(acceptance: "list[str]", cwd: str) -> dict:
    """実行前のファイル指紋。実行後の照合で「変わっていない」を検出するために取る。"""
    return {f: _tl_file_stamp(f) for f in acceptance_paths(acceptance, cwd)}


# ---------------------------------------------------------------------------
# 判定層: パスを含まない自然文基準を、読み取り専用の検証エージェントに判定させる
# ---------------------------------------------------------------------------
#
# 機械層（上）が照合できるのはバッククォートのプロジェクト内パスだけで、「レポートに
# 前週比が含まれている」のような基準は誰も見ていなかった。実行は `verified: false` で
# 記録されるが `ok: true` にはなるので、**書いた本人は検証されているつもり**でいる。
#
# 判定は既定で走らせない（opt-in）。判定のためにもう 1 回 CLI を起こすので、黙って
# 有効にするとトークン費用が倍増する経路が増える。有効にしたときは fail-closed——
# 判定できなかった基準は「満たしていない」として扱う。
#
# 判定役は `agents/<name>.json` の変種（`variants` の "verify"）へ振り替える。作業した
# 当人に自分の仕事を採点させるのが最も弱い構成で、どのモデルに検証させるかを決めるのは
# 定義側の責務だからだ（agent-flow / agent-project と同じ口を使い、新しい設定面を人に
# 書かせない）。振り替え先が無ければ元のエージェントのまま動く。
# 判定の実行上限も共通 fallback（_TL_DEFAULT_AGENT_TIMEOUT_SEC）に従う。判定役は変種
# （ollama-verify = gemma4:12b）で本体より遅いことが多く、ここだけ短い既定を持たせると
# 「本体は通るのに判定だけ切れる」になる。


def acceptance_prose(acceptance: "list[str]", cwd: str) -> "list[str]":
    """機械層が触れない基準（プロジェクト内パスを含まない自然文）を返す。"""
    machine = set(acceptance_paths(list(acceptance or []), cwd))
    out: list[str] = []
    for text in acceptance or []:
        one = str(text or "").strip()
        if one and not set(acceptance_paths([one], cwd)) & machine:
            out.append(one)
    return out


def _tl_judge_agent(agent: dict, cwd: str) -> dict:
    """検証専用の変種（用途キー `verify`）へ振り替える。無ければ元のまま。"""
    mod = agent.get("agentcli")
    name = str(agent.get("cli") or "")
    if mod is None or not name:
        return agent
    try:
        variant = mod.resolve_variant(name, "verify", cwd)
        if not variant:
            return agent
        # 変種は検証用に調整された自分の既定モデルを持つことが多いので、呼び出し元が
        # 明示していない限りそちらを使う（resolve_variant が返す model をそのまま渡す）。
        return _tl_resolve_agent(variant["agent_cli"], variant.get("model") or "", cwd)
    except (ToolLoopError, AttributeError, KeyError):
        return agent


def _tl_verified_by(acceptance: "list[str]", cwd: str, judged: bool) -> str:
    """この実行を実際に検証したのは誰か。`verified` の真偽だけでは足りない。

    機械照合と自然文の判定は確かさが違う。同じ `verified: true` に潰すと、後から
    「これはファイル指紋で見たのか、モデルが読んで良しと言ったのか」が分からない。
    """
    machine = _tl_verified(acceptance, cwd)
    if machine and judged:
        return "machine+judge"
    if machine:
        return "machine"
    return "judge" if judged else ""


def _tl_apply_judge(acceptance: "list[str]", *, cwd: str, agent: dict, log_file: str,
                    output: str, files: "list[str]", judge: bool) -> dict:
    """判定層を通す（opt-in）。返り値 {"errors": [...], "judged": bool}。

    judged は「判定層が実際に何かを判定したか」。基準が全部パス付きなら判定する対象が
    無いので False——何もしていないことを「判定した」と記録しない。
    """
    if not judge:
        return {"errors": [], "judged": False}
    prose = acceptance_prose(list(acceptance or []), cwd)
    if not prose:
        return {"errors": [], "judged": False}
    errors = judge_acceptance(prose, cwd=cwd, agent=agent, log_file=log_file,
                              output=output, files=list(files or []))
    return {"errors": errors, "judged": True}


def _tl_judge_prompt(criteria: "list[str]", *, cwd: str, output: str,
                     files: "list[str]") -> str:
    """判定の 1 回分。判定材料は「観測できたもの」だけを渡す。"""
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    listed = "\n".join(f"- {f}" for f in files) or "- none"
    return (
        "You judge whether each acceptance criterion is satisfied. You are a reviewer, "
        "not the author. Do not modify anything.\n"
        f"Working folder: {cwd}\n"
        f"Files changed by the run:\n{listed}\n"
        f"What the agent reported:\n---\n{output[:4000]}\n---\n"
        f"Criteria:\n{numbered}\n"
        "Read the files if you need to check a claim. Judge only what you can observe; "
        "the agent's report alone is not evidence. If you cannot verify a criterion, "
        'answer "fail" and say what is missing.\n'
        "Return exactly one JSON object and no markdown:\n"
        '{"verdicts":[{"n":1,"pass":true,"reason":"one short sentence"}]}\n'
        "Include one entry for every criterion number."
    )


def judge_acceptance(criteria: "list[str]", *, cwd: str, agent: dict, log_file: str,
                     output: str, files: "list[str]") -> "list[str]":
    """自然文基準を読み取り専用で判定し、満たしていない理由を並べて返す。

    空リスト = 判定層 pass。**fail-closed**——判定役を起こせない・出力を読めない・
    基準に対する判定が返ってこない、はすべて「満たしていない」に倒す。判定を頼まれて
    判定できなかったことを pass として記録すると、機械層を入れる前より悪くなる。
    """
    items = [str(c).strip() for c in (criteria or []) if str(c).strip()]
    if not items:
        return []
    judge = _tl_judge_agent(agent, cwd)
    mod = judge["agentcli"]
    prompt = _tl_judge_prompt(items, cwd=cwd, output=str(output or ""), files=list(files or []))
    try:
        built = mod.headless_cmd(judge["spec"], judge["model"], prompt,
                                 readonly=True, no_session=True)
    except Exception as exc:
        return [f"受入条件の判定を起動できませんでした（{judge['cli']}）: {exc}"]

    argv = built["argv"]
    timeout_sec = float(built.get("timeout") or 0) or _TL_DEFAULT_AGENT_TIMEOUT_SEC
    _tl_append_log(log_file, {"event": "judge_start", "cli": judge["cli"],
                              "model": judge["model"] or "", "criteria": len(items)})
    result = _tl_exec_argv(argv[0], argv[1:], cwd=cwd, timeout_sec=timeout_sec,
                           env=built.get("env") or {}, stdin=built.get("stdin"),
                           output_file=built.get("output_file"), log_file=log_file,
                           idle=True)
    if result["status"] != 0 or result["error"]:
        detail = (result["error"] or result["stderr"] or "").strip()[:300]
        return [f"受入条件を判定できませんでした（{judge['cli']}）: {detail or '実行に失敗しました'}"]

    parsed = _tl_parse_json_object(result["stdout"])
    verdicts = (parsed or {}).get("verdicts")
    if not isinstance(verdicts, list):
        return [f"受入条件の判定結果を読めませんでした（{judge['cli']} が JSON を返しませんでした）"]

    by_number: dict[int, dict] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            by_number[int(v.get("n"))] = v
        except (TypeError, ValueError):
            continue

    errors: list[str] = []
    for i, text in enumerate(items, 1):
        v = by_number.get(i)
        if v is None:
            errors.append(f"判定されなかった受入条件です: {text}")
            continue
        if not bool(v.get("pass")):
            reason = str(v.get("reason") or "").strip() or "理由の記載がありません"
            errors.append(f"受入条件を満たしていません: {text}（{reason}）")
    _tl_append_log(log_file, {"event": "judge_done", "cli": judge["cli"],
                              "criteria": len(items), "errors": errors})
    return errors


# ---------------------------------------------------------------------------
# ゴール単位のツールループ
# ---------------------------------------------------------------------------


def _tl_history_has_run(history: "list[str]") -> bool:
    """履歴に「成功した run」があるか。"""
    for entry in history or []:
        request = _tl_parse_json_object(entry)
        if request and request.get("type") == "run" and request.get("status") == 0:
            return True
    return False


def _tl_goal_prompt(*, goal: str, cwd: str, skills: "list[dict]", reads: "list[str]",
                    acceptance: "list[str]", history: "list[str]") -> str:
    """1 ゴール分の限定ツール要求プロンプト。

    文言は statemachine の planner プロンプトと同じ規律を引き継ぐ——小型ローカルモデルの
    実測失敗（作業をしていないのに完了を主張する／フラグと値を 1 トークンにまとめる／
    markdown で包む）から逆算したもので、飾りではない。
    """
    skill_lines = "\n".join(
        f"- {s['name']}: {s['root']}"
        + (f"\n  scripts: {', '.join(_tl_skill_scripts(s))}" if _tl_skill_scripts(s) else "")
        for s in skills) or "- none"
    criteria = "\n".join(f"- {a}" for a in acceptance) or "- none"
    return (
        "You execute exactly one task. Do not simulate it or claim work without a "
        "TOOL_RESULT. Do not start unrelated work.\n"
        f"Working folder: {cwd}\n"
        "Loaded skills (copy exact paths; do not guess script locations or add unrequested flags):\n"
        f"{skill_lines}\n"
        f"Readable files already assigned:\n{chr(10).join(reads) or '- none'}\n"
        f"Task:\n---\n{goal}\n---\n"
        f"Acceptance criteria (all must hold when you finish):\n{criteria}\n"
        + (f"Previous tool results:\n{chr(10).join(history)}\n" if history else "")
        # 「もう実行済み」は履歴に成功した run があるときだけ言う。無条件に出していた頃は
        # 1 周目から「実行済みだから走らせるな」と教えており、モデルは実行していない
        # コマンドを「実行した」と書いた（実測: 未実行のまま「executed successfully」）。
        + ("A run TOOL_RESULT with status 0 already completed; do not run that command again. "
           "Request read_files only if inspection is still required.\n"
           if _tl_history_has_run(history) else
           "If the task names a command, run it and use its TOOL_RESULT as the only source of "
           "facts. Never write results you have not seen in a TOOL_RESULT.\n")
        + ("For run.args, put every CLI token in its own JSON string. Never combine a flag and "
           "value or add flags not requested by the task.\n"
           "Return exactly one JSON object and no markdown. Allowed forms:\n"
           '{"type":"read_files","paths":["relative/path"]}\n'
           '{"type":"write_files","paths":["relative/path"]}\n'
           '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}\n'
           '{"type":"final","output":"a short report of what you did"}'))


def run_goal(*, goal: str, cwd: str, agent: dict, log_file: str,
             acceptance: "list[str] | None" = None, max_rounds: int = 0,
             tag: str = "toolloop", judge: bool = False,
             skills: "list[str] | None" = None) -> dict:
    """ツールループ非内蔵の CLI に、ゴール 1 件を限定ツール契約で実行させる。

    `skills` は entry の `slash` などプログラム経路で**明示指定**されたスキル名。
    解決できないときは黙って落とさず ToolLoopError（指定した手順が使われないまま
    成功に見えるのが一番悪い——agentcore.ollama_skills と同じ原則）。ゴール本文の
    `` `名前` スキル `` 表記から拾った名前は従来どおり、見つからなければ素通し。

    戻り値: {ok, output, files, evidenceErrors, logFile}。
    `ok` は「ツールループが final に到達し、機械層の証跡ゲートを通った」こと。
    受入条件が空なら機械層は何も検証しない——その事実は呼び出し側が「検証なし」として
    記録する（done の根拠にしない）。
    """
    root = os.path.realpath(str(cwd))
    criteria = list(acceptance or [])
    rounds = int(max_rounds or _TL_MAX_TOOL_ROUNDS)
    declared = [str(n).strip() for n in (skills or []) if str(n).strip()]
    names: list[str] = []
    for n in declared + _tl_action_skill_names(goal):
        if n not in names:
            names.append(n)
    resolved_skills: list[dict] = []
    missing: list[str] = []
    for n in names:
        s = _tl_resolve_skill(n, root)
        if s is not None:
            resolved_skills.append(s)
        elif n in declared:
            missing.append(n)
    if missing:
        raise ToolLoopError(
            f"スキルが見つかりません: {', '.join(missing)}\n"
            f"  探索先: {', '.join(_tl_skill_search_dirs(root))}\n"
            "  配布は `python install.py --agent aider --all-skills` 等で行います"
            "（既定インストールは tier: core のスキルだけです）。")
    skills = resolved_skills
    # 受入条件とゴール本文が名指しした実在ファイルは、最初から読める状態で渡す。
    # aider は「チャットに入っているファイル」しか触らないので、ここを渡さないと着手しない。
    reads: set = {f for f in (_tl_action_project_files(goal, root)
                              + acceptance_paths(criteria, root)) if os.path.isfile(f)}
    reads |= {s["skill_file"] for s in skills if s.get("skill_file")}
    stamps_before = acceptance_stamps(criteria, root)
    touched: set = set()
    history: list[str] = []
    output = ""
    pending_run_error = ""
    control = _tl_control_agent(agent, root)
    if control is not agent:
        _tl_progress(f"制御応答: {control['cli']}（編集: {agent['cli']}）", tag)

    # 却下はループを 1 周させるだけで、以前は画面にもログにも何も出さなかった。ローカル
    # モデルは 1 周に数十秒かかるので、外からは「止まっている」ようにしか見えない
    # ——実際は同じ場所を回っていることが多く、そこが見えないと人は打ち切りも修正もできない。
    def reject(error: str) -> None:
        _tl_progress(f"却下: {error}", tag)
        _tl_append_log(log_file, {"event": "rejected", "error": error})
        history.append("TOOL_RESULT " + json.dumps(
            {"rejected": True, "error": error}, ensure_ascii=False))

    for _round in range(rounds):
        _tl_progress(f"ラウンド {_round + 1}/{rounds}: エージェントに問い合わせ中…", tag)
        raw = _tl_run_control(control, _tl_goal_prompt(
            goal=goal, cwd=root, skills=skills, reads=sorted(reads),
            acceptance=criteria, history=history,
        ), cwd=root, read_files=sorted(reads), log_file=log_file)
        try:
            request = _tl_validate_tool_request(_tl_parse_tool_request(raw), root, skills)
        except ToolLoopError as exc:
            # 契約外の応答でも、受入条件が成果物を名指ししていて未着手なら、そこへの
            # write_files として続ける。小型モデルは材料が揃った瞬間に JSON をやめて
            # 本文を書き始める（実測: run の直後から 5 ラウンド連続で契約外）。ラウンドは
            # 1 周 90 秒前後で、捨てるほど余裕は無い。書けたかどうかは従来どおり機械層が
            # 見るので、ここを通しても done の根拠は緩まない（C5）。
            targets = [f for f in acceptance_paths(criteria, root) if f not in touched]
            if not targets:
                reject(str(exc))
                continue
            _tl_progress(f"契約外の応答（{exc}）。宣言済みの成果物へ書き込みます", tag)
            _tl_append_log(log_file, {"event": "fallback_write", "error": str(exc),
                                      "paths": targets})
            request = {"type": "write_files", "paths": targets}

        if request["type"] == "final":
            output = request["output"]
            break

        if request["type"] == "read_files":
            missing = [f for f in request["paths"] if not os.path.exists(f)]
            if missing:
                reject("読み取り対象がありません: "
                       + ", ".join(os.path.relpath(f, root) for f in missing))
                continue
            reads.update(request["paths"])
            _tl_progress("read_files: "
                         + ", ".join(os.path.relpath(f, root) for f in request["paths"]), tag)
            history.append("TOOL_RESULT " + json.dumps(
                {"type": request["type"], "paths": request["paths"]}, ensure_ascii=False))
            continue

        if request["type"] == "write_files":
            before = {f: _tl_file_stamp(f) for f in request["paths"]}
            for file in request["paths"]:
                parent = os.path.dirname(file)
                if parent:
                    os.makedirs(parent, exist_ok=True)
            _tl_progress("write_files: "
                         + ", ".join(os.path.relpath(f, root) for f in request["paths"]), tag)
            # 書き込みの呼び出しにも tool 結果を渡す。渡していなかった頃、コマンドを
            # 実行した直後の write_files がその出力を見ないまま書き、モデルは中身を
            # 創作した（実測: 集計に 3 グループ出ているのに「データなし」と書いた）。
            written = _tl_run_agent(
                agent,
                "Execute the task now and edit the editable files. Do not merely describe or "
                "return the existing content. Use the tool results below as the only source of "
                "facts: do not invent names, counts or numbers that are not in them. "
                "After editing, report briefly what you changed."
                f"\n\nTask:\n{goal}\n\nAcceptance criteria:\n"
                + "\n".join(f"- {a}" for a in criteria)
                + (f"\n\nTool results:\n{chr(10).join(history)}" if history else ""),
                cwd=root, readonly=False,
                read_files=[f for f in sorted(reads) if f not in request["paths"]],
                files=request["paths"], log_file=log_file)
            missing = [f for f in request["paths"] if not os.path.exists(f)]
            changed = any(_tl_file_stamp(f) != before[f] for f in request["paths"])
            if missing or not changed:
                reject(("書き込み対象がありません: "
                        + ", ".join(os.path.relpath(f, root) for f in missing)) if missing
                       else "write_files が対象ファイルを変更しませんでした")
                continue
            touched.update(request["paths"])
            output = written
            history.append("TOOL_RESULT " + json.dumps(
                {"type": request["type"], "paths": request["paths"]}, ensure_ascii=False))
            # 機械層が pass した時点で完了とし、`final` の申告を待たない。小型モデルは
            # 書き終えても final を出さず、同じ write_files を繰り返してラウンド上限まで
            # 走り続ける（実測: 1 回で書けた仕事に 8 ラウンド）。done の根拠は元から
            # 機械検証だけ（C5）なので、その PASS を停止条件にしても緩まない。
            # 照合できる受入条件が無いときは従来どおり final を待つ——止める根拠が無い。
            if _tl_verified(criteria, root) and not pending_run_error and not acceptance_evidence_errors(
                    criteria, cwd=root, touched=touched, stamps_before=stamps_before):
                _tl_progress("受入条件を満たしました（final を待たずに完了）", tag)
                break
            continue

        _tl_progress(f"run: {request['command']} {' '.join(request['args'])}", tag)
        tool = _tl_exec_argv(request["command"], request["args"], cwd=root,
                             timeout_sec=request["timeout_sec"], log_file=log_file)
        run_ok = tool["status"] == 0 and not tool["error"]
        if run_ok:
            # 引数を直した再試行も回復として扱う。小型モデルは最初の要求でサブコマンドを
            # 落とすことがあるため、完全一致の再実行だけに絞ると正しい修正まで失敗になる。
            pending_run_error = ""
        else:
            command = shlex.join([request["command"], *request["args"]])
            pending_run_error = (
                f"コマンド実行が失敗したままです（status {tool['status']}）: {command}")
            _tl_progress(pending_run_error, tag)
        for arg in request["args"]:
            try:
                file = _tl_project_path(root, arg)
            except ToolLoopError:
                continue   # プロジェクト内ファイルでない引数
            if os.path.isfile(file):
                touched.add(file)
                # ponytail: 大きな成果物の自動再投入はローカルモデルを詰まらせる。
                # 必要ならエージェントが read_files を要求する。
                if os.stat(file).st_size <= _TL_MAX_AUTO_READ_BYTES:
                    reads.add(file)
        history.append("TOOL_RESULT " + json.dumps({
            # ok は status 由来。stdout が空でも成功は成功（statemachine 側と同じ形）。
            "type": request["type"], "ok": run_ok,
            "status": tool["status"], "error": tool["error"],
            "stdout": tool["stdout"][-4000:], "stderr": tool["stderr"][-2000:],
            "logFile": log_file,
        }, ensure_ascii=False))

    evidence_errors = acceptance_evidence_errors(
        criteria, cwd=root, touched=touched, stamps_before=stamps_before)
    if pending_run_error:
        evidence_errors.append(pending_run_error)
    judged = _tl_apply_judge(criteria, cwd=root, agent=agent, log_file=log_file,
                             output=output, files=sorted(touched), judge=judge)
    evidence_errors.extend(judged["errors"])
    ok = bool(output) and not evidence_errors
    verified = _tl_verified(criteria, root) or judged["judged"]
    _tl_append_log(log_file, {"event": "goal_done", "ok": ok, "verified": verified,
                              "verifiedBy": _tl_verified_by(criteria, root, judged["judged"]),
                              "files": sorted(touched), "evidenceErrors": evidence_errors})
    return {"ok": ok, "output": output, "files": sorted(touched),
            "evidenceErrors": evidence_errors, "verified": verified,
            "verifiedBy": _tl_verified_by(criteria, root, judged["judged"]),
            "logFile": log_file}


def run_cli_loop(*, goal: str, cwd: str, agent: dict, log_file: str,
                 acceptance: "list[str] | None" = None, judge: bool = False) -> dict:
    """層2（tool-loop）: CLI 内部のループに任せて headless を 1 回呼ぶ。

    触ったファイルを外から観測できないので、証跡は受入条件が名指ししたファイルの
    指紋変化で見る（「この実行で変わったか」は観測できる）。
    """
    mod = agent["agentcli"]
    criteria = list(acceptance or [])
    stamps_before = acceptance_stamps(criteria, cwd)
    built = mod.headless_cmd(agent["spec"], agent["model"], goal,
                             readonly=False, no_session=True)
    argv = built["argv"]
    timeout_sec = float(built.get("timeout") or 0) or _TL_DEFAULT_AGENT_TIMEOUT_SEC
    result = _tl_exec_argv(argv[0], argv[1:], cwd=cwd, timeout_sec=timeout_sec,
                           env=built.get("env") or {}, stdin=built.get("stdin"),
                           output_file=built.get("output_file"), log_file=log_file,
                           idle=True)
    _tl_record_usage(agent, result, log_file)
    if result["status"] != 0 or result["error"]:
        detail = "\n".join(x for x in (result["error"], result["stderr"],
                                       result["stdout"]) if x)
        hint, transient = _tl_failure_hint(agent, detail)
        raise ToolLoopError(hint or detail or f"{argv[0]} が失敗しました",
                            transient=transient)
    output = str(result["stdout"] or "").strip()
    if not output and agent["spec"].get("empty_output_is_error", True):
        raise ToolLoopError("エージェントが空の応答を返しました")
    touched = {f for f in acceptance_paths(criteria, cwd)
               if _tl_file_stamp(f) != stamps_before.get(f, "")}
    errors = acceptance_evidence_errors(criteria, cwd=cwd, touched=touched,
                                        stamps_before=stamps_before)
    judged = _tl_apply_judge(criteria, cwd=cwd, agent=agent, log_file=log_file,
                             output=output, files=sorted(touched), judge=judge)
    errors.extend(judged["errors"])
    verified = _tl_verified(criteria, cwd) or judged["judged"]
    _tl_append_log(log_file, {"event": "goal_done", "ok": not errors,
                              "verified": verified,
                              "verifiedBy": _tl_verified_by(criteria, cwd, judged["judged"]),
                              "files": sorted(touched), "evidenceErrors": errors})
    return {"ok": not errors, "output": output, "files": sorted(touched),
            "evidenceErrors": errors, "verified": verified,
            "verifiedBy": _tl_verified_by(criteria, cwd, judged["judged"]),
            "logFile": log_file}


def run_prompt(*, goal: str, cwd: str, agent: dict, log_file: str,
               acceptance: "list[str] | None" = None, tag: str = "toolloop",
               judge: bool = False, slash: "list[str] | None" = None) -> dict:
    """ゴール 1 件を、CLI の層（`headless_autonomy`）に応じた経路で 1 回実行する。

    層の判定と分岐をここ 1 か所に置く（C7）。デーモンの headless 枝も `run` サブコマンドも
    同じ関数を通るので、「デーモン経由なら証跡ゲートが効くが単発だと効かない」のような
    経路差が生まれない。**tmux を使うかどうかはこの関数の関知しないこと**——tmux は
    コマンドを送り結果を見せる手段であって、実行契約の一部ではない。

    `slash` は entry の `slash` 行（`<名前> [引数]`。先頭の `/` は正規化済み）。
    以前は headless 経路で黙って捨てていた（対話ペインへ send-keys する前提の機能
    だったため）。層2（tool-loop 内蔵 CLI）へはネイティブのスラッシュコマンドとして
    本文先頭へ前置し、層3（single-shot）へはスキルとして解決してツールループへ渡す。
    """
    lines = [str(s).strip() for s in (slash or []) if str(s).strip()]
    if str(agent["spec"].get("headless_autonomy") or "single-shot") == "tool-loop":
        if lines:
            goal = "\n".join("/" + line for line in lines) + "\n\n" + goal
        return run_cli_loop(goal=goal, cwd=cwd, agent=agent, log_file=log_file,
                            acceptance=acceptance, judge=judge)
    skill_names: list[str] = []
    for line in lines:
        name, _, args = line.partition(" ")
        if name not in skill_names:
            skill_names.append(name)
        note = f"`{name}` スキルの手順に従って実行してください。"
        if args.strip():
            note += f"（引数: {args.strip()}）"
        goal = note + "\n" + goal
    return run_goal(goal=goal, cwd=cwd, agent=agent, log_file=log_file,
                    acceptance=acceptance, tag=tag, judge=judge, skills=skill_names)


def _tl_run_log_file(tag: str = "run") -> str:
    directory = agent_home_subdir("AGENT_LOOP_RUN_DIR", "runs") / "headless"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / f"{int(time.time() * 1000)}-{tag}.jsonl")


def cmd_run(args: argparse.Namespace, cwd: Path) -> None:
    """run サブコマンド: プロンプト 1 件をその場で 1 回実行する（デーモン不要）。

    `send` が「常駐セッションへ送る」のに対し、こちらは「今ここで 1 回実行して結果を返す」。
    **対話ペインの有無で呼び分けるものではない**——tmux はこのコマンドを走らせて様子を
    見せる側の手段であって、実行契約とは独立している。`statemachine` と同じく終了時に
    `RESULT {json}` を 1 行出し、それが呼び出し側（dashboard の定常業務）との結果契約になる。
    """
    work_dir = Path(getattr(args, "dir", None) or cwd).expanduser().resolve()
    if not work_dir.is_dir():
        print(f"[agent-loop] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
        sys.exit(1)
    goal = " ".join(getattr(args, "prompt", None) or []).strip()
    # send と同じ流儀: 実在するファイルパスを渡したらその中身を本文にする。
    candidate = (work_dir / goal) if goal and not os.path.isabs(goal) else Path(goal or ".")
    if goal and candidate.is_file():
        goal = candidate.read_text(encoding="utf-8").strip()
    if not goal:
        print("[agent-loop] ERROR: プロンプトが空です。", file=sys.stderr)
        sys.exit(2)
    acceptance = [str(a).strip() for a in (getattr(args, "acceptance", None) or []) if str(a).strip()]
    log_file = _tl_run_log_file()
    try:
        agent = _tl_resolve_agent(getattr(args, "agent_cli", None) or "aider",
                                  getattr(args, "model", None) or "", str(work_dir))
        _tl_progress(f"agent: {agent['cli']}"
                     + (f" / model: {agent['model']}" if agent["model"] else " (default model)")
                     + f" / log: {log_file}", "run")
        judge = bool(getattr(args, "judge", False))
        if not _tl_verified(acceptance, str(work_dir)) and not (
                judge and acceptance_prose(acceptance, str(work_dir))):
            # 「条件が無い」と「条件はあるが機械で照合できない」を区別して伝える。
            # 後者は書いた本人が検証されているつもりでいる分、黙って通す害が大きい。
            _tl_progress(
                (f"受入条件（--acceptance）がありません。{agent['cli']} は自分でツールを回さない"
                 "ため、done を検証できません。"
                 if not acceptance else
                 "受入条件にバッククォートで囲んだファイルパスがありません"
                 "（例: `reports/digest.md` が更新されている）。"
                 "機械が照合できるのはこの表記だけで、グロブや文章だけの条件は判定されません"
                 "（--judge で検証エージェントに判定させられます）。")
                + "実行はしますが結果は「検証なし」として記録します。", "run")
        result = run_prompt(goal=goal, cwd=str(work_dir), agent=agent, log_file=log_file,
                            acceptance=acceptance, tag="run", judge=judge)
        print("RESULT " + json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get("ok") else 1)
    except ToolLoopError as exc:
        print("RESULT " + json.dumps({"ok": False, "error": str(exc), "logFile": log_file},
                                     ensure_ascii=False))
        print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
