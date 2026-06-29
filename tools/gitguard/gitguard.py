#!/usr/bin/env python3
"""gitguard — git/GitLab アクセスの横断サーキットブレーカー + 監視。

特定のツール/スキルに依存せず、ホスト内のすべての git/GitLab アクセスを 1 つの
ブレーカーと 1 本のイベントログで束ねる。設計の詳細・転用方法は
docs/designs/git-gitlab-circuit-breaker-pattern.md を参照。

採用は 3 経路:
  1. Python から薄く: `import gitguard; p = gitguard.git(["clone", url, dest])`
  2. 任意の操作を包む:   `with gitguard.guard(ep, "fetch") as g: ...; g.success()`
  3. shell/非 Python:    `git-guard clone <url> <dest>` / `git-guard api <host> GET <path>`

不変条件:
  INV-1 トリップはインフラ/一過性障害のみ（接続不可・DNS・timeout・429・5xx・407）。
        正当な失敗（git のマージ衝突等の非 0 終了・404/422 等の 4xx）は数えない＝誤爆しない。
  INV-2 状態はホスト共有（flock + JSON）。全プロセス・全ツールで 1 つのブレーカーを共有。
  INV-3 既定は監視のみ（ブロックしない）。GITGUARD_ENFORCE=1 で初めて fail-fast。
        導入しても既存挙動を壊さない（観測→しきい値調整→enforce の順で安全に効かせる）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

try:
    import fcntl  # POSIX のみ。無ければロックは no-op（ベストエフォート）。
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
# 結果分類: 成功 / インフラ障害（トリップ対象）/ アプリ起因の失敗（数えない）/ ブロック（開状態で短絡）
SUCCESS, INFRA_FAIL, APP_FAIL, BLOCKED = "success", "infra_fail", "app_fail", "blocked"

# インフラ/一過性障害のシグネチャ（git stderr・例外文言の小文字に対して検査）。
_INFRA_PATTERNS = re.compile(
    r"could not resolve host|couldn't resolve host|name or service not known|"
    r"temporary failure in name resolution|connection refused|connection reset|"
    r"connection timed out|operation timed out|timed out|failed to connect|"
    r"network is unreachable|no route to host|the remote end hung up|early eof|"
    r"rpc failed|ssl|tls|gnutls|unable to access|503 |502 |500 |429 |"
    r"service unavailable|bad gateway|gateway time-?out|too many requests|"
    r"proxy|407 |remote error|http/2 stream|recv failure|send failure",
    re.IGNORECASE,
)


class CircuitOpenError(RuntimeError):
    """ブレーカーが開いていて（enforce 時に）アクセスを短絡したことを表す。"""

    def __init__(self, endpoint: str, state: str):
        super().__init__(f"circuit open for {endpoint} (state={state})")
        self.endpoint = endpoint
        self.state = state


# --------------------------------------------------------------------------
# 設定（すべて環境変数。プロセス毎に都度読む＝外から動的に変えられる）
# --------------------------------------------------------------------------
def _root() -> str:
    return os.environ.get("GITGUARD_DIR") or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "gitguard")


def _cfg() -> dict:
    def _f(name, default):
        try:
            return float(os.environ.get(name, "") or default)
        except ValueError:
            return float(default)
    return {
        "enforce": (os.environ.get("GITGUARD_ENFORCE", "") or "0") not in ("0", "", "false", "no"),
        "disabled": (os.environ.get("GITGUARD_DISABLE", "") or "0") not in ("0", "", "false", "no"),
        "threshold": int(_f("GITGUARD_THRESHOLD", 5)),     # 連続インフラ障害で開く回数
        "cooldown": _f("GITGUARD_COOLDOWN", 60.0),         # open → half_open までの秒
        "window": _f("GITGUARD_WINDOW", 120.0),            # 連続カウントを束ねる窓（秒）
    }


# テスト用に時計を差し替えられるようにする（ホスト共有のため wall-clock を使う）。
_time = time.time


def _now() -> float:
    return _time()


def _key(endpoint: str) -> str:
    return hashlib.sha1(endpoint.strip().encode()).hexdigest()


def _state_path(endpoint: str) -> str:
    return os.path.join(_root(), "state", f"{_key(endpoint)}.json")


def _events_path() -> str:
    return os.path.join(_root(), "events.ndjson")


@contextmanager
def _lock(endpoint: str):
    d = os.path.join(_root(), "locks")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{_key(endpoint)}.lock")
    if fcntl is None:  # pragma: no cover
        yield
        return
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _read_state(endpoint: str) -> dict:
    try:
        with open(_state_path(endpoint), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"endpoint": endpoint, "state": CLOSED, "consecutive": 0,
                "opened_at": 0.0, "probe_inflight": False,
                "updated_at": 0.0, "last_error": ""}


def _write_state(st: dict) -> None:
    st["updated_at"] = _now()
    path = _state_path(st["endpoint"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, path)


def _emit(event: dict) -> None:
    try:
        os.makedirs(_root(), exist_ok=True)
        with open(_events_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:  # 監視ログの失敗で本処理を止めない
        pass


# --------------------------------------------------------------------------
# 状態機械
# --------------------------------------------------------------------------
def endpoint_for_url(url: str, kind: str = "git") -> str:
    """URL/リモートからエンドポイントキー（kind:host）を作る。host が取れなければ raw を使う。"""
    raw = (url or "").strip()
    host = ""
    if "://" in raw:
        host = urllib.parse.urlparse(raw).hostname or ""
    elif "@" in raw and ":" in raw:                  # scp 形式 git@host:path
        host = raw.split("@", 1)[1].split(":", 1)[0]
    return f"{kind}:{host or raw or 'unknown'}"


def decide(endpoint: str) -> "tuple[bool, str]":
    """いま呼んでよいか（allowed, state）を返す。open→half_open 遷移とプローブ制御を行う。"""
    if _cfg()["disabled"]:
        return True, CLOSED
    cfg = _cfg()
    with _lock(endpoint):
        st = _read_state(endpoint)
        state = st.get("state", CLOSED)
        if state == OPEN:
            if _now() - st.get("opened_at", 0.0) >= cfg["cooldown"]:
                st["state"] = HALF_OPEN          # クールダウン明け → プローブを 1 本だけ通す
                st["probe_inflight"] = True
                _write_state(st)
                return True, HALF_OPEN
            return False, OPEN                    # まだ開いている → 短絡
        if state == HALF_OPEN:
            if st.get("probe_inflight"):
                return False, HALF_OPEN           # プローブ進行中 → 他は短絡
            st["probe_inflight"] = True
            _write_state(st)
            return True, HALF_OPEN
        return True, CLOSED


def report(endpoint: str, outcome: str, op: str = "",
           latency_ms: float = 0.0, error: str = "") -> str:
    """結果を記録して状態遷移し、監視イベントを 1 件吐く。遷移後の state を返す。"""
    cfg = _cfg()
    new_state = CLOSED
    if not cfg["disabled"]:
        with _lock(endpoint):
            st = _read_state(endpoint)
            state = st.get("state", CLOSED)
            if outcome == SUCCESS:
                st.update(state=CLOSED, consecutive=0, probe_inflight=False)
            elif outcome == INFRA_FAIL:
                if state == HALF_OPEN:
                    st.update(state=OPEN, opened_at=_now(), probe_inflight=False)  # プローブ失敗 → 再び開く
                else:
                    # 窓を越えた古い連続カウントはリセットしてから加算
                    if _now() - st.get("updated_at", 0.0) > cfg["window"]:
                        st["consecutive"] = 0
                    st["consecutive"] = int(st.get("consecutive", 0)) + 1
                    if st["consecutive"] >= cfg["threshold"]:
                        st.update(state=OPEN, opened_at=_now())
                st["last_error"] = (error or "")[:200]
            elif outcome == APP_FAIL:
                # アプリ起因はブレーカーに数えない（が half_open のプローブは閉じる）
                if state == HALF_OPEN:
                    st.update(state=CLOSED, consecutive=0, probe_inflight=False)
            # BLOCKED は状態を変えない（記録のみ）
            _write_state(st)
            new_state = st["state"]
    _emit({"ts": _now(), "endpoint": endpoint, "op": op, "outcome": outcome,
           "latency_ms": round(latency_ms, 1), "error": (error or "")[:200],
           "state": new_state, "pid": os.getpid()})
    return new_state


def _enforcing(override: "bool | None") -> bool:
    return _cfg()["enforce"] if override is None else bool(override)


# --------------------------------------------------------------------------
# guard — 任意の操作を包む最小プリミティブ（最も疎結合な採用経路）
# --------------------------------------------------------------------------
class _Recorder:
    def __init__(self, endpoint: str, op: str):
        self.endpoint, self.op = endpoint, op
        self._t0 = time.monotonic()
        self._done = False

    def _lat(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def success(self) -> None:
        self._done = True
        report(self.endpoint, SUCCESS, self.op, self._lat())

    def infra(self, error: str = "") -> None:
        self._done = True
        report(self.endpoint, INFRA_FAIL, self.op, self._lat(), error)

    def app(self, error: str = "") -> None:
        self._done = True
        report(self.endpoint, APP_FAIL, self.op, self._lat(), error)

    def http_status(self, code: int, error: str = "") -> None:
        """HTTP ステータスから自動分類（429/408/5xx=インフラ, 2xx=成功, 他 4xx=アプリ）。"""
        if code in (408, 429) or 500 <= code <= 599:
            self.infra(error or f"HTTP {code}")
        elif 200 <= code < 400:
            self.success()
        else:
            self.app(error or f"HTTP {code}")


@contextmanager
def guard(endpoint: str, op: str = "", enforce: "bool | None" = None):
    """endpoint への 1 アクセスを包む。開いていれば（enforce 時）CircuitOpenError。
    ブロックで yield しないので with 本体は実行されない。本体で g.success()/g.infra()/
    g.app()/g.http_status() を呼ぶ。呼ばずに抜ければ成功とみなす。例外は INFRA とみなして再送出。"""
    allowed, state = decide(endpoint)
    if not allowed:
        report(endpoint, BLOCKED, op)
        if _enforcing(enforce):
            raise CircuitOpenError(endpoint, state)
        # 監視のみモード: 開いていても通す（観測のため）
    rec = _Recorder(endpoint, op)
    try:
        yield rec
    except CircuitOpenError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        rec.infra(str(e))
        raise
    except Exception as e:  # noqa: BLE001 — 想定外はアプリ失敗として記録（トリップしない）
        if not rec._done:
            rec.app(str(e))
        raise
    else:
        if not rec._done:
            rec.success()


# --------------------------------------------------------------------------
# git ラッパ（subprocess.run の drop-in に近い）
# --------------------------------------------------------------------------
_REMOTEY = re.compile(r"^(https?://|git://|ssh://|[^/]+@[^/]+:)")
_NET_OPS = {"clone", "fetch", "pull", "push", "ls-remote", "remote-https", "submodule"}


def _detect_remote(args: "list[str]", cwd: "str | None") -> str:
    for a in args:
        if isinstance(a, str) and _REMOTEY.match(a):
            return a
    try:
        r = subprocess.run(["git", "-C", cwd or ".", "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def classify_git(returncode: int, stderr: str) -> str:
    if returncode == 0:
        return SUCCESS
    return INFRA_FAIL if _INFRA_PATTERNS.search(stderr or "") else APP_FAIL


def git(args, cwd: "str | None" = None, remote: "str | None" = None,
        timeout: float = 600.0, env: "dict | None" = None,
        enforce: "bool | None" = None) -> subprocess.CompletedProcess:
    """`git args` を実行し、結果を分類してブレーカー/監視へ反映する。返り値は subprocess の
    CompletedProcess（失敗もそのまま返す＝呼び出し側の既存リトライを壊さない）。ブレーカーが開いて
    いて enforce 有効なら CircuitOpenError。ネットワーク操作でなければブレーカーは素通り（記録のみ）。"""
    args = list(args)
    op = args[0] if args else ""
    is_net = op in _NET_OPS
    ep = endpoint_for_url(remote or (_detect_remote(args, cwd) if is_net else ""), "git")
    if is_net and ep.endswith(":") is False:
        allowed, state = decide(ep)
        if not allowed:
            report(ep, BLOCKED, op)
            if _enforcing(enforce):
                raise CircuitOpenError(ep, state)
    t0 = time.monotonic()
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        if is_net:
            report(ep, INFRA_FAIL, op, (time.monotonic() - t0) * 1000.0, "timeout")
        raise
    if is_net:
        outcome = classify_git(p.returncode, p.stderr or "")
        report(ep, outcome, op, (time.monotonic() - t0) * 1000.0,
               (p.stderr or "").strip()[:200] if p.returncode else "")
    return p


# --------------------------------------------------------------------------
# GitLab REST ラッパ（urllib・依存なし）
# --------------------------------------------------------------------------
def gitlab_api(host: str, method: str, path: str, token: "str | None" = None,
               data=None, params: "dict | None" = None, timeout: float = 30.0,
               enforce: "bool | None" = None) -> "tuple[int, object]":
    """GitLab REST を呼び、(status, parsed_json) を返す。ブレーカー/監視を通す。
    transport 例外（接続不可・timeout）は guard が INFRA として記録し再送出する。"""
    url = f"https://{host}/api/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Content-Type": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ep = endpoint_for_url(f"https://{host}", "gitlab")
    with guard(ep, f"{method} {path.split('?')[0]}", enforce=enforce) as g:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                g.http_status(getattr(resp, "status", 200))
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            g.http_status(e.code, f"HTTP {e.code} {e.reason}")
            try:
                raw = e.read()
                return e.code, (json.loads(raw) if raw.strip() else {})
            except (ValueError, OSError):
                return e.code, {}


# --------------------------------------------------------------------------
# 監視の集計（CLI status/stats が使う）
# --------------------------------------------------------------------------
def list_states() -> "list[dict]":
    d = os.path.join(_root(), "state")
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            pass
    return out


def read_events(since: float = 0.0, limit: int = 0) -> "list[dict]":
    path = _events_path()
    out: "list[dict]" = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("ts", 0) >= since:
                    out.append(ev)
    except OSError:
        return out
    return out[-limit:] if limit else out


def aggregate(since: float = 0.0) -> "dict[str, dict]":
    """endpoint 毎に件数・エラー率・レイテンシ percentile を集計する。"""
    by: "dict[str, dict]" = {}
    for ev in read_events(since):
        ep = ev.get("endpoint", "?")
        a = by.setdefault(ep, {"total": 0, SUCCESS: 0, INFRA_FAIL: 0,
                               APP_FAIL: 0, BLOCKED: 0, "lat": []})
        a["total"] += 1
        a[ev.get("outcome", APP_FAIL)] = a.get(ev.get("outcome", APP_FAIL), 0) + 1
        if ev.get("outcome") == SUCCESS and ev.get("latency_ms"):
            a["lat"].append(float(ev["latency_ms"]))
    for a in by.values():
        lat = sorted(a.pop("lat"))
        a["p50_ms"] = lat[len(lat) // 2] if lat else 0.0
        a["p95_ms"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.0
        net = a["total"] - a[BLOCKED]
        a["infra_rate"] = round(a[INFRA_FAIL] / net, 3) if net else 0.0
    return by


def reset(endpoint: "str | None" = None) -> int:
    """ブレーカー状態をクリアする（endpoint 指定で 1 つ、None で全部）。削除数を返す。"""
    d = os.path.join(_root(), "state")
    if not os.path.isdir(d):
        return 0
    targets = [f"{_key(endpoint)}.json"] if endpoint else os.listdir(d)
    n = 0
    for name in targets:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
_SUBCMDS = {"status", "stats", "reset", "api"}
_CLI_HELP = """git-guard — git/GitLab アクセスの横断サーキットブレーカー + 監視

使い方:
  git-guard <git サブコマンド...>      git をブレーカー経由で透過実行（例: git-guard clone <url> <dest>）
  git-guard api <host> <METHOD> <path> [--token T] [--data JSON]   GitLab REST をブレーカー経由で呼ぶ
  git-guard status                     エンドポイント毎のブレーカー状態
  git-guard stats [--since EPOCH] [--json]   監視イベントの集計
  git-guard reset [endpoint]           ブレーカー状態をクリア

環境変数: GITGUARD_ENFORCE(1で fail-fast) GITGUARD_DISABLE GITGUARD_THRESHOLD
          GITGUARD_COOLDOWN GITGUARD_WINDOW GITGUARD_DIR"""


def _cli(argv: "list[str]") -> int:
    import argparse
    # status/stats/reset/api 以外は git のサブコマンドとして透過実行する（argparse のサブコマンド
    # 検証に git の任意引数が弾かれないよう、先頭引数を見て手で振り分ける）。
    if not argv or argv[0] in ("-h", "--help"):
        print(_CLI_HELP)
        return 0
    cmd = argv[0] if argv[0] in _SUBCMDS else None

    if cmd == "status":
        states = list_states()
        if not states:
            print("（ブレーカー状態なし）")
            return 0
        now = _now()
        for st in states:
            extra = ""
            if st.get("state") == OPEN:
                left = max(0.0, _cfg()["cooldown"] - (now - st.get("opened_at", 0.0)))
                extra = f" cooldown_left={left:.0f}s"
            print(f"{st.get('state', '?'):9} {st.get('endpoint', '?'):40} "
                  f"consecutive={st.get('consecutive', 0)}{extra}"
                  + (f"  last={st.get('last_error', '')[:60]}" if st.get('last_error') else ""))
        return 0
    if cmd == "stats":
        ps = argparse.ArgumentParser(prog="git-guard stats")
        ps.add_argument("--since", type=float, default=0.0, help="この epoch 秒以降だけ集計")
        ps.add_argument("--json", action="store_true")
        a = ps.parse_args(argv[1:])
        agg = aggregate(a.since)
        if a.json:
            print(json.dumps(agg, ensure_ascii=False, indent=2))
            return 0
        if not agg:
            print("（イベントなし）")
            return 0
        for ep, s in sorted(agg.items()):
            print(f"{ep:40} total={s['total']:5} ok={s[SUCCESS]:5} "
                  f"infra={s[INFRA_FAIL]:4} app={s[APP_FAIL]:4} blocked={s[BLOCKED]:4} "
                  f"infra_rate={s['infra_rate']:.3f} p50={s['p50_ms']:.0f}ms p95={s['p95_ms']:.0f}ms")
        return 0
    if cmd == "reset":
        print(f"reset {reset(argv[1] if len(argv) > 1 else None)} 件")
        return 0
    if cmd == "api":
        pa = argparse.ArgumentParser(prog="git-guard api")
        pa.add_argument("host")
        pa.add_argument("method")
        pa.add_argument("path")
        pa.add_argument("--token", default=os.environ.get("GITLAB_TOKEN"))
        pa.add_argument("--data", default=None, help="JSON 文字列")
        a = pa.parse_args(argv[1:])
        data = json.loads(a.data) if a.data else None
        try:
            status, body = gitlab_api(a.host, a.method.upper(), a.path,
                                      token=a.token, data=data)
        except CircuitOpenError as e:
            print(f"BLOCKED: {e}")
            return 75   # EX_TEMPFAIL
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0 if 200 <= status < 400 else 1

    # git 透過モード: argv 全体をそのまま git に渡す（例: git-guard clone <url> <dest>）。
    try:
        proc = git(argv)
    except CircuitOpenError as e:
        print(f"BLOCKED: {e}", flush=True)
        return 75
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", flush=True)
    return proc.returncode


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
