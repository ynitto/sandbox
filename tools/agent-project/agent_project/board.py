from __future__ import annotations
# board.py — 委譲公示板（agent-board）への依頼側アクセス。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# agent-project は依頼側（バックログを持ち、重い作業を board へ出す）。板は「リポジトリ＋契約」
# だけで処理を持たない（schemas/board.schema.json）。入札・実行は請負側（agent-flow /
# agent-amigos の board 参加デーモン）が担い、完了したら板の result.json へ書き戻す
# （agent_flow/board.py・agent_amigos/board.py の report_results）。ここではその板を
# ポーリングして post を書き・result を読むだけ（結合はデータ契約のみ・エンジンの中身は
# import しない）。手動投函（`board-offload` サブコマンド）と daemon の自動配線
# （§ decide_location location=board・flow.py の _act_board）の両方がこのモジュールを使う。

try:
    import fcntl  # POSIX（macOS/Linux/WSL・install.sh の対象 OS）
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

# `_transport`（共通 git 転送層）は _head.py が共有名前空間へ入れている。


def _safe_node(who: str) -> str:
    """ノード id → ファイル名。板の他の実装（agent-flow / agent-amigos の `_safe`）と同じ規則。"""
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(who)) or "x"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_age_base(ts: "str | None") -> float:
    """ISO8601 の時刻を epoch 秒にする（読めなければ 0＝「とても古い」）。
    心拍の鮮度判定にだけ使うので、読めない値は「書き直すべき」に倒す。"""
    if not ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class BoardRepo:
    """委譲公示板への依頼側アクセス。ローカル dir / git+<url> の両対応。

    agent-project の並列消費（`_act_batch` の ThreadPoolExecutor）から複数タスクが同時に board を
    叩きうるため、git 操作（clone・pull・push）はプロセス間 flock で直列化する
    （agent-flow / agent-amigos の claim ロックと同じ技法・別実装）。転送の実体は
    `agentcore.transport.GitTransport`（stale lock 掃除・中断 rebase の abort・
    fsck プローブ・破損時の退避→再クローン→復元・durable-write・push 指数バックオフ
    ——force push はしない）。ブランチは board の規約どおり単一 main
    （設計 §4.2 — 会話が無く書き込み頻度が低いためミッション別分離は不要）。"""

    def __init__(self, spec: str, workdir: "str | None" = None):
        spec = str(spec or "").strip()
        self.git = spec.startswith("git+")
        if self.git:
            self.remote = spec[4:]
            base = workdir or os.path.join(
                os.path.expanduser("~/.agents/project-board"),
                hashlib.sha1(self.remote.encode()).hexdigest()[:8])
            self.dir = os.path.abspath(base)
            self._transport = _transport.GitTransport(
                self.dir, self.remote, branch="main",
                managed_flag="agent-project.board",
                commit_user_name="agent-project", commit_user_email="agent-project@local")
        else:
            self.remote = None
            self.dir = os.path.abspath(spec)
            self._transport = None

    def _lock_path(self) -> str:
        h = hashlib.sha1(os.path.realpath(self.dir).encode()).hexdigest()
        d = os.path.join(tempfile.gettempdir(), "agent-project-board-locks")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{h}.lock")

    @contextlib.contextmanager
    def _locked(self):
        if fcntl is None:  # pragma: no cover — 非 POSIX 環境のみ（想定外）
            yield
            return
        f = open(self._lock_path(), "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()

    def _ensure(self) -> None:
        """dir を用意する（呼び出しは _locked() の中から）。git なら transport.ensure_clone に
        委ねる（未クローン時は clone・再利用時は自己回復。git でなければ delegations/ だけ作る）。"""
        if not self.git:
            os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)
            return
        self._transport.ensure_clone()
        os.makedirs(os.path.join(self.dir, "delegations"), exist_ok=True)

    def sync_pull(self, force: bool = False, interval: float = 20.0) -> None:
        """fetch/pull（間隔律速）。ローカル dir なら no-op（毎回最新）。"""
        with self._locked():
            self._ensure()
            if not self.git:
                return
            self._transport.interval = interval
            self._transport.sync_pull(force=force)

    def sync_push(self, msg: str) -> None:
        """add -A && commit && push（push 競合は pull --rebase → 再 push の指数バックオフ。
        force push はしない）。ローカル dir なら no-op。"""
        with self._locked():
            if not self.git:
                return
            self._ensure()
            self._transport.sync_push(msg or "board update")

    def delegation_dir(self, did: str) -> str:
        return os.path.join(self.dir, "delegations", str(did))

    def has_post(self, did: str) -> bool:
        return os.path.exists(os.path.join(self.delegation_dir(did), "post.json"))

    def write_post(self, env: dict) -> bool:
        """post.json を書く（冪等 — 既存なら何もせず False。新規に書けたら True）。
        呼び出し側は True のときだけ sync_push すればよい（無駄な空 commit を作らない・
        同一 id の再投函は同一公示という設計の二重公示防止をここで担保する）。"""
        with self._locked():
            self._ensure()
            path = os.path.join(self.delegation_dir(env["id"]), "post.json")
            if os.path.exists(path):
                return False
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(env, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True

    # ------------------------------------------------------------------
    # 請負側（ノード名義）の書き込み — 常駐体の board tick だけが呼ぶ（R2a）
    #
    # 板へ書けるのは常駐体だけ、という規律をここで担保する。dashboard は板の作業クローンへ
    # 直接ファイルを置いていたが、それを push する主体が居ないので `git+` 板では誰にも
    # 届いていなかった（S8-2 の本当の理由）。書き込みの入口をこの層に集約し、呼び出し元は
    # ノード宛て指示ドロップ（`~/.agents/commands/`）経由にする。
    #
    # **書き込みは全て `with self._locked(): self._ensure()` を通す**（P2-4）。競合相手は
    # 実在する: 転送層の破損時再クローン（`transport._reset_clone_dir` の `rmtree`）と
    # `sync_push` の `pull --rebase` はどちらも作業ツリーを動かし、どちらも `_locked()` の
    # 内側で走る。ロックを取らずに書くと、入札や中止マーカーが再クローンごと消えうる。
    # 読みは取らない（壊れて読めなければ None に倒れるだけ）。
    #
    # **この中から `BoardRepo.sync_push` を呼んではいけない。** `_locked()` は `fcntl.flock`
    # で、同一プロセスが別の fd で同じロックを取ると自分自身と競合して止まる。push は
    # 呼び出し側（常駐体の指示取り込み）が外側で 1 回だけ行う。ロックの入れ子は
    # **板 → claim（`agentcore-claim-locks`）の一方向だけ**——逆順に取る経路を作らないこと。
    # ------------------------------------------------------------------

    def list_delegations(self) -> "list[str]":
        root = os.path.join(self.dir, "delegations")
        try:
            return sorted(d for d in os.listdir(root)
                          if os.path.isdir(os.path.join(root, d)))
        except OSError:
            return []

    def read_delegation_json(self, did: str, *parts: str) -> "dict | None":
        path = os.path.join(self.delegation_dir(did), *parts)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def read_post(self, did: str) -> "dict | None":
        return self.read_delegation_json(did, "post.json")

    def is_terminal(self, did: str) -> bool:
        """終端（成果が確定した / 中止された）公示か。入札も落札もしてはいけない。"""
        d = self.delegation_dir(did)
        return os.path.exists(os.path.join(d, "result.json")) or \
            os.path.exists(os.path.join(d, "cancelled.json"))

    def node_path(self, node_id: str) -> str:
        return os.path.join(self.dir, "nodes", f"{_safe_node(node_id)}.json")

    @staticmethod
    def _without_stamps(rec: dict) -> dict:
        """内容の比較から時計を外す。

        **`heartbeat` を外すだけでは足りない。** `budget.observed_at` が同じ時刻の写しで
        （`resident_cli._node_capability` が heartbeat をそのまま渡す）、外さないと秒を
        またぐたび「内容が変わった」と判定される。tick は 30 秒間隔なので前回書いた秒とは
        必ず違い、**間引きが一度も効かないまま毎 tick 板へ書いていた**（docstring が避けよう
        としている「無意味なコミット」がそのまま起きる）。
        """
        out = {k: v for k, v in rec.items() if k != "heartbeat"}
        budget = out.get("budget")
        if isinstance(budget, dict):
            out["budget"] = {k: v for k, v in budget.items() if k != "observed_at"}
        return out

    def write_node(self, cap: dict, *, heartbeat_interval: float = 300.0) -> bool:
        """ノードの能力宣言 `nodes/<node-id>.json` を書く（書いたら True・S3-5 / P1-a）。

        **心拍だけの更新は `heartbeat_interval` に律速する。** 30 秒ごとに心拍を書き換えると
        板に無意味なコミットが積み上がる——読む側は `fresh_after_sec` との比較で生死を見るので、
        その猶予を割らない範囲で書かなければよい。内容（能力・担当リポジトリ）が変わったときは
        間隔に関わらず即座に書く（宣言の変更が反映されない方が害が大きい）。
        """
        with self._locked():
            self._ensure()
            path = self.node_path(str(cap.get("node") or ""))
            cur = None
            try:
                with open(path, encoding="utf-8") as f:
                    cur = json.load(f)
            except (OSError, ValueError):
                cur = None
            if isinstance(cur, dict):
                if self._without_stamps(cur) == self._without_stamps(cap):
                    age = time.time() - _iso_age_base(cur.get("heartbeat"))
                    if age < max(0.0, float(heartbeat_interval)):
                        return False
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_json_atomic(path, cap)
            return True

    def write_bid(self, did: str, node_id: str, lease: float,
                  workload: str = "flow") -> bool:
        """自ノード名義の入札を書く／延長する（手動入札の実体・S8-3）。

        戻り値は「実際に書いたか」——`renew_lease` は残 lease が半分以上あれば書かずに
        False を返す（冪等）。呼び出し側はこれを使って「入札しました」と「既に有効です」を
        区別する（捨てると、押したのに何も書かれていない場合が観測できない）。

        アルゴリズムは `agentcore.protocol.renew_lease`——自動入札（agent-flow / agent-amigos）と
        **同一実装**にする。二重落札を防ぐ規則（lease と `(ts, who)` タイブレーク）の 2 つ目の
        実装を作らないのが手動入札の設計要件（S8-3）。
        """
        with self._locked():
            self._ensure()
            bids = os.path.join(self.delegation_dir(did), "bids")
            return _protocol.renew_lease(bids, node_id, lease, extra={"workload": workload})

    def announce_away(self, node_id: str) -> "list[str]":
        """この端末が離席することを板へ宣言する（graceful 停止の 4 ステップ・設計 §4.2）。

        自分が落札して**まだ終端していない**委譲の `status/<who>.json` を `away` にする。
        away は `_renew_dispatched_leases`（agent-flow）が「延長しない」と読む状態なので、
        入札の lease は自然に失効し、依頼側は再入札で別の端末へ回せる。

        これを書かずに落ちると、板から見えるのは「心拍が止まった dispatched」だけになり、
        依頼側は lease 失効を待つあいだ「応答なし」を見続ける（P0 詳細設計 §7-F2）。
        ノード直轄実行（R2b）で「落札した仕事を持つワーカーノードが落ちる」経路が現実に
        なったので、停止側から明示的に宣言する。書き換えた委譲 id を返す（push は呼び出し側）。"""
        touched: "list[str]" = []
        with self._locked():
            self._ensure()
            for did in self.list_delegations():
                if self.is_terminal(did):
                    continue
                rec = self.read_delegation_json(did, "status", f"{_safe_node(node_id)}.json")
                if not isinstance(rec, dict):
                    continue
                state = str(rec.get("state") or "")
                if state != "dispatched":
                    continue      # 自分が実行中の委譲だけ（終端済み・away は触らない）
                path = os.path.join(self.delegation_dir(did), "status",
                                    f"{_safe_node(node_id)}.json")
                write_json_atomic(path, {**rec, "state": "away", "heartbeat": _now_iso_utc()})
                touched.append(did)
        return touched

    def has_live_bid(self, did: str, node_id: str) -> bool:
        rec = self.read_delegation_json(did, "bids", f"{_safe_node(node_id)}.json")
        try:
            return bool(rec) and float(rec.get("lease_until") or 0) >= time.time()
        except (TypeError, ValueError):
            return False

    def write_cancelled(self, did: str, reason: str, by: str) -> str:
        """中止マーカーを書く（S8-2）。

        板の契約ではこのパスの書き手は「依頼者」だが、**パス単位の書き込み所有権は git で
        コンフリクトさせないための規約であって認可ではない**。止める判断は人にあり、
        人がどの PC の前に座っているかで可否が変わるのは筋が悪い。誰が止めたかは残す。
        """
        with self._locked():
            self._ensure()
            path = os.path.join(self.delegation_dir(did), "cancelled.json")
            write_json_atomic(path, {"reason": str(reason or ""), "cancelled_by": str(by or ""),
                                     "cancelled_at": _now_iso_utc()})
            return path

    def write_award(self, did: str, node: str, by: str) -> str:
        """owner-picks の落札を確定する。

        Phase2: award と同時に板ルートへ budget reservation を作成（claim と同契約）。
        board は任意のまま。予約失敗（unknown/不変条件）でも award 自体は残す——
        落札記録の喪失より予約欠落の方が回復しやすい。不変条件違反は journal 相当を残さない
        （板に journal が無い）が reservations は作らない。
        """
        with self._locked():
            self._ensure()
            path = os.path.join(self.delegation_dir(did), "award.json")
            awarded_at = _now_iso_utc()
            write_json_atomic(path, {"node": str(node), "awarded_by": str(by or ""),
                                     "awarded_at": awarded_at})
            self._create_award_reservation(did, str(node), awarded_at)
            return path

    def _create_award_reservation(self, did: str, node: str, awarded_at: str) -> None:
        """板ルートに award 経路の reservation を書く（失敗は award をロールバックしない）。"""
        try:
            root = Path(self.dir)
            # 板 nodes/<node>.json の budget ミラーがあればゲート材料にする
            status_record = None
            node_path = root / "nodes" / f"{_safe_node(node)}.json"
            try:
                raw = json.loads(node_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else None
                    if budget is None and isinstance(raw.get("capacity"), dict):
                        budget = {
                            "contract_version": 1,
                            "source": "local-ledger",
                            "capacity": raw.get("capacity"),
                            "can_accept": raw.get("can_accept", True),
                            "reason_codes": list(raw.get("reason_codes") or ["ok"]),
                        }
                    if isinstance(budget, dict):
                        status_record = {
                            "node": node,
                            "availability": "active",
                            "updated_iso": awarded_at,
                            "fresh_after_sec": 3600.0,
                            "budget": budget,
                        }
            except (OSError, ValueError, TypeError):
                status_record = None
            if status_record is None:
                # ミラー前・board 単独: unlimited 相当で予約量 0 の live を残し追跡可能にする
                status_record = {
                    "node": node,
                    "availability": "active",
                    "updated_iso": awarded_at,
                    "fresh_after_sec": 3600.0,
                    "budget": {
                        "contract_version": 1,
                        "source": "local-ledger",
                        "capacity": {"limit": None, "used": None, "reserved": None},
                        "unit": None,
                        "can_accept": True,
                        "reason_codes": ["unlimited"],
                        "enforce": False,
                    },
                }
            gate = status_budget_gate(status_record, enforce_default=False)
            # Config 無しでも動く最小スタブ（board は agent-project Config に依存しない）
            class _Cfg:
                backlog = root / "backlog"
                journal = None
                budget_summary = None
                controller_lease_sec = 900.0
            create_reservation_in_root(
                root, _Cfg(), node=node, source="award", gate=gate,
                status_record=status_record, delegation_id=did,
                claim_token=f"award:{did}:{node}", at=None,
                ttl_sec=900.0, journal=False)
        except Exception:  # noqa: BLE001 — award 本体を守る
            pass
    def read_result(self, did: str) -> "dict | None":
        path = os.path.join(self.delegation_dir(did), "result.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError):
            return None

    def is_cancelled(self, did: str) -> bool:
        return os.path.exists(os.path.join(self.delegation_dir(did), "cancelled.json"))

    def drop_delegation(self, did: str) -> bool:
        """終端を回収し終えた公示を板から消す（消せたら True）。

        **消してよいと知っているのは依頼側だけ**なので、依頼側が settle した直後に呼ぶ。
        時間ベースの一括掃除だと、その期間オフラインだった依頼側が結果を読む前に消えて
        `read_result` が None を返し、offloaded タスクが未終端のまま永久に固まる
        （タイムアウトが無い）。読んだ後にしか消えない形にすれば取りこぼしが原理的に起きない。

        板は調整用のバスで履歴の置き場ではない（run 履歴は flow 側が持つ）。settle 後の
        delegation を参照する経路はコードベースに無い。"""
        with self._locked():
            self._ensure()
            d = self.delegation_dir(did)
            if not os.path.isdir(d):
                return False
            shutil.rmtree(d, ignore_errors=True)
            return not os.path.isdir(d)

    def sweep_terminal_delegations(self, older_than_sec: float) -> int:
        """終端して `older_than_sec` 以上経った公示を掃除して件数を返す（孤児の保険）。

        通常の削除は依頼側が結果を読んだ直後の `drop_delegation`。ここが拾うのは、
        依頼側の PC ごと消えた等で誰も回収しなくなった孤児だけなので、マージンは長く取る
        ——**読む前に消すと offloaded が未終端のまま永久に固まる**（回収にタイムアウトが
        無い）ので、まだ結果を読んでいない依頼側を巻き込まないことが最優先。

        板の層に置くのは、レイアウト（`delegations/<id>/result.json`）と flock を
        既に持っているのはここだけだから。呼び出し側（常駐体の gc tick）が同じ知識を
        持つと、git+ 板では clone を見に行けず掃除が黙って no-op になる。"""
        with self._locked():
            self._ensure()
            root = os.path.join(self.dir, "delegations")
            if not os.path.isdir(root):
                return 0
            cutoff = time.time() - max(0.0, float(older_than_sec))
            removed = 0
            for did in sorted(os.listdir(root)):
                d = os.path.join(root, did)
                if not os.path.isdir(d):
                    continue
                stamps = []
                for mark in ("result.json", "cancelled.json"):
                    with contextlib.suppress(OSError):
                        stamps.append(os.stat(os.path.join(d, mark)).st_mtime)
                if not stamps or max(stamps) > cutoff:
                    continue    # 未終端、または終端したばかり（依頼側がまだ読むかもしれない）
                shutil.rmtree(d, ignore_errors=True)
                if not os.path.isdir(d):
                    removed += 1
            if removed and self.git:
                self._transport.sync_push(f"gc: 孤児の公示 {removed} 件を掃除")
            return removed


def _deleg_id_from_task(tid: str) -> str:
    """タスク id だけから委譲 id を作る（[A-Za-z0-9_-]{1,64}）。delegation_id 未指定時のフォールバック。
    自動配線（_act_board）は常に _board_delegation_id（cfg を使う決定的版）を渡すため、
    これは手動呼び出し・テストでの簡易フォールバックに留まる。"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(tid or "")).strip("-") or "task"
    return ("dg-" + safe)[:64]


def _board_delegation_id(task: "Task", cfg: "Config") -> str:
    """タスクから委譲 id を決定的に作る（[A-Za-z0-9_-]{1,64}）。(backlog, task.id, retries, rev) が
    同じなら同じ id になる＝再投函は同一公示（冪等・二重公示防止。agent-flow の _req_id_for と
    同じ再試行契約 — retries/rev が変われば新しい委譲になる）。"""
    h = hashlib.sha1(str(cfg.backlog.resolve()).encode()).hexdigest()[:8]
    tid = re.sub(r"[^A-Za-z0-9_-]+", "-", str(task.id)).strip("-")[:40] or "task"
    rev = str(task.get("rev", "") or "").strip()
    rev_sfx = ("-v" + re.sub(r"[^A-Za-z0-9_-]+", "-", rev)) if rev else ""
    return f"dg-{h}-{tid}-r{task.retries}{rev_sfx}"[:64]


def workset_offload_blocked(specs: "list[dict]") -> str:
    """書込先の集合を板へ出せない理由（出せるなら空文字）。

    `workspaces` を知らない請負ノードはそれを未知キーとして無視し **primary だけに書く**。
    記録には成功として残るので、静かな部分実行になる。入札選別の契約版
    （`agentcore.board.CONTRACT_VERSION`）は完全一致なので、フリートを静止点で一斉に
    上げるまで（設計 §7 の P4）依頼側が出さないのが唯一の安全弁である。"""
    if len(specs or []) <= 1 or _boardrules.workset_posts_supported():
        return ""
    return (f"書込先が {len(specs)} つ（workset）ある仕事は、フリートの委譲契約が版 "
            f"{_boardrules.WORKSET_CONTRACT_VERSION} へ上がるまで板へ出しません"
            "（版 1 のノードは primary にしか書かないため）")


def task_to_delegation(task: "Task", spec: "dict | None", workload: str = "flow",
                       delegation_id: "str | None" = None, request: "str | None" = None,
                       references: "list[dict] | None" = None,
                       workset: "list[dict] | None" = None) -> dict:
    """タスク＋解決済み workspace spec から delegation post 封筒を組み立てる。

    goal は request（build_request の全文。省略時は task.title）をそのまま使う——ローカル run /
    daemon submit と同じ文脈（charter・rules・decisions・run ブリーフ等）を board 経由でも
    欠かさない（自動配線が location を board に振り替えても、実行者が受け取る指示は変わらない）。
    workspace.url がそのまま「そのリポジトリを担当する board ノードだけが入札する」選別条件になる
    （board_eligible は workspace.url を URL 正規化で突き合わせる。requires.repos は追加しない —
    spec["name"] は依頼側のローカルなルーティング名で、請負側ノードが同じリポジトリを別名で
    宣言しているとURL一致でも入札不能になる誤検出を生むため）。

    `workset`（書込先の集合）が 2 要素以上のときだけ `workspaces[]` を足し、あわせて
    `requires.repos`（全要素の **URL**。名前ではないので上の誤検出は起きない）と
    `requires.contract_version` を付ける——集合を知らないノードが primary だけに書く
    「静かな部分実行」を、版の完全一致で不参加へ倒すため（設計 §5.7）。"""
    did = delegation_id or _deleg_id_from_task(task.id)
    goal = request if request else (task.title or task.id)
    env: dict = {
        "op": "post", "version": 1, "id": did, "workload": workload,
        "goal": goal, "title": task.title or "", "requested_by": "agent-project",
    }
    if not request:
        # request（全文）が無いフォールバック時だけ、desc/why から design を簡易合成する
        desc = task.get("desc") or task.get("why") or ""
        if desc:
            env["design"] = str(desc)

    def _ws_view(sp: dict, named: bool) -> dict:
        view = {"url": sp["url"]}
        for k in ("path", "base", "target"):
            if sp.get(k):
                view[k] = sp[k]
        if named:
            view["name"] = workset_element_name(sp)
        return view

    elements = [sp for sp in (workset or []) if isinstance(sp, dict) and sp.get("url")]
    if isinstance(spec, dict) and spec.get("url"):
        env["workspace"] = _ws_view(spec, named=len(elements) > 1)
    if len(elements) > 1:
        # 書込先の集合。`workspace` は primary として引き続き載る（集合を知らない読み手が
        # そのまま動く）。`requires` は「この全 repo を担当するノードだけが入札する」条件で、
        # 契約版は workset を扱えるノードだけに絞る（版 1 のノードは fail-close で不参加）。
        env["workspaces"] = [_ws_view(sp, named=True) for sp in elements]
        requires = env.get("requires") if isinstance(env.get("requires"), dict) else {}
        requires["repos"] = [sp["url"] for sp in elements]
        requires["contract_version"] = _boardrules.WORKSET_CONTRACT_VERSION
        env["requires"] = requires
    if references:
        refs = []
        for r in references:
            if isinstance(r, dict) and r.get("url"):
                refs.append({k: r[k] for k in ("url", "path", "base", "desc") if r.get(k)})
        if refs:
            env["references"] = refs
    return env


def write_board_post(board_repo: str, env: dict, workdir: "str | None" = None) -> str:
    """post.json を書く薄いラッパー（BoardRepo 経由・冪等・git+ にも対応）。書いたパス（新規/
    既存いずれも）を返す。手動 CLI（board-offload）向けの単発呼び出し用。daemon 内の自動配線
    （_act_board）は sync_pull/write_post/sync_push を個別に呼び、無駄な pull/push を避ける。"""
    repo = BoardRepo(board_repo, workdir=workdir)
    repo.sync_pull()
    if repo.write_post(env):
        repo.sync_push(f"post {env['id']}")
    return os.path.join(repo.delegation_dir(env["id"]), "post.json")


def cmd_board_offload(cfg: "Config", args) -> int:
    """`agent-project board-offload <task-id> [--board <repo>]`:
    ready なタスクをルーティングで workspace を確定し、委譲公示板へ手動で委譲する
    （daemon による自動配線は location: board / policy.offload を参照）。"""
    board_repo = getattr(args, "board", None) or cfg.board
    if not board_repo:
        print("エラー: --board <公示板リポジトリ> か設定 board: が必要です", file=sys.stderr)
        return 2
    tasks = load_tasks(cfg.backlog)
    task = next((t for t in tasks if t.id == args.id), None)
    if task is None:
        task = next((t for t in tasks if t.matches(args.id)), None)
    if task is None:
        print(f"エラー: タスクが見つかりません: {args.id}", file=sys.stderr)
        return 2
    try:
        specs, routed = resolve_workset(cfg, task, load_policy(cfg.policy))
    except (OSError, ValueError) as e:
        specs, routed = [], f"routing-error: {e}"
    blocked = workset_offload_blocked(specs)
    if blocked:
        print(f"エラー: {blocked}", file=sys.stderr)
        return 2
    spec = specs[0] if specs else None
    did = _board_delegation_id(task, cfg)
    workload = getattr(args, "board_workload", None) or cfg.board_workload or "flow"
    # board 委譲は請負側が別マシン（--context-file のようなローカル参照を渡せない）ため、
    # stable_prefix が有効でも charter/rules/repo_map は本文へ埋め込む。
    env = task_to_delegation(task, spec, workload=workload, delegation_id=did,
                             request=build_request(task, cfg, force_inline_context=True),
                             references=task_reference_specs(cfg, task), workset=specs)
    workdir = getattr(args, "board_workdir", None) or cfg.board_workdir
    path = write_board_post(board_repo, env, workdir=workdir)
    print(env["id"])
    print(f">>> タスク {task.id} を委譲公示板へ委譲しました: {env['id']}"
          f"（workspace={routed}）→ {path}", file=sys.stderr)
    return 0
