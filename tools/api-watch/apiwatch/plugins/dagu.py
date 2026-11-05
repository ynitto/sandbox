"""api-watch の Dagu プラグイン（apiwatch/plugins/dagu.py）。Dagu に依存するものはこのファイルだけにある。

    python api_watch.py --plugin dagu ...     （.pyz なら python api-watch.pyz --plugin dagu ...）

足すもの:
    アダプタ dagu   受け渡し先を Dagu の DAG にする（delivery.adapter: dagu）
    コマンド dags   Dagu の DAG を書き出す（取得元ごとの取得・再送・整理）

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
DAG は v2.25.2 の dag.schema.json に合う書式（type: graph + depends、action: exec の argv）で書く。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apiwatch import VERSION
from apiwatch.adapters import DONE, FAILED, SendError
from apiwatch.adapters.command import decode
from apiwatch.config import Config
from apiwatch.connections import parse_ref
from apiwatch.util import EXIT_OK, REDACT, ConfigError, log


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


# ---------------------------------------------------------------------------
# DAG の生成（dags コマンド）
# ---------------------------------------------------------------------------


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _step(name: str, argv: list[str], *, depends: str | None, timeout: int | None, retry: dict | None) -> list[str]:
    lines = [f"  - name: {name}"]
    if depends:
        lines.append(f"    depends: [{depends}]")
    lines += ["    action: exec", "    with:", f"      command: {_q(argv[0])}", "      args:"]
    lines += [f"        - {_q(a)}" for a in argv[1:]]
    if timeout:
        lines.append(f"    timeout_sec: {timeout}")
    if retry:
        lines.append("    retry_policy:")
        lines.append(f"      limit: {retry['limit']}")
        lines.append(f"      interval_sec: {retry['interval_sec']}")
        if retry.get("backoff"):
            lines.append("      backoff: true")
            lines.append(f"      max_interval_sec: {retry['max_interval_sec']}")
        if retry.get("exit_code"):
            lines.append(f"      exit_code: {json.dumps(retry['exit_code'])}")
    return lines


def _header(cfg: Config, desc: str, schedule: str, opts) -> list[str]:
    lines = [f"# api-watch {VERSION} の dags が作った雛形。作ったあとは自由に直してよい（dags は既にあるファイルを上書きしない）。",
             f"# 設定: {cfg.path}",
             "# schedule は Dagu がこのワークフローを起動する間隔。取得元に schedule があれば、起動されても予定の時刻までは",
             "# api-watch が取得しない（取得の予定は api-watch の設定にある）。無ければ起動されるたびに取得する",
             f"description: {_q(desc)}",
             f"schedule: {_q(schedule)}",
             "type: graph",
             f"hist_retention_days: {opts.hist_retention_days}"]
    if opts.dotenv:
        lines.append("dotenv:")
        lines += [f"  - {_q(d)}" for d in opts.dotenv]
    lines.append("steps:")
    return lines


def _exec(argv: list[str], indent: str) -> list[str]:
    return [f"{indent}action: exec", f"{indent}with:", f"{indent}  command: {_q(argv[0])}", f"{indent}  args:"] + \
        [f"{indent}    - {_q(a)}" for a in argv[1:]]


def _child(cfg: Config, base: list[str], rname: str, t, opts) -> list[str]:
    """external の受け渡し先の子のワークフロー（親の dag.run が受け渡しの ID を DELIVERY で渡す）。

    begin が run と言ったときだけ handle を実行し、終わったら handler_on で finish する。handle は雛形なので、
    処理（エージェントの harness.run など）に置き換えて使う。"""
    d = "${params.DELIVERY}"
    lines = [f"# api-watch {VERSION} の dags が作った雛形。handle を処理に置き換えて使う（dags は既にあるファイルを上書きしない）。",
             f"# 設定: {cfg.path}",
             f"# ルール {rname} の受け渡し先 {t.name} の受け渡しを 1 件処理する。親のワークフローが DELIVERY（受け渡しの ID）を渡す。",
             "#   begin   取り直して、実行してよいか（run / skip / busy / wait / failed / gone）を DECISION に出す",
             "#   handle  DECISION が run のときだけ動く。イベントは render で文章にできる:",
             f"#           {' '.join(base[1:2])} ... render {d} --template <テンプレート>",
             "#   finish  handler_on から呼ぶ。成功なら処理し終えた状態を記録し、失敗ならあとでやり直す",
             f"description: {_q(f'ルール {rname} の受け渡し先 {t.name} を 1 件処理する')}",
             "type: graph",
             f"hist_retention_days: {opts.hist_retention_days}",
             "params:",
             '  - DELIVERY: ""']
    if opts.dotenv:
        lines.append("dotenv:")
        lines += [f"  - {_q(x)}" for x in opts.dotenv]
    lines.append("steps:")
    lines += _step("begin", base + ["begin", d], depends=None, timeout=600, retry=None)
    lines.append("    output: DECISION")
    run_only = ["    preconditions:", '      - condition: "${DECISION}"', '        expected: "run"']
    last = "begin"
    if t.input_template:
        # 受け渡し先の input_template を展開した文章を INPUT に入れる（handle で ${INPUT} として使う）
        lines += _step("render", base + ["render", d], depends="begin", timeout=300, retry=None)
        lines += run_only + ["    output: INPUT"]
        last = "render"
    lines += ["  - name: handle", f"    depends: [{last}]"] + run_only + [f"    timeout_sec: {int(t.timeout_sec)}"]
    snippet = (getattr(opts, "handle_steps", None) or {}).get(f"{rname}.{t.name}")
    if snippet is not None:
        # dags --handle-step で渡されたステップの中身（name・depends・preconditions は上で付けた）
        lines += ["    " + ln if ln.strip() else "" for ln in snippet.rstrip("\n").splitlines()]
    else:
        lines += ["    # ここを処理に置き換える（例: action: harness.run でエージェント CLI を動かす。dags --handle-step で差し込める）",
                  f'    run: echo "子のワークフロー {rname}-{t.name} の handle を処理に置き換えてください" >&2; exit 1']
    lines.append("handler_on:")
    for when, extra in (("success", []), ("failure", ["--failed"]), ("abort", ["--failed", "--message", "中止された"])):
        lines.append(f"  {when}:")
        lines += _exec(base + ["finish", d] + extra, "    ")
    return lines


def generate(cfg: Config, out_dir: Path, python: str, script: Path, opts) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = [python, str(script), "--config", str(cfg.path)] + plugin_args(getattr(opts, "plugin", None) or ["dagu"])
    if cfg.connections.path is not None:
        base += ["--connections", str(cfg.connections.path)]
    if getattr(opts, "state_dir", None):
        base += ["--state-dir", str(cfg.state_dir)]
    prefix = opts.prefix
    written = []

    def write(name: str, lines: list[str]) -> None:
        path = out_dir / f"{prefix}-{name}.yaml"
        if path.exists() and not getattr(opts, "force", False):
            log(f"既にあるので書きません（上書きするなら --force）: {path}")
            return
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)

    def schedule_for(src) -> str:
        # 設定の schedule は書き写さない。Dagu の起動間隔は --schedule（既定は毎分。取得するかは api-watch が決める）
        if opts.schedule:
            return opts.schedule
        if src.cron is None and not src.min_interval_sec:
            raise ConfigError(f"取得元 {src.name} には schedule も min_interval_sec も無いので、毎分起動すると毎分問い合わせます。"
                              "api-watch の設定に schedule（取得の予定）か min_interval_sec を書くか、"
                              "dags --schedule で Dagu の起動間隔を指定してください")
        return "* * * * *"

    def fetch_step(src) -> list[str]:
        # ずらし（jitter_sec）と、別のワークフローの取得を待つ時間（busy_wait_sec）も入れる
        timeout = int(max(300, src.timeout_sec * src.retry_attempts * 4 + src.max_wait_sec + 120
                          + src.jitter_sec + src.busy_wait_sec))
        return _step("fetch", base + ["fetch", "--source", src.name], depends=None, timeout=timeout,
                     retry={"limit": 3, "interval_sec": 60, "backoff": True, "max_interval_sec": 600,
                            "exit_code": [3]})

    def here(rule) -> bool:
        return any(t.runs_here for t in rule.targets)

    for name, src in cfg.sources.items():
        rules = cfg.rules_for(name)
        if rules and all(here(r) for r in rules):
            continue      # どのルールも自分のワークフロー（下）で取得から処理まで行う
        lines = _header(cfg, f"{name} を取得し、ルールで判定して受け渡す", schedule_for(src), opts)
        lines += fetch_step(src)
        lines += _step("evaluate", base + ["evaluate", "--source", name], depends="fetch", timeout=600,
                       retry={"limit": 2, "interval_sec": 10})
        lines += _step("dispatch", base + ["dispatch", "--source", name], depends="evaluate", timeout=600, retry=None)
        write(name, lines)
    # command の受け渡し先があるルールは、1 つのワークフローで取得 → 判定 → 実行まで行う。
    # 同じ取得元を使うワークフローが重なっても、取得は schedule（または min_interval_sec / busy_wait_sec）で 1 回にまとまる
    reserved = set(cfg.sources) | {"dispatch", "maintenance"}
    for rname, rule in cfg.rules.items():
        if not here(rule):
            continue
        if rname in reserved:
            raise ConfigError(f"ルール {rname} のワークフロー名 {prefix}-{rname} が、取得元か再送・整理のワークフローと重なります。"
                              "ルールの名前を変えてください")
        src = cfg.sources[rule.source]
        lines = _header(cfg, f"{rule.source} を取得し、ルール {rname} で判定して、その場で実行する",
                        schedule_for(src), opts)
        lines += fetch_step(src)
        lines += _step("evaluate", base + ["evaluate", "--rule", rname], depends="fetch", timeout=600,
                       retry={"limit": 2, "interval_sec": 10})
        last = "evaluate"
        if any(not t.runs_here for t in rule.targets):
            lines += _step("dispatch", base + ["dispatch", "--rule", rname], depends="evaluate", timeout=600,
                           retry=None)
            last = "dispatch"
        if any(t.command is not None for t in rule.targets):
            # 1 件ごとの上限は受け渡し先の timeout_sec が持つので、ステップには上限を付けない
            lines += _step("work", base + ["work", "--rule", rname], depends=last, timeout=None, retry=None)
            last = "work"
        for t in rule.targets:
            if not t.external:
                continue
            # external の受け渡し先: 実行を待っている受け渡しを、子のワークフローで 1 件ずつ（同時に 1 つ）処理する
            child = f"{prefix}-{rname}-{t.name}"
            if f"{rname}-{t.name}" in reserved or f"{rname}-{t.name}" in cfg.rules:
                raise ConfigError(f"子のワークフロー名 {child} が、ほかのワークフローと重なります。受け渡し先の name を変えてください")
            var = "DUE_" + re.sub(r"[^A-Za-z0-9]", "_", t.name).upper()
            lines += _step(f"due-{t.name}", base + ["due", "--rule", rname, "--target", t.name], depends=last,
                           timeout=300, retry=None)
            lines.append(f"    output: {var}")
            lines += [f"  - name: run-{t.name}", f"    depends: [due-{t.name}]", "    action: dag.run", "    with:",
                      f"      dag: {_q(child)}", "      params:", '        DELIVERY: "${ITEM}"',
                      "    parallel:", f'      items: "${{{var}}}"', "      max_concurrent: 1",
                      "    preconditions:              # 実行を待っているものが無ければ呼ばない（空の配列は Dagu が誤りにする）",
                      f'      - condition: "${{{var}}}"', '        expected: "[]"', "        negate: true"]
            write(f"{rname}-{t.name}", _child(cfg, base, rname, t, opts))
        write(rname, lines)
    if any(not t.runs_here for r in cfg.rules.values() for t in r.targets):
        lines = _header(cfg, "未判定の取得結果の判定と、渡せていない変更の再送・後続処理の完了の確認",
                        opts.dispatch_schedule, opts)
        lines += _step("evaluate", base + ["evaluate"], depends=None, timeout=600, retry=None)
        lines += _step("dispatch", base + ["dispatch"], depends="evaluate", timeout=600, retry=None)
        lines += _step("reconcile", base + ["reconcile"], depends="dispatch", timeout=300, retry=None)
        write("dispatch", lines)
    lines = _header(cfg, "保持期間を過ぎた取得結果・イベントを整理する", opts.maintenance_schedule, opts)
    lines += _step("gc", base + ["gc"], depends=None, timeout=1800, retry=None)
    write("maintenance", lines)
    return written


def entry_path() -> Path:
    """DAG から呼ぶ入口。.pyz から動いていればそのファイル、そうでなければ api_watch.py。"""
    import apiwatch
    root = Path(apiwatch.__file__).resolve().parent.parent
    return root if root.is_file() else root / "api_watch.py"


def plugin_args(specs: list[str]) -> list[str]:
    out: list[str] = []
    for s in dict.fromkeys(specs):
        if s.endswith(".py") or "/" in s or "\\" in s:
            s = str(Path(s).expanduser().resolve())
        out += ["--plugin", s]
    return out


def cmd_dags(cfg: Config, store, a) -> int:
    a.handle_steps = {}
    for spec in a.handle_step or []:
        key, sep, path = spec.partition("=")
        if not sep or "." not in key:
            raise ConfigError(f"--handle-step は <ルール>.<受け渡し先>=<ファイル> の形で書いてください: {spec}")
        r, _, tn = key.partition(".")
        rule = cfg.rules.get(r)
        if rule is None or not any(t.name == tn and t.external for t in rule.targets):
            raise ConfigError(f"--handle-step: ルール {r} に external の受け渡し先 {tn} がありません")
        try:
            a.handle_steps[key] = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"--handle-step のファイルが読めません: {path}（{e}）")
    script = Path(a.script).resolve() if a.script else entry_path()
    for p in generate(cfg, Path(a.out).expanduser().resolve(), a.python or sys.executable, script, a):
        log(f"書きました: {p}")
    return EXIT_OK


def register(api) -> None:
    """--plugin dagu で読み込まれたときに、アダプタ dagu とコマンド dags を足す。"""
    api.add_adapter("dagu", parse, create)
    sp = api.add_command("dags", cmd_dags, "Dagu の DAG の雛形を書き出す（取得元ごとの取得・ルールごとの取得から実行まで・再送・整理）")
    sp.add_argument("--out", required=True)
    sp.add_argument("--python", help="DAG が使う Python（既定: いま動いている Python）")
    sp.add_argument("--script", help="DAG が呼ぶ api_watch.py（既定: apiwatch と同じ場所の api_watch.py）")
    sp.add_argument("--prefix", default="api-watch")
    sp.add_argument("--dotenv", action="append", default=[], help="DAG に読み込ませる環境変数のファイル（繰り返し可）")
    sp.add_argument("--schedule", help="取得するワークフローを Dagu が起動する間隔（既定: 毎分。取得するかは api-watch が"
                                       "取得元の schedule / min_interval_sec で決める）")
    sp.add_argument("--force", action="store_true", help="既にある DAG を上書きする")
    sp.add_argument("--handle-step", action="append", default=[],
                    help="external の受け渡し先の子のワークフローの handle に差し込むステップの中身（<ルール>.<受け渡し先>=<YAML のファイル>）")
    sp.add_argument("--dispatch-schedule", default="* * * * *", help="再送の DAG の起動間隔（既定: 毎分）")
    sp.add_argument("--maintenance-schedule", default="17 3 * * *", help="整理の DAG の起動間隔（既定: 毎日 3:17）")
    sp.add_argument("--hist-retention-days", type=int, default=14, help="Dagu の実行履歴を残す日数")
