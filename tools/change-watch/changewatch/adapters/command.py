"""任意のコマンドで受け渡すアダプタ（Dagu を使わないとき・独自の受け口・試験用）。

    delivery:
      adapter: command
      command: [python3, /path/to/handoff.py, "{{ workflow }}", "{{ run_id }}"]
      retry_command: [...]       # 省略可。無ければ retry はできない
      status_command: [...]      # 省略可。標準出力の 1 行目が done / failed なら後続処理の状態として記録する
      timeout_sec: 60

argv には {{ run_id }}・{{ workflow }}・{{ queue }}・{{ params_json }}・{{ params.NAME }} を埋められる。
引数は環境変数 CHANGEWATCH_* としても渡す。終了コード 0 で成功、9 か出力に already exists で「既にある」。
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .. import template
from ..util import REDACT, ConfigError
from . import DONE, FAILED, SendError


@dataclass
class Spec:
    command: list
    retry_command: list | None
    status_command: list | None
    timeout_sec: float


def parse(options: dict, where: str) -> Spec:
    unknown = sorted(set(options) - {"command", "retry_command", "status_command", "timeout_sec"})
    if unknown:
        raise ConfigError(f"{where}: 知らない項目です: {', '.join(unknown)}")
    for key in ("command", "retry_command", "status_command"):
        v = options.get(key)
        if key == "command" and v is None:
            raise ConfigError(f"{where}.command: argv の配列を書いてください")
        if v is not None and not (isinstance(v, list) and v):
            raise ConfigError(f"{where}.{key}: argv の配列を書いてください")
    t = options.get("timeout_sec", 60)
    if not isinstance(t, (int, float)) or isinstance(t, bool) or t < 1:
        raise ConfigError(f"{where}.timeout_sec: 1 以上の数を書いてください")
    return Spec(options["command"], options.get("retry_command"), options.get("status_command"), float(t))


def decode(b: bytes | None) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


class CommandAdapter:
    def __init__(self, spec: Spec):
        self.spec = spec
        self.timeout_sec = spec.timeout_sec

    def _run(self, argv_t: list, run_id: str, workflow: str, params: dict[str, str], queue: str | None
             ) -> tuple[int, str, str]:
        ctx: dict[str, Any] = {"run_id": run_id, "workflow": workflow, "queue": queue or "",
                               "params_json": json.dumps(params, ensure_ascii=False), "params": params}
        argv = [str(x) for x in template.render(argv_t, ctx)]
        env = dict(os.environ)
        env.update({k: v for k, v in params.items()})
        env["CHANGEWATCH_RUN_ID"] = run_id
        env["CHANGEWATCH_WORKFLOW"] = workflow
        try:
            p = subprocess.run(argv, capture_output=True, timeout=self.spec.timeout_sec, env=env)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise SendError(f"受け渡しのコマンドを実行できません: {e}") from e
        return p.returncode, decode(p.stdout), decode(p.stderr)

    def enqueue(self, run_id: str, workflow: str, params: dict[str, str], queue: str | None) -> str:
        rc, out, err = self._run(self.spec.command, run_id, workflow, params, queue)
        text = (out + "\n" + err).strip()
        if rc == 0:
            return "ok"
        if rc == 9 or "already exists" in text:
            return "duplicate"
        raise SendError(f"受け渡しのコマンドが失敗しました（終了コード {rc}）: {REDACT(text[-500:])}")

    def retry(self, run_id: str, workflow: str) -> None:
        if not self.spec.retry_command:
            raise SendError("delivery.retry_command が無いので再実行を代行できません。受け渡し先の仕組みで再実行してください")
        rc, out, err = self._run(self.spec.retry_command, run_id, workflow, {}, None)
        if rc != 0:
            raise SendError(f"再実行のコマンドが失敗しました（終了コード {rc}）: {REDACT((out + err)[-500:])}")

    def status(self, run_id: str, workflow: str) -> str | None:
        if not self.spec.status_command:
            return None
        rc, out, _ = self._run(self.spec.status_command, run_id, workflow, {}, None)
        first = out.strip().splitlines()[0].strip().lower() if rc == 0 and out.strip() else ""
        return first if first in (DONE, FAILED) else None


def create(spec: Spec, connections) -> CommandAdapter:
    return CommandAdapter(spec)
