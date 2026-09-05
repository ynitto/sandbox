from __future__ import annotations
# plan.py — 元 agent-project.py の 9290-9601 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
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
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").split()
    return out[0] if r.returncode == 0 and out else None


# 要約本文の始まり（見出し・箇条書き・番号付き）。この手前は前置き＝作業メモとして落とす。
# repo_map は道具ループ（`--tools bash`）で走る面で、モデルの最終ターンは調査の作業報告に
# なりやすい。本番はその本文をそのまま `context/<repo>.md` に保存して planner へ渡すので、
# **前置きがそのまま計画の材料になる**。プロンプトは既に「要約本文のみ」と言っている
# （＝言わせるのは済んでいる）ので、残りは機械が落とす。
_REPO_MAP_BODY_RE = re.compile(r"(?m)^[ \t]{0,3}(?:#{1,6}[ \t]+\S|[-*+][ \t]+\S|\d+\.[ \t]+\S)")

# 落とさずに残す前置きの上限（字）。概要 1〜2 文（「このリポジトリは…のモノレポである」）は
# 正当な要約の書き出しで、落とすと planner から全体像が消える——自由文の器（クラウド CLI）は
# この形で書き出すことが多い。実測（2026-08-31 台帳）の作業報告はこれより長い複数行で、
# この上限では残らない。
_REPO_MAP_LEAD_MAX = 200


def _repo_map_strip_preamble(body: str) -> "tuple[str, int]":
    """要約本文の手前（前置き＝作業報告）を落とし、(本文, 落とした字数) を返す。

    落とすのは**構造化された本文（見出し・箇条書き）の手前に長い散文があるとき**だけ:

    - 前置きが短い（`_REPO_MAP_LEAD_MAX` 以内）なら残す——概要段落は要約の一部で、
      作業報告ではない
    - 本文の始まりが 1 つも無い出力は**そのまま残す**——散文だけの要約は正当な形
      （構造は義務ではない）。全捨てすると、読めているのに「生成なし」へ倒れる。
      実測の「作業報告だけ」の失敗（2026-08-31: RM1 の失点）は本文ゼロ・`no_command` で、
      ここへは来ない
    """
    text = (body or "").strip()
    m = _REPO_MAP_BODY_RE.search(text)
    if not m or m.start() <= _REPO_MAP_LEAD_MAX:
        return text, 0
    return text[m.start():].strip(), m.start()


# 材料収集の上限。プロンプトへ入れる量を有界に保つ（無制限だと大きい repo で planner の
# 文脈ごと押し出す）。切り捨ては材料の側に注記する（黙って捨てない）。
_REPO_MAP_LS_MAX = 400          # ls-files の行数
_REPO_MAP_HEAD_LINES = 60       # 主要ファイル 1 件から読む行数
_REPO_MAP_MATERIAL_MAX = 8000   # 材料全体の字数
_REPO_MAP_KEY_FILES = ("README.md", "README.rst", "README", "Makefile",
                       "pyproject.toml", "package.json", "setup.py", "setup.cfg")


def _repo_map_material(dest: str) -> str:
    """repo_map の材料を機械が集める（`git ls-files` + 主要ファイルの先頭）。

    構造はファイル一覧から、ビルド・テストのコマンドと規約は README / ビルド定義の
    先頭から読める。道具に探索させると最終ターンが作業報告になる（実測 2026-08-31:
    失点は本文ゼロ・`status=no_command`）ので、材料は機械が集めてプロンプトで渡す。
    """
    try:
        r = subprocess.run(["git", "-C", dest, "ls-files"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        files = (r.stdout or "").splitlines() if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        files = []
    parts = []
    if files:
        listing = "\n".join(files[:_REPO_MAP_LS_MAX])
        if len(files) > _REPO_MAP_LS_MAX:
            listing += f"\n…（他 {len(files) - _REPO_MAP_LS_MAX} 件は省略）"
        parts.append(f"## ファイル一覧（git ls-files）\n{listing}")
    for name in _REPO_MAP_KEY_FILES:
        path = Path(dest) / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            continue
        head = "".join(lines[:_REPO_MAP_HEAD_LINES])
        if head.strip():
            parts.append(f"## {name}（先頭）\n{head}")
    return "\n\n".join(parts)[:_REPO_MAP_MATERIAL_MAX]


def _repo_map_generate(cfg: "Config", spec: dict) -> str:
    """repo を一時 worktree に用意し、機械が集めた材料からエージェントに理解を
    要約させる（有界・失敗は空）。

    2026-08-31 の作り変え: 以前は clone 先を道具（`--tools bash`）で探索させていた。
    実測の失点は道具ループの出口（本文ゼロ・`status=no_command`）だったので、材料を
    機械が集めてプロンプトへ入れ、道具を外した（purpose=repo_map は readonly 既定）。
    材料が集まらない repo（git が読めない）は従来の clone 失敗と同じ「生成なし」へ倒す。
    """
    tmp = tempfile.mkdtemp(prefix="agent-repomap-")
    dest = str(Path(tmp) / "repo")
    try:
        _clone_repo_shallow(spec["url"], spec.get("base") or "", dest)
        material = _repo_map_material(dest)
        if not material.strip():
            return ""
        prompt = (
            "次の材料はあるリポジトリのファイル一覧と主要ファイルの先頭です。"
            "この材料だけから、次を Markdown で 2000 字以内に要約してください。\n"
            "- 構造（主要ディレクトリと役割）\n- 主要モジュールと責務\n"
            "- ビルド・テスト・リンタの実行コマンド\n- 命名・実装の規約（読み取れる範囲で）\n"
            "材料に無いことは推測で書かないこと。出力は要約本文のみ（前置き・後書きなし）。\n\n"
            + material)
        raw = _run_agent_cli(prompt, cfg.model, purpose="repo_map").strip()
        body, dropped = _repo_map_strip_preamble(raw)
        if dropped:
            append_journal(cfg.journal,
                           f"repo-map: 前置きを {dropped} 字落とした"
                           f"（{spec.get('name') or spec.get('url')}）")
        return body[:4000]
    except Exception:  # noqa: BLE001  clone 失敗・エージェント不在・タイムアウトは生成なし
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_repo_maps(cfg: "Config", charter: "Charter | None", force: bool = False) -> None:
    """charter の書込先 repo ごとに context/<repo名>.md を用意する（plan の直前に呼ぶ）。
    HEAD sha が前回生成時と同じなら再生成しない（sha 不明でファイルが既にあれば温存＝
    無限再生成を避ける）。stub executor では生成しない（plan_via_stub と同じ扱い）。

    force=True は `repo_map` 設定に関わらず生成する。plan と spec の経路がこれを使う:
    S6 の必須セクション（作業概要の「変更対象」）も S7 のライト spec（影響範囲）も、
    **既存コードの文脈が無いと書けない**。opt-in のままだと決定的ゲートが恒常的に発火し、
    設定 1 つで機能全体が空回りする。

    **コスト**: 生成（clone + LLM）は HEAD sha が変わらない限り走らない。ただし
    **sha の取得（`git ls-remote`）は毎回走る**——非 readonly repo 1 件につき 1 往復で、
    到達不能なら `_repo_head_sha` のタイムアウト（60 秒）まで待つ。plan の前置を無条件に
    した分、オフラインのノードではここが plan の待ち時間になる（積み残し: 設計 §7-6）。
    """
    if not charter or cfg.executor == "stub" or not (force or cfg.repo_map):
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


# 1 件ずつ出させる契約（split → map と同じ形。件数の制御は本体が持つ）。
# 必須セクション 6 つ × 複数タスクを 1 回の JSON 配列で出させると、`--format json`
# （本番の `plan` は `ollama-json` へ振り替わる＝**オブジェクト**しか返せない）と衝突し、
# 実測 5 回のうち 4 回はタスク 0 件になっていた（2026-08-31）。
PLAN_ONE_AT_A_TIME = (
    "\n\n出力は **JSON オブジェクト 1 件のみ**（タスク 1 件）。配列にしないこと。"
    " これ以上足すべきタスクが無ければ {\"done\": true} だけを返すこと。オブジェクトは")

# 配列で一括に受ける契約（自由文の器＝クラウド CLI ほか向け）。1 件ずつはオブジェクトしか
# 返せない器（`json_object_only`）への手当てであって、配列を返せる器でまで払うと
# タスク K 件に K+1 回の呼び出し（毎回 charter 全文）を課すことになる。器で分ける
# （`_plan_object_only`）——一般則「`--format json` の面に配列契約を書かない」の対偶で、
# 配列を返せる面から配列契約を取り上げる理由も無い。
PLAN_ARRAY = "\n\n出力は JSON 配列のみ。各要素は"


def _plan_decompose_prompt(charter: "Charter", granularity: "str | None" = None,
                           context: str = "", contract: str = "single") -> str:
    one = contract != "array"
    return (
        "あなたはプロジェクトを実行可能なタスクに分解するプランナーです。以下の憲章を、"
        "それぞれ独立に検証できるタスクへ分解してください。"
        + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + (f"\n\n参考文脈（プロジェクトルール・リポジトリ理解。分解の粒度と verify の精度に使う）:\n{context}"
           if context else "")
        + (PLAN_ONE_AT_A_TIME if one else PLAN_ARRAY)
        + " {\"title\": str, …} で、"
        " 各タスクには次を**必ず**付けること（人が実行前にレビューする材料であり、欠けると"
        "そのタスクは draft に落ちて実行されない）:"
        " **\"why\": str（憲章のどの目標に効くか・1〜2 文）**、"
        " **\"desc\": [str, …]（作業概要＝変更対象・作業ステップ・影響範囲を1要素1項目）**、"
        " **\"scope\": [str, …]（変更してよい範囲）**、"
        " **\"risks\": [str, …]（実装・運用上のリスクと対策。該当なしも [\"なし\"]）**、"
        " **\"acceptance\": [str, …]（受入基準チェックリスト 3〜7 項目・自然文。"
        "検証エージェントが基準ごとに実行して証跡付きで判定し、全 pass のみが完了の根拠になる。"
        "シェルコマンドを 1 行合成して書かないこと——確かめ方は検証エージェントが決める）**、"
        " **\"size\": \"S\"|\"M\"|\"L\"（規模感）**。"
        " タスク間に順序依存があれば **\"after\": [\"先行タスクの title\"]**（"
        + ("**既に出したタスク**の title・任意" if one else "配列内の先行タスク・任意")
        + "）を付けること（依存グラフとして実行順と並列性の判断に使われる。循環は不可）。"
        " 各タスクには **\"workspace\": \"name\"（唯一の書込先・必須）** を付ける。workspace は"
        " **受入基準が触るパスの owns を持つリポジトリ**にすること。読むだけの他リポジトリは"
        " \"refs\": [\"name\", ...] に入れる（書込先にはしない）。"
        " 任意で \"paths\": [str, …]（このタスクが触る見込みのパス）を付けると、"
        "書込先の突き合わせ（owns）に使われる。"
        " 同じ手順を多数の対象に繰り返すタスクは 1 件ずつ列挙せず、"
        " {\"title\": \"…{item}…\", \"acceptance\": [\"…{item}…\", …], \"cohort_items\": [\"対象1\", \"対象2\", …]} の"
        " 1 件にまとめること（{item} に各対象が差し込まれ、先頭を pilot として人が指示を固めてから残りが生成される）。"
        " 有益なら任意で \"out_of_scope\": str（このタスクで"
        "やらないこと・隣のタスクとの境界）・\"hints\": str（実装の手がかり・関連ファイルや参考箇所）"
        "も付けること（これらは実行ワーカーへの指示と人の実行前レビューの判断材料になる）。"
        " 何を完了とするかを書けない曖昧なタスクは含めないでください。")


def _multi_ws(cfg) -> bool:
    """書込先が複数になることを許す設定（`multi_workspace`）。既定 false＝従来どおり 1 つ。"""
    return bool(getattr(cfg, "multi_workspace", False))


def _owns_hits_for_paths(workspaces: "list[dict]", paths: "list[str]") -> "list[dict]":
    """パス群の owns にヒットする書込先を**全部**返す（順序は charter の並び）。"""
    if not paths:
        return []
    return [s for s in workspaces if any(_owns_matches(s.get("owns", []), p) for p in paths)]


def assign_plan_workspace(charter: "Charter", spec: dict, multi: bool = False) -> dict:
    """plan で生成した spec に**書込先 workspace を必ず明示**し、参照を refs に振り分ける。
    workspace = verify が操作するパスの owns を持つリポジトリ（プランナーが付けた workspace が
    owns を持つ書込先候補ならそれを尊重）。それ以外の charter repo・プランナーが挙げた repo は
    すべて参照（refs）として扱う。書込先が決まらなければ何も設定しない（route 層が後段で解決）。

    `multi`（プロジェクト設定 `multi_workspace`）が真なら、操作パスが**複数 repo の owns に
    跨る**ときだけ `- workspace: a, b` の集合を書く。既定は従来どおり——曖昧なら空にして
    決定的解決（rule → owns → 既定 → 候補が 1 つ）へ倒す。プランナー（LLM）に repo を
    増やさせるのではなく、あくまで owns という決定論の結果を畳まずに残すだけである。"""
    smap = charter_repo_spec_map(charter)
    workspaces = [s for s in charter.repo_specs if s.get("owns")]
    picked: "list[dict]" = []
    hint = _strip_code(str(spec.get("workspace") or ""))
    if hint and smap.get(hint) and smap[hint].get("owns"):     # プランナー指定（owns 持ち）を尊重
        picked = [smap[hint]]
    if not picked:                                             # verify が操作するパスの owns で決定論的に確定
        paths = _split_tokens(spec.get("paths")) or _verify_paths(str(spec.get("verify") or ""))
        hits = _owns_hits_for_paths(workspaces, paths)
        if len(hits) > 1 and multi:
            picked = hits                                      # 複数 repo に跨る＝両方に書く
        else:
            one = _infer_workspace_from_paths(workspaces, paths)
            picked = [one] if one else []
    ws = picked[0] if picked else None
    ws_urls = {s["url"] for s in picked if s.get("url")}
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
        if not url or url in ws_urls or url in seen:
            continue
        seen.add(url)
        ref_names.append(s.get("name") or url)
    spec.pop("repos", None)                                   # repos は廃止: workspace/refs へ置換
    if picked:
        spec["workspace"] = ", ".join(s.get("name") or s["url"] for s in picked)
    elif not smap.get(hint):
        # 候補に無い名前は**残さない**。プランナーは書込先を訊かれて「（なし、ファイル単位の
        # 作業のため）」のような散文や成果物のファイル名を書くことがあり、それがそのまま
        # タスクの書込先として下流（route / act）へ流れていた。決まらないときは空にして
        # 決定的解決（rule → owns → 既定 → 候補が 1 つ）へ倒す＝docstring どおりの動き。
        spec["workspace"] = ""
    if ref_names:
        spec["refs"] = ",".join(ref_names)
    return spec


# 計画レビューに要る必須セクション（S6-2）。**欠落は機械で見る**——LLM に「ちゃんと書けたか」を
# 自己申告させても意味がない。`workspace` は assign_plan_workspace が推定で補うのでここには入れない。
PLAN_REQUIRED_KEYS = ("why", "desc", "scope", "risks", "acceptance", "size")

_PLAN_KEY_LABELS = {"why": "why（なぜやるか）", "desc": "desc（作業概要）",
                    "scope": "scope（変更範囲）", "risks": "risks（リスクと対策）",
                    "acceptance": "acceptance（受入基準）", "size": "size（規模感 S/M/L）"}


def _validate_backlog_spec(spec: dict) -> "list[str]":
    """必須セクションのうち欠落しているキー名を返す（決定的ゲート）。"""
    missing = []
    for k in PLAN_REQUIRED_KEYS:
        v = spec.get(k)
        if k == "acceptance":
            if not coerce_multiline(v):
                missing.append(k)
        elif not str(v or "").strip():
            missing.append(k)
    return missing


# プランナーへ見せる却下済み（archive の rejected）の上限。archive は際限なく育つので、
# 直近（mtime 新しい順）に絞ってプロンプトを有界に保つ。
_PLANNER_REJECTED_LIMIT = 30

_REJECT_REASON_RE = re.compile(r"(?m)^- 却下:\s*(?P<reason>.+)$")


def _backlog_existing_summary(cfg: "Config", charter_tag: "str | None") -> "list[dict]":
    """プランナーへ渡す既存タスク一覧（重複・意図の再提案を「出させない」ための入力・S6-5 ①）。

    投入側の Jaccard 照合（最終防衛線）は残したまま、生成側にも既存を見せる。
    投入側だけだと、プランナーは毎回同じものを出し、それが黙って落とされる分の
    トークンを払い続けることになる（人からは「再分解しても何も起きない」に見える）。

    現役 backlog（保留 blocked・仕掛かり doing/offloaded・レビュー中 review を含む）に加えて
    **archive の却下済み（rejected）も理由付きで載せる**。抑止の一次表現は投入側のタイトル照合
    ではなくこの入力——プランナー（スキル）が「同一バージョンのバックログと意図が似ているものは
    出さない」を判断する。タイトルが違っても意図が同じ再提案はタイトル照合では捕まらないため。
    """
    out: "list[dict]" = []
    for t in load_tasks(cfg.backlog):
        if not task_belongs_to_charter(t, charter_tag):
            continue
        out.append({"id": t.id, "title": t.title, "status": t.norm_status(),
                    "edited": str(t.get("edited") or ""),
                    "summary": str(t.get("why") or t.get("desc") or "")[:160]})
    adir = cfg.archive_dir()
    if adir.exists():
        rejected: "list[tuple[float, dict]]" = []
        for p in adir.glob("*.md"):
            try:
                text = p.read_text(encoding="utf-8")
                t = parse_task(text, p.stem)
            except (OSError, ValueError):
                continue
            if t.norm_status() != "rejected" or not task_belongs_to_charter(t, charter_tag):
                continue
            m = _REJECT_REASON_RE.search(text)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            rejected.append((mtime, {
                "id": t.id, "title": t.title, "status": "rejected",
                "edited": str(t.get("edited") or ""),
                "reason": (m.group("reason").strip() if m else ""),
                "summary": str(t.get("why") or t.get("desc") or "")[:160]}))
        rejected.sort(key=lambda x: x[0], reverse=True)
        out.extend(rec for _, rec in rejected[:_PLANNER_REJECTED_LIMIT])
    return out


def build_planner_input(cfg: "Config", charter: "Charter", charter_tag: "str | None" = None,
                        notes: str = "", retry: str = "",
                        produced: "list[str] | None" = None,
                        contract: str = "single") -> dict:
    """backlog-planner スキルへ渡す入力（契約は .github/skills/backlog-planner/SKILL.md）。

    `produced` は**この分解で既に出したタスクの題**。1 件ずつ出させる契約
    （`PLAN_ONE_AT_A_TIME`）で、重複を避けさせ `after` の参照先を与えるために渡す。
    `contract` は出力契約の選択（"single" = 1 件ずつ / "array" = 配列で一括）。
    器で決まる（`_plan_object_only`）——スキル側は写しを持たず、この値に従う。
    """
    return {
        "contract": ("array" if contract == "array" else "single"),
        "produced": list(produced or []),
        "charter": build_charter_request(charter),
        "owns": _charter_owns_note(charter),
        "granularity": (cfg.granularity or "coarse"),
        "rules": project_rules_context(cfg),
        "repo_context": repo_map_context(cfg),
        "existing": _backlog_existing_summary(cfg, charter_tag),
        "tombstones": [{"title": t["title"], "reason": t["reason"]}
                       for t in load_tombstones(cfg, charter_tag)],
        "notes": notes,
        "retry": retry,
    }


def build_planner_prompt(cfg: "Config", spec: dict, charter: "Charter") -> str:
    """分解プロンプトを組み立てる（スキル優先・見つからなければ組み込み）。

    スキルを必須にしないのは、**計画が止まるとプロジェクトが 1 歩も進まない**から
    （backlog-verifier と同じ判断）。組み込みは従来のハードコードプロンプトそのもの。
    """
    skill = str(getattr(cfg, "planner_skill", "backlog-planner") or "backlog-planner")
    script = find_skill_script(skill, "prompt.py")
    if script:
        try:
            proc = subprocess.run([sys.executable, script],
                                  input=json.dumps(spec, ensure_ascii=False),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
            print(f">>> 警告: {skill} スキルがプロンプトを返しませんでした（組み込みへ）: "
                  f"{(proc.stderr or '').strip()[:200]}", file=sys.stderr)
        except (OSError, subprocess.SubprocessError) as e:
            print(f">>> 警告: {skill} スキルを実行できませんでした（組み込みへ）: {e}", file=sys.stderr)
    ctx = "\n\n".join(x for x in (spec.get("rules") or "", spec.get("repo_context") or "") if x)
    return _plan_decompose_prompt(charter, spec.get("granularity"), context=ctx,
                                  contract=str(spec.get("contract") or "single")) \
        + _builtin_planner_extras(spec)


def _builtin_planner_extras(spec: dict) -> str:
    """組み込みプロンプトへ足す入力（既存タスク・墓標・メモ・再要求）。

    スキル側と同じ情報を載せる。落とすと、スキルを入れていない環境では
    **再要求が同じプロンプトの繰り返しになり**（欠落は直らない）、`distill-notes` は
    メモを読まないまま分解する（＝押しても何も起きない）。
    """
    def _block(title: str, body: str) -> str:
        body = (body or "").strip()
        return f"\n\n## {title}\n{body}" if body else ""

    live = [e for e in (spec.get("existing") or [])
            if e.get("title") and e.get("status") != "rejected"]
    rejected = [e for e in (spec.get("existing") or [])
                if e.get("title") and e.get("status") == "rejected"]
    existing = "\n".join(
        f"- {e['title']}（{e.get('status') or '?'}"
        + ("／**人が確定済み・作り直さない**" if e.get("edited") == "human" else "") + "）"
        + (f" — {e['summary']}" if e.get("summary") else "")
        for e in live)
    rejected_rows = "\n".join(
        f"- {e['title']}" + (f" — 却下理由: {e['reason']}" if e.get("reason") else "")
        + (f"（{e['summary']}）" if e.get("summary") else "")
        for e in rejected)
    graves = "\n".join(
        f"- {g['title']}" + (f" — 却下理由: {g['reason']}" if g.get("reason") else "")
        for g in (spec.get("tombstones") or []) if g.get("title"))
    produced = "\n".join(f"- {t}" for t in (spec.get("produced") or []) if t)
    return (
        _block("この分解で既に出したタスク（**同じ・似たものを出さない**。"
               "\"after\" の参照先にはこの題を使う）", produced)
        + _block("既存タスク（このバージョンのバックログ。**タイトルが違っても、これらと意図が"
               "同じ・似ているタスクは出力しない**——保留・実行中・レビュー中のものも、言い換えや"
               "粒度を変えた再提案をしないこと）", existing)
        + _block("却下済み（人が明示的に廃止したタスク。**同じものはもちろん、意図が似ている"
                 "タスクも再提案しない**——却下理由が示す方針に反する提案は出さないこと）",
                 rejected_rows)
        + _block("却下・削除済み（墓標。同じものを再提案しない）", graves)
        + _block("観点メモ（人が書き溜めたもの。ここからもタスクを起こす）",
                 str(spec.get("notes") or ""))
        + _block("⚠ 前回の出力に不足がありました（必須項目をすべて埋めて出し直してください）",
                 str(spec.get("retry") or "")))


def _plan_spec_from_item(charter: "Charter", item: dict, multi: bool = False) -> dict:
    """プランナー出力の 1 要素をタスク spec へ正規化する。"""
    title = str(item["title"]).strip()
    sp = {"title": title,
          # 原題を残す: 人が題を直しても重複照合が効き続ける（S6-3 の保護の実体）
          "planned_title": title,
          # 旧 "verify"（コマンド一発生成）は P1-A8 で受け取りをやめた。プランナーが出しても
          # 捨てる——新規データに裸の verify を書かない（検証の一次表現は acceptance）。
          "workspace": _strip_code(str(item.get("workspace") or "").strip()),
          # 触る見込みのパス（owns 突き合わせの根拠）。`,` 区切り 1 行（enqueue の paths と同じ規約）
          "paths": ",".join(_coerce_repos(item.get("paths"))),
          "refs": _coerce_repos(item.get("refs")) or _coerce_repos(item.get("repos")),
          "cohort_items": _coerce_repos(item.get("cohort_items")),
          "acceptance": coerce_multiline(item.get("acceptance")),
          # 依存（先行タスクの title）。enqueue 後に id へ決定的に解決される（after_titles）
          "after_titles": _coerce_titles(item.get("after")),
          # 誘導・レビュー記述（実行前レビューの判断材料 兼 ワーカーへの指示）
          **{k: (item.get(k) if isinstance(item.get(k), list)
                 else str(item.get(k) or "").strip())
             for k in ("why", "desc", "scope", "out_of_scope", "hints", "risks")
             if item.get(k) not in (None, "", [])},
          "source": "charter"}
    size = str(item.get("size") or "").strip().upper()
    if size in ("S", "M", "L"):
        sp["size"] = size
    return assign_plan_workspace(charter, sp, multi)


def _plan_retry_note(bad: "list[tuple[str, list[str]]]") -> str:
    """再要求のプロンプトに載せる「どのタスクの何が欠けたか」。"""
    return "\n".join(
        f"- 「{title}」: {', '.join(_PLAN_KEY_LABELS.get(k, k) for k in missing)} が未記入"
        for title, missing in bad[:20])


# 1 回の分解で受け取るタスクの上限（件数の制御は**本体が持つ**）。粒度の目安と同じ語彙で、
# モデルが「もう無い」と言わなかったときの止め所。
_PLAN_MAX_ITEMS = {"coarse": 10, "fine": 20, "finest": 30}


def _plan_item_from_output(out: str) -> "dict | None":
    """1 件契約の受け方。本番の `plan` は `ollama-json`（`--format json`）へ振り替わり、
    **オブジェクトしか返せない**ので、オブジェクト 1 件で受ける。`{"task": {...}}` /
    `{"tasks": [{...}]}` の 1 段の包みは剥がす。title が無ければ「もう出すものが無い」
    （`{"done": true}`）とみなして None を返す。"""
    obj = _extract_json_object_loose(out)
    if not isinstance(obj, dict):
        return None
    if str(obj.get("title") or "").strip():
        return obj
    for v in obj.values():
        if isinstance(v, dict) and str(v.get("title") or "").strip():
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict) \
                and str(v[0].get("title") or "").strip():
            return v[0]
    return None


def _items_from_output(out: str) -> "list[dict]":
    """配列契約の受け方。**1 件だけ返された回を 0 件と読まない。**

    本番の `plan` / `review` は `ollama-json`（`--format json`）で走り、この器は
    **オブジェクトしか返せない**——モデルは 1 件のとき配列をやめてオブジェクトを返す。
    `_extract_json_array` は「最初の釣り合った `[...]`」を拾うので、そこから本文中の配列
    （`desc` / `acceptance`）を先に取り、dict 要素 0 件＝「1 件も出なかった」になる。
    plan では実測で 5 回中 4 回これを踏んでいた（2026-08-31）。review も同じ受け方で、
    **所見 1 件は黙って「所見なし」になり、プロジェクトはそのまま収束していた。**
    """
    items = [i for i in (_extract_json_array(out) or [])
             if isinstance(i, dict) and str(i.get("title", "")).strip()]
    if items:
        return items
    one = _plan_item_from_output(out)      # 1 件だけのオブジェクト（包み 1 段も剥がす）
    return [one] if one else []


def _plan_object_only(cfg: "Config") -> bool:
    """本番の `plan` が **JSON オブジェクト 1 件しか返せない器**で走るか。

    定義に問い合わせる（argv の綴りでは判定しない）——器の性質は agents/*.json の
    `json_object_only`（ollama の json profile が true）。自由文の器（クラウド CLI ほか）は
    False で、配列契約 1 回で受ける——1 件ずつはオブジェクト限定の器への手当てであり、
    配列を返せる器でまで払うとタスク K 件に K+1 回（毎回 charter 全文）を課す。
    定義が引けないときは 1 件ずつへ倒す（どちらの器でも動く側）。
    """
    try:
        cli, _ = _agent_for("plan")
        return bool(load_agent_plugin(cli).get("json_object_only"))
    except Exception:  # noqa: BLE001  定義欠落・解決不能は起動時に別途表面化する
        return True


def _plan_array_specs(cfg: "Config", charter: "Charter", charter_tag: "str | None",
                      notes: str, strict: bool) -> "list[dict]":
    """配列契約 1 回で受ける（自由文の器向け・従来の受け方）。
    必須セクションの欠落は**機械で見て 1 回だけ再要求**する。失敗は空（plan を諦め人へ）。"""
    retry = ""
    specs: "list[dict]" = []
    for attempt in range(2):
        pin = build_planner_input(cfg, charter, charter_tag, notes=notes, retry=retry,
                                  contract="array")
        try:
            out = _run_agent_cli(build_planner_prompt(cfg, pin, charter),
                                 cfg.model, purpose="plan")
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            append_journal(cfg.journal, f"project plan: 分解に失敗（{e}）")
            return []
        specs = [_plan_spec_from_item(charter, i, _multi_ws(cfg))
                 for i in (_extract_json_array(out) or [])
                 if isinstance(i, dict) and str(i.get("title", "")).strip()]
        bad = [(sp["title"], m) for sp in specs if (m := _validate_backlog_spec(sp))]
        if not bad or not strict or attempt == 1:
            return specs
        retry = _plan_retry_note(bad)
        append_journal(cfg.journal,
                       f"project plan: 必須セクション欠落 {len(bad)} 件 → 1 回だけ再要求する")
    return specs


def _plan_next_spec(cfg: "Config", charter: "Charter", charter_tag: "str | None",
                    notes: str, produced: "list[str]", strict: bool) -> "dict | None":
    """次の 1 件をプランナーから受け取る。欠落は**機械で見て 1 回だけ再要求**する。
    もう出すものが無い（または呼び出しに失敗した）なら None。"""
    retry, spec = "", None
    for _ in range(2):
        pin = build_planner_input(cfg, charter, charter_tag, notes=notes, retry=retry,
                                  produced=produced)
        try:
            out = _run_agent_cli(build_planner_prompt(cfg, pin, charter),
                                 cfg.model, purpose="plan")
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            append_journal(cfg.journal, f"project plan: 分解に失敗（{e}）")
            return spec
        item = _plan_item_from_output(out)
        if item is None:
            return spec
        spec = _plan_spec_from_item(charter, item, _multi_ws(cfg))
        missing = _validate_backlog_spec(spec)
        if not missing or not strict:
            return spec
        retry = _plan_retry_note([(spec["title"], missing)])
        append_journal(cfg.journal,
                       f"project plan: 「{spec['title']}」の必須セクション欠落 "
                       f"{missing} → 1 回だけ再要求する")
    return spec


def plan_via_agent(cfg: "Config", charter: "Charter", charter_tag: "str | None" = None,
                   notes: str = "") -> "list[dict]":
    """charter を backlog-planner スキルに分解させ、タスク spec 群を得る。
    知能は委譲し、取り込み（enqueue）は本体が決定的に行う。失敗時は空（plan を諦め人へ）。

    **出力契約は器で選ぶ**（`_plan_object_only`）。オブジェクトしか返せない器
    （`--format json`＝ollama-json 系）では 1 件ずつ出させて機械が集める（`split` → `map` と
    同じ形）——配列契約のままだと起動形と衝突して 5 回中 4 回がタスク 0 件だった
    （2026-08-31 の実測）。配列を返せる器（クラウド CLI ほか）では従来どおり配列 1 回で
    受ける——1 件ずつを課すとタスク K 件に K+1 回の呼び出し（毎回 charter 全文）を払う。

    必須セクション（why / 作業概要 / 受入基準 / 規模感）の欠落は**機械で見て 1 回だけ再要求**し、
    それでも欠けるタスクは**捨てずに、人の目に入る場所へ置く**。捨てると人には「プランナーが
    何も出さなかった」としか見えず、charter の書き方が悪いのかスキルが壊れたのかを切り分ける
    材料が消える。置き場は人が設定したレビュー面に合わせる:

      plan_review: on  → `proposed`（計画レビュー票に欠落項目を書く。人が直して承認できる）
      plan_review: off → `draft`  （票が立たない設定なので、消化対象外にして journal に残す）

    どちらでも**未記入のまま実行されることはない**。
    """
    strict = str(getattr(cfg, "plan_sections", "required") or "required") == "required"
    specs: "list[dict]" = []
    if not _plan_object_only(cfg):
        specs = _plan_array_specs(cfg, charter, charter_tag, notes, strict)
    else:
        cap = _PLAN_MAX_ITEMS.get(str(getattr(cfg, "granularity", "") or "coarse").lower(), 10)
        produced: "list[str]" = []
        for _ in range(cap):
            sp = _plan_next_spec(cfg, charter, charter_tag, notes, produced, strict)
            if sp is None:
                break
            if sp["title"] in produced:   # 同じ題を繰り返し始めたら進んでいない＝打ち切る
                append_journal(cfg.journal,
                               f"project plan: 「{sp['title']}」を繰り返したので分解を打ち切る")
                break
            specs.append(sp)
            produced.append(sp["title"])
    for sp in specs:                       # 2 回目も欠けたものは捨てずに人の目へ回す
        missing = _validate_backlog_spec(sp)
        if not (missing and strict):
            continue
        labels = ", ".join(_PLAN_KEY_LABELS.get(k, k) for k in missing)
        sp["status"] = "proposed" if getattr(cfg, "plan_review", False) else "draft"
        sp["needs_reason"] = (f"計画レビューに要る項目が未記入です: {labels}。"
                              "内容を直してから承認してください（このまま承認すると"
                              "未記入のまま実行されます）")
        append_journal(cfg.journal,
                       f"project plan: 「{sp['title']}」の必須項目が未記入（{labels}）→ "
                       f"{sp['status']} で投入")
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


# レビュアへ渡す完了済みの上限（archive は際限なく育つので直近だけ）。
_REVIEW_DONE_LIMIT = 30


def _acceptance_report(results: "list | None") -> str:
    """決定的な受入コマンドの判定結果（機械が実行した事実）を 1 行ずつにする。"""
    rows = []
    for row in (results or []):
        try:
            cmd, ok, msg = row[0], row[1], (row[2] if len(row) > 2 else "")
        except (TypeError, IndexError):
            continue
        line = f"- {'PASS' if ok else 'FAIL'}: {cmd}"
        if not ok and str(msg or "").strip():
            line += f" — {str(msg).strip()[:160]}"
        rows.append(line)
    return "\n".join(rows)


def _review_progress_summary(cfg: "Config", charter_tag: "str | None" = None) -> str:
    """成果物の状態（完了済み・残り）。**レビュアが当否を判断できる唯一の材料**である。

    憲章だけを見せると、モデルは何が未達かを推測して書く（実測 RV1 の落ち方は
    `workspace` に成果物のファイル名を書く形）。PV1（撤去された charter verifier）と
    同じ「材料が手元に無い」構造なので、道具ではなく材料を届ける。
    """
    rows: "list[str]" = []
    done: "list[tuple[float, str]]" = []
    for t in load_tasks(cfg.backlog):
        if not task_belongs_to_charter(t, charter_tag):
            continue
        if t.norm_status() == "done":
            done.append((0.0, t.title))
        else:
            rows.append(f"- 残り（{t.norm_status()}）: {t.title}")
    adir = cfg.archive_dir()
    if adir.exists():
        for path in adir.glob("*.md"):
            try:
                t = parse_task(path.read_text(encoding="utf-8"), path.stem)
                mtime = path.stat().st_mtime
            except (OSError, ValueError):
                continue
            if t.norm_status() == "done" and task_belongs_to_charter(t, charter_tag):
                done.append((mtime, t.title))
    done.sort(key=lambda x: x[0], reverse=True)
    return "\n".join([f"- 完了: {title}" for _, title in done[:_REVIEW_DONE_LIMIT]] + rows)


def _review_prompt(charter: "Charter", granularity: "str | None" = None,
                   acceptance: str = "", progress: str = "") -> str:
    def _block(title: str, body: str) -> str:
        body = (body or "").strip()
        return f"\n\n## {title}\n{body}" if body else ""

    return (
        "あなたは成果物を批判的にレビューする敵対的レビュアです。以下の憲章の目標・成果物に対し、"
        "現状の成果物がまだ満たせていない点（短絡的達成・抜け漏れ・品質不足）を洗い出してください。"
        "改善タスクの粒度: " + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + _block("決定的な受入コマンドの判定結果（機械が実行した事実。**全 PASS でも"
                 "成果物が揃っているとは限らない**——この短絡を疑うのがあなたの仕事）", acceptance)
        + _block("成果物の現状（バックログの完了済みと残り。**ここに無い成果物は誰も作っていない**）",
                 progress)
        + "\n\n憲章の成果物・目標と上の現状を突き合わせ、**まだ誰も手を付けていないもの**と"
        "**done になっているが目標を満たしていないもの**を指摘してください。"
        + "\n\n出力は JSON 配列のみ。各要素は {\"title\": str,"
        " \"acceptance\": [str, …]（受入基準・自然文。検証エージェントが基準ごとに実行して"
        "証跡付きで判定する。シェルコマンドを 1 行合成して書かないこと）,"
        " \"workspace\": \"name\"（唯一の書込先・必須。受入基準が触るパスの owns を持つ repo）,"
        " \"refs\": [\"name\", ...]（読むだけの参照）}（改善タスク）。"
        " 各タスクには \"why\": str（何が不足でこの改善が要るのか・人のレビュー向けに 1 文）を付けること。"
        " 問題が無ければ空配列 [] を返してください。")


def review_via_agent(cfg: "Config", charter: "Charter", results: "list | None" = None,
                     charter_tag: "str | None" = None) -> "list[dict]":
    """敵対的レビュー（opt-in）。成果物 vs 目標の不足を改善タスク [{title, acceptance}] として返す。
    plan と同様、各タスクに書込先 workspace を必ず明示する（旧 "verify" の受け取りは
    P1-A8 でやめた——新規データに裸の verify を書かない）。

    `results` は呼び出し元（`_project_evaluate`）が**その場で実行した**受入コマンドの判定
    （再実行はしない）。これと backlog / archive の要約が、レビュアが当否を判断できる材料である。
    """
    try:
        out = _run_agent_cli(
            _review_prompt(charter, cfg.granularity,
                           acceptance=_acceptance_report(results),
                           progress=_review_progress_summary(cfg, charter_tag)),
            cfg.model, purpose="review")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"project review: レビューに失敗（{e}）")
        return []
    specs = []
    for i in _items_from_output(out):
        sp = {"title": str(i["title"]).strip(),
              "acceptance": coerce_multiline(i.get("acceptance")),
              "workspace": _strip_code(str(i.get("workspace") or "").strip()),
              "refs": _coerce_repos(i.get("refs")) or _coerce_repos(i.get("repos")),
              **({"why": str(i.get("why") or "").strip()} if str(i.get("why") or "").strip() else {}),
              "source": "review"}
        specs.append(assign_plan_workspace(charter, sp, _multi_ws(cfg)))
    return specs


def _enqueue_specs(cfg: "Config", specs: "list[dict]", existing: "list[str]",
                   threshold: float, charter: "str | None" = None,
                   active_only: bool = False,
                   ignore_tombstones: bool = False) -> "list[Task]":
    """spec 群を冪等に backlog へ投入（既存と類似は飛ばす）。verify 無しは enqueue_task が inbox にする。

    冪等照合は「呼び出し時点のスナップショット ∪ 投入直前に読み直した現物」で行う。plan/review は
    エージェント委譲で数分かかるため、スナップショットだけだと、その間に投入されたタスク
    （別インスタンス・前パスの残り・state_git 同期で届いた分・リセット後に書き戻された残骸）が
    照合に無く、類似バックログを二重投入してしまう。
    active_only は読み直しも「done 以外」に絞る（replan のやり直し経路。スナップショット側の
    絞り込みと揃えないと、ここで done の archive タイトルが混ざり再作成が弾かれてしまう）。

    ignore_tombstones=True（`replan --revive`）は墓標を**今回だけ**無視する（行は消さない）。
    消すのは `agent-project revive`——再分解の結果を見てから消すか決められるようにするため。"""
    merged = list(existing) + _existing_titles(cfg, charter, active_only=active_only)
    graves = [] if ignore_tombstones else load_tombstones(cfg, charter)
    created: list[Task] = []
    afters: "dict[str, list[str]]" = {}   # 新規タスク id → 先行タスクの title 群（後段で id へ解決）
    for sp in specs:
        title = str(sp.get("title", "") or "").strip()
        verify = str(sp.get("verify", "") or "").strip()
        if not title:
            continue
        if _is_duplicate(title, verify, merged, threshold):
            # 止めるのは完全一致だけ。しかも黙って落とさない——プランナーから見ると出したものが
            # 消えるので、記録が無いと「再分解しても何も起きない」としか見えない。
            append_journal(cfg.journal, f"同じ題の既存タスクがあるため投入を見送り: {title}")
            continue
        grave = tombstone_hit(title, graves)     # 完全一致のみ抑止（§ tombstone_hit の注記）
        if grave is not None:
            append_journal(cfg.journal,
                           f"墓標により投入を見送り: {title}"
                           + (f"（却下理由: {grave['reason']}）" if grave["reason"] else "")
                           + "。作り直すなら `agent-project revive` で解除してください")
            continue
        wants = _coerce_titles(sp.pop("after_titles", None))  # 生 title を task に書かない（id が正）
        # 類似は墓標も現役タスクも**止めず**に注記だけ残す（人が票で見て却下できる＝取り返しがつく）
        near = similar_tombstones(title, graves, threshold)
        similar = _similar_existing(title, merged, threshold)
        notes = []
        if near:
            notes.append("⚠ 却下済みのタスクに似ています: "
                         + " / ".join(f"「{t['title']}」（理由: {t['reason'] or '記録なし'}）"
                                      for t in near[:2]))
        if similar:
            notes.append("⚠ 既存タスクに似ています: "
                         + " / ".join(f"「{s}」" for s in similar[:2]))
        if notes:
            sp = dict(sp, note=" ⏎ ".join(
                x for x in [str(sp.get("note") or "").strip(), *notes] if x))
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
