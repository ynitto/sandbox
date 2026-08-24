from __future__ import annotations
# agent.py — 元 agent-flow.py の 2926-3481 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# Executor — タスク実行（エージェント CLI or stub）
# --------------------------------------------------------------------------
from agentcore import promptrender  # noqa: E402


class EmptyOutputError(RuntimeError):
    """エージェント CLI が rc=0 のまま本文を返さなかった（空応答）。

    RuntimeError の一種なので既存の呼び出し側の扱いは変わらない。型を分けるのは
    「空だったのか、内容が失敗したのか」を**文言の正規表現ではなく型で**判別させるため
    （書き手が文言を変えると読み手だけが静かに壊れる、を作らない）。"""


def _agent_timeout(purpose: str = "", plugin_timeout=None) -> float | None:
    """エージェント CLI 1 呼び出しのタイムアウト秒。

    agent-control の用途別（work 系は worker も継承）→ flow 共通 → plugin →
    agent_timeout / 環境変数 → 既定 600 の順で解決する。0/負は次の設定へ委ねる。
    """
    if purpose:
        ctl = _control_workload()
        agents = ctl.get("agents") or {}
        role = agents.get(purpose) or {}
        raw_values = [role.get("timeout_sec")]
        if purpose in VALID_KINDS and purpose != "worker":
            raw_values.append((agents.get("worker") or {}).get("timeout_sec"))
        raw_values.append(ctl.get("timeout_sec"))
        for raw in raw_values:
            try:
                to = float(raw)
                if to > 0:
                    return to
            except (TypeError, ValueError):
                pass
    if plugin_timeout is not None:
        try:
            to = float(plugin_timeout)
            if to > 0:
                return to
        except (TypeError, ValueError):
            pass
    to = _AGENT_TIMEOUT
    if to is None:
        raw = os.environ.get("AGENT_FLOW_TIMEOUT") or os.environ.get("AGENT_FLOW_KIRO_TIMEOUT") or "600"
        try:
            to = float(raw)
        except ValueError:
            to = 600.0
    return to if to > 0 else None


# 設定ファイル/CLI で解決した閾値を、args を持たない free 関数（run_agent 等）が参照できる
# よう、main の resolve 後に _configure_thresholds がここへ反映する（既定は CONFIG_DEFAULTS）。
_ARGV_LIMIT = CONFIG_DEFAULTS["argv_limit"]
# レイヤ1（in-place リトライ）: transient 分類の失敗を run_agent 内で再試行する回数と
# 初回バックオフ秒（設定 transient_retries / transient_backoff）。
_TRANSIENT_RETRIES = int(CONFIG_DEFAULTS["transient_retries"])
_TRANSIENT_BACKOFF = float(CONFIG_DEFAULTS["transient_backoff"])
# レイヤ2（形式修復リトライ）: 出力契約違反の修復再呼び出し回数（設定 format_retries）。
_FORMAT_RETRIES = int(CONFIG_DEFAULTS["format_retries"])
# executor プラグインの追加検索ディレクトリ（設定 executor_dir）。
_EXECUTOR_DIR: "str | None" = None
# エージェント CLI タイムアウト秒 / stub スリープ上限秒（設定 agent_timeout / stub_sleep_max）。
# None のままなら _agent_timeout / _stub_sleep が環境変数→組み込み既定にフォールバックする。
_AGENT_TIMEOUT: "float | None" = None
_STUB_SLEEP_MAX: "float | None" = None
# LLM 実行に使うエージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）。
_AGENT_CLI: str = str(CONFIG_DEFAULTS["agent_cli"])
# 役割（purpose）毎の上書き（設定 agents: の正規化済みマップ）。キーは planner / evaluator /
# worker（全 kind の既定）/ 個別 kind（work/generate/classify/synthesize/verify/filter/judge/
# reduce/split/map/extract/retrieve。human はエージェントを呼ばない）。値は
# {agent_cli, model}。子プロセスへは --config 伝搬で同じ設定が届く。
_AGENT_OVERRIDES: "dict[str, dict]" = {}
_EXECUTION_OVERRIDES: "dict[str, dict]" = {}
AGENT_ROLES = ("planner", "evaluator", "worker")
EXECUTION_ROLES = frozenset((*AGENT_ROLES, "verify", "human", "session"))
# 読み取り専用が**既定**の役割（適用拡大設計 §5「読まない系」）。planner / evaluator は材料を
# 全部プロンプトで受け取り、テキストか JSON を返すだけなので道具が要らない。既定を write の
# ままにすると、agent-control が agent_cli をツールループ型（agent-ollama の --tools bash 等）へ
# 差し替えたときに、契約どおりの JSON 応答が「規約から外れています」と蹴られて planner が
# 空回りする。設定 `agents: {planner: {readonly: false}}` と明示すれば従来どおり write で呼べる。
READONLY_ROLES = frozenset({"planner", "evaluator"})
# 出力が JSON だけと決まっている役割（適用拡大設計 §4.3）。CLI 定義が用途別の変種
# （`variants`）を申告していれば、この役割に限って自動でそちらへ振り替える。
# verify / map / work は成果物側にワークスペースの本文や自由記述を含むので入れない。
# STRUCTURED_KINDS（JSON を抽出しようと試みる kind）とは別物: あちらは「JSON なら拾う」、
# こちらは「JSON 以外を返してはいけない」。
JSON_CONTRACT_ROLES = frozenset({"planner", "evaluator", "split", "filter", "judge", "reduce", "extract"})
# うち、トップレベルが JSON **配列**でなければ下流が動かない役割。split の data が配列で
# ないと `_expand_splits` が展開されず run が空振りする（申告が無い CLI へは振り替わらない
# ——variants に該当キーが無ければ resolve_variant が None を返すだけ）。
LIST_CONTRACT_ROLES = frozenset({"split"})
# variant 振り替えの対象となる用途の全体集合。JSON/配列契約に加え、根拠を実際に読む
# 必要がある retrieve（ollama-json へ寄せると read tool を失う）と、検証専用チューニング
# を持つ verify（ollama-verify の gemma4:12b 等）も含む——いずれも「この用途では base
# 定義のままでは要件を満たせない」という同種の事情なので、同じ口（agents/<name>.json の
# `variants`）で申告させる。
VARIANT_ELIGIBLE_ROLES = JSON_CONTRACT_ROLES | LIST_CONTRACT_ROLES | frozenset({"retrieve", "verify"})
# 本文の末尾に完了可否の封筒 `{"ok": ...}` を置くよう指示している kind（実行系のうち
# JSON 抽出をしないもの）。プロンプトの指示とここが食い違うと、自己申告した未完了が
# 黙って done になる——一致は tests/test_agent_cli.py が prompt 側の EXEC_KINDS と突き合わせる。
_ENVELOPE_KINDS = frozenset({"work", "generate"})
# JSON 契約の役割が空応答を返したときの言い直し（レイヤ2 相当）。ツールループ型の CLI が
# 制御語（TASK_COMPLETE 等）だけを返す・思考だけで本文を出さない、が実際の空応答の中身。
_EMPTY_OUTPUT_NUDGE = (
    "[前回の出力は空でした]\n"
    "本文が空のまま終了しました（制御語だけ・思考だけで本文を出していない可能性があります）。"
    "説明・前置き・完了報告を書かず、要求された JSON だけを本文として出力してください。")
# executor=agent の実行系プロンプトを供給するスキル名（設定 worker_skill）。
# none/builtin/空 で無効＝常に組み込みプロンプト。
_WORKER_SKILL: str = str(CONFIG_DEFAULTS["worker_skill"])
# 計画（3 段パイプライン）のプロンプトを供給するスキル名（設定 planner_skill）。
# 名前を固定していると、プロジェクト独自のプランナーを別名で置けない（worker_skill と対称）。
_PLANNER_SKILL: str = str(CONFIG_DEFAULTS["planner_skill"])


def agent_cli_binary(cli: str) -> str:
    """doctor が PATH 確認すべき実行ファイル名。定義ファイルの command[0] から導く（S9）。
    対応表を残すと「JSON を直したのに doctor だけ古い名前を探す」二重管理になる。"""
    try:
        return str(load_agent_plugin(cli)["command"][0])
    except RuntimeError:
        return str(cli)


def _normalize_agent_overrides(raw) -> "dict[str, dict]":
    """設定 agents:（役割毎の agent_cli/model/readonly 上書き）を正規化する。有効キーは
    AGENT_ROLES と各ノード kind（VALID_KINDS）。不正な値は黙って落とす（設定ミスで run を
    殺さない）。"""
    out: "dict[str, dict]" = {}
    if not isinstance(raw, dict):
        return out
    valid = set(AGENT_ROLES) | set(VALID_KINDS)
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in valid or not isinstance(v, dict):
            continue
        ov: dict = {}
        if v.get("agent_cli"):
            ov["agent_cli"] = str(v["agent_cli"]).strip().lower()
        if v.get("model"):
            ov["model"] = str(v["model"]).strip()
        if isinstance(v.get("readonly"), bool):
            ov["readonly"] = v["readonly"]
        if isinstance(v.get("fallbacks"), list):
            fallbacks = [{"agent_cli": str(x["agent_cli"]).strip().lower(),
                          **({"model": str(x["model"]).strip()} if x.get("model") else {})}
                         for x in v["fallbacks"]
                         if isinstance(x, dict) and str(x.get("agent_cli") or "").strip()]
            if fallbacks:
                ov["fallbacks"] = fallbacks
        if ov:
            out[key] = ov
    return out


def _normalize_execution_overrides(raw) -> dict:
    """inbox の実行時指定を正規化する。未知キーは加算的互換のため無視する。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return {}
    out = {"version": 1, "roles": {}, "kinds": {}}
    for group, valid in (("roles", EXECUTION_ROLES), ("kinds", set(VALID_KINDS) - {"human"})):
        values = raw.get(group)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            name = str(key).strip().lower()
            if name not in valid or not isinstance(value, dict):
                continue
            item = {}
            for field in ("tier", "agent_cli", "model"):
                if value.get(field):
                    item[field] = str(value[field]).strip()
            if item:
                item["pinned"] = True
                out[group][name] = item
    return out if out["roles"] or out["kinds"] else {}


def _execution_override(purpose: str) -> dict:
    kinds = _EXECUTION_OVERRIDES.get("kinds") or {}
    roles = _EXECUTION_OVERRIDES.get("roles") or {}
    if purpose in kinds:
        return {**kinds[purpose], "source": "run-kind"}
    role = purpose if purpose in EXECUTION_ROLES else "worker" if purpose in VALID_KINDS else ""
    if role and role in roles:
        return {**roles[role], "source": "run-role"}
    return {}


def _agent_readonly(purpose: str) -> bool:
    """この役割を読み取り専用で呼ぶか（設定 `agents[purpose].readonly`・既定は
    READONLY_ROLES に属する役割だけ True）。

    解決順は `_agent_for` と同じ（kind は agents["worker"] へフォールバック）。宣言して
    よいのは**読まない系**——planner / evaluator や判定系 kind のように、材料を全部
    プロンプトで受け取ってテキストか JSON を返す役割に限る。work / verify のように
    ワークスペースを触る役割へ付けると、道具ごと失って空振りする（適用拡大設計 §5）。
    """
    ov = _AGENT_OVERRIDES.get(purpose)
    if ov is None and purpose in VALID_KINDS:
        ov = _AGENT_OVERRIDES.get("worker")
    ov = ov or {}
    if "readonly" in ov:
        return bool(ov["readonly"])
    return purpose in READONLY_ROLES


def _agent_for(purpose: str) -> "tuple[str, str | None]":
    """役割（purpose）の実効エージェント (agent_cli, model 上書き)。解決順:
    agent-control（管理面の横断上書き）＞ agents[purpose] ＞（purpose がノード kind なら）
    agents["worker"] ＞ グローバル agent_cli。soft/縮退中は control の degraded を重ねる。

    解決した CLI が用途別の変種（`variants`）を申告していれば、対象の用途
    （VARIANT_ELIGIBLE_ROLES）だけ最後にそちらへ振り替える。振り替えは同じエンジンでの
    起動形の違いなので、どの層で CLI が決まっても同じ規則が効く。モデルは、人が明示した
    層（設定 `agents:` の役割別モデル・run 単位の実行時指定）が無ければ変種自身の既定
    モデルへ寄せる——変種は用途専用にチューニングされていることが多く（例:
    ollama-verify の gemma4:12b）、tier/agent-control が自動選択したモデルをそのまま
    持ち込むと調整が無効化される。人が明示した層は最優先のまま変更しない（自動選択層
    だけを変種の既定で上書きする）。"""
    ov = _AGENT_OVERRIDES.get(purpose)
    if ov is None and purpose in VALID_KINDS:
        ov = _AGENT_OVERRIDES.get("worker")
    ov = ov or {}
    cli = str(ov.get("agent_cli") or _AGENT_CLI).lower()
    model = ov.get("model") or None
    configured_model = bool(model)
    # 候補ベース（version 2）: selection_policy があれば Resolver の決定が control 層を
    # 置き換える。park のときは candidate を変えない——実行は run_agent の環境ガードが
    # 実行前に止めるので、ここで legacy / 縮退候補へ黙って降格しない（設計 §6.6 / §5.2）。
    decision = _control_policy_decision(purpose)
    if decision is not None:
        selected = decision.get("selected")
        if selected:
            cli = str(selected["agent_cli"]).lower()
            model = selected.get("model") or model
        # 縮退（degraded）は legacy の口。候補ベースでは Compiler の strategy が消費を
        # 織り込んで rank を出すので、二重に重ねない。
    else:
        # agent-control（control > CLI引数 > 設定ファイル > 組み込み既定）が最優先の上書き。
        c_cli, c_model = _control_override(purpose)
        if c_cli:
            cli = c_cli.lower()
        if c_model:
            model = c_model
        # node-budget の soft_ratio 到達中（または on_exhausted=degrade で超過中）は縮退指定を重ねる。
        nb = _node_budget_state()
        if nb and (nb.get("soft") or (nb.get("exceeded") and nb.get("on_exhausted") == "degrade")):
            d_cli, d_model = _control_degraded()
            if d_cli:
                cli = d_cli.lower()
            if d_model:
                model = d_model
    # run 単位の固定は、保存済みノード・control・縮退候補より優先する。hard budget と lifecycle
    # はエージェント解決の外側で止めるため、この固定で安全弁を迂回することはない。
    run_ov = _execution_override(purpose)
    explicit_model = configured_model or bool(run_ov.get("model"))
    if run_ov.get("agent_cli"):
        cli = str(run_ov["agent_cli"]).lower()
    if run_ov.get("model"):
        model = str(run_ov["model"])
    if purpose in VARIANT_ELIGIBLE_ROLES:
        variant = _agentcli.resolve_variant(cli, purpose)
        if variant:
            cli = variant["agent_cli"]
            if not explicit_model and variant["default_model"]:
                model = variant["default_model"]
    return cli, model


def retry_agent_for(purpose: str) -> "dict | None":
    """役割の宣言済み ladder から、相対コストが高い次の 1 段だけを選ぶ。"""
    ov = _AGENT_OVERRIDES.get(purpose)
    if ov is None and purpose in VALID_KINDS:
        ov = _AGENT_OVERRIDES.get("worker")
    current = _agent_for(purpose)[0]
    target = _agentcli.costlier_fallback(current, (ov or {}).get("fallbacks"))
    if target:
        target["from_agent_cli"] = current
    return target


def _configure_thresholds(args) -> None:
    """設定ファイル/CLI（resolve_config 済み）の閾値をモジュール変数へ確定させる。
    run_agent / executor 解決は args を受け取らないため、プロセス起動時に一度だけ値を固定する。"""
    global _ARGV_LIMIT, _EXECUTOR_DIR, _AGENT_TIMEOUT, _STUB_SLEEP_MAX, _AGENT_CLI, _AGENT_OVERRIDES
    global _EXECUTION_OVERRIDES
    global _WORKER_SKILL, _PLANNER_SKILL, _TRANSIENT_RETRIES, _TRANSIENT_BACKOFF, _FORMAT_RETRIES
    for name, attr, cast in (("_TRANSIENT_RETRIES", "transient_retries", int),
                             ("_TRANSIENT_BACKOFF", "transient_backoff", float),
                             ("_FORMAT_RETRIES", "format_retries", int)):
        v = getattr(args, attr, None)
        if v is not None:
            try:
                globals()[name] = cast(v)
            except (TypeError, ValueError):
                pass
    ac = getattr(args, "agent_cli", None)
    if ac:
        _AGENT_CLI = str(ac).lower()
    _AGENT_OVERRIDES = _normalize_agent_overrides(getattr(args, "agents", None))
    _EXECUTION_OVERRIDES = _normalize_execution_overrides(getattr(args, "execution_overrides", None))
    wsk = getattr(args, "worker_skill", None)
    if wsk is not None:
        _WORKER_SKILL = str(wsk).strip()
    psk = getattr(args, "planner_skill", None)
    if psk is not None:
        _PLANNER_SKILL = str(psk).strip()
    v = getattr(args, "argv_limit", None)
    if v:
        try:
            _ARGV_LIMIT = int(v)
        except (TypeError, ValueError):
            pass
    d = getattr(args, "executor_dir", None)
    if d:
        _EXECUTOR_DIR = str(d)
    kt = getattr(args, "agent_timeout", None)
    if kt is not None:
        try:
            _AGENT_TIMEOUT = float(kt)
        except (TypeError, ValueError):
            pass
    ss = getattr(args, "stub_sleep_max", None)
    if ss is not None:
        try:
            _STUB_SLEEP_MAX = float(ss)
        except (TypeError, ValueError):
            pass


def _agent_argv_limit() -> int:
    """エージェント CLI へ argv（コマンドライン）で渡すプロンプトの最大バイト数。
    これを超えるプロンプトは一時ファイルへ退避し参照渡しに切り替える。依存タスクの
    成果物が大きいとプロンプトが肥大し、OS の ARG_MAX（コマンドライン長制限）に達して
    プロセス起動自体が失敗するため。設定 argv_limit / CLI --argv-limit で調整（既定 100000）。"""
    return _ARGV_LIMIT if _ARGV_LIMIT > 0 else CONFIG_DEFAULTS["argv_limit"]


# --- エージェント CLI 定義（データ契約: schemas/agent-cli.schema.json） -----------------------
# 読み込みと argv 組み立ては agentcore.agentcli の 1 実装（agent-project / agent-amigos と共有）。
# **組み込み（kiro/claude/copilot/codex）もここでは特別扱いしない**（S9）——以前は同じ argv 知識が
# 4 ツールに重複し、同じ CLI でもツールによってフラグが違う状態になっていた。
# ここに残すのは「読み込んだ定義を覚えておき、失敗トリアージの errors[] を集める」ところだけ。
_AGENT_PLUGIN_CACHE: "dict[str, dict]" = {}


def load_agent_plugin(name: str) -> dict:
    """agents/<name>.json を読む（agentcore へ委譲）。見つからない・壊れているは RuntimeError。"""
    key = str(name or "").strip().lower()
    if key in _AGENT_PLUGIN_CACHE:
        return _AGENT_PLUGIN_CACHE[key]
    try:
        # キャッシュはここで持つ（二重キャッシュにすると定義の差し替えが効かなくなる）
        spec = _agentcli.load_cli(key, use_cache=False)
    except _agentcli.AgentCliError as e:
        raise RuntimeError(str(e)) from e
    _AGENT_PLUGIN_CACHE[key] = spec
    return spec


def _plugin_error_patterns() -> tuple:
    out = []
    for spec in _AGENT_PLUGIN_CACHE.values():
        out.extend(spec.get("errors") or [])
    return tuple(out)


# --- ノード予算 v2（node-budget 契約: schemas/node-budget.schema.json） --------------------
# ノード（マシン）単位の共有台帳。定常業務（agent-loop）・agent-project・agent-flow・
# agent-amigos が同じ台帳（$AGENT_BUDGET_DIR、既定 ~/.agents/budget/）に記帳し、合計が上限
# （0 = 無制限）を超えたら新規の LLM 実行を控える。v2 で一次単位をトークンへ拡張（時間上限は
# v1 互換で AND）。台帳には実測のみ（実測秒＋実測できたトークン）を書き、未報告行は rates で
# 読み出し時に推定する。配分・較正の知能は管理面（dashboard）にあり、エンジンは単純比較のみ。
# 読取・推定・state は agentcore.nodebudget に集約（C7）。記帳は各ツールが自前で持つ。
from agentcore import nodebudget as _nodebudget  # noqa: E402

_NODE_BUDGET_WORKLOAD = "flow"
_NODE_BUDGET_TOOL = "agent-flow"


def _node_budget_dir() -> str:
    return os.path.abspath(agent_home_subdir("AGENT_BUDGET_DIR", "budget"))


def _node_budget_rate(cfg: dict, cli: str, model: str) -> float:
    """トークン未報告行の推定レート（tokens/秒）。解決順 cli:model → cli → default。"""
    return _nodebudget.rate(cfg, cli, model)


def _row_tokens(rec: dict, cfg: dict) -> float:
    """1 記帳のトークン消費。実測（tokens_in+tokens_out）があればその値、無ければ秒 × レート。"""
    return _nodebudget.row_tokens(rec, cfg)


def _node_budget_state() -> "dict | None":
    """ノード予算の消費状況。設定が無い/上限が全て 0 なら None（= 無制限・チェック不要）。
    exceeded は時間上限・トークン上限（合計 or 自ワークロードの実効上限）のいずれか到達。
    soft は縮退開始（soft_ratio 到達・未超過）。on_exhausted は超過時の方針。"""
    return _nodebudget.state(_NODE_BUDGET_WORKLOAD, dir=_node_budget_dir(), view="engine")


def _node_budget_record(seconds: float, ref: str = "", agent_cli: str = "",
                        model: str = "", tokens_in=None, tokens_out=None, usd=None,
                        extra: "dict | None" = None) -> None:
    """台帳へ 1 記帳を追記する（O_APPEND — 複数プロセスの同時追記でも行は壊れない）。
    tokens_* は実測できたときだけ渡す（推定値は書かない）。agent_cli / model は帰属。
    extra は観測行（quota_kind / reset_at）用の追加フィールド。"""
    if seconds <= 0 and not tokens_in and not tokens_out and not extra:
        return
    d = os.path.join(_node_budget_dir(), "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"ts": now_iso(), "workload": _NODE_BUDGET_WORKLOAD,
               "tool": _NODE_BUDGET_TOOL, "seconds": round(float(seconds), 3),
               "ref": ref, "purpose": ref}
        rec.update(extra or {})
        rid = str(os.environ.get("AGENT_RESERVATION_ID") or rec.get("reservation_id") or "").strip()
        if rid and "reservation_id" not in rec:
            rec["reservation_id"] = rid
        if agent_cli:
            rec["agent_cli"] = str(agent_cli)
        if model:
            rec["model"] = str(model)
        if tokens_in is not None:
            rec["tokens_in"] = float(tokens_in)
        if tokens_out is not None:
            rec["tokens_out"] = float(tokens_out)
        if usd is not None:
            rec["usd"] = float(usd)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fd = os.open(os.path.join(d, time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass    # 記帳失敗で実行を止めない（台帳は best-effort、上限は次の実行前チェックで効く）


def _record_quota_observation(cli: str, blob: str) -> None:
    """quota で落ちた CLI を台帳へ**観測**として残す（消費 0 行）。

    これが無いと、細分した quota（恒久枯渇 / 時限レート制限と復帰時刻）は失敗メッセージの
    中で消えてしまい、管理面の段判定は「枠に当たった」ことを永久に知れない。観測を台帳へ
    置くのは、**書き手を増やさない**ため——エンジンは既に台帳の書き手で、管理面は既に
    台帳の読み手なので、経路が 1 本増えない（C7）。"""
    if not cli:
        return
    try:
        spec = _agentcli.load_cli(cli)
        detail = _agentcli.classify_error(spec, blob, detailed=True, now=time.time())
    except Exception:  # noqa: BLE001 — 観測の失敗で実行を止めない
        return
    if not detail or not detail.get("quota_kind"):
        return
    extra = {"event": "quota", "quota_kind": detail["quota_kind"]}
    if detail.get("reset_at"):
        extra["reset_at"] = detail["reset_at"]
    _node_budget_record(0.0, ref="", agent_cli=cli, extra=extra)


# --- agent-control（管理面→エンジンの宣言的オーケストレーション契約） ----------------------
# schemas/agent-control.schema.json。$AGENT_CONTROL_DIR（既定 ~/.agents/control/）の control.json
# に管理面が「望ましい状態」を書き、各エンジンが mtime を見て pull で適用する（push 型 IPC なし）。
# 優先順位 control > CLI 引数 > 設定ファイル > 組み込み既定。適用状況は status/<tool>-<pid>.json へ。
_CONTROL_CACHE = {"mtime": None, "data": {}}


def _control_dir() -> str:
    return os.path.abspath(agent_home_subdir("AGENT_CONTROL_DIR", "control"))


def _load_control() -> dict:
    """control.json を mtime キャッシュ付きで読む。無ければ {}。"""
    path = os.path.join(_control_dir(), "control.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _CONTROL_CACHE["mtime"], _CONTROL_CACHE["data"] = None, {}
        return {}
    if _CONTROL_CACHE["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                _CONTROL_CACHE["data"] = json.load(f) or {}
        except (OSError, ValueError):
            _CONTROL_CACHE["data"] = {}
        _CONTROL_CACHE["mtime"] = mtime
    return _CONTROL_CACHE["data"]


def _control_workload() -> dict:
    return dict((_load_control().get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {})


def _control_lifecycle() -> str:
    """このワークロードの望ましい lifecycle（run|pause|stop）。既定 run。"""
    return str(_control_workload().get("lifecycle") or "run")


def _control_override(key: str = "") -> "tuple[str | None, str | None]":
    """(agent_cli, model) の上書き。解決 workloads[wl].agents[key] > workloads[wl] > defaults。"""
    ctl = _load_control()
    wl = _control_workload()
    agents = wl.get("agents") or {}
    layers = ([agents.get(key) or {}] if key else []) + [wl, ctl.get("defaults") or {}]
    cli = model = None
    for layer in layers:
        if cli is None and layer.get("agent_cli"):
            cli = str(layer.get("agent_cli"))
        if model is None and layer.get("model"):
            model = str(layer.get("model"))
    return cli, model


def _control_degraded() -> "tuple[str | None, str | None]":
    d = _control_workload().get("degraded") or {}
    return (str(d["agent_cli"]) if d.get("agent_cli") else None,
            str(d["model"]) if d.get("model") else None)


# run meta の execution_envelope（agent-project が計画承認時に snapshot する承認済み契約）。
# ワーカーが claim 時に据え、candidate_permissions（pins / trials / tier_ceiling_override /
# retry_limit）だけを Resolver の明示固定として解釈する。scope / 受入条件は監査用のまま。
_EXECUTION_ENVELOPE: dict = {}


def _set_execution_envelope(meta) -> None:
    """run meta から承認済み Execution Envelope を実行文脈へ据える（無ければ空）。"""
    global _EXECUTION_ENVELOPE
    envelope = (meta or {}).get("execution_envelope")
    approved = (isinstance(envelope, dict)
                and (envelope.get("approval") or {}).get("status") == "approved")
    _EXECUTION_ENVELOPE = envelope if approved else {}


def _envelope_pin(purpose: str) -> "dict | None":
    """Envelope の candidate_permissions → Resolver の explicit_pin（該当なしは None）。

    - pins: 明示固定。policy の適格候補か、trials にも載る候補だけ実行できる
      （執行は Resolver——ここは写像だけ）。
    - trials: pin が無くても、その候補を「この run 限定の trial」として固定する
      ——trial は走らないと実測が貯まらず昇格できない（E5 の入口）。
    - 項目の purpose / kind でロールを絞れる（省略 = 全ロール）。
    """
    perms = (_EXECUTION_ENVELOPE or {}).get("candidate_permissions") or {}
    pins = [p for p in (perms.get("pins") or []) if isinstance(p, dict)]
    trials = [t for t in (perms.get("trials") or []) if isinstance(t, dict)]

    def usable(item):
        scope = str(item.get("purpose") or item.get("kind") or "").strip()
        return (item.get("agent_cli") and item.get("model")
                and (not scope or scope == purpose))

    chosen = next((p for p in pins if usable(p)), None)
    trial_entry = chosen is None
    if chosen is None:
        chosen = next((t for t in trials if usable(t)), None)
    if chosen is None:
        return None
    pin = {"agent_cli": str(chosen["agent_cli"]), "model": str(chosen["model"])}
    if chosen.get("tier"):
        pin["tier"] = str(chosen["tier"])
    override = str(perms.get("tier_ceiling_override") or "")
    if override:
        pin["tier_ceiling_override"] = override
    retry = perms.get("retry_limit")
    if isinstance(retry, int) and not isinstance(retry, bool):
        pin["retry_limit"] = retry
    if trial_entry or any(t.get("agent_cli") == pin["agent_cli"]
                          and t.get("model") == pin["model"] for t in trials):
        pin["trial_approved"] = True
    return pin


# 候補ごとの失敗 attempt（candidate_id → 回数）。transient を使い切った候補をここへ記録し、
# Resolver が retry_limit 到達で次候補（縮退先）を選べるようにする（設計 §13「attempt は候補ごとに
# 数え、fallback 先は attempt 1 から始める」）。12b 検証役の停止性（2/27・計画 2026-08-22 §4.3 B3）
# の縮退基準「再投入後も続いたら e4b」は、policy の retry_limit と候補順がこの登録簿で効く形。
# 登録簿の寿命は **run × control revision**（設計「その run では再選択しない」）。run が変わる
# （worker が別 run のノードを claim する＝ _METHOD_RUN_ID が変わる）か control の revision が
# 上がれば消える——長命 worker で「一度落ちた候補が回復しても二度と選ばれない」天井を作らない。
# ponytail: run 単位の永続化はしない（同じ run を別 worker が拾えば登録簿は空から）。
_CANDIDATE_ATTEMPTS: "dict[str, int]" = {}
_CANDIDATE_ATTEMPTS_SCOPE = None
_LAST_FALLBACK: "dict[str, dict]" = {}     # purpose → {"from": cid, "to": cid}（直近の縮退）


def _attempt_counts_for(ctl: dict) -> dict:
    global _CANDIDATE_ATTEMPTS_SCOPE
    scope = (ctl.get("revision"), globals().get("_METHOD_RUN_ID", ""))
    if scope != _CANDIDATE_ATTEMPTS_SCOPE:
        _CANDIDATE_ATTEMPTS.clear()
        _LAST_FALLBACK.clear()
        _CANDIDATE_ATTEMPTS_SCOPE = scope
    return dict(_CANDIDATE_ATTEMPTS)


def last_execution_fallback(purpose: str) -> "dict | None":
    """この purpose の直近の呼び出しで縮退が起きたか（{"from", "to"}）。result の記録用。"""
    return _LAST_FALLBACK.get(purpose)


def _control_policy_decision(purpose: str) -> "dict | None":
    """selection_policy（agent-control version 2）があるときの Resolver 決定。無ければ None。

    候補の解決は agentcore.executionresolver の 1 実装（E1）。ここは control と
    Envelope の明示固定を渡すだけで、順位・除外・park の判断を複製しない。
    version 1（または selection_policy 無し）は旧 reader として従来の
    `_control_override` 経路へ委ね、version >= 2 は壊れた policy・未知 version でも
    Resolver が park を返す——legacy fallback を再解釈しない（設計 §6.6）。
    """
    ctl = _load_control()
    version = ctl.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 2:
        return None
    if not isinstance(_control_workload().get("selection_policy"), dict):
        return None
    return _executionresolver.resolve_execution(
        _NODE_BUDGET_WORKLOAD, purpose_or_role=purpose,
        explicit_pin=_envelope_pin(purpose), compiled_control=ctl,
        attempt_counts=_attempt_counts_for(ctl),
        now=datetime.now(timezone.utc))


def _candidate_fallback(purpose: str, decision: "dict | None") -> "dict | None":
    """transient を使い切った候補を登録簿へ記し、Resolver が別候補を返すならそれを返す。

    返り値は新しい decision（selected が前と違う）か None（縮退先なし＝そのまま失敗）。
    判断は Resolver に任せる——ここで fallback_candidates を直接引くと順位・除外の規則が
    2 実装になる。pin（Envelope の明示固定）は Resolver 側で retry-exhausted → park になる。
    """
    selected = (decision or {}).get("selected")
    if not selected:
        return None
    cid = _executioncontract.candidate_id(str(selected["agent_cli"]), str(selected["model"]))
    retry_limit = int((decision or {}).get("retry_limit") or 0)
    _attempt_counts_for(_load_control())
    _CANDIDATE_ATTEMPTS[cid] = max(_CANDIDATE_ATTEMPTS.get(cid, 0), retry_limit + 1)
    again = _control_policy_decision(purpose)
    nxt = (again or {}).get("selected")
    if not nxt:
        return None
    nxt_id = _executioncontract.candidate_id(str(nxt["agent_cli"]), str(nxt["model"]))
    if nxt_id == cid:
        return None
    _LAST_FALLBACK[purpose] = {"from": cid, "to": nxt_id}
    return again


def _control_concurrency() -> dict:
    """同時実行数の上書き（`workloads.flow.concurrency`）。宣言された整数キーだけを返す。

    この PC が同時にどれだけ走らせてよいかは**そのノードの資源の話**で、設定ファイル
    （`max_runs` / `workers`）は各プロジェクトのルートに散っている。1 台の負荷を下げたい
    人が全プロジェクトの yaml を直して回ることになっていたので、管理面（dashboard の
    全体設定）から 1 か所で宣言できるようにする。**エンジンは control を読むだけ**で、
    選択の知能は管理面に置く（agent-profiles と同じ分業）。

    壊れた値（負数・数値でない・workers=0＝ワーカー無し）は宣言なしとして無視する
    ——GUI の入力ミスで run が誰にも進められなくなる方が、上書きが効かないより高くつく。
    """
    raw = _control_workload().get("concurrency")
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, floor in (("max_runs", 0), ("workers", 1)):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = int(value)
        if number >= floor:
            out[key] = number
    return out


def control_max_runs(fallback: int) -> int:
    """同時に実行する run の上限（control > CLI 引数 > 設定ファイル > 既定）。0 以下で無制限。"""
    return int(_control_concurrency().get("max_runs", fallback) or 0)


def control_workers(fallback: int) -> int:
    """run 1 本あたりの worker 数（control > CLI 引数 > 設定ファイル > 既定）。"""
    return int(_control_concurrency().get("workers", fallback))


def _selection_meta(purpose: str = "", agent: "dict | None" = None) -> dict:
    wl = _control_workload()
    run_ov = _execution_override(purpose)
    tier = str(run_ov.get("tier") or (agent or {}).get("tier") or wl.get("tier") or "")
    purpose_control = (wl.get("agents") or {}).get(purpose) if purpose else None
    decision = None if (run_ov or agent) else _control_policy_decision(purpose)
    if run_ov:
        source = run_ov["source"]
    elif agent and agent.get("tier"):
        source = "pinned-tier"
    elif agent:
        source = "pinned-agent"
    elif decision is not None:
        # 候補ベースの決定。読み手（dashboard / audit）が設定から再推測しないよう、
        # receipt v2 の execution_decision ブロックを事実としてそのまま残す（§6.5）。
        # park は選択が無いので block を載せない（park_reason は selection_source で読める）。
        source = decision.get("selection_source") or "parked"
        meta = {"tier": tier or None, "selection_source": source,
                "selection_reason": decision.get("reason") or "",
                "pinned": source in ("explicit-pin", "trial-candidate")}
        if decision.get("selected"):
            meta["execution_decision"] = _executionresolver.receipt_execution_decision(decision)
        return meta
    elif purpose_control:
        source = "control-purpose"
    else:
        source = wl.get("selection_source") or (
            "control-workload" if wl.get("agent_cli") or wl.get("model") else "tool-config")
    return {"tier": tier or None, "selection_source": source,
            "selection_reason": wl.get("selection_reason") or "", "pinned": bool(run_ov or agent)}


def _write_status(effective_cli: str = "", effective_model: str = "", lifecycle: str = "run",
                  budget: "dict | None" = None, fresh_after_sec: int = 120,
                  purpose: str = "", pinned: bool = False, tier: str = "") -> None:
    """status/<tool>-<pid>.json へ適用状況ハートビートを原子書換する（best-effort）。"""
    ctl = _load_control()
    d = os.path.join(_control_dir(), "status")
    try:
        os.makedirs(d, exist_ok=True)
        meta = _selection_meta(
            purpose, ({"tier": tier} if tier else {"agent_cli": "pinned"}) if pinned else None)
        rec = {"tool": _NODE_BUDGET_TOOL, "workload": _NODE_BUDGET_WORKLOAD,
               "pid": os.getpid(), "lifecycle": lifecycle,
               "effective": {"agent_cli": effective_cli or None, "model": effective_model or None,
                             **meta},
               "fresh_after_sec": fresh_after_sec, "ts": now_iso()}
        if ctl.get("revision") is not None:
            rec["revision_applied"] = ctl.get("revision")
        # グローバル指示（agent-instructions）: ワーカーが注入した run スナップショットの revision。
        # dashboard が instructions.revision と突き合わせ未反映を可視化する（agent-control status へ相乗り）。
        if _INSTRUCTIONS_REV_APPLIED is not None:
            rec["instructions_revision_applied"] = _INSTRUCTIONS_REV_APPLIED
        # セッション開始コマンド: このワーカープロセスの起動時に適用した revision（未適用は省略）。
        if _SESSION_COMMANDS_REV_APPLIED is not None:
            rec["session_commands_revision_applied"] = _SESSION_COMMANDS_REV_APPLIED
        if budget is not None:
            rec["budget"] = {"exceeded": bool(budget.get("exceeded")),
                             "soft": bool(budget.get("soft"))}
        target = os.path.join(d, f"{_NODE_BUDGET_TOOL}-{os.getpid()}.json")
        tmp = target + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass


# --- 失敗トリアージ（決定的） -------------------------------------------------------------
# エラー本文から「誰が直すか」を分類し、メッセージ先頭の機械可読タグ [agent-error:<class>] で運ぶ。
# agent-flow は run の打ち切り（環境要因なら全ノードでリトライを焼かない）、agent-project は
# リトライ節約と人への説明、viewer は行動提示に同じ判定を使う。
#   control=管理設定による停止（明示的に run へ戻すまで継続）/ quota=利用上限（時間をおけば回復）/
#   auth=認証切れ（人が直す）/ env=実行環境の問題（人が直す）/ transient=一時的。
AGENT_ERROR_ENV_CLASSES = ("control", "quota", "auth", "env")
_AGENT_ERROR_TAG_RE = re.compile(r"\[agent-error:(control|quota|auth|env|transient|integration)\]")
_AGENT_ERROR_PATTERNS = (
    ("control", re.compile(r"\[agent-control\]", re.I),
     "管理設定で実行が停止されています（dashboard で実行を許可してください）"),
    ("quota", re.compile(r"usage limit|quota exceeded|rate.?limit|too many requests", re.I),
     "利用上限に達しています（時間をおくか、プラン・クレジットを見直してください）"),
    ("auth", re.compile(r"AccessDenied|Unauthorized|authentication failed|not authenticated"
                        r"|SendMessageError|please (re)?login", re.I),
     "認証に失敗しています（再ログインが必要です）"),
    ("env", re.compile(r"issue with the selected model|invalid model"
                       r"|model .{0,40}(not found|does not exist)|may not have access to it"
                       r"|command not found|No such file or directory", re.I),
     "実行環境の問題です（モデル名・CLI の導入・PATH を確認してください）"),
    ("transient", re.compile(r"timed? ?out|connection (reset|refused|closed)|ECONNRESET"
                             r"|ETIMEDOUT|temporarily unavailable|service unavailable|overloaded",
                             re.I),
     "一時的なエラーです（自動でやり直します）"),
)


# 発生元マーカー → 分類。マーカーは止めた本人（raise した箇所）が書くので、外側の層が
# 後から載せたタグより確かな証拠になる。タグを無条件に正とすると、内側で付いた分類が
# 外へ運ばれ続けて上書きできない——実際 [agent-control] による停止が quota として運ばれ、
# 画面は「利用上限です。時間をおいてください」と表示した。必要な操作は「実行を run に
# 戻す」で、待っても永久に回復しない。マーカーがあればそれを先に見る。
_AGENT_ERROR_SOURCE_CLASSES = (
    ("[agent-control]", "control"),
    ("[node-budget]", "quota"),
)


def _source_marker_class(text: str) -> "str | None":
    """本文中の発生元マーカーから分類を引く（無ければ None）。"""
    return next((cls for marker, cls in _AGENT_ERROR_SOURCE_CLASSES if marker in text), None)


def agent_error_chain(blob: str) -> "list[str]":
    """本文から観測できる分類を**すべて**、確からしい順に返す（該当なしは空）。

    層をまたぐ間に分類は複数載る（内側が付けたタグの外側にマーカーが増える等）。
    先頭だけ残して他を捨てると、後から「本当は何が起きていたか」を復元できない——
    実際 quota タグと [agent-control] マーカーが同居した記録で、捨てた側が正しかった。
    先頭が proximate cause（表示・行動提示に使う）で、残りは根拠として保持する。"""
    text = str(blob or "")
    chain: "list[str]" = []
    marker = _source_marker_class(text)
    if marker:
        chain.append(marker)
    for m in _AGENT_ERROR_TAG_RE.finditer(text):
        if m.group(1) not in chain:
            chain.append(m.group(1))
    if not chain:
        for cls, pat, _ in _plugin_error_patterns() + _AGENT_ERROR_PATTERNS:
            if pat.search(text) and cls not in chain:
                chain.append(cls)
    return chain


def _cli_error_patterns(cli: str) -> tuple:
    """その CLI 自身の errors[] だけ（読めなければ空）。"""
    if not cli:
        return ()
    try:
        return tuple(_agentcli.load_cli(str(cli)).get("errors") or ())
    except Exception:  # noqa: BLE001 — 定義が読めなくても分類は続ける
        return ()


def classify_agent_failure(blob: str, cli: str = "") -> "tuple[str, str] | None":
    """エラー本文を (class, hint) に分類する（該当なしは None＝内容の問題）。
    発生元マーカー > [agent-error:] タグ > プラグイン定義 > 汎用パターン の順に見る。
    全分類が要るときは agent_error_chain を使う（ここは先頭＝proximate cause だけ返す）。"""
    chain = agent_error_chain(blob)
    if not chain:
        return None
    cls = chain[0]
    text = str(blob or "")
    # ヒントは「**実際に一致した規則**」から採る。クラスだけで引くと、読み込み済みの別 CLI 定義に
    # 同じクラスの規則があるとその文言が出る（codex の usage limit に kiro の月間上限の案内が
    # 付く、という取り違えが実際に起きた）。一致する規則が無いクラス（[agent-error:] タグや
    # 発生源マーカー由来）だけ、従来どおりクラス一致の汎用ヒントへ落とす。
    #
    # **実行した CLI が分かるときは、その定義の規則を先に見る。** 一致した規則から採るだけでは
    # まだ足りない——複数の CLI が「usage limit」のような同じ語を拾う規則を持つと、どれが先に
    # 当たるかが「プラグインキャッシュに何が載っているか」＝実行順で決まってしまう。
    rules = _cli_error_patterns(cli) + _plugin_error_patterns() + _AGENT_ERROR_PATTERNS
    hint = next((h for c, pat, h in rules if c == cls and pat.search(text)), "")
    if not hint:
        hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == cls), "")
    return cls, hint


def _agent_failure(cli: str, rc: int, out: str, err: str) -> str:
    """エージェント CLI の失敗を、人が原因に辿り着ける文言にする。

    CLI は起動バナー（workdir / model / プロンプト全文）を stderr へ流す。先頭だけを切り取ると
    肝心のエラーがバナーに埋もれて消える — 実際 codex の「利用上限に達した」を丸ごと取り逃し、
    全ノードが理由不明の failed になった。エラーは末尾に出るので末尾を拾い、分類（トリアージ）は
    機械可読タグとして先頭に載せる。"""
    blob = f"{out or ''}\n{err or ''}"
    triage = classify_agent_failure(blob, cli)
    head = f"{cli} 失敗 (rc={rc})"
    if triage:
        cls, hint = triage
        head = f"[agent-error:{cls}] {head}" + (f": {hint}" if hint else "")
    tail = (err or out or "").strip()
    return f"{head}\n{tail[-500:]}" if tail else head


def run_agent(prompt: str, model: str | None, purpose: str = "", cwd: "str | None" = None,
              agent: "dict | None" = None, files: "list[str] | None" = None,
              read_files: "list[str] | None" = None,
              readonly: "bool | None" = None) -> str:
    """エージェント CLI を呼び出してテキスト応答を返す（このツールの全 LLM 呼び出しの単一チョーク
    ポイント: planner / evaluator / executor / verify / 裁定）。

    `agent`（`{agent_cli, model, timeout_sec}`）はこの呼び出し 1 回だけの明示指定で、設定・
    agent-control・縮退より**強い**。検証計画がタスク単位で「これで確かめてくれ」と言うための
    口で、ノード全体の設定に負けては用を成さない（設計:
    docs/plans/2026-08-09-verification-settlement-design.md §4）。

    レイヤ1（自己回復リトライ）: 失敗が transient 分類（接続断・5xx・overloaded・timeout）なら、
    ここで指数バックオフ再試行して上位層（グラフ再計画の retries 予算）へ持ち上げない。
    control/quota/auth/env・内容の問題（タグ無し）は再試行せず即座に上位へ（従来どおり）。
    実行中は worker の Heartbeat が claim lease を延長し続けるため、再試行で実行が延びても
    分散環境で横取りされない。試行し尽くした失敗は例外に attempts 属性を載せて raise する
    （worker が data.attempts として failed result に構造化する）。"""
    # agent-control: このワークロードが pause/stop 指定なら新規実行を控える（環境要因として運ぶ）。
    lifecycle = _control_lifecycle()
    if lifecycle in ("pause", "stop"):
        _write_status(lifecycle=lifecycle)
        raise RuntimeError(
            f"[agent-error:control] [agent-control] このワークロード（flow）は管理面により "
            f"lifecycle={lifecycle} 指定です。dashboard のオーケストレーションタブで run に戻して"
            "ください")
    nb = _node_budget_state()
    # 超過かつ on_exhausted != degrade なら控える。degrade は縮退指定で継続（_agent_for が適用）。
    if nb and nb["exceeded"] and nb.get("on_exhausted") != "degrade":
        _write_status(lifecycle=lifecycle, budget=nb)
        unit = ("トークン" if nb.get("token_limit") else "実行時間")
        raise RuntimeError(
            f"[agent-error:quota] [node-budget] このノードの{unit}予算を超過しています"
            f"（{nb['spent_min']:.1f}分/{nb['limit_min']:.0f}分・"
            f"{nb['spent_tokens']:.0f}tok/{nb['token_limit']:.0f}tok・period={nb['period']}）。"
            "上限を上げる（dashboard のオーケストレーションタブ / agent-amigos budget node）か"
            "期間の更新を待ってください")
    # 候補ベース（selection_policy）の park。呼び出し 1 回の明示指定（verification 等の
    # `agent`）と run 固定は人の承認済み指定なので止めない。park は lifecycle と同じ
    # 環境要因として運ぶ——弱い候補へ黙って降格せず、run 単位の打ち切りへ載せる（§5.2）。
    if not (agent and (agent.get("agent_cli") or agent.get("model"))) \
            and not _execution_override(purpose):
        decision = _control_policy_decision(purpose)
        if decision is not None and decision.get("parked"):
            _write_status(lifecycle=lifecycle, budget=nb)
            raise RuntimeError(
                f"[agent-error:control] [selection-policy] park"
                f"（{decision.get('park_reason')}）: {decision.get('reason')}。"
                f"再開条件: {decision.get('resume_condition')}")
    cli_used, model_used = _effective_agent(purpose, model, agent)
    prompt = _apply_methods(prompt, purpose, cli_used, model_used,
                            str((agent or {}).get("tier") or ""))
    _write_status(effective_cli=cli_used, effective_model=model_used or "",
                  lifecycle=lifecycle, budget=nb, purpose=purpose,
                  pinned=bool(agent), tier=str((agent or {}).get("tier") or ""))
    # 候補ベースの呼び出し（明示指定も run 固定も無い）だけが縮退の対象。直近の縮退記録は
    # 呼び出しごとに消す（前の呼び出しの縮退を今回の result に付けない）。
    policy_driven = not (agent and (agent.get("agent_cli") or agent.get("model"))) \
        and not _execution_override(purpose)
    _LAST_FALLBACK.pop(purpose, None)
    last: "RuntimeError | None" = None
    empty_fixes = 0
    attempt = 0
    while attempt <= max(0, _TRANSIENT_RETRIES):
        try:
            t0 = time.monotonic()
            file_args = {"read_files": read_files} if read_files else {}
            text = _run_agent_once(prompt, model, purpose, cwd, agent=agent, files=files,
                                   readonly=readonly, **file_args)
            _node_budget_record(time.monotonic() - t0, ref=purpose or "worker",
                                agent_cli=cli_used, model=model_used or "",
                                tokens_in=getattr(text, "tokens_in", None),
                                tokens_out=getattr(text, "tokens_out", None),
                                extra=_method_ledger_fields(purpose))
            return text
        except EmptyOutputError as e:
            # JSON 契約の役割にとって空応答は形式違反であって内容の失敗ではない。契約を
            # 言い直して呼び直す（レイヤ2 と同じ考え方だが、パース前に落ちるぶんここで拾う）。
            # 予算は transient とは別枠 _FORMAT_RETRIES で有界（C7: 必ず止まる）。
            if purpose in JSON_CONTRACT_ROLES and empty_fixes < max(0, _FORMAT_RETRIES):
                empty_fixes += 1
                log("agent", f"空応答を形式違反として再要求 #{empty_fixes}/{_FORMAT_RETRIES}"
                             f"（purpose={purpose}）")
                prompt = f"{prompt}\n\n{_EMPTY_OUTPUT_NUDGE}"
                continue
            if empty_fixes:
                e = EmptyOutputError(f"{e}（形式を言い直して {empty_fixes} 回再要求後）")
            # 空応答は**内容の失敗ではない**（ツールループ型の CLI が制御語だけを返す・
            # 思考だけで本文を出さない）。分類の付いていない空応答を内容の失敗として上げると、
            # 再計画がこれを「実装の失敗」と読んで計画そのものを壊す（実際 agent-ollama の
            # 空応答から push 待機タスクが捏造された）。既知の分類（認証切れ等）が付いて
            # いなければ transient として運び、run 単位の打ち切り → cooldown 後の auto-heal
            # （done は温存）へ載せる。ここで再試行を足さないのは、空応答の再試行は同じ
            # プロンプトの投げ直しで、遅いローカル LLM では壁時計だけを焼くため。
            if classify_agent_failure(str(e)) is None:
                e = EmptyOutputError(f"[agent-error:transient] {e}")
            e.attempts = attempt + 1  # type: ignore[attr-defined]
            raise e
        except RuntimeError as e:
            triage = classify_agent_failure(str(e))
            if triage and triage[0] == "quota":
                _record_quota_observation(cli_used, str(e))
            if triage is not None and triage[0] == "transient" and attempt >= _TRANSIENT_RETRIES \
                    and policy_driven:
                # レイヤ1 を使い切った。候補ベースなら Resolver に次候補（縮退先）を訊く。
                # 停止性の問題（12b の生成暴走）は候補を替えれば止まる性質で、同じ候補を
                # 叩き続けるより弱くて止まる候補へ下りる方が run 全体は前へ進む。
                # 縮退先でも transient 上限は attempt 1 から数え直す（設計 §13）。
                again = _candidate_fallback(purpose, _control_policy_decision(purpose))
                if again is not None:
                    cli_used, model_used = _effective_agent(purpose, model, agent)
                    log("agent", f"候補を縮退: {_LAST_FALLBACK[purpose]['from']} → "
                                 f"{_LAST_FALLBACK[purpose]['to']}（transient "
                                 f"{attempt + 1} 回・purpose={purpose or 'worker'}）: {str(e)[:120]}")
                    _write_status(effective_cli=cli_used, effective_model=model_used or "",
                                  lifecycle=lifecycle, budget=nb, purpose=purpose)
                    attempt = 0
                    last = e
                    continue
            if triage is None or triage[0] != "transient" or attempt >= _TRANSIENT_RETRIES:
                if attempt > 0:  # レイヤ1 を経たことを上位・人が読めるようにする
                    e = RuntimeError(f"{e}（{attempt + 1} 回試行後）")
                e.attempts = attempt + 1  # type: ignore[attr-defined]
                raise e
            wait = _TRANSIENT_BACKOFF * (2 ** attempt) + random.uniform(0, 1.0)
            log("agent", f"transient エラーを再試行 #{attempt + 1}/{_TRANSIENT_RETRIES}"
                         f"（{wait:.0f}s 待機・purpose={purpose or 'worker'}）: {str(e)[:120]}")
            backoff_sleep(wait)
            last = e
            attempt += 1
    raise last if last else RuntimeError("run_agent: unreachable")  # pragma: no cover


def _effective_agent(purpose: str, model: "str | None",
                     agent: "dict | None" = None) -> "tuple[str, str | None]":
    """この呼び出しで実際に使う (agent_cli, model)。呼び出し 1 回の明示指定が最優先。

    解決の実装を 1 か所に置く——run_agent（台帳と status に何を記録するか）と
    _run_agent_once（実際に何を起動するか）が別々に解くと、記録と実行がずれる。"""
    cli, model_ov = _agent_for(purpose)
    model = model_ov or model
    run_ov = _execution_override(purpose)
    if agent:
        if not run_ov.get("agent_cli"):
            cli = str(agent.get("agent_cli") or cli).strip().lower()
        if not run_ov.get("model") and agent.get("model"):
            model = str(agent["model"]).strip()
    return cli, model


def _run_agent_once(prompt: str, model: str | None, purpose: str = "",
                    cwd: "str | None" = None, agent: "dict | None" = None,
                    files: "list[str] | None" = None,
                    read_files: "list[str] | None" = None,
                    readonly: "bool | None" = None) -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    purpose（planner / evaluator / ノード kind）を渡すと設定 agents: の役割毎上書きが効く
    （kind は agents["worker"] へフォールバック）。model は 上書き ＞ 呼び出し値。
    `agent` はこの呼び出しだけの明示指定で、設定・control・縮退のどれよりも強い。"""
    cli, model = _effective_agent(purpose, model, agent)
    plug = load_agent_plugin(cli)
    # プロンプトが argv 渡しで長すぎるときは一時ファイルへ退避し、「そのファイルを読んで実行」の
    # 短い指示に置き換える（成果物の受け渡しを参照渡しにする）。argv 長制限は OS の事情なので
    # CLI 定義ではなくここで見る——定義側の spill は「stdin を読まない CLI の癖」への対処で別物
    # （権限フラグを fs_read へ置き換えるため、実行して確かめる呼び出しには使えない）。
    # 退避そのものは agentcore.agentcli の 1 実装（agent-project / agent-amigos と共有）。
    spill, prompt = _agentcli.spill_prompt(
        prompt, _agent_argv_limit(), prompt_via=plug["prompt_via"],
        prefix="agent-flow-prompt-",
        # 枠は agentcore の 1 実装（P2-5）。ここが決めるのは「何の全文か」だけ。
        instruction=_agentcli.spill_instruction(
            "このタスクの全文（依存タスクの成果物を含む）",
            then="その指示に従ってタスクを実行してください"))
    # 読込割付のパスは本文だけでなく argv でも渡す。「チャットに入っているファイルしか
    # 編集しない」CLI（aider）は、本文に書いてあっても着手できない——定義が file_flag を
    # 宣言していなければ 1 トークンも増えないので、他の CLI の起動形は変わらない。
    use_readonly = _agent_readonly(purpose) if readonly is None else bool(readonly)
    built = _agentcli.headless_cmd(plug, model, prompt, readonly=use_readonly,
                                   files=files or (), read_files=read_files or ())
    cmd, stdin_text, out_file = built["argv"], built["stdin"], built["output_file"]
    # 発生源で色を抑止（NO_COLOR/TERM=dumb）。残った ANSI は strip_ansi で除去する二段構え
    # （agent-project と同じ扱い）。定義の env は最後に載せるので上書きできる。
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", **(plug.get("env") or {})}
    timeout = _agent_timeout(purpose, plug.get("timeout"))
    if agent and agent.get("timeout_sec"):
        try:
            explicit = float(agent["timeout_sec"])
        except (TypeError, ValueError):
            explicit = 0.0
        if explicit > 0:
            timeout = explicit
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin_text,
                              timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        # 失敗として上位へ。ハングは一時的な公算が高いので transient タグを明示付与し、
        # レイヤ1（in-place 再試行）の対象にする（従来は日本語文言が英語の transient パターンに
        # 掛からず「内容の問題」扱い＝再計画 retry の予算を焼いていた）。恒久ハングでも
        # 試行ごとに本タイムアウトで有界。
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
        label = f"{timeout:.0f}s" if timeout is not None else "上限なし"
        raise RuntimeError(f"[agent-error:transient] {cmd[0]} タイムアウト（{label}）")
    finally:
        if spill:
            with contextlib.suppress(OSError):
                os.remove(spill)
    try:
        if proc.returncode != 0:
            raise RuntimeError(_agent_failure(cmd[0], proc.returncode, proc.stdout, proc.stderr))
        text = strip_ansi(proc.stdout).strip()
        if out_file:   # codex 等: 最終応答ファイルが取れればそれを正とする（stdout はイベントログ）
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
        if not text and not plug.get("empty_output_is_error", True):
            return ""
        if not text:
            # rc=0 でも本文が空で返る CLI がある（kiro-cli は AWS 認証が切れるとバナーだけ出して
            # rc=0 で終わる）。空を成功として扱うと、worker は「空の成果物で done」、planner は
            # stub 戦略へ黙って落ちる＝LLM を呼べていないのに動いているように見える。失敗にする。
            # 専用の型で投げるのは、呼び出し側が**文言を読み直さずに**空応答だと判別できる
            # ようにするため（JSON 契約の役割はここから形式の言い直しへ回す）。
            raise EmptyOutputError(_agent_failure(cmd[0], 0, proc.stdout, proc.stderr)
                                   .replace("失敗 (rc=0)", "が空の応答を返しました (rc=0)"))
        tokens_in, tokens_out = _agentcli.parse_usage(proc.stderr or "")
        return _agentcli.UsageText(text, tokens_in, tokens_out)
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)


def _repair_json_output(prompt: str, bad_text: str, purpose: str, why,
                        model: "str | None" = None, want_list: bool = False,
                        agent: "dict | None" = None):
    """レイヤ2（形式修復リトライ）: LLM 応答が出力契約（JSON）を満たさないとき、
    「前回の出力はこう契約違反だった」と指摘して同じ役割で呼び直す（format_retries 回・有界）。
    Claude Dynamic Workflows の structured output 検証リトライの移植。寛容パーサ
    （extract_json / _normalize_verify 等）で救える崩れはそもそもここへ来ない。
    修復できたら解釈済み JSON を、できなければ None を返す（呼び出し側が従来のフォールバックへ）。"""
    contract = "JSON 配列" if want_list else "JSON"
    for _ in range(max(0, _FORMAT_RETRIES)):
        repair = (f"{prompt}\n\n[前回の出力は契約違反でした]\n"
                  f"前回の出力（先頭 400 文字）: {str(bad_text)[:400]}\n"
                  f"違反: {why}\n"
                  f"説明・前置き・コードフェンスを付けず、指示された {contract} だけを再出力してください。")
        try:
            bad_text = run_agent(repair, model, purpose=purpose, agent=agent)
            data = extract_list(bad_text) if want_list else extract_json(bad_text)
        except Exception as e:  # noqa: BLE001 — 修復呼び出し自体の失敗も「まだ壊れている」扱い
            why = str(e)
            continue
        log("agent", f"format repair 成功（purpose={purpose}）")
        return data
    return None


# dep_results は {dep_id: result_dict}（result_dict は output テキストと任意の data を持つ）。
# 実行結果は (text, data) を返す。data は構造化成果（JSON 可、無ければ None）。
def _dep_text(r: dict) -> str:
    return str((r or {}).get("output", ""))


def _dep_data(r: dict):
    return (r or {}).get("data")


def _stub_sleep() -> None:
    """stub の擬似実行時間。既定 1〜5 秒。設定ファイル `stub_sleep_max` で調整
    （テストや動作確認では 0 にして高速化できる）。設定が無ければ環境変数
    AGENT_FLOW_STUB_SLEEP_MAX → 既定 5 にフォールバックする。"""
    mx = _STUB_SLEEP_MAX
    if mx is None:
        try:
            mx = float(os.environ.get("AGENT_FLOW_STUB_SLEEP_MAX", "5"))
        except ValueError:
            mx = 5.0
    if mx > 0:
        time.sleep(random.uniform(min(1.0, mx), mx))


def execute_stub(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = "", readonly: bool = False):
    # repo_instruction（成果物リポジトリの clone 指示）は stub の判定に使わない（goal は本来の goal）。
    _stub_sleep()  # 実行時間を模す（AGENT_FLOW_STUB_SLEEP_MAX で調整可）
    # 失敗注入: "FAIL" を含むと失敗（retry される）/ "FLAKY" は一旦 issue を残す（verify loop 用）
    if "FAIL" in goal:
        raise RuntimeError(f"[stub] 意図的失敗: {goal}")
    # gate（verify の判定 {"ok":...}）は集約対象から除く
    def _is_gate(r):
        dv = _dep_data(r)
        return isinstance(dv, dict) and "ok" in dv
    agg = {d: r for d, r in dep_results.items() if not _is_gate(r)}
    texts = {d: _dep_text(r) for d, r in dep_results.items()}
    if kind == "split":
        # 入力をリストへ分解（データ駆動 fan-out の起点）。要素数は goal 中の数字 or 既定 3
        m = re.search(r"\d+", goal)
        k = max(1, min(int(m.group()) if m else 3, 8))
        items = [f"{goal[:30]} #{i + 1}" for i in range(k)]
        return f"[split] {k} 件に分解", items
    if kind == "classify":
        label = next((lbl for lbl in ("frontend", "backend", "security", "performance")
                      if lbl in goal.lower()), "general")
        return f"class={label}", {"label": label}
    if kind == "synthesize":
        return (f"[synth] {len(agg)} 件を統合: " + " | ".join(agg)[:80],
                {"merged": list(agg)})
    if kind == "filter":
        kept = [d for d, t in texts.items() if "FAIL" not in t and "issue" not in t]
        return f"[filter] 採用={','.join(kept)}", {"kept": kept}
    if kind == "judge":
        win = next(iter(dep_results), "")
        return f"[judge] winner={win}", {"winner": win}
    if kind == "verify":
        ok = all("issue" not in t and "fail" not in t.lower() for t in texts.values())
        return ("verify=pass" if ok else "verify=fail"), {"ok": ok}
    if kind == "reduce":
        # 依存の構造化 data を畳み込む（gate は除外。list は連結、その他は要素として収集）
        items = []
        for d, r in agg.items():
            dv = _dep_data(r)
            if isinstance(dv, list):
                items.extend(dv)
            elif dv is not None:
                items.append(dv)
            else:
                items.append(_dep_text(r))
        return f"[reduce] {len(items)} 件を集約", {"items": items, "count": len(items)}
    # work / generate
    if "FLAKY" in goal:
        return f"[stub] 未完(issue): {goal}", None
    return f"[stub] 完了: {goal}", None


# flow-worker スキルの prompt.py の解決結果メモ（プロセス内。未発見 = None も記憶する）。
_worker_skill_script: "dict[str, str | None]" = {}


def _flow_worker_prompt(payload: dict) -> "str | None":
    """flow-worker スキルのプロンプトビルダーを呼び、実行規律入りプロンプトを得る。

    flow-planner と同じ作戦: スキル未インストール・生成失敗なら None を返し、
    呼び出し側は組み込みプロンプトへフォールバックする（run を止めない）。
    ビルダーは決定的（LLM 無し）で、LLM 呼び出し・役割別ルーティングは従来どおり
    run_agent が担う。payload は stdin JSON 渡し（依存成果が大きくても ARG_MAX に当たらない）。"""
    skill = (_WORKER_SKILL or "").strip().lower()
    if not skill or skill in ("none", "builtin", "off"):
        return None
    if skill not in _worker_skill_script:
        _worker_skill_script[skill] = _find_skill_script(skill, "prompt.py")
    script = _worker_skill_script[skill]
    if not script:
        return None
    try:
        proc = subprocess.run([sys.executable, script],
                              input=json.dumps(payload, ensure_ascii=False, default=str),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:300])
        return proc.stdout.strip() or None
    except Exception:  # noqa: BLE001 — スキル失敗は組み込みプロンプトで続行
        return None


def execute_agent(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = "", workspace: "dict | None" = None,
                 references: "list[dict] | None" = None, request: str = "",
                 instructions: str = "", prompt_table: bool = False,
                 repair: "dict | None" = None, context: str = "",
                 read_allocation: "list[dict] | None" = None,
                 agent: "dict | None" = None, readonly: bool = False):
    role = {
        "classify": "分類役。入力を適切なカテゴリへ分類し『class=<ラベル>』形式で出力。",
        "synthesize": "統合役。依存タスクの成果を統合して 1 つの成果物にまとめる。",
        "filter": "選別役。依存の候補から基準を満たすものだけを残し、採用理由を述べる。",
        "judge": "審判役。依存の複数案を比較し最良案を選び理由を述べる。",
        "reduce": "集約役。依存タスクの構造化データを畳み込み、集約結果を JSON で出力。"
                  " 要素数を表す count を含める場合は、必ず集約後リストの実際の要素数と一致させること。",
        "split": "分解役。入力を独立に処理できる小片のリストへ分解し、"
                 "各要素を文字列とする JSON 配列のみを出力（例: [\"1-100\", \"101-200\"]）。"
                 " 説明文は付けず配列だけを返すこと。",
        "map": "map役。ゴールに示された本来のタスクを、与えられた1要素だけに適用して結果を返す。"
               " 勝手に別の処理（合計・件数など）に変えないこと。"
               " リスト状の成果は JSON 配列で出力し、後段の集約に渡せるようにする。",
        "extract": "抽出役。入力から指定項目を抜き出し、JSON "
                   '{"records":[{"fields":{},"evidence":[{"source_id":"...",'
                   '"locator":"...","excerpt":"..."}]}],"warnings":[]} のみを出力。'
                   "各 record に根拠を最低1件含め、該当なしは空の records とする。",
        "retrieve": "取得役。読み取り可能な道具で根拠を確認し、JSON "
                    '{"sources":[{"id":"...","uri":"...","title":"...",'
                    '"locator":"...","excerpt":"...","digest":"..."}],"warnings":[]} '
                    "のみを出力。推測で source を作らず、該当なしは空の sources とする。",
        "verify": "検証役。依存の成果を鵜呑みにせず独立に検算する。"
                  "可能なら結果を自分で再導出して突き合わせ、最低限"
                  "(1)件数・合計の整合 (2)抜け漏れ・重複 (3)各要素の妥当性の抜き取り検査"
                  " を行う。問題が無ければ『verify=pass』、あれば『verify=fail』と"
                  "具体的な該当箇所を出力し、末尾に JSON"
                  ' {"ok": true|false, "issues": ["..."]} を必ず添える。',
    }.get(kind, "ワーカー。次のタスクだけを完了し成果物を出力。")
    # 集約・選別系では gate（verify の判定）を入力から除く（成果物に紛れ込ませない）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    art_note = artifact_instruction(art_dir, dep_arts)
    repair_note = repair_instruction(repair)   # 案 B-1・オプトイン（repair=None なら空文字）
    read_note = render_read_allocation(read_allocation)
    # flow-worker スキルがあれば実行規律入りプロンプトを使う（無ければ従来の組み込み）。
    # 出力契約（verify の JSON・split の配列等）はスキル側でも同一に保たれている。
    prompt = _flow_worker_prompt({
        "role": "worker", "kind": kind, "goal": goal, "request": request,
        "deps": {d: {"output": _dep_text(r), "data": _dep_data(r)} for d, r in deps.items()},
        "repo_instruction": repo_instruction, "artifact_note": art_note,
        "workspace": workspace, "references": references or [],
        # グローバル指示（run スナップショットの描画済みブロック）。スキルが受け取り先頭へ前置する。
        "instructions": instructions,
        # 差分修復リトライのブリーフ（未対応スキルは未知キーとして無視するだけ＝壊れない）。
        "repair_note": repair_note,
        "read_note": read_note,
    })
    if not prompt:
        prompt = f"あなたは分散 Dynamic Workflow の{role}\nタスク({kind}): {goal}\n"
        if request:
            prompt += f"元の依頼:\n{request}\n"
        if repo_instruction:  # 成果物リポジトリの clone 指示（ローカル実行のエージェントへ伝える）
            prompt += repo_instruction + "\n"
        if art_note:  # 中間成果物のファイル参照プロトコル（出力先・依存成果物のパス）
            prompt += art_note + "\n"
        if repair_note:  # 前回の試行・差し戻し理由（全作り直しではなく指摘箇所の修復を促す）
            prompt += repair_note + "\n"
        if read_note:
            prompt += read_note + "\n"
        if deps:
            lines = []
            for d, r in deps.items():
                line = f"[{d}] {_dep_text(r)}"
                dv = _dep_data(r)
                if dv is not None:
                    # 案 K-1/K-2（docs/plans/2026-08-05-json-prompt-compression-study.md）:
                    # 常に compact で注入し、prompt_table（オプトイン）なら reduce/map の
                    # 均質な dict 配列（items 等）をさらに表形式へ畳む（内容は不変）。
                    rendered = (promptrender.render_table(dv) if prompt_table
                               else promptrender.dumps_prompt(dv))
                    line += f"\n  data: {rendered[:400]}"
                lines.append(line)
            prompt += "\n依存タスクの成果:\n" + "\n".join(lines) + "\n"
        prompt += "\n成果物を簡潔に直接出力してください。"
    # インストール済み flow-worker が新しい kind をまだ知らなくても、エンジン側の契約を優先する。
    marker = '"records"' if kind == "extract" else ('"sources"' if kind == "retrieve" else "")
    if marker and marker not in prompt:
        prompt += f"\n\n【出力契約】{role}"
    # 実行 tier（basic）の分解粒度指示は split だけに効かせる——fan-out の細かさは split の
    # 出力要素数が決め、展開（_expand_splits）自体は LLM を通らないため、動的 fan-out へ
    # tier 補償を届ける注入点はここしかない。continue_agent の評価指示と同じ流儀で
    # スキル/組み込みの両経路へ一律に後置する（スキルは tier を知らないため二重注入しない）。
    # tier の解決順も _method_context と同じ: ノード固定 tier ＞ agent-control の workload 宣言。
    if kind == "split":
        tier_note = tier_split_directive(str((agent or {}).get("tier") or "") or flow_tier())
        if tier_note:
            prompt = f"{prompt}\n\n{tier_note}"
    # プロジェクト文脈（案 H・オプトイン）を先に前置してから、グローバル指示をさらにその前へ
    # 前置する（最終順序: [instructions][context][goal/deps ...]）。context は毎回このプロンプト
    # 文字列を新規に組み立ててから 1 回だけ付けるので二重注入の心配は無い（instructions と違い
    # スキル側は context を知らない）。instructions 側は flow-worker スキルが既に前置していれば
    # （マーカー検出で）二重注入しない＝新旧どちらのスキルでも 1 回だけ効く（組み込み fallback でも同様）。
    prompt = _promptcompose.compose([context], [prompt])
    prompt = prepend_instructions(prompt, instructions)
    # 割付があるなら参照専用の argv へも回す。work/generate で明示 opt-in された大きい Python
    # 参照は一時的な symbol slice へ差し替え、判断を result data の receipt に残す。
    reference_cwd = next((str(ref.get("local")) for ref in (references or [])
                          if isinstance(ref, dict) and ref.get("local")
                          and os.path.isdir(str(ref.get("local")))), None)
    agent_cwd = (str(workspace["clone"]) if workspace and workspace.get("clone")
                 else reference_cwd if readonly else None)
    if kind in ("work", "generate"):
        alloc_files, slice_receipts, slice_cleanup = prepare_read_allocation_files(
            read_allocation, agent_cwd)
    else:
        alloc_files = [row["path"] for row in normalize_read_allocation(read_allocation)]
        slice_receipts, slice_cleanup = [], []
    try:
        text = run_agent(prompt, model, purpose=kind, cwd=agent_cwd, agent=agent,
                         read_files=alloc_files, readonly=readonly)
    except Exception as exc:
        if slice_receipts:
            current = getattr(exc, "data", None)
            exc.data = {**(current if isinstance(current, dict) else {}),
                        "context_slices": slice_receipts}
        raise
    finally:
        for temp_path in slice_cleanup:
            with contextlib.suppress(OSError):
                os.remove(temp_path)
    # 構造化データを意図する kind のみ JSON を抽出（自由記述の本文から JSON 風断片を
    # data に誤昇格させない）。
    data = None
    if kind in STRUCTURED_KINDS:
        try:
            data = extract_list(text) if kind == "split" else extract_json(text)
        except Exception as e:  # noqa: BLE001 — 構造化できなければテキストのみ
            data = None
            why = str(e)
        else:
            why = "JSON としては解釈できたが配列でない"
        # split は data が JSON 配列でないと fan-out（_expand_splits）が展開されず run が
        # 空振りする＝出力契約が固い。レイヤ2 の修復リトライで救う（verify/reduce は
        # _normalize_verify / _reconcile_count の寛容パーサがあるため修復不要）。
        if kind == "split" and not isinstance(data, list):
            repaired = _repair_json_output(prompt, text, kind, why, model, want_list=True, agent=agent)
            if isinstance(repaired, list):
                data = repaired
        elif kind in ("extract", "retrieve"):
            try:
                data = _nodecontract.validate_node_data(kind, data)
            except _nodecontract.NodeDataError as first_error:
                repaired = _repair_json_output(prompt, text, kind, str(first_error), model, agent=agent)
                try:
                    data = _nodecontract.validate_node_data(kind, repaired)
                except _nodecontract.NodeDataError as repair_error:
                    raise _nodecontract.NodeDataError(
                        f"{kind} の結果契約を修復できませんでした: {repair_error}") from repair_error
    elif kind in _ENVELOPE_KINDS:
        # 本文は自由記述のまま保つが、末尾の完了可否 envelope だけは機械判定へ渡す。
        # generate も対象にする: プロンプト側は実装系の全 kind（work / generate / map）へ
        # 「未完了なら {"ok": false} を付けろ」と指示しており、work だけ読んでいたため
        # generate ノードの自己申告した未完了が done として通っていた（map は
        # STRUCTURED_KINDS の JSON 抽出で拾われる）。
        # 本文中の JSON 例を誤採用しないよう、契約どおり末尾にある {"ok": ...} に限定する。
        matches = list(re.finditer(r'\{\s*"ok"\s*:', text))
        if matches:
            try:
                envelope = json.loads(text[matches[-1].start():].strip())
            except (ValueError, TypeError):
                envelope = None
            if isinstance(envelope, dict) and isinstance(envelope.get("ok"), bool):
                data = envelope
    if slice_receipts:
        data = {**(data if isinstance(data, dict) else {}),
                "context_slices": slice_receipts}
    if kind == "reduce":
        data = _reconcile_count(data)
    elif kind == "verify":
        data = _normalize_verify(text, data)
    return text, data
