from __future__ import annotations
# instances.py — 元 agent-project.py の 1086-1552 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 稼働インスタンスのレジストリ（外部から「いま見ているフォルダ」を発見可能にする）
#
# run（特に --watch 常駐）中、監視中のルートと OS/WSL 情報を共通 home に記録する。
# 外部の操作者（agent-project スキル等）が `instances` で発見し、同じフォルダへ
# 読み書きできる。プロセスは WSL で動き操作側は Windows/WSL という構成を想定し、
# 可能なら Windows パス（wslpath -w）も併記する。
# ---------------------------------------------------------------------------
def watch_lock_path(cfg: "Config") -> Path:
    """プロジェクトの監視ロック（1 root = 1 監視ループ）の置き場。

    鍵は**実効 root の realpath**（`--root` の綴り・symlink・相対パスの違いを吸収する）。
    ロック置き場は状態ホーム配下で、同じ PC の全プロセスが同じファイルを掴む。"""
    root = str((getattr(cfg, "source_root", None) or cfg.backlog.parent).resolve())
    key = hashlib.sha1(os.path.realpath(root).encode("utf-8")).hexdigest()[:16]
    d = resolve_state_home(use_env=not getattr(cfg, "profile_mode", False)) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"watch-{key}.lock"


def acquire_watch_lock(cfg: "Config"):
    """同一プロジェクトの二重監視を OS の排他で防ぐ（取れたら lock file、取れなければ None）。

    以前は instances レジストリの「心拍が新しいレコードがあるか」で判定していたが、それは
    **推定**なので窓の隙間（心拍 ttl は長い LLM 実行中に切れる）で二重起動が通る。同じ
    backlog を 2 ループが奪い合うと、同じタスクを二重実行して状態ファイルと決定記録を
    互いに上書きする。OS のロックなら隙間が無い（実装計画 W1-9）。

    実装は `agent-flow` の daemon singleton ロックと同じ 3 段（R1: 同じ仕事の実装を 1 つの
    流儀に揃える）——flock → msvcrt → PID 生存フォールバック。**ロックは fd に紐づく**ので、
    返した file を掴んだまま監視ループを回し、終了時に `release_watch_lock` で解放する。"""
    lock_path = watch_lock_path(cfg)
    # 既存ホルダの pid を消さないよう truncate せず開く（ロック取得後にだけ書く）
    lock_file = os.fdopen(os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644), "r+")
    if fcntl is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return None
    elif msvcrt is not None:
        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            return None
    else:  # pragma: no cover — fcntl も msvcrt も無い環境のみ
        # PID 生存フォールバック。TOCTOU（2 プロセスが同時に判定を通過する）が残るが、
        # OS のロックが無い環境では他に手が無い（agent-flow と同じ割り切り）。
        try:
            lock_file.seek(0)
            raw = (lock_file.read() or "").strip()
            if raw:
                old = int(raw)
                if old != os.getpid() and _pid_alive(old):
                    lock_file.close()
                    return None
        except (ValueError, OSError):
            pass
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def watch_lock_holder(cfg: "Config") -> int:
    """監視ロックを保持しているプロセスの pid（不明・未保持は 0）。案内文に載せる用。"""
    try:
        raw = watch_lock_path(cfg).read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return 0
    return pid if pid != os.getpid() and _pid_alive(pid) else 0


def release_watch_lock(lock_file) -> None:
    """監視ロックを解放して fd を閉じる（自己更新の execv 前にも呼ぶ——flock は fd に
    紐づくため、解放しないと再起動後の再取得が自分自身と衝突して二重起動扱いになる）。"""
    # 二重解放は無害に済ませる（正常終了の finally と execv 前の明示解放が重なりうる）。
    if lock_file is None or lock_file.closed:
        return
    try:
        if fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        elif msvcrt is not None:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    except (OSError, ValueError):
        pass
    with contextlib.suppress(OSError, ValueError):
        lock_file.close()


def resolve_state_home(use_env: bool = True) -> Path:
    """インスタンス・レジストリ等の置き場: 環境変数 AGENT_PROJECT_HOME → ~/.agent-project。"""
    raw = (os.environ.get("AGENT_PROJECT_HOME") if use_env else None) or "~/.agent-project"
    return Path(raw).expanduser()


def detect_runtime() -> dict:
    """実行環境（linux / wsl / windows / darwin）と WSL ディストロ名を判定する。"""
    info: dict = {"runtime": "linux", "wsl_distro": None}
    distro = os.environ.get("WSL_DISTRO_NAME")
    is_wsl = False
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            is_wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    if distro or is_wsl:
        info["runtime"], info["wsl_distro"] = "wsl", distro
    elif sys.platform.startswith("win"):
        info["runtime"] = "windows"
    elif sys.platform == "darwin":
        info["runtime"] = "darwin"
    return info



def _self_script() -> str:
    """この CLI を再実行できる実体の絶対パス（子プロセス起動・graceful 再起動に使う）。

    パッケージ化後の __file__ は agent_project/instances.py を指し単体実行できないため:
      1) パッケージ隣の shim agent-project.py があればそれ（リポジトリ/開発/テスト実行）。
         テスト実行時の sys.argv[0] は pytest 本体を指すので argv[0] は使えない。
      2) shim が無ければ（zipapp 配布時）起動に使われた実体 sys.argv[0]（zipapp）を再実行する。
    """
    shim = Path(__file__).resolve().parent.parent / "agent-project.py"
    if shim.exists():
        return str(shim)
    arg0 = sys.argv[0] if sys.argv else ""
    if arg0 and Path(arg0).exists():
        return str(Path(arg0).resolve())
    return str(Path(__file__).resolve())




def _flow_pids_for_bus(bus: Path) -> "list[int]":
    """自分の bus を回している agent-flow プロセスの pid（POSIX のみ。取れなければ空）。

    agent-project は agent-flow を `--bus <root>/bus` で起動するので、コマンドラインの bus パスで
    「自分のもの」を特定できる。ps が無い環境（Windows 素の cmd 等）では空を返し、従来動作に倒す。"""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    me, target, out = os.getpid(), str(Path(bus).resolve()), []
    for line in r.stdout.splitlines():
        pid_s, _, args = line.strip().partition(" ")
        if "agent-flow" not in args or target not in args:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid != me:
            out.append(pid)
    return out


def _expire_run_leases(cfg: "Config") -> int:
    """非終端 run の生存リースを失効させる（失効させた数）。

    残骸を止めた直後、meta のリースはまだ未来を指している（最後の heartbeat + 猶予）。そのままだと
    消費者は「まだ実行中」と読み続け、続きから再開せず新しい run を作ってしまう。駆動していた
    プロセスを止めた以上どのリースも当てにならないので、明示的に失効させて「停滞」と読ませる。"""
    n = 0
    try:
        runs = sorted((cfg.bus / "runs").iterdir())
    except OSError:
        return 0
    for d in runs:
        f = d / "meta.json"
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(meta.get("status") or "") in _FLOW_TERMINAL:
            continue
        if meta.get("orch_lease_until") is None:
            continue
        meta["orch_lease_until"] = 0.0        # 失効＝以後は age ではなくリースで「停滞」と判定される
        try:
            write_json_atomic(str(f), meta)
            n += 1
        except OSError:
            continue
    return n


def reap_orphan_flow(cfg: "Config") -> int:
    """自分の bus に居残った agent-flow（前世代の残骸）を止める。止めたプロセス数を返す。

    agent-project がクラッシュ（kill -9 / 電源断 / OOM）すると、stop を通らないので agent-flow の
    orchestrator と worker が生き残る。残った orchestrator は run の生存リースを更新し続けるため、
    次に起動した agent-project はその run を「実行中」と読み、**続きから再開せず新しい run を作り
    直す**。結果、同じタスクを二重に実行し、同じ作業ブランチへ両方が push しあう（実際に起きた:
    17/23 まで進んだ run を捨てて 1/20 からやり直した）。

    同じ bus を使う agent-project は 1 つだけ（cmd_run が重複起動を弾く）ので、その bus を回して
    いる agent-flow は全て自分の前世代の残骸として刈ってよい。"""
    pids = _flow_pids_for_bus(cfg.bus)
    if not pids:
        return 0
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)        # グループには自分（agent-project）が混ざりうるので個別に
        except OSError:
            pass
    deadline = time.time() + 5.0
    while time.time() < deadline and any(_pid_alive(p) for p in pids):
        time.sleep(0.1)
    for p in pids:
        if _pid_alive(p) and hasattr(signal, "SIGKILL"):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
    _expire_run_leases(cfg)
    return len(pids)
