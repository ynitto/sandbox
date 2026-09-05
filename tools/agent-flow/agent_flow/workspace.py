from __future__ import annotations
# workspace.py — 元 agent-flow.py の 2278-2556 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# ワークスペース / workset — この run（=バックログ単位）の書込先リポジトリの集合。
#   worker が temp 領域へ clone し、作業ブランチ af/<run_id> を base から作って作業する。
#   変更があれば agent-flow が commit して push する（エージェントは編集のみ）。読み取り専用
#   グラフ（変更ゼロ）なら何も push しない。参照だけのリポジトリはワークスペースではなく、
#   タスク記述（goal 本文）として伝搬する（agent-project が埋め込む）。
#   リポジトリの同一性は (url, path, base) で判定する（同 URL でも path/ブランチが違えば別）。
#
#   **workset**（設計: docs/plans/2026-09-05-agent-flow-multi-workspace-design.md §5）:
#   run が書き込んでよいリポジトリの順序付き集合。先頭要素を primary と呼ぶ。要素ごとに
#   同じ規律（作業ブランチ・commit/push・publication・復旧 ref・base-sync・CI）を適用する。
#   **1 要素のときは従来の単一 workspace と挙動が一致する**（`workspace` / `delivery` /
#   `publication` の形と意味は変えない）。複数要素のときだけ `workspaces[]` / `deliveries[]`
#   が現れる。書込先の集合を決めるのは依頼側（agent-project / dashboard / 板）で、
#   agent-flow の planner は集合を増減しない（ノードは既定で repo を知らない＝repo-blind）。
# --------------------------------------------------------------------------
# clone は (url, base, branch) 単位で共有する——同じリポジトリの同じ起点を、path（モノレポの
# 作業フォルダ）が違うというだけで 2 回取り直さないため（§5.1「同 url・同 base・別 path は
# 1 clone を共有し、path の和集合を変更許可範囲にする」）。
_workspace_clone: "dict[tuple, str]" = {}   # (url,base,branch) -> clone パス（""=clone 失敗）
_workspace_root: "str | None" = None
# push 拒否のうち「リモートが進んでいた」＝ fetch + rebase で解ける理由のマーカー。
# これ以外（認証・権限・保護ブランチ・ネットワーク）は rebase しても解けないので即座に上げる。
_PUSH_STALE_MARKERS = ("non-fast-forward", "fetch first", "stale info",
                       "Updates were rejected")


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
    spec = {"url": "", "local": "", "path": "", "base": "", "target": "", "desc": ""}
    if token.startswith("{"):
        try:
            d = json.loads(token)
        except (ValueError, TypeError):
            d = None
        if isinstance(d, dict) and d.get("url"):
            # `name` は repos レジストリのエントリ名。ノード内・記録内の repo 参照は常に
            # この名前を使う（codd-gate の `repo名:相対パス` と同じ語彙）。省略時は url から導く。
            for k in ("name", "url", "local", "path", "base", "target", "desc", "branch"):
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


def parse_workset(tokens, primary=None) -> "list[dict]":
    """`--workspace`（繰り返し可）のトークン列を workset へ正規化する。

    1 トークンが JSON 配列でも受ける（投函 UI が 1 引数で集合を渡せる形）。`primary` は
    旧形の単一 `--workspace` 値で、トークン列が空のときの補完に使う。url を持たない
    トークンは捨てる（読み取り専用 run では空リストになる）。"""
    raw: "list" = []
    if isinstance(tokens, (str, bytes)):
        tokens = [tokens]
    for tok in (tokens or []):
        text = str(tok or "").strip()
        if text.startswith("["):
            try:
                arr = json.loads(text)
            except (ValueError, TypeError):
                arr = None
            if isinstance(arr, list):
                raw.extend(json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
                           for x in arr)
                continue
        raw.append(text)
    if not raw and primary:
        raw = [primary]
    return normalize_workset([parse_workspace(tok) for tok in raw])


def normalize_workset(specs) -> "list[dict]":
    """workspace spec 列 → workset（順序付き集合）。先頭が primary。

    - url の無い要素は落とす。同一性 (url, path, base) が重なる要素は初出だけ残す。
    - `name` が無ければ url から導き（`_repo_name`）、重複したら -2, -3 … を付けて一意にする
      ——name は記録・指示・検証計画が要素を指す唯一の鍵なので、重複させない。

    **1 要素で名前の明示も無い workset には `name` を足さない。** 従来の単一 workspace の
    run が持ち回る spec（delivery / publication へそのまま写る）に、この変更でキーが 1 つ
    増えるのを避けるため——N=1 では形も意味も変えない（§5.1 不変条件 3）。"""
    kept: "list[dict]" = []
    seen: "set[tuple]" = set()
    for spec in (specs or []):
        if not isinstance(spec, dict) or not str(spec.get("url") or "").strip():
            continue
        key = workspace_id(spec)
        if key in seen:
            continue
        seen.add(key)
        kept.append(spec)
    out: "list[dict]" = []
    names: "set[str]" = set()
    for spec in kept:
        explicit = str(spec.get("name") or "").strip()
        if not explicit and len(kept) <= 1:
            out.append(dict(spec))
            continue
        name = explicit or _repo_name(str(spec["url"]))
        if name in names:
            n = 2
            while f"{name}-{n}" in names:
                n += 1
            name = f"{name}-{n}"
        names.add(name)
        out.append({**spec, "name": name})
    return out


def workset_errors(workset: "list[dict]") -> "list[str]":
    """workset の決定的検査。空リスト＝受理してよい（fail-close の判定材料）。

    同じ url の要素は base が等しくなければならない——要素ごとの作業ブランチは同名
    `af/<run-id>` なので、同 url・別 base のまま作ると 1 本のブランチの起点が矛盾する
    （どちらの base から分岐したのか決まらない）。明示 `branch` で分ける経路だけ許す（§5.1）。"""
    errs: "list[str]" = []
    bases: "dict[str, tuple]" = {}
    for e in (workset or []):
        url = str(e.get("url") or "")
        branch = str(e.get("branch") or "")
        base = str(e.get("base") or "")
        prev = bases.get(url)
        if prev is None:
            bases[url] = (base, branch)
            continue
        prev_base, prev_branch = prev
        if prev_base != base and prev_branch == branch:
            errs.append(f"同じリポジトリ {url} に別の base（{prev_base or '既定'} と "
                        f"{base or '既定'}）が指定されています。同名の作業ブランチを作れません")
    return errs


def workset_primary(workset) -> "dict | None":
    """workset の primary（先頭要素）。空なら None（読み取り専用 run）。"""
    for e in (workset or []):
        if isinstance(e, dict) and e.get("url"):
            return e
    return None


def workset_names(workset) -> "list[str]":
    """workset の要素名（順序どおり）。指示・記録・検証計画が要素を指す語彙。"""
    return [str(e.get("name") or "") for e in (workset or []) if isinstance(e, dict)]


def drop_workset_references(references, workset) -> "list[dict]":
    """references から workset と重なる url を落とす（§5.1「workset が勝つ」）。

    同じリポジトリに「読むだけ」と「書込先」の両方の注記が出ると、エージェントに矛盾した
    指示を渡すことになる。書込先の方が強い契約なので参照側を落とす。"""
    urls = {str(e.get("url") or "") for e in (workset or []) if isinstance(e, dict)}
    return [r for r in (references or [])
            if isinstance(r, dict) and str(r.get("url") or "") not in urls]


def workspace_id(spec: dict) -> tuple:
    """ワークスペースの一意キー = (url, path, base)。同 URL でも path（モノレポのフォルダ）や
    base（作業ブランチ）が違えば別ワークスペースとして扱う。"""
    return (spec.get("url", ""), spec.get("path", ""), spec.get("base", ""))


def run_branch_name(run_id: str) -> str:
    """この run の作業ブランチ名。worker が base から作り、変更を push する先。"""
    return f"af/{_safe(run_id)}"


def recovery_ref_name(run_id: str) -> str:
    """remote 公開に失敗しても手元から再 push できる hidden ref。"""
    return f"refs/agent-flow/recovery/{_safe(run_id)}"


def _save_recovery_ref(ws: dict, run_id: str) -> "str | None":
    """現在の workspace HEAD をローカル元リポジトリへ保存する。

    local が無い remote-only run は従来どおり remote push だけを行う。Dashboard 起点の run は
    local を必ず渡すため、公開失敗時も一時 worktree の掃除で commit が消えない。
    """
    local = str(ws.get("local") or "").strip()
    if not local:
        return None
    clone = str(ws.get("clone") or "")
    ref = recovery_ref_name(run_id)
    head = _ws_git(clone, "rev-parse", "HEAD").stdout.strip()
    saved = _ws_git(local, "update-ref", ref, head)
    if saved.returncode != 0:
        raise RuntimeError(
            f"workspace recovery ref を保存できませんでした: "
            f"{(saved.stderr or saved.stdout).strip()[:300]}")
    return ref


def _delete_recovery_ref(ws: dict, run_id: str) -> None:
    """remote 公開済みの recovery ref を後始末する（失敗時は GC に委ねる）。"""
    local = str(ws.get("local") or "").strip()
    if local:
        _ws_git(local, "update-ref", "-d", recovery_ref_name(run_id))


class WorkspacePublishError(RuntimeError):
    """commit は保存済みだが remote branch へ公開できなかった失敗。"""

    def __init__(self, message: str, publication: dict):
        super().__init__(message)
        self.data = {"error_class": "workspace_publish", "publication": publication}


def _workspace_publish_error(ws: dict, run_id: str, branch: str, detail: str) -> WorkspacePublishError:
    clone = str(ws.get("clone") or "")
    head = _ws_git(clone, "rev-parse", "HEAD").stdout.strip()
    message = f"workspace push が {branch} へ反映できませんでした: {detail.strip()[:300]}"
    publication = {"state": "failed", "url": ws.get("url"), "branch": branch,
                   "commit": head, "attempted_at": now_iso(), "error": detail.strip()[:300]}
    if ws.get("name"):
        publication["name"] = str(ws["name"])   # どの workset 要素が公開できなかったか
    local = str(ws.get("local") or "").strip()
    if local:
        publication["recovery"] = {
            "repository": local,
            "ref": recovery_ref_name(run_id),
        }
    return WorkspacePublishError(message, publication)


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
    """clone 内で git を実行（capture, check しない）。

    **資格情報を対話で聞かせない・無期限に待たせない。** worker には答えられる人が居ないので、
    聞かれた git は永久に待つ——実際 `push origin HEAD:refs/heads/ap/...` が `/dev/tty` で
    プロンプトを出したまま 5 時間動かず、心拍が claim を延長し続けるので他ノードへも回らず、
    run 全体が静かに停止した。護りは agentcore の transport（GitBus が使っているもの）と
    同じ 1 実装を使う——ネットワークを触るサブコマンドだけ上限を伸ばし、超えたら失敗として
    返す（例外で貫通させず、returncode を見る既存の呼び出しをそのまま動かす）。"""
    cmd = ["git", "-C", clone, *args]
    limit = _transport.git_timeout_for(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=limit,
                              env=_transport.harden_git_env(dict(os.environ)))
    except subprocess.TimeoutExpired:
        return _transport.timed_out_result(cmd, limit)


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
    """run のワークスペース 1 要素を worker 専用 temp へ clone し、作業ブランチを用意する。
    ブランチは spec の明示 `branch`（agent-project のタスク単位ブランチ等）＞ run 毎の af/<run_id>。
    (url,base,branch) 単位でプロセス内キャッシュ（同じ起点は path が違っても 1 clone を共有）。
    spec が無ければ None（読み取り専用 run）。
    返り値は spec に clone 先パス（clone="" は失敗）と branch を足した dict。"""
    global _workspace_root
    if not spec or not spec.get("url"):
        return None
    branch = str(spec.get("branch") or "").strip() or run_branch_name(run_id)
    key = (str(spec.get("url") or ""), str(spec.get("base") or ""), branch)
    if key in _workspace_clone:
        return {**spec, "clone": _workspace_clone[key], "branch": branch}
    if _workspace_root is None:
        # pid を名に埋める → SIGKILL 等で残った孤立 clone を janitor が安全に回収できる。
        _workspace_root = tempfile.mkdtemp(prefix=f"agent-flow-ws-{os.getpid()}-")
    stem = str(spec.get("name") or "").strip() or _repo_name(spec["url"])
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


def ensure_workset(workset, run_id: str) -> "list[dict]":
    """workset の全要素を用意する（要素ごとに clone と作業ブランチ）。

    要素ごとに同じ規律を適用するのがこの設計の骨で、ここが「集合に対して 1 回だけ回す」
    唯一の場所。作業ブランチは要素ごとに同名 `af/<run-id>`（明示 `branch` があればそれ）
    ——横断 MR を相関させる鍵にもなる。1 要素なら `ensure_workspace_clone` を 1 回呼ぶのと
    同じ結果になる。1 要素でも clone に失敗すれば例外を上げる（半端な workset で
    エージェントを走らせない）。"""
    out: "list[dict]" = []
    for spec in (workset or []):
        ready = ensure_workspace_clone(spec, run_id)
        if ready:
            out.append(ready)
    return out


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


def base_sync_node_id(workset, spec: dict) -> str:
    """要素の base-sync ノード id。1 要素なら従来どおり固定 `base-sync`。

    複数要素では要素ごとに 1 ノード要る（§5.4）。設計の表記は `base-sync:<name>` だが、
    ノード id は `tasks/<id>.json` と `claims/<id>/` のパスになるため **`:` は使わない**
    ——Windows のファイル名として不正で、全 PC へ配る run 状態がそのノードでだけ壊れる。
    同じ意味で衝突しない区切りとして `@` を使う（`base-sync-<n>`＝verify 再統合ノードとも
    ぶつからない）。"""
    if len(workset or []) <= 1:
        return "base-sync"
    name = str(spec.get("name") or "") or _repo_name(str(spec.get("url") or ""))
    return f"base-sync@{_safe(name)}"


def inject_base_sync(nodes: dict, workspace: "dict | None") -> "dict | None":
    """書込ブランチと target が異なる graph の root 前へ system node を差し込む。"""
    injected = inject_base_syncs(nodes, [workspace] if workspace else [])
    return injected[0] if injected else None


def inject_base_syncs(nodes: dict, workset) -> "list[dict]":
    """workset の要素ごとに base-sync ノードを差し込む（branch != target の要素だけ）。

    root は**全ての** base-sync に依存する——1 つでも target を取り込まないまま作業を
    始めると、その repo だけ古い起点の上で検証することになる。"""
    pending: "list[dict]" = []
    for spec in (workset or []):
        if not isinstance(spec, dict):
            continue
        branch, target = str(spec.get("branch") or ""), str(spec.get("target") or "")
        if not spec.get("url") or not branch or not target or branch == target:
            continue
        nid = base_sync_node_id(workset, spec)
        if nid in nodes:
            continue
        label = str(spec.get("name") or "")
        where = f"（{label}）" if len(workset or []) > 1 and label else ""
        pending.append({"id": nid, "kind": "base-sync", "deps": [], "workspaces": [label]
                        if len(workset or []) > 1 and label else [],
                        "goal": f"最新 {target} を {branch} へ統合し、競合があれば解消する{where}"})
    if not pending:
        return []
    roots = [node for node in nodes.values() if not node.get("deps")]
    for task in pending:
        if not task["workspaces"]:
            task.pop("workspaces")
        nodes[task["id"]] = _node_entry(task)
    ids = [t["id"] for t in pending]
    for node in roots:
        node["deps"] = list(ids)
    return pending


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


def _scope_violations(staged: "list[str]", allowed) -> "list[str]":
    """staged パスのうち、許可された path 接頭辞の外にあるものを返す。

    `allowed` に空文字（＝リポジトリ全体）が 1 つでもあれば制限なし。複数 repo を同時に
    開くとエージェントが隣の repo / 隣のフォルダを触る余地が増えるので、指示だけでなく
    ここで機械的に止める（§5.3・§9）。"""
    prefixes = [str(a or "").strip().strip("/") for a in (allowed or [])]
    if not prefixes or any(not pfx for pfx in prefixes):
        return []
    out = []
    for path in staged:
        norm = str(path).replace("\\", "/").strip("/")
        if not any(norm == pfx or norm.startswith(pfx + "/") for pfx in prefixes):
            out.append(path)
    return out


def finalize_workspace(ws: "dict | None", run_id: str, node_id: str,
                       kind: str = "", allowed_paths=None) -> "dict | None":
    """エージェント実行後、ワークスペースに変更があれば作業ブランチへ commit し push する
    （rebase リトライで分散ワーカーの push を統合）。変更が無ければ何もしない＝読み取り専用
    グラフ（調査タスク等）ではブランチを push しない。返り値: 反映したデリバリ dict か None。

    `kind` はノード種別。base-sync（target の取り込み）は差分品質チェックの対象外——
    取り込んだ他人の commit の体裁をこのノードの責任にしない。旧来は固定 id `base-sync`
    との文字列比較だったが、要素ごとに id が変わるので kind 判定へ寄せた（§5.4）。
    `allowed_paths` は変更を許す path 接頭辞（workset で共有 clone のときは和集合）。"""
    if not ws:
        return None
    clone, branch = ws.get("clone"), ws.get("branch")
    if not clone or not os.path.isdir(clone):
        return None
    is_base_sync = (kind == "base-sync") if kind else (node_id == "base-sync")
    _ws_git(clone, "add", "-A")
    unmerged = _ws_git(clone, "diff", "--name-only", "--diff-filter=U").stdout.strip()
    if unmerged:
        raise RuntimeError(f"未解決の競合があります: {unmerged[:300]}")
    staged = [p for p in _ws_git(clone, "diff", "--cached", "--name-only",
                                 "--diff-filter=ACMR").stdout.splitlines() if p]
    outside = _scope_violations(staged, allowed_paths) if not is_base_sync else []
    if outside:
        label = str(ws.get("name") or ws.get("url") or "")
        raise RuntimeError(
            f"変更が許可された範囲の外にあります（{label}）: {', '.join(sorted(outside)[:10])}")
    if not is_base_sync:
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
    _save_recovery_ref(ws, run_id)
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
            _delete_recovery_ref(ws, run_id)
            named = {"name": str(ws["name"])} if ws.get("name") else {}
            return {**named, "url": ws.get("url"), "branch": branch, "commit": head,
                    "target": ws.get("target") or ws.get("base") or "", "path": ws.get("path") or "",
                    "publication": {**named, "state": "published", "url": ws.get("url"),
                                    "branch": branch, "commit": head,
                                    "attempted_at": now_iso()}}
        # 失敗の理由を見分ける。rebase で解けるのは「リモートが進んでいた」だけで、認証切れ・
        # 権限不足・保護ブランチ・ネットワーク断は何度 rebase しても解けない。見分けずに
        # rebase へ倒すと、押せなかった本当の理由（例: could not read Username）が捨てられ、
        # ログには「rebase が競合しました: invalid upstream 'FETCH_HEAD'」だけが残る
        # ——リモートに無いブランチを fetch して FETCH_HEAD が書かれないためで、実際に
        # 認証切れの調査を丸ごと誤らせた。英語判定でよいのは harden_git_env が LC_ALL=C を
        # 固定しているため。
        push_detail = (last_push.stderr or last_push.stdout or "")
        if not any(m in push_detail for m in _PUSH_STALE_MARKERS):
            raise _workspace_publish_error(ws, run_id, branch, push_detail)
        # リモートの branch を FETCH_HEAD に取り込み（共有 cache の ref は書き換えない）、
        # detached のまま rebase して再 push。分散ワーカーの push を統合する。
        fetched = _ws_git(clone, "fetch", "--quiet", "origin", branch)
        if fetched.returncode != 0:
            detail = (f"{push_detail.strip()[:200]} / 統合のための fetch も失敗: "
                      f"{(fetched.stderr or fetched.stdout).strip()[:150]}")
            raise _workspace_publish_error(ws, run_id, branch, detail)
        rb = _ws_git(clone, "rebase", "FETCH_HEAD")
        if rb.returncode != 0:
            # コンフリクトした rebase を放置したまま push を繰り返しても解消しない上、
            # 部分適用状態のツリーが後続の finalize を汚す。中断して失敗を伝える。
            _ws_git(clone, "rebase", "--abort")
            raise RuntimeError(
                f"workspace rebase が競合しました（{branch}）: {(rb.stderr or rb.stdout).strip()[:300]}")
        _save_recovery_ref(ws, run_id)
        backoff_sleep(2 ** i if i < 4 else 16)
    detail = ((last_push.stderr or last_push.stdout).strip()[:300]
              if last_push is not None else "")
    raise _workspace_publish_error(ws, run_id, branch, detail)


# publication 状態の悪さの順（集約は最悪を採る）。§5.2
_PUBLICATION_SEVERITY = {"not-required": 0, "published": 1, "published-manually": 2, "failed": 3}


class WorksetPublishError(WorkspacePublishError):
    """workset の一部の要素が公開できなかった失敗（半公開状態）。

    成功した要素の publication は「published のまま」記録に残す——複数 remote への push は
    原子的にできないので、隠さずに要素ごとへ復旧させる（§5.5）。"""

    def __init__(self, message: str, deliveries: "list[dict]"):
        RuntimeError.__init__(self, message)
        self.data = {"error_class": "workspace_publish",
                     "publication": aggregate_publication(deliveries) or {},
                     "deliveries": deliveries}


def aggregate_publication(deliveries) -> "dict | None":
    """要素ごとの publication から run/ノード単位の集約を導く（最悪状態を採る）。

    完了条件は「全要素の publication が published（変更ゼロの要素は not-required）」なので、
    1 要素でも failed なら集約も failed になる。どの要素が公開できて、どれができなかったかは
    `repositories` / `failed` に名前で残す——読み手が deliveries を畳み直さないで済むように。"""
    pubs = [(d, d.get("publication")) for d in (deliveries or [])
            if isinstance(d, dict) and isinstance(d.get("publication"), dict)]
    if not pubs:
        return None
    worst = "not-required"
    for _d, pub in pubs:
        state = str(pub.get("state") or "")
        if _PUBLICATION_SEVERITY.get(state, 3) > _PUBLICATION_SEVERITY.get(worst, 0):
            worst = state
    published = [str(d.get("name") or "") for d, pub in pubs
                 if str(pub.get("state") or "") in ("published", "published-manually")]
    failed = [str(d.get("name") or "") for d, pub in pubs
              if str(pub.get("state") or "") == "failed"]
    out = {"state": worst, "attempted_at": now_iso(),
           "repositories": [n for n in published if n]}
    if failed:
        out["failed"] = [n for n in failed if n]
    return out


def _not_required_delivery(spec: dict) -> dict:
    """変更が無かった要素の記録。「公開不要」と「古い記録で公開状態不明」を混同させない。"""
    named = {"name": str(spec["name"])} if spec.get("name") else {}
    return {**named, "url": spec.get("url"), "branch": spec.get("branch"),
            "target": spec.get("target") or spec.get("base") or "",
            "path": spec.get("path") or "",
            "publication": {**named, "state": "not-required", "url": spec.get("url"),
                            "branch": spec.get("branch"), "attempted_at": now_iso()}}


def finalize_workset(workset, run_id: str, node_id: str, kind: str = "") -> "list[dict]":
    """workset の要素ごとに finalize し、要素ごとのデリバリ記録を返す。

    同じ clone・同じ作業ブランチを共有する要素（同 url・同 base・別 path）はまとめて 1 回
    commit/push し、変更を許す範囲は path の和集合にする（§5.1）。

    **1 要素の push 失敗で止めない**。残りの要素も finalize を試みてから
    `WorksetPublishError` を上げる——先に失敗した要素で打ち切ると、他の要素が
    「commit も push もされていない」のか「されたが記録が無い」のか区別できなくなる。
    公開できなかった要素だけを resume で再 push できるよう、成功した要素は published の
    まま残す（§5.5）。"""
    groups: "dict[tuple, list]" = {}
    order: "list[tuple]" = []
    deliveries: "list[dict]" = []
    for spec in (workset or []):
        if not isinstance(spec, dict):
            continue
        if not spec.get("clone"):
            # clone を用意できなかった要素。従来（1 要素）と同じく「公開不要」を明示する
            # ——読み手が「古い記録で公開状態不明」と混同しないため。指示ブロックには
            # clone 失敗が出ているので、書けなかったこと自体は別に伝わっている。
            deliveries.append(_not_required_delivery(spec))
            continue
        key = (str(spec.get("clone") or ""), str(spec.get("branch") or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(spec)
    failures: "list[WorkspacePublishError]" = []
    for key in order:
        members = groups[key]
        allowed = [str(m.get("path") or "") for m in members]
        try:
            delivered = finalize_workspace(members[0], run_id, node_id,
                                           kind=kind, allowed_paths=allowed)
        except WorkspacePublishError as exc:
            failures.append(exc)
            publication = dict(exc.data.get("publication") or {})
            for m in members:
                named = {"name": str(m["name"])} if m.get("name") else {}
                deliveries.append({**named, "url": m.get("url"), "branch": m.get("branch"),
                                   "target": m.get("target") or m.get("base") or "",
                                   "path": m.get("path") or "",
                                   "publication": {**publication, **named}})
            continue
        if delivered is None:
            deliveries.extend(_not_required_delivery(m) for m in members)
            continue
        for m in members:
            named = {"name": str(m["name"])} if m.get("name") else {}
            deliveries.append({**delivered, **named, "path": m.get("path") or "",
                               "publication": {**delivered["publication"], **named}})
    if failures:
        if len(workset or []) <= 1:
            raise failures[0]      # 1 要素は従来どおりの例外（形も意味も変えない）
        names = ", ".join(str(d.get("name") or "") for d in deliveries
                          if str((d.get("publication") or {}).get("state") or "") == "failed")
        raise WorksetPublishError(f"workset の一部が公開できませんでした（{names}）: "
                                  f"{failures[0]}", deliveries)
    return deliveries


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
    """**1 要素の** 書込先ワークスペースをエージェントに伝える決定的な指示ブロック。
    複数要素は `workset_instruction` が扱う（そちらが 1 要素のときここへ委譲する）。
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


def workset_instruction(workset) -> str:
    """workset をエージェントへ伝える決定的な指示ブロック。

    1 要素なら従来の `workspace_instruction`（「唯一の書込先」の文言つき）をそのまま出す
    ——N=1 のプロンプトを 1 文字も変えないため。複数要素では要素ごとに
    「name / clone / 変更してよい path / ブランチ / 役割」を並べ、cwd の外にも書込先が
    あることと、列挙した以外の場所を変更しないことを明示する。"""
    specs = [e for e in (workset or []) if isinstance(e, dict) and e.get("url")]
    if len(specs) <= 1:
        return workspace_instruction(specs[0] if specs else None)
    primary = specs[0]
    lines = [f"【ワークスペース】このタスクの書込先リポジトリは {len(specs)} つあります"
             "（すべて clone 済み・順に primary から）。",
             f"  作業ディレクトリ（primary）: {primary.get('clone') or '(clone 失敗)'}"]
    for i, e in enumerate(specs):
        tag = "primary" if i == 0 else "追加"
        lines.append(f"  [{e.get('name')}] {e.get('url')}（{tag}）")
        if not e.get("clone"):
            lines.append("    clone に失敗しました。このリポジトリへは書き込めません。")
            continue
        lines.append(f"    ディレクトリ: {e['clone']}")
        if e.get("path"):
            lines.append(f"    変更してよいのは {e['path']} 配下のみ")
        br = f"    作業ブランチ: {e.get('branch')}"
        if e.get("base"):
            br += f"（{e['base']} から分岐"
            if e.get("target") and e["target"] != e["base"]:
                br += f"・最終的な MR/PR ターゲット = {e['target']}"
            br += "）"
        lines.append(br)
        if e.get("desc"):
            lines.append(f"    役割: {e['desc']}")
    lines.append("  上に列挙したディレクトリ以外は変更しないこと。ファイルを指すときは "
                 "`<name>:<リポジトリ相対パス>` の形（例 "
                 f"`{specs[1].get('name')}:src/index.ts`）か絶対パスを使うこと。")
    lines.append("  作業ツリー内のファイルを編集すること。commit と push は agent-flow が"
                 "リポジトリごとに自動で行うので、あなたは commit/push やブランチ切替を"
                 "しないこと。変更が不要なリポジトリは何も書き換えない（そのリポジトリへは"
                 "push されません）。")
    return "\n".join(lines)


def workset_path(workset, path: str) -> str:
    """`<name>:<相対パス>` 接頭辞を絶対パスへ解く。無接頭辞は primary 相対（現行と同じ）。

    read_allocation / operation.scope が要素をまたいでファイルを指すための語彙で、
    未知の name はそのまま返す（解けない指定で実行を止めない——参照は best-effort）。"""
    text = str(path or "")
    specs = [e for e in (workset or []) if isinstance(e, dict) and e.get("clone")]
    if not specs:
        return text
    name, sep, rest = text.partition(":")
    if sep and name and not os.path.isabs(text):
        for e in specs:
            if str(e.get("name") or "") == name:
                return os.path.join(str(e["clone"]), rest.lstrip("/\\"))
    return text


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
