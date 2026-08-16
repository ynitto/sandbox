from __future__ import annotations
# ci.py — 公開（ブランチ push）後の CI 結果を run のデリバリへ取り込む。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# 「公開は成功したが CI が赤」を、公開失敗と同じ器（結果ノードの publication）へ載せる
# （設計: docs/plans/2026-08-15-workflow-feature-improvement-proposals.md P6）。
# 読み手（dashboard）は既にこの器を見ているので、ここは**書き手**だけを足す。
#
# CI の種類ごとのクライアントは持たない。利用者が宣言したコマンドの JSON 出力を正典にする
# ——統一 verify の固定コマンドと同じ「決定的な外部コマンド + 受領証」の作法で、gh / glab /
# 社内 CI のどれでも同じ契約で扱える。
#
#   ci_status_command … 標準出力へ {"state": "...", "url": "...", "checks": [...]} を出す
#                       コマンド。実行時に AGENT_CI_URL / AGENT_CI_BRANCH / AGENT_CI_COMMIT /
#                       AGENT_CI_REPOSITORY を環境変数で渡す
#   ci_wait_seconds   … 終端（passed/failed）まで待つ上限秒。0（既定）= 1 回だけ問い合わせる
#   ci_poll_seconds   … 待つときの問い合わせ間隔
#
# 状態は passed / failed / running / unknown の 4 値。**判定できないときは unknown**で、
# 決して passed に倒さない——黙って緑にすると、CI が赤い成果が「完了」として下流へ流れる。

CI_STATES = ("passed", "failed", "running", "unknown")
CI_TERMINAL_STATES = ("passed", "failed")
# CI の状態語は系統ごとに違う（GitHub は success/failure、GitLab は success/failed、
# 素の CLI は ok/ng）。宣言側にこの表を強制せず、こちらで正規語彙へ寄せる。
_CI_STATE_ALIASES = {
    "passed": "passed", "pass": "passed", "success": "passed", "successful": "passed",
    "ok": "passed", "green": "passed", "completed": "passed", "neutral": "passed",
    "failed": "failed", "failure": "failed", "fail": "failed", "error": "failed",
    "red": "failed", "cancelled": "failed", "canceled": "failed", "timed_out": "failed",
    "action_required": "failed",
    "running": "running", "pending": "running", "queued": "running", "waiting": "running",
    "in_progress": "running", "started": "running",
}
_CI_MAX_WAIT_SECONDS = 1800.0
_CI_COMMAND_TIMEOUT = 120.0
_CI_CHECK_LIMIT = 50


def ci_state(raw) -> str:
    """CI 状態語を passed / failed / running / unknown へ正規化する。未知語は unknown。"""
    return _CI_STATE_ALIASES.get(str(raw or "").strip().lower(), "unknown")


def normalize_ci_report(raw, checked_at: "str | None" = None) -> "dict | None":
    """コマンドが返した JSON を CI レコードへ整える。dict でなければ None（＝記録しない）。"""
    if not isinstance(raw, dict):
        return None
    checks = []
    for item in (raw.get("checks") if isinstance(raw.get("checks"), list) else [])[:_CI_CHECK_LIMIT]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        checks.append({"name": name, "state": ci_state(item.get("state") or item.get("conclusion")),
                       "url": str(item.get("url") or "").strip()})
    report = {
        "state": ci_state(raw.get("state") or raw.get("status") or raw.get("conclusion")),
        "url": str(raw.get("url") or "").strip(),
        "checked_at": checked_at or now_iso(),
    }
    if checks:
        report["checks"] = checks
    detail = str(raw.get("detail") or raw.get("note") or "").strip()
    if detail:
        report["detail"] = detail[:300]
    return report


def _ci_command_report(command: str, env: dict, cwd: "str | None") -> dict:
    """状態コマンドを 1 回実行して CI レコードを返す。壊れた出力・失敗は unknown（緑にしない）。"""
    try:
        proc = subprocess.run(command, shell=True, cwd=(cwd if cwd and os.path.isdir(cwd) else None),
                              env={**os.environ, **env}, timeout=_CI_COMMAND_TIMEOUT,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"state": "unknown", "checked_at": now_iso(),
                "detail": f"CI 状態コマンドがタイムアウトしました（{int(_CI_COMMAND_TIMEOUT)}s）"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "unknown", "checked_at": now_iso(),
                "detail": f"CI 状態コマンドを起動できませんでした: {str(exc)[:200]}"}
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[:200]
        return {"state": "unknown", "checked_at": now_iso(),
                "detail": f"CI 状態コマンドが失敗しました（exit {proc.returncode}）: {tail}"}
    try:
        parsed = json.loads(str(proc.stdout or "").strip() or "null")
    except ValueError:
        parsed = None
    report = normalize_ci_report(parsed)
    if report is None:
        return {"state": "unknown", "checked_at": now_iso(),
                "detail": "CI 状態コマンドの出力を JSON として読めませんでした"}
    return report


def collect_ci_status(publication: dict, command: str, wait_seconds: float = 0.0,
                      poll_seconds: float = 30.0, runner=None, sleeper=None) -> "dict | None":
    """公開済み branch/commit の CI 状態を取る。終端まで待つ上限は wait_seconds（0 = 1 回だけ）。

    待っても終端しなければ、そのときの状態（running 等）をそのまま記録する——「まだ回っている」
    ことは事実であり、緑にも赤にも倒さない。"""
    if not command or not isinstance(publication, dict):
        return None
    env = {
        "AGENT_CI_URL": str(publication.get("url") or ""),
        "AGENT_CI_BRANCH": str(publication.get("branch") or ""),
        "AGENT_CI_COMMIT": str(publication.get("commit") or ""),
        "AGENT_CI_REPOSITORY": str((publication.get("recovery") or {}).get("repository") or ""),
    }
    if not env["AGENT_CI_BRANCH"] or not env["AGENT_CI_COMMIT"]:
        return None
    ask = runner or (lambda: _ci_command_report(command, env, env["AGENT_CI_REPOSITORY"] or None))
    rest = sleeper or backoff_sleep
    budget = max(0.0, min(float(wait_seconds or 0.0), _CI_MAX_WAIT_SECONDS))
    interval = max(1.0, float(poll_seconds or 30.0))
    waited = 0.0
    report = ask()
    while report.get("state") not in CI_TERMINAL_STATES and waited < budget:
        rest(min(interval, budget - waited))
        waited += interval
        report = ask()
    return report


def _published_publications(bus) -> list:
    """公開に成功した結果ノードの (node_id, result, publication) を返す。"""
    found = []
    for node_id in bus.task_ids():
        result = bus.read_result(node_id) or {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        delivery = data.get("delivery") if isinstance(data.get("delivery"), dict) else {}
        publication = data.get("publication") if isinstance(data.get("publication"), dict) else \
            (delivery.get("publication") if isinstance(delivery.get("publication"), dict) else {})
        if str(publication.get("state") or "") not in ("published", "published-manually"):
            continue
        found.append((node_id, result, publication))
    return found


def _worst_ci_state(states) -> str:
    for state in ("failed", "unknown", "running"):
        if state in states:
            return state
    return "passed" if states else "unknown"


def attach_ci_results(bus, args, collector=None) -> "dict | None":
    """公開済みノードへ CI 結果を書き戻し、run 全体の CI 状態を返す（記録が無ければ None）。

    書き先は結果ノードの publication そのもの（`data.publication.ci`）。公開失敗の復旧導線と
    同じ器に載るので、読み手は公開状態と CI を 1 か所で読める。"""
    command = str(getattr(args, "ci_status_command", "") or "").strip()
    if not command:
        return None
    records = _published_publications(bus)
    if not records:
        return None
    collect = collector or collect_ci_status
    cache: dict = {}
    states = []
    for node_id, result, publication in records:
        commit = str(publication.get("commit") or "")
        if commit not in cache:
            cache[commit] = collect(
                publication, command,
                float(getattr(args, "ci_wait_seconds", 0) or 0),
                float(getattr(args, "ci_poll_seconds", 30) or 30))
        report = cache[commit]
        if not report:
            continue
        states.append(report.get("state"))
        data = dict(result.get("data") or {})
        if isinstance(data.get("publication"), dict):
            data["publication"] = {**data["publication"], "ci": report}
        elif isinstance(data.get("delivery"), dict):
            delivery = dict(data["delivery"])
            delivery["publication"] = {**(delivery.get("publication") or {}), "ci": report}
            data["delivery"] = delivery
        else:
            continue
        result["data"] = data
        write_json_atomic(bus.result_path(node_id), result)
    if not states:
        return None
    overall = _worst_ci_state(set(states))
    urls = [str((cache.get(c) or {}).get("url") or "") for c in cache]
    return {"state": overall, "checked_at": now_iso(),
            **({"url": next((u for u in urls if u), "")} if any(urls) else {})}
