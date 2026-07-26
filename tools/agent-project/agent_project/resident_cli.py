from __future__ import annotations
# resident_cli.py — 常駐体 CLI: agent-project serve / status / worker init / worker
# （実装計画 W1-11、設計 §4.2・§4.3）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 本フラグメントの範囲（このパスで配線するもの）:
#   - host.yaml の読み込み（PC 宣言の単一ソース）
#   - Supervisor によるプロジェクト子（`run --watch`）の起動・監視・隔離・graceful 停止
#     （旧 cmd_start の detach & 放置に代わる、実際に生死を見る親）
#   - 心拍・子状態を `.agents/engine/status.json` へ書き出す tick
#   - amigos 参加 tick（claim・心拍・板巡回のみ。手番実行は NodeWorkerPool へ委ねる）
#   - flow 参加 tick（cancel 受理・孤児回収・板巡回・inbox 受理のみ。run の実行は
#     NodeWorkerPool へ委ねる。旧 `agent-flow daemon` の置き換え — 実装計画 W1-9）
#   - gc tick（登録プロジェクトの agent-flow バス掃除。掃除の実装は持たない — R1）
# 参加 tick はどちらも「調停は都度起動の CLI・実行はプール」に揃えてある。周期を超えうる
# 仕事（run・手番）を tick 内で実行すると、self-watchdog がハングと読んで健全な常駐体を
# abort する。
#
# nodes/<pc>.json のノード能力宣言（node 名義で板へ「何ができる PC か」を出す）はまだ無い。
# 板の請負自体は各ツールの `participate` が委譲側 bus 経由で行っており、ノード直轄の能力
# 宣言はそれとは別の設計判断が要るため、ここでは手を付けない（実装計画 W1-11 残）。

from types import SimpleNamespace

from agentcore.nodeid import normalize_node_id
from agentcore.protocol import write_json_atomic
from agent_project.resident import (ChildSpec, ChildStatus, EngineStatus,
                                    NodeWorkerPool, Scheduler, Supervisor, SyncHealth,
                                    Tick, WorkItem, graceful_shutdown, run_gc)

HOST_CONFIG_NAMES = ("agent-project.host.yaml", "agent-project.host.yml",
                     "agent-project.host.json")

# tick が起動する外部コマンドの打ち切り時間。**同じ値を Tick.timeout にも渡す**
# （self-watchdog の猶予に入る）——ここと Scheduler で別々の数字を持つと、正当に長い tick が
# ハング扱いされて健全な常駐体が abort する。
_GC_PROJECT_TIMEOUT_SEC = 120.0
_AMIGOS_TICK_TIMEOUT_SEC = 60.0


def _agents_home() -> Path:
    """PC 単位の状態置き場（既定 `~/.agents`）。flow-board/amigos の node.json と同じ流儀
    （実装計画 P0〜W1-10 を通して既にこの下に各種状態が置かれている）。
    `AGENT_PROJECT_AGENTS_HOME` で上書き可（テストが実ホームを汚さないため。
    `resolve_state_home` の `AGENT_PROJECT_HOME` と同じ流儀の別変数——インスタンス
    レジストリの置き場 `~/.agent-project` とは意味が違うので混ぜない）。"""
    override = os.environ.get("AGENT_PROJECT_AGENTS_HOME")
    if override:
        return Path(override)
    return Path.home() / AGENT_HOME


def _find_host_config(explicit: "str | None" = None) -> "str | None":
    """agent-project.host.yaml の探索: 明示指定 > cwd > ~/.agents。"""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    for base in (Path.cwd(), _agents_home()):
        for name in HOST_CONFIG_NAMES:
            p = base / name
            if p.is_file():
                return str(p)
    return None


def _normalize_host_repos(raw) -> "list[dict]":
    """host.yaml の `repos:` を [{url, local}, …] へ正規化する（S3）。

    人が手で書くので型は保証されない。list（正）のほか、旧 mapping 形式（url -> local）と
    素の文字列列も受ける。壊れていても例外にせず落とせる分だけ拾う——設定ミスでノードが
    起動しない方が、ローカル最適化が効かないことより高くつく（`_normalize_hooks` と同じ流儀）。"""
    out: "list[dict]" = []
    if isinstance(raw, dict):
        for url, local in raw.items():
            if isinstance(local, dict):
                out.append({"url": str(url), **{k: str(v) for k, v in local.items()}})
            elif local:
                out.append({"url": str(url), "local": str(local)})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("url"):
                out.append({str(k): (str(v) if v is not None else "") for k, v in item.items()})
            elif isinstance(item, str) and item.strip():
                out.append({"url": item.strip()})
    return out


class HostConfig:
    """agent-project.host.yaml の内容（PC 宣言の単一ソース。設計 §4.2）。

    `projects` が空ならワーカーノード（lite）プロファイル（設計 §4.3）——別プログラムには
    せず、ロール分岐は「projects が空か」の 1 点だけに保つ（C12: フォークは重複実装の
    再演になる）。"""

    def __init__(self, data: dict, path: "str | None" = None):
        self.path = path
        declared = str(data.get("node_id") or "").strip()
        # 「宣言されたか」を保つ: `resolve_config` は宣言 > 環境変数 > ホスト名の順に採る
        # （宣言があるのに AGENT_PROJECT_NODE で黙って別名になると、板の名義と claim の
        # 持ち主が食い違う）。node_id 自体は常に値を持つので、この旗が無いと区別できない。
        self.node_id_declared = bool(declared)
        self.node_id = normalize_node_id(declared) if declared \
            else normalize_node_id(socket.gethostname())
        self.projects = [dict(p) for p in (data.get("projects") or []) if isinstance(p, dict)]
        self.tags = [str(t) for t in (data.get("tags") or [])]
        # 能力宣言の `agent_cli:`（このノードで使える CLI の一覧・板への入札判定に使う）と、
        # `defaults.agent_cli`（このノードの既定 CLI・スカラ）は **別のキー**。前者は板の語彙、
        # 後者は設定の層（S1 の SHARED 群）で、混ぜると「1 つしか宣言していない PC の既定が
        # 勝手に変わる」ことになる。
        self.agent_cli = [str(a) for a in (data.get("agent_cli") or [])]
        # S1: ノード全体の共有キー既定。projects[].overrides と合わせて resolve_config が読む。
        self.defaults = dict(data.get("defaults") or {})
        # S3: ノード固有のローカルクローン宣言（url/local の列）。共有 repos.json には置けない
        # （ホスト固有の絶対パスが state repo 経由で全 PC へ配られてしまうため）。
        # 旧形式（mapping）も読めるようにしておく——能力宣言への転記しかしていなかった頃の
        # host.yaml がそのまま残っていても落とさない。
        self.repos = _normalize_host_repos(data.get("repos"))
        # S1: ツールの自動アップデートはノードのインストール管理（`update.*` → update_* キー）。
        self.update = dict(data.get("update") or {})
        self.board = str(data.get("board") or "")
        self.board_workdir = data.get("board_workdir")
        self.max_concurrent = int((data.get("budget") or {}).get("max_concurrent", 0) or 0)
        # amigos 参加 tick（設計 §4.2「amigos 参加（claim・心拍・away）」）の対象バス。
        # 未設定なら amigos 参加 tick 自体を skip する（board 未設定で板 tick が no-op になるのと
        # 同じ流儀 — host.yaml に無い機能を強制起動しない）。
        self.amigos_bus = str(data.get("amigos_bus") or "")
        self.amigos_config = data.get("amigos_config")
        # 稼働時間帯（夜間停止）。PC 単位の性質なので host.yaml が単一ソース
        # （実装計画 W1-4）。子は自分で止まらない——親がこの宣言で pause/resume する。
        self.availability = dict(data.get("availability") or {})
        # 常駐化の方式（設計 §7 の 2 案のどちらを選んだか）。doctor はこの宣言に従う——
        # systemd 側しか検査できないので、windows-task を選んだ PC で「常駐化が未構成」を
        # 出し続けると、正しく構成した利用者に恒久的な誤警告を浴びせることになる。
        # 既定 auto は「systemd がある環境なら systemd 案とみなす」（従来の挙動）。
        self.residency = str(data.get("residency") or "auto").strip().lower()

    @property
    def is_worker(self) -> bool:
        return not self.projects


def load_host_config(explicit: "str | None" = None) -> HostConfig:
    path = _find_host_config(explicit)
    if not path:
        return HostConfig({})
    return HostConfig(_load_config_file(path), path=path)


def _project_name(project: dict) -> str:
    """host.yaml の 1 プロジェクト宣言の識別名（宣言があればそれ、無ければ実効 root の slug）。
    子プロセス名・gc sweeper 名・`engine/status.json` の children[].name はすべてこれ——
    導出が割れると status の子と gc の集計が別名で並び、dashboard が同じプロジェクトを
    2 件に見せる。"""
    return str(project.get("name") or "").strip() \
        or _slug(_resolved_root(str(project.get("root") or "").strip()))


def _availability_tick(host: "HostConfig", sup: "Supervisor",
                       status: "EngineStatus", at: "datetime | None" = None) -> None:
    """稼働時間帯の外では子を計画停止し、戻ったら再開する（実装計画 W1-4・設計 §6「PC の
    計画停止」）。**停止を決めるのは常に親**——子が自分に SIGTERM を送る旧経路では、
    Supervisor がそれをクラッシュと読んで再起動し、繰り返しで隔離に達していた。

    `pause` は死亡回数に数えないので、毎晩の停止を繰り返しても隔離されない。
    availability 未宣言なら何もしない（時間帯の概念が無い＝常時稼働）。

    **止めるのは `shutdown_due`（daily_stop + shutdown_grace_sec）に達してから**。
    `draining` で止めると `drain_before_sec` と `shutdown_grace_sec` が死に設定になり、
    走っているタスクを完走させずに SIGTERM することになる——drain は「新規 claim を
    止めて走っているものを終わらせる」ための時間帯で、その扱いは子側の
    `start_availability_monitor` が既に担っている。

    `at` は判定時刻の明示（既定は現在時刻）。`availability_state` / `shutdown_due` が
    元から持っている seam をここまで通す——時間帯の判定を実時計に委ねたままだと、
    テストが「いまから 1 時間後に停止」のような相対時刻でしか書けず、その 1 時間が
    日付を跨ぐ時間帯（daily_stop は時刻の概念なので跨ぎを持たない）に走ったときだけ
    落ちる。"""
    if not host.availability:
        return
    # availability_state / shutdown_due は cfg の `availability` 属性しか見ない。
    # host.yaml の宣言をそのまま渡すための最小の器（Config を組み立てる必要は無い）。
    cfg = SimpleNamespace(availability=host.availability)
    state = availability_state(cfg, at)
    if state == "invalid":
        # 不正設定では時間帯を判定できない。**止める側に倒す**（動かし続けると、止めたい
        # 時間帯に動く方が害が大きい）。同じ文言を tick ごとに積むと recent_errors が
        # 埋まって他のエラーが押し出されるので、内容が変わるまでは 1 度だけ記録する。
        msg = f"availability 設定が不正です（子を停止したままにします）: {host.availability}"
        if msg not in status.recent_errors:
            status.record_error(msg)
        for name in sup.names():
            sup.pause(name)
        return
    off = shutdown_due(cfg, at)
    for name in sup.names():
        if off:
            sup.pause(name)
        else:
            sup.resume(name)


def _amigos_turns_dir() -> Path:
    """agent-amigos が置く手番マーカーの場所（`agent_amigos.turnmark.turns_dir` と同じ解決）。
    ツールを跨ぐのはこのデータ契約だけで、実装は import しない（R1）。"""
    return agent_home_subdir("AGENT_AMIGOS_TURNS_DIR", "amigos", "turns")


def _external_amigos_inflight(host: "HostConfig") -> set:
    """この PC で**いま走っている** amigos 手番を観測する
    （実装計画 W1-5「計数は status/run ファイルから導出」）。

    常駐体はノード唯一の実行主体ではない——設計 §1.3 C14 はスキル起動の単発実行
    （人が `agent-amigos run --once` / `agent-amigos drive` を直接叩く）との併走を明示的に
    許す。プロセス内カウンタだけで数えると、その分がノード全体の `max_concurrent` を
    素通りして超過する。

    根拠は `agent_amigos.turnmark` が手番の実行中だけ置く pid ファイル
    （`~/.agents/amigos/turns/*.json`）。**バスの `status/<node>--<role>.json` は使えない**
    ——あれはロールの在籍状態で、ターンが終わっても `state` は `working` のまま残るため、
    観測に流用すると終わった手番を走行中と誤読して、自分が回したロールの次の手番を
    自分で永久に弾いてしまう（`NodeWorkerPool.submit` が二重実行回避で捨てる）。

    生存は pid で判定するので、落ちたプロセスの書き残しは数えない（鮮度の当て推量は不要）。
    返す id は `NodeWorkerPool` への投入 id と同じ空間（`amigos/<mission>/<role>`）で、
    自分が起こした手番も同じ印を置くため集合演算で二重計上されない。"""
    out: set = set()
    d = _amigos_turns_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            mark = json.loads((d / name).read_text(encoding="utf-8"))
            pid = int((mark or {}).get("pid") or 0)
        except (OSError, ValueError, TypeError):
            continue
        mission, role = str(mark.get("mission") or ""), str(mark.get("role") or "")
        if mission and role and _pid_alive(pid):
            out.add(f"amigos/{mission}/{role}")
    return out


# 依頼側が消し損ねた公示を掃く保険のマージン。通常の削除は依頼側が settle 直後に行う
# （`_reap_offloaded`）ので、ここに残るのは依頼側が永久に消えた孤児だけ。長めに取るのは
# 「まだ結果を読んでいない依頼側」を巻き込まないため——読む前に消すと offloaded が
# 未終端のまま固まる（タイムアウトが無い）。
_BOARD_ORPHAN_DAYS = 30.0


def _sweep_terminal_delegations(host: "HostConfig") -> dict:
    """終端して久しい公示を板から掃除する（設計 §4.2 gc tick「終端した公示」の保険側）。

    通常の削除は依頼側が結果を読んだ直後に行う（`_reap_offloaded` → `drop_delegation`）。
    ここが拾うのは、依頼側の PC ごと消えた等で誰も回収しなくなった孤児だけ。

    掃除の実体は `BoardRepo.sweep_terminal_delegations`——板のレイアウトと flock、
    git+ 板の clone 解決を持つのはあの層だけで、ここで `Path(host.board)` を直接
    見に行くと git+ 板では黙って no-op になる。"""
    if not host.board:
        return {}
    board = BoardRepo(host.board, workdir=host.board_workdir)
    removed = board.sweep_terminal_delegations(_BOARD_ORPHAN_DAYS * 86400.0)
    return {"delegations": removed} if removed else {}


def _observe_sync_health(roots_by_name: dict) -> "list[SyncHealth]":
    """登録プロジェクトごとの同期健康を観測する（設計 §5・実装計画 W2-5）。

    **dashboard の同期表示はこれが唯一の根拠**（`engine.summarize` は `sync_health` の
    `last_error` と ahead/behind しか見ない）。空のまま出すと、リモートが落ちていても
    未 push が溜まっていても「共有先と揃っています」と緑で出る——W2-1 で dashboard 側の
    fetch（`refreshRemote`）を廃止した以上、ここが埋まっていないと同期の異常を知る手段が
    どこにも無くなる。

    観測は副作用なし（`DirectStateGit.observe_sync` は fetch しない）。remote 未設定の
    プロジェクトは同期の概念が無いので載せない。"""
    out: "list[SyncHealth]" = []
    for name, root in sorted(roots_by_name.items()):
        try:
            obs = DirectStateGit(Path(root), interval=0.0).observe_sync()
        except OSError as e:
            out.append(SyncHealth(name=name, last_error=str(e)[:300]))
            continue
        if not obs:
            continue    # remote 無し＝ローカル完結の縮退
        out.append(SyncHealth(name=name, ahead=int(obs.get("ahead") or 0),
                              behind=int(obs.get("behind") or 0),
                              last_error=obs.get("last_error")))
    return out


def _project_child_spec(project: dict) -> "ChildSpec | None":
    """host.yaml の 1 プロジェクト宣言から Supervisor.ChildSpec を組み立てる。
    `agent-project run --watch` を Popen する（detach せず Supervisor が Popen ハンドルを
    保持・監視する）。

    渡すのは `--project <name>` だけ——root / state_repo / branch / overrides の解釈は子の
    `resolve_config` が host.yaml を読み直して行う（S1）。宣言の解釈が親子で 2 実装になると、
    片方だけ直したときに黙って食い違う。clone の確保も子が行うので、常駐体は git を触らない
    （失敗は子の起動失敗として Supervisor の隔離と status.json の recent_errors に載る）。"""
    if not str(project.get("root") or "").strip():
        return None
    name = _project_name(project)
    return ChildSpec(name=name,
                     argv=[sys.executable, _self_script(), "run", "--watch", "--project", name])


def _project_children(host: HostConfig) -> "list[ChildSpec]":
    specs: "list[ChildSpec]" = []
    seen: "set[str]" = set()
    for project in host.projects:
        spec = _project_child_spec(project)
        if spec is None:
            print(f"[agent-project] serve: host.yaml の project に root が無いエントリを"
                 f"無視しました（root には状態リポジトリの clone 先を絶対パスで宣言します）: "
                 f"{project}", file=sys.stderr)
            continue
        if spec.name in seen:
            # ここで落とさず両方 specs に残すと、`Supervisor.add` が同名キーを辞書ごと
            # 差し替えて 1 本目の Popen ハンドルを失い、続く `start` が 2 本目を起こす。
            # 1 本目は誰にも監視されず graceful_shutdown でも止まらない孤児になる。
            print(f"[agent-project] serve: host.yaml に重複したプロジェクト名 "
                 f"{spec.name} — 2 件目以降を無視します（root を確認してください）",
                 file=sys.stderr)
            continue
        seen.add(spec.name)
        specs.append(spec)
    return specs


def _resolve_agent_amigos() -> "list[str]":
    """agent-amigos 実行コマンド（PATH → tools/ 隣接配置の順）。`request.py` の
    `resolve_agent_flow` と同じ導出（R1: 探索アルゴリズムは 1 つ）——ただし host.yaml に
    override フィールドは持たない（amigos はプロジェクト設定を経由しないため明示指定の
    出番が無い。要れば host.yaml へ後で足せる）。"""
    found = shutil.which("agent-amigos")
    if found:
        return [found]
    here = Path(__file__).resolve()
    tools_sibling = here.parents[2] / "agent-amigos" / "agent-amigos.py"
    if tools_sibling.is_file():
        return [sys.executable, str(tools_sibling)]
    legacy = here.parents[1] / "agent-amigos" / "agent-amigos.py"
    return [sys.executable, str(legacy)]


def _amigos_participate_tick(host: "HostConfig", pool: "NodeWorkerPool",
                             status: "EngineStatus") -> None:
    """amigos 参加 tick（設計 §4.2「amigos 参加（claim・心拍・away）」の実体・実装計画 W1-11 残）。
    `agent-amigos participate`（claim・オーナー職務・板巡回のみ）を都度起動し、自分が roster 上で
    担当することになった (mission,role) を `agent-amigos run --once` として NodeWorkerPool へ
    投入する——手番の実行は周期を超えうるため tick 内では絶対に実行しない（設計の実行規約）。
    プロセス境界を跨ぐが、調停はすべて bus（ファイル）越しなので安全（R9 の帰結でもある:
    amigos 自体が常駐なしで成立する設計だからこそ、この分離が素直に実装できる）。"""
    amigos = _resolve_agent_amigos()
    cmd = amigos + ["participate", "--node-id", host.node_id, "--json"]
    if host.amigos_bus:
        cmd += ["--bus", host.amigos_bus]
    if host.amigos_config:
        cmd += ["--config", str(host.amigos_config)]
    if host.tags:
        cmd += ["--tags", ",".join(host.tags)]
    if host.agent_cli:
        cmd += ["--agent-cli", host.agent_cli[0]]   # 複数宣言時は先頭を既定として使う
    if host.board:
        cmd += ["--board", host.board]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_AMIGOS_TICK_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as e:
        status.record_error(f"amigos participate 起動失敗: {e}")
        return
    if proc.returncode != 0:
        status.record_error(f"amigos participate 失敗（rc={proc.returncode}）: "
                            f"{proc.stderr.strip()[-300:]}")
        return
    try:
        owned = json.loads(proc.stdout or "[]")
    except ValueError:
        return
    for item in owned:
        mid = str((item or {}).get("mission_id") or "").strip()
        rid = str((item or {}).get("role_id") or "").strip()
        if not mid or not rid:
            continue
        run_cmd = amigos + ["run", "--mission", mid, "--role", rid, "--once",
                            "--node-id", host.node_id]
        if host.amigos_bus:
            run_cmd += ["--bus", host.amigos_bus]
        if host.amigos_config:
            run_cmd += ["--config", str(host.amigos_config)]
        if host.agent_cli:
            run_cmd += ["--agent-cli", host.agent_cli[0]]
        pool.submit(WorkItem(id=f"amigos/{mid}/{rid}",
                             run=lambda c=run_cmd: subprocess.run(
                                 c, capture_output=True, text=True, timeout=1800)))


def _project_cmd(project: dict, *argv: str) -> "list[str]":
    """host.yaml の 1 プロジェクト宣言に対する `agent-project <argv…>` の起動 argv。
    プロジェクト設定の解決は agent-project 本体に任せる（常駐体は名前を渡すだけ・S1）。"""
    return [sys.executable, _self_script(), *argv, "--project", _project_name(project)]


def _flow_participate_tick(host: "HostConfig", pool: "NodeWorkerPool",
                           status: "EngineStatus") -> None:
    """flow 参加 tick（設計 §4.2 node 層・実装計画 W1-9）。登録プロジェクトのバスごとに
    `agent-project flow-participate` を都度起動し、受理された run を
    `agent-project flow-run` として NodeWorkerPool へ投入する——run の実行は周期を
    超えうるため tick 内では絶対に実行しない（amigos 参加 tick と同じ実行規約）。

    旧 `agent-flow daemon` が担っていた責務のうち、生存リース・park 監視・停滞回収は
    `agent-flow run` 自身が持つ（run 単発は自己完結する）。ここに残るのは「誰が何を
    実行するか」の調停だけ——cancel の受理・孤児の引き継ぎ判断・板の巡回・inbox の受理。

    走っている / 起動待ちの run-id は `--running` で渡す。渡さないと、枠が空くのを待って
    いる run を毎周『駆動者が居ない孤児』と読んで再開回数を焼き、上限で failed に確定する。"""
    busy = pool.busy_ids()
    for project in host.projects:
        if not str(project.get("root") or "").strip():
            continue
        name = _project_name(project)
        running = sorted(r[len(f"flow/{name}/"):] for r in busy
                         if r.startswith(f"flow/{name}/"))
        cmd = _project_cmd(project, "flow-participate", "--json")
        if running:
            cmd += ["--running", ",".join(running)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=_FLOW_TICK_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as e:
            status.record_error(f"flow participate {name}: {e}")
            continue
        if proc.returncode != 0:
            status.record_error(f"flow participate {name} 失敗（rc={proc.returncode}）: "
                                f"{proc.stderr.strip()[-300:]}")
            continue
        try:
            items = json.loads(proc.stdout or "[]")
        except ValueError:
            continue
        for item in items:
            rid = str((item or {}).get("run_id") or "").strip()
            if not rid:
                continue
            run_cmd = _project_cmd(project, "flow-run", "--run-id", rid)
            pool.submit(WorkItem(id=f"flow/{name}/{rid}",
                                 run=lambda c=run_cmd: subprocess.run(c)))


def _project_gc_sweeper(project: dict) -> "tuple[str, object] | None":
    """host.yaml の 1 プロジェクト宣言から gc sweeper（`resident.gc.run_gc` が食べる
    `(名前, 引数無し callable)` の組）を組み立てる。掃除の実装は持たない（R1）——
    `agent-project gc` を単発起動して集計 dict をそのまま返すだけ。"""
    if not str(project.get("root") or "").strip():
        return None
    name = _project_name(project)
    cmd = _project_cmd(project, "gc", "--json")

    def sweep() -> dict:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_GC_PROJECT_TIMEOUT_SEC)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[-300:] or f"exit {proc.returncode}")
        try:
            return json.loads(proc.stdout or "{}")
        except ValueError:
            return {}

    return (name, sweep)


def _project_gc_sweepers(host: "HostConfig") -> "list[tuple[str, object]]":
    sweepers = []
    for project in host.projects:
        s = _project_gc_sweeper(project)
        if s:
            sweepers.append(s)
    return sweepers


def _build_resident(host: "HostConfig", *, start_children: bool = True):
    """host.yaml から Supervisor・Scheduler・EngineStatus を組み立てる（未 start）。
    セットアップだけを切り出してあるのは単体テストのため——`cmd_serve` はこれに
    `Scheduler.start()`/ブロッキングループ/graceful 停止を足すだけの薄い皮。"""
    specs = _project_children(host)
    sup = Supervisor()
    for spec in specs:
        sup.add(spec)
        if start_children:
            sup.start(spec.name)

    status = EngineStatus(host.node_id)
    status_path = _agents_home() / "engine" / "status.json"
    # 子の名前 → host.yaml の root 宣言。dashboard のプロジェクト発見はこれを辿る（W2-4）ので、
    # 子状態と同じ 1 件として載せる（Supervisor は root を知らない＝名前で突き合わせる）。
    roots_by_name = {_project_name(p): str(p.get("root") or "").strip()
                     for p in host.projects if str(p.get("root") or "").strip()}

    def write_status() -> None:
        # EngineStatus.write(state_home) は <state_home>/.agents/engine/status.json へ書く
        # （プロジェクト workdir を渡す想定の API）。ここでは _agents_home() が既に
        # `.agents` 自身なので、二重に .agents が付かないよう write_json_atomic を直接使う。
        status.heartbeat = datetime.now(timezone.utc).isoformat()
        status.children = [
            ChildStatus(name=name, alive=info["alive"], quarantined=info["quarantined"],
                       deaths=info["deaths"], root=roots_by_name.get(name),
                       paused=bool(info.get("paused")))
            for name, info in sup.status().items()]
        status.sync_health = _observe_sync_health(roots_by_name)
        status.running_runs = list(pool.status().get("inflight") or [])
        write_json_atomic(str(status_path), status.to_dict())

    # ノード直轄ワーカー（設計 §4.2・実装計画 W1-5/W1-11）。max_concurrent: 0 は「明示未設定」
    # として旧 flow daemon 既定の --max-workers（4）に合わせる（NodeWorkerPool 自体は
    # max(1, n) を強制するため 0 を「無制限」にはできない。host.yaml.example の記載も
    # 「既定 4」に合わせて更新済み）。
    pool = NodeWorkerPool(
        host.max_concurrent if host.max_concurrent > 0 else 4,
        # スキル起動の単発実行（C14 併走）も同じ枠で律速する。プロセス内だけで数えると
        # 人が直接叩いた手番が max_concurrent を素通りする（実装計画 W1-5）。
        external_inflight=lambda: _external_amigos_inflight(host),
        on_event=lambda item_id, ev, exc: (status.record_error(f"worker {item_id}: {exc}")
                                           if ev == "failed" else None))

    def tick_supervise() -> None:
        # 時間帯の判定は check_health より先に。逆順だと、停止時間帯に入った直後の 1 巡で
        # check_health が「止まっている子」を死亡とみなして起こしてしまう。
        _availability_tick(host, sup, status)
        sup.check_health()
        pool.drain()   # 専用 tick を新設せず、既存の短周期 tick に相乗りさせる（設計に無い tick を増やさない）
        status.tick_counts["supervise"] = status.tick_counts.get("supervise", 0) + 1
        write_status()

    def tick_amigos() -> None:
        if not host.amigos_bus:
            return   # board 未設定で板 tick が no-op になるのと同じ流儀
        _amigos_participate_tick(host, pool, status)
        status.tick_counts["amigos"] = status.tick_counts.get("amigos", 0) + 1
        write_status()

    def tick_flow() -> None:
        _flow_participate_tick(host, pool, status)
        status.tick_counts["flow"] = status.tick_counts.get("flow", 0) + 1
        write_status()

    def tick_gc() -> None:
        run_gc(_project_gc_sweepers(host) + [("board", lambda: _sweep_terminal_delegations(host))],
              on_event=lambda name, ev, exc: (status.record_error(f"gc {name}: {exc}")
                                              if ev == "failed" else None))
        status.tick_counts["gc"] = status.tick_counts.get("gc", 0) + 1
        write_status()

    # 周期は設計 §4.2 の周期表のコード定数（yaml では変えない — 設定より規約）。
    # timeout は「1 回の呼び出しに掛かってよい想定時間」で、self-watchdog の猶予にも入る。
    # gc はプロジェクトごとに `agent-project gc`（各 timeout=120s）を**逐次**起動するので、
    # プロジェクト数に比例して正当に長くなる。宣言しないと 3 プロジェクト以上で猶予
    # （既定 watchdog_timeout=300s）を超え、健全な常駐体を abort してしまう。
    gc_budget = _GC_PROJECT_TIMEOUT_SEC * max(1, len(host.projects))
    # flow tick はプロジェクトごとに `agent-project flow-participate` を**逐次**起動するので、
    # プロジェクト数に比例して正当に長くなる（gc と同じ理由で timeout を宣言する）。
    flow_budget = _FLOW_TICK_TIMEOUT_SEC * max(1, len(host.projects))
    sched = Scheduler([Tick("supervise", 5.0, tick_supervise),
                       Tick("amigos", 5.0, tick_amigos, timeout=_AMIGOS_TICK_TIMEOUT_SEC),
                       Tick("flow", 5.0, tick_flow, timeout=flow_budget),
                       Tick("gc", 600.0, tick_gc, timeout=gc_budget)],
                      on_tick_error=lambda name, exc: status.record_error(f"{name}: {exc}"))
    return sup, sched, status, write_status, pool


def cmd_serve(args) -> int:
    """常駐体のエントリポイント（設計 §4.2・実装計画 W1-11）。フォアグラウンド実行——
    常駐化（起動時に上がる・死んだら上げ直す）は起動系（systemd 等）の役目で、このコマンド
    自体は「起動されたら動き続ける」だけを担う（設計 §7）。"""
    host = load_host_config(getattr(args, "host_config", None))
    print(f"[agent-project] serve: node_id={host.node_id}"
         + (f" host_config={host.path}" if host.path else "（host.yaml 未検出・既定ノード）"))
    if host.is_worker:
        print("[agent-project] serve: projects 未宣言 — ワーカーノード（lite）として動作します")

    sup, sched, status, write_status, pool = _build_resident(host)
    write_status()
    sched.start()
    print(f"[agent-project] serve: 起動しました（projects={len(sup.names())} pid={os.getpid()}）")
    # SIGTERM を必ず拾う。既定ハンドラは即死なので下の finally（子の graceful 停止）が
    # 走らず、`run --watch` の子が監督者不在のまま生き残る——次の起動で Supervisor が
    # 新しい子を起こすので同一プロジェクトにループが 2 本並ぶ。systemd の stop/restart は
    # SIGTERM なので、`install.sh --service` が勧める経路がそのままこれを踏む。
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: stopping.set())
        except (ValueError, OSError):
            pass    # メインスレッド以外（テスト等）では設定できない
    try:
        while not stopping.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass        # ハンドラ設定前に届いた場合の保険
    finally:
        print("[agent-project] serve: 停止します（子の graceful 停止）", file=sys.stderr)
        sched.stop()
        graceful_shutdown(sup)
        sched.join(timeout=5.0)
    return 0


def cmd_status(args) -> int:
    """`.agents/engine/status.json`（常駐体が書く心拍・子状態）を表示する（実装計画 W1-11）。"""
    path = _agents_home() / "engine" / "status.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not data:
        print(f"エンジン状態が見つかりません: {path}（agent-project serve が起動していないか、"
             f"まだ 1 tick も回っていません）", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"node_id     : {data.get('node')}")
    print(f"heartbeat   : {data.get('heartbeat')}")
    print(f"tick_counts : {data.get('tick_counts')}")
    children = data.get("children") or []
    if children:
        print("children:")
        for c in children:
            mark = "!" if c.get("quarantined") else ("+" if c.get("alive") else "-")
            print(f"  [{mark}] {c.get('name')} deaths={c.get('deaths')}")
    else:
        print("children    : (なし・ワーカーノード)")
    errors = data.get("recent_errors") or []
    if errors:
        print(f"recent_errors ({len(errors)}):")
        for e in errors[-5:]:
            print(f"  - {e}")
    return 0


def cmd_worker_init(args) -> int:
    """ワーカーノード（lite）の host.yaml を生成する（実装計画 W1-11・設計 §4.3）。
    projects を持たない host.yaml を書く——導入は clone + install.sh + このコマンドの
    最小手順（設計の要件 R6）。"""
    node_id = normalize_node_id(getattr(args, "node_id", None) or socket.gethostname())
    # 専有項目の契約はワーカーノードでもフルノードと同一（S1）。生成物にも同じ骨格
    # （defaults / projects / repos）を出しておく——空の器があるだけで「ここに書く」が伝わり、
    # プロジェクト yaml 側へ書いてしまう事故（E1/E2）を減らせる。
    data = {
        "schema_version": 1,
        "node_id": node_id,
        "projects": [],
        "defaults": {},
        "repos": [],
        "tags": [t for t in (getattr(args, "tags", None) or "").split(",") if t],
        "agent_cli": [a for a in (getattr(args, "agent_cli", None) or "").split(",") if a],
        "board": getattr(args, "board", None) or "",
        "budget": {"max_concurrent": int(getattr(args, "max_concurrent", 0) or 0)},
    }
    out = getattr(args, "out", None) or str(_agents_home() / HOST_CONFIG_NAMES[0])
    if os.path.isfile(out) and not getattr(args, "force", False):
        print(f"既に存在します: {out}（上書きするには --force）", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if yaml is not None:
        with open(out, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    else:
        write_json_atomic(out, data)
    print(f"ワーカーノード設定を書き出しました: {out}（node_id={node_id}）")
    print(f"起動: agent-project worker" + (f" --host-config {out}" if getattr(args, "out", None) else ""))
    return 0


def cmd_worker(args) -> int:
    """ワーカーノード（lite）の起動（実装計画 W1-11・設計 §4.3）。

    別プログラムではない——`agent-project serve` と同一実装（C12: プロファイル分岐は
    「projects が空か」の 1 点のみ）。projects が宣言されていれば警告するだけで動作は
    変えない（host.yaml の内容が正——このサブコマンド名はオペレータへの案内でしかない）。"""
    host = load_host_config(getattr(args, "host_config", None))
    if not host.is_worker:
        print(f"[agent-project] worker: host.yaml に projects が {len(host.projects)} 件"
             f"宣言されています。フルノードとして動作します"
             f"（ワーカー専用にするなら projects を空にしてください）。", file=sys.stderr)
    return cmd_serve(args)
