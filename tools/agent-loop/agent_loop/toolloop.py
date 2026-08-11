from __future__ import annotations
# toolloop.py — ツールループを持たない headless CLI（agents/<name>.json の
# headless_autonomy: single-shot）へ、限定ツール契約でツール実行を供給する共用ハーネス。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 設計: docs/plans/2026-08-11-agent-loop-headless-agent-cli-design.md。
# aider は渡されたファイルを編集するだけ、素の ollama は純粋な推論で、どちらも自分では
# 探索もコマンド実行もしない。ここが read_files / write_files / run / final の 4 つだけを
# 許す契約でその不足を補う。**ゴールに依存しない部分だけ**を持ち、ステートマシンの遷移
# （statemachine.py）と定期プロンプトの実行（scheduler.py）の両方から使う——同じ護りを
# 2 実装に分けない（C7）。
#
# 由来: statemachine.py の限定ツールループを切り出したもの。statemachine.py 側は
# 末尾の別名で従来の `_sm_*` を指し続ける（呼び出し側を書き換えないため）。

# statemachine.py — aider 等の headless CLI 向けステートマシン実行ハーネス。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 設計: docs/plans/2026-08-11-agent-dashboard-routine-aider-tmux-harness-design.md。
# dashboard の in-process 実行器（旧 stateMachineRunner.js）の限定ツール契約を
# agent-loop 側へ移し、tmux ウィンドウの中で「aider を動かしている様子」が見える
# 実行にする。ステートマシンの検証・遷移は statemachine-use スキルのスクリプト
# （run_machine.py / next_state.py）を正典として使い、Aider に不足するツール実行
# （read_files / write_files / run / final）だけを狭い契約で補う。
#
# CLI とモデルの解決は agentcore.agentcli（agents/<name>.json 契約）へ委譲する。
# モデルは `agent-loop statemachine --model` で実行ごとに指定できる（省略時は
# 定義の default_model）。

_TL_MAX_TOOL_ROUNDS = 8
_TL_MAX_TOOL_TIMEOUT_SEC = 300
_TL_MAX_AUTO_READ_BYTES = 32768
_TL_HARNESS_TIMEOUT_SEC = 30
_TL_DEFAULT_AIDER_TIMEOUT_SEC = 180
# ponytail: 上限は固定値。経路ごとに変えたくなるまで設定にしない。
_TL_SHELLS = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe",
              "powershell", "powershell.exe", "pwsh"}
# statemachine-use のスクリプトが要求する Python（README の動作環境と同じ 3.10 以上）。
_TL_SKILL_PYTHON_MIN = (3, 10)
_TL_PYTHON_CANDIDATES = ("python3", "python", "python3.14", "python3.13",
                         "python3.12", "python3.11", "python3.10")
_TL_SKILL_PYTHON = ""


class ToolLoopError(RuntimeError):
    """ツールループの実行失敗（検証違反・契約不成立・環境不足）。"""


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


def _tl_resolve_skill(name: str, cwd: str) -> "dict | None":
    roots = [
        os.path.join(cwd, ".github", "skills", name),
        os.path.join(_tl_source_root(), ".github", "skills", name) if _tl_source_root() else "",
        os.path.join(os.path.expanduser("~"), ".agents", "skills", name),
        os.path.join(os.path.expanduser("~"), ".codex", "skills", name),
    ]
    for root in roots:
        if root and os.path.isfile(os.path.join(root, "SKILL.md")):
            return {"name": name, "root": root, "skill_file": os.path.join(root, "SKILL.md")}
    return None


def _tl_action_skill_names(text: str) -> "list[str]":
    out: list[str] = []
    for m in re.finditer(r"`([A-Za-z0-9_.-]+)`\s*スキル", str(text or "")):
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
        if re.search(r"\.py$", command, re.I):
            args = [command, *args]
            command = _tl_python_command()
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


def _tl_progress(message: str, tag: str = "statemachine") -> None:
    """tmux ウィンドウ（人が見る画面）への進行表示。ログとは別に短く出す。"""
    print(f"[{tag}] {message}", flush=True)


def _tl_exec_argv(command: str, args: "list[str]", *, cwd: str, timeout_sec: float,
                  env: "dict | None" = None, stdin: "str | None" = None,
                  output_file: "str | None" = None, log_file: str) -> dict:
    started = time.time()
    argv = [command, *args]
    _tl_append_log(log_file, {"event": "start", "argv": argv, "cwd": cwd,
                              "timeoutMs": int(timeout_sec * 1000)})
    merged_env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "1000",
                  **(env or {})}
    result = {"status": None, "stdout": "", "stderr": "", "error": ""}
    try:
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


def _tl_run_agent(agent: dict, prompt: str, *, cwd: str, readonly: bool,
                  read_files: "list[str]", files: "list[str]", log_file: str) -> str:
    """エージェント CLI（aider 等）を headless で 1 回呼び、応答本文を返す。"""
    mod = agent["agentcli"]
    built = mod.headless_cmd(agent["spec"], agent["model"], prompt,
                             readonly=readonly, no_session=True,
                             read_files=read_files, files=files)
    argv = built["argv"]
    timeout_sec = float(built.get("timeout") or 0) or _TL_DEFAULT_AIDER_TIMEOUT_SEC
    result = _tl_exec_argv(argv[0], argv[1:], cwd=cwd, timeout_sec=timeout_sec,
                           env=built.get("env") or {}, stdin=built.get("stdin"),
                           output_file=built.get("output_file"), log_file=log_file)
    if result["status"] != 0 or result["error"]:
        detail = "\n".join(x for x in (result["error"], result["stderr"], result["stdout"]) if x)
        classified = mod.classify_error(agent["spec"], detail)
        hint = classified[1] if classified else ""
        raise ToolLoopError(hint or detail or f"{argv[0]} が失敗しました")
    output = str(result["stdout"] or "").strip()
    if not output:
        raise ToolLoopError("エージェントが空の応答を返しました")
    return output


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
    try:
        stat = os.stat(file)
        return f"{stat.st_size}:{stat.st_mtime_ns}"
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
            if not raw or raw.startswith("-") or re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
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


def acceptance_stamps(acceptance: "list[str]", cwd: str) -> dict:
    """実行前のファイル指紋。実行後の照合で「変わっていない」を検出するために取る。"""
    return {f: _tl_file_stamp(f) for f in acceptance_paths(acceptance, cwd)}


# ---------------------------------------------------------------------------
# ゴール単位のツールループ
# ---------------------------------------------------------------------------


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
        + "A run TOOL_RESULT with status 0 already completed; do not run that command again. "
          "Request read_files only if inspection is still required.\n"
          "For run.args, put every CLI token in its own JSON string. Never combine a flag and "
          "value or add flags not requested by the task.\n"
          "Return exactly one JSON object and no markdown. Allowed forms:\n"
          '{"type":"read_files","paths":["relative/path"]}\n'
          '{"type":"write_files","paths":["relative/path"]}\n'
          '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}\n'
          '{"type":"final","output":"a short report of what you did"}')


def run_goal(*, goal: str, cwd: str, agent: dict, log_file: str,
             acceptance: "list[str] | None" = None, max_rounds: int = 0,
             tag: str = "toolloop") -> dict:
    """ツールループ非内蔵の CLI に、ゴール 1 件を限定ツール契約で実行させる。

    戻り値: {ok, output, files, evidenceErrors, logFile}。
    `ok` は「ツールループが final に到達し、機械層の証跡ゲートを通った」こと。
    受入条件が空なら機械層は何も検証しない——その事実は呼び出し側が「検証なし」として
    記録する（done の根拠にしない）。
    """
    root = os.path.realpath(str(cwd))
    criteria = list(acceptance or [])
    rounds = int(max_rounds or _TL_MAX_TOOL_ROUNDS)
    skills = [s for s in (_tl_resolve_skill(n, root) for n in _tl_action_skill_names(goal)) if s]
    # 受入条件とゴール本文が名指しした実在ファイルは、最初から読める状態で渡す。
    # aider は「チャットに入っているファイル」しか触らないので、ここを渡さないと着手しない。
    reads: set = {f for f in (_tl_action_project_files(goal, root)
                              + acceptance_paths(criteria, root)) if os.path.isfile(f)}
    reads |= {s["skill_file"] for s in skills if s.get("skill_file")}
    stamps_before = acceptance_stamps(criteria, root)
    touched: set = set()
    history: list[str] = []
    output = ""

    for _round in range(rounds):
        raw = _tl_run_agent(agent, _tl_goal_prompt(
            goal=goal, cwd=root, skills=skills, reads=sorted(reads),
            acceptance=criteria, history=history,
        ), cwd=root, readonly=True, read_files=sorted(reads), files=[], log_file=log_file)
        try:
            request = _tl_validate_tool_request(_tl_parse_tool_request(raw), root, skills)
        except ToolLoopError as exc:
            history.append("TOOL_RESULT " + json.dumps(
                {"rejected": True, "error": str(exc)}, ensure_ascii=False))
            continue

        if request["type"] == "final":
            output = request["output"]
            break

        if request["type"] == "read_files":
            missing = [f for f in request["paths"] if not os.path.exists(f)]
            if missing:
                history.append("TOOL_RESULT " + json.dumps(
                    {"rejected": True, "error": "読み取り対象がありません: "
                     + ", ".join(os.path.relpath(f, root) for f in missing)}, ensure_ascii=False))
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
            written = _tl_run_agent(
                agent,
                "Execute the task now and edit the editable files. Do not merely describe or "
                "return the existing content. After editing, report briefly what you changed."
                f"\n\nTask:\n{goal}\n\nAcceptance criteria:\n"
                + "\n".join(f"- {a}" for a in criteria),
                cwd=root, readonly=False,
                read_files=[f for f in sorted(reads) if f not in request["paths"]],
                files=request["paths"], log_file=log_file)
            missing = [f for f in request["paths"] if not os.path.exists(f)]
            changed = any(_tl_file_stamp(f) != before[f] for f in request["paths"])
            if missing or not changed:
                error = ("書き込み対象がありません: "
                         + ", ".join(os.path.relpath(f, root) for f in missing)
                         if missing else "write_files が対象ファイルを変更しませんでした")
                history.append("TOOL_RESULT " + json.dumps(
                    {"rejected": True, "error": error}, ensure_ascii=False))
                continue
            touched.update(request["paths"])
            output = written
            history.append("TOOL_RESULT " + json.dumps(
                {"type": request["type"], "paths": request["paths"]}, ensure_ascii=False))
            continue

        _tl_progress(f"run: {request['command']} {' '.join(request['args'])}", tag)
        tool = _tl_exec_argv(request["command"], request["args"], cwd=root,
                             timeout_sec=request["timeout_sec"], log_file=log_file)
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
            "type": request["type"], "status": tool["status"], "error": tool["error"],
            "stdout": tool["stdout"][-4000:], "stderr": tool["stderr"][-2000:],
            "logFile": log_file,
        }, ensure_ascii=False))

    evidence_errors = acceptance_evidence_errors(
        criteria, cwd=root, touched=touched, stamps_before=stamps_before)
    ok = bool(output) and not evidence_errors
    _tl_append_log(log_file, {"event": "goal_done", "ok": ok, "verified": bool(criteria),
                              "files": sorted(touched), "evidenceErrors": evidence_errors})
    return {"ok": ok, "output": output, "files": sorted(touched),
            "evidenceErrors": evidence_errors, "verified": bool(criteria), "logFile": log_file}
