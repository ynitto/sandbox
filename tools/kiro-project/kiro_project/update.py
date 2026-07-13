from __future__ import annotations
# update.py — 元 kiro-project.py の 10914-11227 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# 自動アップデート — スキルリポジトリ（main）の更新を取り込み graceful 再起動する
# ---------------------------------------------------------------------------
# doctor と同じ流儀（知能は委譲・操作は決定的）で、本体は「決定的な取り込み」だけを行う:
#   1. git ls-remote でスキルリポジトリ main の最新コミットを得る
#   2. 適用済み SHA（state ファイル）と違えば「更新あり」
#   3. アイドル時に temp 領域へ sparse-checkout（このツールの tools/kiro-project/ だけ）
#   4. install.sh を実行して ~/.local/bin の本体を更新
#   5. 動いていた cwd のまま os.execv で新しい本体へ graceful 再起動
# update_repo 未設定 or update_check_interval<=0 のときは完全に無効（既定 off）。
class _RestartRequested(Exception):
    """自己更新の適用後に graceful 再起動を要求する内部シグナル。
    watch 常駐の finally（レジストリ後始末）を必ず通してから exec するため例外で伝播する。"""


# 更新チェックの最終実行時刻（プロセス内 1 watcher 前提のモジュール状態）。
_UPDATE_LAST_CHECK = {"t": 0.0}


def _update_state_path() -> Path:
    base = os.environ.get("KIRO_STATE_HOME") or os.path.expanduser("~/.kiro")
    return Path(base) / "kiro-project.update.json"


def read_update_state() -> dict:
    try:
        return json.loads(_update_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_update_state(state: dict) -> None:
    p = _update_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def remote_branch_sha(repo: str, branch: str, runner=None) -> "str | None":
    """git ls-remote でリモート branch の先頭コミット SHA を得る（取得不能なら None）。"""
    if not repo:
        return None
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=60))
    try:
        r = run(["git", "ls-remote", repo, f"refs/heads/{branch}"])
    except Exception:  # noqa: BLE001  git 不在・ネットワーク不通・タイムアウト
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    lines = (getattr(r, "stdout", "") or "").strip().splitlines()
    if not lines:
        return None
    sha = lines[0].split()[0].strip()
    return sha if len(sha) >= 7 else None


def find_skill_registry(home: "str | None" = None) -> "str | None":
    """install.py が生成する skill-registry.json を探す（無ければ None）。
    $KIRO_SKILL_REGISTRY（ファイル or ディレクトリ）が指定されていれば**それを権威として使い**
    （フォールバックしない）、未指定なら各エージェントホーム（~/.kiro / ~/.claude 等）を探す。"""
    env = home or os.environ.get("KIRO_SKILL_REGISTRY")
    if env:
        p = os.path.expanduser(env)
        cand = os.path.join(p, "skill-registry.json") if os.path.isdir(p) else p
        return cand if os.path.isfile(cand) else None
    for d in _AGENT_HOME_DIRS:
        c = os.path.join(os.path.expanduser("~"), d, "skill-registry.json")
        if os.path.isfile(c):
            return c
    return None


def registry_update_source(registry: "str | None" = None) -> "tuple[str | None, str | None]":
    """skill-registry.json からスキルリポジトリの (url, branch) を解決する（無ければ (None, None)）。
    repositories の origin（無ければ priority 昇順の先頭）を採り、url が無ければ install_dir
    （インストール元のローカルクローン＝『自動更新の参照元』）にフォールバックする。"""
    path = registry or find_skill_registry()
    if not path or not os.path.isfile(path):
        return (None, None)
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return (None, None)
    repos = reg.get("repositories") or []
    chosen = next((r for r in repos if r.get("name") == "origin"), None)
    if chosen is None and repos:
        chosen = sorted(repos, key=lambda r: r.get("priority", 99))[0]
    if chosen and chosen.get("url"):
        return (chosen["url"], chosen.get("branch") or "main")
    idir = reg.get("install_dir")               # フォールバック: ローカルクローンを直接 clone 元に
    if idir and os.path.isdir(idir):
        return (idir, (chosen.get("branch") if chosen else None) or "main")
    return (None, None)


def resolve_update_target(cfg: "Config") -> "tuple[str, str]":
    """更新元リポジトリと branch を確定する。優先順位 設定の update_repo > skill-registry.json > 無効。
    update_repo 未指定（自動）のときは registry の branch を採用（設定 update_branch が既定 main のまま時）。"""
    repo = cfg.update_repo or ""
    branch = cfg.update_branch or "main"
    if not repo:
        rurl, rbranch = registry_update_source()
        if rurl:
            repo = rurl
            if rbranch and branch == "main":     # 設定で branch を変えていなければ registry を採用
                branch = rbranch
    return repo, branch


def check_update(cfg: "Config", runner=None) -> dict:
    """更新の有無を判定する（取り込みはしない）。戻り値の dict:
      {enabled, repo, branch, remote_sha, applied_sha, available, baseline}
    repo は設定 update_repo か skill-registry.json から解決する。
    初回（applied_sha 未記録）は現在の本体を最新とみなし remote_sha をベースライン記録して
    available=False を返す（無用な初回更新ループを避ける）。"""
    repo, branch = resolve_update_target(cfg)
    info = {"enabled": bool(repo), "repo": repo, "branch": branch, "remote_sha": None,
            "applied_sha": None, "available": False, "baseline": False}
    if not repo:
        return info
    state = read_update_state()
    info["applied_sha"] = state.get("applied_sha")
    remote = remote_branch_sha(repo, branch, runner=runner)
    info["remote_sha"] = remote
    if not remote:
        return info
    if not info["applied_sha"]:
        state["applied_sha"] = remote
        state["baseline_at"] = _now_ts()
        write_update_state(state)
        info["applied_sha"] = remote
        info["baseline"] = True
        return info
    info["available"] = (remote != info["applied_sha"])
    return info


def sparse_checkout_tool(repo: str, branch: str, subdir: str, dest: str, runner=None) -> str:
    """repo の branch から subdir 以下だけを dest へ sparse-checkout し dest/subdir のパスを返す。
    無関係ファイルを取得しないため --no-checkout + blob フィルタ + sparse-checkout を使う。"""
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    r = run(["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none",
             "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:   # blob フィルタ非対応サーバ向けフォールバック
        r = run(["git", "clone", "--no-checkout", "--depth", "1", "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:
        raise RuntimeError(f"git clone 失敗: {(getattr(r, 'stderr', '') or '').strip()[:300]}")

    def g(cmd):
        return run(["git", "-C", dest] + cmd)
    g(["sparse-checkout", "init", "--cone"])
    g(["sparse-checkout", "set", subdir])
    co = g(["checkout", branch])
    if getattr(co, "returncode", 1) != 0:
        raise RuntimeError(f"git checkout 失敗: {(getattr(co, 'stderr', '') or '').strip()[:300]}")
    tool_dir = os.path.join(dest, subdir)
    if not os.path.isdir(tool_dir):
        raise RuntimeError(f"sparse-checkout 後に {subdir} が見つかりません（リポジトリ構成を確認）")
    return tool_dir


def run_installer(tool_dir: str, installer: str = "install.sh", runner=None) -> "tuple[bool, str]":
    """tool_dir 内の installer を実行して本体を更新する。(成功, 末尾出力) を返す。"""
    path = os.path.join(tool_dir, installer)
    if not os.path.isfile(path):
        return False, f"インストーラが見つかりません: {path}"
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    try:
        r = run(["bash", path], cwd=tool_dir)
    except Exception as e:  # noqa: BLE001
        return False, f"インストーラ実行に失敗: {e}"
    out = ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).strip()
    return getattr(r, "returncode", 1) == 0, out[-2000:]


def _tree_digest(root: str) -> str:
    """ツールディレクトリの内容ダイジェスト（.git を除く、相対パス＋内容の sha256）。
    「リポジトリの HEAD は進んだが本体（update_subdir）は変わっていない」を判定する
    （コミット SHA の比較では判別できない）。"""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, root).encode("utf-8"))
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError:
                continue
    return h.hexdigest()


def apply_update(cfg: "Config", info: dict, runner=None) -> bool:
    """temp 領域へ sparse-checkout → install.sh → 適用済み SHA を記録。成功で True。
    temp は必ず後始末する。失敗時は state を変えない（次回再試行）。
    subdir の内容が前回適用時と同一なら installer を実行せずベースラインだけ進めて False:
    direct state-git 構成では自分の state sync push が update_repo の新コミットになるため、
    SHA 比較だけだと「自分の push → 更新検出 → 再起動 → また push」の自己増殖ループになる。"""
    subdir = cfg.update_subdir or TOOL_SUBDIR
    installer = cfg.update_installer or "install.sh"
    tmp = tempfile.mkdtemp(prefix="kiro-project-update-")
    dest = os.path.join(tmp, "repo")
    try:
        tool_dir = sparse_checkout_tool(info["repo"], info["branch"], subdir, dest, runner=runner)
        digest = _tree_digest(tool_dir)
        state = read_update_state()
        if digest == state.get("applied_digest"):
            state["applied_sha"] = info["remote_sha"]
            state["skipped_at"] = _now_ts()
            write_update_state(state)
            print(f"[update] 本体（{subdir}）に変更なし——適用をスキップし "
                  f"ベースラインを {info['remote_sha'][:8]} へ進めました。", flush=True)
            return False
        ok, out = run_installer(tool_dir, installer, runner=runner)
        if not ok:
            print(f"[update] install.sh 失敗（更新を見送り）: {out[-300:]}", flush=True)
            append_journal(cfg.journal, "=== update: install.sh 失敗（更新を見送り・次回再試行）===")
            return False
        state = read_update_state()
        state["applied_sha"] = info["remote_sha"]
        state["applied_digest"] = digest
        state["applied_at"] = _now_ts()
        write_update_state(state)
        print(f"[update] 更新を適用しました（{info['remote_sha'][:8]}）。", flush=True)
        append_journal(cfg.journal, f"=== update: 更新を適用（{info['remote_sha'][:8]}）===")
        return True
    except Exception as e:  # noqa: BLE001  clone/checkout/installer の失敗は次回再試行
        print(f"[update] 更新の取り込みに失敗（次回再試行）: {e}", flush=True)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_self(cwd: "str | None" = None) -> None:
    """更新後の本体へ os.execv で graceful 再起動する。動いていた cwd を保ったまま起動し直す。"""
    if cwd and os.path.isdir(cwd):
        try:
            os.chdir(cwd)
        except OSError:
            pass
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, _self_script()] + sys.argv[1:])


def maybe_self_update(cfg: "Config", runner=None) -> bool:
    """watch のアイドル時に定期的に呼ぶ自己更新チェック。更新を適用したら True
    （呼び出し側は _RestartRequested を投げて finally 後始末の後に restart_self する）。
    update_enabled=false / update_check_interval<=0 で無効。間隔は前回からの経過で律速する。"""
    if not cfg.update_enabled:
        return False
    interval = float(cfg.update_check_interval or 0)
    if interval <= 0:
        return False
    now = time.time()
    # 前回チェック時刻は state ファイルにも持続化して参照する。自己更新は restart_self の
    # 新プロセスになりメモリの時刻がリセットされるため、メモリだけだと再起動直後に即時
    # 再チェック→再適用→再起動…の自己増殖ループになる。
    try:
        persisted = float(read_update_state().get("last_check_at") or 0.0)
    except (TypeError, ValueError):
        persisted = 0.0
    if now - max(_UPDATE_LAST_CHECK["t"], persisted) < interval:
        return False
    _UPDATE_LAST_CHECK["t"] = now
    state = read_update_state()
    state["last_check_at"] = now
    write_update_state(state)
    info = check_update(cfg, runner=runner)
    if not info.get("available"):
        return False
    print(f"[update] スキルリポジトリ {info['branch']} に更新を検出: "
          f"{(info['applied_sha'] or '')[:8]} → {(info['remote_sha'] or '')[:8]}", flush=True)
    return apply_update(cfg, info, runner=runner)


def cmd_update(cfg: "Config", now: bool = False, check: bool = False) -> int:
    """手動アップデート: 更新の有無を確認し、--now で取り込んで再起動する。
    終了コード: 0=最新/ベースライン記録/更新あり表示 / 1=取り込み失敗 / 2=未設定・取得不能。"""
    info = check_update(cfg)
    if not info["enabled"]:
        print("[kiro-project] update: update_repo が未設定です（設定ファイルで指定してください）。",
              file=sys.stderr)
        return 2
    if info["remote_sha"] is None:
        print(f"[kiro-project] update: リモート {info['repo']}@{info['branch']} を取得できませんでした。",
              file=sys.stderr)
        return 2
    if info.get("baseline"):
        print(f"[kiro-project] update: ベースラインを記録しました（{info['remote_sha'][:8]}）。"
              "以降この地点からの更新を検出します。")
        return 0
    if not info["available"]:
        print(f"[kiro-project] update: 最新です（{info['applied_sha'][:8]}）。")
        return 0
    print(f"[kiro-project] update: 更新があります "
          f"{info['applied_sha'][:8]} → {info['remote_sha'][:8]}")
    if check or not now:
        print("  取り込むには `kiro-project update --now` を実行してください。")
        return 0
    if apply_update(cfg, info):
        print("  install.sh を実行して更新しました。再起動します。")
        restart_self(_START_CWD or os.getcwd())   # 戻らない
    if read_update_state().get("applied_sha") == info.get("remote_sha"):
        print("  本体（update_subdir）に変更が無かったため適用をスキップし、ベースラインだけ進めました。")
        return 0
    print("  更新の取り込みに失敗しました（ログを確認してください）。", file=sys.stderr)
    return 1


