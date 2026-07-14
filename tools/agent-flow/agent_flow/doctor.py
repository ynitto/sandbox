from __future__ import annotations
# doctor.py — 元 agent-flow.py の 5939-6272 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# doctor（稼働診断）— bus 上の run（meta/events/results）と環境から稼働状況を
#   kiro-cli に診断させ、原因を env（ユーザー環境固有）/ config（設定）/
#   program（プログラム上の不具合）へ分類する。env/config は --fix で修正、program は
#   gitlab-idd スキルでイシュー起票（無ければ出力のみ）。収集・修正・起票の駆動は決定的、
#   診断と分類は kiro-cli へ委譲する。`agent-flow doctor --json` は単独でも、
#   agent-project の doctor からの連携呼び出しでも使える（同一スキーマの findings を返す）。
# --------------------------------------------------------------------------
_DOCTOR_CATEGORIES = ("env", "config", "program")
_DOCTOR_SEVERITIES = ("critical", "warn", "info")
_DOCTOR_STUCK_HOURS = 2.0     # 非終端のまま放置された run を「滞留」とみなす目安（時間）
_DOCTOR_RECENT_RUNS = 10      # 診断で走査する直近 run 数


def _doctor_norm(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower()).strip()


def doctor_env_findings(args, which=shutil.which) -> "list[dict]":
    """環境/設定の決定的チェック（LLM 不要）。fix_action を持つものは --fix で修正できる。"""
    findings: list[dict] = []
    needs_cli = (getattr(args, "executor", "agent") == "agent"
                 or getattr(args, "planner", "") == "agent")
    agent_cli = str(getattr(args, "agent_cli", "kiro") or "kiro")
    agent_bin = _AGENT_CLI_BINARIES.get(agent_cli, agent_cli)
    if needs_cli and not which(agent_bin):
        findings.append({
            "category": "env", "severity": "critical",
            "title": f"{agent_bin} が PATH に見つからない",
            "evidence": (f"executor={getattr(args, 'executor', '?')} "
                         f"planner={getattr(args, 'planner', '?')} agent_cli={agent_cli} は "
                         f"{agent_bin} を要求する"),
            "fix": f"{agent_bin} をインストールして PATH を通す（暫定回避は --executor stub / --planner stub）"})
    if getattr(args, "git", None) and not which("git"):
        findings.append({
            "category": "env", "severity": "critical",
            "title": "git バスモードなのに git が見つからない",
            "evidence": f"git={args.git} の分散バスは git クローン/同期に git を使う",
            "fix": "git をインストールして PATH を通す（単一ノードなら --git を外す）"})
    bus_root = os.path.abspath(args.bus)
    parent = os.path.dirname(bus_root) or "."
    if not os.path.isdir(bus_root):
        findings.append({
            "category": "config", "severity": "info", "title": "バスのルートが未作成",
            "evidence": f"bus={bus_root}",
            "fix": "バスのルートを作成する（run 実行時にも自動作成される）",
            "fix_action": "ensure-bus"})
    elif not os.access(bus_root, os.W_OK):
        findings.append({
            "category": "env", "severity": "critical", "title": "バスのルートに書き込めない",
            "evidence": f"bus={bus_root} が書き込み不可",
            "fix": "バスのディレクトリの権限を修正するか、書き込める --bus を指定する"})
    if os.path.isdir(bus_root) and not os.access(parent, os.W_OK):
        findings.append({
            "category": "env", "severity": "warn", "title": "バスの親ディレクトリに書き込めない",
            "evidence": f"parent={parent}（一時ファイルの atomic 書き込みに影響）",
            "fix": "親ディレクトリの権限を確認する"})
    if int(getattr(args, "max_iterations", 3) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "critical", "title": "max_iterations が無限（≤0）",
            "evidence": f"max_iterations={getattr(args, 'max_iterations', None)}",
            "fix": "max_iterations を正の値にする（再計画の有限停止）"})
    if int(getattr(args, "max_retries", 3) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "warn", "title": "サーキットブレーカーが無効（max_retries≤0）",
            "evidence": f"max_retries={getattr(args, 'max_retries', None)}",
            "fix": "max_retries を正の値にする（達成不能な完了条件での無限作り直しを防ぐ）"})
    if float(getattr(args, "lease", 1800.0) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "warn", "title": "claim リースが非正（lease≤0）",
            "evidence": f"lease={getattr(args, 'lease', None)}",
            "fix": "lease を正の秒数にする（claim の横取り防止）"})
    if int(getattr(args, "argv_limit", 100000) or 0) <= 0:
        findings.append({
            "category": "config", "severity": "info", "title": "argv_limit が無効（≤0）",
            "evidence": f"argv_limit={getattr(args, 'argv_limit', None)}",
            "fix": "argv_limit を正のバイト数にする（大きなプロンプトの ARG_MAX 回避）"})
    return findings


def collect_doctor_signals(args) -> dict:
    """bus 上の直近 run から滞留・失敗・再計画ループ・kiro-cli エラーを決定的に集める（有界）。"""
    probe = make_bus(args, "doctor")
    try:
        probe.sync_pull()
    except Exception:  # noqa: BLE001  バス取得失敗は env 所見側で拾う
        pass
    runs = probe.list_runs()
    metas = [(rid, probe.run_meta(rid)) for rid in runs]
    metas.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    recent = metas[:_DOCTOR_RECENT_RUNS]
    stuck, failed, errors = [], [], []
    for rid, meta in recent:
        st = meta.get("status")
        age = _age_hours(meta)
        view = probe.run_view(rid)
        nodes = (view.read_graph() or {}).get("nodes", {})
        node_states = {nid: view.node_state(nid) for nid in nodes}
        failed_nodes = [nid for nid, s in node_states.items() if s == "failed"]
        if st not in TERMINAL and age >= _DOCTOR_STUCK_HOURS:
            stuck.append({"run": rid, "status": st, "age_h": round(age, 1),
                          "claimed": sum(1 for s in node_states.values() if s == "claimed"),
                          "pending": sum(1 for s in node_states.values() if s == "pending")})
        if st == "failed" or failed_nodes:
            failed.append({"run": rid, "status": st, "failed_nodes": failed_nodes[:8],
                           "iteration": (view.read_graph() or {}).get("iteration", 0)})
        for e in view.recent_events(30):
            kind = str(e.get("kind", ""))
            msg = str(e.get("error") or e.get("detail") or "")
            if kind in ("error", "failed") or any(
                    k in msg for k in ("kiro-cli", "失敗", "Traceback", "タイムアウト", "Error")):
                errors.append({"run": rid, "who": e.get("who"), "kind": kind,
                               "msg": msg[:200]})
        for nid in failed_nodes[:3]:
            out = str((view.read_result(nid) or {}).get("output", ""))[:300]
            if out:
                errors.append({"run": rid, "node": nid, "output": out})
    return {
        "runs_total": len(runs),
        "recent": [{"run": rid, "status": m.get("status"),
                    "age_h": round(_age_hours(m), 1), "request": (m.get("request") or "")[:80]}
                   for rid, m in recent],
        "stuck": stuck[:10], "failed": failed[:10], "errors": errors[:20],
    }


def _doctor_prompt(signals: dict, deterministic: "list[dict]") -> str:
    sig = json.dumps(signals, ensure_ascii=False, indent=2)[:6000]
    det = json.dumps(deterministic, ensure_ascii=False, indent=2)[:2000]
    return (
        "あなたは分散 Dynamic Workflow エンジン（agent-flow）の稼働診断医です。以下の run 状態・"
        "イベント・失敗出力・決定的チェックから稼働の問題を洗い出し、3カテゴリに分類してください。\n"
        "- env     : ユーザー環境固有（kiro-cli/git 不在・権限・PATH・worker/daemon 未起動・ネットワーク等）。\n"
        "- config  : 設定の問題（有限停止の無効化・矛盾した planner/executor・lease/argv_limit 不正等）。\n"
        "- program : agent-flow 自体のプログラム上の不具合（想定外の例外・グラフ生成や claim/再計画の"
        "ロジック欠陥・正しい環境/設定でも再現する failed）。コード修正が必要でイシュー起票の対象。\n"
        "**判断は保守的に。** 滞留(stuck)は worker/daemon 未起動という env がよくある原因。env/config で"
        "説明できるものを安易に program にしない。\n\n"
        f"=== 決定的チェック（既出の所見・重複可）===\n{det}\n\n"
        f"=== 稼働シグナル（recent / stuck / failed / errors）===\n{sig}\n\n"
        "出力は次の形の JSON 配列だけ（説明文なし。問題が無ければ [] ）:\n"
        '[{"category":"env|config|program","severity":"critical|warn|info",'
        '"title":"簡潔な要約","evidence":"根拠（どの run/イベントか）",'
        '"fix":"env/config は具体的な修正手順 / program は不具合の説明と再現条件"}]')


def _parse_doctor_findings(text: str) -> "list[dict] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arr, list):
        return None
    out: list[dict] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category", "")).strip().lower()
        if cat not in _DOCTOR_CATEGORIES:
            continue
        sev = str(it.get("severity", "warn")).strip().lower()
        out.append({
            "category": cat,
            "severity": sev if sev in _DOCTOR_SEVERITIES else "warn",
            "title": str(it.get("title", "")).strip()[:200],
            "evidence": str(it.get("evidence", "")).strip()[:600],
            "fix": str(it.get("fix", "")).strip()[:600],
            "source": "agent"})
    return out


def diagnose_with_agent(args, signals: dict, deterministic: "list[dict]",
                        agent_run=None) -> "list[dict] | None":
    """kiro-cli に稼働を診断させ、分類済み finding を得る。kiro-cli 不在・解析不能は None。"""
    run = agent_run or run_agent
    try:
        out = run(_doctor_prompt(signals, deterministic), getattr(args, "model", None))
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等
        return None
    return _parse_doctor_findings(out)


def _dedupe_findings(findings: "list[dict]") -> "list[dict]":
    """(category, 正規化 title) で重複を畳む（決定的チェックを優先して残す）。"""
    seen: dict = {}
    for f in findings:
        key = (f["category"], _doctor_norm(f.get("title", "")))
        if key not in seen:
            seen[key] = f
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(seen.values(),
                  key=lambda f: (_DOCTOR_CATEGORIES.index(f["category"]),
                                 order.get(f["severity"], 1)))


def find_skill(name: str, home: "str | None" = None) -> "str | None":
    """名前付きスキルのディレクトリを探す（無ければ None）。検索順: $KIRO_SKILLS_HOME →
    cwd から上方向の .github/skills → ~/.agent/skills → ~/.kiro/skills → ~/.claude/skills → ~/.github/skills。"""
    cands: list[str] = []
    env = home or os.environ.get("KIRO_SKILLS_HOME")
    if env:
        cands.append(os.path.join(os.path.expanduser(env), name))
    cur = os.getcwd()
    while True:
        cands.append(os.path.join(cur, ".github", "skills", name))
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    for base in ("~/.agent/skills", "~/.kiro/skills", "~/.claude/skills", "~/.github/skills"):
        cands.append(os.path.join(os.path.expanduser(base), name))
    for c in cands:
        if os.path.isdir(c):
            return c
    return None


def apply_doctor_fix(args, finding: dict) -> str:
    """env/config の finding を決定的に修正する（既知の fix_action のみ）。結果文を返す。"""
    if finding.get("fix_action") == "ensure-bus":
        os.makedirs(os.path.abspath(args.bus), exist_ok=True)
        return f"バスのルートを作成しました（{os.path.abspath(args.bus)}）"
    return ""


def file_issues_via_gitlab_idd(args, program: "list[dict]", skill_dir: str,
                               agent_run=None) -> bool:
    """program カテゴリの不具合を gitlab-idd スキルのリクエスター役で起票させる（kiro-cli 委譲）。"""
    run = agent_run or run_agent
    items = "\n".join(
        f"{i}. {f['title']}\n   - 根拠: {f.get('evidence', '')}\n   - 詳細: {f.get('fix', '')}"
        for i, f in enumerate(program, 1))
    prompt = (
        "あなたは gitlab-idd スキルのリクエスター役です。agent-flow の稼働診断で見つかった"
        "『プログラム上の不具合』について、gitlab-idd スキルの手順に従い GitLab イシューを起票して"
        f"ください（スキル: {skill_dir}）。各不具合ごとに目的・再現条件・『## 受け入れ条件』を含む"
        "1 イシューを作成し、既に同一不具合のイシューがあれば重複起票しないこと。\n\n"
        f"=== 不具合一覧 ===\n{items}")
    try:
        run(prompt, getattr(args, "model", None))
        return True
    except Exception:  # noqa: BLE001  kiro-cli 不在・失敗 → 起票せず（呼び出し側で出力）
        return False


def cmd_doctor(args, agent_run=None, skill_finder=find_skill) -> int:
    """稼働を診断し env/config を（--fix で）修正、program は gitlab-idd で起票する。
    終了コード: 0=健康 / 1=未解決の所見あり / 2=未解決の critical あり。"""
    fix = bool(getattr(args, "fix", False))
    as_json = bool(getattr(args, "json", False))
    deterministic = doctor_env_findings(args)
    for f in deterministic:
        f["source"] = "check"
    signals = collect_doctor_signals(args)
    agent = diagnose_with_agent(args, signals, deterministic, agent_run=agent_run)
    findings = _dedupe_findings(deterministic + (agent or []))

    applied: list = []
    if fix:
        for f in findings:
            if f["category"] in ("env", "config"):
                msg = apply_doctor_fix(args, f)
                if msg:
                    f["resolved"] = msg
                    applied.append(f)
        still = {(g["category"], _doctor_norm(g.get("title", "")))
                 for g in doctor_env_findings(args)}
        for f in findings:
            if f.get("source") == "check" and not f.get("resolved"):
                if (f["category"], _doctor_norm(f.get("title", ""))) not in still:
                    f["resolved"] = "修正により解消"

    program = [f for f in findings if f["category"] == "program"]
    skill_dir = skill_finder("gitlab-idd")
    filed = False
    if fix and program:
        if skill_dir:
            filed = file_issues_via_gitlab_idd(args, program, skill_dir, agent_run=agent_run)
            if filed:
                for f in program:
                    f["resolved"] = f"gitlab-idd で起票（{os.path.basename(skill_dir)}）"

    unresolved = [f for f in findings if not f.get("resolved")]
    has_critical = any(f["severity"] == "critical" for f in unresolved)
    code = 2 if has_critical else (1 if unresolved else 0)

    if as_json:
        print(json.dumps({
            "tool": "agent-flow", "agent_used": agent is not None,
            "skill_available": bool(skill_dir), "fix": fix, "findings": findings,
            "applied": len(applied), "issues_filed": filed, "unresolved": len(unresolved),
        }, ensure_ascii=False, indent=2))
        return code

    print("=== agent-flow doctor（稼働診断）===")
    print(f"診断: {'kiro-cli' if agent is not None else '決定的チェックのみ（kiro-cli 不在/解析不能）'}"
          f"  / 所見 {len(findings)} 件")
    if not findings:
        print("問題は見つかりませんでした（healthy）。")
        return 0
    label = {"env": "環境", "config": "設定", "program": "プログラム"}
    mark = {"critical": "✗", "warn": "−", "info": "·"}
    for cat in _DOCTOR_CATEGORIES:
        group = [f for f in findings if f["category"] == cat]
        if not group:
            continue
        print(f"\n[{label[cat]}] {len(group)} 件")
        for f in group:
            print(f"  {mark.get(f['severity'], '−')} {f['title']}")
            if f.get("evidence"):
                print(f"      根拠: {f['evidence']}")
            if f.get("fix"):
                print(f"      対処: {f['fix']}")
            if f.get("resolved"):
                print(f"      ✓ {f['resolved']}")
    print()
    if fix:
        print(f"修正: env/config {len(applied)} 件を適用。")
        if program:
            if skill_dir and filed:
                print(f"起票: program {len(program)} 件を gitlab-idd で起票しました。")
            elif skill_dir and not filed:
                print(f"起票: gitlab-idd への委譲に失敗（kiro-cli 不在等）。program "
                      f"{len(program)} 件は未起票です。")
            else:
                print(f"起票: gitlab-idd スキルが見つからないため、program {len(program)} 件は"
                      f"出力のみ（イシュー未起票）。")
    else:
        print("（--fix で env/config の修正と program のイシュー起票を実行します）")
    return code

