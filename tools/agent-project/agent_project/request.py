from __future__ import annotations
# request.py — 元 agent-project.py の 3621-4099 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
def resolve_agent_flow(explicit: "str | None") -> "list[str]":
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = shutil.which("agent-flow")
    if found:
        return [found]
    # パッケージ断片: .../tools/agent-project/agent_project/request.py
    # → 隣接の .../tools/agent-flow/agent-flow.py。
    # parent×2（agent-project/agent-flow）は存在せず、act が常に起動失敗する。
    # 以前は act 失敗を捨てて verify で偽 done になっていた。
    here = Path(__file__).resolve()
    tools_sibling = here.parents[2] / "agent-flow" / "agent-flow.py"  # tools/
    if tools_sibling.is_file():
        return [sys.executable, str(tools_sibling)]
    legacy = here.parents[1] / "agent-flow" / "agent-flow.py"  # agent-project/ 直下の旧配置
    return [sys.executable, str(legacy)]


def _charter_definition(ch: "Charter") -> str:
    parts = []
    if ch.goal:
        parts.append(f"目標: {ch.goal}")
    # 対象リポジトリ・リンクは「どこで・何に対して作業するか」の重要情報。goal 直後に置き、
    # ワーカー（gitlab イシュー等）へ確実に伝わるよう truncation で落ちにくい位置にする。
    # タスクは説明（desc）を見て関係する repo を選び、その base/target ブランチを踏まえて作業する。
    if ch.repo_specs:
        lines = ["対象リポジトリ（タスクは説明を見て関係するものを選び、その base/target ブランチで作業。"
                 "path 指定があればそのフォルダ配下のみ変更）:"]
        for r in ch.repo_specs:
            head = f"- {r['name']} = {r['url']}" if r["name"] else f"- {r['url']}"
            br = []
            if r.get("path"):
                br.append(f"path={r['path']}")
            if r["base"]:
                br.append(f"base={r['base']}")
            if r["target"]:
                br.append(f"target={r['target']}")
            if r.get("readonly"):
                br.append("参照のみ・push しない")
            elif r.get("owns"):
                br.append("書込先候補（owns: " + ", ".join(r["owns"][:3]) + "）")
            if br:
                head += "（" + ", ".join(br) + "）"
            lines.append(head)
            if r["desc"]:
                lines.append(f"    説明: {r['desc']}")
        parts.append("\n".join(lines))
    if ch.link_specs:
        lines = ["関連リンク（wiki/ドキュメント/横展開先など。踏まえること）:"]
        for l in ch.link_specs:
            lines.append(f"- {l['text']}" + (f" — {l['desc']}" if l["desc"] else ""))
        parts.append("\n".join(lines))
    if ch.constraints:
        parts.append("制約:\n" + "\n".join(f"- {c}" for c in ch.constraints))
    if ch.assumptions:
        parts.append("前提:\n" + "\n".join(f"- {a}" for a in ch.assumptions))
    if ch.deliverables:
        parts.append("成果物:\n" + "\n".join(f"- {d}" for d in ch.deliverables))
    return "\n".join(parts).strip()


def _charter_plan_signature(ch: "Charter") -> str:
    """charter の「backlog 分解に効く内容」の安定ハッシュ（目標/repos/リンク/制約/前提/成果物）。
    mtime ではなく *内容* ベースなので、state_git 同期やファイルコピーで mtime だけ変わっても
    誤検知せず、内容が実際に変わったときだけ変化する。これを project state に記録し、次回 run で
    charter が変わっていれば（＝署名が違えば）消化可能タスクがあっても backlog を再計画する。
    acceptance は done 判定に効くが分解入力ではないため署名には含めない（評価側で反映される）。"""
    return hashlib.sha256(_charter_definition(ch).encode("utf-8")).hexdigest()


def _charter_full_signature(ch: "Charter") -> str:
    """charter.md 全文（raw）の安定ハッシュ。acceptance も含めて「何か変わったか」を見る。
    承認済み（accepted）プロジェクトを再開すべきか（人が charter.md を更新したか）の判定専用。
    _charter_plan_signature は分解入力だけで acceptance を除くため、acceptance だけの更新では
    変わらず、この判定には使えない（accepted のまま何度も再収束してしまう）。"""
    return hashlib.sha256(ch.raw.encode("utf-8")).hexdigest()


def charter_context(cfg: "Config", max_chars: int = 1400, task: "Task | None" = None) -> str:
    """charter.md（プロジェクト定義＝目標/制約/前提/成果物）を act ワーカーへ渡す文脈に要約する。
    **`project` でも通常 `run` でも、charter.md が存在すれば全 act に注入**＝agent-flow のワーカーが
    プロジェクトの北極星（目標・制約）を踏まえて働く。`## links` があればリンク先プロジェクトの定義も
    続けて取り込む（横展開）。charter 無し（通常運用）では空＝従来どおり。
    task を渡すと `charter:` タグの charter（複数 charter 運用）を選ぶ。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        return ""
    if ch is None:
        return ""
    block = _charter_definition(ch)
    if len(block) > max_chars:                  # 有界化（先頭＝目標/制約を優先して残す）
        block = block[:max_chars].rstrip() + " …"
    # 横展開: リンク先プロジェクトの定義を要約付与（有界・1 階層）
    for name, root in resolve_linked_projects(cfg, ch):
        lp = root / "charter.md"
        if not lp.exists():
            continue
        try:
            linked = parse_charter(lp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = _charter_definition(linked)
        if d:
            block += f"\n\n[リンク: {name}] の定義（横展開・踏まえること）:\n" + d[:600]
    return block


def decision_context(cfg: "Config", task: Task, max_chars: int = 1000) -> str:
    """このタスクの過去の判断記録（needs の判断結果・人の承認/差し戻し/learn）を act ワーカーへ渡す。
    **project/backlog を問わず**、`decisions/<id>.md` があれば注入する（末尾＝直近を優先して有界化）。"""
    dp = decision_path(cfg, task.id)
    if not dp.exists():
        return ""
    try:
        txt = dp.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not txt:
        return ""
    if len(txt) > max_chars:
        txt = "…\n" + txt[-max_chars:]
    return txt


def _scan_learn_lines(decisions_dir: Path, limit: int = 12) -> "list[str]":
    """decisions/ 配下の `- learn: <title> :: <guide>` 行を集める（再利用可能な人の判断）。"""
    out: list[str] = []
    if not decisions_dir.exists():
        return out
    for f in sorted(decisions_dir.glob("*.md")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                m = LEARN_RE.match(line.strip())
                if m:
                    out.append(f"{m.group('title').strip()} :: {m.group('guide').strip()}")
        except OSError:
            continue
    return out[-limit:]


def linked_learnings_context(cfg: "Config", max_chars: int = 800,
                             task: "Task | None" = None) -> str:
    """charter `## links` 先プロジェクトの判断（decisions の learn）を act ワーカーへ取り込む（横展開）。
    リンク先で人が下した再利用可能な判断を、別プロジェクトの作業にも効かせる（明示 opt-in・有界）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        return ""
    if ch is None or not ch.links:
        return ""
    lines: list[str] = []
    for name, root in resolve_linked_projects(cfg, ch):
        for ln in _scan_learn_lines(root / "decisions"):
            lines.append(f"[{name}] {ln}")
    if not lines:
        return ""
    block = "\n".join(f"- {x}" for x in lines)
    return block[:max_chars]


def build_request(task: Task, cfg: "Config | None" = None) -> str:
    base = (f"{task.title}\n\n"
            f"このタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n"
            f"完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
            f"  {task.verify or '（verify 未定義）'}\n\nタスクID: {task.id}")
    fb = task.feedback()
    if fb:
        base += f"\n\n人からのフィードバック（必ず反映すること）:\n{fb}"
    if cfg is not None and task.get("spec_for"):
        # spec 作成タスク: 3 ファイルの作成指示（実装はさせない）を要求文に載せる
        base += "\n\n" + _spec_instructions(cfg, task)
    if cfg is not None:
        # spec 展開で生まれた実装タスク・総合検証タスクには spec/design を文脈注入（有界）
        sc = spec_context(cfg, task)
        if sc:
            base += "\n\n仕様（spec 前段の成果・必ず従うこと）:\n" + sc
        # 参照リポジトリは要求本文に畳まず、agent-flow へ `--reference` で構造化伝搬する
        # （分解後の各ノード／gitlab イシューにも確実に届くように）。
        # 定義（charter）と判断結果（decisions）を、project でも通常 run でもワーカーへ渡す。
        cc = charter_context(cfg, task=task)
        if cc:
            base += ("\n\nプロジェクト定義（charter・常に踏まえること。成果物が目標/制約に反しないこと）:\n"
                     + cc)
        # プロジェクトルール（rules.md・人が書く＋効いた learn の自動昇格）。learn の recall が
        # 類似タスク限定なのに対し、これは全タスクへ常時注入される恒常ルール層。
        pr = project_rules_context(cfg)
        if pr:
            base += "\n\nプロジェクトルール（rules.md・全タスク共通。必ず従うこと）:\n" + pr
        # リポジトリ理解（context/*.md・生成は opt-in repo_map / 人の手書きも可）。workspace 指定
        # タスクはその repo 分だけ、無指定はプロジェクトの全ファイル（有界）を注入する。
        rm = repo_map_context(cfg, [task.get("workspace")] if task.get("workspace") else None)
        if rm:
            base += "\n\nリポジトリ理解（構造・規約・ビルド/テストコマンド）:\n" + rm
        dc = decision_context(cfg, task)
        if dc:
            base += ("\n\nこのタスクに関する過去の判断記録（needs の判断結果・必ず踏まえること）:\n" + dc)
        lc = linked_learnings_context(cfg, task=task)
        if lc:
            base += ("\n\nリンク先プロジェクトの判断（横展開・参考にすること）:\n" + lc)
        # 似た過去タスクの学び（gitlab 却下/承認・needs の learn）を **分解と実装の両方**に効かせる。
        # 要求本文に載るため flow-planner が分解時に、ワーカーが実装時に踏まえる（＝分解の再考にも届く）。
        if cfg.learn:
            matched = find_learned_resolution(cfg, task)
            if matched:
                base += ("\n\n類似タスクでの学び（分解・verify・実装で踏まえ、同種の手戻りを繰り返さないこと）:\n"
                         f"- {matched[1]}")
    return base


def decide_pace(cfg: "Config", cycle_elapsed: float) -> float:
    pace = cfg.pace
    if cfg.max_seconds and cfg.max_cycles:
        pace = max(pace, cfg.max_seconds / cfg.max_cycles)
    return max(0.0, pace - cycle_elapsed)


def decide_location(task: Task, policy: Policy, cfg: "Config") -> str:
    """act の実行モードを local / daemon / remote に決める（agent-flow の起動方法を統合）。

      local  : agent-flow run（単発・自己完結・daemon 不要）
      daemon : ローカルバスの daemon に submit して結果を待つ（warm worker 再利用）
      remote : 共有 git バス（別マシンの daemon）へ submit＝真のオフロード
    `--location auto`（既定）: offload 一致かつ git-bus → remote / ローカル daemon 稼働 → daemon / それ以外 local。
    明示指定（local/daemon/remote）はそれを優先（remote は git-bus 必須、無ければ local）。"""
    if task.get("spec_for"):
        # spec 作成タスク（§5.10）: 成果物 specs/<id>/ はプロジェクトの workdir に要る。
        # daemon/remote だと別プロセス・別マシンに生成されローカルの verify が通らないため、
        # location 設定に依らず常にローカル単発 run で実行する（executor の差し替えは
        # build_agent_flow_cmd が行う。local 固定はその前提でもある）。
        return "local"
    loc = cfg.location
    if loc == "auto":
        if cfg.git_bus and any(task.matches(p) for p in policy.offload):
            return "remote"
        if daemon_running(cfg, use_git=False):
            return "daemon"
        return "local"
    if loc == "remote" and not cfg.git_bus:
        return "local"
    return loc


def _kf_base(cfg: "Config", use_git: bool) -> "list[str]":
    """agent-flow 共通 argv（bus / git / --config）。

    flow_config は daemon 起動（flow_daemon_cmd）だけでなく sync run / submit / result /
    doctor にも渡す。付け忘れると manage_flow_daemon=false の主経路だけ executor・gitlab・
    agent_cli 等の yaml 設定が消える。"""
    base = resolve_agent_flow(cfg.agent_flow) + ["--bus", str(cfg.bus)]
    if use_git and cfg.git_bus:
        base += ["--git", cfg.git_bus, "--git-branch", cfg.git_branch]
        if cfg.git_subdir:
            base += ["--git-subdir", cfg.git_subdir]
    fc = getattr(cfg, "flow_config", None)
    if fc:
        base += ["--config", os.path.abspath(os.path.expanduser(str(fc)))]
    return base


def _is_reference_repo(spec: dict) -> bool:
    """owns: が無い repo は参照リポジトリ（read-only）。書込先ワークスペースの候補にしない。"""
    return not spec.get("owns")


def _split_tokens(raw) -> "list[str]":
    return [_strip_code(t) for t in re.split(r"[,\s]+", str(raw or "").strip()) if _strip_code(t)]


def _raw_url_spec(tok: str) -> "dict | None":
    """charter に無い素の URL トークンを最小の workspace spec にする（owns 無し＝参照ではなく明示指定用）。"""
    if "://" in tok or "@" in tok or tok.endswith(".git"):
        return {"name": "", "url": tok, "desc": "", "base": "", "target": "", "path": "",
                "readonly": False, "owns": []}
    return None


def route_target(task: Task, policy: "Policy") -> str:
    """policy の `route: <パターン> -> <repo名>` を順に評価し、最初に一致した repo 名を返す（無ければ ""）。"""
    for rule in policy.route:
        for sep in ("->", "=>", "→"):
            if sep in rule:
                pattern, name = rule.split(sep, 1)
                if task.matches(pattern.strip()):
                    return _strip_code(name.strip())
                break
    return ""


def _glob_prefix(g: str) -> str:
    """グロブの先頭の非ワイルドカード部分（`apps/api/**` → `apps/api/`）。"""
    m = re.search(r"[*?\[]", g)
    return g[:m.start()] if m else g


def _owns_matches(owns: "list[str]", tok: str) -> bool:
    """パストークン tok が owns グロブのどれかに該当するか（fnmatch ＋ 先頭プレフィックス一致）。"""
    for g in owns:
        if fnmatch.fnmatch(tok, g) or fnmatch.fnmatch(tok, g.rstrip("/") + "/*"):
            return True
        pre = _glob_prefix(g).rstrip("/")
        if pre and (tok == pre or tok.startswith(pre + "/")):
            return True
    return False


def _verify_paths(verify: str) -> "list[str]":
    """verify シェルコマンドから「操作するパス」らしきトークンを抽出する（owns 突き合わせ用）。
    シェル区切りで割り、引用符/先頭 ./ を外し、`/` か拡張子を含むパスらしいものだけ残す。"""
    out: "list[str]" = []
    for t in re.split(r"[\s;|&()<>=]+", verify or ""):
        t = t.strip().strip("'\"`").lstrip("./")
        if t and ("/" in t or re.search(r"\.\w+$", t)):
            out.append(t)
    return out


def _infer_workspace_from_paths(workspaces: "list[dict]", paths: "list[str]") -> "dict | None":
    """パス群を各ワークスペースの owns: と突き合わせ、所有する1つを返す。曖昧（複数一致）/不一致なら
    候補が1つだけのときはそれ、そうでなければ None。"""
    if not paths:
        return workspaces[0] if len(workspaces) == 1 else None
    hits = [s for s in workspaces if any(_owns_matches(s.get("owns", []), p) for p in paths)]
    if len(hits) == 1:
        return hits[0]
    return workspaces[0] if (not hits and len(workspaces) == 1) else None


def _owns_infer(task: Task, workspaces: "list[dict]") -> "dict | None":
    """タスクが触る予定パス（`- paths:` ヒント。無ければ verify コマンドから抽出）を charter の owns:
    グロブと突き合わせ、所有するワークスペースを推定する。曖昧（複数一致）なら推定しない。"""
    paths = _split_tokens(task.get("paths")) or _verify_paths(task.verify)
    if not paths:
        return None
    hits = [s for s in workspaces if any(_owns_matches(s.get("owns", []), p) for p in paths)]
    return hits[0] if len(hits) == 1 else None


def _route_agent_prompt(task: Task, workspaces: "list[dict]") -> str:
    lines = ["次のタスクをコミットすべき書込先リポジトリ（ワークスペース）を1つだけ選んでください。",
             f"タスク: {task.title}", f"verify: {task.verify or '（未定義）'}", "", "候補リポジトリ:"]
    for s in workspaces:
        owns = "・".join(s.get("owns", []))
        lines.append(f"- {s.get('name') or s['url']}"
                     + (f"（担当: {owns}）" if owns else "")
                     + (f": {s['desc']}" if s.get("desc") else ""))
    lines.append('\n出力は JSON のみ: {"workspace": "<repo名>"}（判断できなければ {"workspace": ""}）')
    return "\n".join(lines)


def route_agent(cfg: "Config", task: Task, workspaces: "list[dict]",
                kiro_run=None) -> str:
    """曖昧なタスクの書込先を LLM に1つ選ばせる（決定論で決まらなかったときのみ）。失敗時は ""。"""
    kiro_run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="route"))
    try:
        out = kiro_run(_route_agent_prompt(task, workspaces), cfg.model)
        data = _extract_json_obj(out)
        return _strip_code(str((data or {}).get("workspace") or "").strip())
    except Exception:  # noqa: BLE001 — 推定失敗は「決まらない」に倒す
        return ""


def resolve_workspace(cfg: "Config", task: Task, policy: "Policy") -> "tuple[dict | None, str]":
    """タスク → ちょうど1つの書込先ワークスペース spec を決める。解決順（上が優先）:
      1. 明示 `- workspace:`  2. policy `route:`  3. charter owns: 推定
      4. auto-route エージェント（route_planner=kiro）  5. 既定（default_workspace / 候補が1つ）
    返り値 (spec or None, routed_by)。None は書込先なし＝読み取り専用 run（調査タスク等）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        ch = None
    specs = registry_specs(cfg, ch)               # repos ファイル単独（charter 無し）でも解決できる
    smap = repo_spec_map(specs)
    workspaces = [s for s in specs if not _is_reference_repo(s)]

    explicit = _strip_code(str(task.get("workspace") or "").strip())
    if explicit:                                  # 1. 人/過去ルーティングの明示指定（最優先）
        sp = smap.get(explicit) or _raw_url_spec(explicit)
        if sp:
            return sp, "explicit"
    name = route_target(task, policy)             # 2. route: パターンルール（決定論）
    if name and smap.get(name) and not _is_reference_repo(smap[name]):
        return smap[name], "rule"
    sp = _owns_infer(task, workspaces)            # 3. charter owns: パス推定（決定論）
    if sp:
        return sp, "owns"
    if cfg.route_planner == "agent" and workspaces:  # 4. auto-route エージェント（曖昧時のみ）
        nm = route_agent(cfg, task, workspaces)
        if nm and smap.get(nm) and not _is_reference_repo(smap[nm]):
            return smap[nm], "agent"
    if cfg.default_workspace and smap.get(cfg.default_workspace):  # 5a. 既定ワークスペース
        return smap[cfg.default_workspace], "default"
    if len(workspaces) == 1:                       # 5b. 書込先候補が1つだけ → それ
        return workspaces[0], "sole"
    return None, "none"


def resolve_and_persist_workspace(cfg: "Config", task: Task, policy: "Policy") -> "dict | None":
    """タスクを書込先ワークスペースへルーティングし、決定を md（`- workspace:`/`- routed_by:`）へ
    書き戻して安定・監査可能にする（毎サイクル LLM を呼ばない）。返り値は解決した spec か None。"""
    spec, routed_by = resolve_workspace(cfg, task, policy)
    if spec and routed_by != "explicit":          # 明示指定はそのまま（上書きしない）
        task.set("workspace", spec.get("name") or spec["url"])
        task.set("routed_by", routed_by)
        persist_task(cfg, task)
    return spec


def task_branch_name(cfg: "Config", task: "Task") -> str:
    """タスク単位ターゲットブランチ名（ap/<task-id>）。全試行（リトライ含む）の成果を集約する。"""
    return f"{getattr(cfg, 'task_branch_prefix', 'ap/') or 'ap/'}{task.id}"


def _workspace_token(spec: dict) -> str:
    """workspace spec を agent-flow の `--workspace` 値（JSON）にする。
    url/path/base/target/desc/branch/local を伝搬。worker（作業ツリーの用意・作業ブランチ）と
    gitlab の起票先解決の双方で使われる。

    local（手元にある同じリポジトリのクローン）を落とすと、worker は目の前に同じリポジトリが
    あってもネットワーク越しにミラーを取り直す。ここで伝搬させることで worker はローカルから
    worktree を切れる（速い・オフラインでも動く）。"""
    meta = {k: spec[k] for k in ("path", "base", "target", "desc", "branch", "local")
            if spec.get(k)}
    if meta.get("desc") and len(meta["desc"]) > 300:
        meta["desc"] = meta["desc"][:300]         # argv 肥大を防ぐ（説明は有界に）
    return json.dumps({"url": spec["url"], **meta}, ensure_ascii=False, separators=(",", ":"))


def _workspace_spec_for(cfg: "Config", task: Task) -> "dict | None":
    """既に解決・永続化済みの `- workspace:`（_act_batch で確定）を charter spec へ。
    未解決なら None（読み取り専用 run）。ルーティングはここでは行わない（決定は1度だけ）。"""
    name = _strip_code(str(task.get("workspace") or "").strip())
    if not name:
        return None
    try:
        smap = repo_spec_map(registry_specs(cfg, charter_for_task(cfg, task)))
    except (OSError, ValueError):
        smap = {}
    spec = smap.get(name) or _raw_url_spec(name)
    if spec and getattr(cfg, "task_branch", False):
        # タスク単位ターゲットブランチ: agent-flow は run 毎の af/<run-id> の代わりにこのブランチへ
        # push する（リトライも同一ブランチに積み増し、レビュー・MR の対象を 1 本に集約する）
        spec = {**spec, "branch": task_branch_name(cfg, task)}
    return spec


def _workspace_cmd_args(cfg: "Config", task: Task) -> "list[str]":
    """agent-flow へ渡す `--workspace`（唯一の書込先）。書込先が無ければ空＝読み取り専用 run。"""
    spec = _workspace_spec_for(cfg, task)
    return ["--workspace", _workspace_token(spec)] if spec else []


def _reference_token(spec: dict) -> str:
    """参照リポジトリ spec を agent-flow の `--reference` 値（JSON）にする。url/path/base/desc を伝搬。"""
    meta = {k: spec[k] for k in ("path", "base", "desc") if spec.get(k)}
    if meta.get("desc") and len(meta["desc"]) > 300:
        meta["desc"] = meta["desc"][:300]
    return json.dumps({"url": spec["url"], **meta}, ensure_ascii=False, separators=(",", ":"))


def _reference_cmd_args(cfg: "Config", task: Task) -> "list[str]":
    """agent-flow へ渡す `--reference` 列（参照リポジトリ＝読むだけ。executor が描画する）。"""
    args: "list[str]" = []
    for spec in task_reference_specs(cfg, task):
        args += ["--reference", _reference_token(spec)]
    return args


def task_reference_specs(cfg: "Config", task: Task) -> "list[dict]":
    """このタスクが参照する（書き込まない）リポジトリの spec 列。charter の owns: 無しエントリ全部に、
    タスクの `- refs:`（および `- repos:` に挙げた参照先）で明示したものを足す。書込先 `- workspace:`
    に解決された url は除く（書込先は参照に含めない）。要求本文へ記述として埋め込む（clone はしない）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        ch = None
    specs = registry_specs(cfg, ch)
    smap = repo_spec_map(specs)
    ws = _workspace_spec_for(cfg, task)
    ws_url = ws["url"] if ws else None
    out: "list[dict]" = []
    seen: "set[str]" = set()
    refs = [s for s in specs if _is_reference_repo(s)]
    for tok in _split_tokens(task.get("refs")) + _split_tokens(task.get("repos")):
        sp = smap.get(tok) or _raw_url_spec(tok)
        if sp:
            refs.append(sp)
    for s in refs:
        url = s.get("url")
        if url and url not in seen and url != ws_url:   # 書込先は参照に含めない
            seen.add(url)
            out.append(s)
    return out


