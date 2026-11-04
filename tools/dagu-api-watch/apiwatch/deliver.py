"""Dagu への受け渡し。

受け渡しはイベント × 受け渡し先ごとに 1 行（deliveries）。Dagu の実行 ID に受け渡しの ID をそのまま使うので、
渡し直しても Dagu 側で同じ実行が 2 つできることはない（「既にある」と言われたら渡せたものとして扱う）。

順序（受け渡し先の order）:
    completion  同じルール・受け渡し先・レコードでは、前のイベントの後続処理が完了するまで次を渡さない（既定）
    enqueue     前のイベントを Dagu のキューに入れたら次を渡す（実行の順序は Dagu のキューに任せる）
    none        順序を問わない
別のレコードはこの制約を受けないので、Dagu のキューの並列数まで並んで動く。
1 件が渡せなくても、それより前に並ぶ同じレコードのイベントしか止まらない。
"""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import template
from .config import Config, DaguSpec, TargetSpec
from .store import (DEAD, DISPATCHED, DONE, FAILED, PENDING, SEND_FAILED, SENDING, SKIPPED, Store)
from .util import REDACT, atomic_write, log, now, unpack


class SendError(Exception):
    pass


def _decode(b: bytes | None) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def dag_name(dag: str) -> str:
    """API 用の DAG 名（パスや拡張子が付いていれば外す）。"""
    name = Path(dag).name
    for ext in (".yaml", ".yml"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


# ---------------------------------------------------------------------------
# Dagu との口
# ---------------------------------------------------------------------------


class CliBackend:
    """`dagu enqueue --run-id=<ID> <DAG> -- KEY=VALUE ...`"""

    def __init__(self, spec: DaguSpec):
        self.spec = spec

    def _run(self, argv: list[str]) -> tuple[int, str]:
        try:
            p = subprocess.run(argv, capture_output=True, timeout=self.spec.timeout_sec)
        except FileNotFoundError as e:
            raise SendError(f"dagu を起動できません: {argv[0]}（dagu.executable で指定できます）") from e
        except subprocess.TimeoutExpired as e:
            raise SendError(f"dagu が {self.spec.timeout_sec:.0f} 秒で終わりませんでした") from e
        return p.returncode, (_decode(p.stdout) + "\n" + _decode(p.stderr)).strip()

    def enqueue(self, run_id: str, dag: str, params: dict[str, str], queue: str | None) -> str:
        argv = list(self.spec.executable) + ["enqueue"] + list(self.spec.extra_args) + [f"--run-id={run_id}"]
        if queue:
            argv.append(f"--queue={queue}")
        argv += [dag, "--"] + [f"{k}={v}" for k, v in params.items()]
        rc, out = self._run(argv)
        if rc == 0:
            return "ok"
        if "already exists" in out:
            return "duplicate"
        raise SendError(f"dagu enqueue が失敗しました（終了コード {rc}）: {REDACT(out[-500:])}")

    def retry(self, run_id: str, dag: str) -> None:
        argv = list(self.spec.executable) + ["retry"] + list(self.spec.extra_args) + [f"--run-id={run_id}", dag]
        rc, out = self._run(argv)
        if rc != 0:
            raise SendError(f"dagu retry が失敗しました（終了コード {rc}）: {REDACT(out[-500:])}")

    def status(self, run_id: str, dag: str) -> str | None:
        return None   # CLI の出力は人向けなので読まない。完了は後続の DAG から ack で知らせる


class ApiBackend:
    """Dagu の REST API（/api/v1）。"""

    def __init__(self, spec: DaguSpec, conn: dict | None):
        self.spec = spec
        base = (spec.url or (conn or {}).get("url") or "").rstrip("/")
        if not base:
            raise SendError("dagu.url（または接続設定の url）がありません")
        self.base = base if base.endswith("/api/v1") else base + "/api/v1"
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        conn = conn or {}
        token = conn.get("token") or conn.get("api_token")
        if token:
            REDACT.add(token)
            self.headers["Authorization"] = f"Bearer {token}"
        elif conn.get("user") and conn.get("password"):
            REDACT.add(conn["password"])
            raw = base64.b64encode(f"{conn['user']}:{conn['password']}".encode("utf-8")).decode("ascii")
            REDACT.add(raw)
            self.headers["Authorization"] = f"Basic {raw}"

    def _call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.spec.timeout_sec) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read() if e.fp else b""
            status = e.code
        except (urllib.error.URLError, OSError) as e:
            raise SendError(f"Dagu に接続できません: {getattr(e, 'reason', e)}") from e
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, _decode(raw)

    def enqueue(self, run_id: str, dag: str, params: dict[str, str], queue: str | None) -> str:
        body: dict[str, Any] = {"dagRunId": run_id, "params": json.dumps(params, ensure_ascii=False)}
        if queue:
            body["queue"] = queue
        status, resp = self._call("POST", f"/dags/{urllib.parse.quote(dag_name(dag), safe='')}/enqueue", body)
        if 200 <= status < 300:
            return "ok"
        text = json.dumps(resp, ensure_ascii=False) if not isinstance(resp, str) else resp
        if status == 409 and "already exists" in text:
            return "duplicate"
        raise SendError(f"Dagu API の enqueue が失敗しました（HTTP {status}）: {REDACT(text[:500])}")

    def retry(self, run_id: str, dag: str) -> None:
        status, resp = self._call("POST", f"/dag-runs/{urllib.parse.quote(dag_name(dag), safe='')}/"
                                          f"{urllib.parse.quote(run_id, safe='')}/retry", {"dagRunId": run_id})
        if not 200 <= status < 300:
            raise SendError(f"Dagu API の retry が失敗しました（HTTP {status}）: {REDACT(str(resp)[:500])}")

    def status(self, run_id: str, dag: str) -> str | None:
        status, resp = self._call("GET", f"/dag-runs/{urllib.parse.quote(dag_name(dag), safe='')}/"
                                         f"{urllib.parse.quote(run_id, safe='')}")
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise SendError(f"Dagu API で状態を取れません（HTTP {status}）")
        details = (resp or {}).get("dagRunDetails") if isinstance(resp, dict) else None
        if isinstance(details, dict) and isinstance(details.get("statusLabel"), str):
            return details["statusLabel"]
        return None


class CommandBackend:
    """任意のコマンドで渡す（試験や独自の受け口用）。0 で成功、9 か出力に already exists で「既にある」。"""

    def __init__(self, spec: DaguSpec):
        self.spec = spec

    def enqueue(self, run_id: str, dag: str, params: dict[str, str], queue: str | None) -> str:
        ctx = {"run_id": run_id, "dag": dag, "queue": queue or "", "params_json": json.dumps(params, ensure_ascii=False),
               "params": params}
        argv = [str(x) for x in template.render(self.spec.command, ctx)]
        try:
            p = subprocess.run(argv, capture_output=True, timeout=self.spec.timeout_sec)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise SendError(f"受け渡しのコマンドを実行できません: {e}") from e
        out = (_decode(p.stdout) + "\n" + _decode(p.stderr)).strip()
        if p.returncode == 0:
            return "ok"
        if p.returncode == 9 or "already exists" in out:
            return "duplicate"
        raise SendError(f"受け渡しのコマンドが失敗しました（終了コード {p.returncode}）: {REDACT(out[-500:])}")

    def retry(self, run_id: str, dag: str) -> None:
        raise SendError("mode: command では Dagu での再実行を代行できません。Dagu の画面か dagu retry で再実行してください")

    def status(self, run_id: str, dag: str) -> str | None:
        return None


def backend_for(cfg: Config):
    spec = cfg.dagu
    if spec.mode == "api":
        return ApiBackend(spec, cfg.resolve_connection(spec.connection))
    if spec.mode == "command":
        return CommandBackend(spec)
    return CliBackend(spec)


# ---------------------------------------------------------------------------
# 受け渡し
# ---------------------------------------------------------------------------

DONE_LABELS = {"succeeded", "partially_succeeded"}
FAILED_LABELS = {"failed", "aborted", "rejected"}


@dataclass
class DispatchOutcome:
    sent: int = 0
    duplicate: int = 0
    failed: int = 0
    dead: int = 0
    waiting: int = 0
    unknown: int = 0


class Dispatcher:
    def __init__(self, cfg: Config, store: Store, backend=None):
        self.cfg = cfg
        self.store = store
        self._backend = backend

    @property
    def backend(self):
        if self._backend is None:
            self._backend = backend_for(self.cfg)
        return self._backend

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
        import os
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
        ctx = {"event": payload, "delivery": {"id": d["id"], "target": t.name, "dag": t.dag}, "env": dict(os.environ)}
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
        lease_until = t + self.cfg.dagu.timeout_sec * 2 + 30
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
            result = self.backend.enqueue(d["id"], tgt.dag, params, tgt.queue)
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
            log(f"[{d['rule']} → {tgt.name}] {d['id']} は Dagu に既にあります（渡し済みとして扱います）")
        else:
            out.sent += 1
            log(f"[{d['rule']} → {tgt.name}] {d['id']}（レコード {d['rkey']}・{payload['kinds']}）を {tgt.dag} に渡しました")

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
        counts = {"done": 0, "failed": 0, "unchanged": 0, "unsupported": 0}
        for d in self.store.q("SELECT id, dag, status FROM deliveries WHERE status IN (?, ?)", (DISPATCHED, FAILED)):
            try:
                label = self.backend.status(d["id"], d["dag"])
            except SendError as e:
                log(f"{d['id']}: {e}")
                counts["unchanged"] += 1
                continue
            if label is None:
                counts["unsupported" if isinstance(self.backend, (CliBackend, CommandBackend)) else "unchanged"] += 1
                continue
            if label in DONE_LABELS:
                self.ack(d["id"], True)
                counts["done"] += 1
            elif label in FAILED_LABELS and d["status"] != FAILED:
                self.ack(d["id"], False, f"Dagu の実行が {label} で終わりました")
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
                    self.backend.retry(i, d["dag"])
                except SendError as e:
                    msgs.append(f"{i}: {e}")
                    continue
                with self.store.tx() as db:
                    db.execute("UPDATE deliveries SET status=?, last_error=NULL, completed_at=NULL, updated_at=? "
                               "WHERE id=?", (DISPATCHED, now(), i))
                msgs.append(f"{i}: Dagu で同じ実行 ID のまま再実行しました")
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
