from __future__ import annotations
# instances.py — 元 kiro-project.py の 1086-1552 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
# 稼働インスタンスのレジストリ（外部から「いま見ているフォルダ」を発見可能にする）
#
# run（特に --watch 常駐）中、監視中のルートと OS/WSL 情報を共通 home に記録する。
# 外部の操作者（kiro-project スキル等）が `instances` で発見し、同じフォルダへ
# 読み書きできる。プロセスは WSL で動き操作側は Windows/WSL という構成を想定し、
# 可能なら Windows パス（wslpath -w）も併記する。
# ---------------------------------------------------------------------------
def resolve_state_home() -> Path:
    """インスタンス・レジストリ等の置き場: 環境変数 KIRO_PROJECT_HOME → ~/.kiro-project。"""
    raw = os.environ.get("KIRO_PROJECT_HOME") or "~/.kiro-project"
    return Path(raw).expanduser()


def instances_dir() -> Path:
    return resolve_state_home() / "instances"


# リモート（別ホスト）レコードは PID が当てにならないので heartbeat の鮮度で生死を見る。
INSTANCE_TTL = 90.0           # heartbeat からこの秒数を超えたリモートレコードは「停止」とみなす
REMOTE_PRUNE_GRACE = 86400.0  # これより古い（=長期間死んでいる）リモートレコードは誰が掃除してもよい


def resolve_registry_dirs(extra: "list | str | None" = None) -> "list[Path]":
    """レコードを書く/読むディレクトリ群。先頭が自分の書き込み先（ローカル home）。
    KIRO_PROJECT_REGISTRY（os.pathsep 区切り）と extra（--registry）を共有レジストリとして加える。
    共有先を NFS / 同期フォルダ / git バスのチェックアウト等にすると、別ホスト同士が相互発見できる
    （core は決定的なファイル操作のみ。ネットワークは共有先の仕組みが担うので不変条件④⑤を保つ）。"""
    dirs = [instances_dir()]
    seen = {dirs[0]}
    sources: list[str] = []
    env = os.environ.get("KIRO_PROJECT_REGISTRY")
    if env:
        sources += env.split(os.pathsep)
    if extra:
        sources += extra if isinstance(extra, list) else [extra]
    for s in sources:
        s = (s or "").strip()
        if not s:
            continue
        p = Path(s).expanduser()
        if p not in seen:
            dirs.append(p)
            seen.add(p)
    return dirs


def _split_registry(arg: "list | str | None") -> "list[str]":
    """--registry の値（os.pathsep 区切り文字列 / 繰り返しリスト）を正規化した list にする。"""
    if not arg:
        return []
    items = arg if isinstance(arg, list) else [arg]
    out: list[str] = []
    for it in items:
        out += [s for s in str(it).split(os.pathsep) if s.strip()]
    return out


def _instance_filename(rec: dict) -> str:
    """ホスト修飾のレコードファイル名（共有レジストリで別ホストの同一 PID と衝突しないように）。"""
    host = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(rec.get("host", "host")) or "host")
    return f"{host}-{rec.get('pid', 0)}.json"


def _record_alive(rec: dict) -> bool:
    """レコードの生死。ローカルホストは PID で、別ホストは heartbeat の鮮度（TTL）で判定する。"""
    if str(rec.get("host", "")) == socket.gethostname():
        return _pid_alive(int(rec.get("pid", -1)))
    hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
    ttl = float(rec.get("ttl", INSTANCE_TTL) or INSTANCE_TTL)
    return (time.time() - hb) <= max(ttl, INSTANCE_TTL)


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


def to_windows_path(p: "str | Path") -> "str | None":
    """WSL パス → Windows パス（`wslpath -w`）。wslpath が無ければ None。"""
    if not shutil.which("wslpath"):
        return None
    try:
        out = subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def instance_record(cfg: "Config") -> dict:
    """このプロセスの監視対象（プロジェクトルートと主要パス・OS/WSL 情報）を表す発見用レコード。
    外部操作者が CLI を組むときは `root` を `--root` に渡す（1 プロジェクト = 1 ルート）。

    root は **リダイレクト前の素の root**（source_root）。実書き込み先（backlog.parent）は状態
    worktree 側を指すので、それを root として記録すると 2 つ壊れる: start/stop が照合に使う
    _resolved_root（リダイレクトしない）と一致せず重複検出・停止が空振りし、この root を
    --root に渡した外部操作者は worktree をさらに worktree へ逃がす二重リダイレクトに落ちる。
    各パス（backlog / needs / commands …）は実体を指したままなので、読み書きには影響しない。"""
    root = (cfg.source_root or cfg.backlog.parent).resolve()
    rt = detect_runtime()
    rec = {
        "pid": os.getpid(),
        "root": str(root),
        "project": cfg.project_name or root.name,
        "backlog": str(cfg.backlog.resolve()),
        "needs": str(cfg.needs.resolve()),
        "commands": str(commands_dir(cfg).resolve()),
        "decisions": str(cfg.decisions.resolve()),
        "archive": str(cfg.archive_dir().resolve()),
        "policy": str(cfg.policy.resolve()),
        "delivery": str(Path(cfg.delivery).resolve()),
        "journal": str(cfg.journal.resolve()),
        "workdir": str(cfg.workdir.resolve()),
        "watch": cfg.watch,
        "started_at": time.time(),
        "started_iso": datetime.now().isoformat(timespec="seconds"),
        "heartbeat": time.time(),                               # 生存信号（リモート発見の鮮度判定に使う）
        "heartbeat_iso": datetime.now().isoformat(timespec="seconds"),
        "ttl": max(INSTANCE_TTL, cfg.poll * 3),                 # poll より十分長くしてフラッピングを防ぐ
        "host": socket.gethostname(),
        "python": sys.executable,
        **rt,
    }
    if rt["runtime"] == "wsl":
        rec["root_windows"] = to_windows_path(root)  # \\wsl.localhost\<distro>\... 等。無ければ None
    return rec


def register_instance(cfg: "Config", extra: "list | str | None" = None) -> "list[Path]":
    """全レジストリ（ローカル home＋共有先）に自分を登録し、書けたファイルパス一覧を返す。
    共有先にも書くことで別ホストから発見される（失敗しても run は止めない）。"""
    rec = instance_record(cfg)
    blob = json.dumps(rec, ensure_ascii=False, indent=2)
    fname = _instance_filename(rec)
    written: list[Path] = []
    for d in resolve_registry_dirs(extra):
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / fname
            p.write_text(blob, encoding="utf-8")
            written.append(p)
        except OSError:
            continue
    return written


def refresh_instance(paths: "list[Path]") -> None:
    """登録済みレコードの heartbeat を更新する（watch の各パス/idle で呼ぶ＝リモートに生存を示す）。"""
    now = time.time()
    iso = datetime.now().isoformat(timespec="seconds")
    for p in paths:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["heartbeat"], rec["heartbeat_iso"] = now, iso
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            continue


def _start_heartbeat_thread(cfg: "Config", paths: "list[Path]",
                            interval: "float | None" = None) -> "threading.Event":
    """watch 中、本体が長い処理でブロックしている間も心拍を打ち続けるデーモンスレッドを起動し、
    停止用の Event を返す（set() で次のティックに終わる。プロセス終了は待たせない＝daemon）。

    従来 heartbeat は watch の各パスと idle でしか打てなかった。1 タスクの実行（エージェント
    CLI の呼び出しや kiro-flow run）は数分〜数十分ブロックするため、その間に INSTANCE_TTL
    （90 秒）を大きく超えて心拍が途切れる。外から見ると死んだように見え、kiro-projects-viewer
    では稼働中のプロジェクトが「停止中」や「別マシンで稼働中」と誤表示されていた
    （viewer は鮮度切れの instances レコードを捨て、status.json の鮮度判定へ落ちるため）。

    status.json は state_git のコミット対象なので、ここでは既存のポリシー
    （maybe_heartbeat_status＝設定 status_interval。既定 0＝無効）にそのまま従う。
    無効なら触らない＝idle の git 負荷は従来と変わらない。"""
    stop = threading.Event()
    if interval is None:
        interval = max(5.0, INSTANCE_TTL / 3.0)   # ttl の 1/3。切れる前に必ず 1 回は打つ

    def _beat() -> None:
        while not stop.wait(interval):
            with contextlib.suppress(Exception):   # 心拍の失敗で run を巻き込まない
                refresh_instance(paths)
            with contextlib.suppress(Exception):
                maybe_heartbeat_status(cfg)

    threading.Thread(target=_beat, name="kiro-project-heartbeat", daemon=True).start()
    return stop


def _maybe_prune(rec: dict, f: Path) -> None:
    """死んだレコードの掃除。自ホストのものは即削除、リモートは長期（grace 超）に限り削除。
    他ホストの最近のレコードは（共有先での競合を避け）触らない。"""
    try:
        if str(rec.get("host", "")) == socket.gethostname():
            f.unlink()
        else:
            hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
            if (time.time() - hb) > REMOTE_PRUNE_GRACE:
                f.unlink()
    except OSError:
        pass


def list_instances(prune: bool = True, extra: "list | str | None" = None) -> list:
    """生存中のインスタンス一覧（ローカル＋共有レジストリを横断）。同一インスタンスが複数ディレクトリに
    現れたら heartbeat が新しい方を採用。死んだレコードは _maybe_prune で掃除する。"""
    best: dict = {}                          # (host,pid,root) -> (rec, heartbeat)
    for d in resolve_registry_dirs(extra):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not _record_alive(rec):
                if prune:
                    _maybe_prune(rec, f)
                continue
            key = (str(rec.get("host", "")), int(rec.get("pid", -1)), str(rec.get("root", "")))
            hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
            cur = best.get(key)
            if cur is None or hb > cur[1]:
                best[key] = (rec, hb)
    return [v[0] for v in best.values()]


def cmd_instances(as_json: bool = False, extra: "list | str | None" = None) -> int:
    """稼働中の kiro-project（監視中プロジェクトルート）を一覧。外部操作者の発見口。
    共有レジストリを併用すると別ホストのインスタンスも横断表示する。"""
    recs = list_instances(prune=True, extra=extra)
    recs.sort(key=lambda r: (str(r.get("host", "")), int(r.get("pid", 0))))
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print("稼働中の kiro-project はありません（run/--watch 起動時に登録されます）。")
        return 0
    me = socket.gethostname()
    for r in recs:
        rt = r.get("runtime", "?")
        if r.get("wsl_distro"):
            rt += f":{r['wsl_distro']}"
        flags = "watch" if r.get("watch") else "run"
        host = str(r.get("host", "?"))
        where = "" if host == me else f" @{host}(remote)"
        print(f"pid={r['pid']} [{rt}] {flags}{where}  root={r['root']}")
        if r.get("root_windows"):
            print(f"    Windows: {r['root_windows']}")
    return 0


# ---------------------------------------------------------------------------
# 常駐ライフサイクル（start / stop / restart）— レジストリ(§4)の上に起動・停止操作を一級化
# ---------------------------------------------------------------------------
def _self_script() -> str:
    """この CLI を再実行できる実体の絶対パス（子プロセス起動・graceful 再起動に使う）。

    パッケージ化後の __file__ は kiro_project/instances.py を指し単体実行できないため:
      1) パッケージ隣の shim kiro-project.py があればそれ（リポジトリ/開発/テスト実行）。
         テスト実行時の sys.argv[0] は pytest 本体を指すので argv[0] は使えない。
      2) shim が無ければ（zipapp 配布時）起動に使われた実体 sys.argv[0]（zipapp）を再実行する。
    """
    shim = Path(__file__).resolve().parent.parent / "kiro-project.py"
    if shim.exists():
        return str(shim)
    arg0 = sys.argv[0] if sys.argv else ""
    if arg0 and Path(arg0).exists():
        return str(Path(arg0).resolve())
    return str(Path(__file__).resolve())


def _norm_root(root: str) -> str:
    return str(Path(root).expanduser().resolve())


def _drop_instance_record(rec: dict, extra: "list | str | None" = None) -> None:
    """このレコードのファイルを全レジストリから消す（ホスト修飾名＋旧 `<pid>.json` 形式の両方）。"""
    fname = _instance_filename(rec)
    pid = rec.get("pid")
    for d in resolve_registry_dirs(extra):
        for name in (fname, f"{pid}.json"):
            try:
                (d / name).unlink()
            except OSError:
                pass


def _reap(pid: int) -> None:
    """対象が自分の子なら回収してゾンビ化を防ぐ（他人の子・未対応は無視）。"""
    try:
        os.waitpid(pid, os.WNOHANG)
    except (OSError, ChildProcessError, AttributeError):
        pass


def select_instances(root: "str | None" = None, pid: "int | None" = None,
                     want_all: bool = False, extra: "list | str | None" = None) -> list:
    """稼働インスタンスを root / pid / 全件 で選ぶ。
    停止対象に使うため自ホストのレコードのみを返す（別ホストの PID へはシグナルを送れない）。"""
    me = socket.gethostname()
    recs = [r for r in list_instances(prune=True, extra=extra) if str(r.get("host", "")) == me]
    if want_all:
        return recs
    nr = _norm_root(root) if root else None
    out = []
    for r in recs:
        if pid is not None and int(r.get("pid", -1)) == pid:
            out.append(r)
            continue
        if nr is not None and str(r.get("root", "")) == nr:
            out.append(r)
    return out


def _signal_tree(pid: int, sig) -> None:
    """インスタンスとその子孫（kiro-flow の orchestrator / worker）へシグナルを送る。

    本人にだけ送ると kiro-flow が生き残る。すると残った orchestrator が run の生存リースを
    更新し続け、次に起動した kiro-project はそれを「まだ実行中」と読んで **続きから再開せず
    新しい run を作り直す**（実際 17/23 まで進んだ run を捨てて 1/20 からやり直した）。さらに
    同じタスクを二重に実行し、同じ作業ブランチへ両方が push しあう。

    start（detached）で起動したインスタンスは自分がプロセスグループのリーダーなので、グループへ
    送れば子孫まで届く。そうでない（端末から run --watch を直叩きした）場合はグループに無関係の
    プロセス（人のシェルや他のジョブ）が混ざるため、本人にだけ送る。"""
    try:
        if os.getpgid(pid) == pid:            # detached 起動＝自分がグループリーダー
            os.killpg(pid, sig)
            return
    except OSError:
        pass
    os.kill(pid, sig)


def _flow_pids_for_bus(bus: Path) -> "list[int]":
    """自分の bus を回している kiro-flow プロセスの pid（POSIX のみ。取れなければ空）。

    kiro-project は kiro-flow を `--bus <root>/bus` で起動するので、コマンドラインの bus パスで
    「自分のもの」を特定できる。ps が無い環境（Windows 素の cmd 等）では空を返し、従来動作に倒す。"""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    me, target, out = os.getpid(), str(Path(bus).resolve()), []
    for line in r.stdout.splitlines():
        pid_s, _, args = line.strip().partition(" ")
        if "kiro-flow" not in args or target not in args:
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
            f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
        except OSError:
            continue
    return n


def reap_orphan_flow(cfg: "Config") -> int:
    """自分の bus に居残った kiro-flow（前世代の残骸）を止める。止めたプロセス数を返す。

    kiro-project がクラッシュ（kill -9 / 電源断 / OOM）すると、stop を通らないので kiro-flow の
    orchestrator と worker が生き残る。残った orchestrator は run の生存リースを更新し続けるため、
    次に起動した kiro-project はその run を「実行中」と読み、**続きから再開せず新しい run を作り
    直す**。結果、同じタスクを二重に実行し、同じ作業ブランチへ両方が push しあう（実際に起きた:
    17/23 まで進んだ run を捨てて 1/20 からやり直した）。

    同じ bus を使う kiro-project は 1 つだけ（cmd_run が重複起動を弾く）。したがって **自分の起動
    時点でその bus を回している kiro-flow は、例外なく前世代の残骸** である。止めたうえでリースを
    失効させ、run を「停滞」として続きから再開できる状態に戻す。"""
    pids = _flow_pids_for_bus(cfg.bus)
    if not pids:
        return 0
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)        # グループには自分（kiro-project）が混ざりうるので個別に
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


def cmd_stop(root: "str | None" = None, pid: "int | None" = None,
             want_all: bool = False, timeout: float = 5.0,
             extra: "list | str | None" = None,
             config: "str | None" = None) -> int:
    """稼働インスタンスへ SIGTERM（必要なら SIGKILL）を送り、レジストリも掃除する（自ホストのみ）。
    kiro-flow の子プロセスも道連れにする（_signal_tree 参照）。"""
    if not pid and not want_all:                  # 既定は cwd（または --root/設定）のプロジェクトを止める
        root = _resolved_root(root, config)
    targets = select_instances(root, pid, want_all, extra=extra)
    if not targets:
        print("停止対象の稼働インスタンスが見つかりません（instances で確認できます）。", file=sys.stderr)
        return 1
    all_ok = True
    for r in targets:
        p = int(r["pid"])
        if p == os.getpid():                  # 自分自身は決して止めない（安全ガード）
            continue
        try:
            # graceful: 子側の SIGTERM ハンドラが finally で後始末。kiro-flow の子孫まで届かせる
            _signal_tree(p, signal.SIGTERM)
        except OSError as e:
            print(f"pid={p}: SIGTERM 失敗（{e}）", file=sys.stderr)
            all_ok = False
            continue
        deadline = time.time() + timeout
        while time.time() < deadline and _pid_alive(p):
            _reap(p)
            time.sleep(0.1)
        if _pid_alive(p) and hasattr(signal, "SIGKILL"):  # 居残りは強制終了（POSIX のみ）
            try:
                _signal_tree(p, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(0.2)
            _reap(p)
        _drop_instance_record(r)
        ok = not _pid_alive(p)
        all_ok = all_ok and ok
        print(f"pid={p} {'停止しました' if ok else '停止できませんでした'}  root={r.get('root')}")
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
