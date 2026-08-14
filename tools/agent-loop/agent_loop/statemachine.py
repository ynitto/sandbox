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
#
# 限定ツール契約そのもの（パス検証・コマンド検証・JSON パース・証跡・agent 呼び出し）は
# ゴールに依存しないので toolloop.py へ切り出した。ここに残るのはステートマシン固有の
# 遷移・出力契約・テンプレート展開だけ。以下の別名は移設前の呼び名を保つためのもので、
# 実体は 1 つ（C7）。
StateMachineHarnessError = ToolLoopError
_SM_MAX_TOOL_ROUNDS = _TL_MAX_TOOL_ROUNDS
_SM_MAX_TOOL_TIMEOUT_SEC = _TL_MAX_TOOL_TIMEOUT_SEC
_SM_MAX_AUTO_READ_BYTES = _TL_MAX_AUTO_READ_BYTES
_SM_HARNESS_TIMEOUT_SEC = _TL_HARNESS_TIMEOUT_SEC
_SM_FAILURE_RE = _TL_FAILURE_RE
_sm_inside = _tl_inside
_sm_project_path = _tl_project_path
_sm_resolve_skill = _tl_resolve_skill
_sm_action_skill_names = _tl_action_skill_names
_sm_action_project_files = _tl_action_project_files
_sm_skill_scripts = _tl_skill_scripts
_sm_skill_declared_scripts = _tl_skill_declared_scripts
_sm_run_control = _tl_run_control
_sm_validate_tool_request = _tl_validate_tool_request
_sm_parse_json_object = _tl_parse_json_object
_sm_parse_tool_request = _tl_parse_tool_request
_sm_append_log = _tl_append_log
_sm_progress = _tl_progress
_sm_exec_argv = _tl_exec_argv
_sm_run_agent = _tl_run_agent
_sm_final_evidence_error = _tl_final_evidence_error
_sm_file_stamp = _tl_file_stamp
_sm_python_ok = _tl_python_ok
_sm_python_command = _tl_python_command
_sm_resolve_agent = _tl_resolve_agent

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
    # 定型業務のアクションは日付を要求することが多い（日報・ダイジェスト）。モデルに
    # 「今日」を推測させると学習時点の日付を書くので、実行時の値を組み込みで渡す。
    now = _dt.datetime.now().astimezone()
    context: dict = {**declared, "context": declared, "input": "",
                     "history": {}, "last_output": "", "step_count": 0,
                     "today": now.strftime("%Y-%m-%d"),
                     "now": now.isoformat(timespec="seconds")}
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
        + ("A run TOOL_RESULT with status 0 already completed; do not repeat the same command. "
           "Run another command only if Current action explicitly requires it. Request "
           "read_files only if inspection is still required.\n"
           if _tl_history_has_run(history) else
           "If the action names a command, run it and use its TOOL_RESULT as the only source "
           "of facts. Never report results you have not seen in a TOOL_RESULT.\n")
        + ("If the action asks to create, save, or edit a file without naming a command, "
           "request write_files. Never run a skill script unless Current action explicitly "
           "names that script.\n")
        + ("For run.args, put every CLI token in its own JSON string. Never combine a flag and "
          "value or add flags not requested by the action.\n"
          "Return exactly one JSON object and no markdown. Put type at the top level; "
          "never use a tool name as an object key. Run shape:\n"
          '{"type":"run","command":"executable","args":["arg"],"timeout_sec":60}\n'
          "Required fields by type:\n"
          "- read_files: type, paths (existing concrete paths from Current action only)\n"
          "- write_files: type, paths (concrete output paths from Current action only)\n"
          "- run: type, command, args, timeout_sec\n"
          "- final: type, output (the exact action Output Contract)"))


def _sm_max_attempts(state: dict) -> int:
    try:
        retries = int(float(state.get("max_retries")))
    except (TypeError, ValueError):
        retries = 0
    return max(1, retries + 1)


def _sm_check_only_transitions(workflow: dict, state_id: str) -> bool:
    """state からの遷移がすべて check_* キーだけを材料にしているか。

    1 つでも last_output / output_key を読む遷移があれば False——そこでは
    モデルの書いた本文が遷移材料なので、機械が本文を合成してはいけない。
    """
    found = False
    for transition in workflow.get("transitions", []):
        if not isinstance(transition, dict) or transition.get("from") != state_id:
            continue
        found = True
        rule = str(transition.get("condition_rule") or "").strip()
        for clause in filter(None, (part.strip() for part in rule.split(";"))):
            parts = clause.split(":")
            if len(parts) < 3 or not parts[1].strip().startswith("check_"):
                return False
    return found


def _sm_write_success_output(*, workflow_path: str, state_id: str, state: dict,
                             validator, files: set, cwd: str) -> str:
    """一意な成功遷移がある単一ファイル書込の結果を機械的に作る。"""
    rule = str(validator or "")
    if not rule.startswith("startswith:") or len(files) != 1:
        return ""
    prefixes = [value.strip() for value in rule[len("startswith:"):].split(",")]
    workflow = _sm_load_workflow_dict(workflow_path)
    file = next(iter(files))
    # 検査（check）だけを材料に遷移するステートでは、書式契約は check へ進む口でしか
    # ない——成否を語るのはハーネスが実行する検査の exit code。編集層は黙って直すのが
    # 普通なので、契約の欠けた完了済み書込をここで落とすと check まで到達しない。
    # 合成する本文は先頭の宣言接頭辞（遷移はどの接頭辞でも読まない）。
    if state.get("check") and _sm_check_only_transitions(workflow, state_id):
        return f"{prefixes[0]}\npath: {os.path.relpath(file, cwd)}"
    keys = {"last_output"}
    if _sm_scalar(state.get("output_key")):
        keys.add(_sm_scalar(state.get("output_key")))
    successful = set()
    for transition in workflow.get("transitions", []):
        if not isinstance(transition, dict) or transition.get("from") != state_id:
            continue
        if not _sm_terminal_status(transition.get("to"), "")["ok"]:
            continue
        clauses = {part.strip() for part in str(transition.get("condition_rule") or "").split(";")}
        successful.update(prefix for prefix in prefixes
                          if any(f"startswith:{key}:{prefix}" in clauses for key in keys))
    if len(successful) != 1:
        return ""  # 分類など複数の正常遷移はモデルの判断を捏造しない。
    return f"{successful.pop()}\npath: {os.path.relpath(file, cwd)}"


def _sm_execute_action(*, workflow_path: str, state_id: str, state: dict, context: dict,
                       cwd: str, agent: dict, log_file: str, touched: set,
                       check_note: str = "", retry_paths: "list[str] | None" = None) -> str:
    action = _sm_workflow_action(workflow_path, state_id, state)
    rendered = _sm_render_template(action["text"], context)
    if check_note:
        # 検査が落ちた後の再投入。測った不一致を課題文へ足す（受入は真偽だけでも変わらないが、
        # 実測では再試行が 28% 速い）。足す先は課題文そのもの——planner 周と編集周の両方が読む。
        rendered = f"{rendered}\n\n{check_note}"
    skills = [s for s in (_sm_resolve_skill(n, cwd) for n in _sm_action_skill_names(rendered)) if s]
    skill_scripts = {
        os.path.realpath(script) for skill in skills for script in _sm_skill_scripts(skill)
    }
    # 実行してよいスキルスクリプト = アクションが名指ししたもの ∪ そのスキルの SKILL.md が
    # 入口として載せているもの。scripts/ にあるだけの下請けは呼ばせない。
    named_skill_scripts = {
        script for script in skill_scripts if os.path.basename(script) in rendered
    } | {
        os.path.realpath(script) for skill in skills
        for script in _sm_skill_declared_scripts(skill)
    }
    action_reads = _sm_action_project_files(rendered, cwd)
    reads: set = {f for f in [workflow_path, action["file"], *(s["skill_file"] for s in skills)] if f}
    validator = state.get("output_validator")
    max_attempts = _sm_max_attempts(state)
    successful_runs: set = set()
    provided_reads: set = set()

    # 編集対象の割付。優先は (1) 検査の再投入なら前の試行が書いたファイル（契約の path 行）、
    # (2) state の `write:` 宣言（定型の事前分解はファイル割付まで決めてある——worker_eval の
    # steps[].files と同じ考え方で、割付は制御席のモデルに訊く仕事ではない）。
    declared = state.get("write")
    declared = [declared] if isinstance(declared, str) else list(declared or [])
    seed_paths = [str(p) for p in ((retry_paths if check_note and retry_paths else None)
                                   or declared) if str(p).strip()]
    seeded = False
    for attempt in range(max_attempts):
        history: list[str] = []
        evidence: set = set()
        for _round in range(_SM_MAX_TOOL_ROUNDS):
            request = None
            if seed_paths and not seeded and not attempt:
                # 編集の周から入る（制御周スキップ）。制御席へ「次の一手」を訊くと、
                # 小型モデルは pytest 実行や pip install の調査ループで周を使い切る
                # （実機再測 2026-08-15 の失敗機序）。割付が宣言済みなら訊く必要がない。
                seeded = True
                try:
                    request = {"type": "write_files",
                               "paths": [_sm_project_path(cwd, p) for p in seed_paths]}
                except StateMachineHarnessError:
                    request = None
            if request is None:
                retry = (f"Retry {attempt}/{max_attempts - 1}: the previous output violated "
                         "the Output Contract." if attempt else "")
                # 制御応答（次の一手の JSON）は編集能力の要らない周。定義が申告していれば
                # JSON 用の変種へ振り替える（run_goal と同じ口を使う——C7）。
                raw = _sm_run_control(_tl_control_agent(agent, cwd), _sm_planner_prompt(
                    action=rendered, cwd=cwd, skills=skills, reads=sorted(reads),
                    history=history, retry=retry,
                ), cwd=cwd, read_files=sorted(reads), log_file=log_file)
                try:
                    parsed = _sm_parse_tool_request(raw)
                except StateMachineHarnessError as exc:
                    # ツール要求ですらない = 素の本文。Output Contract を満たすならそれが答え。
                    contract = _sm_validated_output(raw, validator)
                    if contract and not _sm_final_evidence_error(raw, cwd, evidence, touched):
                        return contract
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": str(exc)}, ensure_ascii=False))
                    continue
                try:
                    request = _sm_validate_tool_request(parsed, cwd, skills)
                except StateMachineHarnessError as exc:
                    # 拒否されたツール要求は「やらなかった」であって成功ではない。JSON の中身が
                    # たまたま Output Contract の形をしていても、契約文として拾わない。
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": str(exc)}, ensure_ascii=False))
                    continue
            if request["type"] == "run":
                script = os.path.realpath(request["command"])
                if request["args"] and os.path.realpath(request["args"][0]) in skill_scripts:
                    script = os.path.realpath(request["args"][0])
                if script in skill_scripts and script not in named_skill_scripts:
                    history.append("TOOL_RESULT " + json.dumps({
                        "rejected": True,
                        "error": "Current action にないスキルスクリプトは実行できません: "
                                 + os.path.basename(script),
                    }, ensure_ascii=False))
                    continue
                executable = _tl_executable_on_path(request["command"]) or request["command"]
                run_key = (os.path.realpath(executable), tuple(
                    os.path.realpath(os.path.join(cwd, arg))
                    if os.path.isabs(arg) or "/" in arg or "\\" in arg else arg
                    for arg in request["args"]
                ))
                if run_key in successful_runs:
                    history.append("TOOL_RESULT " + json.dumps({
                        "rejected": True,
                        "error": "同じ run は既に成功しています。再実行せず final を返してください",
                    }, ensure_ascii=False))
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
                existing = [file for file in request["paths"] if os.path.exists(file)]
                reads.update(existing)
                evidence.update(existing)
                missing = [file for file in request["paths"] if file not in existing]
                files = []
                for file in existing:
                    item = {"path": file, "size": os.stat(file).st_size}
                    if file in provided_reads:
                        item["already_provided"] = True
                    elif item["size"] <= _SM_MAX_AUTO_READ_BYTES:
                        with open(file, "r", encoding="utf-8", errors="replace") as source:
                            item["content"] = source.read()
                        provided_reads.add(file)
                    else:
                        item["content_omitted"] = (
                            f"{item['size']} bytes exceeds {_SM_MAX_AUTO_READ_BYTES} byte limit")
                    files.append(item)
                result = {"type": request["type"], "files": files}
                if missing:
                    result.update({"rejected": True, "error": "読み取り対象がありません: "
                                   + ", ".join(os.path.relpath(file, cwd)
                                               for file in missing)})
                    history.append("TOOL_RESULT " + json.dumps(result, ensure_ascii=False))
                    continue
                _sm_progress(f"read_files: {', '.join(os.path.relpath(f, cwd) for f in request['paths'])}")
                history.append("TOOL_RESULT " + json.dumps(result, ensure_ascii=False))
                continue
            if request["type"] == "write_files":
                for file in request["paths"]:
                    os.makedirs(os.path.dirname(file), exist_ok=True)
                _sm_progress(f"write_files: {', '.join(os.path.relpath(f, cwd) for f in request['paths'])}")
                backups: dict[str, str] = {}
                try:
                    # Existing generated content can make a small model claim it is already done.
                    # Keep it recoverable, but give the replacement editor an empty target.
                    for file in request["paths"]:
                        if os.path.isfile(file):
                            backup = f"{file}.agent-loop-{uuid.uuid4().hex}.bak"
                            os.replace(file, backup)
                            backups[file] = backup
                            Path(file).touch()
                    staged = {f: _sm_file_stamp(f) for f in request["paths"]}
                    output = _tl_run_agent(
                        agent,
                        "Execute only this action now. The editable files are stale: replace them "
                        "from the assigned read-only inputs in this run. Do not merely describe or "
                        "return the existing content. After editing, return only the action Output "
                        f"Contract.\n\n{rendered}",
                        cwd=cwd, readonly=False,
                        read_files=[f for f in sorted(reads | set(action_reads))
                                    if f not in request["paths"]],
                        files=request["paths"], log_file=log_file, allow_empty=True)
                except BaseException:
                    for file, backup in backups.items():
                        os.replace(backup, file)
                    raise
                missing = [f for f in request["paths"] if not os.path.exists(f)]
                changed = any(_sm_file_stamp(f) != staged[f] for f in request["paths"])
                if missing or not changed:
                    for file, backup in backups.items():
                        os.replace(backup, file)
                    error = (
                        "書き込み対象がありません: "
                        + ", ".join(os.path.relpath(f, cwd) for f in missing)
                        if missing else "write_files が対象ファイルを変更しませんでした")
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": error}, ensure_ascii=False))
                    continue
                candidates = set(request["paths"])
                evidence_error = _sm_final_evidence_error(
                    output, cwd, evidence | candidates, touched | candidates)
                if evidence_error:
                    for file, backup in backups.items():
                        os.replace(backup, file)
                    history.append("TOOL_RESULT " + json.dumps(
                        {"rejected": True, "error": evidence_error}, ensure_ascii=False))
                    continue
                contract = _sm_validated_output(output, validator)
                if contract:
                    for backup in backups.values():
                        try:
                            os.unlink(backup)
                        except OSError:
                            pass
                    evidence.update(candidates)
                    touched.update(candidates)
                    _sm_append_log(log_file, {
                        "event": "write_completed", "state": state_id,
                        "paths": sorted(candidates), "contractSource": "editor",
                    })
                    return contract
                machine_contract = _sm_write_success_output(
                    workflow_path=workflow_path, state_id=state_id, state=state,
                    validator=validator, files=candidates, cwd=cwd)
                if machine_contract:
                    for backup in backups.values():
                        try:
                            os.unlink(backup)
                        except OSError:
                            pass
                    evidence.update(candidates)
                    touched.update(candidates)
                    _sm_append_log(log_file, {
                        "event": "write_completed", "state": state_id,
                        "paths": sorted(candidates), "contractSource": "machine",
                    })
                    return machine_contract
                for file, backup in backups.items():
                    os.replace(backup, file)
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
            succeeded = tool["status"] == 0 and not tool["error"]
            history.append("TOOL_RESULT " + json.dumps({
                # ok は status 由来。stdout が空でも成功は成功——出力の有無で成否を
                # 推測させると、何も印字しないコマンドの後にモデルが延々やり直す。
                "type": request["type"], "ok": succeeded,
                "status": tool["status"], "error": tool["error"],
                "stdout": tool["stdout"][-4000:], "stderr": tool["stderr"][-2000:],
                "logFile": log_file,
            }, ensure_ascii=False))
            if succeeded:
                successful_runs.add(run_key)
    raise StateMachineHarnessError(f"ステート {state_id} が Output Contract を満たしませんでした")


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


# 検査の診断を課題文へ戻すときの上限。ローカルモデルの文脈は黙って切り捨てられるので、
# 長い pytest 出力をそのまま積むと system prompt 側から落ちる。
_SM_CHECK_OUTPUT_LIMIT = 2000


def _sm_state_check_spec(*, scripts: dict, workflow_path: str, state_id: str,
                         cwd: str, log_file: str) -> dict:
    """ステートが宣言する決定的検査を statemachine-use から取得する。

    正規化（文字列の分割・既定値・不正宣言の拒否）はスキル側の 1 実装が持つ。ここで
    YAML を読み直すと、定義側が形を足した日に黙ってずれる（argv の写しで前科がある）。
    """
    spec = _sm_parse_json_object(_sm_harness_script(
        scripts["next"], [workflow_path, "--state", state_id, "--state-check"],
        cwd=cwd, log_file=log_file))
    if not spec:
        raise StateMachineHarnessError(f"ステート {state_id} の検査宣言を解析できません")
    try:
        retries = max(0, int(spec.get("check_retries") or 0))
    except (TypeError, ValueError):
        retries = 0
    return {"check": spec.get("check") if isinstance(spec.get("check"), dict) else None,
            "retries": retries,
            "on_exhausted": _sm_scalar(spec.get("check_on_exhausted")) or "escalate",
            "feedback": spec.get("check_feedback") is not False}


def _sm_check_context(status, stdout: str, stderr: str, error: str) -> dict:
    """検査結果 → condition_rule のコンテキスト値。

    キー名と書式の正典は statemachine-use の `engine.check_context`
    （references/schema.md「検査結果のコンテキスト変数」）。ハーネスは自分で測った値だけを
    遷移材料に入れる——モデルが書いたテキストはここへ 1 バイトも混ぜない。
    """
    ok = status == 0 and not error
    detail = error or (str(stderr or "").strip() or str(stdout or "").strip())
    first = detail.splitlines()[0].strip() if detail.strip() else ""
    return {"check_status": "error" if status is None else str(status),
            "check_ok": "true" if ok else "false",
            "check_output": first}


def _sm_run_check(check: dict, *, state_id: str, cwd: str, log_file: str) -> dict:
    """宣言された検査コマンドを実行する。シェルは介さない（argv を直接渡す）。"""
    argv = [check["command"], *check["args"]]
    _sm_progress(f"check: {' '.join(argv)}")
    result = _sm_exec_argv(check["command"], check["args"], cwd=cwd,
                           timeout_sec=check["timeout_sec"], log_file=log_file)
    context = _sm_check_context(result["status"], result["stdout"], result["stderr"],
                                result["error"])
    _sm_append_log(log_file, {"event": "check", "state": state_id, "argv": argv, **context})
    return {**result, "ok": context["check_ok"] == "true", "argv": argv, "context": context}


def _sm_check_note(result: dict, attempt: int, attempts: int, *, feedback: bool) -> str:
    """検査が落ちた後の再投入で課題文へ足す文。検査そのものは書き換えさせない。"""
    head = (f"Retry {attempt}/{attempts - 1}: the declared check failed "
            f"(exit status {result['context']['check_status']}).")
    tail = "Fix the work so this exact command succeeds. Do not modify the check itself."
    if not feedback:
        return f"{head}\n{tail}"
    detail = "\n".join(x for x in (result["error"], result["stderr"], result["stdout"])
                       if x).strip()
    return (f"{head}\nCheck command: {' '.join(result['argv'])}\n"
            + (f"Check output:\n{detail[-_SM_CHECK_OUTPUT_LIMIT:]}\n" if detail else "")
            + tail)


def _sm_next_state(*, scripts: dict, workflow_path: str, state_id: str, output: str,
                   outputs: dict, agent: dict, cwd: str, log_file: str,
                   extra: "dict | None" = None) -> str:
    context = {"last_output": _sm_first_line(output),
               **{key: _sm_first_line(str(value)) for key, value in outputs.items()},
               **(extra or {})}
    base = [workflow_path, "--state", state_id,
            "--context", json.dumps(context, ensure_ascii=False)]
    listed = _sm_parse_json_object(
        _sm_harness_script(scripts["next"], [*base, "--auto-eval"], cwd=cwd, log_file=log_file))
    if not listed:
        raise StateMachineHarnessError("条件リストを解析できません")
    # 決定済みの応答は 2 形。auto_advance（条件が無く next_state だけ返る）と
    # resolved（condition_rule だけで確定）。どちらも LLM 評価も --eval も要らない。
    decided = listed.get("next_state") or listed.get("resolved")
    if isinstance(decided, str) and decided:
        return decided
    if not isinstance(listed.get("conditions"), list):
        raise StateMachineHarnessError("条件リストを解析できません")
    pending = [c for c in listed["conditions"] if c.get("needs_llm_eval") is True]
    evals: dict = {}
    if pending:
        raw = _sm_run_control(
            _tl_control_agent(agent, cwd),
            "Evaluate only these state-machine conditions against the completed action "
            "output. Return one JSON object mapping each index to true or false.\n"
            f"Output:\n{output}\nConditions:\n{json.dumps(pending, ensure_ascii=False)}",
            cwd=cwd, read_files=[workflow_path], log_file=log_file)
        evals = _sm_parse_json_object(raw)
        if not evals:
            raise StateMachineHarnessError("Aider の条件評価を JSON として読めません")
    return _sm_harness_script(scripts["next"], [*base, "--evals", json.dumps(evals)],
                              cwd=cwd, log_file=log_file)


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
        # 宣言された検査を通してからでないと次へ進めない。落ちたら同じステートをやり直す
        # ——遷移の材料をモデルの自己申告からハーネスの実測へ移すのがこの段の目的。
        gate = _sm_state_check_spec(scripts=scripts, workflow_path=workflow_file,
                                    state_id=current, cwd=root, log_file=log_file)
        attempts = gate["retries"] + 1 if gate["check"] else 1
        checked: "dict | None" = None
        note = ""
        retry_paths: list = []
        for attempt in range(attempts):
            try:
                last_output = _sm_execute_action(
                    workflow_path=workflow_file, state_id=current, state=state, context=context,
                    cwd=root, agent=agent, log_file=log_file, touched=touched,
                    check_note=note, retry_paths=retry_paths).strip()
            except StateMachineHarnessError:
                if not attempt or not gate["check"]:
                    raise
                # 検査の再投入がアクションを完走できなかった＝この段では直せない。run 全体の
                # 失敗ではなく上限到達として数え、escalate の口（下の check_exhausted）へ流す。
                _sm_append_log(log_file, {"event": "retry_action_failed", "state": current,
                                          "attempt": attempt + 1})
                continue
            if not gate["check"]:
                break
            checked = _sm_run_check(gate["check"], state_id=current, cwd=root,
                                    log_file=log_file)
            if checked["ok"] or attempt == attempts - 1:
                break
            note = _sm_check_note(checked, attempt + 1, attempts, feedback=gate["feedback"])
            # 再投入は前の試行が書いたファイルへの編集から入る（成果の所在は契約の path 行）。
            retry_paths = re.findall(r"^path:\s*(.+?)\s*$", last_output, re.I | re.M)
        context["last_output"] = last_output
        context["step_count"] = step + 1
        context["history"][current] = last_output
        output_key = _sm_scalar(state.get("output_key"))
        if output_key:
            context[output_key] = last_output
            outputs[output_key] = last_output
        checks = checked["context"] if checked else {}
        context.update(checks)
        if checked and not checked["ok"] and gate["on_exhausted"] != "continue":
            # 上限到達は「この段では解けない」の宣告。実測ではこの型の失敗は同形で揺れないので、
            # 引き直しても埋まらない——上位の段へ回すシグナルとして失敗一般と区別する。
            escalate = gate["on_exhausted"] == "escalate"
            reason = (f"ステート {current} の検査が {attempts} 回とも失敗しました: "
                      + (checks["check_output"] or f"status={checks['check_status']}"))
            _sm_append_log(log_file, {"event": "check_exhausted", "state": current,
                                      "attempts": attempts, "escalate": escalate, **checks})
            _sm_progress(f"check exhausted: {current}"
                         + (" -> escalate（上位の段へ）" if escalate else ""))
            if not escalate:
                raise StateMachineHarnessError(reason)
            return {"ok": False, "escalate": True, "error": reason, "stdout": last_output,
                    "stderr": str(checked["stderr"] or checked["stdout"] or "")
                    [-_SM_CHECK_OUTPUT_LIMIT:],
                    "finalState": current, "logFile": log_file, "files": sorted(touched),
                    "check": {"state": current, "attempts": attempts,
                              "argv": checked["argv"], **checks}}
        nxt = _sm_next_state(scripts=scripts, workflow_path=workflow_file, state_id=current,
                             output=last_output, outputs=outputs, agent=agent, cwd=root,
                             log_file=log_file, extra=checks)
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
        # 3 = 検査の再投入上限に達した（この段では解けない）。呼び出し側が RESULT を
        # 読まずに終了コードだけで昇格へ振り分けられるようにする。非 0 を失敗として
        # 扱う既存の呼び出し側とは互換のまま。
        if result.get("escalate"):
            sys.exit(3)
        sys.exit(0 if result.get("ok") else 1)
    except StateMachineHarnessError as exc:
        print("RESULT " + json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(f"[agent-loop] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
