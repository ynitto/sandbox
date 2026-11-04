"""受け渡し（本体側）。どのワークフロー基盤に渡すかはアダプタ（apiwatch.adapters）に任せる。

受け渡しはイベント × 受け渡し先ごとに 1 行（deliveries）。受け渡しの ID をアダプタの run_id にそのまま使う。
アダプタは同じ run_id を 2 回起動しない（Dagu なら実行 ID の重複を断る）ので、渡し直しても後続処理は 1 回だけ走る。

順序（受け渡し先の order）:
    completion  同じルール・受け渡し先・レコードでは、前のイベントの後続処理が完了するまで次を渡さない（既定）
    enqueue     前のイベントを渡したら次を渡す（実行の順序は受け渡し先のキューに任せる）
    none        順序を問わない
別のレコードはこの制約を受けないので並列に進む。1 件が渡せなくても、同じレコードの後ろのイベントしか止まらない。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import template
from .adapters import DONE as A_DONE, FAILED as A_FAILED, SendError
from .config import Config, TargetSpec
from .store import DEAD, DISPATCHED, DONE, FAILED, PENDING, SEND_FAILED, SENDING, SKIPPED, Store
from .util import REDACT, ConfigError, atomic_write, log, now, unpack


@dataclass
class DispatchOutcome:
    sent: int = 0
    duplicate: int = 0
    failed: int = 0
    dead: int = 0
    waiting: int = 0
    unknown: int = 0


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
            d = self.cfg.delivery
            self._adapter = d.module.create(d.spec, self.cfg.connections)
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
