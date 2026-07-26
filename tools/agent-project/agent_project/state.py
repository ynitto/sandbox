from __future__ import annotations
# state.py — 状態ルート（状態専用リポジトリの clone）の確保。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 状態（backlog / needs / decisions / journal …）は **常に状態専用リポジトリの通常 clone** に置く
# （S1・single-resident-controller 設計 §4.1）。成果物リポジトリの中（<repo>/.agent-project）に
# 置くと、watch が 5 秒ごとに書き換えるファイルで人の git status が永久に汚れ、人やツールの
# git 操作（stash / rebase / pull --autostash）が書き込み中の状態ファイルを巻き込んで壊す。
#
# 以前はこれを「同じリポジトリの専用ブランチの worktree へ逃がす」方式（state_worktree_dir /
# state_branch / state_commit / state_push / state_backup_branch）で回避していたが、
#   ・worktree の生成とパス解決が Python と JS の二重実装になり Windows/WSL のパス差で壊れる
#   ・正本ブランチへのミラー（backup_state）がドリフトと履歴肥大を生む
#   ・root が「成果物側」と「状態側」の 2 つある（state_top / source_root）ことで、
#     start/stop の照合・検収 diff・インスタンスレジストリが別々の値を使う
# という費用が高く、S1 で廃止した。状態の commit / fetch / push は DirectStateGit が
# 単独で担う（書き手は 1 つ）。
def _git_toplevel_of(p: Path) -> "Path | None":
    """p を含む git 作業ツリーのトップ（git 管理外なら None）。"""
    try:
        r = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None



def _same_git_remote(a: str, b: str) -> bool:
    """2 つの git リモート指定（URL/パス）が同じリポジトリを指すか。

    実装は `agentcore.repolocal.same_repo` に一本化した（S3）。以前は agent-project・
    agent-flow の gitcache・board が別々に持っていて、末尾 .git とスラッシュは 3 者とも
    吸収する一方、**ローカルパスの絶対化は agent-project だけ・小文字化は agent-flow だけ**
    という食い違いがあり、同じ 2 つの URL が経路によって一致したりしなかったりしていた。"""
    return _repolocal.same_repo(a, b)


def _ensure_state_repo_clone(state_repo: str, dst: Path, branch: str) -> bool:
    """状態専用リポジトリを dst へ通常 clone する。成否だけを返す（判断は呼び出し側）。

    branch がリモートに在れば単一ブランチで取り、無ければ普通に clone してそのブランチを
    用意する（空リポジトリの初回起動）。**既存 dst の再利用可否はここでは見ない**——
    origin の同一性検査と失敗時の停止は `_ensure_state_clone`（起動契約の側）が持つ。
    以前ここが「origin が違えば黙って worktree 方式へフォールバック」していたため、移行が
    効いていないことに誰も気付けないまま状態が旧構成へ書かれ続ける事故が起きた。"""
    def _run(*args) -> "subprocess.CompletedProcess | None":
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)
        except (OSError, subprocess.SubprocessError):
            return None

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    r = _run("clone", "-q", "--branch", branch, str(state_repo), str(dst))
    if r is not None and r.returncode == 0:
        return True
    # branch が無い（新規/空リポジトリ等）: 普通に clone してブランチを用意する
    r = _run("clone", "-q", str(state_repo), str(dst))
    if r is None or r.returncode != 0:
        return False
    cur = _git_line(dst, "rev-parse", "--abbrev-ref", "HEAD") or ""
    if cur != branch:
        _run("-C", str(dst), "checkout", "-q", "-B", branch)   # 最初の同期 push で確定する
    return True


def _git_line(cwd: Path, *args: str, env=None) -> "str | None":
    """git を実行して stdout を返す（失敗は None）。"""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None





def _resolved_root(root: "str | None") -> str:
    """プロジェクトルート（状態リポジトリの clone）を絶対パス文字列で返す。

    常駐体の子プロセス名・gc sweeper 名・`engine/status.json` の children[].name はすべて
    この導出を通す——割れると status の子と gc の集計が別名で並び、dashboard が同じ
    プロジェクトを 2 件に見せる。設定ファイルは読まない: S1 以降 root は host.yaml の
    `projects[].root` が宣言する 1 か所だけで、設定ファイル側の `root:` は無効になった。"""
    p = Path(str(root or ".")).expanduser()
    p = p if p.is_absolute() else (Path.cwd() / p)
    return str(p.resolve())


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "memory"


def count_learn_hits(cfg: "Config") -> "dict[str, int]":
    """各 learn ルール（出典 DR id）が auto-resolve で実際に効いた回数を数える（昇格の根拠）。"""
    hits: dict[str, int] = {}
    if not cfg.decisions.exists():
        return hits
    pat = re.compile(r"learned from (?:ltm:)?(?P<src>\S+?):")
    for df in cfg.decisions.glob("*.md"):
        for line in df.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- reason"):
                m = pat.search(line)
                if m:
                    src = m.group("src")
                    hits[src] = hits.get(src, 0) + 1
    return hits


def collect_learnings(cfg: "Config") -> "list[tuple[str, str, str]]":
    """decisions/ の全 learn ルールを (出典id, title, guide) で列挙。"""
    out: list[tuple[str, str, str]] = []
    if not cfg.decisions.exists():
        return out
    for df in sorted(cfg.decisions.glob("*.md")):
        for line in df.read_text(encoding="utf-8").splitlines():
            m = LEARN_RE.match(line)
            if m:
                out.append((df.stem, m.group("title").strip(), m.group("guide").strip()))
    return out


def _promote_marker(cfg: "Config", src: str) -> bool:
    p = decision_path(cfg, src)
    return p.exists() and "- promoted:" in p.read_text(encoding="utf-8")


def write_ltm_memory(mem_dir: Path, title: str, guide: str, src: str, hits: int) -> str:
    """ltm-use 記憶フォーマット（frontmatter＋本文）で1件書き出し、記憶IDを返す。

    本文に機械可読な `- learn: <title> :: <guide>` を残し、recall 時に同じ LEARN_RE で読み戻す。"""
    mem_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(mem_dir.glob("*.md"))) + 1
    date = datetime.now().strftime("%Y-%m-%d")
    memid = f"mem-{datetime.now().strftime('%Y%m%d')}-{n:03d}"
    name = f"{_slug(title)}-{n:03d}"
    summary = guide.replace("\n", " ")[:120]
    body = (
        f"---\n"
        f"id: {memid}\n"
        f"title: \"{title}\"\n"
        f"created: \"{date}\"\n"
        f"updated: \"{date}\"\n"
        f"status: active\n"
        f"scope: home\n"
        f"tags: [{LTM_CATEGORY}, learn]\n"
        f"memory_type: procedural\n"
        f"promoted_from: \"decisions/{src}.md\"\n"
        f"access_count: {hits}\n"
        f"summary: \"{summary}\"\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## コンテキスト\n"
        f"agent-project の判断ノウハウ。出典 decisions/{src}.md で {hits} 回再利用され昇格。\n\n"
        f"## 学び・結論\n"
        f"- learn: {title} :: {guide}\n"
    )
    (mem_dir / f"{name}.md").write_text(body, encoding="utf-8")
    return memid


def promote_learnings(cfg: "Config") -> "list[tuple[str, str]]":
    """効果が再現した learn ルール（hits ≥ promote_threshold・未昇格）を ltm-use home へ昇格。

    返り値 [(出典id, 記憶id)]。ltm 無効や home 未解決なら何もしない（グレースフル no-op）。"""
    mem_dir = ltm_memories_dir(cfg)
    if mem_dir is None:
        return []
    hits = count_learn_hits(cfg)
    seen: set[str] = set()
    promoted: list[tuple[str, str]] = []
    for src, title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold or _promote_marker(cfg, src):
            continue
        seen.add(src)
        memid = write_ltm_memory(mem_dir, title, guide, src, hits[src])
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write(f"- promoted: {memid}（ltm-use home へ昇格 / hits={hits[src]}）\n")
        append_journal(cfg.journal, f"学習昇格: {src} → ltm-use {memid}（hits={hits[src]}）")
        promoted.append((src, memid))
    return promoted


# ---------------------------------------------------------------------------
