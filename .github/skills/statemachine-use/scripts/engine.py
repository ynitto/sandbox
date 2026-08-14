"""
yaml-statemachine engine.py
LLM駆動 YAML ワークフロー向けのコア非同期ステートマシンエンジン。
"""

from __future__ import annotations

import re
import json
import shlex
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

import yaml


# ─────────────────────────────────────────────
#  データクラス（YAMLからパース）
# ─────────────────────────────────────────────

@dataclass
class StateConfig:
    id: str
    description: str = ""
    action: str = ""
    terminal: bool = False
    on_enter: str = ""
    on_exit: str = ""
    output_key: str = ""
    max_retries: int = 0
    output_validator: str = ""   # "startswith:VAL1,VAL2" — 第1行の形式を検証
    # 決定的検査（ハーネスが実行する。モデルは触れない）。宣言が無ければ従来どおり素通り。
    check: "dict | None" = None          # {command, args, timeout_sec} に正規化済み
    check_retries: int = 0               # 検査が落ちたときに同じステートをやり直す回数
    check_on_exhausted: str = "escalate"  # "escalate" | "continue" | "error"
    check_feedback: bool = True          # 再投入時に検査の出力を課題文へ足すか
    check_error: str = ""                # 宣言が壊れている場合の理由（validate が報告する）


@dataclass
class TransitionConfig:
    from_state: str   # "*" = wildcard
    to_state: str
    condition: str
    condition_rule: str = ""  # 決定論的評価ルール（LLM評価より優先）
    priority: int = 0
    description: str = ""


@dataclass
class MachineConfig:
    max_steps: int = 50
    on_max_steps: str = "error"      # "error" | "stop" | state_id
    on_no_transition: str = "error"  # "error" | "stop" | state_id
    verbose: bool = False


@dataclass
class WorkflowDefinition:
    name: str
    initial_state: str
    states: dict[str, StateConfig]
    transitions: list[TransitionConfig]
    initial_context: dict[str, Any] = field(default_factory=dict)
    config: MachineConfig = field(default_factory=MachineConfig)
    description: str = ""


# ─────────────────────────────────────────────
#  決定的検査（check）
# ─────────────────────────────────────────────
#
#  遷移の材料を「モデルが書いたテキスト」から「ハーネスが測った事実」へ移すための口。
#  検査コマンドは workflow.yaml が宣言し、ハーネスが実行する——モデルは中身も結果も
#  書き換えられない。実測（tools/agent-tools/eval/results/archive/）では、決定的な検知と
#  再投入だけで受入が 0/9 → 3/3 になり、検知を伴わない分解は逆に下がった。

CHECK_ON_EXHAUSTED = ("escalate", "continue", "error")
CHECK_DEFAULT_TIMEOUT_SEC = 120
CHECK_OUTPUT_LIMIT = 2000

# 検査結果がコンテキストへ入れるキー。condition_rule はここだけを見れば遷移を決められる。
# agent-loop 側のハーネス（tools/agent-loop/agent_loop/statemachine.py）も同じ名前で入れる。
CHECK_CONTEXT_KEYS = ("check_status", "check_ok", "check_output")

# シェルを介さない（argv 直接実行）ので、メタ文字は解釈されずリテラルの引数になる。
# 黙って別物を実行するより、宣言の時点で落とす。
_CHECK_SHELL_CHARS = ("|", "&", ";", ">", "<", "`", "$(", "\n")


def normalize_check(value: Any) -> "dict | None":
    """state の `check` 宣言を {command, args, timeout_sec} へ正規化する。

    受ける形は 3 つ。宣言が無ければ None（ゲート無し = 従来動作）。

      check: "python3 -m pytest tests/test_x.py -q"     文字列（shlex で分割）
      check: ["python3", "-m", "pytest", "tests/"]      配列（分割済み）
      check: {command: python3, args: [...], timeout_sec: 300}

    不正な宣言は ValueError を送出する（validate_workflow が理由を集めて報告する）。
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        for token in _CHECK_SHELL_CHARS:
            if token in value:
                raise ValueError(
                    f"check にシェル記号 '{token}' は使えません（argv を直接実行するため"
                    "解釈されません）。パイプやリダイレクトが要るならスクリプトにしてください")
        parts = shlex.split(value)
        if not parts:
            raise ValueError("check が空です")
        return {"command": parts[0], "args": parts[1:],
                "timeout_sec": CHECK_DEFAULT_TIMEOUT_SEC}
    if isinstance(value, list):
        parts = [str(v) for v in value if str(v) != ""]
        if not parts:
            raise ValueError("check が空です")
        return {"command": parts[0], "args": parts[1:],
                "timeout_sec": CHECK_DEFAULT_TIMEOUT_SEC}
    if isinstance(value, dict):
        command = str(value.get("command") or "").strip()
        if not command:
            raise ValueError("check.command は必須です")
        args = value.get("args", [])
        if not isinstance(args, list):
            raise ValueError("check.args はリストで指定してください")
        raw_timeout = value.get("timeout_sec", CHECK_DEFAULT_TIMEOUT_SEC)
        try:
            timeout = int(float(raw_timeout))
        except (TypeError, ValueError):
            raise ValueError(f"check.timeout_sec は数値で指定してください: {raw_timeout!r}")
        return {"command": command, "args": [str(a) for a in args],
                "timeout_sec": max(1, timeout)}
    raise ValueError("check は文字列・配列・オブジェクトのいずれかで指定してください")


def check_context(status: "int | None", stdout: str = "", stderr: str = "",
                  error: str = "") -> dict:
    """検査結果を condition_rule のコンテキスト値へ変換する（キーの契約はここが正典）。

    - `check_status`: 終了コードの文字列。実行できなかった場合（タイムアウト・
      コマンド不在）は "error" — 0 でないので「落ちた」側へ倒れる。
    - `check_ok`: "true" / "false"。`equals:check_ok:true` で書ける短縮形。
    - `check_output`: 診断の先頭行（stderr 優先）。人にもモデルにも同じものを見せる。
    """
    ok = status == 0 and not error
    detail = error or (stderr.strip() or stdout.strip())
    first_line = detail.splitlines()[0].strip() if detail.strip() else ""
    return {
        "check_status": "error" if status is None else str(status),
        "check_ok": "true" if ok else "false",
        "check_output": first_line,
    }


def check_feedback_note(check: dict, result: dict, attempt: int, attempts: int) -> str:
    """再投入時に課題文へ足す診断。実測では受入は同じで再試行が 28% 速くなる。"""
    argv = " ".join([check["command"], *check["args"]])
    detail = "\n".join(x for x in (result.get("error", ""), result.get("stderr", ""),
                                   result.get("stdout", "")) if x).strip()
    return (
        f"Retry {attempt}/{attempts - 1}: the declared check failed "
        f"(exit status {result['context']['check_status']}).\n"
        f"Check command: {argv}\n"
        + (f"Check output:\n{detail[-CHECK_OUTPUT_LIMIT:]}\n" if detail else "")
        + "Fix the work so this exact command succeeds. Do not modify the check itself."
    )


def run_check(check: dict, *, cwd: "str | Path | None" = None) -> dict:
    """検査コマンドを実行して結果を返す。シェルは介さない（argv を直接実行）。

    戻り値: {ok, status, stdout, stderr, error, argv, context}
    """
    argv = [check["command"], *check["args"]]
    status: "int | None" = None
    stdout = stderr = error = ""
    try:
        proc = subprocess.run(argv, cwd=str(cwd or Path.cwd()), capture_output=True,
                              text=True, errors="replace",
                              timeout=max(1, int(check["timeout_sec"])))
        status, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        error = f"検査コマンドがタイムアウトしました ({check['timeout_sec']}s)"
    except OSError as exc:
        error = f"検査コマンドを実行できません: {exc}"
    context = check_context(status, stdout, stderr, error)
    return {"ok": context["check_ok"] == "true", "status": status, "stdout": stdout,
            "stderr": stderr, "error": error, "argv": argv, "context": context}


# ─────────────────────────────────────────────
#  YAML パーサー
# ─────────────────────────────────────────────

def resolve_workflow_path(path_or_name: str | Path) -> Path:
    """名前またはパスから workflow.yaml を解決する。"""
    p = Path(path_or_name)
    if p.suffix in (".yaml", ".yml"):
        return p
    # .statemachine/{name}/workflow.yaml を試みる
    candidate = Path(".statemachine") / p / "workflow.yaml"
    if candidate.exists():
        return candidate
    # ディレクトリとして扱う
    return p / "workflow.yaml"


def _load_text_from_file(value: str, base_dir: Path) -> str:
    """file: プレフィックスまたはファイルパスからテキストを読み込む。"""
    if value.startswith("file:"):
        file_path = base_dir / value[5:].strip()
        return file_path.read_text(encoding="utf-8")
    return value


def load_workflow(path: str | Path) -> WorkflowDefinition:
    """YAML ワークフローファイルを WorkflowDefinition にパースする。
    名前を渡した場合は .statemachine/{name}/workflow.yaml を解決する。"""
    path = resolve_workflow_path(path)
    base_dir = path.parent

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # ステート
    states: dict[str, StateConfig] = {}
    for state_id, sdef in data.get("states", {}).items():
        # action の解決: action_file > file: prefix > インライン > 自動探索
        action = sdef.get("action", "")
        action_file = sdef.get("action_file", "")
        if action_file:
            action = (base_dir / action_file).read_text(encoding="utf-8")
        elif action.startswith("file:"):
            action = _load_text_from_file(action, base_dir)
        elif not action:
            auto = base_dir / "actions" / f"{state_id}.md"
            if auto.exists():
                action = auto.read_text(encoding="utf-8")

        # 決定的検査。宣言が壊れていても load では落とさず、validate_workflow が
        # 全部の理由をまとめて報告する（他のエラーと同じ扱いにする）。
        check: "dict | None" = None
        check_error = ""
        try:
            check = normalize_check(sdef.get("check"))
        except ValueError as exc:
            check_error = f"ステート '{state_id}' の check が不正です: {exc}"

        states[state_id] = StateConfig(
            id=state_id,
            description=sdef.get("description", state_id),
            action=action,
            terminal=sdef.get("terminal", False),
            on_enter=sdef.get("on_enter", ""),
            on_exit=sdef.get("on_exit", ""),
            output_key=sdef.get("output_key", ""),
            max_retries=sdef.get("max_retries", 0),
            output_validator=sdef.get("output_validator", ""),
            check=check,
            # 検査の再投入予算。未宣言なら max_retries を継ぐ（実測ハーネスと同じ意味）。
            check_retries=int(sdef.get("check_retries", sdef.get("max_retries", 0)) or 0),
            check_on_exhausted=str(sdef.get("check_on_exhausted", "escalate")).strip(),
            check_feedback=bool(sdef.get("check_feedback", True)),
            check_error=check_error,
        )

    # トランジション（priority 順にソート）
    raw_transitions = data.get("transitions", [])
    transitions: list[TransitionConfig] = []
    for t in raw_transitions:
        # condition の解決: condition_file > file: prefix > インライン > 自動探索
        condition = t.get("condition", "")
        condition_file = t.get("condition_file", "")
        if condition_file:
            condition = (base_dir / condition_file).read_text(encoding="utf-8")
        elif condition.startswith("file:"):
            condition = _load_text_from_file(condition, base_dir)
        elif not condition:
            from_id = t["from"].replace("*", "wildcard")
            auto = base_dir / "conditions" / f"{from_id}_to_{t['to']}.md"
            if auto.exists():
                condition = auto.read_text(encoding="utf-8")

        transitions.append(TransitionConfig(
            from_state=t["from"],
            to_state=t["to"],
            condition=condition,
            condition_rule=t.get("condition_rule", ""),
            priority=t.get("priority", 0),
            description=t.get("description", ""),
        ))
    transitions.sort(key=lambda t: t.priority)

    # 設定
    cfg_raw = data.get("config", {})
    config = MachineConfig(
        max_steps=cfg_raw.get("max_steps", 50),
        on_max_steps=cfg_raw.get("on_max_steps", "error"),
        on_no_transition=cfg_raw.get("on_no_transition", "error"),
        verbose=cfg_raw.get("verbose", False),
    )

    return WorkflowDefinition(
        name=data.get("name", "Unnamed Workflow"),
        description=data.get("description", ""),
        initial_state=data["initial_state"],
        states=states,
        transitions=transitions,
        initial_context=data.get("context", {}),
        config=config,
    )


def validate_workflow(wf: WorkflowDefinition) -> list[str]:
    """バリデーションエラーのリストを返す（空リスト = 正常）。"""
    errors = []
    if wf.initial_state not in wf.states:
        errors.append(f"initial_state '{wf.initial_state}' が states に存在しません")
    for t in wf.transitions:
        if t.from_state != "*" and t.from_state not in wf.states:
            errors.append(f"未知のステート '{t.from_state}' からのトランジション")
        if t.to_state not in wf.states:
            errors.append(f"未知のステート '{t.to_state}' へのトランジション")
    # 非終端ステートに出力トランジションがないことを確認
    for state_id, state in wf.states.items():
        if state.terminal:
            continue
        has_transition = any(
            t.from_state in (state_id, "*") for t in wf.transitions
        )
        if not has_transition:
            errors.append(f"非終端ステート '{state_id}' に出力トランジションがありません")
    # 決定的検査の宣言。壊れた宣言を黙って素通りさせると、ゲートが無いまま
    # 「ゲートを置いた」つもりの実行になる——検知の欠落は最も気付けない事故なので投入前に落とす。
    for state_id, state in wf.states.items():
        if state.check_error:
            errors.append(state.check_error)
        if state.check and state.terminal:
            errors.append(f"終端ステート '{state_id}' は check を持てません（アクションが無い）")
        if state.check_on_exhausted not in CHECK_ON_EXHAUSTED:
            errors.append(
                f"ステート '{state_id}' の check_on_exhausted が不正です: "
                f"'{state.check_on_exhausted}'（{' | '.join(CHECK_ON_EXHAUSTED)}）")
        if state.check_retries < 0:
            errors.append(f"ステート '{state_id}' の check_retries は 0 以上で指定してください")
    # 検査結果のキーを見る condition_rule が、検査を宣言していないステートから出ていないか。
    # 検査が無ければキーも無く、evaluate_condition_rule は None（= LLM 評価へフォールバック）を
    # 返す——「決定的に見ているつもりが自己申告で決まっていた」に静かに戻る経路をここで塞ぐ。
    for t in wf.transitions:
        if not any(key in t.condition_rule for key in CHECK_CONTEXT_KEYS):
            continue
        sources = ([s for s in wf.states.values() if not s.terminal]
                   if t.from_state == "*" else [wf.states.get(t.from_state)])
        for source in sources:
            if source is not None and not source.check:
                errors.append(
                    f"トランジション '{t.from_state}' → '{t.to_state}' は検査結果"
                    f"（{'/'.join(CHECK_CONTEXT_KEYS)}）で分岐しますが、ステート "
                    f"'{source.id}' に check がありません")
    return errors


# ─────────────────────────────────────────────
#  テンプレートレンダラー
# ─────────────────────────────────────────────

def render_template(template: str, context: dict[str, Any]) -> str:
    """コンテキストを使って {{variable}} プレースホルダーを置換する。"""
    def replacer(m: re.Match) -> str:
        key = m.group(1).strip()
        # Support dot notation: history.state_id, context.key
        parts = key.split(".")
        val: Any = context
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part, m.group(0))  # 未定義の場合は元の文字列を保持
            else:
                val = m.group(0)
                break
        return str(val)

    return re.sub(r"\{\{([^}]+)\}\}", replacer, template)


# ─────────────────────────────────────────────
#  condition_rule 決定論的評価
# ─────────────────────────────────────────────

def evaluate_condition_rule(rule: str, ctx: dict[str, Any]) -> bool | None:
    """condition_rule を決定論的に評価する。

    書式: {演算子}:{キー}:{値}
      startswith:KEY:VALUE     ctx[KEY].startswith(VALUE)
      contains:KEY:VALUE       VALUE in ctx[KEY]
      equals:KEY:VALUE         ctx[KEY] == VALUE
      regex:KEY:PATTERN        re.search(PATTERN, ctx[KEY])
      lt:KEY:NUMBER            float(ctx[KEY]) < float(NUMBER)
      gte:KEY:NUMBER           float(ctx[KEY]) >= float(NUMBER)
      not-startswith:KEY:V     not ctx[KEY].startswith(VALUE)
      not-contains:KEY:V       VALUE not in ctx[KEY]
      not-equals:KEY:VALUE     ctx[KEY] != VALUE

    複合条件（AND）: セミコロン区切り "rule1;rule2"
      全ルールが True の場合のみ True

    ルールが空・解析不能・キー不在の場合は None を返してLLM評価にフォールバック。
    """
    if not rule:
        return None

    # セミコロン区切りの複合条件（AND評価）
    parts_list = [r.strip() for r in rule.split(";") if r.strip()]
    if len(parts_list) > 1:
        results = [evaluate_condition_rule(r, ctx) for r in parts_list]
        if any(r is None for r in results):
            return None  # 解析不能なルールが含まれる場合はフォールバック
        return all(results)  # type: ignore[arg-type]

    parts = rule.split(":", 2)
    if len(parts) < 3:
        return None

    op, key, value = parts[0].strip(), parts[1].strip(), parts[2].strip()

    if key not in ctx:
        return None

    ctx_value = str(ctx[key])

    try:
        match op:
            case "startswith":      return ctx_value.startswith(value)
            case "contains":        return value in ctx_value
            case "equals":          return ctx_value == value
            case "regex":           return bool(re.search(value, ctx_value))
            case "lt":              return float(ctx_value) < float(value)
            case "gte":             return float(ctx_value) >= float(value)
            case "not-startswith":  return not ctx_value.startswith(value)
            case "not-contains":    return value not in ctx_value
            case "not-equals":      return ctx_value != value
            case _:                 return None
    except (ValueError, re.error):
        return None


# ─────────────────────────────────────────────
#  実行結果
# ─────────────────────────────────────────────

@dataclass
class ExecutionResult:
    success: bool
    final_state: str
    output: str
    context: dict[str, Any]
    steps: list[dict[str, Any]]
    error: str = ""
    # 検査の再投入上限に達した = この段では解けない。失敗一般とは別に名前を付ける
    # （実測では上限到達の失敗は 9/9 が同形で、引き直しても揺れない——上位の段へ
    # 回すシグナルとして使う）。
    escalate: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "final_state": self.final_state,
            "output": self.output,
            "steps": self.steps,
            "error": self.error,
            "escalate": self.escalate,
        }


# ─────────────────────────────────────────────
#  エンジン
# ─────────────────────────────────────────────

LLMFn = Callable[[str], Awaitable[str]]


class StateMachineEngine:
    """
    非同期ステートマシンエンジン。

    llm_fn: async function (prompt: str) -> str
            ステートアクションとトランジション条件の評価の両方で呼び出される。
    """

    def __init__(self, llm_fn: LLMFn, verbose: bool = False):
        self.llm_fn = llm_fn
        self.verbose = verbose

    # ── 公開エントリーポイント ──────────────────────────────────────────

    async def run(
        self,
        workflow: WorkflowDefinition | str | Path,
        input_text: str = "",
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """ワークフローを実行する。ExecutionResult を返す。"""
        if not isinstance(workflow, WorkflowDefinition):
            workflow = load_workflow(workflow)

        errors = validate_workflow(workflow)
        if errors:
            return ExecutionResult(
                success=False,
                final_state="",
                output="",
                context={},
                steps=[],
                error="バリデーション失敗:\n" + "\n".join(errors),
            )

        # 組み込み変数。モデルに「今日」を推測させると学習時点の日付を書くので実行時の値を渡す。
        # workflow の context や呼び出し側の指定が上書きできる位置に置く。
        started = datetime.now().astimezone()
        ctx = {"today": started.strftime("%Y-%m-%d"),
               "now": started.isoformat(timespec="seconds"),
               **workflow.initial_context, **(context or {})}
        ctx["input"] = input_text
        ctx["history"] = {}
        ctx["step_count"] = 0
        ctx["last_output"] = ""

        verbose = self.verbose or workflow.config.verbose
        steps: list[dict] = []
        current_state_id = workflow.initial_state

        for step_idx in range(workflow.config.max_steps):
            state = workflow.states[current_state_id]
            self._log(verbose, f"\n{'─'*50}")
            self._log(verbose, f"[ステップ {step_idx+1}] ステートに入りました: {current_state_id} ({state.description})")

            # ステートアクションを実行し、宣言があれば決定的検査を通す。
            # 検査が落ちたら同じステートをやり直す（自己申告ではなく測った事実で決める）。
            output, gate = await self._execute_gated(state, ctx, verbose)
            ctx["last_output"] = output
            ctx["history"][current_state_id] = output
            ctx["step_count"] = step_idx + 1
            if state.output_key:
                ctx[state.output_key] = output
            if gate is not None:
                ctx.update(gate["context"])

            steps.append({
                "step": step_idx + 1,
                "state": current_state_id,
                "output": output,
                **({"check": {"ok": gate["ok"], "status": gate["status"],
                              "attempts": gate["attempts"]}} if gate else {}),
            })

            # 検査が最後まで通らなかった。ここから先は宣言が決める。
            if gate is not None and not gate["ok"] and state.check_on_exhausted != "continue":
                escalate = state.check_on_exhausted == "escalate"
                self._log(verbose, f"  ✗ 検査が {gate['attempts']} 回とも失敗しました"
                                   + ("（上位の段へ回す）" if escalate else ""))
                return ExecutionResult(
                    success=False,
                    final_state=current_state_id,
                    output=output,
                    context=ctx,
                    steps=steps,
                    error=(f"ステート '{current_state_id}' の検査が "
                           f"{gate['attempts']} 回とも失敗しました: "
                           f"{gate['context']['check_output'] or gate['context']['check_status']}"),
                    escalate=escalate,
                )

            # 終端ステート → 完了
            if state.terminal:
                self._log(verbose, f"✓ 終端ステートに到達しました: {current_state_id}")
                return ExecutionResult(
                    success=True,
                    final_state=current_state_id,
                    output=output,
                    context=ctx,
                    steps=steps,
                )

            # トランジションを評価
            next_state_id = await self._evaluate_transitions(
                current_state_id, workflow.transitions, ctx, verbose
            )

            if next_state_id is None:
                action = workflow.config.on_no_transition
                if action == "stop":
                    return ExecutionResult(
                        success=True,
                        final_state=current_state_id,
                        output=output,
                        context=ctx,
                        steps=steps,
                    )
                elif action in workflow.states:
                    next_state_id = action
                else:
                    return ExecutionResult(
                        success=False,
                        final_state=current_state_id,
                        output=output,
                        context=ctx,
                        steps=steps,
                        error=f"ステート '{current_state_id}' からの一致するトランジションがなく、on_no_transition='error' です",
                    )

            self._log(verbose, f"→ 遷移先: {next_state_id}")
            current_state_id = next_state_id

        # 最大ステップ数に到達
        action = workflow.config.on_max_steps
        if action == "stop":
            return ExecutionResult(
                success=True,
                final_state=current_state_id,
                output=ctx["last_output"],
                context=ctx,
                steps=steps,
            )
        elif action in workflow.states:
            # 設定されたステートへジャンプ
            state = workflow.states[action]
            output = await self._execute_state(state, ctx, verbose)
            return ExecutionResult(
                success=True,
                final_state=action,
                output=output,
                context=ctx,
                steps=steps,
            )
        else:
            return ExecutionResult(
                success=False,
                final_state=current_state_id,
                output=ctx["last_output"],
                context=ctx,
                steps=steps,
                error=f"最大ステップ数 ({workflow.config.max_steps}) に到達しました",
            )

    # ── 内部: ステート実行 ───────────────────────────────────

    async def _execute_gated(
        self, state: StateConfig, ctx: dict, verbose: bool
    ) -> "tuple[str, dict | None]":
        """アクションを実行し、宣言された検査を通す。落ちたら同じステートをやり直す。

        戻り値: (最終出力, 検査結果 | None)。検査の宣言が無ければ従来どおり 1 回だけ実行する。
        """
        if not state.check:
            return await self._execute_state(state, ctx, verbose), None

        attempts = max(1, state.check_retries + 1)
        note = ""
        for attempt in range(attempts):
            output = await self._execute_state(state, ctx, verbose, check_note=note)
            result = run_check(state.check)
            self._log(verbose, f"  検査 [{' '.join(result['argv'])}]: "
                               f"{'✓ 通過' if result['ok'] else '✗ 失敗'} "
                               f"(status={result['context']['check_status']})")
            if result["ok"] or attempt == attempts - 1:
                return output, {**result, "attempts": attempt + 1}
            note = (check_feedback_note(state.check, result, attempt + 1, attempts)
                    if state.check_feedback else
                    f"Retry {attempt + 1}/{attempts - 1}: the declared check failed. "
                    "Fix the work so it succeeds.")
        return "", None   # 到達しない（ループ内で必ず返る）

    async def _execute_state(
        self, state: StateConfig, ctx: dict, verbose: bool, check_note: str = ""
    ) -> str:
        parts = []
        if state.on_enter:
            parts.append(render_template(state.on_enter, ctx))
        if state.action:
            parts.append(render_template(state.action, ctx))

        if not parts:
            return ""

        base_prompt = "\n\n".join(parts)
        if check_note:
            base_prompt += f"\n\n{check_note}"
        max_attempts = state.max_retries + 1

        output = ""
        for attempt in range(max_attempts):
            prompt = base_prompt
            if attempt > 0:
                prompt += (
                    f"\n\n⚠️ リトライ {attempt}/{state.max_retries}: "
                    "前回の出力が Output Contract に違反しました。"
                    "指定された形式を必ず守って再実行してください。"
                )
            self._log(verbose, f"  アクションプロンプト (attempt {attempt+1}):\n{self._indent(prompt)}")

            output = (await self.llm_fn(prompt)).strip()
            self._log(verbose, f"  アクション出力: {output[:200]}{'...' if len(output) > 200 else ''}")

            if state.output_validator:
                if self._validate_output(output, state.output_validator):
                    break
                if attempt < state.max_retries:
                    self._log(verbose, f"  ⚠ 出力バリデーション失敗、リトライします ({attempt+1}/{max_attempts})")
                else:
                    self._log(verbose, f"  ⚠ 出力バリデーション失敗、最大リトライ到達")
            else:
                break  # validator なしは常に成功

        if state.on_exit:
            exit_prompt = render_template(state.on_exit, {**ctx, "last_output": output})
            self._log(verbose, f"  on_exit プロンプト:\n{self._indent(exit_prompt)}")
            exit_output = await self.llm_fn(exit_prompt)
            self._log(verbose, f"  on_exit 出力: {exit_output.strip()[:100]}")

        return output

    # ── 内部: トランジション評価 ────────────────────────────

    async def _evaluate_transitions(
        self,
        current_state_id: str,
        transitions: list[TransitionConfig],
        ctx: dict,
        verbose: bool,
    ) -> str | None:
        """最初に一致したトランジションの遷移先ステートIDを返す。一致なしは None。"""
        candidates = [
            t for t in transitions
            if t.from_state == current_state_id or t.from_state == "*"
        ]

        for transition in candidates:
            label = transition.description or f"{transition.from_state} → {transition.to_state}"

            # 0. 無条件トランジション（条件文もルールも無い）は評価せず成立させる。
            #    空の条件文を LLM に渡すと答えが安定しない。
            if not transition.condition.strip() and not transition.condition_rule.strip():
                self._log(verbose, f"  条件 [{label}] (無条件): ✓ 真")
                return transition.to_state

            # 1. condition_rule で決定論的評価を試みる
            rule_result = evaluate_condition_rule(transition.condition_rule, ctx)
            if rule_result is not None:
                self._log(verbose, f"  条件 [{label}] (rule): {'✓ 真' if rule_result else '✗ 偽'}")
                if rule_result:
                    return transition.to_state
                continue

            # 2. LLM フォールバック
            condition = render_template(transition.condition, ctx)
            matches = await self._evaluate_condition(condition, ctx, verbose)
            self._log(verbose, f"  条件 [{label}] (llm): {'✓ 真' if matches else '✗ 偽'}")
            if matches:
                return transition.to_state

        return None

    async def _evaluate_condition(
        self, condition: str, ctx: dict, verbose: bool
    ) -> bool:
        """LLM に条件を評価させる。True/False を返す。"""
        last_output = ctx.get("last_output", "")
        prompt = f"""あなたはステートマシンのトランジション条件を評価しています。

最後のステートの出力:
\"\"\"
{last_output}
\"\"\"

コンテキスト変数:
{json.dumps({k: v for k, v in ctx.items() if k not in ('history',)}, indent=2, default=str)}

評価する条件:
{condition}

条件が真なら YES、偽なら NO の一語のみで回答してください。
説明は不要です。"""

        response = (await self.llm_fn(prompt)).strip().upper()
        # Accept YES / NO even with trailing punctuation
        return response.startswith("YES")

    # ── 内部: 出力バリデーション ──────────────────────────────

    @staticmethod
    def _validate_output(output: str, validator: str) -> bool:
        """output_validator ルールに従って出力の第1行を検証する。

        書式: "startswith:VAL1,VAL2,VAL3"
          出力の第1行がいずれかの値で始まること
        """
        if not validator:
            return True
        first_line = output.split("\n")[0].strip()
        if validator.startswith("startswith:"):
            allowed = [v.strip() for v in validator[len("startswith:"):].split(",")]
            return any(first_line.startswith(v) for v in allowed)
        return True  # 未知の validator は常に True

    # ── ユーティリティ ───────────────────────────────────────────────────

    @staticmethod
    def _log(verbose: bool, msg: str) -> None:
        if verbose:
            print(msg)

    @staticmethod
    def _indent(text: str, prefix: str = "    ") -> str:
        return "\n".join(prefix + line for line in text.splitlines())
