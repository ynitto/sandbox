from __future__ import annotations
# mr.py — 元 agent-project.py の 5363-5900 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# タスク MR（成果物レビュー）— ap/<task-id> → target の MR を作り、承認で自動決着する。
#   GitLab REST v4 を stdlib で直叩きする最小クライアント（agent-flow executors/gitlab.py の
#   トークン解決・URL 解釈・承認規則の縮小版）。GitLab に到達できなければすべて無害にスキップし、
#   従来どおり「記録のみ」で動く（done の確定は MR に依存させるが、未設定なら従来のまま）。
# ---------------------------------------------------------------------------
_GL_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")
_GL_RC_FILES = ("~/.bashrc", "~/.bash_profile", "~/.profile", "~/.zshrc")


def _find_gitlab_idd_scripts_dir() -> "str | None":
    """gitlab-idd スキルの scripts/ ディレクトリ（config_loader.py 同梱）を探す
    （agent-flow の executors/gitlab.py と同じ探索順。connections.yaml を同じ流儀で読む）。"""
    candidates = []
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", "gitlab-idd", "scripts"))
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
            encoding="utf-8", errors="replace").stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", "gitlab-idd", "scripts"))
    except Exception:  # noqa: BLE001
        pass
    for skills_home in ("~/.agent/skills", "~/.kiro/skills"):
        candidates.append(os.path.join(os.path.expanduser(skills_home), "gitlab-idd", "scripts"))
    for agent_dir in [os.path.expanduser("~/.agent"), os.path.expanduser("~/.kiro"),
                      os.path.expanduser("~/.copilot"),
                      os.path.expanduser("~/.claude"), os.path.expanduser("~/.codex")]:
        reg = os.path.join(agent_dir, "skill-registry.json")
        if os.path.isfile(reg):
            try:
                with open(reg, encoding="utf-8") as f:
                    home = json.load(f).get("skill_home", "")
                if home:
                    candidates.append(os.path.join(home, "gitlab-idd", "scripts"))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(os.path.join(c, "config_loader.py")):
            return c
    return None


def _token_from_connections(conn_label: str = "default") -> str:
    """gl.py / agent-flow の executors/gitlab.py と同じ connections.yaml から接続ラベルのトークンを
    読む（config_loader 経由）。config_loader / connections.yaml / PyYAML が無ければ空文字
    （→ 次のソースへ委ねる）。"""
    scripts_dir = _find_gitlab_idd_scripts_dir()
    if not scripts_dir:
        return ""
    try:
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import importlib
        config_loader = importlib.import_module("config_loader")
        conn = config_loader.get_connection("gitlab", conn_label)
        return str((conn or {}).get("token") or "").strip()
    except Exception:  # noqa: BLE001 — 不在/解析失敗は無視し、環境変数・シェル rc へ委ねる
        return ""


def _gl_token() -> str:
    """トークンを agent-flow の gitlab executor と同じ場所・同じ優先順で解決する:
    connections.yaml（既定ラベル "default"） → 環境変数 GITLAB_TOKEN/GL_TOKEN → シェル rc ファイル。
    以前は環境変数/rc しか見ておらず、connections.yaml にだけトークンを置いた環境ではタスク MR の
    作成だけが（agent-flow 側の委譲 executor は動くのに）静かにスキップされていた。"""
    token = _token_from_connections()
    if token:
        return token
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


def _gl_parse_repo(url: str) -> "tuple[str, str, str] | None":
    """リポジトリ URL → (scheme, host, project_path)。HTTP(S) のスキームを保持する。"""
    u = (url or "").strip()
    m = re.match(r"^(https?)://([^/]+)/(.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", u)
    if m:
        return "https", m.group(1), m.group(2)
    return None


def _gl_api(scheme: str, host: str, token: str, method: str, path: str,
            data: "dict | None" = None, params: "dict | None" = None):
    import urllib.error
    import urllib.parse
    import urllib.request
    url = f"{scheme}://{host}/api/v4{path}"
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


# ---------------------------------------------------------------------------
# フォージ境界（S4-1.7）
# ---------------------------------------------------------------------------
# 成果物レビューの正は MR/PR 一本にする、という仕様（S4）は「どのフォージか」に依存しない。
# 実装は **GitLab のみ**で、GitHub / Gitea は境界だけ切って未対応にしてある——動作確認できる
# 環境が無いまま書いた API クライアントは「動くかどうか分からないコード」が増えるだけだから。
# 未対応フォージは「フォージ無し」として扱い、従来の dashboard ボタン決着へ倒す（S4-6）。
#
# 認証情報は **設定 2 層のどちらにも置かない**（host.yaml は共有しないが平文で PC に残り、
# プロジェクト yaml は state repo 経由で全 PC へ配られる）。connections.yaml / 環境変数 / rc ファイルのまま。
_FORGE_UNSUPPORTED_WARNED: "set[str]" = set()
_GITLAB_NO_TOKEN_WARNED = False


def _forge_kind(url: str) -> str:
    """リポジトリ URL からフォージ種別を返す。'gitlab' / 'github' / 'gitea' / ''（不明）。

    自己ホストの GitLab はホスト名に gitlab を含まないことが多い（git.example.com 等）ので、
    ホスト名で判別できないときは **GitLab トークンの有無**で決める（移行前の挙動そのまま:
    `_gl_parse_repo` が通り、かつトークンがあれば GitLab として叩いていた）。
    """
    parsed = _gl_parse_repo(url)
    if not parsed:
        return ""
    host = parsed[1].lower()
    if "gitlab" in host:
        return "gitlab"
    if "github" in host:
        return "github"
    if "gitea" in host or "codeberg" in host:
        return "gitea"
    return "gitlab" if _gl_token() else ""


def forge_available(cfg: "Config", url: str) -> str:
    """この URL に対して決着まで扱えるフォージ種別（扱えなければ ""）。

    未対応フォージ・トークン欠落はそれぞれ 1 回だけ警告して "" を返す——黙って無視すると
    「MR ができない理由」がどこにも出ず、検収カードに MR が載らない原因を人が追えない。
    """
    global _GITLAB_NO_TOKEN_WARNED
    kind = _forge_kind(url)
    if kind == "gitlab":
        if _gl_token():
            return "gitlab"
        if not _GITLAB_NO_TOKEN_WARNED:
            _GITLAB_NO_TOKEN_WARNED = True
            print(">>> 注意: GitLab トークンが見つかりません"
                  "（connections.yaml の gitlab/default、環境変数 GITLAB_TOKEN/GL_TOKEN、"
                  "または ~/.bashrc 等のいずれにも無し）。タスク MR の自動作成・決着は行いません。"
                  "検収は dashboard のボタンで行ってください", file=sys.stderr)
        return ""
    if kind and kind not in _FORGE_UNSUPPORTED_WARNED:
        _FORGE_UNSUPPORTED_WARNED.add(kind)
        print(f">>> 注意: {kind} は未対応のフォージです（MR/PR の自動作成・決着は行いません）。"
              "検収は dashboard のボタンで行ってください", file=sys.stderr)
    return ""


def _task_mr_coords(task: "Task") -> "tuple[str, str, str, str] | None":
    """タスクに記録済みの MR 座標 (scheme, host, project, iid)。無ければ None。"""
    iid = str(task.get("mr_iid") or "").strip()
    pj = str(task.get("mr_project") or "")
    if not iid or "|" not in pj:
        return None
    endpoint, proj = pj.split("|", 1)
    if "://" in endpoint:
        scheme, host = endpoint.split("://", 1)
    else:
        scheme, host = "https", endpoint  # スキーム記録前の既存タスクとの互換性
    return scheme, host, proj, iid


def ensure_task_mr(cfg: "Config", task: "Task") -> str:
    """review 到達時に ap/<task-id> → target の MR を用意する（冪等）。
    GitLab 未設定・非 GitLab リポジトリ・API 失敗は ""（記録のみで続行＝done の確定は従来どおり）。"""
    if not getattr(cfg, "task_branch", False):
        return ""
    if task.get("mr_url"):
        return str(task.get("mr_url"))
    spec = _workspace_spec_for(cfg, task)
    if not spec or not spec.get("url"):
        return ""
    parsed = _gl_parse_repo(spec["url"])
    if not parsed or not forge_available(cfg, spec["url"]):
        return ""                       # フォージ無し運用（S4-6）＝従来どおり記録のみで続行
    token = _gl_token()
    scheme, host, proj = parsed
    source = task_branch_name(cfg, task)
    target = spec.get("target") or spec.get("base") or "main"
    try:
        ep = _gl_quote(proj)
        found = _gl_api(scheme, host, token, "GET", f"/projects/{ep}/merge_requests",
                        params={"source_branch": source, "state": "opened"})
        mr = found[0] if isinstance(found, list) and found else None
        if mr is None:
            mr = _gl_api(scheme, host, token, "POST", f"/projects/{ep}/merge_requests",
                         data={"source_branch": source, "target_branch": target,
                               "title": f"[agent-project] {task.id}: {task.title[:80]}",
                               "description": f"agent-project タスク {task.id} の成果物"
                                              f"（ブランチ {source}。承認でクリーンなら自動マージ）",
                               "remove_source_branch": True})
        task.drop("mr_url", "mr_iid", "mr_project")
        task.extra += [("mr_url", str(mr.get("web_url") or "")),
                       ("mr_iid", str(mr.get("iid") or "")),
                       ("mr_project", f"{scheme}://{host}|{proj}")]
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
    scheme, host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        mr = _gl_api(scheme, host, token, "GET", f"/projects/{ep}/merge_requests/{iid}")
        state = str(mr.get("state") or "")
        if state in ("merged", "closed"):
            return True, f"MR は決着済み（{state}）"
        problems = []
        discussions = _gl_api(scheme, host, token, "GET",
                              f"/projects/{ep}/merge_requests/{iid}/discussions",
                              params={"per_page": 100})
        unresolved = sum(1 for d in (discussions if isinstance(discussions, list) else [])
                         if any(n.get("resolvable") and not n.get("resolved")
                                for n in (d.get("notes") or [])))
        changes = _gl_api(scheme, host, token, "GET", f"/projects/{ep}/merge_requests/{iid}/changes")
        no_diff = isinstance(changes.get("changes"), list) and not changes["changes"]
        conflicts = bool(mr.get("has_conflicts")) or \
            str(mr.get("merge_status") or "") == "cannot_be_merged"
        if unresolved:
            problems.append(f"未解決のレビューコメントが {unresolved} 件")
        if conflicts and not no_diff:
            problems.append(f"コンフリクト（merge_status={mr.get('merge_status')}）")
        if problems:
            why = "; ".join(problems)
            _gl_api(scheme, host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                    data={"body": f"agent-project: # 差し戻し（自動チェック）\n- {why}\n"
                                  "解消後に再度 approve してください。"})
            return False, why
        if no_diff:                              # 差分なし＝マージするものが無い → クローズで決着
            _gl_api(scheme, host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                    data={"state_event": "close"})
            return True, "差分なし MR＝クローズで決着"
        _gl_api(scheme, host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}/merge",
                data={"should_remove_source_branch": True})
        return True, "MR を自動マージ"
    except RuntimeError as e:
        return False, f"MR の決着に失敗（解消/再試行してください）: {e}"


def finalize_task_delivery(cfg: "Config", task: "Task") -> "tuple[bool, str]":
    """検収承認された作業ブランチを target へ統合する。

    GitLab MR が使える場合はレビュー情報を保ったまま API でマージする。MR を作れない場合も
    origin 上の target が作業ブランチの祖先であることを確認し、fast-forward push で統合する。
    統合できなければ review を維持し、成果未反映のまま done にしない。
    """
    if _task_mr_coords(task) is None:
        if ensure_task_mr(cfg, task):
            persist_task(cfg, task)
    if _task_mr_coords(task) is not None:
        return finalize_task_mr(cfg, task)

    work = _task_work_branch(cfg, task)
    if work is None:
        return True, ""  # 書込 workspace を持たない読み取り専用・旧形式タスク
    target, branch = work
    repo = _source_repo(cfg, task)
    ref, files = work_branch_changes(cfg, target, branch, repo=repo)
    if not ref:
        return False, f"作業ブランチ {branch} を解決できないため、{target} へマージできません"

    def run(*args: str):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=180)

    if run("check-ref-format", "--branch", target).returncode != 0 \
            or run("check-ref-format", "--branch", branch).returncode != 0:
        return False, "作業ブランチまたはターゲットブランチ名が不正です"
    fetched = run("fetch", "-q", "origin", target)
    target_ref = f"origin/{target}" if fetched.returncode == 0 else target
    # 既に target 側へ取り込まれていれば冪等成功。
    if run("merge-base", "--is-ancestor", ref, target_ref).returncode == 0:
        return True, f"作業ブランチ {branch} は {target} へ統合済み"
    if not files:
        return False, f"{target} と {branch} の検収差分を取得できません"

    push_ref = ref
    diverged = run("merge-base", "--is-ancestor", target_ref, ref).returncode != 0
    if diverged:
        # target が作業開始後に進んでいても、人が検収承認した成果を永久に
        # done にできない理由にはしない。現在の作業ツリーを触らない一時 worktree で
        # 通常マージを試し、Git が競合無しと判定できた場合だけ target へ push する。
        # 競合時は一切解決せず review を維持し、人に具体的な理由を返す。
        with tempfile.TemporaryDirectory(prefix="agent-project-approve-merge-") as merge_dir:
            added = run("worktree", "add", "--detach", merge_dir, target_ref)
            if added.returncode != 0:
                why = (added.stderr or added.stdout or "git worktree add failed").strip()[:300]
                return False, f"{target} と {branch} の統合準備に失敗しました: {why}"
            try:
                merged = subprocess.run(
                    ["git", "-C", merge_dir,
                     "-c", "user.name=agent-project",
                     "-c", "user.email=agent-project@localhost",
                     "merge", "--no-ff", "--no-edit", ref],
                    capture_output=True, text=True, timeout=180,
                )
                if merged.returncode != 0:
                    why = (merged.stderr or merged.stdout or "git merge failed").strip()[:500]
                    return False, (f"{target} と {branch} の自動統合で競合しました。"
                                   f"成果ブランチを更新して再検収してください: {why}")
                head = subprocess.run(
                    ["git", "-C", merge_dir, "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=30,
                )
                if head.returncode != 0 or not head.stdout.strip():
                    return False, f"{target} と {branch} の統合コミットを確定できません"
                push_ref = head.stdout.strip()
            finally:
                run("worktree", "remove", "--force", merge_dir)

    pushed = run("push", "origin", f"{push_ref}:refs/heads/{target}", "--porcelain")
    if pushed.returncode != 0:
        why = (pushed.stderr or pushed.stdout or "git push failed").strip()[:300]
        return False, f"作業ブランチを {target} へマージできません: {why}"
    # target への反映後だけ作業ブランチを削除する。削除失敗は納品結果を巻き戻さない。
    run("push", "origin", "--delete", branch)
    mode = "競合なしで統合" if diverged else "fast-forward マージ"
    return True, f"作業ブランチ {branch} を {target} へ {mode}"


def review_target_fresh(cfg: "Config", task: "Task") -> "tuple[bool, str]":
    """検証時 target が現在も同じかを確認する。証跡が無い旧タスクは従来互換で通す。"""
    target = str(task.get("gate_target") or "").strip()
    verified = str(task.get("gate_target_rev") or "").strip()
    if not target or not verified:
        return True, ""
    repo = _source_repo(cfg, task)

    def run(*args: str):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              text=True, timeout=180)

    fetched = run("fetch", "-q", "origin", target)
    if fetched.returncode != 0:
        return False, f"target {target} の最新 revision を確認できないため承認できません"
    current = run("rev-parse", f"origin/{target}").stdout.strip()
    if not current:
        return False, f"target {target} の revision を解決できないため承認できません"
    if current != verified:
        try:
            report = str(json.loads(str(task.get("verification") or "{}"))
                         .get("report") or "")
        except (TypeError, ValueError):
            report = ""
        result_rev = os.path.basename(report).removesuffix(".md")
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", result_rev) \
                and run("merge-base", "--is-ancestor", result_rev,
                        f"origin/{target}").returncode == 0:
            return True, ""  # 検証済み成果を外部で統合済み
        return False, (f"検証後に target {target} が更新されました"
                       f"（{verified[:12]} → {current[:12]}）。最新 target を統合して再検証してください")
    return True, ""


# ---------------------------------------------------------------------------
# フォージ側シグナルからの決着（S4-3・S4-4）
# ---------------------------------------------------------------------------
# 「人のレビューコメントをいつ差し戻しに変えるか」を、キーワード推定ではなく **決定的な
# シグナル**で決める。コメント本文の語（「やり直し」「LGTM」等）は書き手の言い回し 1 つで
# 判定が変わり、しかも変わったことに気づけない。
#
#   MR がマージされた                        → approve（done 確定）
#   MR が未マージでクローズされた            → reject
#   changes-requested ラベル / レビュー      → revise（未解決コメントを feedback に注入）
#   それ以外（コメントのみ・承認だけ等）      → 何もしない（人の明示操作を待つ）
#
# 照会と書き込みは常駐体の sync 周期が担う（dashboard は表示に徹する）。
_CHANGES_REQUESTED_LABELS = ("status:changes-requested", "changes-requested")


def _mr_changes_requested(mr: dict, reviews) -> bool:
    """フォージ側で「修正を求める」が明示されたか（ラベル or Changes Requested レビュー）。"""
    labels = [str(x).lower() for x in (mr.get("labels") or [])]
    if any(lbl in labels for lbl in _CHANGES_REQUESTED_LABELS):
        return True
    for r in (reviews if isinstance(reviews, list) else []):
        # GitLab は reviewer の state（reviewed / requested_changes）を持つ
        if str((r or {}).get("state") or "").lower() in ("requested_changes", "changes_requested"):
            return True
    return False


def _unresolved_notes(scheme: str, host: str, token: str, ep: str, iid: str) -> "list[str]":
    """未解決のレビューコメント本文（解決済み・system note は除く）。

    解決済みまで流すと、一度直した指摘が毎回 feedback へ積み直されて収束しない。
    """
    out: "list[str]" = []
    discussions = _gl_api(scheme, host, token, "GET",
                          f"/projects/{ep}/merge_requests/{iid}/discussions",
                          params={"per_page": 100})
    for d in (discussions if isinstance(discussions, list) else []):
        for n in (d.get("notes") or []):
            if n.get("system") or not n.get("resolvable") or n.get("resolved"):
                continue
            body = str(n.get("body") or "").strip()
            if body and not body.startswith("agent-project:"):   # 自分が書いたコメントは拾わない
                out.append(body)
    return out


def _settle_from_forge(cfg: "Config", task: "Task") -> "tuple[str, str]":
    """1 タスクぶんのフォージ照会。(決着, 理由) を返す。決着は
    approve / reject / revise / ""（何もしない）。到達不能も ""——フォージが見えないことを
    「未マージ＝reject」と読むと、回線が切れただけで成果が却下される。"""
    coords = _task_mr_coords(task)
    token = _gl_token()
    if coords is None or not token:
        return "", ""
    scheme, host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        mr = _gl_api(scheme, host, token, "GET", f"/projects/{ep}/merge_requests/{iid}")
        state = str(mr.get("state") or "")
        if state == "merged":
            return "approve", "MR がマージされた"
        if state == "closed":
            return "reject", "MR が未マージでクローズされた"
        reviews = _gl_api(scheme, host, token, "GET", f"/projects/{ep}/merge_requests/{iid}/reviewers")
        if not _mr_changes_requested(mr, reviews):
            return "", ""
        notes = _unresolved_notes(scheme, host, token, ep, iid)
        return "revise", ("\n".join(notes)[:1500] or "フォージで修正が要求されました（コメント無し）")
    except RuntimeError:
        return "", ""                    # 到達不能は決着しない（現状維持）


def _apply_revise(cfg: "Config", task: "Task", guidance: str) -> None:
    """フォージの changes-requested を差し戻しへ変換する（needs の [x] 差し戻しと同じ形）。"""
    task.status = "ready"
    task.drop("feedback")
    task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
    task.retries += 1                    # 計画変更＝新しい run（同 run 再開にしない）
    append_brief_item(cfg, task, guidance, source="forge-changes-requested")
    autonomy_record(cfg, task, clean=False)
    persist_task(cfg, task)
    clear_needs_file(cfg, task.id)
    append_decision(cfg, task.id, "forge",
                    context=f"{task.id}（{task.title}）にフォージで修正要求",
                    action="forge-revise", reason=guidance[:300],
                    affects=f"{task.id} → ready（次 act に反映）")


def poll_task_mrs(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """検収待ち（review）タスクの MR を照会し、決定的シグナルを決着へ変換する（S4-3/S4-4）。

    対象は `mr_iid` を持つ review 状態のタスクだけなので、API 呼び出し数は検収待ち件数に
    比例して有界。`remote_review: observe` では照会結果を journal に残すだけで決着させない
    （移行用）。決着の口は「フォージのシグナル」と「dashboard のボタン」の 2 つで、どちらも
    同じ approve / reject / revise の契約へ合流する。
    """
    # 値域の正規化は build_config（`_one_of`）が済ませている。ここで getattr の既定や
    # 値域クランプを持つと、Config への配線が落ちても「常に settle」で静かに動き続ける
    # ——実際にそれで observe 分岐が到達不能の死んだコードになっていた。
    mode = cfg.remote_review
    settled: "list[str]" = []
    for t in tasks:
        if t.norm_status() != "review" or not t.get("mr_iid"):
            continue
        decision, why = _settle_from_forge(cfg, t)
        if not decision:
            continue
        if mode == "observe":
            append_journal(cfg.journal,
                           f"remote_review(observe): {t.id} のフォージ決着は {decision}（{why[:120]}）"
                           "— 表示のみで決着させません")
            continue
        if decision == "approve":
            release_claim(cfg, t)
            ok, msg = approve_review_done(cfg, t, f"フォージの決着: {why}")
            append_journal(cfg.journal, f"remote_review: {t.id} を承認（{why}）"
                                        + ("" if ok else f" — 保留: {msg[:120]}"))
        elif decision == "reject":
            cmd_reject(cfg, t.id, why)
            append_journal(cfg.journal, f"remote_review: {t.id} を却下（{why}）")
        else:
            _apply_revise(cfg, t, why)
            append_journal(cfg.journal, f"remote_review: {t.id} を差し戻し（未解決コメントを注入）")
        settled.append(t.id)
    return settled


def close_task_mr(cfg: "Config", task: "Task", reason: str) -> None:
    """却下（reject）時: タスク MR をクローズしソースブランチを削除する（best-effort・
    gitlab-review-viewer の却下と同じ規則）。GitLab 未設定なら何もしない。"""
    coords = _task_mr_coords(task)
    token = _gl_token()
    if coords is None or not token:
        return
    scheme, host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        _gl_api(scheme, host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                data={"body": f"agent-project: タスク {task.id} は却下されました（{reason}）。"})
        _gl_api(scheme, host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                data={"state_event": "close"})
        branch = task_branch_name(cfg, task)
        _gl_api(scheme, host, token, "DELETE",
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


def _run_task_verifier(cfg: "Config", task: "Task",
                       vcwd: "Path") -> "tuple[bool, bool, str, dict | None]":
    """receipt を採用できなかったタスクの残余経路（P1-A8 で旧 verify 実行は撤去済み）。

    受理するのは検証委譲（P4-b）の external verdict だけ。それも無ければ done にせず
    人の判断へ倒す——旧 fast path（task.verify のローカル実行）と旧 verifier（LLM）は
    agent-flow runner の receipt が完全に置換した。検証材料は verification_plan として
    run に渡り、receipt の検算（settle_from_receipt）だけが done の根拠になる。
    戻り値は既存の settle と同じ形 (ok, flaky, vmsg) に検証レコードを足したもの。
    """
    rev = _git_out(vcwd, "rev-parse", "HEAD").strip() if (vcwd / ".git").exists() else ""
    if task_acceptance(task):
        external = read_external_verdict(cfg, task, rev)
        if external:
            # 検証委譲（P4-b）の受理: このノードでは確かめられなかった基準を板へ回し、別の端末が
            # 同じ成果コミットで確かめた receipt が返ってきている。**同じことをもう一度させない**
            # （C3）ため、この rev の検証として受け入れる。採否の規則は内蔵 verifier と同一で、
            # 検算（read_external_verdict）を通った receipt だけがここへ来る。
            return _adopt_receipt(cfg, task, external,
                                  f"別の端末（{external.get('verified_by') or '不明'}）の検証委譲")
    if has_verify_plan(task):
        return False, False, ("検証 receipt がありません（統一 verify の receipt を検算できるまで"
                              " done にしない → 人の判断へ）"), None
    return False, False, "verify 未定義（自己申告では done にできない → 人の判断へ）", None


def _settle_review(cfg, task, act_msg, git_base, branch, ev, vmsg, protect_hits, assisted,
                   policy, reasons, cycle, risk: "tuple[str, str] | None" = None,
                   verification: "dict | None" = None):
    """verify は通ったが承認ゲート対象（review/gate/protect/assisted）→ done せず人の承認(review)へ。
    所在（ref/ブランチ）を gate_* に保持し、approve 時の受領書へ引き継ぐ。"""
    ts = _now_ts()
    ref = extract_delivery_ref(act_msg, cfg, git_base)
    task.status = "review"
    task.drop("gate_ref", "gate_vmsg", "gate_ts", "gate_protect",
              "gate_target", "gate_target_rev")
    task.set("gate_ref", ref)
    task.set("gate_ts", ts)
    task.set("gate_branch", branch)             # approve 時の受領書に所在（ブランチ）を引き継ぐ
    task.set("gate_vmsg", vmsg.replace("\n", " ")[:200])
    if isinstance(verification, dict):
        integration = verification.get("integration")
        if isinstance(integration, dict) and integration.get("verdict") == "pass":
            task.set("gate_target", str(integration.get("target") or ""))
            task.set("gate_target_rev", str(integration.get("target_rev") or ""))
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
    # 成果物レビューの MR: タスクブランチ（ap/<id>）→ target の MR を用意し（冪等・GitLab 設定時のみ）、
    # 承認（approve）時に Stage 2 と同じ規則（クリーンなら自動マージ）で決着させる
    mr_url = ensure_task_mr(cfg, task) or str(task.get("mr_url") or "").strip()
    if mr_url:
        if "- MR:" not in (ev or ""):
            ev = (ev + "\n" if ev else "") + f"- MR: {mr_url}（承認時にクリーンなら自動マージ）"
        if not ref:
            task.set("gate_ref", mr_url)
    # viewer の検収サブ画面向け: 複数リポジトリの構造化ペイロード（書込先＋参照）
    delivery = delivery_entries(cfg, task, mr_url=mr_url)
    mark_needs_entry(cfg, task)   # この検収待ちが「人の決定より後」であることの印（G-2）
    persist_task(cfg, task)
    write_needs_file(cfg, task,
                     f"検証は通っている（verify=PASS）。人の検収を待っている理由: {gate_why}。"
                     f"内容が良ければ approve で done 確定、直したいことがあれば下に書いて差し戻す",
                     review=True, evidence=ev, risk=risk, mr_url=mr_url, delivery=delivery,
                     verification=verification)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 検収待ち{disp} — {ref}")


def _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle, autonomy_cache):
    """verify=PASS かつゲート対象外 → 無人 auto-done（受領書＋archive）。集計 delta を返す。"""
    task.status = "done"
    record_learn_outcome(cfg, task, worked=True)                        # learn 適用で done＝成功（W10）
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


def _failure_record(cfg, task, blob, vmsg, phase, verdict) -> dict:
    """needs へ載せる失敗の構造化レコード（表示層はこれを読むだけ）。

    分類（chain）と検証の解釈（diagnose_verify_failure）を、生データを持っているここで
    一度だけ行う。以前は agent-dashboard が判断材料の**散文を正規表現で読み直して**おり、
    書き手の文言が変わると読み手だけが静かに壊れた。解釈できない項目は空のままにする
    （空は「分からない」であって「失敗していない」ではない）。"""
    chain = agent_error_chain(blob)
    rec = {"cls": chain[0] if chain else "", "chain": chain,
           "phase": str(phase or ""), "verdict": str(verdict or "")}
    # 検証の所見は「検証まで到達した」ときだけ。未実行の記録から所見を作らない。
    if verdict == VERIFY_FAILED and task.verify:
        rec.update(diagnose_verify_failure(task.verify, vmsg, cfg.workdir))
    return rec


def _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location="local",
                    phase=PHASE_VERIFY, verdict=VERIFY_FAILED):
    """verify=NG → 上限内なら積み直し / 学習で自動解決 / 上限超で人へエスカレーション。
    委譲 executor（gitlab）の却下なら、人コメント（やり直し指示）を次 act の feedback に注入する。

    その前に**失敗トリアージ**: 失敗が実行制御または環境要因（control=管理停止 /
    quota=利用上限 / auth=認証切れ / env=CLI・
    モデルの問題）なら、これはタスクの内容と無関係で、リトライしても同じ理由で全タスクが
    落ち続ける。リトライを焼かず・裁定（これも LLM 呼び出し＝同じ理由で失敗する）も呼ばず、
    原因と直し方を明記して人へ回す。環境を直して approve すれば同じ run の続きから再開する。"""
    blob = f"{vmsg}\n{_flow_failure_blob(cfg, task)}"
    failure = _failure_record(cfg, task, blob, vmsg, phase, verdict)
    triage = classify_agent_failure(blob)
    if triage and triage[0] in AGENT_ERROR_ENV_CLASSES:
        cls, hint = triage
        labels = {"control": "管理設定による停止", "quota": "利用上限",
                  "auth": "認証切れ", "env": "実行環境の問題", "transient": "一時的なエラー"}
        label = labels[cls]
        category = "実行制御" if cls == "control" else "環境の問題"
        remedy = "全体設定で実行を許可してから" if cls == "control" else "環境を直してから"
        # 併記された他の分類も残す。1 つに畳むと、実行制御で止まった記録に前回の利用上限が
        # 混ざっていたことが消え、なぜその分類になったのかを後から追えない。
        others = [labels.get(c, c) for c in agent_error_chain(blob)[1:] if c in labels]
        also = f"（記録にはほかに {'・'.join(others)} の痕跡もあります）" if others else ""
        # needs にメモを書いて [x] しても run_id_for が新 run を作らないよう、再開約束を残す。
        task.set("env_resume", "1")
        _block(cfg, task, f"[agent-error:{cls}] {category}（{label}）: {hint}{also} "
                          "タスクの内容の問題ではないため、リトライ回数は消費していません。"
                          f"{remedy} approve すると、同じ run の続き（失敗した工程だけ）"
                          "から再開します。", reasons, evidence=ev, failure=failure)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（{category}: {label}。"
                                    f"リトライ・裁定は消費しない）")
        return
    task.retries += 1
    if not task.verify and verdict != VERIFY_NOT_RUN:
        # 失敗ではなく「完了条件が無いので人が確認して完了にする」状態。理由文もそう読める形にする
        # （「verify 未定義」だけだと viewer で失敗理由のように見える）。
        _escalate(cfg, task, "verify 未定義（工程は完了しています。完了条件が無いため自動では "
                             "done にできません。成果を確認し、問題なければ approve してください）",
                  reasons, cycle, evidence=ev)
        if task.norm_status() == "blocked":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
    elif task.retries > cfg.max_retries:
        learned = find_learned_resolution(cfg, task) if cfg.learn else None
        if learned and not task.get("autolearned"):
            src, guide = learned
            task.drop("feedback", "autolearned")
            # autolearned には出典 id を刻む（W10）: done なら learn-worked、再 blocked なら
            # learn-misfire を出典の決定記録へ返し、連続不発の失効判定の材料にする。
            task.extra += [("feedback", guide.replace("\n", " ⏎ ")), ("autolearned", src)]
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
            guidance = read_reject_guidance(cfg, location == "remote",
                                            run_id=str(task.get("last_run") or ""))
            if guidance:
                task.drop("feedback")
                task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
                # 却下（差し戻し）の意図を run ブリーフへも蓄積（追記のみ）。次 run 以降の全分散ノードへ伝播する。
                append_brief_item(cfg, task, guidance, source="gitlab-reject")
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


def _requeue_for_human(cfg: "Config", task: "Task", cycle: int, need: str, log: str) -> None:
    """成果を捨てず・自動採用もせず、人の判断へ隔離する（`recover_stale_doing` の分散モードと同形）。

    claim を解放して blocked にし、fencing token を進めてから needs 票を書く。token を進めるのは
    「この試行の claim はもう有効でない」と確定させるため——進めないまま放置すると、後から
    復帰したノードが古い token で settle を通してしまう。"""
    task.status = "blocked"
    task.set("claim_owner", "")
    task.set("claim_token", hashlib.sha256(os.urandom(32)).hexdigest()[:32])
    task.set("claim_generation", str(int(task.get("claim_generation") or 0) + 1))
    write_needs_file(cfg, task, need)
    persist_task(cfg, task)
    append_journal(cfg.journal, log)


def _settle_task(cfg: "Config", task: "Task", location: str, act_msg: str, cycle: int,
                 dtok: int, dusd: float, git_base, verify_env, policy: "Policy",
                 autonomy_cache: dict, reasons: dict) -> dict:
    """act 済みタスクを検証ゲート（verify→回帰→保護→進捗→flake）に通し、done/review/retry/escalate を
    確定する。副作用（persist/journal/needs/decision/delivery/archive）は内部で行い、run_loop が集計に使う
    deltas（archived・followups）を返す。run_loop の per-task 本体を 1 か所に切り出したもの（挙動は不変）。"""
    fence = claim_fence_state(cfg, task)
    if fence == "lost":
        refresh_distributed_task(cfg, task.id)
        append_journal(cfg.journal,
                       f"cycle {cycle}: {task.id} の stale 結果を破棄（claim fencing token 不一致）")
        return {"archived": 0, "followups": []}
    if fence == "unknown":
        # リモートに触れず claim を検証できなかった。fence を失った証拠は無いので破棄しない
        # （一過性の通信断で完成した成果が消える）。かといって他ノードが取り直していない保証も
        # 無いので自動採用もしない。実行ノード消失時（recover_stale_doing）と同じ扱いで人へ回す。
        # W7: 隔離元ノードを刻む——上限判定（_budget_reason）と次パスの再確認 1 回
        # （requeue_unknown_once）がこの印を読む。
        task.set("fence_unknown", cfg.node or "1")
        _requeue_for_human(cfg, task, cycle,
                           "リモート不通で claim を検証できませんでした。成果は保持しています。"
                           "resume/revise で採否を決めてください",
                           f"cycle {cycle}: {task.id} の claim をリモート不通で検証できず保留"
                           f"（成果は保持。人の判断へ）")
        return {"archived": 0, "followups": []}
    # act 中に人が revise（軌道修正）していたら、この試行の結果は確定せず修正内容で積み直す。
    # verify より先に判定する（方向の変わった成果に PASS/FAIL を付けない・verify コストも省く）。
    fresh = _load_task_file(cfg, task.id)
    if fresh is not None and fresh.get("revised"):
        _requeue_revised(cfg, task, fresh, cycle)
        return {"archived": 0, "followups": []}
    if location != "local":
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} を {location} で実行"
                       + (f"（{cfg.git_bus}）" if location == "remote" else ""))

    # ノードが実行中に発見した恒常制約を捕捉する（best-effort・決定的）。capture_insight が
    # 2 スコープへ射影: run ブリーフ（同一タスクの次 run・全分散ノードへ即時伝播）＋ learn
    # （タスク横断。auto-resolve → hits 閾値で rules.md 昇格のラダーに乗り、タスク完了後も
    # 教訓が死蔵されない）。verify の前（done/retry いずれの結末でも通る位置）で回収する。
    # 回収失敗はタスク処理を止めない。
    try:
        discovered = read_brief_discoveries(cfg, location == "remote",
                                            run_id=str(task.get("last_run") or ""))
        added = sum(1 for c in (discovered or [])
                    if capture_insight(cfg, task, c, source="node",
                                       learn=True, learn_action="node-constraint"))
        if added:
            append_journal(cfg.journal,
                           f"cycle {cycle}: {task.id} ノード発見制約 {added} 件を"
                           "run ブリーフ＋learn へ環流")
    except Exception:  # noqa: BLE001 — ブリーフ回収の失敗は settle を止めない
        pass

    # 人が「成果物の所在（リポジトリ/ブランチ/コミット）・差分・検証」を見て判断できる材料。
    # needs（判断待ち）と DELIVERY/archive（受領）双方に載せる。
    branch = _current_branch(cfg)
    regressed = False
    vtmp = None
    verification: "dict | None" = None      # 証跡ベース検証の判定レコード（S5）
    try:
        # workspace 指定タスクは git-bus ルート（workdir）でなく該当 repo のクローン内（指定 branch・
        # クローンのルート）で検証する。verify はリポジトリ直下からの相対で書かれる規約なので path
        # 配下には潜らない。明示 verify_cwd はそれを優先。
        vcwd, vtmp = _task_verify_cwd(cfg, task)
        venv = verify_env
        if vtmp and (vcwd / ".git").exists():          # 一時 clone は差分基準を clone の HEAD に取り直す
            head = _git_out(vcwd, "rev-parse", "HEAD").strip()
            venv = {"AGENT_BASE_REV": head, "KIRO_BASE_REV": head} if head else None
        # 検証は統一 verify（P1-A3/A8）のみ: run に渡した verification_plan の receipt を検算し、
        # digest・成果 revision・証跡が一致した判定だけを採用する。run が receipt を返さない
        # 経路（dry-run・stub・旧 agent-flow）は local runner が固定コマンドを同じ契約で実行
        # する。旧 fast path（task.verify のローカル直実行）と旧 verifier（LLM）は撤去済み
        # ——plan の無いタスクは板委譲の external verdict だけ受理し、無ければ人の判断へ。
        # flake 判定（verify_confirm）は plan の policy として receipt runner が実行する。
        _rev_head = _git_out(vcwd, "rev-parse", "HEAD").strip() if (vcwd / ".git").exists() else ""
        adopted = settle_from_receipt(cfg, task, build_task_verification_plan(cfg, task),
                                      _rev_head, vcwd, venv)
        if adopted is not None:
            ok, flaky, vmsg, verification = adopted
        else:
            ok, flaky, vmsg, verification = _run_task_verifier(cfg, task, vcwd)
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok,
                               phase=PHASE_VERIFY, task=task)
        if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
            # 回帰検査は **常に git-bus ルート（workdir）** で走らせる。task.verify と違い
            # cfg.regression_cmd はグローバル検査で、パス（例 `--repos <root>/repos.json`）も
            # 差分基準（`--base "$AGENT_BASE_REV"`）も workdir を前提に書かれる。workspace タスクの
            # vcwd（該当 repo の一時 clone）で走らせると codd-gate が repos.json を解決できず、
            # AGENT_BASE_REV も clone の HEAD（workdir に無い rev）になって回帰ゲートが壊れる。
            rok, rmsg = run_verify(cfg.regression_cmd, cfg.workdir, cfg.verify_timeout, verify_env)
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
        verification = None
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok,
                               phase=PHASE_VERIFY, task=task)
    finally:
        if vtmp:
            shutil.rmtree(vtmp, ignore_errors=True)
            _prune_caches(_provisioned_urls)   # 共有 cache の worktree 登録を回収（本体は残す）

    changed: set = set()
    protect_hits: list = []
    if ok and not flaky and not regressed:
        # 成果差分は **作業ブランチ（ap/<task-id>）** から取る。cfg.workdir は状態 worktree を指す
        # ので、そこを見ると bus/ の claims/events が「変更ファイル」として並び、保護パス判定も
        # リスク判定（大差分＝med）も実体と無関係な数字で動いてしまう。
        wb = _task_work_branch(cfg, task)
        if wb:
            _ref, _files = work_branch_changes(cfg, wb[0], wb[1], task=task)
            changed = set(_files)
        if not changed:                               # 作業ブランチが無い（単発実行等）は従来どおり
            changed = meaningful_changes(cfg, git_base)
        if policy.protect:                             # act が保護パスを触ったか（safety denylist）
            protect_hits = sorted({(p, m) for p in changed
                                   if (m := path_protected(p, policy.protect))})
    # no-progress: verify=PASS でも変更ゼロ＝履歴一致 verify による偽 done の疑い（opt-in）。
    # `- no_diff:` 宣言（W4）は「差分ゼロが正」の宣言なので expect: none と同じく外れる
    # （差分基準の差し替えは build_task_verification_plan 側。ここは決定的ガードの opt-out）。
    _expect = task.get("expect", "")
    require_prog = ((cfg.require_progress or _expect == "changes") and _expect != "none"
                    and not str(task.get("no_diff") or "").strip()
                    and (cfg.workdir / ".git").exists())
    no_progress = (ok and not flaky and not regressed and require_prog and not changed)
    # red-green: 合成 verify が act 前ツリーでも PASS＝この変更を弁別していない（偽 done）。
    # no-progress（変更ゼロ）の上位互換で、変更があっても verify がそれを追えていないケースを弾く。
    undiscriminating = (ok and not flaky and not regressed and not no_progress
                        and verify_undiscriminating(cfg, task, cfg.workdir,
                                                     vtmp is not None, git_base, verify_env))
    # 実効自律レベル（明示 - level: > track 自動昇格 > グローバル）。report は選択時に除外済み
    assisted = resolve_level(task, cfg, autonomy_cache) == "assisted"

    # 「検証不能」（環境にツールが無い等）は **失敗ではない**。fail と混ぜてリトライを焼くと、
    # 直す先がタスクの中に無いのに何度も作り直させることになる。環境要因失敗と同じ扱いで、
    # リトライを消費せず理由付きで人へ回す（環境を直して approve すれば同じ run の続きから）。
    unverifiable = (verification is not None and not verification["ok"]
                    and verification["unverifiable"] > 0 and verification["fail"] == 0)
    if unverifiable and not regressed:
        blocked_reasons = " ／ ".join(
            f"{c['text'][:60]} — {c['note'][:100]}"
            for c in verification["criteria"] if c["verdict"] == "unverifiable")[:400]
        # **まず機械で試せる解決を試す**（C3・C5）: 「このノードでは確かめられない」は
        # 他の端末なら確かめられるかもしれない。板があるなら検証を公示し、返ってくるまで
        # 待つ（P4-b）。人へ送るのは、公示できない・誰も請けない場合だけ。
        if delegate_verification(cfg, task, verification, blocked_reasons, cycle):
            return {"archived": 0, "followups": []}
        task.set("env_resume", "1")
        _block(cfg, task, f"[agent-error:env] 検証不能: このノードでは確かめられない基準があります"
                          f"（{blocked_reasons}）。タスクの内容の問題ではないため、リトライ回数は"
                          "消費していません。環境を直してから approve すると、同じ run の続きから"
                          "再開します。", reasons, evidence=ev)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（検証不能・"
                                    "リトライは消費しない）")
        return {"archived": 0, "followups": []}

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
                       risk=risk_digest(cfg, task, changed, protect_hits, dtok, dusd),
                       verification=verification)
    elif ok:
        capture_approve_learn(cfg, task, location)   # 承認時の人コメント（正例）を横断 learn 化
        return _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle,
                            autonomy_cache)
    else:
        _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location)
    return {"archived": 0, "followups": []}


def heal_partial_settles(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """settle 途中死の投影復旧（W6）。前へ倒して完成させた id を返す。

    settle は archive 書き込み → backlog 削除 → 納品書再生成 → needs 掃除の順にファイルを置き、
    コミットは state sync が 1 つにまとめる。途中死すると「archive にも backlog にも同じ id が
    ある」形が残り、そのままコミットされると done の記録と doing の実行予定が併存する。
    専用の復旧台帳は持たず、毎パスの整合点が残りの手順（削除・再生成・掃除）を決定的に
    やり直す——巻き戻しではなく前へ倒す（archive の納品記録を消すと成果参照が失われる）。

    id 再利用（過去の done と同 id で積み直された新タスク）と区別するため、倒すのは
    **実行中の姿のまま残っている（doing / offloaded / done）かつ題が一致する**ものだけ。
    ready や inbox で積み直されたものは新しい仕事なので触らない（intake は同じ id を
    再投入しうる）。題が違えば journal に残して人の目へ回す（自動では消さない）。"""
    adir = cfg.archive_dir()
    if not adir.exists():
        return []
    healed: "list[str]" = []
    for t in list(tasks):
        ap = adir / f"{t.id}.md"
        if not ap.is_file() or t.norm_status() not in ("doing", "offloaded", "done"):
            continue
        try:
            arch = parse_task(ap.read_text(encoding="utf-8"), t.id)
        except (OSError, ValueError):
            continue
        if arch.norm_status() not in ("done", "rejected"):
            continue
        if (arch.title or "").strip() != (t.title or "").strip():
            append_journal(cfg.journal,
                           f"整合点: {t.id} が backlog と archive の両方にあるが題が違う"
                           f"（id 再利用の疑い。自動では触らない）")
            continue
        delete_task_file(cfg, t)
        clear_needs_file(cfg, t.id)
        rebuild_delivery(cfg)
        tasks.remove(t)
        append_journal(cfg.journal,
                       f"整合点: {t.id} の settle 途中死を回収（archive を正として backlog を"
                       f"閉じ、納品書を再生成）")
        healed.append(t.id)
    return healed


def _run_setup(cfg: "Config", controller: bool = True) -> tuple:
    """run_loop の前処理: inbox 取り込み → 読み込み → 人のフィードバック解除 → triage/rot で
    ready/blocked を確定 → verify を用意する。(tasks, policy, reasons, ingested, inboxed, pre_blocked)。"""
    ensure_dirs(cfg)
    if not controller:
        tasks = load_tasks(cfg.backlog)
        policy = load_policy(cfg.policy)
        pre_blocked = {t.id for t in tasks if t.norm_status() in ("blocked", "review", "proposed")}
        return tasks, policy, {}, [], [], pre_blocked
    ingest_commands(cfg)          # 人の指示（approve/hold/pin/defer/revise のファイルドロップ）を先に適用
    inboxed = run_intake(cfg) + ingest_inbox(cfg)     # 取り込みコマンド＋外部ドロップ(inbox/)を backlog へ
    tasks = load_tasks(cfg.backlog)
    heal_partial_settles(cfg, tasks)  # settle 途中死（archive と backlog に同 id）の投影復旧（W6）
    recover_revised(cfg, tasks)   # 実行側が settle できなかった revise 予約の回収（クラッシュ自己回復）
    recover_stale_doing(cfg, tasks)   # 実行者が失踪した doing を ready へ戻す（再起動/クラッシュ自己回復）
    requeue_unknown_once(cfg, tasks)  # unknown 隔離の fencing 再確認（次パス 1 回だけ・W7）
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
    reconcile_needs(cfg, tasks)                       # 判断待ち（proposed/blocked/review）の票を status から整合
    #                                                   （作る＝ensure／消す＝対応タスクを失った票の掃除）
    prune_dangling_afters(cfg, tasks)                 # 削除された先行タスクへの after 参照を後続から切り離す
    reap_orphan_task_state(cfg)                       # タスク本体を失った付随状態（検証記録・ブリーフ・claim）を掃除
    if _coordination_active(cfg) and controller:
        state_sync(cfg, force=True)                   # allocation は remote 正本の最新 backlog を親にする
        allocate_distributed_tasks(cfg)
        tasks = load_tasks(cfg.backlog)
    return tasks, policy, reasons, ingested, inboxed, pre_blocked


def _budget_reason(cfg: "Config", cycle: int, start: float,
                   tokens_used: int, cost_used: float,
                   tasks: "list[Task] | None" = None) -> "str | None":
    """予算ゲート: サイクル/実時間/トークン/コスト/ソフト(throttle) の上限到達なら停止理由を返す。

    unknown 隔離（W7）も同じ出口: 自ノード印の隔離が上限に達したら throttle と同じ
    report 降格で新規 claim を止める（停止機構を増やさない。他ノードは走り続ける）。"""
    if tasks is not None and cfg.unknown_quarantine_max > 0:
        mine = sum(1 for t in tasks
                   if t.norm_status() == "blocked"
                   and str(t.get("fence_unknown") or "") == (cfg.node or "1"))
        if mine >= cfg.unknown_quarantine_max:
            return REASON_THROTTLE
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
