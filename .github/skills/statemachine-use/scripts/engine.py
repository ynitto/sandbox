"""
yaml-statemachine engine.py
LLM駆動 YAML ワークフロー向けのコア非同期ステートマシンエンジン。
"""

from __future__ import annotations

import re
import json
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

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "final_state": self.final_state,
            "output": self.output,
            "steps": self.steps,
            "error": self.error,
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

        ctx = {**workflow.initial_context, **(context or {})}
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

            # ステートアクションを実行（max_retries でリトライ）
            output = await self._execute_state(state, ctx, verbose)
            ctx["last_output"] = output
            ctx["history"][current_state_id] = output
            ctx["step_count"] = step_idx + 1
            if state.output_key:
                ctx[state.output_key] = output

            steps.append({
                "step": step_idx + 1,
                "state": current_state_id,
                "output": output,
            })

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

    async def _execute_state(
        self, state: StateConfig, ctx: dict, verbose: bool
    ) -> str:
        parts = []
        if state.on_enter:
            parts.append(render_template(state.on_enter, ctx))
        if state.action:
            parts.append(render_template(state.action, ctx))

        if not parts:
            return ""

        base_prompt = "\n\n".join(parts)
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
