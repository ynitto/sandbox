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
#   - board tick（板の同期・ノード能力宣言 `nodes/<pc>.json` の書き出し・ノード宛て指示の
#     取り込み。実装計画 §7 R2a）
#   - gc tick（登録プロジェクトの agent-flow バス掃除。掃除の実装は持たない — R1）
# 参加 tick はどちらも「調停は都度起動の CLI・実行はプール」に揃えてある。周期を超えうる
# 仕事（run・手番）を tick 内で実行すると、self-watchdog がハングと読んで健全な常駐体を
# abort する。
#
# **落札した仕事の実行経路は 2 つ**（どちらも実行はプールへ渡し、tick では走らせない）:
#   - フルノード（プロジェクトを 1 つ以上持つ PC）… 各プロジェクトのバス経由
#     （`_flow_participate_tick`）。従来からの経路。
#   - ワーカーノード（projects 0）… ノード直轄実行（`_node_direct_flow_tick`・実装計画 §7 R2b）。
#     `~/.agents/flow-node/bus` を唯一の取り込み先にして、入札 → 落札 → 実行 → 板へ報告まで通す。
# board tick はどちらの経路が使えるかを `engine/status.json` の `board.intake_projects` /
# `board.node_direct` に出し、dashboard が手動入札のボタンを出すかどうかの根拠にする
# （操作だけ増えて実行できない状態を構造的に防ぐ）。

from types import SimpleNamespace

from agentcore.nodeid import normalize_node_id
from agentcore.protocol import write_json_atomic
from agent_project.resident import (CONTRACT_VERSION, ChildSpec, ChildStatus, EngineStatus,
                                    NodeCapability, NodeWorkerPool, Scheduler, Supervisor,
                                    SyncHealth, Tick, WorkItem, graceful_shutdown, run_gc)

HOST_CONFIG_NAMES = ("agent-project.host.yaml", "agent-project.host.yml",
                     "agent-project.host.json")

# tick が起動する外部コマンドの打ち切り時間。**同じ値を Tick.timeout にも渡す**
# （self-watchdog の猶予に入る）——ここと Scheduler で別々の数字を持つと、正当に長い tick が
# ハング扱いされて健全な常駐体が abort する。
_GC_PROJECT_TIMEOUT_SEC = 120.0
_AMIGOS_TICK_TIMEOUT_SEC = 60.0
_BOARD_TICK_TIMEOUT_SEC = 60.0
# 板の入札 lease（秒）。手動入札はこの猶予のあいだだけ「人が選別を上書きした」印として効く。
# agent-flow の board_lease 既定と同じ値にする——別の数字にすると、常駐体が書いた入札を
# 請負側が延長する前に失効させてしまう。
_BOARD_BID_LEASE_SEC = 900.0
# 心拍だけの `nodes/<pc>.json` 更新の律速（秒）。板は git リポジトリなので、30 秒 tick の
# たびに心拍を書き換えるとコミットが積み上がる。読む側は fresh_after_sec との比較で
# 生死を見るので、その猶予（下の係数）を割らない範囲で書かなければよい。
_NODE_HEARTBEAT_INTERVAL_SEC = 300.0
_NODE_FRESH_FACTOR = 4.0     # fresh_after_sec = 心拍間隔 × これ
# ノード宛て指示の「書きかけ猶予」（秒）。プロジェクト側 `debounce` の既定と同値にする
# ——利用者から見えるのは同じ 1 つの流れなので、猶予の長さも揃える。板 tick は 30 秒周期
# なので、猶予に掛かった指示は最悪 30 秒後の巡回で取り込まれる。
_NODE_COMMAND_DEBOUNCE_SEC = 3.0


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

    **実装は `agentcore.repolocal.normalize_repos` の 1 つ**（P2-5）。ここに写しを持っていた
    ため、mapping 形の `local: null` が片方では `"None"` という**文字列のパス**になっていた
    ——`agentcore.repolocal` は「同じ宣言が経路によって違って読める」を潰すために作った
    モジュールなので、その宣言の読み方をここで持ち直すのは動機に反する。

    人が手で書くので型は保証されない。list（正）のほか、旧 mapping 形式（url -> local）と
    素の文字列列も受ける。壊れていても例外にせず落とせる分だけ拾う——設定ミスでノードが
    起動しない方が、ローカル最適化が効かないことより高くつく（`_normalize_hooks` と同じ流儀）。"""
    return _repolocal.normalize_repos(raw)


def _str_list(raw, key: str, findings: "list[str] | None" = None) -> "list[str]":
    """`tags:` / `agent_cli:` を文字列の列へ。**スカラは 1 要素へ畳む**（P1-3）。

    素朴な `[str(a) for a in raw]` は文字列を**文字へ分解する**——`agent_cli: codex` が
    `["c","o","d","e","x"]` になり、板の `nodes/<id>.json` にその形で publish される。
    入札選別は fail-close（`agentcore.board.eligible`）なので、症状は誤動作ではなく
    「なぜかこの PC だけ仕事を取らない」という無言の不参加になり、いちばん追いにくい。
    `defaults.agent_cli`（このノードの既定 CLI・スカラ）と紛らわしいキーなので誤記は起きる。

    畳んだことは**必ず所見に残す**——黙って直すと「配列で書かなくても動く」という
    別の思い込みを作る（`_normalize_host_repos` が旧形式を受けるのと同じ流儀）。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        if findings is not None:
            # `agent_cli` はトップレベル（能力宣言の配列）と `defaults`（既定 CLI のスカラ）で
            # 意味が違う。スカラを書いた人は後者のつもりのことが多いので、行き先も示す。
            hint = ("（このノードの既定 CLI を指定したいなら `defaults.agent_cli:` です）"
                    if key == "agent_cli" else "")
            findings.append(f"{key}: は配列です（`{key}: [{raw}]` と書いてください）— "
                            f"1 要素の配列として読みます{hint}")
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(a) for a in raw]
    if findings is not None:
        findings.append(f"{key}: が配列ではありません（{type(raw).__name__}）— 無視します")
    return []


# host.yaml のトップレベルで意味を持つキー（このノードの宣言）。ここに無いキーは
# 読まれないので、書いた本人が「効いていない」ことに気付けるよう所見にする（S1 の設計動機）。
HOST_TOP_KEYS = frozenset({
    "schema_version",       # 受けるだけ（現在は読まない。契約のバージョニングは P2 以降）
    "node_id", "defaults", "projects", "repos", "tags", "agent_cli",
    "board", "board_workdir", "amigos_bus", "amigos_config", "budget", "update",
    "availability", "residency",
    "workloads",            # 引き受けるエンジン（板の入札選別・P2-3）。省略 = 制限しない
})

# `projects[]` の 1 要素で意味を持つキー。
HOST_PROJECT_KEYS = frozenset({"name", "state_repo", "branch", "root", "overrides",
                               "board_workdir"})

# 形が宣言と違うと黙って壊れるキー → 期待する型。
_HOST_MAPPING_KEYS = ("defaults", "budget", "update", "availability")


def host_config_findings(data: dict) -> "list[str]":
    """host.yaml の綻びを人が読む文の列にする（**判定だけ・出力はしない**）。

    純関数にするのは読み手が 2 人いるため——起動時の警告（`load_host_config`）と doctor。
    同じ規則を 2 実装にすると「doctor は緑なのに起動時は警告」という、いちばん人を
    混乱させる形になる（`agentcore.repolocal` に集約したのと同型の理由）。

    **警告どまりで起動は止めない**（S1 の E1/E2 とは強度が違う）。既存運用の host.yaml に
    残っている未知キーで、フリート全台を一斉に起動不能にする方が害が大きい。
    E への昇格は canary 明けの判断に回す。"""
    findings: "list[str]" = []
    if not isinstance(data, dict):
        return ["host.yaml の中身がマッピングではありません（無視します）"]
    for key in sorted(k for k in data if str(k) not in HOST_TOP_KEYS
                      and not str(k).startswith("_")):
        findings.append(f"{key}: は host.yaml では読まれません（無視します）— "
                        + _host_key_hint(str(key)))
    for key in _HOST_MAPPING_KEYS:
        if key in data and data[key] is not None and not isinstance(data[key], dict):
            findings.append(f"{key}: はマッピングです（{type(data[key]).__name__} を無視します）")
    for key in ("tags", "agent_cli", "workloads"):
        _str_list(data.get(key), key, findings)
    for w in _str_list(data.get("workloads"), "workloads"):
        if w not in ("flow", "amigos"):
            findings.append(f"workloads: の `{w}` は板の語彙にありません"
                            "（flow / amigos）— この宣言では板の仕事を 1 つも受けません")
    raw_budget = data.get("budget")
    raw_max = raw_budget.get("max_concurrent") if isinstance(raw_budget, dict) else None
    if raw_max is not None and (not isinstance(raw_max, (int, float))
                                or isinstance(raw_max, bool) or int(raw_max) < 0):
        findings.append(f"budget.max_concurrent: は 0 以上の整数です（{raw_max!r} を無視して"
                        "既定に戻します）— 0 は「無制限」の意味です")
    if data.get("projects") is not None and not isinstance(data.get("projects"), list):
        findings.append("projects: は配列です（無視します）")
    if data.get("repos") is not None and not isinstance(data.get("repos"), (list, dict)):
        findings.append("repos: は配列です（無視します）")
    for i, entry in enumerate(data.get("projects") or []
                              if isinstance(data.get("projects"), list) else []):
        if not isinstance(entry, dict):
            findings.append(f"projects[{i}]: はマッピングです（無視します）")
            continue
        for key in sorted(k for k in entry if str(k) not in HOST_PROJECT_KEYS):
            hint = ("設定は状態リポジトリ直下の agent-project.yaml が置き場です（S1）"
                    if str(key) == "config" else "無視します")
            findings.append(f"projects[{i}].{key}: は読まれません — {hint}")
    return findings


def _host_key_hint(key: str) -> str:
    """未知のトップレベルキーへの案内。**層違いは行き先を名指しする**——「未知のキー」
    だけだと、正しい置き場が分からないまま消すか放置するかになる。"""
    if key in _REMOVED_WORKTREE_KEYS:
        return ("廃止した状態 worktree 方式のキーです（状態ルートは状態専用リポジトリの "
                "clone に一本化しました・S1）")
    if key in SHARED_KEYS:
        return ("`defaults:` の下（このノード × このプロジェクトなら "
                "`projects[].overrides:`）へ書いてください")
    if key.startswith("update_"):
        return f"`update:` マッピング配下の `{key[len('update_'):]}:` が置き場です"
    if key in ("state_repo", "state_repo_branch"):
        return "`projects[].state_repo` / `projects[].branch` が置き場です"
    if key in CONFIG_DEFAULTS:
        return ("プロジェクトの合意なので、状態リポジトリ直下の agent-project.yaml へ"
                "書いてください")
    near = difflib.get_close_matches(key, sorted(HOST_TOP_KEYS), n=1, cutoff=0.7)
    return (f"`{near[0]}:` の綴り間違いではありませんか" if near else "綴りを確認してください")


# 同じ host.yaml について警告を出したパス（プロセス内で 1 度きり。`load_host_config` は
# CLI の実行と子プロセスのたびに呼ばれるので、毎回出すとログが埋まる）。
_HOST_WARNED: "set[str]" = set()


class HostConfig:
    """agent-project.host.yaml の内容（PC 宣言の単一ソース。設計 §4.2）。

    `projects` が空ならワーカーノード（lite）プロファイル（設計 §4.3）——別プログラムには
    せず、ロール分岐は「projects が空か」の 1 点だけに保つ（C12: フォークは重複実装の
    再演になる）。"""

    def __init__(self, data: dict, path: "str | None" = None):
        self.path = path
        # 読んだままの宣言。doctor が起動時と**同じ判定**（`host_config_findings`）を
        # 掛けられるようにするために持つ——doctor 用に読み直すと、読み方の 2 実装目になる。
        self.raw = dict(data) if isinstance(data, dict) else {}
        declared = str(data.get("node_id") or "").strip()
        # 「宣言されたか」を保つ: `resolve_config` は宣言 > 環境変数 > ホスト名の順に採る
        # （宣言があるのに AGENT_PROJECT_NODE で黙って別名になると、板の名義と claim の
        # 持ち主が食い違う）。node_id 自体は常に値を持つので、この旗が無いと区別できない。
        self.node_id_declared = bool(declared)
        self.node_id = normalize_node_id(declared) if declared else default_node_id()
        self.projects = [dict(p) for p in (data.get("projects") or []) if isinstance(p, dict)]
        self.tags = _str_list(data.get("tags"), "tags")
        # 能力宣言の `agent_cli:`（このノードで使える CLI の一覧・板への入札判定に使う）と、
        # `defaults.agent_cli`（このノードの既定 CLI・スカラ）は **別のキー**。前者は板の語彙、
        # 後者は設定の層（S1 の SHARED 群）で、混ぜると「1 つしか宣言していない PC の既定が
        # 勝手に変わる」ことになる。
        self.agent_cli = _str_list(data.get("agent_cli"), "agent_cli")
        # 板で引き受けるエンジン（P2-3）。**明示宣言だけを正**とし、`amigos_bus` の有無から
        # 導出しない——導出値を判定に使うと、宣言せずに amigos の板参加を起こしている PC が
        # 黙って入札をやめる。空 = 制限しない（板の語彙では「空 = 全部」）。
        self.workloads = _boardrules.declared_workloads(data if isinstance(data, dict) else {})
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
        # ノード全体の同時実行上限。**未宣言（None）と明示の 0 を区別する**（P2-3）。
        # 板の契約（`board.schema.json` の `$defs.node.max_concurrent`）では 0 = 無制限で、
        # 未宣言は「既定に従う」。以前は両方を 0 に潰しており、無制限を書く手段が無く、
        # しかも契約（0 = 無制限）と実装（0 = 既定 4）が真逆になっていた。
        budget = data.get("budget")
        raw_max = budget.get("max_concurrent") if isinstance(budget, dict) else None
        self.max_concurrent: "int | None" = (
            int(raw_max) if isinstance(raw_max, (int, float)) and not isinstance(raw_max, bool)
            and int(raw_max) >= 0 else None)
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
    data = _load_config_file(path)
    _warn_host_config(data, path)
    return HostConfig(data, path=path)


def _warn_host_config(data, path: str) -> None:
    """host.yaml の綻びを 1 度だけ報せる（P1-3）。

    プロジェクト yaml 側には層検査（S1 の E1/E2）と未知キー警告があるのに、host.yaml の
    トップレベルは誰も見ていなかった——`plan_review: false` を書いても `node_id` を
    `nodeid` と綴り間違えても、警告ゼロで黙って無視される。「設定したのに効かないことに
    気付けない」という S1 の設計動機が、host.yaml 側だけ抜けていた。"""
    if path in _HOST_WARNED:
        return
    _HOST_WARNED.add(path)
    for line in host_config_findings(data if isinstance(data, dict) else {}):
        print(f">>> 警告: host.yaml（{path}）: {line}", file=sys.stderr)


def _record_diagnostic_findings(host, status) -> int:
    """doctor の決定的所見（host.yaml の綻び・構造の到達不能）を横断エラーへ載せる（W15）。

    パイプは 1 本——所見 → `engine/status.json` の `recent_errors` → dashboard。所見の
    **種類**だけを増やし、露出の経路は増やさない。起動時警告（`_warn_host_config`）は
    stderr にしか出ず、常駐運用では誰も読まない（別 PC の dashboard からは尚更見えない）。
    同じ所見を毎 tick 積まないよう、既に載っている行は書かない（リングバッファの浪費防止）。"""
    findings = doctor_host_config_findings(host) + doctor_structure_findings()
    seen = set(status.recent_errors)
    n = 0
    for f in findings:
        line = f"doctor[{f['severity']}] {f['title']}: {f.get('evidence', '')}"[:400]
        if line not in seen:
            status.record_error(line)
            seen.add(line)
            n += 1
    return n


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


def node_commands_dir() -> Path:
    """ノード宛て指示ドロップの置き場（`schemas/agent-node-command.schema.json`）。

    `$AGENT_COMMANDS_DIR` > `~/.agents/commands`。`$AGENT_CONTROL_DIR`（望ましい状態）・
    `$AGENT_BUDGET_DIR`（予算）と同じノードスコープの並びで、**プロジェクトの `commands/` とは
    別物**——板はプロジェクトに属さないので、プロジェクト経由の口しか無いとプロジェクトを
    1 つも持たない PC（ワーカーノード）から板を操作できない。"""
    override = os.environ.get("AGENT_COMMANDS_DIR")
    return Path(override) if override else (_agents_home() / "commands")


# 同時実行上限を宣言していないノードの既定（旧 agent-flow daemon の `--max-workers`）。
# **「未宣言なら 4」は設定を読む側の既定**で、`NodeWorkerPool` の仕事ではない——プールが
# 既定を持つと、宣言を読む場所が 2 つになる（P2-3）。
_DEFAULT_MAX_CONCURRENT = 4


def _effective_max_concurrent(host: "HostConfig") -> int:
    """このノードの実効同時実行上限（板の語彙: 0 = 無制限）。

    | host.yaml | 実効値 |
    |---|---|
    | 未宣言 | 4（既定） |
    | `0` | 0（無制限・スキーマの語彙どおり） |
    | `n > 0` | n |
    """
    return _DEFAULT_MAX_CONCURRENT if host.max_concurrent is None else int(host.max_concurrent)


def _board_repo_declaration(repos: "list[dict]") -> "list[dict] | None":
    """host.yaml の `repos[]` → 板の `nodes/<id>.json` に載せる形（P2-2）。

    **`local` は落とす。** あれはこの PC にしか存在しない絶対パスで、板は共有リポジトリ
    ＝置いた瞬間に全 PC へ配られる。`repos.schema.json` が `local` を「ホスト固有なので
    共有レジストリには置けない」と宣言しているのと同じ理由がそのまま当たる（S3）。

    速度最適化のヒントとしての `local` は、**請負ノードが自分の host.yaml から**解決する
    （`agentcore.repolocal.merge_local`）ので板を経由する必要がそもそも無い。実測でも
    板の `local` を読む消費者はゼロだった——入札判定（`agentcore.board.declared_repo_ids`）は
    name と正規化 url しか見ず、画面（`board-adapter.listNodes`）は url からラベルを作るだけ、
    doctor は心拍しか見ない。

    `url` を持たないエントリは落とす（照合に使えず、読み手に空のラベルを出させるだけ）。
    """
    out = [{k: v for k, v in r.items() if k != "local"} for r in repos]
    return [r for r in out if str(r.get("url") or "").strip()] or None


def _node_capability(host: "HostConfig") -> dict:
    """host.yaml の宣言 → 板の `nodes/<node-id>.json`（`board.schema.json` の `$defs.node`）。

    載せるのは**他のノードが読んで意味を持つものだけ**: 担当リポジトリの url（入札の照合）・
    タグ・使える CLI・稼働時間帯・同時実行上限・契約バージョン・心拍。`local`（P2-2）は
    落とし、`workloads` は宣言があるときだけ載せる（P2-3）——導出値を宣言として配ると、
    owner-picks の落札判断と端末一覧が「host.yaml が言っていないこと」を読むことになる。
    """
    cap = NodeCapability(
        node=host.node_id,
        workloads=list(host.workloads),
        tags=list(host.tags),
        agent_cli=list(host.agent_cli),
        repos=_board_repo_declaration(host.repos),
        availability=_availability_declaration(host),
        # 未宣言のノードは「既定に従う」＝板から見れば無制限ではなく既定枠。板の語彙には
        # 「既定」が無いので、実効値（`_effective_max_concurrent`）を宣言する。
        max_concurrent=_effective_max_concurrent(host),
        heartbeat=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        fresh_after_sec=_NODE_HEARTBEAT_INTERVAL_SEC * _NODE_FRESH_FACTOR,
    )
    return cap.to_dict()


def _availability_declaration(host: "HostConfig") -> "str | None":
    """稼働時間帯の宣言を板の語彙（`"HH:MM-HH:MM TZ"`）へ。宣言が無ければ None（常時稼働）。

    host.yaml が持つのは停止時刻（`daily_stop`）だけで開始時刻は無い——「止まる時刻」しか
    宣言していないものを勝手に区間へ広げると、読む側に無い情報を作ることになる。
    停止時刻からその直前までを 1 日の稼働と読める形にして、時刻の解釈は読む側に委ねる。"""
    av = host.availability or {}
    stop = str(av.get("daily_stop") or "").strip()
    if not stop:
        return None
    tz = str(av.get("timezone") or "").strip()
    return f"-{stop} {tz}".strip() if tz else f"-{stop}"


def _board_intake_projects(host: "HostConfig") -> "list[str]":
    """落札した仕事を**実際に取り込めて実行できる**プロジェクト経路の名前一覧。

    板の請負は `agent-flow participate`（プロジェクトのバス経由）が担うので、
    バスを持つプロジェクトが 1 つも無いノード（＝ワーカーノード）にはこの経路が無い。
    ワーカーノードの取り込み先はノード直轄実行（R2b・`board.node_direct`）で、可否の判断は
    dashboard 側が両方を見る——ここは「プロジェクト経由の経路」だけを数える名前のまま保つ
    （名前が指す事実を広げると、画面がどちらの経路を見ているのか分からなくなる）。"""
    return [_project_name(p) for p in host.projects if str(p.get("root") or "").strip()]


def _board_node_direct(host: "HostConfig") -> bool:
    """このノードが**ノード直轄実行**（R2b）で落札した仕事を実行できるか。

    プロジェクトを持たないワーカーノードは、`~/.agents/flow-node/bus` を唯一の取り込み先に
    して板の仕事を実行する（`_node_direct_flow_tick`）。dashboard の手動入札ボタンは
    `intake_projects` とこの旗の**どちらか**が立っていれば有効になる——押せるのに何も
    起きない状態を作らない、という S8 §9-1 の約束はそのまま保つ。"""
    return bool(host.board) and host.is_worker


def _reject_node_command(path: str, why: str, status: "EngineStatus") -> None:
    """ノード宛て指示を `.err` へ退避し、**必ず** status にも残す。

    `engine/status.json` の `recent_errors` は画面の唯一の横断ビュー。ここに出ないと、
    板不一致・未知指示・公示不在で落ちた指示は `.err` を直接開くまで誰にも見えない
    （プロジェクト側は journal に残るので、ここだけ痕跡が薄かった）。"""
    _cmddrop.reject(path, why)
    status.record_error(f"node command {os.path.basename(path)}: {why}")


def _ingest_node_commands(host: "HostConfig", board: "BoardRepo",
                          status: "EngineStatus") -> "list[str]":
    """ノード宛て指示（`~/.agents/commands/*.json`）を取り込む。実行した指示の一覧を返す。

    形（`<name>.json` / `processed/` / `.err`）と述語は `agentcore.commands` と共有する
    ——プロジェクト配下の `commands/` と 2 種類の挙動を作らない（利用者から見えるのは
    「送信済み → 受理済み → 失敗バナー」の同じ 1 つの流れ）。

    書きかけには猶予を与える（スキーマは書き手として人を認めており、手置きは原子的とは
    限らない）。**猶予したら後続も処理しない**——指示はファイル名の時刻順＝処理順が規約で、
    同じ公示への「入札 → 中止」を飛び越えると中止済みの板へ入札を書くことになる。
    """
    cdir = node_commands_dir()
    done: "list[str]" = []
    for path in _cmddrop.pending(cdir, debounce_sec=_NODE_COMMAND_DEBOUNCE_SEC,
                                 stop_at_deferred=True):
        name = os.path.basename(path)
        rec, why = _cmddrop.read_command(path)
        if rec is None:
            # 猶予を過ぎても読めない＝書きかけではなく壊れている（再試行ループにしない）。
            _reject_node_command(path, why, status)
            continue
        action = str(rec.get("command") or "").strip()
        did = str(rec.get("id") or "").strip()
        reason = str(rec.get("reason") or "").strip()
        target = str(rec.get("board") or "").strip()
        if target and target != host.board:
            # 別の板宛ての指示を、宣言している板へ黙って適用しない。
            _reject_node_command(
                path, f"このノードの板と一致しません（宣言: {host.board or '未設定'}）", status)
            continue
        if action not in ("board-bid", "board-cancel", "board-award"):
            _reject_node_command(path, f"未知の指示です: {action or '(空)'}", status)
            continue
        if not did:
            _reject_node_command(path, f"{action} には id（委譲 id）が必要です", status)
            continue
        if board.read_post(did) is None:
            _reject_node_command(path, f"板に公示がありません: {did}", status)
            continue
        if board.is_terminal(did):
            _reject_node_command(path, f"終端済みの公示です（成果確定または中止済み）: {did}", status)
            continue
        detail = ""
        if action == "board-bid":
            wrote = board.write_bid(did, host.node_id, _BOARD_BID_LEASE_SEC,
                                    workload=str(board.read_post(did).get("workload") or "flow"))
            # 二度押しは失敗ではない（入札は冪等）が、**何も書かなかったことは残す**——
            # 常に「入札しました」と返すと、押したのに板へ届いていない場合と区別が付かない。
            detail = (f"{host.node_id} が入札しました" if wrote
                      else f"{host.node_id} の入札は既に有効です（延長は不要）")
        elif action == "board-cancel":
            board.write_cancelled(did, reason, host.node_id)
            detail = "中止しました"
        else:
            node = str(rec.get("node") or "").strip()
            if not node:
                _reject_node_command(path, "board-award には node（落札ノード）が必要です", status)
                continue
            board.write_award(did, node, host.node_id)
            detail = f"{node} に落札しました"
        board.sync_push(f"{action} {did} by {host.node_id}")
        _cmddrop.write_receipt(cdir, name, {"action": action, "id": did, "detail": detail})
        try:
            os.unlink(path)
        except OSError:
            pass
        # 同じ委譲 id の古い失敗退避を消す（画面の失敗バナーは id 単位。直ったのに
        # 失敗表示が残り続けるのを防ぐ。掃除は成功時だけ——プロジェクト側と同じ規則）。
        _cmddrop.clear_rejected(cdir, did)
        done.append(f"{action}:{did}")
    return done


def _board_participate_tick(host: "HostConfig", status: "EngineStatus") -> None:
    """板 tick（30s・設計 §4.2 の周期表「板（入札・依頼）」／実装計画 §7 R2a）。

    やること: 板の同期 → ノード能力宣言の書き出し → ノード宛て指示の取り込み →
    `engine/status.json` の `board` ブロック更新。**入札の自動判断はここでは行わない**——
    自動入札は各ツールの `participate`（プロジェクトのバス経由）が担っており、ここに 2 つ目の
    入札主体を置くと同じノードが二重に落札しにいく。ここが書く入札は「人が押した」分だけ。
    """
    if not host.board:
        status.board = {"configured": False}
        return
    board_state = {
        "configured": True,
        "location": host.board,
        "node_id": host.node_id,
        "contract_version": CONTRACT_VERSION,
        "intake_projects": _board_intake_projects(host),
        # ノード直轄実行（R2b）で請けられるか。`intake_projects` が空でも、この旗が立って
        # いれば落札した仕事は `~/.agents/flow-node/bus` で実行される。追加項目なので
        # contract_version は据え置き——読めない古い画面は従来どおり非活性へ縮退する。
        "node_direct": _board_node_direct(host),
        "last_tick": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "last_error": None,
    }
    try:
        board = BoardRepo(host.board, workdir=host.board_workdir)
        # 板の作業ディレクトリ（`git+` 板なら clone 先）。dashboard が読んでいる板のフォルダと
        # 同じ実体なので、**「画面が見ている板」と「この常駐体が参加している板」の突き合わせ**に
        # 使える。これが無いと dashboard は指示ドロップの `board:` に作業ディレクトリを入れる
        # しかなく、常駐体は所在（location）と完全一致で照合するため全指示が .err へ落ちた
        # （P0-2）。追加項目なので contract_version は据え置き——載らなければ画面は
        # 「古い実行エンジン」として board を省略して投函する（安全側へ縮退する）。
        board_state["workdir"] = board.dir
        board.sync_pull()
        if board.write_node(_node_capability(host),
                            heartbeat_interval=_NODE_HEARTBEAT_INTERVAL_SEC):
            board.sync_push(f"node {host.node_id}")
        _ingest_node_commands(host, board, status)
        open_ids, my_bids = [], []
        for did in board.list_delegations():
            if board.is_terminal(did) or board.read_post(did) is None:
                continue
            open_ids.append(did)
            if board.has_live_bid(did, host.node_id):
                my_bids.append(did)
        board_state["open_delegations"] = len(open_ids)
        board_state["my_bids"] = my_bids
    except (OSError, RuntimeError, ValueError) as e:
        # 板が落ちている・クローンが壊れている等。tick を落とさず状態に残す——ここで例外を
        # 上げると Scheduler が隔離し、板が復帰しても次の周期で自然に治らない。
        board_state["last_error"] = str(e)[:300]
        status.record_error(f"board tick: {e}")
    status.board = board_state


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


def node_flow_bus_dir() -> str:
    """ノード直轄実行のバス（`~/.agents/flow-node/bus`）。

    プロジェクトを 1 つも持たないワーカーノードには、落札した仕事を置く場所が無かった
    （板の請負はプロジェクトのバス経由だったため）。**PC に 1 つのバス**を用意して、
    そこを唯一の取り込み先にする——プロジェクトごとのバスと同じ形なので、実行・回収・
    掃除の経路はすべて既存のものがそのまま効く（フォークを作らない・設計 §9 C12）。"""
    return str(_agents_home() / "flow-node" / "bus")


def _node_agent_cli(host: "HostConfig") -> str:
    """ノード直轄実行で使うエージェント CLI。`defaults.agent_cli`（このノードの既定・スカラ）
    → 能力宣言 `agent_cli[]` の先頭 → 未指定（agent-flow の既定）。

    ワーカーノードはプロジェクト設定を持たないので、実行系の既定は agent-flow 自身の既定に
    従う。ノードが宣言しているのは「この PC で使える CLI」だけなので、渡すのもそれだけ。"""
    declared = str((host.defaults or {}).get("agent_cli") or "").strip()
    return declared or (str(host.agent_cli[0]).strip() if host.agent_cli else "")


def _node_flow_base(host: "HostConfig") -> "list[str]":
    """ノード直轄実行の agent-flow 共通 argv（グローバル引数）。

    板の場所は **host.yaml が正典**（S1: ノード固有宣言は host.yaml 専有）。フルノードでは
    flow の設定ファイルから解決していたが、ワーカーノードにはその設定ファイルが無い——
    宣言を持っている側から明示的に渡す。"""
    base = resolve_agent_flow(None) + ["--bus", node_flow_bus_dir(), "--board", host.board]
    if host.path:
        # 入札選別に使う宣言（repos / tags / agent_cli / workloads / budget）の在処。
        # 非既定の host.yaml で常駐している PC で、子だけ別の宣言を読まないため。
        base += ["--node-declaration", str(host.path)]
    cli = _node_agent_cli(host)
    if cli:
        base += ["--agent-cli", cli]
    return base


def _node_direct_flow_tick(host: "HostConfig", pool: "NodeWorkerPool",
                           status: "EngineStatus") -> None:
    """ノード直轄実行（実装計画 §7 R2b・設計 §4.2〜§4.3）。

    プロジェクトを 1 つも持たないワーカーノードが、板の公示に入札 → 落札 → ノードのバスへ
    取り込み → `NodeWorkerPool` で実行 → 板へ結果報告、まで通る唯一の経路。入札・落札・
    報告の実装は `agent-flow participate`（`poll_board`）が既に持っているので、ここが足すのは
    **その参加をプロジェクトではなくノードのスコープで 1 巡させること**だけ。

    ロール分岐は「プロジェクト子を起動しない・coordination に触れない」の 2 点に保つ
    （設計 §9 C12）。したがってこの tick はフルノードでは走らせない——フルノードには
    プロジェクトのバス経由の取り込み経路が既にあり、同じノードに 2 つ目の取り込み主体を
    置くと、板の枠（`max_concurrent` の自己抑制）の数え方が経路ごとに割れる。

    実行は必ずプールへ渡す（tick 内では実行しない）。run は周期を超えるので、tick で
    走らせると self-watchdog が健全な常駐体を abort する（参加 tick 共通の実行規約）。"""
    if not host.board or not host.is_worker:
        return
    busy = pool.busy_ids()
    running = sorted(r[len("node/"):] for r in busy if r.startswith("node/"))
    cmd = _node_flow_base(host) + ["participate", "--json", "--node-id", host.node_id]
    if running:
        # 渡さないと、枠が空くのを待っている run を毎周「駆動者が居ない孤児」と読んで
        # 再開回数を焼き、上限で failed に確定する（プロジェクト側の tick と同じ約束）。
        cmd += ["--running", ",".join(running)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_FLOW_TICK_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as e:
        status.record_error(f"node flow participate: {e}")
        return
    if proc.returncode != 0:
        status.record_error(f"node flow participate 失敗（rc={proc.returncode}）: "
                            f"{proc.stderr.strip()[-300:]}")
        return
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        return
    for item in items:
        rid = str((item or {}).get("run_id") or "").strip()
        if not rid:
            continue
        run_cmd = _node_flow_base(host) + ["--run-id", rid, "run", "--from-inbox"]
        pool.submit(WorkItem(id=f"node/{rid}", run=lambda c=run_cmd: subprocess.run(c)))


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
        # 板の入札選別に使うノード宣言（repos / tags / agent_cli）の在処を子へ渡す。
        # 常駐体が非既定の host.yaml（`--host-config`）で動いているとき、子だけが
        # 既定の探索順で別の宣言を読むと、入札の可否が親子で食い違う。
        if host.path:
            cmd += ["--node-declaration", str(host.path)]
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


def _sweep_node_commands() -> dict:
    """ノード宛て指示の残骸掃除（gc tick）。

    受理レシートは書き込みのたびに prune されるが、**失敗退避（`.err`）を消す口が
    無かった**——`clear_rejected` は「同じ委譲 id の指示が次に通ったとき」しか消せず、
    板の公示は終端すると消えるので、その id の指示は二度と来ない＝残り続ける。"""
    d = node_commands_dir()
    out = {"commands.err": _cmddrop.prune_rejected(d),
           "commands.receipts": _cmddrop.prune_receipts(d)}
    return {k: v for k, v in out.items() if v}


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

    # ノード直轄ワーカー（設計 §4.2・実装計画 W1-5/W1-11）。上限は板と同じ語彙で解決する
    # （未宣言 = 既定 4 / `0` = 無制限 / `n` = n・P2-3）。板の `nodes/<id>.json` に宣言する
    # 値と同じ関数から出すので、「板には 4 と言っておいて手元は無制限」が作れない。
    pool = NodeWorkerPool(
        _effective_max_concurrent(host),
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
        # ノード直轄実行（R2b）。プロジェクトを持たないワーカーノードでだけ走る——
        # 上の tick が回るプロジェクトが 1 つも無い PC の、唯一の取り込み経路。
        _node_direct_flow_tick(host, pool, status)
        status.tick_counts["flow"] = status.tick_counts.get("flow", 0) + 1
        write_status()

    def tick_board() -> None:
        _board_participate_tick(host, status)
        status.tick_counts["board"] = status.tick_counts.get("board", 0) + 1
        write_status()

    def tick_gc() -> None:
        _record_diagnostic_findings(host, status)   # 綻び・到達不能の所見を横断エラーへ（W15）
        run_gc(_project_gc_sweepers(host) + [("board", lambda: _sweep_terminal_delegations(host)),
                                            ("commands", _sweep_node_commands)],
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
                       # 板 tick は 30s（設計 §4.2 の周期表）。short-lived——入札の意思を
                       # 板へ書くだけで、落札した仕事の実行は flow tick 側の経路に任せる。
                       Tick("board", 30.0, tick_board, timeout=_BOARD_TICK_TIMEOUT_SEC),
                       Tick("gc", 600.0, tick_gc, timeout=gc_budget)],
                      on_tick_error=lambda name, exc: status.record_error(f"{name}: {exc}"))
    return sup, sched, status, write_status, pool


def _board_away_announcer(host: "HostConfig") -> "tuple":
    """graceful 停止に注入する (away 宣言, 最終 push) の 2 ステップを組み立てる。

    板を宣言していない PC では両方 None（`graceful_shutdown` は None のステップを飛ばす）。
    **停止経路は失敗しても停止を止めない**——板が落ちている・クローンが壊れているときに
    例外を投げると、子を畳んだ後の停止が止まり、起動系が SIGKILL するまで残る。

    away を書くのは「まだ実行中と board に見えている委譲」だけ（`announce_away`）。書けた
    ものがあるときだけ push する——空 push は板に無意味なコミットを積む。"""
    if not host.board:
        return (None, None)
    state: dict = {"board": None, "touched": []}

    def _announce() -> None:
        try:
            board = BoardRepo(host.board, workdir=host.board_workdir)
            board.sync_pull()
            state["board"] = board
            state["touched"] = board.announce_away(host.node_id)
        except (OSError, RuntimeError, ValueError) as e:
            print(f"[agent-project] serve: 板への離席宣言に失敗（停止は続けます）: {e}",
                  file=sys.stderr)

    def _push() -> None:
        board, touched = state.get("board"), state.get("touched") or []
        if board is None or not touched:
            return
        try:
            board.sync_push(f"away {host.node_id}（{len(touched)} 件の実行中を離席）")
        except (OSError, RuntimeError, ValueError) as e:
            print(f"[agent-project] serve: 板への最終反映に失敗（停止は続けます）: {e}",
                  file=sys.stderr)

    return (_announce, _push)


SERVE_BANNER = "[agent-project] serve: node_id="
"""起動バナーの接頭辞。**「この行が出た後はシグナルを取りこぼさない」という約束**の
観測点で、テストの待ち合わせにも使う（`cmd_serve` はこれを出す前にハンドラを設置する）。"""


def _install_stop_signals() -> "threading.Event":
    """SIGTERM/SIGINT を graceful 停止要求（Event）へ変換する。

    既定ハンドラは即死なので、設置前に SIGTERM が届くと `cmd_serve` の finally
    （子の graceful 停止）が走らず、`run --watch` の子が監督者不在のまま生き残る——
    次の起動で Supervisor が新しい子を起こすので同一プロジェクトにループが 2 本並ぶ。
    systemd の stop/restart は SIGTERM なので、`install.sh --service` が勧める経路が
    そのままこれを踏む。**したがって設置は子を起こすより前**（P0-1）。

    2 度目のシグナルは握らずに既定ハンドラへ戻す。停止処理そのものが詰まったときに、
    人（Ctrl-C 2 回）と起動系（SIGKILL 前の再送）が諦める手段を残すため。"""
    stopping = threading.Event()

    def _handler(signum, _frame):
        if stopping.is_set():
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass    # メインスレッド以外（テスト等）では設定できない
    return stopping


def cmd_serve(args) -> int:
    """常駐体のエントリポイント（設計 §4.2・実装計画 W1-11）。フォアグラウンド実行——
    常駐化（起動時に上がる・死んだら上げ直す）は起動系（systemd 等）の役目で、このコマンド
    自体は「起動されたら動き続ける」だけを担う（設計 §7）。

    順序が契約: **シグナルハンドラ → バナー → 子の起動 → 状況の書き出し → tick 開始**。
    ハンドラより後ろは、どこで停止要求が届いても finally が子を畳む。"""
    stopping = _install_stop_signals()
    host = load_host_config(getattr(args, "host_config", None))
    print(f"{SERVE_BANNER}{host.node_id}"
         + (f" host_config={host.path}" if host.path else "（host.yaml 未検出・既定ノード）"),
          flush=True)
    if host.is_worker:
        print("[agent-project] serve: projects 未宣言 — ワーカーノード（lite）として動作します",
              flush=True)

    sup = sched = None
    try:
        sup, sched, status, write_status, pool = _build_resident(host)
        # 子の起動中に停止要求が入っていたら、ここから先へは進まない。write_status() は
        # git 観測を含んで数秒かかりうるので、畳む直前に「子は生きている」と書きながら
        # 起動系の停止猶予を食い潰すのは損しかない。
        if not stopping.is_set():
            write_status()
            sched.start()
            print(f"[agent-project] serve: 起動しました"
                  f"（projects={len(sup.names())} pid={os.getpid()}）", flush=True)
            while not stopping.wait(1.0):
                pass
    except KeyboardInterrupt:
        pass        # ハンドラを設置できない環境（メインスレッド外）の保険
    finally:
        print("[agent-project] serve: 停止します（子の graceful 停止）", file=sys.stderr)
        if sched is not None:
            sched.stop()
        if sup is not None:
            # 停止の 4 ステップのうち、板に関わる 2 つ（away 宣言・最終 push）を注入する
            # （設計 §4.2・P0 詳細設計 §7-F2 が「R2b で一緒に設計する」と予約していた分）。
            # claims / lease の解放は現状 lease 失効が吸収するので注入しない——落札した
            # 仕事の在処だけが、待っている人から見えない状態だった。
            away = _board_away_announcer(host)
            graceful_shutdown(sup, announce_away=away[0], final_push=away[1],
                              on_step=lambda label: print(
                                  f"[agent-project] serve: 停止手順 {label}", file=sys.stderr))
        if sched is not None:
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
    node_id = (normalize_node_id(getattr(args, "node_id", None))
               if getattr(args, "node_id", None) else default_node_id())
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
    }
    # 板の契約（board.schema.json §max_concurrent）は「0/省略=無制限、宣言時のみ数値上限」。
    # CLI 未指定（None）のときは budget キー自体を書かない——0 を既定値として書くと
    # 「省略」のつもりが「0 を明示宣言」になり、契約上は無制限を意味してしまう（HostConfig
    # 側の None/0 区別・resident_cli.py の max_concurrent 参照と合わせる）。
    _max_concurrent = getattr(args, "max_concurrent", None)
    if _max_concurrent is not None:
        data["budget"] = {"max_concurrent": int(_max_concurrent)}
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
