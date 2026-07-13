from __future__ import annotations
# mr.py — 元 kiro-project.py の 5363-5900 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# タスク MR（成果物レビュー）— kp/<task-id> → target の MR を作り、承認で自動決着する。
#   GitLab REST v4 を stdlib で直叩きする最小クライアント（kiro-flow executors/gitlab.py の
#   トークン解決・URL 解釈・承認規則の縮小版）。GitLab に到達できなければすべて無害にスキップし、
#   従来どおり「記録のみ」で動く（done の確定は MR に依存させるが、未設定なら従来のまま）。
# ---------------------------------------------------------------------------
_GL_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")
_GL_RC_FILES = ("~/.bashrc", "~/.bash_profile", "~/.profile", "~/.zshrc")


def _gl_token() -> str:
    for k in _GL_TOKEN_ENVS:
        v = os.environ.get(k, "").strip()
        if v:
            return v
    pat = re.compile(r"^\s*(?:export\s+)?(?:GITLAB_TOKEN|GL_TOKEN)=[\"\']?([^\"\'\s]+)")
    for rc in _GL_RC_FILES:
        try:
            for line in Path(rc).expanduser().read_text(encoding="utf-8",
                                                        errors="ignore").splitlines():
                m = pat.match(line)
                if m:
                    return m.group(1)
        except OSError:
            continue
    return ""


def _gl_parse_repo(url: str) -> "tuple[str, str] | None":
    """リポジトリ URL → (host, project_path)。https 形と ssh 形（git@host:group/repo.git）を解釈。"""
    u = (url or "").strip()
    m = re.match(r"^https?://([^/]+)/(.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1), m.group(2)
    return None


def _gl_api(host: str, token: str, method: str, path: str,
            data: "dict | None" = None, params: "dict | None" = None):
    import urllib.error
    import urllib.parse
    import urllib.request
    url = f"https://{host}/api/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"PRIVATE-TOKEN": token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitLab API {method} {path} 失敗: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitLab API {method} {path} へ接続できません: {e.reason}")


def _gl_quote(project: str) -> str:
    import urllib.parse
    return urllib.parse.quote(project, safe="")


def _task_mr_coords(task: "Task") -> "tuple[str, str, str] | None":
    """タスクに記録済みの MR 座標 (host, project, iid)。無ければ None。"""
    iid = str(task.get("mr_iid") or "").strip()
    pj = str(task.get("mr_project") or "")
    if not iid or "|" not in pj:
        return None
    host, proj = pj.split("|", 1)
    return host, proj, iid


def ensure_task_mr(cfg: "Config", task: "Task") -> str:
    """review 到達時に kp/<task-id> → target の MR を用意する（冪等）。
    GitLab 未設定・非 GitLab リポジトリ・API 失敗は ""（記録のみで続行＝done の確定は従来どおり）。"""
    if not getattr(cfg, "task_branch", False):
        return ""
    if task.get("mr_url"):
        return str(task.get("mr_url"))
    spec = _workspace_spec_for(cfg, task)
    if not spec or not spec.get("url"):
        return ""
    parsed = _gl_parse_repo(spec["url"])
    token = _gl_token()
    if not parsed or not token:
        return ""
    host, proj = parsed
    source = task_branch_name(cfg, task)
    target = spec.get("target") or spec.get("base") or "main"
    try:
        ep = _gl_quote(proj)
        found = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests",
                        params={"source_branch": source, "state": "opened"})
        mr = found[0] if isinstance(found, list) and found else None
        if mr is None:
            mr = _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests",
                         data={"source_branch": source, "target_branch": target,
                               "title": f"[kiro-project] {task.id}: {task.title[:80]}",
                               "description": f"kiro-project タスク {task.id} の成果物"
                                              f"（ブランチ {source}。承認でクリーンなら自動マージ）",
                               "remove_source_branch": True})
        task.drop("mr_url", "mr_iid", "mr_project")
        task.extra += [("mr_url", str(mr.get("web_url") or "")),
                       ("mr_iid", str(mr.get("iid") or "")),
                       ("mr_project", f"{host}|{proj}")]
        append_journal(cfg.journal, f"タスク MR 用意: {task.id} → {mr.get('web_url', '')}")
        return str(mr.get("web_url") or "")
    except RuntimeError as e:
        append_journal(cfg.journal, f"タスク MR の用意に失敗（記録のみで続行）: {task.id}: {e}")
        return ""


def finalize_task_mr(cfg: "Config", task: "Task") -> "tuple[bool, str]":
    """approve（検収承認）時にタスク MR を Stage 2（gitlab executor）と同一規則で自動決着する:
    クリーン（コンフリクト無し・未解決ディスカッション無し）→ マージ（ソースブランチ削除）、
    差分なし → クローズ、未クリーン → 差し戻しコメントを付けて (False, 理由)（done にしない）。
    MR 無し・GitLab 未設定は (True, "")＝従来どおり done 確定のみ。"""
    coords = _task_mr_coords(task)
    if coords is None:
        return True, ""
    token = _gl_token()
    if not token:
        return True, "GitLab トークン無し（MR は手動で決着してください）"
    host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        mr = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests/{iid}")
        state = str(mr.get("state") or "")
        if state in ("merged", "closed"):
            return True, f"MR は決着済み（{state}）"
        problems = []
        discussions = _gl_api(host, token, "GET",
                              f"/projects/{ep}/merge_requests/{iid}/discussions",
                              params={"per_page": 100})
        unresolved = sum(1 for d in (discussions if isinstance(discussions, list) else [])
                         if any(n.get("resolvable") and not n.get("resolved")
                                for n in (d.get("notes") or [])))
        changes = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests/{iid}/changes")
        no_diff = isinstance(changes.get("changes"), list) and not changes["changes"]
        conflicts = bool(mr.get("has_conflicts")) or \
            str(mr.get("merge_status") or "") == "cannot_be_merged"
        if unresolved:
            problems.append(f"未解決のレビューコメントが {unresolved} 件")
        if conflicts and not no_diff:
            problems.append(f"コンフリクト（merge_status={mr.get('merge_status')}）")
        if problems:
            why = "; ".join(problems)
            _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                    data={"body": f"kiro-project: # 差し戻し（自動チェック）\n- {why}\n"
                                  "解消後に再度 approve してください。"})
            return False, why
        if no_diff:                              # 差分なし＝マージするものが無い → クローズで決着
            _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                    data={"state_event": "close"})
            return True, "差分なし MR＝クローズで決着"
        _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}/merge",
                data={"should_remove_source_branch": True})
        return True, "MR を自動マージ"
    except RuntimeError as e:
        return False, f"MR の決着に失敗（解消/再試行してください）: {e}"


def close_task_mr(cfg: "Config", task: "Task", reason: str) -> None:
    """却下（reject）時: タスク MR をクローズしソースブランチを削除する（best-effort・
    gitlab-review-viewer の却下と同じ規則）。GitLab 未設定なら何もしない。"""
    coords = _task_mr_coords(task)
    token = _gl_token()
    if coords is None or not token:
        return
    host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                data={"body": f"kiro-project: タスク {task.id} は却下されました（{reason}）。"})
        _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                data={"state_event": "close"})
        branch = task_branch_name(cfg, task)
        _gl_api(host, token, "DELETE",
                f"/projects/{ep}/repository/branches/{_gl_quote(branch)}")
    except RuntimeError as e:
        append_journal(cfg.journal, f"却下 MR の後始末に失敗（無視）: {task.id}: {e}")


def risk_digest(cfg: "Config", task: "Task", changed: "set[str]", protect_hits: list,
                dtok: int = 0, dusd: float = 0.0) -> "tuple[str, str]":
    """承認（review）前のリスクダイジェスト。決定的な材料だけで組み立てる（LLM 不使用・
    gitlab-gatekeeper の「人が 1 枚で決める判断パケット」の薄い移植）。返り値 (level, markdown)。
    level は high > med > low: protect 接触・avoid 類似＝high、リトライ経験・大きな差分・
    自動合成 verify・採点 r=3＝med、どれも無ければ low。承認フロー自体は変えない（情報が増えるだけ）。"""
    lines: "list[str]" = []
    high = med = False
    if protect_hits:
        paths = ", ".join(p for p, _ in protect_hits)
        lines.append(f"- 保護パス接触: {paths[:200]}")
        high = True
    avoided = find_avoidance(cfg, task)
    if avoided:
        src, why = avoided
        lines.append(f"- 過去の回避判断（avoid）に類似: {src} — {why[:160]}")
        high = True
    if task.retries:
        lines.append(f"- リトライ: {task.retries} 回（NG 積み直しを経た成果）")
        med = True
    if changed:
        sample = ", ".join(sorted(changed)[:5])
        more = f" 他 {len(changed) - 5} 件" if len(changed) > 5 else ""
        lines.append(f"- 変更ファイル: {len(changed)} 件（{sample}{more}）")
        if len(changed) >= 10:
            med = True
    vsrc = task.get("verify_source", "")
    if vsrc.startswith("synth"):
        lines.append(f"- verify は自動合成（{vsrc}）。合否基準そのものの妥当性も確認")
        med = True
    assess = task.get("assess", "")
    if assess:
        lines.append(f"- 投入時採点: {assess}（c=複雑さ r=リスク a=曖昧さ・各1-3）")
        m = re.search(r"\br=(\d)", assess)
        if m and int(m.group(1)) >= 3:
            med = True
    if cfg.regression_cmd:
        lines.append(f"- 回帰ゲート: PASS（`{cfg.regression_cmd}`）")
    if dtok or dusd:
        lines.append(f"- コスト: tokens={dtok} usd={dusd:.4f}")
    level = "high" if high else ("med" if med else "low")
    label = {"high": "高", "med": "中", "low": "低"}[level]
    header = f"- 総合: {label}（protect/avoid=高、リトライ・大差分・合成 verify=中）"
    return level, "\n".join([header] + lines)


def _settle_review(cfg, task, act_msg, git_base, branch, ev, vmsg, protect_hits, assisted,
                   policy, reasons, cycle, risk: "tuple[str, str] | None" = None):
    """verify は通ったが承認ゲート対象（review/gate/protect/assisted）→ done せず人の承認(review)へ。
    所在（ref/ブランチ）を gate_* に保持し、approve 時の受領書へ引き継ぐ。"""
    ts = _now_ts()
    ref = extract_delivery_ref(act_msg, cfg, git_base)
    task.status = "review"
    task.drop("gate_ref", "gate_vmsg", "gate_ts", "gate_protect")
    task.set("gate_ref", ref)
    task.set("gate_ts", ts)
    task.set("gate_branch", branch)             # approve 時の受領書に所在（ブランチ）を引き継ぐ
    task.set("gate_vmsg", vmsg.replace("\n", " ")[:200])
    # 「なぜ人の番なのか」を、失敗の理由と読み違えられない書き方にする。ここは verify が通った
    # 成果を人が検収する場面であって、何かが失敗したわけではない（「verify=PASS だが 承認ゲート
    # 対象（review/policy.gate）」とだけ書かれていると、成功したのに失敗理由が並んでいるように
    # 読める、という指摘を受けた）。
    if protect_hits:
        paths = ", ".join(p for p, _ in protect_hits)
        task.set("gate_protect", paths[:200])
        gate_why = f"保護パス（protect）に触れているため人の確認が要る: {paths[:160]}"
    elif assisted and not needs_human_review(task, policy):
        gate_why = "自律レベルが assisted（done の確定は人が行う設定）"
    else:
        gate_why = "このタスクが承認ゲートの対象（review / policy.gate）"
    disp = (f"（保護パス: {paths[:80]}）" if protect_hits
            else "（assisted）" if assisted else "（承認ゲート）")
    reasons[task.id] = ("検収待ち（verify=PASS・保護パス変更。approve で done 確定）"
                        if protect_hits else "検収待ち（verify=PASS。approve で done 確定）")
    # 成果物レビューの MR: タスクブランチ（kp/<id>）→ target の MR を用意し（冪等・GitLab 設定時のみ）、
    # 承認（approve）時に Stage 2 と同じ規則（クリーンなら自動マージ）で決着させる
    mr_url = ensure_task_mr(cfg, task)
    if mr_url:
        ev = (ev + "\n" if ev else "") + f"- MR: {mr_url}（承認時にクリーンなら自動マージ）"
        if not ref:
            task.set("gate_ref", mr_url)
    persist_task(cfg, task)
    write_needs_file(cfg, task,
                     f"検証は通っている（verify=PASS）。人の検収を待っている理由: {gate_why}。"
                     f"内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す",
                     review=True, evidence=ev, risk=risk)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 検収待ち{disp} — {ref}")


def _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle, autonomy_cache):
    """verify=PASS かつゲート対象外 → 無人 auto-done（受領書＋archive）。集計 delta を返す。"""
    task.status = "done"
    autonomy_record(cfg, task, clean=True, cache=autonomy_cache)        # 無人 auto-done＝clean 実績
    ts = _now_ts()
    ref = extract_delivery_ref(act_msg, cfg, git_base)   # 成果参照（baseline 以降の新規のみ）
    if dtok or dusd:                                  # コストを納品書に残し stats で集計可能に
        task.extra.append(("cost", f"tokens={dtok} usd={dusd:.4f}"))
    append_delivery(cfg, task, ref, ts, branch=branch)   # 受領書一覧に追記（所在ブランチ併記）
    if cfg.do_archive:
        archive_task(cfg, task, vmsg, ref, ts, evidence=ev)  # backlog → archive/（納品書＋判断材料）
        done_disp = "DONE → archive（納品書）"
    else:
        delete_task_file(cfg, task)
        done_disp = "DONE 削除"
    clear_needs_file(cfg, task.id)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} {done_disp} — {ref}")
    return {"archived": 1 if cfg.do_archive else 0, "followups": parse_followups(task, act_msg)}


def _flow_failure_blob(cfg, task) -> str:
    """このタスクの直近 run（last_run）の失敗情報（meta.failure_reason + final summary）。
    act の stdout 末尾（vmsg）はトリアージタグが切れていることがあるため、bus 側も見る。"""
    rid = str(task.get("last_run") or "").strip()
    if not rid or rid != os.path.basename(rid):
        return ""
    parts = []
    for name, key in (("meta.json", "failure_reason"), ("final.json", "summary")):
        try:
            data = json.loads((cfg.bus / "runs" / rid / name).read_text(encoding="utf-8"))
            parts.append(str(data.get(key) or ""))
        except (OSError, ValueError):
            continue
    return "\n".join(p for p in parts if p)


def _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location="local"):
    """verify=NG → 上限内なら積み直し / 学習で自動解決 / 上限超で人へエスカレーション。
    委譲 executor（gitlab）の却下なら、人コメント（やり直し指示）を次 act の feedback に注入する。

    その前に**失敗トリアージ**: 失敗が環境要因（quota=利用上限 / auth=認証切れ / env=CLI・
    モデルの問題）なら、これはタスクの内容と無関係で、リトライしても同じ理由で全タスクが
    落ち続ける。リトライを焼かず・裁定（これも LLM 呼び出し＝同じ理由で失敗する）も呼ばず、
    原因と直し方を明記して人へ回す。環境を直して approve すれば同じ run の続きから再開する。"""
    triage = classify_agent_failure(f"{vmsg}\n{_flow_failure_blob(cfg, task)}")
    if triage and triage[0] in AGENT_ERROR_ENV_CLASSES:
        cls, hint = triage
        label = {"quota": "利用上限", "auth": "認証切れ", "env": "実行環境の問題"}[cls]
        _block(cfg, task, f"[agent-error:{cls}] 環境の問題（{label}）: {hint} "
                          "タスクの内容の問題ではないため、リトライ回数は消費していません。"
                          "環境を直してから approve すると、同じ run の続き（失敗した工程だけ）"
                          "から再開します。", reasons, evidence=ev)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（環境の問題: {label}。"
                                    f"リトライ・裁定は消費しない）")
        return
    task.retries += 1
    if not task.verify:
        _escalate(cfg, task, "verify 未定義", reasons, cycle, evidence=ev)
        if task.norm_status() == "blocked":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
    elif task.retries > cfg.max_retries:
        learned = find_learned_resolution(cfg, task) if cfg.learn else None
        if learned and not task.get("autolearned"):
            src, guide = learned
            task.drop("feedback", "autolearned")
            task.extra += [("feedback", guide.replace("\n", " ⏎ ")), ("autolearned", "1")]
            task.status = "ready"
            persist_task(cfg, task)
            append_decision(cfg, task.id, "auto",
                            context=f"{task.id}（{task.title}）を学習で自動解決",
                            action="auto-resolve", reason=f"learned from {src}: {guide[:120]}",
                            affects=f"{task.id} → ready")
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} 学習で自動解決"
                                        f"（{src} に倣う・通知を抑制）")
        else:
            _escalate(cfg, task, f"繰り返し NG（retries={task.retries}）: {vmsg}", reasons, cycle,
                      evidence=ev)
            if task.norm_status() == "blocked":
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（繰り返し NG）")
    else:
        task.status = "ready"
        # 委譲 executor の却下: 人コメント（やり直し指示）を feedback に載せて次 act で活かす。
        # コメントが無ければ空＝注入なし（ワーカーが自動で原因判断してやり直す）。
        if executor_delegates(cfg):
            guidance = read_reject_guidance(cfg, location == "remote")
            if guidance:
                task.drop("feedback")
                task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} 却下コメントを次 act に注入")
                # cohort メンバ/pilot の却下なら、同 cohort の未完了メンバへ指摘を波及（兄弟に同じ轍を踏ませない）。
                cohort_reflux(cfg, task, guidance)
                # 同一タスクの再試行に注入するだけでなく、**横断学習ストアにも蒸留して残す**。
                # これで似たタスク（find_learned_resolution）・別プロジェクト（links）・ltm へ還元される。
                # 対象は人と判別済みの gitlab 人コメント（判別は executor 側 _human_notes）。
                if cfg.learn_capture:
                    append_decision(cfg, task.id, "gitlab",
                                    context=f"{task.id}（{task.title}）が gitlab で却下",
                                    action="gitlab-reject", reason=guidance[:300],
                                    affects=f"{task.id} → ready（次 act に反映）",
                                    learn=distill_learn(cfg, task.title, guidance))
                    # 系の反復検知（昇格ラダー）: 同種の gitlab 却下が閾値に達したら、silent 積み直しを
                    # やめて「分解/verify/policy の見直し」を人へ提案する（＝系の再考へ格上げ）。
                    if cfg.reject_recur > 0 and \
                            count_gitlab_reject_recur(cfg, task) + 1 >= cfg.reject_recur:
                        _escalate(cfg, task,
                                  f"系の再考: 同種タスクの gitlab 却下が反復（≥{cfg.reject_recur} 件）。"
                                  "個別のやり直しでなく、タスク分解・verify・policy の見直しを検討してください。"
                                  f" 直近の指摘: {guidance[:200]}", reasons, cycle, evidence=ev)
                        append_journal(cfg.journal,
                                       f"cycle {cycle}: {task.id} → 人の判断（系の再考・却下反復）")
                        return
        persist_task(cfg, task)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} NG 積み直し "
                                    f"({task.retries}/{cfg.max_retries}) — {vmsg}")


def _settle_task(cfg: "Config", task: "Task", location: str, act_msg: str, cycle: int,
                 dtok: int, dusd: float, git_base, verify_env, policy: "Policy",
                 autonomy_cache: dict, reasons: dict) -> dict:
    """act 済みタスクを検証ゲート（verify→回帰→保護→進捗→flake）に通し、done/review/retry/escalate を
    確定する。副作用（persist/journal/needs/decision/delivery/archive）は内部で行い、run_loop が集計に使う
    deltas（archived・followups）を返す。run_loop の per-task 本体を 1 か所に切り出したもの（挙動は不変）。"""
    # act 中に人が revise（軌道修正）していたら、この試行の結果は確定せず修正内容で積み直す。
    # verify より先に判定する（方向の変わった成果に PASS/FAIL を付けない・verify コストも省く）。
    fresh = _load_task_file(cfg, task.id)
    if fresh is not None and fresh.get("revised"):
        _requeue_revised(cfg, task, fresh, cycle)
        return {"archived": 0, "followups": []}
    if location != "local":
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} を {location} で実行"
                       + (f"（{cfg.git_bus}）" if location == "remote" else ""))

    # 人が「成果物の所在（リポジトリ/ブランチ/コミット）・差分・検証」を見て判断できる材料。
    # needs（判断待ち）と DELIVERY/archive（受領）双方に載せる。
    branch = _current_branch(cfg)
    regressed = False
    vtmp = None
    try:
        # workspace 指定タスクは git-bus ルート（workdir）でなく該当 repo のクローン内（指定 branch・
        # クローンのルート）で検証する。verify はリポジトリ直下からの相対で書かれる規約なので path
        # 配下には潜らない。明示 verify_cwd はそれを優先。
        vcwd, vtmp = _task_verify_cwd(cfg, task)
        venv = verify_env
        if vtmp and (vcwd / ".git").exists():          # 一時 clone は差分基準を clone の HEAD に取り直す
            head = _git_out(vcwd, "rev-parse", "HEAD").strip()
            venv = {"KIRO_BASE_REV": head} if head else None
        ok, flaky, vmsg = run_verify_stable(task.verify, vcwd, cfg.verify_timeout,
                                            cfg.verify_confirm, venv)
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok, task=task)
        if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
            rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
            if not rok:
                regressed = True
                if cfg.regression_revert:
                    _revert_workdir(cfg)
                _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons,
                       evidence=ev)
                autonomy_record(cfg, task, clean=False, cache=autonomy_cache)   # 手戻り（track 信頼を下げる）
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（回帰検知）"
                               + ("・revert 済" if cfg.regression_revert else ""))
    except RuntimeError as e:      # workspace clone 失敗等は黙って workdir に倒さず NG（成果の無い場所で誤判定しない）
        ok, flaky, vmsg = False, False, str(e)[:500]
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok, task=task)
    finally:
        if vtmp:
            shutil.rmtree(vtmp, ignore_errors=True)
            _prune_caches(_provisioned_urls)   # 共有 cache の worktree 登録を回収（本体は残す）

    changed: set = set()
    protect_hits: list = []
    if ok and not flaky and not regressed:
        # 成果差分は **作業ブランチ（kp/<task-id>）** から取る。cfg.workdir は状態 worktree を指す
        # ので、そこを見ると bus/ の claims/events が「変更ファイル」として並び、保護パス判定も
        # リスク判定（大差分＝med）も実体と無関係な数字で動いてしまう。
        wb = _task_work_branch(cfg, task)
        if wb:
            _ref, _files = work_branch_changes(cfg, wb[0], wb[1])
            changed = set(_files)
        if not changed:                               # 作業ブランチが無い（単発実行等）は従来どおり
            changed = meaningful_changes(cfg, git_base)
        if policy.protect:                             # act が保護パスを触ったか（safety denylist）
            protect_hits = sorted({(p, m) for p in changed
                                   if (m := path_protected(p, policy.protect))})
    # no-progress: verify=PASS でも変更ゼロ＝履歴一致 verify による偽 done の疑い（opt-in）
    _expect = task.get("expect", "")
    require_prog = ((cfg.require_progress or _expect == "changes") and _expect != "none"
                    and (cfg.workdir / ".git").exists())
    no_progress = (ok and not flaky and not regressed and require_prog and not changed)
    # red-green: 合成 verify が act 前ツリーでも PASS＝この変更を弁別していない（偽 done）。
    # no-progress（変更ゼロ）の上位互換で、変更があっても verify がそれを追えていないケースを弾く。
    undiscriminating = (ok and not flaky and not regressed and not no_progress
                        and verify_undiscriminating(cfg, task, cfg.workdir,
                                                     vtmp is not None, git_base, verify_env))
    # 実効自律レベル（明示 - level: > track 自動昇格 > グローバル）。report は選択時に除外済み
    assisted = resolve_level(task, cfg, autonomy_cache) == "assisted"

    if flaky:
        # verify が不安定（flake）→ 自動修正せず人へ隔離（NG churn / flaky PASS の done を防ぐ）
        task.set("flake", "1")
        _block(cfg, task, f"flake 検知（verify 不安定・自動修正せず隔離）: {vmsg}", reasons, evidence=ev)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（flake 検知・quarantine）")
    elif regressed:
        pass                                  # 既に blocked 化済み。done/review にしない
    elif no_progress:
        # verify=PASS だが act が何も変更していない＝履歴一致 verify 等による偽 done の疑い → 人へ
        task.set("noprogress", "1")
        _block(cfg, task, "no-progress: verify=PASS だが baseline 以降の変更が無い"
               "（履歴一致 verify による偽 done の疑い。verify を差分基準で見直すか expect: none を付与）",
               reasons, evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)       # 偽 done 疑い＝手戻り
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（no-progress・偽 done 疑い）")
    elif undiscriminating:
        # verify=PASS だが act 前のツリーでも PASS＝この変更を弁別していない（恒真式/既存状態/履歴一致）→ 人へ
        task.set("undiscriminating", "1")
        _block(cfg, task, "red-green: verify が act 前のツリーでも PASS＝この変更を弁別していない"
               "（偽 done の疑い。verify を望む最終状態/差分の assert に見直す。除外は - verify_validate: none）",
               reasons, evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)        # 偽 done 疑い＝手戻り
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（red-green・変更を弁別しない verify）")
    elif ok and (getattr(cfg, "delivery_review", False)
                 or needs_human_review(task, policy) or protect_hits or assisted):
        # delivery_review（既定 on）: verify PASS 後は level に依らず常に人の検収（review）へ
        _settle_review(cfg, task, act_msg, git_base, branch, ev, vmsg, protect_hits, assisted,
                       policy, reasons, cycle,
                       risk=risk_digest(cfg, task, changed, protect_hits, dtok, dusd))
    elif ok:
        capture_approve_learn(cfg, task, location)   # 承認時の人コメント（正例）を横断 learn 化
        save_validated_verify(cfg, task)             # 通った自動生成 verify を再利用ライブラリへ
        return _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle,
                            autonomy_cache)
    else:
        _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location)
    return {"archived": 0, "followups": []}


def _run_setup(cfg: "Config") -> tuple:
    """run_loop の前処理: inbox 取り込み → 読み込み → 人のフィードバック解除 → triage/rot で
    ready/blocked を確定 → verify を用意する。(tasks, policy, reasons, ingested, inboxed, pre_blocked)。"""
    ensure_dirs(cfg)
    ingest_commands(cfg)          # 人の指示（approve/hold/pin/defer/revise のファイルドロップ）を先に適用
    inboxed = run_intake(cfg) + ingest_inbox(cfg)     # 取り込みコマンド＋外部ドロップ(inbox/)を backlog へ
    tasks = load_tasks(cfg.backlog)
    recover_revised(cfg, tasks)   # 実行側が settle できなかった revise 予約の回収（クラッシュ自己回復）
    recover_stale_doing(cfg, tasks)   # 実行者が失踪した doing を ready へ戻す（再起動/クラッシュ自己回復）
    policy = load_policy(cfg.policy)
    reasons: dict[str, str] = {}
    ingested = ingest_feedback(cfg, tasks)           # 人のフィードバックでブロック解除
    pre_blocked = {t.id for t in tasks if t.norm_status() in ("blocked", "review", "proposed")}
    transitions = list(triage(tasks, policy, cfg.plan_review))   # inbox→ready/proposed 昇格・deny→blocked
    if cfg.rot:                                       # rot 検知（古い/重複/実行不能を掃除）
        transitions += [(t, f"rot: {why}") for t, why in detect_rot(cfg, tasks)]
    for t, why in transitions:
        if t.norm_status() != "blocked":
            t.status = "blocked"
        reasons[t.id] = why
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)
    for t in tasks:                                   # accept/verify_template から concrete な verify を用意
        if t.norm_status() in CONSUMABLE and not t.verify and ensure_verify(cfg, t):
            persist_task(cfg, t)
            append_journal(cfg.journal, f"verify 用意: {t.id} ← {t.get('verify_source')}")
    if cfg.assess:                                    # 投入時アセスメント（1 タスク 1 回・実行可否は不変）
        for t in tasks:
            if t.norm_status() in ("proposed", "ready", "inbox") and not t.get("assess"):
                assess_task(cfg, t)
                persist_task(cfg, t)
    tasks += route_spec_tasks(cfg, tasks, policy)     # spec ルーティング（opt-in・spec 前段を前置）
    tasks += expand_spec_tasks(cfg, tasks)            # 承認済み spec の tasks.md を実装タスクへ展開
    ensure_needs(cfg, tasks)                          # 判断待ち（proposed/blocked/review）の票を status から整合
    return tasks, policy, reasons, ingested, inboxed, pre_blocked


def _budget_reason(cfg: "Config", cycle: int, start: float,
                   tokens_used: int, cost_used: float) -> "str | None":
    """予算ゲート: サイクル/実時間/トークン/コスト/ソフト(throttle) の上限到達なら停止理由を返す。"""
    if cycle >= cfg.max_cycles:
        return REASON_BUDGET
    if cfg.max_seconds and (time.time() - start) >= cfg.max_seconds:
        return REASON_BUDGET
    if cfg.max_tokens and tokens_used >= cfg.max_tokens:
        return REASON_COST
    if cfg.max_cost and cost_used >= cfg.max_cost:
        return REASON_COST
    if cfg.throttle > 0 and (                 # ソフト予算: ハード上限の手前で緩やかに打ち切る
        (cfg.max_tokens and tokens_used >= cfg.throttle * cfg.max_tokens)
        or (cfg.max_cost and cost_used >= cfg.throttle * cfg.max_cost)):
        return REASON_THROTTLE
    return None


# ---------------------------------------------------------------------------
