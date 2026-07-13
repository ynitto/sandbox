from __future__ import annotations
# plan.py — 元 kiro-project.py の 9290-9601 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# リポジトリ理解の成果物化（repo-map・opt-in `repo_map`）
#   charter の書込先 repo ごとに context/<repo名>.md（構造・主要モジュール・ビルド/テスト
#   コマンド・規約）をエージェントに生成させ、HEAD sha を署名にキャッシュする。
#   生成だけが opt-in で、**読み出しは常時**（人が手書きした context/*.md も同じ口で
#   plan / act / verify 合成に注入される）。生成失敗は空のまま＝従来動作。
# ---------------------------------------------------------------------------
def context_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "context"


_REPO_MAP_HEAD_RE = re.compile(r"^<!--\s*head:\s*(\S+)\s*-->")


def _repo_head_sha(url: str, branch: str = "") -> "str | None":
    """repo の先頭コミット SHA（branch 指定はそのブランチ・無指定は HEAD）。取得不能は None。"""
    if branch:
        return remote_branch_sha(url, branch)
    try:
        r = subprocess.run(["git", "ls-remote", url, "HEAD"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").split()
    return out[0] if r.returncode == 0 and out else None


def _repo_map_generate(cfg: "Config", spec: dict) -> str:
    """repo を一時 worktree に用意してエージェントに理解を要約させる（有界・失敗は空）。"""
    tmp = tempfile.mkdtemp(prefix="kiro-repomap-")
    dest = str(Path(tmp) / "repo")
    try:
        _clone_repo_shallow(spec["url"], spec.get("base") or "", dest)
        prompt = (
            f"ローカルのリポジトリ {dest} を調査し、次を Markdown で 2000 字以内に要約してください。\n"
            "- 構造（主要ディレクトリと役割）\n- 主要モジュールと責務\n"
            "- ビルド・テスト・リンタの実行コマンド\n- 命名・実装の規約（読み取れる範囲で）\n"
            "出力は要約本文のみ（前置き・後書きなし）。")
        return _run_kiro_cli(prompt, cfg.model, purpose="repo_map").strip()[:4000]
    except Exception:  # noqa: BLE001  clone 失敗・エージェント不在・タイムアウトは生成なし
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_repo_maps(cfg: "Config", charter: "Charter | None") -> None:
    """charter の書込先 repo ごとに context/<repo名>.md を用意する（plan の直前に呼ぶ）。
    HEAD sha が前回生成時と同じなら再生成しない（sha 不明でファイルが既にあれば温存＝
    無限再生成を避ける）。stub executor では生成しない（plan_via_stub と同じ扱い）。"""
    if not (cfg.repo_map and charter) or cfg.executor == "stub":
        return
    for spec in charter.repo_specs:
        if not spec.get("url") or spec.get("readonly"):
            continue
        name = _slug_id(spec.get("name") or spec["url"]) or "repo"
        path = context_dir(cfg) / f"{name}.md"
        sha = _repo_head_sha(spec["url"], spec.get("base") or spec.get("target") or "")
        if path.exists():
            try:
                m = _REPO_MAP_HEAD_RE.match(path.read_text(encoding="utf-8"))
            except OSError:
                m = None
            recorded = m.group(1) if m else ""
            if not sha or sha == recorded:
                continue                            # 変化なし（or 判定不能）は再生成しない
        body = _repo_map_generate(cfg, spec)
        if not body:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<!-- head: {sha or 'unknown'} -->\n"
                        f"# リポジトリ理解: {spec.get('name') or spec['url']}\n\n{body}\n",
                        encoding="utf-8")
        append_journal(cfg.journal, f"repo-map 生成: context/{name}.md（{spec['url']}）")


def repo_map_context(cfg: "Config", names: "list[str] | None" = None,
                     limit: int = 1500, max_files: int = 3) -> str:
    """context/*.md（リポジトリ理解・人の手書きも可）を有界に読み出す。names 指定はその repo
    のみ、None は全ファイル（先頭 max_files 件）。repo_map off でも既存ファイルは読む。"""
    cdir = context_dir(cfg)
    if not cdir.exists():
        return ""
    files = sorted(cdir.glob("*.md"))
    if names:
        wanted = {_slug_id(n) for n in names if n}
        files = [f for f in files if f.stem in wanted]
    parts: "list[str]" = []
    for f in files[:max_files]:
        try:
            txt = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        txt = _REPO_MAP_HEAD_RE.sub("", txt).strip()
        if txt:
            parts.append(txt[:limit])
    return "\n\n".join(parts)


def _plan_decompose_prompt(charter: "Charter", granularity: "str | None" = None,
                           context: str = "") -> str:
    return (
        "あなたはプロジェクトを実行可能なタスクに分解するプランナーです。以下の憲章を、"
        "それぞれ独立に検証できるタスクへ分解してください。"
        + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + (f"\n\n参考文脈（プロジェクトルール・リポジトリ理解。分解の粒度と verify の精度に使う）:\n{context}"
           if context else "")
        + "\n\n出力は JSON 配列のみ。各要素は {\"title\": str, \"verify\": str} で、verify は"
        " 終了コード0をPASSとみなすシェルコマンド（『履歴』でなく『望む最終状態/差分』を見ること）。"
        " タスク間に順序依存があれば **\"after\": [\"先行タスクの title\"]**（配列内の先行タスク・"
        "任意）を付けること（依存グラフとして実行順と並列性の判断に使われる。循環は不可）。"
        " 各タスクには **\"workspace\": \"name\"（唯一の書込先・必須）** を付ける。workspace は"
        " **verify が操作するパスの owns を持つリポジトリ**にすること。読むだけの他リポジトリは"
        " \"refs\": [\"name\", ...] に入れる（書込先にはしない）。"
        " 同じ手順を多数の対象に繰り返すタスクは 1 件ずつ列挙せず、"
        " {\"title\": \"…{item}…\", \"verify\": \"…{item}…\", \"cohort_items\": [\"対象1\", \"対象2\", …]} の"
        " 1 件にまとめること（{item} に各対象が差し込まれ、先頭を pilot として人が指示を固めてから残りが生成される）。"
        " 検証コマンドを書けない曖昧なタスクは含めないでください。")


def assign_plan_workspace(charter: "Charter", spec: dict) -> dict:
    """plan で生成した spec に**書込先 workspace を必ず明示**し、参照を refs に振り分ける。
    workspace = verify が操作するパスの owns を持つリポジトリ（プランナーが付けた workspace が
    owns を持つ書込先候補ならそれを尊重）。それ以外の charter repo・プランナーが挙げた repo は
    すべて参照（refs）として扱う。書込先が決まらなければ何も設定しない（route 層が後段で解決）。"""
    smap = charter_repo_spec_map(charter)
    workspaces = [s for s in charter.repo_specs if s.get("owns")]
    ws = None
    hint = _strip_code(str(spec.get("workspace") or ""))
    if hint and smap.get(hint) and smap[hint].get("owns"):     # プランナー指定（owns 持ち）を尊重
        ws = smap[hint]
    if ws is None:                                             # verify が操作するパスの owns で決定論的に確定
        paths = _split_tokens(spec.get("paths")) or _verify_paths(str(spec.get("verify") or ""))
        ws = _infer_workspace_from_paths(workspaces, paths)
    # 参照: 書込先以外の charter repo すべて＋プランナーが挙げた repos/refs（書込先 url は除く）
    ref_names: "list[str]" = []
    seen: "set[str]" = set()
    cand = list(charter.repo_specs)
    for tok in _coerce_repos(spec.get("refs")) + _coerce_repos(spec.get("repos")):
        sp = smap.get(tok) or _raw_url_spec(tok)
        if sp:
            cand.append(sp)
    for s in cand:
        url = s.get("url")
        if not url or (ws and url == ws["url"]) or url in seen:
            continue
        seen.add(url)
        ref_names.append(s.get("name") or url)
    spec.pop("repos", None)                                   # repos は廃止: workspace/refs へ置換
    if ws is not None:
        spec["workspace"] = ws.get("name") or ws["url"]
    if ref_names:
        spec["refs"] = ",".join(ref_names)
    return spec


def plan_via_agent(cfg: "Config", charter: "Charter") -> "list[dict]":
    """charter をエージェント（kiro-flow/kiro-cli）に分解させ、[{title, verify}, ...] を得る。
    知能は委譲し、取り込み（enqueue）は本体が決定的に行う。失敗時は空（plan を諦め人へ）。
    各タスクには書込先 workspace を必ず明示する（verify が操作するパスの owns を持つ repo）。"""
    ctx = "\n\n".join(x for x in (project_rules_context(cfg), repo_map_context(cfg)) if x)
    try:
        out = _run_kiro_cli(_plan_decompose_prompt(charter, cfg.granularity,
                                                   context=ctx), cfg.model, purpose="plan")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"project plan: 分解に失敗（{e}）")
        return []
    arr = _extract_json_array(out) or []
    specs = []
    for item in arr:
        if isinstance(item, dict) and str(item.get("title", "")).strip():
            sp = {"title": str(item["title"]).strip(),
                  "verify": _strip_code(str(item.get("verify", "") or "").strip()),
                  "workspace": _strip_code(str(item.get("workspace") or "").strip()),
                  "refs": _coerce_repos(item.get("refs")) or _coerce_repos(item.get("repos")),
                  "cohort_items": _coerce_repos(item.get("cohort_items")),
                  # 依存（先行タスクの title）。enqueue 後に id へ決定的に解決される（after_titles）
                  "after_titles": _coerce_titles(item.get("after")),
                  "source": "charter"}
            specs.append(assign_plan_workspace(charter, sp))
    return specs


def plan_via_stub(cfg: "Config", charter: "Charter") -> "list[dict]":
    """plan_via_agent の決定的代替（executor: stub 時のデフォルト planner）。エージェントを
    一切呼ばず、charter.acceptance（呼び出し時点で解決済み前提）をそっくり初期タスクにする。
    verify は人が charter に書いた受入条件そのもの。acceptance が無ければ空（呼び出し元の
    no-acceptance ゲートで人へ回る）。

    stub は goal の文章を読めないため、起票源は acceptance しかない。かつては acceptance を
    その場で実行して未達の項目だけを起票していたが、それだと初回から PASS する acceptance
    （`echo ok` 等）では起票がゼロになり、backlog が空のまま converged して「バージョンを足しても
    バックログが現れない」ことになっていた。plan は未達判定の場ではない（それは evaluate の役目）
    ので、ここでは初回未達とみなして全項目を起票する。二周目以降は _enqueue_specs が backlog と
    archive のタイトルで冪等に弾くため、同じ受入条件が積み直されることはない。"""
    if not charter.acceptance:
        return []
    return _acceptance_specs(list(charter.acceptance))


def review_via_stub(cfg: "Config", charter: "Charter") -> "list[dict]":
    """review_via_agent の決定的代替（executor: stub 時のデフォルト reviewer）。敵対的レビューは
    判断を要する性質上、決定的な代用を作らず常に所見なしを返す（--review-project は既定 opt-in
    off のため、stub 環境では何もしない＝acceptance PASS をそのまま信頼する）。"""
    return []


def _review_prompt(charter: "Charter", granularity: "str | None" = None) -> str:
    return (
        "あなたは成果物を批判的にレビューする敵対的レビュアです。以下の憲章の目標・成果物に対し、"
        "現状の成果物がまだ満たせていない点（短絡的達成・抜け漏れ・品質不足）を洗い出してください。"
        "改善タスクの粒度: " + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + "\n\n出力は JSON 配列のみ。各要素は {\"title\": str, \"verify\": str,"
        " \"workspace\": \"name\"（唯一の書込先・必須。verify が操作するパスの owns を持つ repo）,"
        " \"refs\": [\"name\", ...]（読むだけの参照）}（改善タスクと検証）。"
        " 問題が無ければ空配列 [] を返してください。")


def review_via_agent(cfg: "Config", charter: "Charter") -> "list[dict]":
    """敵対的レビュー（opt-in）。成果物 vs 目標の不足を改善タスク [{title, verify}] として返す。
    plan と同様、各タスクに書込先 workspace を必ず明示する。"""
    try:
        out = _run_kiro_cli(_review_prompt(charter, cfg.granularity), cfg.model, purpose="review")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"project review: レビューに失敗（{e}）")
        return []
    arr = _extract_json_array(out) or []
    specs = []
    for i in arr:
        if isinstance(i, dict) and str(i.get("title", "")).strip():
            sp = {"title": str(i["title"]).strip(),
                  "verify": _strip_code(str(i.get("verify", "") or "").strip()),
                  "workspace": _strip_code(str(i.get("workspace") or "").strip()),
                  "refs": _coerce_repos(i.get("refs")) or _coerce_repos(i.get("repos")),
                  "source": "review"}
            specs.append(assign_plan_workspace(charter, sp))
    return specs


def _enqueue_specs(cfg: "Config", specs: "list[dict]", existing: "list[str]",
                   threshold: float, charter: "str | None" = None,
                   active_only: bool = False) -> "list[Task]":
    """spec 群を冪等に backlog へ投入（既存と類似は飛ばす）。verify 無しは enqueue_task が inbox にする。

    冪等照合は「呼び出し時点のスナップショット ∪ 投入直前に読み直した現物」で行う。plan/review は
    エージェント委譲で数分かかるため、スナップショットだけだと、その間に投入されたタスク
    （別インスタンス・前パスの残り・state_git 同期で届いた分・リセット後に書き戻された残骸）が
    照合に無く、類似バックログを二重投入してしまう。
    active_only は読み直しも「done 以外」に絞る（replan のやり直し経路。スナップショット側の
    絞り込みと揃えないと、ここで done の archive タイトルが混ざり再作成が弾かれてしまう）。"""
    merged = list(existing) + _existing_titles(cfg, charter, active_only=active_only)
    created: list[Task] = []
    afters: "dict[str, list[str]]" = {}   # 新規タスク id → 先行タスクの title 群（後段で id へ解決）
    for sp in specs:
        title = str(sp.get("title", "") or "").strip()
        verify = str(sp.get("verify", "") or "").strip()
        if not title or _is_duplicate(title, verify, merged, threshold):
            continue
        wants = _coerce_titles(sp.pop("after_titles", None))  # 生 title を task に書かない（id が正）
        try:
            t = enqueue_task(cfg, sp)
            created.append(t)
            if wants:
                afters[t.id] = wants
            merged.append(title)
            existing.append(title)   # 呼び出し側スナップショットにも反映（同一パス内の連続呼び出し用）
        except ValueError:
            continue
    if afters:
        _resolve_after_titles(cfg, created, afters)
    return created


def _resolve_after_titles(cfg: "Config", created: "list[Task]",
                          afters: "dict[str, list[str]]") -> None:
    """plan が出した after（先行タスクの title）を id へ決定的に解決して persist する。
    照合は「今回作成分」を優先し、次に現役 backlog のタイトル完全一致。未知 title は落とし、
    循環を作る after はそのタスクの分ごと捨てる（DAG の健全性が優先・落とした事実は journal へ）。"""
    by_title = {t.title: t.id for t in load_tasks(cfg.backlog)}
    by_title.update({t.title: t.id for t in created})
    by_id_created = {t.id: t for t in created}
    # 循環判定のグラフは「backlog の現物 ＋ 今回作成分は同一インスタンス」（解決の途中経過を共有）
    all_tasks = [by_id_created.get(x.id, x) for x in load_tasks(cfg.backlog)]
    for t in created:
        deps: "list[str]" = []
        for w in afters.get(t.id) or []:
            tid = by_title.get(w)
            if tid and tid != t.id and tid not in deps:
                deps.append(tid)
        if not deps:
            continue
        prev = task_deps(t)
        t.set("after", ", ".join(dict.fromkeys(prev + deps)))
        if _after_introduces_cycle(all_tasks, t):
            if prev:
                t.set("after", ", ".join(prev))
            else:
                t.drop("after")
            append_journal(cfg.journal, f"plan の after を循環のため破棄: {t.id}")
        persist_task(cfg, t)


def _charter_single_repo(charter: "Charter") -> "dict | None":
    """charter が「成果を push する対象 repo」を 1 つだけ持つならその spec を返す（複数/0 は None）。
    参照のみ（readonly）repo は成果の出る先ではないので除外する。"""
    work = [r for r in charter.repo_specs if r.get("url") and not r.get("readonly")]
    return work[0] if len(work) == 1 else None


# --------------------------------------------------------------------------
