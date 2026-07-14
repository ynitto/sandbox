from __future__ import annotations
# state.py — 元 agent-project.py の 1553-2006 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 状態 worktree — 状態の読み書きを、本体の作業ツリーから切り離す
#
# agent-project は watch 中 5 秒ごとに journal.md / status.json / run-log.jsonl / project.json を
# 書き換える。root を本体リポジトリの中（例 <repo>/.agent-project）に置くと:
#   ・人の git status が永久に dirty になり、人の作業と混ざる
#   ・人やツールの git 操作（stash / rebase / pull --autostash）が、書き込みの最中の状態ファイルを
#     巻き込んで壊す（実際に project.json がコンフリクトマーカーで JSON として読めなくなった）
#   ・viewer の git 同期が本体を toplevel と解釈し、bus/ の実行記録まで本体の index へ流れ込む
# そこで、同じリポジトリの専用ブランチ（state_branch）の worktree を **切りっぱなし** で用意し、
# 状態の読み書きをそこへ逃がす。設定の root は本体を指したままでよい（人が書く自然な形）。
# 本体の作業ツリー・index は一切汚れず、状態の履歴は同じリポジトリに残って共有もできる。
# root が git 管理外（普通のディレクトリ）なら何もしない。
# ---------------------------------------------------------------------------
def _git_toplevel_of(p: Path) -> "Path | None":
    """p を含む git 作業ツリーのトップ（git 管理外なら None）。"""
    try:
        r = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _sparse_state_worktree(wt: Path, rel: str) -> bool:
    """状態 worktree を rel（= 状態ディレクトリ）だけの sparse checkout にする。冪等。

    この worktree で使うのは状態ディレクトリだけなのに、既定では **リポジトリ全体** が
    チェックアウトされる（tools/ や docs/ の丸ごとコピーが隣に生える）。ディスクの無駄という
    より、人が worktree 側の tools/ を本物と思って編集してしまう事故が怖い（そこでの変更は
    agent-state ブランチに乗るだけで、main には決して届かない）。

    sparse は **作業ツリーの見え方** を変えるだけで、ブランチの中身（HEAD のツリー）は完全な
    ままなので、状態のコミット・push・バックアップはどれも影響を受けない。失敗しても致命では
    ないので（全部チェックアウトされるだけ）、黙って False を返す。"""
    try:
        i = subprocess.run(["git", "-C", str(wt), "sparse-checkout", "init", "--cone"],
                           capture_output=True, text=True, timeout=120)
        if i.returncode != 0:
            return False                              # 古い git 等 → 従来どおり全チェックアウト
        s = subprocess.run(["git", "-C", str(wt), "sparse-checkout", "set", rel],
                           capture_output=True, text=True, timeout=180)
        return s.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_state_worktree(top: Path, wt: Path, branch: str, rel: str = "") -> bool:
    """状態用 worktree を切りっぱなしで用意する（既にあれば再利用）。用意できたら True。
    rel を渡すと、その配下だけの sparse checkout にする（リポジトリ全体を複製しない）。"""
    if (wt / ".git").exists():
        if rel:
            _sparse_state_worktree(wt, rel)           # 既存 worktree にも後追いで効かせる（冪等）
        return True                                   # 既に切ってある＝そのまま使う
    try:
        has_branch = subprocess.run(
            ["git", "-C", str(top), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True, timeout=30).returncode == 0
        # --no-checkout で骨だけ作り、sparse を効かせてから中身を出す（一度も全展開しない）
        add = ["git", "-C", str(top), "worktree", "add", "--no-checkout"]
        add += ([str(wt), branch] if has_branch else ["-b", branch, str(wt)])
        r = subprocess.run(add, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        print(f"[agent-project] 状態 worktree を用意できません（本体に書きます）: "
              f"{r.stderr.strip()[:200]}", file=sys.stderr)
        return False
    if rel:
        _sparse_state_worktree(wt, rel)
    try:
        subprocess.run(["git", "-C", str(wt), "checkout"], capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _migrate_state_into_worktree(src: Path, dst: Path) -> bool:
    """本体側に残っている既存の状態を worktree へ引っ越す（初回だけ）。移したら True。

    dst に既に中身があれば触らない（worktree 側が正）。src が空/不在なら何もしない。"""
    if not src.is_dir() or any(src.iterdir()) is False:
        return False
    if dst.is_dir() and any(dst.iterdir()):
        return False                                  # 既に worktree 側で運用中
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
        shutil.rmtree(src, ignore_errors=True)        # 本体側は空にする（二重管理を作らない）
    except OSError as e:
        print(f"[agent-project] 状態の引っ越しに失敗（本体に書きます）: {e}", file=sys.stderr)
        return False
    return True


def _redirect_root_to_state_worktree(root: Path, wt_dir: str,
                                     branch: str) -> "tuple[Path, Path | None]":
    """状態の実書き込み先を決める。(実効 root, 本体リポジトリのトップ or None) を返す。

    git 管理外・worktree を作れない、のいずれでも本体の root をそのまま返す（従来動作）。
    「worktree へ逃がすか」の設定は持たない: 逃がさない選択は本体を dirty にし、状態を git 管理
    できなくするだけで（commit_state は state_top なしでは動かない）、バックアップも取れない。
    自動フォールバックがこの 2 ケースを拾うので、人が選ぶ余地は要らない。"""
    top = _git_toplevel_of(root if root.is_dir() else root.parent)
    if top is None:
        return root, None                             # git 管理外 → そのまま
    try:
        rel = root.resolve().relative_to(top.resolve())
    except ValueError:
        return root, None                             # 既にリポジトリ外を指している
    wt = (Path(wt_dir).expanduser().resolve() if wt_dir
          else (top.parent / f"{top.name}-{branch}"))
    # 使うのは状態ディレクトリ（rel）だけ。リポジトリ全体を複製しない（sparse checkout）。
    if not _ensure_state_worktree(top, wt, branch, rel.as_posix()):
        return root, None
    dst = wt / rel
    _migrate_state_into_worktree(root, dst)
    return dst, top


# 状態のうち「人の判断・計画が動いた」もの。これが変わったら即コミットする（履歴として意味がある）。
_STATE_SIGNIFICANT = ("charter.md", "charters", "backlog", "needs", "decisions", "repos.json",
                      "policy.md", "rules.md", "archive", "DELIVERY.md", "specs",
                      # 設定も人の判断。ここに無いと、どちらのリストにも属さない設定変更は
                      # 「変化なし」と読まれてコミットされず、鏡との差分が延々残る。
                      "agent-flow.yaml", "agent-project.yaml")
# 実行の副産物。watch が回る限り数秒ごとに変わるので、まとめてコミットする（履歴を秒で埋めない）。
_STATE_NOISE = ("journal.md", "status.json", "run-log.jsonl", "project.json",
                "bus", "claims", "flow-archive", "commands", "inbox", "journal-archive")
_last_state_commit: float = 0.0


def _state_changed(root: Path, names) -> bool:
    """worktree の未コミット変更に names 由来のものが含まれるか。"""
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *names],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _git_line(cwd: Path, *args: str, env=None) -> "str | None":
    """git を実行して stdout を返す（失敗は None）。バックアップ経路はここで失敗を吸収する。"""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                           timeout=120, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


_BACKUP_MSG = "agent-project: 状態をバックアップ（自動）"


def _is_pushed(top: Path, branch: str, rev: str) -> bool:
    """rev が origin/<branch> の先祖か（＝ push 済みで書き換えてはいけないか）。

    判定できないとき（リモート未取得・git の失敗）は True＝「push 済み」と読み、書き換えない。
    安全側に倒す: 誤って push 済みの履歴を潰すより、バックアップコミットが 1 つ余分に積まれる
    ほうがましなので。"""
    try:
        r = subprocess.run(["git", "-C", str(top), "merge-base", "--is-ancestor",
                            rev, f"origin/{branch}"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode == 0


# 本体側 <repo>/.agent-project にあっても、**機械が絶対に書かない**ファイル＝人だけが所有する。
# ここに載せてよいのは「agent-project 自身が一度も書き換えないもの」に限る。charter.md / policy.md /
# repos.json は人が書くものに見えるが、機械も書く（approve が policy に deny を積む、repos.json は
# charter から再生成される）。それらを取り込み対象にすると、鏡が遅れているときに古い内容で
# live な状態を巻き戻してしまう。設定ファイルだけが安全。
_HUMAN_OWNED_STATE_FILES = ("agent-flow.yaml", "agent-project.yaml")


def adopt_mirror_edits(top: "Path", wt_top: "Path", branch: str, rel: str) -> "list[str]":
    """本体側 <repo>/.agent-project の **設定ファイルへの人の編集** を状態 worktree へ取り込む。

    人にとって正本は <repo>/.agent-project（リポジトリを開けばそこにある）だが、状態の読み書きは
    worktree へ逃がしてあるので、本体側を編集しても **何も起きない**。実際 agent-flow.yaml の
    agent 切替（evaluator を codex へ）が丸ごと無視されていた。

    ただし「正本ブランチとの差分＝人の編集」とは読めない。鏡は正本ブランチから遅れうる
    （バックアップは意味のある変化のときしか走らない）ため、機械が書くファイルの差分は
    **古い鏡** であることのほうが多く、取り込めば live な状態を巻き戻す（実際 doing のタスクが
    proposed へ戻り、削除済みの cancel が復活した）。だから _HUMAN_OWNED_STATE_FILES に限る。"""
    if _git_line(top, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        return []                          # 別ブランチ作業中 → その差分は状態の編集ではない
    names = _git_line(top, "diff", "--name-only", branch, "--", rel) or ""
    adopted: list[str] = []
    for p in names.splitlines():
        p = p.strip()
        if not p or p.rsplit("/", 1)[-1] not in _HUMAN_OWNED_STATE_FILES:
            continue
        src, dst = top / p, wt_top / p
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            adopted.append(p)
        except OSError:
            continue                       # 取り込めなくても本業は止めない（鏡は次回また差分に出る）
    return adopted


def _sync_backup_mirror(top: "Path", wt_top: "Path", branch: str, rel: str) -> "list[str]":
    """本体が正本ブランチを開いているなら、その <rel> の index・作業ツリーを branch へ揃える。
    揃える **前に** 人の編集を状態 worktree へ取り込む（でないと checkout で消える）。

    「新しいコミットは不要（＝バックアップ済み）」の経路でも必ず呼ぶこと。鏡がずれたまま
    詰む状態はまさにそこで止まり続けるからで、コミットが要らないときこそ揃え直す必要がある。
    パス限定 checkout なので、人が本体で触っている <rel> の外には影響しない。"""
    if _git_line(top, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        return []
    adopted = adopt_mirror_edits(top, wt_top, branch, rel)
    _git_line(top, "checkout", "-q", branch, "--", rel)
    return adopted


def sync_mirror_edits(cfg: "Config") -> "list[str]":
    """本体側 <repo>/.agent-project への人の編集を状態へ取り込む（パス開始時に呼ぶ）。

    backup_state に任せると「意味のある状態変化があったとき」にしか走らない。設定だけを
    書き換えて再起動した人は、それが効くまで延々待たされる（＝効かないと誤解する。実際
    agent-flow.yaml の evaluator 切替が無視され続けた）。だから毎パスの頭で取り込む。"""
    top, branch = cfg.state_top, (cfg.state_backup_branch or "").strip()
    if top is None or not branch:
        return []
    root = cfg.backlog.parent
    wt_top = _git_toplevel_of(root)
    if wt_top is None:
        return []
    try:
        rel = root.resolve().relative_to(wt_top.resolve()).as_posix()
    except ValueError:
        return []
    adopted = adopt_mirror_edits(top, wt_top, branch, rel)
    _journal_adopted(cfg, adopted)
    return adopted


def _journal_adopted(cfg: "Config", adopted: "list[str]") -> None:
    """人の編集を取り込んだことを journal に残す。黙って取り込むと、本体側を編集した人は
    それが効いたのか無視されたのか分からない（実際 agent-flow.yaml の切替が無視されていた）。"""
    if adopted:
        append_journal(cfg.journal, "本体側 .agent-project への編集を状態へ取り込みました: "
                       + ", ".join(sorted(adopted)[:5]))


def backup_state(cfg: "Config") -> bool:
    """状態 worktree の最新を、正本ブランチ（既定 main）へバックアップする。バックアップしたら True。

    これは共有ではなくバックアップ。だから次の性質を守る:

    ・**本体の作業ツリー・index には一切触らない。** 人がどのブランチで何をしていても壊さないよう、
      plumbing（read-tree/commit-tree/update-ref）で正本ブランチの ref だけを進める。本体が
      その正本ブランチを開いていた場合だけ、最後に .agent-project を checkout して作業ツリーの
      表示を揃える（人の他ファイルの変更には触れないパス限定 checkout）。
    ・**1 同期 = 1 コミット（squash）。** worktree 側には 5 秒おきの細かい履歴が積まれるが、それは
      持ち込まない。正本ブランチには「その時点の状態」だけを載せる。
    ・**失敗しても実行は止めない。** バックアップの失敗で本業（バックログ消化）を落とさない。
      ロック競合・並行 push・権限のどれで転んでも False を返して黙って続ける。
    ・**他リポジトリの agent-project と干渉しない。** 触るのは自分のリポジトリの ref だけで、
      update-ref は取得時の値を expect して撃つので、割り込みがあれば撃ち負けて次回に回す。
    """
    top, branch = cfg.state_top, (cfg.state_backup_branch or "").strip()
    if top is None or not branch:
        return False                       # worktree に逃がしていない or バックアップ無効
    root = cfg.backlog.parent              # 状態 worktree 側の実効 root（= .agent-project）
    wt_top = _git_toplevel_of(root)
    if wt_top is None:
        return False
    try:
        rel = root.resolve().relative_to(wt_top.resolve()).as_posix()
    except ValueError:
        return False
    # worktree の HEAD に載っている状態ツリー（commit_state が直前にコミットしている）
    state_tree = _git_line(wt_top, "rev-parse", f"HEAD:{rel}")
    if not state_tree:
        return False
    old = _git_line(top, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if not old:
        return False                       # 正本ブランチが無い（別運用）→ 触らない
    if _git_line(top, "rev-parse", "--verify", "--quiet", f"{branch}:{rel}") == state_tree:
        _journal_adopted(cfg, _sync_backup_mirror(top, wt_top, branch, rel))
        return False                       # 中身が同じ＝バックアップ済み（空コミットを作らない）

    # 本体の作業ツリーを揃えてよいかは ref を進める **前** に決める。進めた後に見ると、ref が
    # 進んだこと自体が「作業ツリーとの差分」として現れ、それを人の編集と読み違えてしまう。
    #
    # 「<rel> に差分があるなら人の編集かもしれないので触らない」とはしない。状態を worktree へ
    # 逃がしている以上（backup_state はその時しか動かない）、本体側の <rel> は**編集面ではなく
    # バックアップの鏡**で、人も agent-project もそこを読み書きしない。差分を人の編集と見て避けると
    # 自己永続的に詰む: 一度ずれた瞬間 adopt が永久に False になり、二度と同期されず、
    # **古いスナップショットが index に staged のまま居座る**。その状態で誰かが main で
    # git commit（パス指定なし）を打つと、バックアップが古い状態へ巻き戻る（実際そうなった）。
    adopt = _git_line(top, "symbolic-ref", "--quiet", "--short", "HEAD") == branch

    # 正本ブランチのツリーの <rel> だけを差し替えた新ツリーを、一時 index の上で組む。
    # 本体の index（人のステージ）を汚さないため GIT_INDEX_FILE を切り替える。
    tmp_index = None
    try:
        fd, tmp_index = tempfile.mkstemp(prefix="kiro-backup-idx-")
        os.close(fd)
        os.unlink(tmp_index)               # git に作らせる（空ファイルだと read-tree が拒む）
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
        if _git_line(top, "read-tree", branch, env=env) is None:
            return False
        _git_line(top, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", rel, env=env)
        if _git_line(top, "read-tree", f"--prefix={rel}", state_tree, env=env) is None:
            return False
        new_tree = _git_line(top, "write-tree", env=env)
        if not new_tree:
            return False
        # 直前が自分の未 push なバックアップコミットなら、その親に付け替えて **置き換える**
        # （amend 相当）。毎回 old を親にして積むと、同期のたびに 1 コミット増え、正本ブランチが
        # 「状態をバックアップ（自動）」で埋まる（実際 18 件積み上がった）。バックアップは履歴では
        # なく「その時点の状態」なので、まとめてよい。**push 済みの履歴は書き換えない**。
        parent = old
        if (_git_line(top, "log", "-1", "--format=%s", old) or "") == _BACKUP_MSG \
                and not _is_pushed(top, branch, old):
            parent = _git_line(top, "rev-parse", f"{old}^") or old
        commit = _git_line(top, "commit-tree", new_tree, "-p", parent,
                          "-m", _BACKUP_MSG, env=env)
        if not commit:
            return False
        # 取得時の値を expect する。割り込みで branch が進んでいたら撃ち負けて次回に回す。
        if _git_line(top, "update-ref", f"refs/heads/{branch}", commit, old) is None:
            return False
    finally:
        if tmp_index and os.path.exists(tmp_index):
            os.unlink(tmp_index)

    # 本体がその正本ブランチを開いていたなら、作業ツリーの表示も揃える（人が差分を見ずに済む）。
    # 人が .agent-project を手で編集していたなら触らない（その変更を消さない）。
    if adopt:
        _journal_adopted(cfg, _sync_backup_mirror(top, wt_top, branch, rel))
    if cfg.state_push:
        _git_line(top, "push", "-q", "origin", f"refs/heads/{branch}:{branch}")
    return True


def commit_state(cfg: "Config", force: bool = False) -> bool:
    """状態 worktree の変更をコミットする（軽微な変化はまとめる）。コミットしたら True。

    watch は 5 秒ごとに journal.md / status.json / run-log.jsonl / project.json を書き換える。
    そのたびにコミットすると履歴が秒単位で埋まって読めない。人の判断や計画が動いたとき
    （charter / backlog / needs / decisions …）は即コミットし、実行の副産物だけの変化は
    state_commit_interval（既定 300 秒）でまとめる。

    コミットは worktree の中だけで完結する（本体の作業ツリー・index には触らない）。"""
    global _last_state_commit
    if not cfg.state_commit or cfg.state_top is None:
        return False                       # 状態を worktree へ逃がしていない＝git 管理しない
    root = cfg.backlog.parent
    significant = _state_changed(root, _STATE_SIGNIFICANT)
    if not significant:
        if not force and (time.time() - _last_state_commit) < cfg.state_commit_interval:
            return False                   # 副産物だけの変化 → まとめる（まだ間隔内）
        if not _state_changed(root, _STATE_NOISE):
            return False                   # そもそも変化なし
    msg = ("agent-project: 状態を更新" if significant
           else "agent-project: 実行ログを更新（自動）")
    # add / diff / commit はすべて自分の root 配下に限定する。同じリポジトリに複数のプロジェクト
    # （<repo>/.agent-project と <repo>/sub/.agent-project）があると state worktree は共有されるため、
    # パスを限定しないと index 全体をコミットしてしまう: 隣のプロジェクトが add した直後に自分が
    # commit すると、相手の状態を自分のコミットに巻き込み、相手は「ステージに何も乗らない」と
    # 判断して自分のコミットを作れなくなる（＝相手の状態がバックアップされない）。
    #
    # 除外契約は DirectStateGit と同一に保つ: claims/（ホスト局所の実行権）と .state-git*
    # （管理クローンの残骸）はコミットしない。ここが `add -A .` で全部拾っていたのが
    # 「複数の書き手の除外規則の食い違い → tracked だが commit されないファイル → 統合が
    # 永久に詰まる」の起点だった。
    pathspec = [".", ":(exclude,glob)**/claims/**", ":(exclude,glob)claims/**",
                ":(exclude,glob)**/.state-git*", ":(exclude,glob).state-git*"]
    try:
        add = subprocess.run(["git", "-C", str(root), "add", "-A", "--", *pathspec],
                             capture_output=True, text=True, timeout=120)
        if add.returncode != 0:
            return False
        if subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--",
                           *pathspec], capture_output=True, timeout=60).returncode == 0:
            return False                   # ステージに何も乗らなかった
        c = subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg, "--", *pathspec],
                           capture_output=True, text=True, timeout=120)
        if c.returncode != 0:
            return False
        _last_state_commit = time.time()
        if cfg.state_push:
            subprocess.run(["git", "-C", str(root), "push", "-q", "origin",
                            f"HEAD:{cfg.state_branch}"], capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return False
    # 人の判断・計画が動いたときだけ正本ブランチへバックアップする。実行の副産物（journal /
    # status.json / bus）は 5 秒ごとに変わるので、正本へ流すとコミットが埋まり、本体で作業して
    # いる人の git status も落ち着かない。それらは worktree 側の履歴に留める。
    if significant:
        backup_state(cfg)                  # 失敗しても実行は止めない（中で吸収する）
    return True


def _resolved_root(root: "str | None", config: "str | None" = None) -> str:
    """start/stop/restart 用にプロジェクトルートを絶対パス文字列で返す。
    build_config の root 計算と一致させ、稼働インスタンスの記録 root と突き合わせる。
    --root 未指定なら設定ファイルの root を読む（daemon 子プロセスは resolve_config 経由で
    設定の root に付くため、ここが cwd 固定だと重複検出・stop の照合が外れうる）。
    root は cwd 相対で解決する（workdir は root 配下の作業場所であってアンカーではない）。"""
    if not root:
        path = _find_config(config)
        filecfg = _load_config_file(path) if path else {}
        root = str(filecfg.get("root") or ".")
    p = Path(root).expanduser()
    p = p if p.is_absolute() else (Path.cwd() / p)
    return str(p.resolve())


def cmd_start(root: "str | None" = None, config: "str | None" = None,
              force: bool = False, extra: "list | str | None" = None) -> int:
    """`run --watch` を切り離して常駐起動する（detached）。重複監視は既定で拒否（--force で許可）。
    監視対象は cwd（または --root/設定ファイルの root）のプロジェクト 1 つ。"""
    expected = _resolved_root(root, config)
    me = socket.gethostname()
    dup = [r for r in list_instances(prune=True, extra=extra)
           if str(r.get("root", "")) == expected and str(r.get("host", "")) == me]
    if dup and not force:
        print(f"既に root={expected} を監視中です（pid={dup[0]['pid']}）。重複起動は --force、"
              f"再起動は restart を使ってください。", file=sys.stderr)
        return 1
    child = [sys.executable, _self_script(), "run", "--watch"]
    if force:
        child += ["--force"]     # 子（run --watch）も重複を弾くので、人の許可を伝える
    if root:
        child += ["--root", root]
    if config:
        child += ["--config", config]
    for r in _split_registry(extra):            # 共有レジストリを子 daemon にも引き継ぐ
        child += ["--registry", r]
    log_dir = resolve_state_home() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"{_slug(expected)}.log"
        logf = open(log, "a", encoding="utf-8")
    except OSError:
        log, logf = None, subprocess.DEVNULL
    try:
        proc = subprocess.Popen(child, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"起動に失敗しました: {e}", file=sys.stderr)
        return 1
    finally:
        if hasattr(logf, "close"):
            try:
                logf.close()
            except OSError:
                pass
    deadline = time.time() + 5.0                # 登録（レジストリ出現）を確認
    registered = False
    while time.time() < deadline:
        if any(int(r.get("pid", -1)) == proc.pid for r in list_instances(prune=False, extra=extra)):
            registered = True
            break
        if not _pid_alive(proc.pid):
            break
        time.sleep(0.2)
    status = "起動しました" if (registered and _pid_alive(proc.pid)) else \
             "起動しましたが登録未確認（log を確認してください）"
    print(f"{status} pid={proc.pid} root={expected}" + (f" log={log}" if log else ""))
    return 0 if _pid_alive(proc.pid) else 1


def cmd_restart(root: "str | None" = None, config: "str | None" = None,
                extra: "list | str | None" = None) -> int:
    """同じプロジェクト root の監視を停止してから起動し直す。"""
    proot = _resolved_root(root, config)
    if select_instances(root=proot, extra=extra):
        cmd_stop(root=proot, extra=extra)
    return cmd_start(root=root, config=config, force=True, extra=extra)



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
