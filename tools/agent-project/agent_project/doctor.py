from __future__ import annotations
# doctor.py — 元 agent-project.py の 7605-8425 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# audit（Loop Readiness セルフ監査）— Loop Engineering の Loop Design Checklist /
#   Quick Red Flags を決定的に採点する。L0–L3 のレベルと 0–100 スコア・赤旗・提案を出し、
#   「いまどの自律度で無人運用してよいか」を機械判定する。stdlib のみ・エージェント不要。
# ---------------------------------------------------------------------------
def compute_audit(cfg: Config) -> dict:
    """backlog/policy/config/state を走査して Loop Readiness を採点する（決定的）。"""
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    protect = list(getattr(policy, "protect", []) or [])
    ready = consumable_tasks(tasks)
    # accept / verify_template を持つタスクは実行時に concrete な verify が用意されるので「verify 無し」に数えない
    # （detect_rot / run_loop S0 と整合させる）。
    ready_no_verify = [t.id for t in ready if not has_verify_plan(t)]
    has_cost_budget = bool(cfg.max_tokens) or bool(cfg.max_cost)
    near_cap = [t.id for t in ready if cfg.max_retries and t.retries >= cfg.max_retries]
    state_ok = cfg.decisions.exists() or cfg.journal.exists()
    handoff_ok = cfg.needs.exists()
    rot_hits = detect_rot(cfg, tasks) if cfg.rot else []   # rot on のときだけ走査

    # checks: id, label, ok, weight, min_level, severity, detail
    checks = [
        ("verify_coverage", "ready タスクは全て verify を持つ（鉄則）",
         not ready_no_verify, 25, 1, "critical",
         (f"verify 無し ready: {ready_no_verify[:8]}" if ready_no_verify else "OK")),
        ("verifier_independent", "verifier は実装者と別（決定的 verify＝rubber-stamp 不能）",
         True, 5, 1, "info", "verify は終了コードで判定（構造的に独立）"),
        ("finite_stop", "有限停止（max_cycles が有限）",
         cfg.max_cycles > 0, 10, 1, "critical",
         f"max_cycles={cfg.max_cycles} max_seconds={cfg.max_seconds}"),
        ("state_observability", "状態/観測（decisions・journal）",
         state_ok, 10, 1, "warn", "decisions/journal あり" if state_ok else "未作成"),
        ("attempt_cap", "リトライ上限→escalate（無限 fix ループ防止）",
         cfg.max_retries >= 0, 10, 2, "warn", f"max_retries={cfg.max_retries}"),
        ("human_handoff", "人へのエスカレーション先（needs/）",
         handoff_ok, 10, 2, "warn", "needs/ あり" if handoff_ok else "needs/ 未作成"),
        ("cost_budget", "コスト予算（max_tokens か max_cost）",
         has_cost_budget, 10, 3, "warn",
         f"tokens={cfg.max_tokens} usd={cfg.max_cost}" if has_cost_budget else "未設定（無人運用は要設定）"),
        ("safety_denylist", "パス保護デニーリスト（policy protect:）",
         bool(protect), 15, 3, "warn",
         f"protect={protect[:6]}" if protect else "未設定（.env/secrets/auth 等を守れていない）"),
        ("prune_state", "状態の掃除（--rot で古い/重複/実行不能を検知）",
         bool(cfg.rot), 5, 3, "info", "rot on" if cfg.rot else "rot off"),
    ]
    score = round(100 * sum(w for _, _, ok, w, *_ in checks if ok)
                  / sum(w for _, _, _, w, *_ in checks))

    # level: 各レベルの必須 check が全て ok か（下から積み上げ）
    def _lvl_ok(n):
        return all(ok for _id, _lbl, ok, _w, ml, sev, _d in checks if ml <= n and sev != "info")
    level = 0
    for n in (1, 2, 3):
        if _lvl_ok(n):
            level = n
        else:
            break

    red_flags = []
    if ready_no_verify:
        red_flags.append(("critical", f"verify を持たない ready タスク {len(ready_no_verify)} 件"
                                      "（拾われても escalate＝人手に逆流）"))
    if cfg.watch and not has_cost_budget:
        red_flags.append(("warn", "無人運用(watch)なのにコスト予算(max_tokens/max_cost)が未設定"))
    if cfg.watch and not protect:
        red_flags.append(("warn", "無人運用(watch)なのに保護パス(protect)が未設定"
                                  "（act が .env/secrets/auth を書き換え得る）"))
    if rot_hits:
        red_flags.append(("warn", f"rot（古い/重複/実行不能）{len(rot_hits)} 件を検知"))
    if near_cap:
        red_flags.append(("warn", f"リトライ上限間際のタスク {near_cap[:6]}（収束していない可能性）"))
    # L3 はクリティカル赤旗があれば認めない
    if level >= 3 and any(sev == "critical" for sev, _ in red_flags):
        level = 2

    suggestions = []
    for _id, lbl, ok, _w, ml, sev, _d in checks:
        if not ok and sev != "info":
            if _id == "cost_budget":
                suggestions.append("max_cost か max_tokens を設定（config か --max-cost/--max-tokens）")
            elif _id == "safety_denylist":
                suggestions.append("policy.md に protect: を追加（.env / **/secrets/** / auth/** など）")
            elif _id == "verify_coverage":
                suggestions.append("verify 無しの ready タスクに検証コマンドを与えるか inbox へ戻す")
            elif _id == "prune_state":
                suggestions.append("--rot を有効化して古い/重複タスクを掃除")
            else:
                suggestions.append(f"未達: {lbl}")

    return {
        "level": level, "level_label": f"L{level}", "score": score,
        "checks": [{"id": i, "label": l, "ok": ok, "min_level": ml,
                    "severity": sev, "detail": d}
                   for i, l, ok, _w, ml, sev, d in checks],
        "red_flags": [{"severity": s, "message": m} for s, m in red_flags],
        "suggestions": suggestions,
        "summary": {"ready": len(ready), "ready_no_verify": len(ready_no_verify),
                    "pending_human": sum(1 for t in tasks
                                         if t.norm_status() in ("blocked", "review")),
                    "watch": cfg.watch, "level": cfg.level},
    }


_LEVEL_MEANING = {0: "Draft（意図のみ）", 1: "Report（報告のみ・自動実行なし相当）",
                  2: "Assisted（検証つき小修正）", 3: "Unattended（無人運用可・人ゲート前提）"}


def cmd_audit(cfg: Config, as_json: bool = False, strict: bool = False) -> int:
    """Loop Readiness を採点して L0–L3・スコア・赤旗・提案を出す。--strict で CI ゲート化。"""
    a = compute_audit(cfg)
    if as_json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print("=== agent-project audit（Loop Readiness）===")
        print(f"レベル : {a['level_label']} — {_LEVEL_MEANING[a['level']]}")
        print(f"スコア : {a['score']}/100")
        print("チェック:")
        for c in a["checks"]:
            mark = "✓" if c["ok"] else ("✗" if c["severity"] == "critical" else "−")
            print(f"  [{mark}] L{c['min_level']} {c['label']} … {c['detail']}")
        if a["red_flags"]:
            print("赤旗:")
            for r in a["red_flags"]:
                print(f"  ⚠ [{r['severity']}] {r['message']}")
        if a["suggestions"]:
            print("提案:")
            for s in a["suggestions"]:
                print(f"  → {s}")
    has_critical = any(r["severity"] == "critical" for r in a["red_flags"])
    if strict and (a["score"] < 40 or has_critical):
        return 2
    return 0


# ---------------------------------------------------------------------------
# doctor（稼働診断）— ログ/状態/環境から稼働状況を エージェント CLI に診断させ、原因を
#   env（ユーザー環境固有）/ config（設定）/ program（プログラム上の不具合）へ分類する。
#   env・config は（--fix で）決定的に修正し、program は gitlab-idd スキルでイシュー起票する
#   （スキルが無ければ起票文面を出力するだけ）。知能（診断・分類・起票文面）は エージェント CLI へ委譲し、
#   収集・修正・起票の駆動は本体が決定的に行う（§1 不変条件: 知能は委譲・操作は決定的）。
# ---------------------------------------------------------------------------
_DOCTOR_CATEGORIES = ("env", "config", "program", "git")
_DOCTOR_SEVERITIES = ("critical", "warn", "info")
_DOCTOR_DEFAULT_PROTECT = ["**/.env", "**/secrets/**", "auth/**", "payments/**", "**/migrations/**"]


def _tail_text(path: "Path | None", n_lines: int = 40, n_chars: int = 2000) -> str:
    """ファイル末尾を有界に読む（無ければ空）。診断文脈の注入用。"""
    if not path or not Path(path).exists():
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n_lines:])[-n_chars:]


def unpushed_commits(repo: "Path | None") -> "tuple[int, str]":
    """(origin へ未 push のコミット数, ブランチ名)。git でない・upstream 未設定なら (0, "")。

    worker と verify は **origin から clone** して実行する（verify.py の _task_verify_cwd →
    gitcache の provision_worktree）。したがって **ローカルにだけあるコミットは、彼らからは
    存在しないのと同じ**。人が手元で直してコミットしただけの成果は verify に届かず、
    「ローカルでは通るのに verify は落ち続ける」という、原因に辿り着きにくい詰まり方をする
    （実際に起きた: 手元では pytest -k codd が 29 件 PASS するのに、クローンでは 0 件収集で
    exit=5 → 繰り返し NG → blocked）。"""
    if repo is None:
        return 0, ""
    try:
        br = subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if br.returncode != 0:
            return 0, ""
        branch = br.stdout.strip()
        r = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", "@{u}..HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:            # upstream 未設定（追跡ブランチが無い）
            return 0, branch
        return int(r.stdout.strip() or 0), branch
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0, ""


def doctor_env_findings(cfg: "Config", which=shutil.which) -> "list[dict]":
    """環境/設定の決定的チェック（LLM 不要）。fix_action を持つものは --fix で修正できる。"""
    findings: list[dict] = []
    # 未 push のローカルコミット（unpushed_commits の docstring 参照）
    n, branch = unpushed_commits(cfg.state_top)
    if n:
        findings.append({
            "category": "git", "severity": "warn",
            "title": f"origin へ未 push のコミットが {n} 件ある（{branch}）",
            "evidence": ("worker と verify は origin から clone して実行するため、ローカルにだけある "
                         "コミットは彼らからは見えない。手元で直した成果は verify に届かず、"
                         "「ローカルでは通るのに verify は落ち続ける」状態になる"),
            "fix": f"git -C {cfg.state_top} push origin {branch}"})
    needs_cli = cfg.planner == "agent" or cfg.executor == "agent" or cfg.auto_adjudicate
    agent_bin = _AGENT_CLI_BINARIES.get(cfg.agent_cli, cfg.agent_cli)
    if needs_cli and not which(agent_bin):
        findings.append({
            "category": "env", "severity": "critical",
            "title": f"{agent_bin} が PATH に見つからない",
            "evidence": (f"planner={cfg.planner} executor={cfg.executor} "
                         f"auto_adjudicate={cfg.auto_adjudicate} agent_cli={cfg.agent_cli} は "
                         f"{agent_bin} を要求する"),
            "fix": f"{agent_bin} をインストールして PATH を通す（暫定回避は --planner none / --executor stub）"})
    if cfg.executor != "stub" and not (cfg.agent_flow or which("agent-flow")):
        findings.append({
            "category": "env", "severity": "warn",
            "title": "agent-flow が見つからない（PATH / --agent-flow / 同梱のいずれにも無い）",
            "evidence": f"act(local run) の委譲先 agent-flow を解決できない（executor={cfg.executor}）",
            "fix": "agent-flow を PATH に置くか --agent-flow で実体を指定する"})
    if not which("git"):
        findings.append({
            "category": "env", "severity": "warn", "title": "git が見つからない",
            "evidence": "成果参照・$KIRO_BASE_REV 差分 verify・回帰巻き戻しに git を使う",
            "fix": "git をインストールして PATH を通す"})
    elif not (cfg.workdir / ".git").exists():
        findings.append({
            "category": "env", "severity": "info", "title": "workdir が git リポジトリでない",
            "evidence": f"workdir={cfg.workdir} に .git が無い",
            "fix": "成果物リポジトリ上で実行するか、charter の repos に owns: を付けて route で書込先を割り当てる"})
    missing = [str(d) for d in (cfg.backlog, cfg.needs, cfg.decisions) if not d.exists()]
    if missing:
        findings.append({
            "category": "config", "severity": "warn", "title": "必須ディレクトリが未作成",
            "evidence": "未作成: " + ", ".join(missing),
            "fix": "backlog / needs / decisions を作成する", "fix_action": "create-dirs"})
    return findings


def doctor_coordination_findings(cfg: "Config") -> "list[dict]":
    """multi-node Git CAS の起動前不変条件を決定的に検査する。"""
    if getattr(cfg, "coordination", "") != "git-cas":
        return []
    findings: list[dict] = []

    def add(title: str, evidence: str, fix: str) -> None:
        findings.append({"category": "config", "severity": "critical", "title": title,
                         "evidence": evidence, "fix": fix})

    if not str(getattr(cfg, "node", "") or "").strip():
        add("git-cas には node が必要", "実行権の owner と controller 候補を識別できない",
            "PC 固有 profile に node を設定する")
    heartbeat = float(getattr(cfg, "controller_heartbeat_sec", 30.0) or 30.0)
    lease = float(getattr(cfg, "controller_lease_sec", 120.0) or 120.0)
    if heartbeat >= lease:
        add("controller heartbeat が lease 以上", f"heartbeat={heartbeat}s lease={lease}s",
            "controller_heartbeat_sec を controller_lease_sec より短くする")
    root = Path(cfg.backlog).parent
    remote = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if remote.returncode != 0 or not remote.stdout.strip():
        add("git-cas の state root に origin が無い", f"state root={root}",
            "state_repo の clone を profile.root に指定し origin を設定する")
    if availability_state(cfg) == "invalid":
        add("availability 設定が不正", json.dumps(getattr(cfg, "availability", {}), ensure_ascii=False),
            "timezone と daily_stop(HH:MM) を修正する")
    return findings


def doctor_audit_findings(cfg: "Config") -> "list[dict]":
    """compute_audit の未達チェックを config カテゴリの finding に変換（決定的）。"""
    a = compute_audit(cfg)
    out: list[dict] = []
    for c in a["checks"]:
        if c["ok"] or c["severity"] == "info":
            continue
        f = {"category": "config",
             "severity": "critical" if c["severity"] == "critical" else "warn",
             "title": f"監査未達: {c['label']}", "evidence": c["detail"], "fix": ""}
        if c["id"] == "safety_denylist":
            f["fix"] = "policy.md に protect: を追加（.env / **/secrets/** / auth/** など）"
            f["fix_action"] = "policy-protect"
        elif c["id"] == "verify_coverage":
            f["fix"] = "verify 無しの ready タスクに検証コマンドを与えるか inbox へ戻す"
        elif c["id"] == "cost_budget":
            f["fix"] = "max_cost か max_tokens を設定（config か --max-cost/--max-tokens）"
        elif c["id"] == "finite_stop":
            f["fix"] = "max_cycles を正の値にする（有限停止の鉄則）"
        elif c["id"] in ("state_observability", "human_handoff"):
            f["fix"] = "decisions / journal / needs を作成する（run か doctor --fix で自動作成）"
            f["fix_action"] = "create-dirs"
        else:
            f["fix"] = f"未達を解消する: {c['label']}"
        out.append(f)
    return out


def doctor_flow_bus_coverage_findings(cfg: "Config") -> "list[dict]":
    """このプロジェクトのバスに稼働中の agent-flow daemon がいるかを確認し、不在を warn にする
    （未担当だと run が local 実行に落ち、夜間停止からの自動再開・gitlab 長期委譲の継続が効かない）。
    manage_flow_daemon が on なら agent-project が自動起動するので通常は満たされ、
    起動失敗や off での起動忘れのときに気づける。鏡写しの落とし先があるバスだけ確認する。"""
    # 対象は「root 配下のバス（agent-project の state 同期が鏡写しする）」か
    # 「root 外でも鏡写しの落とし先があるバス」。どちらでもなければ確認しない。
    if not _bus_inside_state(cfg) and project_flow_remote(cfg) is None:
        return []
    managed = bool(getattr(cfg, "manage_flow_daemon", False))
    if daemon_running(cfg, use_git=False):
        return []
    fix = ("manage_flow_daemon: true を設定（agent-project が自動起動）"
           if not managed else
           f"起動失敗の可能性。手動確認: agent-flow --bus {cfg.bus} daemon"
           "（バスが root 配下なら state-git は不要＝agent-project が鏡写しする）")
    return [{
        "category": "config", "severity": "warn",
        "title": "agent-flow daemon 不在",
        "evidence": f"{cfg.bus} を担当する agent-flow daemon が見つかりません"
                    "（run が local 実行に落ち、夜間停止からの自動再開・gitlab 長期委譲の継続が効きません）",
        "fix": fix,
    }]


def _hook_misconfig_findings(cfg: "Config") -> "list[dict]":
    """フックの設定ミスだけを所見にする。未指定での不在は任意機能が無いだけなので無言（空）。

    人が `hooks:` に名前を書いたのに効いていない状態は、書いた本人からは配線が黙って
    無視されたようにしか見えない。既定の不在と同じ扱いにすると設定ミスが観測できないため、
    明示指定の解決失敗に限って warn を出す。"""
    def warn(title: str, evidence: str) -> "list[dict]":
        return [{
            "category": "config", "severity": "warn", "title": title, "evidence": evidence,
            "fix": "agent-project.yaml の hooks を修正するか、行ごと削除して自動検出に戻す",
        }]

    hooks = getattr(cfg, "hooks", None)
    if hooks is not None and not isinstance(hooks, dict):
        # 型が壊れていれば指定は丸ごと読めない。自動検出がたまたま当たっても、書いた設定が
        # 効いていない事実は変わらないので所見にする。
        return warn("hooks の設定型が不正",
                    _hook_resolution_error("wiring.detect", cfg) or "hooks は能力キー -> module 名の対応表")
    for capability in ("wiring.detect", "wiring.findings"):
        reason = _hook_resolution_error(capability, cfg)
        if reason:
            # 1つの設定キー（hooks.wiring）が両方の能力を担うため、能力ごとに並べず1件へまとめる。
            return warn("指定した配線プロバイダを解決できない", reason)
    return []


def doctor_wiring_findings(cfg: "Config", which=shutil.which, run=subprocess.run) -> "list[dict]":
    """配線プロバイダの検出結果と regression_cmd/intake_cmd の結線の有無を doctor の finding 形式で
    返す（決定的・LLM 不要）。repos.json（`repo_registry_path`）が実在すれば schemas 契約も併せて
    判定する。プロバイダは能力で解決する任意フック（`_hook_provider`）で、本体は固有名を持たない。

    プロバイダが使えない環境では空リストへ no-op 縮退する。「解決失敗」は import できないことに
    限らない（別物への解決・import 時の例外・契約関数の欠落）が、どれも空へ畳んで doctor 全体は
    走り切らせる——結線所見が出ないのは任意機能の欠落だが、doctor が落ちるのは診断コマンドとして
    致命的で、失う情報が桁違いに多い。プロバイダ呼び出し自体が投げた例外も同じ理由で畳む。"""
    out = _hook_misconfig_findings(cfg)          # 既定（hooks 未指定）では常に空
    detect = _hook_provider("wiring.detect", cfg)
    render = _hook_provider("wiring.findings", cfg)
    if detect is None or render is None:
        # 片方だけで走らせない。属性が片方だけ改名された環境で半端な判定を出すより無所見が正しい。
        return out
    try:
        judgment = detect.detect_wiring(
            regression_cmd=cfg.regression_cmd, intake_cmd=cfg.intake_cmd,
            repos_path=repo_registry_path(cfg), which=which, run=run)
        return out + list(render.doctor_findings(judgment))   # judgment は本体にとって不透明
    except Exception:
        return out


def collect_doctor_signals(cfg: "Config") -> dict:
    """ログ/状態から診断材料を決定的に集める（エージェント CLI へ渡す・有界）。"""
    tasks = load_tasks(cfg.backlog)
    blocked = [{"id": t.id, "title": t.title, "status": t.norm_status(),
                "retries": t.retries}
               for t in tasks if t.norm_status() in ("blocked", "review")][:20]
    recs: list[dict] = []
    if cfg.runlog and cfg.runlog.exists():
        for line in cfg.runlog.read_text(encoding="utf-8").splitlines()[-20:]:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    needs: list[str] = []
    if cfg.needs.exists():
        for p in sorted(cfg.needs.glob("*.md"))[:20]:
            head = next((ln[2:].strip() for ln in
                         p.read_text(encoding="utf-8", errors="replace").splitlines()
                         if ln.startswith("# ")), p.stem)
            needs.append(head)
    a = compute_audit(cfg)
    return {
        "stats": compute_stats(cfg),
        "audit": {"level": a["level"], "score": a["score"], "red_flags": a["red_flags"]},
        "runlog_tail": recs,
        "journal_tail": _tail_text(cfg.journal),
        "needs": needs,
        "blocked": blocked,
    }


def _doctor_prompt(signals: dict, deterministic: "list[dict]") -> str:
    sig = json.dumps(signals, ensure_ascii=False, indent=2)[:6000]
    det = json.dumps(deterministic, ensure_ascii=False, indent=2)[:2000]
    return (
        "あなたは自律バックログ・ループ（agent-project）の稼働診断医です。以下のログ・状態・"
        "決定的チェック結果から、稼働の問題を洗い出し、それぞれを次の3カテゴリに分類してください。\n"
        "- env     : ユーザー環境固有（依存コマンド不在・権限・PATH・ネットワーク等）。修正可能。\n"
        "- config  : 設定の問題（予算未設定・保護パス未設定・verify 欠落・矛盾した設定等）。修正可能。\n"
        "- program : agent-project 自体（や委譲先ツール）のプログラム上の不具合・想定外の例外・"
        "ロジックの欠陥。コード修正が必要でイシュー起票の対象。\n"
        "**判断は保守的に。** env/config で説明できるものを安易に program にしない。program は"
        "『正しい環境・正しい設定でも再現する不具合』に限る。\n\n"
        f"=== 決定的チェック（既出の所見・重複可）===\n{det}\n\n"
        f"=== 稼働シグナル（stats / audit / run-log / journal / needs / blocked）===\n{sig}\n\n"
        "出力は次の形の JSON 配列だけ（説明文なし。問題が無ければ [] ）:\n"
        '[{"category":"env|config|program","severity":"critical|warn|info",'
        '"title":"簡潔な要約","evidence":"根拠（どのログ/状態か）",'
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


def diagnose_with_agent(cfg: "Config", signals: dict, deterministic: "list[dict]",
                        agent_run=None) -> "list[dict] | None":
    """エージェント CLI に稼働を診断させ、分類済み finding の配列を得る。
    エージェント CLI 不在・エラー・解析不能は None（＝決定的所見のみで続行）。"""
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="doctor"))
    try:
        out = run(_doctor_prompt(signals, deterministic), cfg.model)
    except Exception:  # noqa: BLE001  エージェント CLI 不在・タイムアウト等
        return None
    return _parse_doctor_findings(out)


def _dedupe_findings(findings: "list[dict]") -> "list[dict]":
    """(category, 正規化 title) で重複を畳む。決定的チェックを優先して残す。"""
    seen: dict = {}
    for f in findings:
        key = (f["category"], re.sub(r"\s+", " ", f.get("title", "").lower()).strip())
        if key not in seen:
            seen[key] = f
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(seen.values(),
                  key=lambda f: (_DOCTOR_CATEGORIES.index(f["category"]),
                                 order.get(f["severity"], 1)))


def find_skill(name: str, home: "str | None" = None) -> "Path | None":
    """名前付きスキルのディレクトリを探す（無ければ None）。検索順: $KIRO_SKILLS_HOME →
    cwd から上方向の .github/skills → ~/.claude/skills → ~/.github/skills。"""
    cands: list[Path] = []
    env = home or os.environ.get("KIRO_SKILLS_HOME")
    if env:
        cands.append(Path(env).expanduser() / name)
    cur = Path.cwd().resolve()
    for base in [cur, *cur.parents]:
        cands.append(base / ".github" / "skills" / name)
    cands.append(Path("~/.claude/skills").expanduser() / name)
    cands.append(Path("~/.github/skills").expanduser() / name)
    for c in cands:
        if c.is_dir():
            return c
    return None


def _ensure_policy_protect(cfg: "Config") -> str:
    """policy.md に protect: が一つも無ければ既定の保護デニーリストを追記する（決定的・冪等）。"""
    if load_policy(cfg.policy).protect:
        return ""
    cfg.policy.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if (cfg.policy.exists() and cfg.policy.stat().st_size > 0) else ""
    with cfg.policy.open("a", encoding="utf-8") as f:
        f.write(prefix + "# doctor: 既定の保護パス（無人運用の最低ライン）\n")
        for g in _DOCTOR_DEFAULT_PROTECT:
            f.write(f"protect: {g}\n")
    return ", ".join(_DOCTOR_DEFAULT_PROTECT)


def apply_doctor_fix(cfg: "Config", finding: dict) -> str:
    """env/config の finding を決定的に修正する。既知の fix_action のみ適用し、結果文を返す
    （未対応なら空文字＝提案の表示のみ）。"""
    act = finding.get("fix_action")
    if act == "create-dirs":
        ensure_dirs(cfg)
        return "backlog / needs / decisions を作成しました"
    if act == "policy-protect":
        added = _ensure_policy_protect(cfg)
        return f"policy.md に protect: を追加しました（{added}）" if added else ""
    return ""


def file_issues_via_gitlab_idd(cfg: "Config", program: "list[dict]", skill_dir: Path,
                               agent_run=None) -> bool:
    """program カテゴリの不具合を gitlab-idd スキルのリクエスター役でイシュー起票させる
    （エージェント CLI へ委譲）。成功で True、エージェント CLI 不在・失敗で False。"""
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="doctor"))
    items = "\n".join(
        f"{i}. {f['title']}\n   - 根拠: {f.get('evidence', '')}\n   - 詳細: {f.get('fix', '')}"
        for i, f in enumerate(program, 1))
    prompt = (
        "あなたは gitlab-idd スキルのリクエスター役です。agent-project の稼働診断で見つかった"
        "『プログラム上の不具合』について、gitlab-idd スキルの手順に従い GitLab イシューを起票して"
        f"ください（スキル: {skill_dir}）。各不具合ごとに目的・再現条件・『## 受け入れ条件』を含む"
        "1 イシューを作成し、既に同一不具合のイシューがあれば重複起票しないこと。\n\n"
        f"=== 不具合一覧 ===\n{items}")
    try:
        run(prompt, cfg.model)
        return True
    except Exception:  # noqa: BLE001  エージェント CLI 不在・失敗 → 起票せず（呼び出し側で出力）
        return False


def collect_flow_findings(cfg: "Config", fix: bool, runner=None) -> "list[dict]":
    """連携: 実行層 `agent-flow doctor --json` を同じバスに対して実行し findings を取り込む。
    agent-project の診断に agent-flow（内側＝act の実体）の稼働所見を統合する。`--fix` のときは
    agent-flow 側にも `--fix` を委譲し、agent-flow が自分の env/config 修正と program 起票を行う
    （本体は agent-flow 由来の finding を再修正・再起票しない＝二重作業を避ける）。
    cfg.with_flow が off・agent-flow 不在・タイムアウト・解析不能は空で無害にスキップ。"""
    if not cfg.with_flow:
        return []
    cmd = _kf_base(cfg, bool(cfg.git_bus)) + ["doctor", "--json"]
    if fix:
        cmd.append("--fix")
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600))
    try:
        proc = run(cmd)
        data = json.loads(getattr(proc, "stdout", "") or "")
    except Exception:  # noqa: BLE001  agent-flow 不在・タイムアウト・JSON 解析失敗
        return []
    out: list[dict] = []
    for f in (data.get("findings", []) if isinstance(data, dict) else []):
        if not isinstance(f, dict) or f.get("category") not in _DOCTOR_CATEGORIES:
            continue
        g = dict(f)
        g["source"] = "agent-flow"
        out.append(g)
    return out


def cmd_doctor(cfg: "Config", fix: bool = False, as_json: bool = False,
               agent_run=None, skill_finder=find_skill, flow_finder=collect_flow_findings) -> int:
    """稼働を診断し env/config を（--fix で）修正、program は gitlab-idd で起票する。
    実行層 agent-flow の doctor も連携実行し findings を統合する（cfg.with_flow 時）。
    終了コード: 0=健康 / 1=未解決の所見あり / 2=未解決の critical あり。"""
    # 決定的所見は ensure_dirs より前に集める（create-dirs 所見を消さないため）
    deterministic = (doctor_env_findings(cfg) + doctor_coordination_findings(cfg)
                     + doctor_audit_findings(cfg)
                     + doctor_flow_bus_coverage_findings(cfg) + doctor_wiring_findings(cfg))
    for f in deterministic:
        f["source"] = "check"
    signals = collect_doctor_signals(cfg)
    agent = diagnose_with_agent(cfg, signals, deterministic, agent_run=agent_run)
    flow = flow_finder(cfg, fix) if cfg.with_flow else []   # 実行層 agent-flow の所見を連携取得
    findings = _dedupe_findings(deterministic + (agent or []) + flow)

    applied: list[tuple] = []
    if fix:
        for f in findings:
            # agent-flow 由来は agent-flow 側で既に処理済み（再修正しない）
            if f["category"] in ("env", "config") and f.get("source") != "agent-flow":
                msg = apply_doctor_fix(cfg, f)
                if msg:
                    f["resolved"] = msg
                    applied.append((f, msg))
        # 適用後に決定的チェックを取り直し、もう再現しない所見は『修正により解消』として畳む
        # （例: create-dirs は複数の監査未達を一度に解消する）。
        still = {(g["category"], re.sub(r"\s+", " ", g.get("title", "").lower()).strip())
                 for g in doctor_env_findings(cfg) + doctor_coordination_findings(cfg)
                 + doctor_audit_findings(cfg)}
        for f in findings:
            if f.get("source") == "check" and not f.get("resolved"):
                key = (f["category"], re.sub(r"\s+", " ", f.get("title", "").lower()).strip())
                if key not in still:
                    f["resolved"] = "修正により解消"

    # program は本体由来のみ本体が起票（agent-flow 由来は agent-flow が起票済み）
    program = [f for f in findings
               if f["category"] == "program" and f.get("source") != "agent-flow"]
    skill_dir = skill_finder("gitlab-idd")
    filed = False
    if fix and program:
        if skill_dir:
            filed = file_issues_via_gitlab_idd(cfg, program, skill_dir, agent_run=agent_run)
            if filed:
                for f in program:
                    f["resolved"] = f"gitlab-idd で起票（{skill_dir.name}）"

    if applied or filed:
        append_journal(cfg.journal,
                        f"doctor: env/config 修正 {len(applied)} 件 / "
                        f"program 起票 {'有' if filed else '無'}（program {len(program)} 件）")

    unresolved = [f for f in findings if not f.get("resolved")]
    has_critical = any(f["severity"] == "critical" for f in unresolved)

    if as_json:
        print(json.dumps({
            "agent_used": agent is not None,
            "skill_available": bool(skill_dir),
            "with_flow": cfg.with_flow,
            "flow_findings": len(flow),
            "fix": fix,
            "findings": findings,
            "applied": len(applied),
            "issues_filed": filed,
            "unresolved": len(unresolved),
        }, ensure_ascii=False, indent=2))
        return 2 if has_critical else (1 if unresolved else 0)

    print("=== agent-project doctor（稼働診断）===")
    flow_note = f"  / agent-flow 連携 {len(flow)} 件" if cfg.with_flow else ""
    print(f"診断: {'エージェント CLI' if agent is not None else '決定的チェックのみ（エージェント CLI 不在/解析不能）'}"
          f"  / 所見 {len(findings)} 件{flow_note}")
    if not findings:
        print("問題は見つかりませんでした（healthy）。")
        return 0
    label = {"env": "環境", "config": "設定", "program": "プログラム", "git": "git"}
    mark = {"critical": "✗", "warn": "−", "info": "·"}
    for cat in _DOCTOR_CATEGORIES:
        group = [f for f in findings if f["category"] == cat]
        if not group:
            continue
        print(f"\n[{label[cat]}] {len(group)} 件")
        for f in group:
            src = " [flow]" if f.get("source") == "agent-flow" else ""
            print(f"  {mark.get(f['severity'], '−')} {f['title']}{src}")
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
                print(f"起票: gitlab-idd への委譲に失敗（エージェント CLI 不在等）。上記 program "
                      f"{len(program)} 件は未起票です。")
            else:
                print(f"起票: gitlab-idd スキルが見つからないため、program {len(program)} 件は"
                      f"出力のみ（イシュー未起票）。")
    else:
        print("（--fix で env/config の修正と program のイシュー起票を実行します）")
    return 2 if has_critical else 1


def cmd_runlog(cfg: Config, as_json: bool = False, tail: int = 10) -> int:
    """構造化 run-log（run-log.jsonl）の末尾を表示。運用判断（slow down/pause/kill）の土台。"""
    recs: list[dict] = []
    p = cfg.runlog
    if p and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    recs = recs[-tail:] if tail and tail > 0 else recs
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print("run-log がありません（run すると run-log.jsonl に1行ずつ記録されます）。")
        return 0
    print(f"=== run-log（最新 {len(recs)} 件）===")
    for r in recs:
        print(f"{r.get('ts', '?')}  reason={str(r.get('reason')):8s} "
              f"done={r.get('done', 0)} esc={r.get('escalations', 0)} "
              f"tokens={r.get('tokens', 0)} usd={r.get('cost', 0)} {r.get('duration_s', 0)}s")
    return 0


def cmd_stats(cfg: Config, as_json: bool = False) -> int:
    """ループの計測値を出す（スループット・自動化率・retry・人対応待ち）。回路調整の土台。"""
    s = compute_stats(cfg)
    if as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    rate = s["automation_rate"]
    rate_disp = f"{rate*100:.0f}%" if rate is not None else "—"
    fp = s["first_pass_done"]
    fp_disp = f"{fp}/{s['done_archived']}" if s["done_archived"] else "—"
    print("=== agent-project stats ===")
    print(f"完了(archive)   : {s['done_archived']}（一発 done {fp_disp}）")
    print(f"納品(DELIVERY)  : {s['delivery_rows']}")
    print(f"未消化 backlog  : {s['backlog_pending']}  {s['by_status']}")
    print(f"人の対応待ち    : {s['pending_human']}（blocked + review）")
    print(f"自動解決/人対応 : {s['auto_resolved']} / {s['human_actions']}  → 自動化率 {rate_disp}")
    print(f"retry 累計      : pending {s['retries_pending_sum']} / archived {s['retries_archived_sum']}")
    print(f"コスト(archive) : tokens {s['tokens_archived']} / usd {s['cost_archived']}")
    print(f"決定記録        : {s['decisions_total']} 件  {s['actions']}")
    return 0


def cmd_rot(cfg: Config, fix: bool) -> int:
    tasks = load_tasks(cfg.backlog)
    rot = detect_rot(cfg, tasks)
    if not rot:
        print("rot は見つかりませんでした。")
        return 0
    print(f"rot を {len(rot)} 件検出:")
    for t, reason in rot:
        print(f"  {t.id}: {t.title} — {reason}")
        if fix:
            _block(cfg, t, f"rot: {reason}", {})
    if fix:
        print("→ いずれも人の判断（blocked）へ回しました。")
    return 1


def cmd_promote(cfg: Config) -> int:
    """効いた学習（decisions/ の learn）を ltm-use 長期記憶へ昇格（エージェント不要）。
    プロジェクト内の常時注入層（rules.md）への昇格も同時に行う。"""
    cfg.ltm = True   # promote は明示操作なので ltm を有効化
    rules = promote_rules(cfg)
    if rules:
        print(f"rules.md へ昇格: {', '.join(rules)}")
    mem_dir = ltm_memories_dir(cfg)
    promoted = promote_learnings(cfg)
    print(f"昇格先: {mem_dir}")
    if not promoted:
        hits = count_learn_hits(cfg)
        print(f"昇格対象なし（threshold={cfg.promote_threshold}・既存hits={hits or '無'}）。")
        return 0
    print(f"{len(promoted)} 件を昇格:")
    for src, memid in promoted:
        print(f"  decisions/{src} → {memid}")
    return 0


def cmd_enqueue(cfg: Config, args) -> int:
    """汎用の取り込み口。CLI フラグ・stdin/JSON から検証済み backlog タスクを作る。
    外部ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む。"""
    ensure_dirs(cfg)
    if getattr(args, "json", False):
        try:
            raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
            data = json.loads(raw)
        except (OSError, ValueError) as e:
            print(f"enqueue 失敗: JSON 読込エラー: {e}", file=sys.stderr)
            return 2
        specs = data if isinstance(data, list) else [data]
    else:
        specs = [{"id": args.id, "title": args.title, "verify": args.verify,
                  "priority": args.priority, "source": args.source, "status": args.status,
                  "after": args.after, "review": args.review, "note": args.note,
                  "accept": args.accept, "verify_template": args.verify_template,
                  "repos": _coerce_repos(getattr(args, "repos", None)),
                  "cohort_items": _coerce_repos(getattr(args, "cohort_items", None)),
                  **{k: getattr(args, k, None) for k in TASK_GUIDE_KEYS}}]
    created = []
    for sp in specs:
        if not isinstance(sp, dict):
            print(f"enqueue 失敗: オブジェクトでない要素: {sp!r}", file=sys.stderr)
            return 2
        try:
            created.append(enqueue_task(cfg, sp))
        except ValueError as e:
            print(f"enqueue 失敗: {e}", file=sys.stderr)
            return 2
    for t in created:
        recalled = apply_intake_recall(cfg, t)   # 過去の hold に類似すれば実行前に人の判断へ
        if recalled:
            warn = f"  ⚠ 過去の hold に類似 → 人の判断へ（needs）: {recalled}"
        elif t.verify:
            warn = ""
        elif t.get("accept"):
            warn = "  （accept から実行時に verify を合成）"
        elif t.get("verify_template"):
            warn = "  ⚠ verify_template が未知 → inbox"
        else:
            warn = "  ⚠ verify 未定義 → inbox（人の triage へ）"
        print(f"enqueued {t.id} [{t.norm_status()}] {t.title}{warn}")
    # 投入したその場でレビュー票を用意する。従来はループのパス頭でしか作られず、実行中タスクが
    # 長いとその間ずっと「backlog は承認待ち・要対応画面には無い」状態になり、人は承認できなかった。
    ensure_needs(cfg, created)
    return 0


def cmd_triage(cfg: Config) -> int:
    ensure_dirs(cfg)
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    for t in tasks:                              # 予防リコール: 過去 hold に類似する ready は実行前に人へ
        apply_intake_recall(cfg, t)              # 一致すれば blocked＋needs（_block が persist 済み）
    for t, why in triage(tasks, policy, cfg.plan_review):
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)
    for t in tasks:
        persist_task(cfg, t)
    recover_stale_doing(cfg, tasks)             # 実行者が失踪した doing を ready へ戻す
    ensure_needs(cfg, tasks)                    # 判断待ち（proposed/blocked/review）の票を status から整合
    order = prioritize(tasks, policy, cfg.planner, cfg.model)
    print("優先順位（消化対象）:")
    for i, t in enumerate(order, 1):
        print(f"  {i}. {t.id}: {t.title}")
    return 0


def _run_single(cfg: Config) -> int:
    """1 プロジェクトの単発実行（charter があれば目標駆動・無ければ backlog ループ）。要約を表示する。
    複数 charter（charters/）は全バージョンを順に 1 パスずつ回す。
    マスター憲章のみ（バージョン未作成）は分解せず backlog ループ＝タスク消化と指示の取り込みだけ行う。"""
    names = charter_names(cfg)
    if names:                                        # charter 駆動（plan→execute→evaluate）
        worst = 0
        for name in names:
            worst = max(worst, cmd_project(cfg, charter_name=name))
        reconcile_milestones(cfg)                    # milestone を status へ整合（唯一の調整点）
        return worst
    if _has_master_charter(cfg):
        print("[project] マスター憲章のみ（計画バージョン未作成）— 分解は行わず backlog を消化します。"
              "charters/<名前>.md にやるべきことを書くと計画が始まります。")
    result = run_loop(cfg)
    counts = result["counts"]
    if result.get("level") == "report":              # report: 消化せず計画だけ提示
        plan = result.get("plan", [])
        print(f"\n=== agent-project report（level=report・実行なし）===")
        print(f"実行待ち {len(plan)} 件（この順で回す予定）:")
        for i, tid in enumerate(plan, 1):
            print(f"  {i}. {tid}")
        print(f"人の対応待ち: blocked={counts['blocked']} review={counts.get('review', 0)}")
        return exit_code_for(result)
    print(f"\n=== agent-project 完了（project={cfg.project_name}）===")
    print(f"停止理由 : {result['reason']}（level={result.get('level')}）")
    print(f"サイクル : {result['cycles']}")
    print(f"done={counts['done']} blocked={counts['blocked']} ready={counts['ready']} "
          f"inbox={counts['inbox']} archived={result.get('archived', 0)} "
          f"ingested={len(result.get('ingested', []))} "
          f"promoted={len(result.get('promoted', []))}")
    return exit_code_for(result)


def cmd_run(cfg: Config) -> int:
    _DRAIN_REQUESTED.clear()
    # 起動時に死んだインスタンスのゴミレコードを掃除する。前回の異常終了（kill -9 / クラッシュ /
    # マシン再起動）では finally が走らず *.json が残るため、自分を register する前に一掃して
    # instances の発見ノイズと start の偽の重複検出を防ぐ（prune は自ホストの死レコードを即削除）。
    live = list_instances(prune=True, extra=cfg.registry, use_env=not cfg.profile_mode)
    # 同じプロジェクトを二重に監視させない。start は弾いていたが `run --watch` の直叩きは
    # 素通りで、同じ backlog を 2 つのループが奪い合う（同じタスクを二重実行し、状態ファイルと
    # 決定記録を互いに上書きする）。start は自分を register する前にここを通るので自分自身を
    # 重複とは見ない。--force は start から伝搬する。
    if cfg.watch and not cfg.force:
        me = socket.gethostname()
        mine = str((cfg.source_root or cfg.backlog.parent).resolve())
        dup = [r for r in live
               if str(r.get("root", "")) == mine and str(r.get("host", "")) == me
               and r.get("watch")]
        if dup:
            print(f"既に root={mine} を監視中です（pid={dup[0].get('pid')}）。"
                  f"重複起動は --force、再起動は restart を使ってください。", file=sys.stderr)
            return 1
    ensure_dirs(cfg)
    # 前世代の agent-flow（クラッシュ・電源断で stop を通らず居残ったもの）を刈る。ここを通らないと
    # 残った orchestrator がリースを更新し続け、この後の run_id_for が「まだ実行中」と読んで
    # **続きから再開せず新しい run を作り**、同じタスクを二重実行する（reap_orphan_flow 参照）。
    # manage_flow_daemon=on なら daemon も含めて刈り（ensure_flow_daemon が立て直す）。
    # 既定 off では外部 daemon を残し、orch/worker/都度 run だけ刈る。
    reaped = reap_orphan_flow(cfg)
    if reaped:
        append_journal(cfg.journal,
                       f"前世代の agent-flow を {reaped} プロセス停止（クラッシュの残骸）。"
                       f"run のリースを失効させ、続きから再開できる状態に戻した")
    reg = register_instance(cfg, cfg.registry)   # ローカル＋共有レジストリへ登録（リモート発見）
    hb = lambda: refresh_instance(reg)
    # watch はタスク実行中（エージェント CLI・agent-flow run）に数分〜数十分ブロックする。
    # パス境界の hb だけでは心拍が TTL 切れし、外からは停止したように見えるため、実行中も
    # 打ち続ける別スレッドを立てる（単発 run は即終わるので不要）。
    hb_stop = _start_heartbeat_thread(cfg, reg) if cfg.watch else None
    controller_stop = None
    availability_stop = None
    try:
        # （再）起動直後は駆動より先にリモート状態を取り込む（停止中に viewer が push した
        # charter 更新/指示/フィードバックを、初回パスが古いローカル状態で読まないように）。
        state_sync(cfg)
        if getattr(cfg, "coordination", "") == "git-cas":
            controller_stop = start_controller_heartbeat(cfg)
        ensure_flow_daemon(cfg, cfg.flow_max_workers)   # 実行層 daemon の確保（opt-in・冪等）
        if cfg.watch:
            _install_sigterm(cfg)                # stop の SIGTERM / drain を graceful 停止へ変換
            if getattr(cfg, "availability", None):
                availability_stop = start_availability_monitor(cfg)
            # マスター憲章のみ（バージョン未作成）も project_watch へ: バージョン
            # （charters/<name>.md）が置かれた瞬間に charter 駆動へ入れる（run_watch は
            # charter の追加を監視しないため、ここで振り分けを間違えると気づけない）。
            if getattr(cfg, "coordination", "") == "git-cas":
                run_watch(cfg, heartbeat=hb)      # role は各パスで lease から決め、停止後も自動昇格する
            elif charter_names(cfg) or _has_master_charter(cfg):
                project_watch(cfg, heartbeat=hb)  # 目標を満たすまで回り続ける常駐（全 charter）
            else:
                run_watch(cfg, heartbeat=hb)      # backlog 監視の常駐
            return 0
        return _run_single(cfg)
    except (KeyboardInterrupt, _StopRequested):
        # stop(SIGTERM / commands の stop) / Ctrl-C: graceful 停止。finally でレジストリを掃除し 0 終了。
        if cfg.watch:
            print("\n=== agent-project 停止（stop/SIGTERM/Ctrl-C 受信）===")
        return 0
    except _RestartRequested:
        # 自己更新を適用済み。finally でレジストリを掃除してから新しい本体へ exec する。
        print("\n=== agent-project 自己更新を適用。graceful 再起動します ===")
    finally:
        if controller_stop is not None:
            controller_stop.set()
        if availability_stop is not None:
            availability_stop.set()
        if getattr(cfg, "coordination", "") == "git-cas":
            release_controller_lease(cfg)
        if hb_stop is not None:
            hb_stop.set()          # レジストリを消す前に心拍を止める（無駄打ちを避ける）
        for p in reg:
            try:
                p.unlink()
            except OSError:
                pass
    # _RestartRequested 経由でここに到達（return 済みの正常/停止系は通らない）。後始末後に再起動。
    restart_self(_START_CWD)
    return 0


def _install_sigterm(cfg: "Config | None" = None) -> None:
    """stop からの SIGTERM を KeyboardInterrupt 化して finally で後始末させる（watch 常駐用）。"""
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        if cfg is not None and hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, lambda *_: request_drain(cfg))
    except (ValueError, OSError):  # メインスレッド以外/未対応では無視
        pass


# ---------------------------------------------------------------------------
