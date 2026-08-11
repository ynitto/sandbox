from __future__ import annotations
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

_SM_MAX_TOOL_ROUNDS = 8
_SM_MAX_TOOL_TIMEOUT_SEC = 300
_SM_MAX_AUTO_READ_BYTES = 32768
_SM_HARNESS_TIMEOUT_SEC = 30
_SM_DEFAULT_AIDER_TIMEOUT_SEC = 180
# ponytail: 初版は statemachine-use 1 経路だけなので固定上限（dashboard 実装と同値）。
_SM_SHELLS = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe",
              "powershell", "powershell.exe", "pwsh"}


class StateMachineHarnessError(RuntimeError):
    """ハーネスの実行失敗（検証違反・契約不成立・環境不足）。"""


def _sm_inside(root: str, file: str) -> bool:
    rel = os.path.relpath(file, root)
    return rel == "." or not (rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel))


def _sm_project_path(cwd: str, value) -> str:
    """作業フォルダ内へ正規化した絶対パス。`..`・シンボリックリンクの逸脱は拒否。"""
    root = os.path.realpath(str(cwd))
    raw = str(value or "").strip()
    if not raw or "\0" in raw:
        raise StateMachineHarnessError("空または不正なファイルパスです")
    requested = os.path.abspath(os.path.join(str(cwd), raw))
    parent = requested
    while not os.path.exists(parent):
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        parent = nxt
    real_parent = os.path.realpath(parent)
    target = os.path.abspath(os.path.join(real_parent, os.path.relpath(requested, parent)))
    if not _sm_inside(root, target):
        raise StateMachineHarnessError(f"作業フォルダ外のパスは使えません: {raw}")
    return target


def _sm_source_root() -> str:
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


def _sm_resolve_skill(name: str, cwd: str) -> "dict | None":
    roots = [
        os.path.join(cwd, ".github", "skills", name),
        os.path.join(_sm_source_root(), ".github", "skills", name) if _sm_source_root() else "",
        os.path.join(os.path.expanduser("~"), ".agents", "skills", name),
        os.path.join(os.path.expanduser("~"), ".codex", "skills", name),
    ]
    for root in roots:
        if root and os.path.isfile(os.path.join(root, "SKILL.md")):
            return {"name": name, "root": root, "skill_file": os.path.join(root, "SKILL.md")}
    return None


def _sm_action_skill_names(text: str) -> "list[str]":
    out: list[str] = []
    for m in re.finditer(r"`([A-Za-z0-9_.-]+)`\s*スキル", str(text or "")):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def _sm_action_project_files(text: str, cwd: str) -> "list[str]":
    files: list[str] = []
    for m in re.finditer(r"`([^`\n]+)`", str(text or "")):
        raw = m.group(1).strip()
        if not raw or raw.startswith("-") or re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
            continue
        try:
            file = _sm_project_path(cwd, raw)
        except StateMachineHarnessError:
            continue   # コマンド例や作業フォルダ外の参照は割り当てない
        if os.path.isfile(file) and file not in files:
            files.append(file)
    return files


def _sm_skill_scripts(skill: dict) -> "list[str]":
    directory = os.path.join(skill["root"], "scripts")
    try:
        return [os.path.join(directory, n) for n in sorted(os.listdir(directory))
                if re.search(r"\.(?:py|js|sh)$", n, re.I)]
    except OSError:
        return []


def _sm_executable_on_path(command: str) -> str:
    return shutil.which(str(command)) or ""


def _sm_validate_command(command, cwd: str, skill_dirs: "list[str]") -> str:
    raw = str(command or "").strip()
    if not raw or re.search(r"[\s\0]", raw):
        raise StateMachineHarnessError("run.command は単一の実行ファイル名が必要です")
    if os.path.basename(raw).lower() in _SM_SHELLS:
        raise StateMachineHarnessError(f"シェルの実行は許可されていません: {raw}")
    if not os.path.isabs(raw) and "/" not in raw and "\\" not in raw:
        if not _sm_executable_on_path(raw):
            raise StateMachineHarnessError(f"PATH 上に実行ファイルがありません: {raw}")
        return raw
    for root in [cwd, *skill_dirs]:
        try:
            resolved = _sm_project_path(root, raw)
        except StateMachineHarnessError:
            continue
        if os.path.exists(resolved):
            return resolved
    raise StateMachineHarnessError(f"実行ファイルは作業フォルダまたはロード済みスキル内に限定されます: {raw}")


def _sm_validate_arg_paths(args: "list[str]", cwd: str, skill_dirs: "list[str]") -> None:
    for arg in args:
        if "\0" in arg:
            raise StateMachineHarnessError("run.args に NUL は使えません")
        if re.match(r"^[a-z][a-z0-9+.-]*://", arg, re.I) or arg.startswith("-"):
            continue
        if os.path.isabs(arg) or ".." in re.split(r"[\\/]", arg):
            candidate = arg if os.path.isabs(arg) else os.path.abspath(os.path.join(cwd, arg))
            allowed = False
            for root in [cwd, *skill_dirs]:
                try:
                    _sm_project_path(root, candidate)
                    allowed = True
                    break
                except StateMachineHarnessError:
                    continue
            if not allowed:
                raise StateMachineHarnessError(f"作業フォルダ外の引数パスは使えません: {arg}")


def _sm_validate_tool_request(raw, cwd: str, skills: "list[dict]") -> dict:
    if not isinstance(raw, dict):
        raise StateMachineHarnessError("Aider のツール要求が JSON オブジェクトではありません")
    kind = str(raw.get("type") or "")
    skill_dirs = [s["root"] for s in skills if s.get("root")]
    if kind in ("read_files", "write_files"):
        paths = raw.get("paths")
        if (not isinstance(paths, list) or not paths
                or any(not isinstance(p, str) for p in paths)):
            raise StateMachineHarnessError(f"{kind}.paths は1件以上の文字列配列が必要です")
        return {"type": kind, "paths": [_sm_project_path(cwd, p) for p in paths]}
    if kind == "run":
        args = raw.get("args")
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            raise StateMachineHarnessError("run.args は文字列配列が必要です")
        args = [str(a) for a in args]
        _sm_validate_arg_paths(args, cwd, skill_dirs)
        command = _sm_validate_command(raw.get("command"), cwd, skill_dirs)
        if re.search(r"\.py$", command, re.I):
            args = [command, *args]
            command = _sm_python_command()
        try:
            timeout_sec = int(float(raw.get("timeout_sec") or 0)) or 60
        except (TypeError, ValueError):
            timeout_sec = 60
        return {"type": kind, "command": command, "args": args,
                "timeout_sec": max(1, min(timeout_sec, _SM_MAX_TOOL_TIMEOUT_SEC))}
    if kind == "final":
        return {"type": kind, "output": str(raw.get("output") or "").strip()}
    raise StateMachineHarnessError(f"許可されていないツール要求です: {kind or '(空)'}")


def _sm_parse_json_object(text) -> "dict | None":
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


def _sm_parse_tool_request(text) -> dict:
    request = _sm_parse_json_object(text)
    if not request or not request.get("type"):
        raise StateMachineHarnessError(
            f"Aider のツール要求を JSON として読めません: {str(text)[:160]}")
    return request


def _sm_append_log(log_file: str, event: dict) -> None:
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _dt.datetime.now(_dt.timezone.utc).isoformat(), **event},
                           ensure_ascii=False) + "\n")


def _sm_progress(message: str) -> None:
    """tmux ウィンドウ（人が見る画面）への進行表示。ログとは別に短く出す。"""
    print(f"[statemachine] {message}", flush=True)


def _sm_exec_argv(command: str, args: "list[str]", *, cwd: str, timeout_sec: float,
                  env: "dict | None" = None, stdin: "str | None" = None,
                  output_file: "str | None" = None, log_file: str) -> dict:
    started = time.time()
    argv = [command, *args]
    _sm_append_log(log_file, {"event": "start", "argv": argv, "cwd": cwd,
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
    _sm_append_log(log_file, {
        "event": "finish", "argv": argv, "cwd": cwd,
        "durationMs": int((time.time() - started) * 1000),
        "status": result["status"], "error": result["error"],
        "stdout": result["stdout"], "stderr": result["stderr"],
    })
    return result


def _sm_run_agent(agent: dict, prompt: str, *, cwd: str, readonly: bool,
                  read_files: "list[str]", files: "list[str]", log_file: str) -> str:
    """エージェント CLI（aider 等）を headless で 1 回呼び、応答本文を返す。"""
    mod = agent["agentcli"]
    built = mod.headless_cmd(agent["spec"], agent["model"], prompt,
                             readonly=readonly, no_session=True,
                             read_files=read_files, files=files)
    argv = built["argv"]
    timeout_sec = float(built.get("timeout") or 0) or _SM_DEFAULT_AIDER_TIMEOUT_SEC
    result = _sm_exec_argv(argv[0], argv[1:], cwd=cwd, timeout_sec=timeout_sec,
                           env=built.get("env") or {}, stdin=built.get("stdin"),
                           output_file=built.get("output_file"), log_file=log_file)
    if result["status"] != 0 or result["error"]:
        detail = "\n".join(x for x in (result["error"], result["stderr"], result["stdout"]) if x)
        classified = mod.classify_error(agent["spec"], detail)
        hint = classified[1] if classified else ""
        raise StateMachineHarnessError(hint or detail or f"{argv[0]} が失敗しました")
    output = str(result["stdout"] or "").strip()
    if not output:
        raise StateMachineHarnessError("Aider が空の応答を返しました")
    return output


def _sm_scalar(value) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)


def _sm_workflow_action(workflow_path: str, state_id: str, state: dict) -> dict:
    base = os.path.dirname(workflow_path)
    action_file = _sm_scalar(state.get("action_file"))
    inline = _sm_scalar(state.get("action"))
    if action_file:
        file = os.path.abspath(os.path.join(base, action_file))
        if not _sm_inside(base, file):
            raise StateMachineHarnessError(f"定義フォルダ外の action_file です: {action_file}")
        return {"text": Path(file).read_text(encoding="utf-8"), "file": file}
    if inline.startswith("file:"):
        file = os.path.abspath(os.path.join(base, inline[5:].strip()))
        if not _sm_inside(base, file):
            raise StateMachineHarnessError(f"定義フォルダ外の action 参照です: {inline}")
        return {"text": Path(file).read_text(encoding="utf-8"), "file": file}
    if inline:
        return {"text": inline, "file": ""}
    file = os.path.join(base, "actions", f"{state_id}.md")
    if os.path.exists(file):
        return {"text": Path(file).read_text(encoding="utf-8"), "file": file}
    return {"text": "", "file": ""}


def _sm_value_at(context, key: str):
    value = context
    for part in str(key).split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _sm_render_template(text, context: dict) -> str:
    def repl(m):
        value = _sm_value_at(context, m.group(1).strip())
        return m.group(0) if value is None else str(value)
    return re.sub(r"\{\{([^}]+)\}\}", repl, str(text or ""))


def _sm_set_nested(target: dict, key: str, value) -> None:
    parts = str(key).split(".")
    cursor = target
    for part in parts[:-1]:
        if not isinstance(cursor.get(part), dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _sm_initial_context(workflow: dict, parameters: "dict | None") -> dict:
    declared = dict(workflow.get("context")) if isinstance(workflow.get("context"), dict) else {}
    context: dict = {**declared, "context": declared, "input": "",
                     "history": {}, "last_output": "", "step_count": 0}
    for key, value in (parameters or {}).items():
        if key == "input":
            context["input"] = value
        elif key.startswith("context."):
            _sm_set_nested(declared, key[len("context."):], value)
        else:
            _sm_set_nested(context, key, value)
    return context


def _sm_validates(output, rule) -> bool:
    validator = str(rule or "")
    if not validator.startswith("startswith:"):
        return True
    first = str(output or "").splitlines()[0].strip() if str(output or "") else ""
    return any(first.startswith(v.strip()) for v in validator[len("startswith:"):].split(","))


def _sm_validated_output(output, rule) -> str:
    text = str(output or "").strip()
    if _sm_validates(text, rule):
        return text
    validator = str(rule or "")
    if not validator.startswith("startswith:"):
        return text
    prefixes = [v.strip() for v in validator[len("startswith:"):].split(",")]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    at = -1
    for i in range(len(lines) - 1, -1, -1):
        if any(lines[i].startswith(p) for p in prefixes):
            at = i
            break
    return "" if at < 0 else "\n".join(lines[at:at + 4])


_SM_FAILURE_RE = re.compile(r"^(?:[A-Z][A-Z0-9_]*_)?(?:FAILED|ERROR)\b", re.I)


def _sm_final_evidence_error(output, cwd: str, evidence: set, executed: set) -> str:
    text = str(output or "").strip()
    if _SM_FAILURE_RE.match(text):
        return ""
    m = re.search(r"^path:\s*(.+?)\s*$", text, re.I | re.M)
    if not m:
        return ""
    try:
        file = _sm_project_path(cwd, m.group(1))
    except StateMachineHarnessError as exc:
        return str(exc)
    if not os.path.isfile(file):
        return f"成功出力のファイルがありません: {m.group(1)}"
    if file not in evidence:
        return f"このステートで確認・生成していないファイルです: {m.group(1)}"
    if file not in executed:
        return f"この実行で生成・検証していないファイルです: {m.group(1)}"
    return ""


def _sm_file_stamp(file: str) -> str:
    try:
        stat = os.stat(file)
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return ""


def _sm_terminal_status(state_id, output) -> dict:
    # ponytail: workflow schema に終端成否が無いので慣例名と出力を使う（dashboard 実装と同一）。
    failed = bool(
        re.search(r"(?:^|[_-])(?:fail(?:ed)?|error)(?:$|[_-])", str(state_id or ""), re.I)
        or re.match(r"^(?:[A-Z][A-Z0-9_]*_)?FAILED\b", str(output or "").strip()))
    return {"ok": not failed, "error": str(output or "").strip() if failed else ""}


def _sm_planner_prompt(*, action: str, cwd: str, skills: "list[dict]",
                       reads: "list[str]", history: "list[str]", retry: str) -> str:
    skill_lines = "\n".join(
        f"- {s['name']}: {s['root']}"
        + (f"\n  scripts: {', '.join(_sm_skill_scripts(s))}" if _sm_skill_scripts(s) else "")
        for s in skills) or "- none"
    return (
        "You execute exactly one state-machine action. Do not simulate it or claim work "
        "without a TOOL_RESULT. Do not work on later states.\n"
        f"Working folder: {cwd}\n"
        "Loaded skills (copy exact paths; do not guess script locations or add unrequested flags):\n"
        f"{skill_lines}\n"
        f"Readable files already assigned:\n{chr(10).join(reads) or '- none'}\n"
        + (f"{retry}\n" if retry else "")
        + f"Current action:\n---\n{action}\n---\n"
        + (f"Previous tool results:\n{chr(10).join(history)}\n" if history else "")
        + "A run TOOL_RESULT with status 0 already completed; do not run that command again. "
          "Request read_files only if inspection is still required.\n"
          "For run.args, put every CLI token in its own JSON string. Never combine a flag and "
          "value or add flags not requested by the action.\n"
          "Return exactly one JSON object and no markdown. Allowed forms:\n"
          '{"type":"read_files","paths":["relative/path"]}\n'
          '{"type":"write_files","paths":["relative/path"]}\n'
          '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}\n'
          '{"type":"final","output":"the exact action Output Contract"}')


def _sm_max_attempts(state: dict) -> int:
    try:
        retries = int(float(state.get("max_retries")))
    except (TypeError, ValueError):
        retries = 0
    return max(1, retries + 1)


def _sm_execute_action(*, workflow_path: str, state_id: str, state: dict, context: dict,
                       cwd: str, agent: dict, log_file: str, touched: set) -> str:
    action = _sm_workflow_action(workflow_path, state_id, state)
    rendered = _sm_render_template(action["text"], context)
    skills = [s for s in (_sm_resolve_skill(n, cwd) for n in _sm_action_skill_names(rendered)) if s]
    action_reads = _sm_action_project_files(rendered, cwd)
    reads: set = {f for f in [workflow_path, action["file"], *(s["skill_file"] for s in skills)] if f}
    validator = state.get("output_validator")
    max_attempts = _sm_max_attempts(state)

    for attempt in range(max_attempts):
        history: list[str] = []
        evidence: set = set()
        for _round in range(_SM_MAX_TOOL_ROUNDS):
            retry = (f"Retry {attempt}/{max_attempts - 1}: the previous output violated "
                     "the Output Contract." if attempt else "")
            raw = _sm_run_agent(agent, _sm_planner_prompt(
                action=rendered, cwd=cwd, skills=skills, reads=sorted(reads),
                history=history, retry=retry,
            ), cwd=cwd, readonly=True, read_files=sorted(reads), files=[], log_file=log_file)
            try:
                request = _sm_validate_tool_request(_sm_parse_tool_request(raw), cwd, skills)
            except StateMachineHarnessError as exc:
                evidence_error = _sm_final_evidence_error(raw, cwd, evidence, touched)
                contract = _sm_validated_output(raw, validator)
                if contract and not evidence_error:
                    return contract
                history.append("TOOL_RESULT " + json.dumps(
                    {"rejected": True, "error": str(exc)}, ensure_ascii=False))
                continue
            if request["type"] == "final":
                evidence_error = _sm_final_evidence_error(request["output"], cwd, evidence, touched)
                if evidence_error:
                    declared = re.search(r"^path:\s*(.+?)\s*$", request["output"], re.I | re.M)
                    declared_file = _sm_project_path(cwd, declared.group(1)) if declared else ""
                    if (declared_file and any(f != declared_file for f in action_reads)
                            and not _SM_FAILURE_RE.match(request["output"])):
                        request = {"type": "write_files", "paths": [declared_file]}
                    else:
                        history.append("TOOL_RESULT " + json.dumps(
                            {"rejected": True, "error": evidence_error}, ensure_ascii=False))
                        continue
                else:
                    if _sm_validates(request["output"], validator):
                        return request["output"]
                    break
            if request["type"] == "read_files":
                for file in request["paths"]:
                    if not os.path.exists(file):
                        raise StateMachineHarnessError(
                            f"読み取り対象がありません: {os.path.relpath(file, cwd)}")
                    reads.add(file)
                    evidence.add(file)
                _sm_progress(f"read_files: {', '.join(os.path.relpath(f, cwd) for f in request['paths'])}")
                history.append("TOOL_RESULT " + json.dumps(
                    {"type": request["type"], "paths": request["paths"]}, ensure_ascii=False))
                continue
            if request["type"] == "write_files":
                before = {f: _sm_file_stamp(f) for f in request["paths"]}
                for file in request["paths"]:
                    os.makedirs(os.path.dirname(file), exist_ok=True)
                _sm_progress(f"write_files: {', '.join(os.path.relpath(f, cwd) for f in request['paths'])}")
                output = _sm_run_agent(
                    agent,
                    "Execute only this action now. The editable files are stale: replace them "
                    "from the assigned read-only inputs in this run. Do not merely describe or "
                    "return the existing content. After editing, return only the action Output "
                    f"Contract.\n\n{rendered}",
                    cwd=cwd, readonly=False,
                    read_files=[f for f in sorted(reads | set(action_reads))
                                if f not in request["paths"]],
                    files=request["paths"], log_file=log_file)
                missing = [f for f in request["paths"] if not os.path.exists(f)]
                changed = any(_sm_file_stamp(f) != before[f] for f in request["paths"])
                if missing or not changed:
                    error = (
                        "書き込み対象がありません: "
                        + ", ".join(os.path.relpath(f, cwd) for f in missing)
                        if missing else "write_files が対象ファイルを変更しませんでした")
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": error}, ensure_ascii=False))
                    continue
                for file in request["paths"]:
                    evidence.add(file)
                    touched.add(file)
                evidence_error = _sm_final_evidence_error(output, cwd, evidence, touched)
                if evidence_error:
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": evidence_error}, ensure_ascii=False))
                    continue
                contract = _sm_validated_output(output, validator)
                if contract:
                    return contract
                break
            _sm_progress(f"run: {request['command']} {' '.join(request['args'])}")
            tool = _sm_exec_argv(request["command"], request["args"], cwd=cwd,
                                 timeout_sec=request["timeout_sec"], log_file=log_file)
            for arg in request["args"]:
                try:
                    file = _sm_project_path(cwd, arg)
                except StateMachineHarnessError:
                    continue   # プロジェクト内ファイルでない引数
                if os.path.isfile(file):
                    evidence.add(file)
                    touched.add(file)
                    # ponytail: 大きな成果物の自動再投入はローカルモデルを詰まらせる。
                    # 必要なら Aider が read_files を要求する。
                    if os.stat(file).st_size <= _SM_MAX_AUTO_READ_BYTES:
                        reads.add(file)
            history.append("TOOL_RESULT " + json.dumps({
                "type": request["type"], "status": tool["status"], "error": tool["error"],
                "stdout": tool["stdout"][-4000:], "stderr": tool["stderr"][-2000:],
                "logFile": log_file,
            }, ensure_ascii=False))
    raise StateMachineHarnessError(f"ステート {state_id} が Output Contract を満たしませんでした")


def _sm_python_command() -> str:
    if os.environ.get("PYTHON"):
        return os.environ["PYTHON"]
    return sys.executable or ("python" if _sm_executable_on_path("python") else "python3")


def _sm_harness_script(script: str, args: "list[str]", *, cwd: str, log_file: str) -> str:
    result = _sm_exec_argv(_sm_python_command(), [script, *args], cwd=cwd,
                           timeout_sec=_SM_HARNESS_TIMEOUT_SEC, log_file=log_file)
    if result["status"] != 0 or result["error"]:
        raise StateMachineHarnessError(
            result["error"] or result["stderr"] or result["stdout"]
            or f"{os.path.basename(script)} が失敗しました")
    return result["stdout"].strip()


def _sm_first_line(text: str) -> str:
    return str(text or "").splitlines()[0] if str(text or "") else ""


def _sm_next_state(*, scripts: dict, workflow_path: str, state_id: str, output: str,
                   outputs: dict, agent: dict, cwd: str, log_file: str) -> str:
    args = [workflow_path, "--state", state_id, "--list-conditions",
            "--last-output", _sm_first_line(output)]
    for key, value in outputs.items():
        args += ["--output", f"{key}={_sm_first_line(str(value))}"]
    listed = _sm_parse_json_object(
        _sm_harness_script(scripts["next"], args, cwd=cwd, log_file=log_file))
    if not listed or not isinstance(listed.get("conditions"), list):
        raise StateMachineHarnessError("条件リストを解析できません")
    pending = [c for c in listed["conditions"] if c.get("needs_llm_eval") is True]
    evals: dict = {}
    if pending:
        raw = _sm_run_agent(
            agent,
            "Evaluate only these state-machine conditions against the completed action "
            "output. Return one JSON object mapping each index to true or false.\n"
            f"Output:\n{output}\nConditions:\n{json.dumps(pending, ensure_ascii=False)}",
            cwd=cwd, readonly=True, read_files=[workflow_path], files=[], log_file=log_file)
        evals = _sm_parse_json_object(raw)
        if not evals:
            raise StateMachineHarnessError("Aider の条件評価を JSON として読めません")
    decide = [workflow_path, "--state", state_id, "--evals", json.dumps(evals),
              "--last-output", _sm_first_line(output)]
    for key, value in outputs.items():
        decide += ["--output", f"{key}={_sm_first_line(str(value))}"]
    return _sm_harness_script(scripts["next"], decide, cwd=cwd, log_file=log_file)


def _sm_load_workflow_dict(workflow_file: str) -> dict:
    try:
        import yaml as _yaml  # type: ignore
    except ImportError as exc:
        raise StateMachineHarnessError(
            "PyYAML が必要です（pip install pyyaml）") from exc
    workflow = _yaml.safe_load(Path(workflow_file).read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not isinstance(workflow.get("states"), dict):
        raise StateMachineHarnessError("workflow.yaml を解析できません")
    return workflow


def _sm_resolve_agent(cli_name: str, model: str, cwd: str) -> dict:
    """agents/<name>.json 契約から headless 実行エージェントを解決する。"""
    mod = _import_agentcli()
    if mod is None:
        raise StateMachineHarnessError(
            "agentcore（agents/<name>.json 定義ローダ）を解決できません。"
            "install.sh の再実行を検討してください。")
    name = str(cli_name or "aider").strip() or "aider"
    try:
        spec = mod.load_cli(name, project_dir=cwd)
    except mod.AgentCliError as exc:
        raise StateMachineHarnessError(str(exc)) from exc
    return {"cli": name, "spec": spec,
            "model": str(model or "").strip() or None, "agentcli": mod}


def run_statemachine(*, workflow_path: str, cwd: str, parameters: "dict | None" = None,
                     agent: dict) -> dict:
    """ステートマシンを headless エージェントで完走させる。

    戻り値: {ok, stdout, stderr, finalState, logFile, files}（dashboard の旧 in-process
    実行器と同じ結果契約。stdout は最終ステートの出力）。
    """
    root = os.path.realpath(str(cwd))
    workflow_file = _sm_project_path(root, workflow_path)
    workflow = _sm_load_workflow_dict(workflow_file)
    harness_skill = _sm_resolve_skill("statemachine-use", root)
    if not harness_skill:
        raise StateMachineHarnessError("statemachine-use スキルの実体が見つかりません")
    scripts = {
        "dry": os.path.join(harness_skill["root"], "scripts", "run_machine.py"),
        "next": os.path.join(harness_skill["root"], "scripts", "next_state.py"),
    }
    log_dir = os.path.join(root, ".statemachine-use", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"agent-loop-{int(time.time() * 1000)}-{os.getpid()}.jsonl")
    touched: set = set()
    _sm_progress(f"workflow: {os.path.relpath(workflow_file, root)} (log: {log_file})")
    _sm_harness_script(scripts["dry"], [workflow_file, "--dry-run"], cwd=root, log_file=log_file)
    current = _sm_harness_script(scripts["next"], [workflow_file, "--initial-state"],
                                 cwd=root, log_file=log_file)
    context = _sm_initial_context(workflow, parameters)
    outputs: dict = {}
    config = workflow.get("config") if isinstance(workflow.get("config"), dict) else {}
    try:
        max_steps = max(1, int(float(config.get("max_steps"))))
    except (TypeError, ValueError):
        max_steps = 50
    last_output = ""

    for step in range(max_steps):
        state = workflow["states"].get(current)
        if not isinstance(state, dict):
            raise StateMachineHarnessError(f"ステートが見つかりません: {current}")
        if state.get("terminal") is True:
            _sm_append_log(log_file, {"event": "terminal", "state": current,
                                      "files": sorted(touched)})
            _sm_progress(f"terminal: {current}")
            return {**_sm_terminal_status(current, last_output),
                    "stdout": last_output, "stderr": "", "finalState": current,
                    "logFile": log_file, "files": sorted(touched)}
        _sm_append_log(log_file, {"event": "state", "state": current, "step": step + 1})
        _sm_progress(f"state: {current} (step {step + 1}/{max_steps})")
        last_output = _sm_execute_action(
            workflow_path=workflow_file, state_id=current, state=state, context=context,
            cwd=root, agent=agent, log_file=log_file, touched=touched).strip()
        context["last_output"] = last_output
        context["step_count"] = step + 1
        context["history"][current] = last_output
        output_key = _sm_scalar(state.get("output_key"))
        if output_key:
            context[output_key] = last_output
            outputs[output_key] = last_output
        nxt = _sm_next_state(scripts=scripts, workflow_path=workflow_file, state_id=current,
                             output=last_output, outputs=outputs, agent=agent, cwd=root,
                             log_file=log_file)
        if nxt == "NONE":
            on_none = _sm_scalar(config.get("on_no_transition")) or "error"
            if on_none == "stop":
                return {"ok": True, "stdout": last_output, "stderr": "",
                        "finalState": current, "logFile": log_file, "files": sorted(touched)}
            if on_none in workflow["states"]:
                nxt = on_none
            else:
                raise StateMachineHarnessError(f"ステート {current} から一致する遷移がありません")
        _sm_progress(f"transition: {current} -> {nxt}")
        current = nxt
    if _sm_scalar(config.get("on_max_steps")) == "stop":
        return {"ok": True, "stdout": last_output, "stderr": "", "finalState": current,
                "logFile": log_file, "files": sorted(touched)}
    raise StateMachineHarnessError(f"最大ステップ数 ({max_steps}) に到達しました")


def _sm_parse_params(pairs: "list[str]", input_value: "str | None") -> dict:
    params: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise StateMachineHarnessError(f"--param は KEY=VALUE 形式で指定してください: {pair}")
        key, _, value = pair.partition("=")
        if not key.strip():
            raise StateMachineHarnessError(f"--param のキーが空です: {pair}")
        params[key.strip()] = value
    if input_value is not None:
        params["input"] = input_value
    return params


def cmd_statemachine(args: argparse.Namespace, cwd: Path) -> None:
    """statemachine サブコマンド: aider 等の headless CLI でステートマシンを完走させる。"""
    work_dir = Path(getattr(args, "dir", None) or cwd).expanduser().resolve()
    if not work_dir.is_dir():
        print(f"[agent-loop] ERROR: ディレクトリが存在しません: {work_dir}", file=sys.stderr)
        sys.exit(1)
    hold = bool(getattr(args, "hold", False))
    code = 0
    try:
        params = _sm_parse_params(getattr(args, "param", None) or [],
                                  getattr(args, "input", None))
        agent = _sm_resolve_agent(getattr(args, "agent_cli", None) or "aider",
                                  getattr(args, "model", None) or "", str(work_dir))
        _sm_progress(f"agent: {agent['cli']}"
                     + (f" / model: {agent['model']}" if agent["model"] else " (default model)"))
        result = run_statemachine(workflow_path=args.workflow, cwd=str(work_dir),
                                  parameters=params, agent=agent)
        print("RESULT " + json.dumps(result, ensure_ascii=False))
        code = 0 if result.get("ok") else 1
    except StateMachineHarnessError as exc:
        print("RESULT " + json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
        code = 1
    if hold:
        # tmux ウィンドウ実行用: 結果を残したままウィンドウを保持する（Enter で閉じる）。
        print("[agent-loop] Enter でこのウィンドウを閉じます", flush=True)
        try:
            input()
        except EOFError:
            pass
    sys.exit(code)
