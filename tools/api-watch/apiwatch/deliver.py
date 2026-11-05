"""受け渡し（本体側）。どのワークフロー基盤に渡すかはアダプタ（apiwatch.adapters）に任せる。

受け渡しはイベント × 受け渡し先ごとに 1 行（deliveries）。受け渡しの ID をアダプタの run_id にそのまま使う。
アダプタは同じ run_id を 2 回起動しない（Dagu なら実行 ID の重複を断る）ので、渡し直しても後続処理は 1 回だけ走る。

順序（受け渡し先の order）:
    completion  同じルール・受け渡し先・レコードでは、前のイベントの後続処理が完了するまで次を渡さない（既定）
    enqueue     前のイベントを渡したら次を渡す（実行の順序は受け渡し先のキューに任せる）
    none        順序を問わない
別のレコードはこの制約を受けないので並列に進む。1 件が渡せなくても、同じレコードの後ろのイベントしか止まらない。

受け渡し先に command を書くと、アダプタで渡さず、`work` がその場でコマンドを実行する（1 つのワークフローの中で
取得から処理までを済ませる）。終了コード 0 で完了、それ以外は時間をおいて同じ ID でやり直す（max_attempts まで）。
dispatch はこの受け渡し先を飛ばす。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import jpath, template
from .adapters import DONE as A_DONE, FAILED as A_FAILED, SendError
from .config import Config, TargetSpec
from .store import DEAD, DISPATCHED, DONE, FAILED, PENDING, SEND_FAILED, SENDING, SKIPPED, Store
from .util import REDACT, ConfigError, atomic_write, canonical, log, now, sha, unpack


@dataclass
class DispatchOutcome:
    sent: int = 0
    duplicate: int = 0
    failed: int = 0
    dead: int = 0
    waiting: int = 0
    unknown: int = 0


@dataclass
class WorkOutcome:
    done: int = 0
    skipped: int = 0            # refresh で、処理し終えた状態と同じ・対象外・見つからないと分かって実行しなかった
    failed: int = 0
    dead: int = 0
    waiting: int = 0
    stopped: bool = False


class Dispatcher:
    def __init__(self, cfg: Config, store: Store, adapter=None):
        self.cfg = cfg
        self.store = store
        self._adapter = adapter

    @property
    def adapter(self):
        if self._adapter is None:
            if self.cfg.delivery is None:
                raise ConfigError("delivery.adapter が設定されていません")
            self._adapter = self.cfg.delivery.create(self.cfg.connections)
        return self._adapter

    def target(self, rule: str, target: str) -> TargetSpec | None:
        r = self.cfg.rules.get(rule)
        if r is None:
            return None
        for t in r.targets:
            if t.name == target:
                return t
        return None

    def _blocked(self, d: Any, order: str) -> bool:
        if order == "none":
            return False
        if order == "completion":
            sql = "status NOT IN ('done','skipped')"
        else:
            sql = "status IN ('pending','sending','send_failed','dead')"
        row = self.store.one(f"SELECT 1 FROM deliveries WHERE rule=? AND target=? AND rkey=? AND seq<? AND {sql} "
                             "LIMIT 1", (d["rule"], d["target"], d["rkey"], d["seq"]))
        return row is not None

    def event_payload(self, event_id: str) -> dict:
        row = self.store.one("SELECT payload FROM events WHERE id=?", (event_id,))
        if row is None:
            raise KeyError(event_id)
        return unpack(row["payload"])

    def write_event_file(self, payload: dict) -> Path:
        path = self.store.event_file(payload["event_id"])
        atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return path

    def params_for(self, d: Any, t: TargetSpec, payload: dict, path: Path) -> dict[str, str]:
        params = {
            "APIWATCH_EVENT_ID": payload["event_id"],
            "APIWATCH_DELIVERY_ID": d["id"],
            "APIWATCH_SOURCE": payload["source"],
            "APIWATCH_RULE": payload["rule"],
            "APIWATCH_RECORD_ID": str(payload["record_key"]),
            "APIWATCH_KINDS": ",".join(payload["kinds"]),
            "APIWATCH_EVENT_FILE": str(path),
        }
        if t.inline_payload:
            params["APIWATCH_EVENT_JSON"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ctx = {"event": payload, "delivery": {"id": d["id"], "target": t.name, "workflow": t.workflow},
               "env": dict(os.environ)}
        for k, v in template.render(t.params, ctx).items():
            params[k] = "" if v is None else (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list))
                                              else str(v).lower() if isinstance(v, bool) else str(v))
        return params

    def dispatch(self, rules: list[str] | None = None, limit: int | None = None) -> DispatchOutcome:
        out = DispatchOutcome()
        t = now()
        rows = self.store.q(
            "SELECT * FROM deliveries WHERE ((status IN ('pending','send_failed') AND next_attempt_at<=?) "
            "OR (status='sending' AND lease_until<?)) ORDER BY seq, id", (t, t))
        for d in rows:
            if rules is not None and d["rule"] not in rules:
                continue
            if limit is not None and out.sent + out.duplicate + out.failed + out.dead >= limit:
                break
            tgt = self.target(d["rule"], d["target"])
            if tgt is None:
                out.unknown += 1
                continue
            if tgt.runs_here:
                continue          # work がその場で実行する
            if self._blocked(d, tgt.order):
                out.waiting += 1
                continue
            self._send_one(d, tgt, out)
        return out

    def _send_one(self, d: Any, tgt: TargetSpec, out: DispatchOutcome) -> None:
        t = now()
        timeout = self.cfg.delivery.timeout_sec if self.cfg.delivery else 60
        lease_until = t + timeout * 2 + 30
        with self.store.tx() as db:
            cur = db.execute("UPDATE deliveries SET status=?, lease_until=?, owner=?, updated_at=? WHERE id=? AND "
                             "((status IN ('pending','send_failed') AND next_attempt_at<=?) OR "
                             "(status='sending' AND lease_until<?))",
                             (SENDING, lease_until, self.store.owner, t, d["id"], t, t))
            if cur.rowcount != 1:
                return   # 別のプロセスが先に取った
        try:
            payload = self.event_payload(d["event_id"])
            path = self.write_event_file(payload)
            params = self.params_for(d, tgt, payload, path)
            result = self.adapter.enqueue(d["id"], tgt.workflow, params, tgt.queue)
        except Exception as e:  # noqa: BLE001  1 件の失敗で他の受け渡しを止めない
            msg = REDACT(f"{e}")
            attempts = int(d["attempts"]) + 1
            if attempts >= tgt.max_attempts:
                status, nxt = DEAD, 0.0
                out.dead += 1
            else:
                status = SEND_FAILED
                nxt = now() + min(tgt.retry_max_backoff_sec, tgt.retry_backoff_sec * (2 ** (attempts - 1)))
                out.failed += 1
            with self.store.tx() as db:
                db.execute("UPDATE deliveries SET status=?, attempts=?, next_attempt_at=?, last_error=?, lease_until=NULL, "
                           "updated_at=? WHERE id=? AND status=? AND owner=?",
                           (status, attempts, nxt, msg, now(), d["id"], SENDING, self.store.owner))
            log(f"[{d['rule']} → {tgt.name}] {d['id']}（レコード {d['rkey']}）を渡せませんでした"
                f"（{attempts}/{tgt.max_attempts} 回目）: {msg}")
            return
        with self.store.tx() as db:
            # 後続処理が先に ack していたら（done / failed）、それを上書きしない
            db.execute("UPDATE deliveries SET status=?, attempts=attempts+1, dispatched_at=?, last_error=NULL, "
                       "lease_until=NULL, updated_at=? WHERE id=? AND status=? AND owner=?",
                       (DISPATCHED, now(), now(), d["id"], SENDING, self.store.owner))
        if result == "duplicate":
            out.duplicate += 1
            log(f"[{d['rule']} → {tgt.name}] {d['id']} は受け渡し先に既にあります（渡し済みとして扱います）")
        else:
            out.sent += 1
            log(f"[{d['rule']} → {tgt.name}] {d['id']}（レコード {d['rkey']}・{payload['kinds']}）を {tgt.workflow} に渡しました")

    # -- その場で実行する受け渡し先（command）------------------------------------

    def work(self, rules: list[str] | None = None, targets: list[str] | None = None,
             limit: int | None = None) -> WorkOutcome:
        """command の受け渡し先の、実行を待っている受け渡しを古い順に 1 件ずつ実行する。"""
        out = WorkOutcome()
        tried: set[str] = set()   # 1 回の work では、同じ受け渡しを 2 回試さない（失敗のやり直しは次の work で）
        stop: list[int] = []
        child: list[subprocess.Popen] = []

        def on_stop(signum, _frame):
            # Dagu の中止・タイムアウト（SIGTERM）。実行中のコマンドにも伝え、失敗として記録してから抜ける
            stop.append(signum)
            for c in child:
                try:
                    c.send_signal(signum)
                except (OSError, ValueError):
                    pass
        old = {}
        for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    old[sig] = signal.signal(sig, on_stop)
                except (OSError, ValueError):
                    pass
        try:
            while not stop:
                if limit is not None and out.done + out.skipped + out.failed + out.dead >= limit:
                    break
                t = now()
                rows = self.store.q(
                    "SELECT * FROM deliveries WHERE ((status IN ('pending','send_failed') AND next_attempt_at<=?) "
                    "OR (status='sending' AND lease_until<?)) ORDER BY seq, id", (t, t))
                picked = None
                waiting = 0
                for d in rows:
                    if d["id"] in tried:
                        continue
                    if (rules is not None and d["rule"] not in rules) or (targets is not None and d["target"] not in targets):
                        continue
                    tgt = self.target(d["rule"], d["target"])
                    if tgt is None or not tgt.runs_here:
                        continue
                    if self._blocked(d, tgt.order):
                        waiting += 1
                        continue
                    picked = (d, tgt)
                    break
                out.waiting = max(out.waiting, waiting)
                if picked is None:
                    break
                tried.add(picked[0]["id"])
                self._work_one(picked[0], picked[1], out, stop, child)
        finally:
            for sig, h in old.items():
                try:
                    signal.signal(sig, h)
                except (OSError, ValueError):
                    pass
        out.stopped = bool(stop)
        return out

    def _finish(self, d: Any, attempts: int, note: str | None = None) -> None:
        with self.store.tx() as db:
            db.execute("UPDATE deliveries SET status=?, attempts=?, dispatched_at=?, completed_at=?, last_error=?, "
                       "lease_until=NULL, updated_at=? WHERE id=? AND owner=?",
                       (DONE, attempts, now(), now(), note, now(), d["id"], self.store.owner))

    def _fail(self, d: Any, tgt: TargetSpec, attempts: int, error: str, out: WorkOutcome) -> None:
        if attempts >= tgt.max_attempts:
            status, nxt = DEAD, 0.0
            out.dead += 1
        else:
            status = SEND_FAILED
            nxt = now() + min(tgt.retry_max_backoff_sec, tgt.retry_backoff_sec * (2 ** (attempts - 1)))
            out.failed += 1
        with self.store.tx() as db:
            db.execute("UPDATE deliveries SET status=?, attempts=?, next_attempt_at=?, last_error=?, lease_until=NULL, "
                       "updated_at=? WHERE id=? AND owner=?",
                       (status, attempts, nxt, REDACT(error), now(), d["id"], self.store.owner))
        log(f"[{d['rule']} → {tgt.name}] {d['id']} 失敗（{error}・{attempts}/{tgt.max_attempts} 回目）→ "
            + ("打ち切り（retry で再開）" if status == DEAD else "あとでやり直す"))

    def _watched_of(self, rule, rec: Any) -> tuple[Any, str]:
        """レコードの監視する値（イベントの after と同じ形）とそのハッシュ。"""
        from .detect import normalize, present_value, watched
        w = watched(rule, normalize(rule, rec))
        return present_value(rule, w), sha(canonical(w), 40)

    def _hash_of_after(self, rule, after: Any) -> str:
        from .detect import WHOLE
        w = {WHOLE: after} if rule.watch is None else (after or {})
        return sha(canonical(w), 40)

    def _refresh(self, d: Any, tgt: TargetSpec, payload: dict) -> Any:
        from .fetch import Fetcher
        src = self.cfg.sources[self.cfg.rules[d["rule"]].source]
        return Fetcher(self.cfg, self.store).refresh_one(src, tgt.refresh, payload)

    def _work_one(self, d: Any, tgt: TargetSpec, out: WorkOutcome, stop: list, child: list) -> None:
        from .fetch import Deferred
        from .util import PermanentError, TransientError
        t = now()
        with self.store.tx() as db:
            cur = db.execute("UPDATE deliveries SET status=?, lease_until=?, owner=?, updated_at=? WHERE id=? AND "
                             "((status IN ('pending','send_failed') AND next_attempt_at<=?) OR "
                             "(status='sending' AND lease_until<?))",
                             (SENDING, t + tgt.timeout_sec + 120, self.store.owner, t, d["id"], t, t))
            if cur.rowcount != 1:
                return   # 別のプロセスが先に取った
        payload = self.event_payload(d["event_id"])
        attempts = int(d["attempts"]) + 1
        rule = self.cfg.rules[d["rule"]]
        label = f"[{d['rule']} → {tgt.name}] {d['id']}"
        before_hash = None
        if tgt.refresh is not None:
            # 実行の前に、最後に処理し終えた状態と比べる。同じなら処理することが無い
            # （自分の書き込みで出たイベント・まとめて処理し終えた古いイベント）
            done = self.store.handled(d["rule"], tgt.name, d["rkey"])
            if done is not None and self._hash_of_after(rule, payload.get("after")) == done["vhash"]:
                self._finish(d, int(d["attempts"]), "処理し終えた状態と同じなので実行しなかった")
                out.skipped += 1
                log(f"{label} は処理し終えた状態と同じなので飛ばします")
                return
            try:
                fresh = self._refresh(d, tgt, payload)
            except Deferred as e:
                with self.store.tx() as db:      # 試行に数えず、待ちが明けてから
                    db.execute("UPDATE deliveries SET status=?, lease_until=NULL, next_attempt_at=?, updated_at=? "
                               "WHERE id=? AND owner=?", (d["status"] if d["status"] != SENDING else PENDING,
                                                          now() + 60, now(), d["id"], self.store.owner))
                out.waiting += 1
                log(f"{label} は取り直せないのであとで: {e}")
                return
            except (TransientError, PermanentError) as e:
                self._fail(d, tgt, attempts, f"実行の前の取り直しに失敗: {REDACT(str(e))}", out)
                return
            if fresh is None:
                self._finish(d, attempts, "取り直したらレコードが見つからなかった")
                out.skipped += 1
                log(f"{label} はレコードが見つからないので飛ばします")
                return
            if rule.filter is not None and not jpath.check(rule.filter, fresh):
                self._finish(d, attempts, "取り直したら filter から外れていた")
                out.skipped += 1
                log(f"{label} は filter から外れたので飛ばします")
                return
            after_value, before_hash = self._watched_of(rule, fresh)
            if done is not None and before_hash == done["vhash"]:
                self._finish(d, attempts, "処理し終えた状態と同じなので実行しなかった")
                out.skipped += 1
                log(f"{label} は処理し終えた状態と同じなので飛ばします")
                return
            payload = dict(payload)
            payload["record"] = fresh
            payload["refreshed"] = {"after": after_value,
                                    "last_handled": unpack(done["value"]) if done is not None else None}
            path = self.store.event_file(payload["event_id"], tgt.name)
            atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            path = self.write_event_file(payload)
        params = self.params_for(d, tgt, payload, path)
        ctx = {"event": payload, "delivery": {"id": d["id"], "target": tgt.name, "workflow": ""},
               "env": dict(os.environ)}
        argv = [str(x) for x in template.render(tgt.command, ctx)]
        result_file = self.store.dir / "events" / f"{d['id']}.result.json"
        result_file.unlink(missing_ok=True)
        env = dict(os.environ)
        env.update(params)
        env["APIWATCH_ATTEMPT"] = str(attempts)
        env["APIWATCH_RESULT_FILE"] = str(result_file)
        log(f"{label}（レコード {d['rkey']}・{payload['kinds']}・{attempts} 回目）を実行します")
        error = None
        try:
            proc = subprocess.Popen(argv, env=env)
        except OSError as e:
            error = f"コマンドを起動できません: {e}"
        else:
            child.append(proc)
            try:
                rc = proc.wait(timeout=tgt.timeout_sec)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                rc = None
            finally:
                child.clear()
            if stop:
                error = "中止された"
            elif rc is None:
                error = f"{tgt.timeout_sec:.0f} 秒で打ち切った"
            elif rc != 0:
                error = f"終了コード {rc}"
        result: dict = {}
        try:
            loaded = json.loads(result_file.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            pass
        result_file.unlink(missing_ok=True)
        if error is not None:
            self._fail(d, tgt, attempts, error, out)
            return
        if tgt.refresh is not None:
            # 実行のあとで取り直し、その状態を処理し終えた状態にする（自分の書き込みでもう一度起きない）。
            # コマンドが {"handled": "before"} を書いたら（実行中にほかの人が動いたなど）、実行前の状態までにとどめる
            value, vhash = after_value, before_hash
            if result.get("handled") != "before":
                try:
                    later = self._refresh(d, tgt, payload)
                except (Deferred, TransientError, PermanentError) as e:
                    log(f"{label} 実行のあとの取り直しに失敗したので、実行前の状態までを処理済みにします: {REDACT(str(e))}")
                    later = None
                if later is not None:
                    value, vhash = self._watched_of(rule, later)
            self.store.set_handled(d["rule"], tgt.name, d["rkey"], vhash, value)
        self._finish(d, attempts)
        out.done += 1
        log(f"{label} 完了")

    # -- 完了の記録・再実行 -----------------------------------------------------

    def ack(self, delivery: str, ok: bool, message: str | None = None) -> str:
        with self.store.tx() as db:
            row = db.execute("SELECT status FROM deliveries WHERE id=?", (delivery,)).fetchone()
            if row is None:
                raise KeyError(delivery)
            if row["status"] == SKIPPED:
                return SKIPPED
            status = DONE if ok else FAILED
            db.execute("UPDATE deliveries SET status=?, completed_at=?, last_error=?, updated_at=? WHERE id=?",
                       (status, now(), None if ok else (REDACT(message) if message else "後続処理が失敗しました"),
                        now(), delivery))
            return status

    def reconcile(self) -> dict[str, int]:
        """後続処理の状態をアダプタに問い合わせて記録する（問い合わせられないアダプタでは何もしない）。"""
        counts = {"done": 0, "failed": 0, "unchanged": 0}
        for d in self.store.q("SELECT id, workflow, status FROM deliveries WHERE status IN (?, ?)", (DISPATCHED, FAILED)):
            try:
                st = self.adapter.status(d["id"], d["workflow"])
            except SendError as e:
                log(f"{d['id']}: {e}")
                counts["unchanged"] += 1
                continue
            if st == A_DONE:
                self.ack(d["id"], True)
                counts["done"] += 1
            elif st == A_FAILED and d["status"] != FAILED:
                self.ack(d["id"], False, "受け渡し先の実行が失敗で終わりました")
                counts["failed"] += 1
            else:
                counts["unchanged"] += 1
        return counts

    def retry(self, ids: list[str]) -> list[str]:
        msgs = []
        for i in ids:
            d = self.store.one("SELECT * FROM deliveries WHERE id=?", (i,))
            if d is None:
                msgs.append(f"{i}: 見つかりません")
                continue
            if d["status"] in (PENDING, SEND_FAILED, DEAD):
                with self.store.tx() as db:
                    db.execute("UPDATE deliveries SET status=?, attempts=0, next_attempt_at=0, updated_at=? WHERE id=?",
                               (PENDING, now(), i))
                msgs.append(f"{i}: 次の dispatch で同じ ID のまま渡し直します")
            elif d["status"] == FAILED:
                try:
                    self.adapter.retry(i, d["workflow"])
                except SendError as e:
                    msgs.append(f"{i}: {e}")
                    continue
                with self.store.tx() as db:
                    db.execute("UPDATE deliveries SET status=?, last_error=NULL, completed_at=NULL, updated_at=? "
                               "WHERE id=?", (DISPATCHED, now(), i))
                msgs.append(f"{i}: 受け渡し先で同じ ID のまま再実行しました")
            else:
                msgs.append(f"{i}: 状態が {d['status']} なので何もしません")
        return msgs

    def skip(self, ids: list[str]) -> list[str]:
        msgs = []
        for i in ids:
            with self.store.tx() as db:
                cur = db.execute("UPDATE deliveries SET status=?, updated_at=? WHERE id=? AND status<>?",
                                 (SKIPPED, now(), i, DONE))
            msgs.append(f"{i}: " + ("飛ばしました（同じレコードの後ろのイベントが進みます）" if cur.rowcount else
                                    "見つからないか、完了済みです"))
        return msgs
