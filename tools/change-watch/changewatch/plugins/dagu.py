"""change-watch の Dagu プラグイン（changewatch/plugins/dagu.py）。Dagu に依存するものはこのファイルだけにある。

    python change_watch.py --plugin dagu ...     （.pyz なら python change-watch.pyz --plugin dagu ...）

足すもの:
    アダプタ dagu   受け渡し先を Dagu の DAG にする（delivery.adapter: dagu）

change-watch を動かす Dagu のワークフローは、dags/ の例（*.example.yaml）をコピーして書く。

アダプタの設定:

    delivery:
      adapter: dagu
      mode: cli              # cli: dagu enqueue / api: REST API（/api/v1）
      executable: dagu       # mode: cli。argv の配列でもよい
      extra_args: []         # mode: cli。enqueue / retry の後ろに足す引数
      url: http://localhost:8080   # mode: api。または connection
      connection: dagu       # mode: api。接続設定の url と token（または user と password）
      timeout_sec: 60

受け渡しの ID を Dagu の実行 ID（--run-id / dagRunId）に使う。Dagu は同じ DAG・同じ実行 ID の enqueue を
断る（CLI は "already exists"、API は 409）ので、渡し直しても後続処理は 1 回しか走らない。
確かめたのは Dagu v2.25.2 のソース（internal/cmd/enqueue.go・api/v1/api.yaml・internal/ir/run_id.go）。
"""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from changewatch.adapters import DONE, FAILED, SendError
from changewatch.adapters.command import decode
from changewatch.connections import parse_ref
from changewatch.util import REDACT, ConfigError


# ---------------------------------------------------------------------------
# アダプタ
# ---------------------------------------------------------------------------

# Dagu の statusLabel → 後続処理の状態
DONE_LABELS = {"succeeded", "partially_succeeded"}
FAILED_LABELS = {"failed", "aborted", "rejected"}


@dataclass
class Spec:
    mode: str = "cli"
    executable: list[str] = field(default_factory=lambda: ["dagu"])
    extra_args: list[str] = field(default_factory=list)
    url: str | None = None
    connection: tuple[str, str] | None = None
    timeout_sec: float = 60


def parse(options: dict, where: str) -> Spec:
    unknown = sorted(set(options) - {"mode", "executable", "extra_args", "url", "connection", "timeout_sec"})
    if unknown:
        raise ConfigError(f"{where}: 知らない項目です: {', '.join(unknown)}")
    mode = options.get("mode", "cli")
    if mode not in ("cli", "api"):
        raise ConfigError(f"{where}.mode: cli か api です")
    exe = options.get("executable", "dagu")
    exe_l = [exe] if isinstance(exe, str) else exe
    if not (isinstance(exe_l, list) and exe_l and all(isinstance(x, str) for x in exe_l)):
        raise ConfigError(f"{where}.executable: 実行ファイル（または argv の配列）を書いてください")
    t = options.get("timeout_sec", 60)
    if not isinstance(t, (int, float)) or isinstance(t, bool) or t < 1:
        raise ConfigError(f"{where}.timeout_sec: 1 以上の数を書いてください")
    spec = Spec(mode=mode, executable=exe_l, extra_args=[str(x) for x in options.get("extra_args") or []],
                url=options.get("url"), connection=parse_ref(options.get("connection"), f"{where}.connection"),
                timeout_sec=float(t))
    if mode == "api" and spec.url is None and spec.connection is None:
        raise ConfigError(f"{where}: mode: api には url か connection（url と token、または user と password）が要ります")
    return spec


def dag_name(dag: str) -> str:
    """API 用の DAG 名（パスや拡張子が付いていれば外す）。"""
    name = Path(dag).name
    for ext in (".yaml", ".yml"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


class CliAdapter:
    """`dagu enqueue --run-id=<ID> [--queue=Q] <DAG> -- KEY=VALUE ...`（argv で渡し、シェルを通さない）"""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.timeout_sec = spec.timeout_sec

    def _run(self, argv: list[str]) -> tuple[int, str]:
        try:
            p = subprocess.run(argv, capture_output=True, timeout=self.spec.timeout_sec)
        except FileNotFoundError as e:
            raise SendError(f"dagu を起動できません: {argv[0]}（delivery.executable で指定できます）") from e
        except subprocess.TimeoutExpired as e:
            raise SendError(f"dagu が {self.spec.timeout_sec:.0f} 秒で終わりませんでした") from e
        return p.returncode, (decode(p.stdout) + "\n" + decode(p.stderr)).strip()

    def enqueue(self, run_id: str, workflow: str, params: dict[str, str], queue: str | None) -> str:
        argv = list(self.spec.executable) + ["enqueue"] + list(self.spec.extra_args) + [f"--run-id={run_id}"]
        if queue:
            argv.append(f"--queue={queue}")
        argv += [workflow, "--"] + [f"{k}={v}" for k, v in params.items()]
        rc, out = self._run(argv)
        if rc == 0:
            return "ok"
        if "already exists" in out:
            return "duplicate"
        raise SendError(f"dagu enqueue が失敗しました（終了コード {rc}）: {REDACT(out[-500:])}")

    def retry(self, run_id: str, workflow: str) -> None:
        argv = list(self.spec.executable) + ["retry"] + list(self.spec.extra_args) + [f"--run-id={run_id}", workflow]
        rc, out = self._run(argv)
        if rc != 0:
            raise SendError(f"dagu retry が失敗しました（終了コード {rc}）: {REDACT(out[-500:])}")

    def status(self, run_id: str, workflow: str) -> str | None:
        return None   # CLI の出力は人向けなので読まない。完了は後続の DAG から ack で知らせる


class ApiAdapter:
    """Dagu の REST API（/api/v1）。"""

    def __init__(self, spec: Spec, conn: dict | None):
        self.spec = spec
        self.timeout_sec = spec.timeout_sec
        base = (spec.url or (conn or {}).get("url") or "").rstrip("/")
        if not base:
            raise ConfigError("delivery.url（または接続設定の url）がありません")
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
            return status, decode(raw)

    @staticmethod
    def _q(s: str) -> str:
        return urllib.parse.quote(s, safe="")

    def enqueue(self, run_id: str, workflow: str, params: dict[str, str], queue: str | None) -> str:
        body: dict[str, Any] = {"dagRunId": run_id, "params": json.dumps(params, ensure_ascii=False)}
        if queue:
            body["queue"] = queue
        status, resp = self._call("POST", f"/dags/{self._q(dag_name(workflow))}/enqueue", body)
        if 200 <= status < 300:
            return "ok"
        text = json.dumps(resp, ensure_ascii=False) if not isinstance(resp, str) else resp
        if status == 409 and "already exists" in text:
            return "duplicate"
        raise SendError(f"Dagu API の enqueue が失敗しました（HTTP {status}）: {REDACT(text[:500])}")

    def retry(self, run_id: str, workflow: str) -> None:
        status, resp = self._call("POST", f"/dag-runs/{self._q(dag_name(workflow))}/{self._q(run_id)}/retry",
                                  {"dagRunId": run_id})
        if not 200 <= status < 300:
            raise SendError(f"Dagu API の retry が失敗しました（HTTP {status}）: {REDACT(str(resp)[:500])}")

    def status(self, run_id: str, workflow: str) -> str | None:
        status, resp = self._call("GET", f"/dag-runs/{self._q(dag_name(workflow))}/{self._q(run_id)}")
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise SendError(f"Dagu API で状態を取れません（HTTP {status}）")
        details = (resp or {}).get("dagRunDetails") if isinstance(resp, dict) else None
        label = details.get("statusLabel") if isinstance(details, dict) else None
        if label in DONE_LABELS:
            return DONE
        if label in FAILED_LABELS:
            return FAILED
        return None


def create(spec: Spec, connections):
    if spec.mode == "api":
        conn = connections.get(*spec.connection) if spec.connection else None
        return ApiAdapter(spec, conn)
    return CliAdapter(spec)


def register(api) -> None:
    """--plugin dagu で読み込まれたときに、アダプタ dagu を足す。"""
    api.add_adapter("dagu", parse, create)
