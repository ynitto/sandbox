from __future__ import annotations
# workspace.py — 元 agent-flow.py の 2278-2556 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# ワークスペース — この run（=バックログ単位）の唯一の書込先リポジトリ。
#   worker が temp 領域へ clone し、作業ブランチ af/<run_id> を base から作って作業する。
#   変更があれば agent-flow が commit して push する（エージェントは編集のみ）。読み取り専用
#   グラフ（変更ゼロ）なら何も push しない。参照だけのリポジトリはワークスペースではなく、
#   タスク記述（goal 本文）として伝搬する（agent-project が埋め込む）。
#   リポジトリの同一性は (url, path, base) で判定する（同 URL でも path/ブランチが違えば別）。
# --------------------------------------------------------------------------
_workspace_clone: "dict[tuple, str]" = {}   # (url,path,base) -> clone パス（""=clone 失敗）
_workspace_root: "str | None" = None


def _repo_name(url: str) -> str:
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    return _safe(base) or "repo"


def parse_workspace(token: "str | None") -> "dict | None":
    """`--workspace` トークンをワークスペース spec に正規化する。素の URL でも、agent-project が
    付ける JSON（{url,path,base,target,desc,branch}）でも受ける。url が無ければ None（読み取り専用 run）。
    `branch` は任意の**明示作業ブランチ**（agent-project のタスク単位ブランチ ap/<task-id> 等）。
    指定があれば run 毎の af/<run-id> の代わりにそこへ push する＝リトライ（別 run-id）でも
    同一ブランチへ成果を積み増せる。"""
    token = (token or "").strip()
    if not token:
        return None
    spec = {"url": "", "path": "", "base": "", "target": "", "desc": ""}
    if token.startswith("{"):
        try:
            d = json.loads(token)
        except (ValueError, TypeError):
            d = None
        if isinstance(d, dict) and d.get("url"):
            for k in ("url", "path", "base", "target", "desc", "branch"):
                if d.get(k):
                    spec[k] = str(d[k]).strip()
            return spec
        return None
    spec["url"] = token                           # 素の URL（メタ無し）
    return spec


def parse_references(tokens: "list[str] | None") -> "list[dict]":
    """`--reference` トークン列を参照リポジトリ spec 列へ正規化する（読むだけ・書き込まない）。
    各トークンは素の URL でも JSON（{url,path,base,desc}）でも可。url の無いものは捨てる。"""
    out: "list[dict]" = []
    seen: "set[str]" = set()
    for tok in (tokens or []):
        spec = parse_workspace(tok)               # 同じ正規化を流用（target は参照では未使用）
        if spec and spec["url"] and spec["url"] not in seen:
            seen.add(spec["url"])
            out.append(spec)
    return out


def reference_instruction(refs: "list[dict]") -> str:
    """参照リポジトリ（読むだけ）をエージェントへ伝える指示ブロック。書込先ではないことを明示する。"""
    if not refs:
        return ""
    lines = ["【参照リポジトリ】読み取り専用。変更・commit・push はしないこと。必要に応じて内容を参照する:"]
    for s in refs:
        label = s["url"]
        tags = []
        if s.get("path"):
            tags.append(f"フォルダ {s['path']}")
        if s.get("base"):
            tags.append(f"ブランチ {s['base']}")
        line = f"  - {label}" + ("（" + "・".join(tags) + "）" if tags else "")
        if s.get("desc"):
            line += f": {s['desc']}"
        lines.append(line)
    return "\n".join(lines)


def workspace_id(spec: dict) -> tuple:
    """ワークスペースの一意キー = (url, path, base)。同 URL でも path（モノレポのフォルダ）や
    base（作業ブランチ）が違えば別ワークスペースとして扱う。"""
    return (spec.get("url", ""), spec.get("path", ""), spec.get("base", ""))


def run_branch_name(run_id: str) -> str:
    """この run の作業ブランチ名。worker が base から作り、変更を push する先。"""
    return f"af/{_safe(run_id)}"


def _clone_repo(url: str, base: str, dest: str) -> str:
    """url を dest へ clone する。base 指定があればそのブランチを checkout（無ければ既定にフォールバック）。
    成功で dest、失敗で "" を返す。一過性のネットワーク障害に備え、バスクローン／push／pull と同じ
    指数バックオフでリトライする（委譲される側＝実作業ノードが起動毎にワークスペースを clone するため、
    ここがネットワーク不安定時に「clone 失敗→タスク失敗」になりやすい）。"""
    attempts = []
    if base:
        attempts.append(["git", "clone", "-b", base, url, dest])
    attempts.append(["git", "clone", url, dest])
    detail = ""
    for i in range(CLONE_RETRIES):
        for cmd in attempts:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
                if r.returncode == 0:
                    return dest
                detail = (r.stderr or r.stdout).strip()[:300]
            except (OSError, subprocess.SubprocessError) as exc:
                detail = str(exc)[:300]
            if os.path.exists(dest):              # 失敗の残骸を消してからフォールバック／再試行
                shutil.rmtree(dest, ignore_errors=True)
        if i < CLONE_RETRIES - 1:
            backoff_sleep(2 ** i if i < 4 else 16)   # バックオフして再試行
    raise RuntimeError(f"workspace clone に失敗しました: {detail or url}")


def _ws_git(clone: str, *args: str):
    """clone 内で git を実行（capture, check しない）。"""
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def _prepare_run_branch(clone: str, branch: str, base: str) -> None:
    """作業ツリーを run の作業起点に整える（commit 用の identity を保証する）。
    worktree は detached のまま・direct clone フォールバックは現在の HEAD（base/既定）から作業し、
    実際の作業ブランチは finalize_workspace が push 時に `HEAD:refs/heads/<branch>` で作る。
    ブランチを checkout しないので「同一ブランチを2つの worktree で同時 checkout 不可」制約を受けない。
    既存の run ブランチへの追従は provision 時に refs 優先順 [branch, base] で起点に反映済み。"""
    if not _ws_git(clone, "config", "user.email").stdout.strip():
        _ws_git(clone, "config", "user.email", "agent-flow@local")
        _ws_git(clone, "config", "user.name", "agent-flow")


def ensure_workspace_clone(spec: "dict | None", run_id: str) -> "dict | None":
    """run のワークスペースを worker 専用 temp へ clone し、作業ブランチを用意する。
    ブランチは spec の明示 `branch`（agent-project のタスク単位ブランチ等）＞ run 毎の af/<run_id>。
    (url,path,base) 単位でプロセス内キャッシュ。spec が無ければ None（読み取り専用 run）。
    返り値は spec に clone 先パス（clone="" は失敗）と branch を足した dict。"""
    global _workspace_root
    if not spec or not spec.get("url"):
        return None
    branch = str(spec.get("branch") or "").strip() or run_branch_name(run_id)
    key = workspace_id(spec)
    if key in _workspace_clone:
        return {**spec, "clone": _workspace_clone[key], "branch": branch}
    if _workspace_root is None:
        # pid を名に埋める → SIGKILL 等で残った孤立 clone を janitor が安全に回収できる。
        _workspace_root = tempfile.mkdtemp(prefix=f"agent-flow-ws-{os.getpid()}-")
    stem = _repo_name(spec["url"])
    dest = os.path.join(_workspace_root, stem)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(_workspace_root, f"{stem}-{n}")
        n += 1
    base = spec.get("base") or ""
    # 作業起点の優先順: 既存の run ブランチ → base → 既定（detached worktree で作り、push 時に作業ブランチ化）。
    # local（手元の同じリポジトリのクローン）があれば、そこから worktree を切る。目の前に同じ
    # リポジトリがあるのに毎回ネットワーク越しにミラーを取り直すのは無駄で、オフラインでも
    # 動かない。local の作業ツリー・index には触らない（別 worktree なので）。
    #
    # spec に載っていなければ **このノードの host.yaml `repos[]` から解決する**（S3）。
    # 依頼元（agent-project / 板）が載せてこない経路——板の公示・古い形の run——でも、
    # 手元にクローンがあるノードではそれを使えるようにする。
    local = str((_repolocal.merge_local(spec) or spec).get("local") or "")
    path = provision_tree(spec["url"], [branch, base], dest, local=local)
    _prepare_run_branch(path, branch, base)
    _workspace_clone[key] = path
    return {**spec, "clone": path, "branch": branch}


def sync_workspace_base(ws: "dict | None") -> dict:
    """明示作業ブランチへ最新 target を通常 merge する。競合は worktree に残して返す。"""
    if not ws or not ws.get("clone") or not ws.get("target"):
        return {"status": "noop", "conflict_files": []}
    clone, target = str(ws["clone"]), str(ws["target"])
    fetched = _ws_git(clone, "fetch", "--quiet", "origin", target)
    if fetched.returncode != 0:
        detail = (fetched.stderr or fetched.stdout).strip()[:300]
        triage = classify_agent_failure(detail)
        failure_class = triage[0] if triage else "transient"
        raise RuntimeError(f"[agent-error:{failure_class}] target {target} の fetch に失敗しました: "
                           f"{detail}")
    target_rev = _ws_git(clone, "rev-parse", "FETCH_HEAD").stdout.strip()
    if not target_rev:
        raise RuntimeError(f"target {target} の revision を解決できません")
    ws["target_rev"] = target_rev
    if _ws_git(clone, "merge-base", "--is-ancestor", target_rev, "HEAD").returncode == 0:
        return {"status": "noop", "target": target, "target_rev": target_rev,
                "conflict_files": []}
    merged = _ws_git(clone, "merge", "--no-commit", "--no-ff", target_rev)
    conflicts = [p for p in _ws_git(
        clone, "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if p]
    if conflicts:
        return {"status": "conflict", "target": target, "target_rev": target_rev,
                "conflict_files": sorted(conflicts)}
    if merged.returncode != 0:
        raise RuntimeError(f"target {target} の merge に失敗しました: "
                           f"{(merged.stderr or merged.stdout).strip()[:300]}")
    return {"status": "merged", "target": target, "target_rev": target_rev,
            "conflict_files": []}


def inject_base_sync(nodes: dict, workspace: "dict | None") -> "dict | None":
    """書込ブランチと target が異なる graph の root 前へ system node を差し込む。"""
    if not workspace:
        return None
    branch, target = str(workspace.get("branch") or ""), str(workspace.get("target") or "")
    if not workspace.get("url") or not branch or not target or branch == target:
        return None
    nid = "base-sync"
    if nid in nodes:
        return None
    roots = [node for node in nodes.values() if not node.get("deps")]
    task = {"id": nid, "kind": "base-sync", "deps": [],
            "goal": f"最新 {target} を {branch} へ統合し、競合があれば解消する"}
    nodes[nid] = _node_entry(task)
    for node in roots:
        node["deps"] = [nid]
    return task


# `git diff --cached --check` の指摘のうち、機械的に直せるもの（他は触らない）。
_WS_CHECK_RE = re.compile(
    r"^(?P<path>[^\"].*?):(?P<line>\d+): "
    r"(?P<msg>trailing whitespace|space before tab in indent|new blank line at EOF)\.$")


def _strip_line_ws(line: str) -> str:
    """行末の空白（CR を含む）を落とし、インデント内の「タブ直前のスペース」を畳む。

    CR を残しても git は行末空白として弾き続けるので、直す行は LF へ寄せる
    （コード・Markdown は LF に揃える運用の既定。CRLF が要るファイルは人が直す）。"""
    body = line.rstrip(" \t\r")
    indent = len(body) - len(body.lstrip(" \t"))
    head, rest = body[:indent], body[indent:]
    while " \t" in head:
        head = head.replace(" \t", "\t")
    return head + rest


def _fix_staged_whitespace(clone: str) -> "list[str]":
    """staged 差分の空白指摘を、git が指した行だけ直す。直したファイルのパスを返す。

    行末空白・EOF の空行は成果の欠陥ではなく体裁の揺れで、エージェントに直させるより
    その場で直す方が安い（小型モデルほど再発し、実装が commit 直前に丸ごと捨てられる）。
    ファイル全体を整形しないのは、ノードが触っていない行を差分へ巻き込まないため
    ＝ worker の変更範囲とレビュー範囲を一致させたままにする。直せない指摘（設定依存の
    tab-in-indent 等）は残し、呼び出し側の再検査で従来どおり失敗させる。"""
    report = _ws_git(clone, "diff", "--cached", "--check")
    if report.returncode == 0:
        return []
    eol_lines: "dict[str, set[int]]" = {}   # 行単位で直す指摘（行末空白・space before tab）
    eof_files: "set[str]" = set()           # 末尾の空行を落とすファイル
    for raw in (report.stdout or "").splitlines():
        m = _WS_CHECK_RE.match(raw)
        if not m:
            continue
        if m["msg"] == "new blank line at EOF":
            eof_files.add(m["path"])
        else:
            eol_lines.setdefault(m["path"], set()).add(int(m["line"]))
    fixed: "list[str]" = []
    for path in sorted(set(eol_lines) | eof_files):
        full = os.path.join(clone, path)
        try:
            with open(full, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                before = fh.read()
        except OSError:
            continue                        # 消えた/読めないものは検査側の失敗に委ねる
        parts = before.split("\n")
        for no in eol_lines.get(path, ()):
            if 1 <= no <= len(parts):
                parts[no - 1] = _strip_line_ws(parts[no - 1])
        if path in eof_files:
            # 末尾が改行で終わる（parts[-1] == ""）ときだけ、その手前の空行を畳む。
            while len(parts) >= 2 and parts[-1] == "" and parts[-2] in ("", "\r"):
                parts.pop(-2)
        after = "\n".join(parts)
        if after == before:
            continue
        with open(full, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.write(after)
        fixed.append(path)
    return fixed


def finalize_workspace(ws: "dict | None", run_id: str, node_id: str) -> "dict | None":
    """エージェント実行後、ワークスペースに変更があれば作業ブランチへ commit し push する
    （rebase リトライで分散ワーカーの push を統合）。変更が無ければ何もしない＝読み取り専用
    グラフ（調査タスク等）ではブランチを push しない。返り値: 反映したデリバリ dict か None。"""
    if not ws:
        return None
    clone, branch = ws.get("clone"), ws.get("branch")
    if not clone or not os.path.isdir(clone):
        return None
    _ws_git(clone, "add", "-A")
    unmerged = _ws_git(clone, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    if unmerged:
        raise RuntimeError(f"未解決の競合があります: {unmerged[:300]}")
    staged = [p for p in _ws_git(clone, "diff", "--cached", "--name-only",
                                 "--diff-filter=ACMR").stdout.splitlines() if p]
    if node_id != "base-sync":
        autofixed = _fix_staged_whitespace(clone)
        if autofixed:
            _ws_git(clone, "add", "--", *autofixed)
        checked = _ws_git(clone, "diff", "--cached", "--check")
        if checked.returncode != 0:
            raise RuntimeError(f"差分品質チェックに失敗しました: "
                               f"{(checked.stdout or checked.stderr).strip()[:300]}")
    if staged:
        checked = _ws_git(clone, "grep", "--cached", "-n", "-E",
                          r"^(<<<<<<< |=======$|>>>>>>> )", "--", *staged)
        if checked.returncode == 0:
            raise RuntimeError(f"競合マーカーが残っています: {checked.stdout.strip()[:300]}")
        if checked.returncode != 1:
            raise RuntimeError(f"競合マーカーを検査できません: "
                               f"{(checked.stderr or checked.stdout).strip()[:300]}")
    merging = _ws_git(clone, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0
    if not merging and _ws_git(clone, "diff", "--cached", "--quiet").returncode == 0:
        return None                               # 変更なし → commit/push しない
    c = _ws_git(clone, "commit", "-m", f"[agent-flow] {node_id} ({run_id})")
    if c.returncode != 0:
        # commit 失敗（hook・identity 未設定・index.lock 等）を無視して push すると、
        # エージェントの編集を含まない古い HEAD が push され「変更が入ったつもりの
        # delivery」で done になる（サイレントなデータ喪失）。ここで明示的に失敗させる。
        raise RuntimeError(f"workspace commit が失敗しました: {(c.stderr or c.stdout).strip()[:300]}")
    target_rev = str(ws.get("target_rev") or "")
    last_push = None
    for i in range(5):
        if target_rev and _ws_git(
                clone, "merge-base", "--is-ancestor", target_rev, "HEAD").returncode != 0:
            raise RuntimeError("merge commit に検証対象 target が含まれていません")
        # detached HEAD のまま作業ブランチへ push（ローカルでブランチを checkout しない）。
        last_push = _ws_git(clone, "push", "origin", f"HEAD:refs/heads/{branch}")
        if last_push.returncode == 0:
            head = _ws_git(clone, "rev-parse", "HEAD").stdout.strip()
            return {"url": ws.get("url"), "branch": branch, "commit": head,
                    "target": ws.get("target") or ws.get("base") or "", "path": ws.get("path") or ""}
        # reject → リモートの branch を FETCH_HEAD に取り込み（共有 cache の ref は書き換えない）、
        # detached のまま rebase して再 push。分散ワーカーの push を統合する。
        _ws_git(clone, "fetch", "--quiet", "origin", branch)
        rb = _ws_git(clone, "rebase", "FETCH_HEAD")
        if rb.returncode != 0:
            # コンフリクトした rebase を放置したまま push を繰り返しても解消しない上、
            # 部分適用状態のツリーが後続の finalize を汚す。中断して失敗を伝える。
            _ws_git(clone, "rebase", "--abort")
            raise RuntimeError(
                f"workspace rebase が競合しました（{branch}）: {(rb.stderr or rb.stdout).strip()[:300]}")
        backoff_sleep(2 ** i if i < 4 else 16)
    detail = ((last_push.stderr or last_push.stdout).strip()[:300]
              if last_push is not None else "")
    raise RuntimeError(f"workspace push が {branch} へ反映できませんでした: {detail}")


def cleanup_workspace() -> None:
    """worker の作業ツリー（temp の worktree／フォールバック clone）を丸ごと削除する（作業後クリーンは必須）。
    共有 cache 本体は残し、worktree 登録だけ prune して回収する。"""
    global _workspace_root
    cleanup_local_worktrees()   # 手元のクローンに残した worktree 登録を先に外す（消す前に）
    if _workspace_root and os.path.isdir(_workspace_root):
        shutil.rmtree(_workspace_root, ignore_errors=True)
    _workspace_root = None
    _workspace_clone.clear()
    _prune_caches(_provisioned_urls)
    _provisioned_urls.clear()


def workspace_instruction(ws: "dict | None") -> str:
    """唯一の書込先ワークスペースをエージェントに伝える決定的な指示ブロック。
    clone 先・対象フォルダ(path)・作業ブランチ(base→target)・役割(desc) を示し、編集だけ行わせる
    （commit/push は agent-flow が行う）。この指示は call_executor 経由で executor へ goal とは別引数
    （repo_instruction）として渡る（gitlab executor は起票先の解決とイシュー本文に使う）。"""
    if not ws:
        return ""
    if not ws.get("clone"):
        return f"【ワークスペース】clone に失敗しました（{ws.get('url') or ''}）。書き込みはできません。"
    lines = [f"【ワークスペース】このタスクの唯一の書込先リポジトリ（clone 済み）: {ws.get('url')}",
             f"  作業ディレクトリ: {ws['clone']}"]
    if ws.get("path"):
        lines.append(f"  変更してよいのは {ws['path']} 配下のみ（他フォルダは触らないこと）")
    br = f"  作業ブランチ: {ws.get('branch')}"
    if ws.get("base"):
        br += f"（{ws['base']} から分岐"
        if ws.get("target") and ws["target"] != ws["base"]:
            br += f"・最終的な MR/PR ターゲット = {ws['target']}"
        br += "）"
    lines.append(br)
    if ws.get("desc"):
        lines.append(f"  役割: {ws['desc']}")
    lines.append("  作業ツリー内のファイルを編集すること。commit と push は agent-flow が自動で行うので、"
                 "あなたは commit/push やブランチ切替をしないこと。変更が不要（調査のみ）なら何も書き換えない。")
    return "\n".join(lines)


def artifact_instruction(self_dir: "str | None", dep_arts: "dict[str, str] | None") -> str:
    """中間成果物（ファイル）の受け渡しプロトコルをエージェントへ伝える指示ブロック。

    output/data に乗らない大きな成果物は決定的なディレクトリでファイル参照する。
    - 自ノードの出力先（self_dir）に書き出すと後続タスクが同じパスで発見できる。
    - 依存タスクの成果物（dep_arts）は、その内容を本文に貼らずパスを示し、
      エージェントにファイルとして読ませる（コマンドライン長制限を避ける狙いも兼ねる）。"""
    if not self_dir and not dep_arts:
        return ""
    lines = ["【中間成果物プロトコル】タスク間の大きな成果物はファイルで受け渡します。"]
    if self_dir:
        lines.append("  - 出力先: 生成ファイル・大きな中間成果物は必ず次のディレクトリに書き出すこと"
                     f"（後続タスクがこのパスで参照します）: {self_dir}")
    have = {d: p for d, p in (dep_arts or {}).items()
            if p and os.path.isdir(p) and os.listdir(p)}
    if have:
        lines.append("  - 依存タスクの成果物（本文には貼りません。次のパス内のファイルを読んで利用すること）:")
        for d, p in have.items():
            files = sorted(os.listdir(p))
            more = " …" if len(files) > 10 else ""
            lines.append(f"    [{d}] {p} （{', '.join(files[:10])}{more}）")
    return "\n".join(lines)


def repair_instruction(repair: "dict | None") -> str:
    """差分修復リトライ（案 B-1・オプトイン）のブリーフを worker への指示ブロックへ描画する。
    `repair` は work.py の `repair_brief()` が実行直前にバスから決定的に組み立てた辞書
    （オプトインでない・対象外・前回結果なしなら None）。全体を作り直させず、指摘箇所の
    修復だけを促す——materials（前回出力・成果物・verify の指摘）はここでは決めない
    （repair_brief の責務）、ここは描画だけを行う。"""
    if not repair:
        return ""
    lines = [f"【前回の試行と差し戻し】このタスクは前回 {repair.get('of')} として実行され、"
             "指摘を受けて差し戻されました。"]
    issues = repair.get("issues") or []
    if issues:
        lines.append("  指摘:")
        lines.extend(f"    - {i}" for i in issues)
    output = repair.get("output")
    if output:
        lines.append(f"  前回の成果（抜粋）: {output}")
    art_dir = repair.get("artifact_dir")
    if art_dir:
        lines.append(f"  前回の成果物: {art_dir} （このディレクトリのファイルを読むこと）")
    if repair.get("delivered"):
        lines.append("  前回の変更はすでに作業ブランチへ反映されています。作業ツリーの現状が前回の結果です。")
    lines.append("  全体を作り直さず、指摘された箇所だけを直してください。前回正しかった部分は保持すること。")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Heartbeat — 長時間タスク実行中に claim の lease を更新し続ける
# --------------------------------------------------------------------------
class Heartbeat(threading.Thread):
    """実行中のワーカーが claim を握り続けるための心拍。

    lease の 1/3 間隔で claims/<node>/<who>.json の lease_until を延長し push する。
    これがないと、実行が lease を超えた瞬間に他ノードへ再 claim され二重実行になりうる。"""

    def __init__(self, bus: Bus, node_id: str, who: str, lease: float):
        super().__init__(daemon=True)
        self.bus, self.node_id, self.who, self.lease = bus, node_id, who, lease
        self._stopped = threading.Event()
        self.lost = threading.Event()   # claim を失った（他者が勝者）ことの検知

    def run(self) -> None:
        interval = max(2.0, self.lease / 3.0)
        while not self._stopped.wait(interval):
            try:
                # lease_until だけを延長する（ts の振り直し・claim の書き戻しはしない）。
                # 失効中に他者が claim していたら延長を止めて喪失を記録する——
                # ここで無条件に claim を書き戻すと両者が走り続けて二重実行になる。
                if not self.bus.extend_claim(self.node_id, self.who, self.lease):
                    self.lost.set()
                    return
                self.bus.sync_push(f"heartbeat {self.node_id} by {self.who}")
            except Exception:  # noqa: BLE001 — 心拍失敗は実行を止めない
                pass

    def stop(self) -> None:
        self._stopped.set()
        self.join(timeout=5)
