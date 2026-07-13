from __future__ import annotations
# project.py — 元 kiro-project.py の 9797-10414 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
def _acceptance_cwd(cfg: "Config", charter: "Charter") -> "tuple[Path, str | None]":
    """acceptance を実行する作業ディレクトリと、片付けが要る一時 clone のパス（無ければ None）を返す。
    優先順位: 明示 verify_cwd > 単一対象 repo の一時 clone（target ブランチ＝worker の push 先）> workdir。
    git-bus 等で workdir に成果が出ないケースに対応する。"""
    if cfg.verify_cwd:
        return resolve_verify_cwd(cfg), None
    spec = _charter_single_repo(charter)
    if spec:
        tmp = tempfile.mkdtemp(prefix="kiro-accept-")
        dest = str(Path(tmp) / "repo")
        branch = spec.get("target") or spec.get("base") or ""
        try:
            _clone_repo_shallow(spec["url"], branch, dest)
        except (OSError, RuntimeError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"対象 repo の clone 失敗（{spec['url']}@{branch or '既定'}）: {e}") from e
        append_journal(cfg.journal, f"project acceptance: {spec['url']}@{branch or '既定'}"
                                    " を clone して検証")
        return Path(dest), tmp
    return cfg.workdir, None


def evaluate_acceptance(cfg: "Config", charter: "Charter") -> "tuple[int, int, list]":
    """charter の acceptance（受入 verify）を実行し (passed, total, [(cmd, ok, msg)]) を返す。
    プロジェクト done の唯一の根拠＝全 PASS。実行先は 明示 verify_cwd > 単一 repo の一時 clone > workdir。
    clone は worker の push 先（target ブランチ）を反映するため毎評価で取り直す。clone 失敗は全 NG 扱い
    （workdir へ黙ってフォールバックすると成果の無い場所で誤判定するため）。"""
    try:
        wd, tmp = _acceptance_cwd(cfg, charter)
    except RuntimeError as e:
        append_journal(cfg.journal, f"project acceptance: {e} → 全 NG 扱い")
        return 0, len(charter.acceptance), [(c, False, str(e)[:500]) for c in charter.acceptance]
    try:
        env = None
        if (wd / ".git").exists():
            head = _git_out(wd, "rev-parse", "HEAD").strip()
            if head:
                env = {"KIRO_BASE_REV": head}
        results = []
        for cmd in charter.acceptance:
            ok, _flaky, msg = run_verify_stable(cmd, wd, cfg.verify_timeout,
                                                cfg.verify_confirm, env)
            results.append((cmd, ok, msg))
        passed = sum(1 for _, ok, _ in results if ok)
        return passed, len(results), results
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
            _prune_caches(_provisioned_urls)   # 共有 cache の worktree 登録を回収（本体は残す）


def _acceptance_kind(line: str) -> "tuple[str, str]":
    """acceptance 1 行を (kind, text) に分類する。kind は 'command'（決定的シェル・そのまま実行）
    か 'accept'（自然言語・要合成）。明示の `accept:` 接頭辞、または『シェルに見えない散文』
    （全角句読点を含む等）を自然言語とみなす。散文をそのまま shell に流して誤実行するのを防ぐため、
    判定不明な行は command でなく accept（合成 → 失敗時は人へ）に倒す。"""
    s = line.strip()
    m = _ACCEPT_PREFIX_RE.match(s)
    if m:
        return "accept", m.group("text").strip()
    if _looks_like_shell_command(s):
        return "command", s
    return "accept", s


def resolve_charter_acceptance(cfg: "Config", charter: "Charter", state: "dict | None" = None,
                               kiro_run=None) -> "tuple[list[str], list[str]]":
    """charter.acceptance の各行を実行可能なシェルコマンドへ解決し (resolved, unresolved) を返す。
    決定的コマンドはそのまま、自然言語（`accept:` 接頭辞 or 散文）はエージェントが決定的 verify を合成する
    （タスクの synth_verify を流用＝偽 done 防止規則を織込）。合成結果は state['acceptance_synth'] に
    原文キーでキャッシュし、サイクル/再実行をまたいで done 基準（acceptance）を安定させる（毎回の再合成と
    非決定的なブレを防ぐ）。合成できない自然言語は unresolved に積み、呼び出し側が done 判定不能として人へ回す。"""
    cache = dict((state or {}).get("acceptance_synth") or {})
    resolved: "list[str]" = []
    unresolved: "list[str]" = []
    for line in charter.acceptance:
        kind, text = _acceptance_kind(line)
        if kind == "command":
            resolved.append(text)
            continue
        cmd = cache.get(text)
        if not cmd:
            cmd = synth_verify(cfg, charter.name or "project", text, kiro_run)
            if cmd:
                cache[text] = cmd
        if cmd:
            resolved.append(cmd)
        else:
            unresolved.append(text)
    if state is not None:
        state["acceptance_synth"] = cache
    return resolved, unresolved


def _acceptance_specs(cmds: "list[str]") -> "list[dict]":
    """acceptance コマンドを、それ自体を verify とするタスク spec にする（決定的・的が外れない）。
    verify は charter に書かれた受入条件そのもの＝人が入力した条件で done を判定する。"""
    return [{"title": f"受入条件を満たす: {cmd}"[:120], "verify": cmd, "source": "acceptance"}
            for cmd in cmds]


def _failing_acceptance_specs(results: "list") -> "list[dict]":
    """未達 acceptance を、それ自体を verify とする改善タスク spec にする（決定的・的が外れない）。"""
    return _acceptance_specs([cmd for cmd, ok, _ in results if not ok])


def write_milestone(cfg: "Config", charter: "Charter", reason: str, summary: str,
                    pid: "str | None" = None, version: str = "") -> None:
    """収束候補/要対応を milestone として needs/<pid>.md に出す（検収ゲートのプロジェクト版）。
    複数 charter 運用では pid が `<project>-<charter名>` になり charter 別に分かれる。
    version（バージョン名）を渡すと見出しに使う: charter の `# Charter:` 宣言名は前バージョンの
    コピー等でプロジェクト名のまま食い違うことがあるため、バージョンの識別はファイル名（version）を
    正とする（viewer の表示も同じ規則）。"""
    pid = pid or _project_id(cfg, charter)
    # 見出し: 複数バージョン運用はバージョン名、単一運用は charter の宣言名。
    # 宣言名がバージョン名と別に意味を持つ場合だけ併記する（「v2」ではなく「v2（保守）」等）。
    heading = charter.name
    if version:
        heading = version if charter.name in ("", "project", version, cfg.project_name) \
            else f"{version}（{charter.name}）"
    cfg.needs.mkdir(parents=True, exist_ok=True)
    labels = {
        REASON_PROJECT_CONVERGED: "収束候補（acceptance 全 PASS・改善ゼロ）",
        REASON_PROJECT_STALL: "停滞（acceptance PASS 数が増えない→人へ）",
        REASON_PROJECT_BUDGET: "サイクル予算到達（人の判断待ち）",
        REASON_PROJECT_COST: "コスト予算到達（人の判断待ち）",
        REASON_PROJECT_BLOCKED: "内側ループが人へエスカレーション",
        REASON_PROJECT_NO_ACCEPTANCE: "acceptance 未定義（done 判定不能→人へ）",
    }
    hint = (
        f"<!-- 完了として受領するなら `kiro-project approve {pid} --reason ...`（プロジェクト done）。\n"
        f"     次フェーズへ続けるなら charter.md の goal/acceptance を更新して再実行。\n"
        f"     方向修正なら下に方針を書いて [x]（または policy.md を編集）。 -->\n")
    body = (
        f"{_madr_frontmatter(pid, 'milestone')}"
        f"# マイルストーン: {heading}\n\n"
        f"## Context and Problem Statement\n\n"
        f"- なぜ: {labels.get(reason, reason)}\n"
        f"- 状態: {reason}\n"
        f"- 概況: {summary}\n\n"
        f"## goal\n{charter.goal}\n\n"
        f"{DECISION_MARKER}\n\n"
        f"<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
        f"{hint}")
    (cfg.needs / f"{pid}.md").write_text(body, encoding="utf-8")


_NEEDS_KIND_RE = re.compile(r"(?m)^kind:\s*(\S+)")


def _needs_kind(path: Path) -> str:
    """needs/<id>.md の frontmatter kind を読む（milestone / plan-review / review / blocked）。"""
    try:
        head = path.read_text(encoding="utf-8")[:400]
    except OSError:
        return ""
    m = _NEEDS_KIND_RE.search(head)
    return m.group(1) if m else ""


def reconcile_milestones(cfg: "Config") -> None:
    """milestone ファイル（needs/<pid>.md, kind=milestone）を project.json の status に一致させる
    唯一の調整点（GC）。この関数だけが「milestone を残すか消すか」を決める＝milestone は status の
    純粋な投影になり、署名比較の綻び・承認失敗・バージョン削除・旧トップレベル残存などで
    milestone が『復活』しても、毎パス確実に status に合わせて掃除される（根本対策）。

    残すのは MILESTONE_STATUSES（converged/no-acceptance/blocked/stall/budget/cost）の、いま存在する
    バージョンの milestone だけ。承認済み（accepted）・もう無いバージョン・旧トップレベル
    （<project>.md、バージョン運用時）の milestone は消す。タスク級の needs（plan-review/review/
    blocked タスク）は kind で除外して触らない。"""
    if not cfg.needs.exists():
        return
    data = load_project_state(cfg)
    names = charter_names(cfg)
    # 有効な pid → status。単一 charter は top-level、バージョン運用は各 version の state だけ
    # （トップレベル state は無効）、マスターのみ（names 空）は有効ゼロ＝全 milestone を消す。
    valid: "dict[str, str]" = {}
    if names == ["default"]:
        if data.get("id"):
            valid[str(data["id"])] = str(data.get("status") or "")
    else:
        charters = data.get("charters") or {}
        for name in names:
            st = charters.get(name) or {}
            if st.get("id"):
                valid[str(st["id"])] = str(st.get("status") or "")
    for nf in sorted(cfg.needs.glob("*.md")):
        if _needs_kind(nf) != "milestone":
            continue
        status = valid.get(nf.stem)
        if status is None or status not in MILESTONE_STATUSES:
            try:
                nf.unlink()
            except OSError:
                pass


def finalize_project(cfg: "Config", state: dict, reason: str,
                     charter: "Charter | None" = None,
                     charter_name: "str | None" = None) -> None:
    """プロジェクト（charter）を done 確定する。最終納品書を残し state を accepted に。
    charter を渡すと accepted_charter_sig を記録し、次回 run は cmd_project 冒頭のガードで
    charter.md が変わるまで再実行しない（accepted 直後に再収束して milestone が復活するのを防ぐ）。"""
    pid = state.get("id", "project")
    name = state.get("name", pid)
    total = int(state.get("acceptance_total", 0))
    ts = _now_ts()
    summary = f"acceptance {total}/{total} PASS"
    final = Task(id=pid, title=f"[project] {name}", status="done",
                 source="project", verify=f"acceptance×{total}")
    append_delivery(cfg, final, summary, ts)
    append_decision(cfg, pid, "user", context=f"プロジェクト『{name}』を完了として受領",
                    action="project-accept", reason=reason, affects=summary)
    clear_needs_file(cfg, pid)
    state["status"] = REASON_PROJECT_ACCEPTED
    if charter is not None:
        state["accepted_charter_sig"] = _charter_full_signature(charter)
    save_charter_state(cfg, state, charter_name)


def project_exit_code(reason: str) -> int:
    if reason == REASON_PROJECT_ACCEPTED:
        return 0
    if reason in (REASON_PROJECT_BUDGET, REASON_PROJECT_COST):
        return 2
    return 1   # converged / no-progress / blocked / no-acceptance は人の対応待ち


def _project_evaluate(cfg: "Config", charter: "Charter", pid: str, state: dict,
                      cycle: int, cost_used: float, review_fn,
                      charter_tag: str = "") -> "tuple[str | None, str]":
    """③ evaluate: acceptance 評価 → 未達/レビュー所見を改善タスク化 → 収束/コスト/停滞を判定する。
    停止すべきなら停止理由を、続行なら None を返す（last_summary も返す）。state(history/best/stall) を更新。
    charter_tag（複数 charter 運用）を渡すと改善タスクにタグを付け、冪等照合もその charter に閉じる。"""
    passed, total, results = evaluate_acceptance(cfg, charter)
    state["history"] = list(state.get("history", [])) + [passed]
    # best（過去最高 PASS 数）は停滞判定の基準であると同時に、viewer の「n / m 達成」の表示元。
    # 停滞判定より先にここで更新する: 下の収束 return より後ろで更新していたため、一発で全 PASS
    # して収束したプロジェクトは best が 0 のまま残り、完了しているのに「0 / 1 達成」と出ていた。
    # 停滞判定は更新前の値（prev_best）と比べる＝従来の意味を変えない。
    prev_best = int(state.get("best", 0))
    state["best"] = max(prev_best, passed)
    existing = _existing_titles(cfg, charter_tag or None)

    def _tag(specs: "list[dict]") -> "list[dict]":
        if charter_tag:
            for sp in specs:
                sp.setdefault("charter", charter_tag)
        return specs

    improved: list[Task] = []
    if passed < total:                        # 未達 acceptance を、それ自体を verify とする改善タスクへ
        improved += _enqueue_specs(cfg, _tag(_failing_acceptance_specs(results)),
                                   existing, cfg.learn_threshold, charter=charter_tag or None)
    findings: list[dict] = []
    if cfg.review_project and passed == total:  # 短絡的達成を疑い敵対的レビュー（opt-in）
        findings = _tag(review_fn(charter))
        improved += _enqueue_specs(cfg, findings, existing, cfg.learn_threshold,
                                   charter=charter_tag or None)
    last_summary = (f"cycle {cycle}: acceptance {passed}/{total} PASS, "
                    f"改善 {len(improved)} 件, cost={cost_used:.4f}")
    append_decision(cfg, pid, "auto",
                    context=f"cycle {cycle}: acceptance {passed}/{total} PASS",
                    action="project-evaluate",
                    reason=("収束候補" if passed == total and not improved else "改善継続"),
                    affects=f"改善 {len(improved)} 件 / findings {len(findings)}")
    append_journal(cfg.journal, "project " + last_summary)
    if passed == total and not improved:      # 収束: acceptance 全 PASS かつ改善ゼロ
        return REASON_PROJECT_CONVERGED, last_summary
    if cfg.max_project_cost and cost_used >= cfg.max_project_cost:
        return REASON_PROJECT_COST, last_summary
    if passed > prev_best:                    # 停滞: PASS 数が過去最高を更新しないなら人へ（自動チャーン止め）
        state["stall"] = 0
    else:
        state["stall"] = int(state.get("stall", 0)) + 1
    if state["stall"] >= cfg.project_stall:
        return REASON_PROJECT_STALL, last_summary
    return None, last_summary


def cmd_project(cfg: "Config", planner=None, reviewer=None, runner=run_loop, heartbeat=None,
                kiro_run=None, charter_name: "str | None" = None) -> int:
    """charter 駆動の plan→execute→evaluate ループ（1 charter の 1 パス。`run` が charter 検出時に呼ぶ）。
    charter_name（charters/<name>.md）を渡すとその charter だけを回す（複数 charter 運用）。
    planner/reviewer/runner/kiro_run は テストのため注入可能（既定はエージェント委譲＋正準ループ）。"""
    ensure_dirs(cfg)
    charter = _load_named_charter(cfg, charter_name)
    multi = _is_multi_charter(cfg, charter_name)
    if charter is None:
        print(f"エラー: charter が見つかりません: {cfg.charter}", file=sys.stderr)
        print("  ヒント: 目標/制約/前提/成果物/acceptance を charter.md に書いてください。",
              file=sys.stderr)
        return 2
    # 人の指示（commands/ ドロップ）を入口で消化する。従来は execute（run_loop）内でしか
    # 取り込まれず、下の accepted ガードで早期 return するパスでは承認/却下/replan の指示ファイルが
    # 何パスも放置され、watch が空振り起床を繰り返した末に、状態が変わってから取り込まれて
    # exit 2（.err 退避）になる実害があった（viewer の承認ボタンが「押しても何も起きない」の一因）。
    ingest_commands(cfg)
    # 人からの再分解要求（エラー回復）を入口で消費する（one-shot）。ここで消しておくことで、
    # acceptance 未定義など早期 return するパスでもマーカーが残らず、has_work の空振り起床が
    # 続かない（要求は消化済み＝下の plan ゲートで charter 無変更でも一発だけ plan を強制する）。
    # 直前の ingest_commands が replan 指示をマーカー化した場合も、この場で拾って同一パスで反映する。
    replan_req = consume_replan_request(cfg, charter_name if multi else None)
    problems = validate_charter(charter)
    if problems:
        print(f"エラー: charter の repos 定義が不正です（{cfg.charter}）:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("  ヒント: 各 repo に `- desc:`（説明・必須）と `- base:`（ベースブランチ・必須）を、"
              "必要なら `- target:`（既定 base）を付けてください。", file=sys.stderr)
        return 2
    pid = _project_id(cfg, charter) + (f"-{charter_name}" if multi else "")
    # バージョン運用（multi）では、旧・単一 charter 時代のトップレベル milestone
    # （needs/<project>.md、バージョン接尾辞なし）は不要。単一 charter で一度 run した後に
    # charters/ を足す（バージョン運用へ移行する）と、この古い milestone が残って「マイルストーン」が
    # 二重に見える（<project>.md と <project>-<version>.md）。バージョン運用のパスに入ったら常に
    # 掃除する（accepted ガードや no-acceptance で早期 return するパスより前で行う＝取り残さない）。
    if multi:
        base_pid = _project_id(cfg, charter)
        if base_pid != pid:
            clear_needs_file(cfg, base_pid)
    # 収束状態は milestone ファイルより先に決める（viewer と milestone GC はこの status を正とする）。
    # 早期 return するパス（no-acceptance / 合成不能）でも status を project.json に必ず残すことで、
    # viewer が「承認できるのか（converged）／完了条件を足すべきか（no-acceptance）」を判別でき、
    # reconcile_milestones が milestone ファイルを status に追従させられる。
    state = load_charter_state(cfg, charter_name if multi else None)
    if state.get("id") != pid:
        state = {"id": pid, "name": charter.name, "history": [], "best": 0, "stall": 0}
    if not charter.acceptance:
        # acceptance（受入 verify）が無いと done を判定できない＝必ず人へ（鉄則の保全）。
        # status=no-acceptance を保存してから milestone を出す（承認ではなく「完了条件を追加」を促す）。
        state.update({"id": pid, "name": charter.name, "status": REASON_PROJECT_NO_ACCEPTANCE})
        save_charter_state(cfg, state, charter_name if multi else None)
        write_milestone(cfg, charter, REASON_PROJECT_NO_ACCEPTANCE,
                        "acceptance 未定義のため done 判定不能", pid=pid,
                        version=charter_name if multi else "")
        print(f"[project] {charter.name}: acceptance 未定義 → 人へ（needs/{pid}.md）")
        return project_exit_code(REASON_PROJECT_NO_ACCEPTANCE)

    # executor: stub はローカル完結（エージェント不使用）が既定の意味＝charter 駆動の
    # 分解・レビューもここで揃える（さもないと --planner none / --executor stub を設定しても
    # plan_via_agent/review_via_agent が黙ってエージェントを呼んでしまう）。
    stub_mode = cfg.executor == "stub"
    plan_fn = planner or ((lambda ch: plan_via_stub(cfg, ch)) if stub_mode
                          else (lambda ch: plan_via_agent(cfg, ch)))
    review_fn = reviewer or ((lambda ch: review_via_stub(cfg, ch)) if stub_mode
                             else (lambda ch: review_via_agent(cfg, ch)))
    # このパス開始時点で「人が承認済み・charter も承認時から無変更」だったか。
    # 下のガードの早期 return に加え、replan（差分ゼロ）等でガードを抜けて再評価した場合にも、
    # 新しい仕事が何も無ければ末尾で accepted を維持する（converged へ降格して承認済み
    # マイルストーンを復活させない）ための基準値。
    was_accepted = (state.get("status") == REASON_PROJECT_ACCEPTED
                    and state.get("accepted_charter_sig") == _charter_full_signature(charter))
    # 承認済み（accepted）かつ charter.md が承認時から無変更なら何もしない（毎 run 再収束して
    # 承認済みプロジェクトの milestone が復活する不具合の防止）。charter.md を編集すると
    # 署名が変わり、ここを抜けて通常どおり再評価される（「続行: charter.md を更新して再実行」の
    # 案内どおりの挙動になる）。replan_req（人が明示的に要求したエラー回復の再分解）がある場合は
    # 素通りしない＝ここで早期 return すると直前で consume 済みの要求が握り潰され、
    # 「再分解を押しても何も起きない」になるため、accepted でも明示要求は必ず一度は処理する。
    if replan_req is None and was_accepted:
        print(f"[project] {charter.name}: 承認済み（charter.md に変更なし）→ 何もしません。"
              f"続けるなら charter.md を編集してください。")
        return project_exit_code(REASON_PROJECT_ACCEPTED)
    # acceptance を実行可能なコマンドへ解決（自然言語は決定的 verify へ合成し、結果を state にキャッシュ）。
    # 合成できない自然言語が残れば done 判定不能＝人へ（acceptance を書けないプロジェクトは人へ回す鉄則）。
    resolved, unresolved = resolve_charter_acceptance(cfg, charter, state, kiro_run)
    if unresolved:
        state["status"] = REASON_PROJECT_NO_ACCEPTANCE          # viewer/GC が status を正に読める
        save_charter_state(cfg, state, charter_name if multi else None)   # 合成済みキャッシュも残す
        summary = ("自然言語の acceptance を決定的 verify に合成できません（done 判定不能）: "
                   + " / ".join(unresolved))
        write_milestone(cfg, charter, REASON_PROJECT_NO_ACCEPTANCE, summary, pid=pid,
                        version=charter_name if multi else "")
        print(f"[project] {charter.name}: acceptance を合成できず → 人へ（needs/{pid}.md）")
        for u in unresolved:
            print(f"  - 未合成: {u}", file=sys.stderr)
        return project_exit_code(REASON_PROJECT_NO_ACCEPTANCE)
    charter.acceptance = resolved             # 以降の評価は合成済みの決定的コマンドで行う
    # charter 変更の検知（内容署名）: backlog 分解に効く内容が前回計画時と変わっていれば、消化可能
    # タスクが残っていても再計画して差分を投入する（viewer 等で charter を編集しても backlog が
    # 変わらない問題への対処）。署名が未記録（初回/既存プロジェクト）はベースラインを張るだけで
    # 再計画は誘発しない（次回以降の編集から検知できる）。
    plan_sig = _charter_plan_signature(charter)
    charter_changed = bool(state.get("planned_charter_sig")) and state["planned_charter_sig"] != plan_sig
    state["planned_charter_sig"] = plan_sig
    # "status" はここでは触らない（旧実装は "running" に即上書きしていた）。② execute
    # （runner=run_loop）が内部で ingest_commands を呼び、その場で approve/hold 等の人の指示を
    # 処理する。ここで status を "running" にしてしまうと、直前サイクルの "converged" を
    # ingest_commands が読めなくなり、watch 中は「承認してもプロジェクト milestone の approve が
    # 常に exit 2 で失敗し、次サイクルでまた収束候補として復活し続ける」実害があった
    # （cmd_approve は status == converged の milestone しか受け付けないため）。
    # 最終的な状態は下の evaluate ループの結果で必ず上書きされるので、ここで省いても状態は失われない。
    state.update({"id": pid, "name": charter.name, "acceptance_total": len(charter.acceptance)})
    save_charter_state(cfg, state, charter_name if multi else None)

    # 前パスが残した milestone（needs/<pid>.md）はこのパスで再評価するため先に掃除する。
    # 残したままだと execute（run 実行）中も「要対応: マイルストーン」カードが出続け、
    # 収束前の承認（cmd_approve は converged しか受けない＝exit 2）を人に押させてしまう。
    # まだ必要なら末尾の write_milestone が最新内容で書き直す。
    clear_needs_file(cfg, pid)

    append_journal(cfg.journal, f"=== project 開始 {charter.name} "
                                f"acceptance={len(charter.acceptance)} ===")
    cost_used = float(state.get("cost", 0.0))
    cycle = 0
    reason = REASON_PROJECT_CONVERGED
    last_summary = ""
    did_work = False              # このパスで新規タスク投入 or 消化があったか（accepted 維持の判定）

    while True:
        cycle += 1
        if heartbeat:
            heartbeat()                  # 長い改善ループ中も生存信号を更新（リモート発見の鮮度）
        if cycle > cfg.max_project_cycles:
            reason = REASON_PROJECT_BUDGET
            break

        # ① plan — 消化可能タスクが無いとき、または charter が前回計画時から変わったときに目標から
        #   backlog を起こす（変更が無ければ毎サイクルの再分解は避ける）。再計画は既存/archive タイトルで
        #   冪等に重複排除されるため、既存タスクを二重投入せず「charter の差分が生む新規タスク」だけ入る。
        #   再分解要求（replan＝エラー回復のやり直し）だけは照合を「done 以外」に絞る:
        #   done と類似でも再作成を許可する（過去に完了した同種タスクが再分解を丸ごと弾き、
        #   「再分解を押しても何も起きない」になるのを防ぐ）。処理中タスクとの二重投入と
        #   却下済み（人の明示判断）の復活はさせない。
        replan_retry = replan_req is not None
        existing = _existing_titles(cfg, charter_name if multi else None,
                                    active_only=replan_retry)
        has_consumable = any(
            t.consumable() and (not multi or task_charter_name(t) == charter_name)
            for t in load_tasks(cfg.backlog))
        if not has_consumable or charter_changed or replan_retry:
            ensure_repo_maps(cfg, charter)   # リポジトリ理解の成果物化（opt-in・sha キャッシュ）
            specs = plan_fn(charter)
            if multi:
                for sp in specs:                 # この charter のタスクとしてタグ付け（スコープの正）
                    if isinstance(sp, dict):
                        sp.setdefault("charter", charter_name)
            planned = _enqueue_specs(cfg, specs, existing, cfg.learn_threshold,
                                     charter=charter_name if multi else None,
                                     active_only=replan_retry)
            trig = ("再分解要求（エラー回復）" if replan_retry
                    else "charter 変更検知" if charter_changed else f"plan cycle {cycle}")
            if planned:
                did_work = True
                append_journal(cfg.journal,
                               f"project cycle {cycle}: {trig} で {len(planned)} 件投入 "
                               f"{[t.id for t in planned]}")
            elif replan_retry:
                # 再分解しても差分ゼロ（すべて現行処理中の backlog と重複）＝やり直し対象なし。要求は消化済み。
                append_journal(cfg.journal,
                               f"project cycle {cycle}: {trig} → 新規なし（現行バックログと重複）")
            charter_changed = False   # 変更由来の再計画は 1 回だけ（以降のサイクルで再分解しない）
            replan_req = None         # 再分解要求も 1 回だけ消化する（one-shot）

        # ② execute — 既存の正準ループを無改造で回す（drained まで）
        result = runner(cfg)
        cost_used += float(result.get("cost", 0.0))
        counts = result["counts"]
        if counts.get("done", 0) > 0:
            did_work = True
        if result["reason"] in (REASON_BUDGET, REASON_COST, REASON_THROTTLE):
            reason = REASON_PROJECT_BUDGET if result["reason"] != REASON_COST else REASON_PROJECT_COST
            break
        if counts.get("blocked", 0) > 0 or counts.get("review", 0) > 0 \
                or counts.get("proposed", 0) > 0:
            reason = REASON_PROJECT_BLOCKED      # 内側が人へ → プロジェクトも人待ちで止める
            break
        # execute 中（runner=run_loop 内の ingest_commands）に人がこの milestone を承認して
        # いるかもしれない。承認済みなら evaluate で acceptance を再収束させて上書きしない
        # （accepted を尊重する。さもないと承認直後の同一サイクルで milestone が復活する）。
        # accepted_charter_sig を「今評価している charter」と突き合わせるのは、charter.md が
        # 変わった直後の run（冒頭ガードは通過済み）で、まだ古い accepted が残っているだけの
        # ケースと区別するため（そのケースは新規承認ではないので短絡しない＝通常どおり再評価する）。
        mid_state = load_charter_state(cfg, charter_name if multi else None)
        if (mid_state.get("status") == REASON_PROJECT_ACCEPTED
                and mid_state.get("accepted_charter_sig") == _charter_full_signature(charter)):
            state.update(mid_state)
            reason = REASON_PROJECT_ACCEPTED
            break

        # ③ evaluate — acceptance 評価・改善起票・収束/コスト/停滞判定（停止理由 or None）
        stop_reason, last_summary = _project_evaluate(cfg, charter, pid, state, cycle,
                                                      cost_used, review_fn,
                                                      charter_tag=charter_name if multi else "")
        if stop_reason:
            reason = stop_reason
            break

    if reason == REASON_PROJECT_CONVERGED and was_accepted and not did_work:
        # 承認済み（accepted・charter 無変更）のプロジェクトを再評価しただけ（新規タスクなし・
        # 消化なし）で再収束した場合は accepted を維持する。降格を許すと、差分ゼロの再分解や
        # 再評価のたびに accepted → converged へ戻り、承認済みのマイルストーンが復活して
        # 人に同じ承認を何度も求めてしまう。新しい仕事が実際にあった場合（did_work）は
        # 新規成果の検収ゲートとして通常どおり converged（milestone）へ進む。
        reason = REASON_PROJECT_ACCEPTED
        append_journal(cfg.journal, "project: 承認済み（差分ゼロの再評価）→ accepted を維持")
    state["cost"] = round(cost_used, 4)
    state["cycles"] = int(state.get("cycles", 0)) + cycle
    state["status"] = reason
    save_charter_state(cfg, state, charter_name if multi else None)

    if reason in (REASON_PROJECT_CONVERGED, REASON_PROJECT_STALL,
                  REASON_PROJECT_BUDGET, REASON_PROJECT_COST, REASON_PROJECT_BLOCKED):
        write_milestone(cfg, charter, reason, last_summary or "（評価前に停止）", pid=pid,
                        version=charter_name if multi else "")
    append_journal(cfg.journal, f"=== project 停止 reason={reason} cycles={cycle} "
                                f"cost={cost_used:.4f} ===")
    print(f"\n=== kiro-project run（charter 駆動: {charter.name}）===")
    print(f"停止理由 : {reason}")
    print(f"概況     : {last_summary or '（評価前に停止）'}")
    if reason == REASON_PROJECT_CONVERGED:
        print(f"→ 収束候補。受領: kiro-project approve {pid} --reason ...  "
              f"／ 続行: charter.md を更新して run を再実行")
    elif reason != REASON_PROJECT_ACCEPTED:
        print(f"→ 人の対応待ち: needs/{pid}.md を確認")
    return project_exit_code(reason)


def _charter_mtimes(cfg: "Config") -> "dict[str, float]":
    """charter ファイル群（charters/*.md と charter.md）の mtime（更新検知用）。"""
    out: "dict[str, float]" = {}
    d = charters_dir(cfg)
    if d.is_dir():
        for f in d.glob("*.md"):
            try:
                out[f.name] = f.stat().st_mtime
            except OSError:
                pass
    if cfg.charter and cfg.charter.exists():
        try:
            out["charter.md"] = cfg.charter.stat().st_mtime
        except OSError:
            pass
    return out


def project_watch(cfg: "Config", planner=None, reviewer=None, runner=run_loop,
                  sleeper=time.sleep, max_passes=None, heartbeat=None) -> int:
    """`run --watch`（charter あり）: 1 パスごとに plan→execute→evaluate を回し、人待ちで止まったら
    charter 更新/フィードバックを poll で拾って再開する（idle 中はエージェント非起動）。"""
    passes = 0
    code = 0
    # （再）起動直後は plan より先にリモート状態を取り込む。自己更新の graceful 再起動を挟むと、
    # 停止中に viewer が push した charter 更新/フィードバックが未取り込みのまま cmd_project の
    # 初回 plan が走り、古い charter で計画してしまう（cmd_project→run_loop の入口同期は plan の後）。
    state_sync(cfg)
    while True:
        if is_paused(cfg):           # pause 中は plan/execute/evaluate を起こさない
            append_journal(cfg.journal, "=== project watch: 一時停止中（resume/stop 待ち）===")
            write_status(cfg)
            code = 0
        else:
            names = charter_names(cfg)
            if not names:
                if not _has_master_charter(cfg):
                    return code
                # マスター憲章のみ（バージョン未作成）: 分解はしない。実 backlog タスクや人の指示が
                # あるときだけ消化し、無ければ何もしない＝アイドルのまま（リセット直後など、やる
                # ことが無いのに run_loop が回って run-log/journal を無駄に増やさない）。バージョン
                # （charters/<名前>.md）が置かれれば次パスで charter 駆動へ入る。
                if has_work(cfg):
                    runner(cfg)
                passes += 1
                if heartbeat:
                    heartbeat()
                if max_passes is not None and passes >= max_passes:
                    return code
            for name in names:       # 全 charter（バージョン）をラウンドロビンで 1 パスずつ
                code = cmd_project(cfg, planner, reviewer, runner, heartbeat=heartbeat,
                                   charter_name=name)
                passes += 1
                if heartbeat:
                    heartbeat()
                if max_passes is not None and passes >= max_passes:
                    return code
        names = charter_names(cfg)
        if not names and not _has_master_charter(cfg):
            return code
        # このパスの版処理を終えたら milestone を status へ整合する（唯一の調整点）。
        # accepted・削除済みバージョン・旧トップレベルの milestone を掃除し、承認できない
        # no-acceptance 等はそのまま残す＝「要対応マイルストーンが何度も復活」を止める根本対策。
        reconcile_milestones(cfg)
        pids = {}
        for name in names:           # charter 別の milestone id（フィードバック検知に使う）
            ch = _load_named_charter(cfg, name)
            if ch is not None:
                pids[name] = _project_id(cfg, ch) + (
                    f"-{name}" if _is_multi_charter(cfg, name) else "")
        mtimes0 = _charter_mtimes(cfg)
        commit_state(cfg)   # パスの区切りで状態をコミット（人の判断が動いたら即・副産物はまとめて）
        append_journal(cfg.journal, "=== project watch: 監視中（charter 更新/フィードバック待ち）===")
        while True:                  # idle: charter が変わるか、人のフィードバックが来たら再開
            sleeper(cfg.poll)
            if heartbeat:
                heartbeat()
            state_sync(cfg)          # 状態 git: リモートの charter 更新/フィードバックを取り込む（間隔律速）
            if is_paused(cfg):
                ingest_commands(cfg)  # pause 中も resume/stop（と他の指示）は受け付ける
                if maybe_self_update(cfg):
                    raise _RestartRequested()
                continue             # pause 中は再開条件を評価しない
            resumed = False
            for pid in pids.values():
                # milestone のフィードバックは [x] を待たず本文だけで再開する（方針を書けば動く）。
                # ただし書きかけを消さないよう静穏化は待つ: settled を挟まないと、人が needs を
                # 編集して保存した瞬間にファイルごと消えてしまう。
                nf = needs_path(cfg, pid)
                if nf.exists() and settled(cfg, nf) and read_feedback(nf):
                    clear_needs_file(cfg, pid)
                    resumed = True
            if resumed or _charter_mtimes(cfg) != mtimes0 or has_work(cfg):
                break
            if maybe_self_update(cfg):   # アイドル時のみ自己更新（取り込めたら再起動）
                raise _RestartRequested()


# ---------------------------------------------------------------------------
