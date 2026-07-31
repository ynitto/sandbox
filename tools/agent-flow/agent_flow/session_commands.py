from __future__ import annotations
# session_commands.py — セッション開始コマンド（agent-session-commands 契約）の読取・計画・実行。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典: schemas/agent-session-commands.schema.json。実体は $AGENT_SESSION_DIR
# （既定 ~/.agents/session/）の session.json（管理面＝agent-dashboard が原子書換）。
#
# agent-flow の「セッション」はワーカープロセス 1 つ。cmd_work / cmd_orchestrate の入口で
# 1 回だけ走らせる。**ノードごとの CLI 呼び出し（run_agent）には入れない** — run あたり
# N 回走ってオーバーヘッドが積み上がるため。
#
# 共通指示（agent-instructions）と違い、これは meta.json にも GitBus にも載せない。
# 副作用のあるコマンドの到達範囲を、各ノードのローカル設定ファイルへ閉じ込める（非伝播が
# 本契約の不変条件）。単発系にはチャットセッションが無いため chat モードはスキップする。
#
# 計画（展開・when 判定・合計秒の有界化）は dashboard（JS）・kiro-loop / agent-loop（Python）と
# 同一結果になるよう決定的に保つ。stdlib のみ。

_SESSION_COMMANDS_DEFAULT_TIMEOUT = 60
_SESSION_COMMANDS_DEFAULT_MAX_TOTAL = 120
_SESSION_COMMANDS_HARD_MAX_TOTAL = 600
_SESSION_COMMANDS_CHAT_ENGINES = ("kiro-loop", "agent-loop", "dashboard")
_SESSION_COMMANDS_PLACEHOLDER_RE = re.compile(
    r"\{(cwd|workspace|engine|workload|agent_cli|model|run_id|node_id)\}"
)
_SESSION_COMMANDS_REV_APPLIED: "int | None" = None


def _session_commands_dir() -> str:
    return os.path.abspath(agent_home_subdir("AGENT_SESSION_DIR", "session"))


def load_session_commands(directory: "str | None" = None) -> "dict | None":
    """session.json を読む。無ければ / 壊れていれば None（＝コマンドなし）。"""
    path = os.path.join(directory or _session_commands_dir(), "session.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def session_commands_revision(data: "dict | None") -> int:
    try:
        return int((data or {}).get("revision") or 0)
    except (TypeError, ValueError):
        return 0


def _session_commands_clamp_total(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _SESSION_COMMANDS_DEFAULT_MAX_TOTAL
    if n <= 0:
        return _SESSION_COMMANDS_DEFAULT_MAX_TOTAL
    return min(n, _SESSION_COMMANDS_HARD_MAX_TOTAL)


def _session_commands_clamp_timeout(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _SESSION_COMMANDS_DEFAULT_TIMEOUT
    if n <= 0:
        return _SESSION_COMMANDS_DEFAULT_TIMEOUT
    return min(n, _SESSION_COMMANDS_HARD_MAX_TOTAL)


def expand_session_placeholders(text, ctx: "dict | None") -> str:
    """プレースホルダを展開する。未定義は空文字へ落とす。**クォートは足さない**
    （空白を含むパスの引用は利用者の責任。複合コマンドを書けるようにするための選択）。"""
    c = ctx if isinstance(ctx, dict) else {}
    return _SESSION_COMMANDS_PLACEHOLDER_RE.sub(
        lambda m: str(c.get(m.group(1)) or ""), str(text or "")
    )


def session_command_matches(when: "dict | None", ctx: "dict | None") -> bool:
    """when 判定。指定された軸をすべて満たすときだけ True（AND 結合）。
    判定材料が ctx に無い軸は絞れないので通す（フェイルセーフ側）。"""
    if not isinstance(when, dict):
        return True
    c = ctx if isinstance(ctx, dict) else {}
    for key, ctx_key in (("engines", "engine"), ("workloads", "workload"), ("agent_cli", "agent_cli")):
        values = when.get(key)
        if not isinstance(values, list):
            continue
        allowed = [str(v).strip() for v in values if str(v).strip()]
        if not allowed:
            continue
        actual = str(c.get(ctx_key) or "").strip()
        if not actual:
            continue
        if actual not in allowed:
            return False
    return True


def render_session_command_bundle(items: list, revision: int = 0) -> str:
    lines = [
        f"<!-- agent-session-command-bundle rev:{int(revision or 0)} -->",
        "## セッション開始時アクション（agent-dashboard 管理）",
        "次の項目は個別ペーストではなく、まとめて依頼された起動アクションです。可能なものを順に実行し、実行できない項目は理由を短く報告してください。",
        "",
    ]
    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. [{item.get('id')}] {item.get('run')}")
    return "\n".join(lines)


def plan_session_commands(data: "dict | None", ctx: "dict | None") -> list:
    """実行計画を組み立てる（決定的・副作用なし）。dashboard のプレビューと同一結果。"""
    out: list = []
    if not isinstance(data, dict) or data.get("enabled") is False:
        return out
    c = ctx if isinstance(ctx, dict) else {}
    engine = str(c.get("engine") or "").strip()
    budget = _session_commands_clamp_total(data.get("max_total_timeout"))
    spent = 0
    bundled = []
    for item in (data.get("commands") or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        run = str(item.get("run") or "").strip()
        if not cid or not run:
            continue
        mode = item.get("mode") if item.get("mode") in ("process", "chat") else "process"
        on_error = item.get("on_error") if item.get("on_error") in ("warn", "fail") else "warn"
        entry = {
            "id": cid,
            "mode": mode,
            "run": expand_session_placeholders(run, c),
            "on_error": on_error,
            "skip": None,
        }
        if mode == "chat":
            entry["strategy"] = item.get("strategy") if item.get("strategy") in ("paste", "bundle") else "paste"
        if mode == "process":
            cwd = str(item.get("cwd") or "").strip()
            entry["cwd"] = expand_session_placeholders(cwd, c) if cwd else str(c.get("cwd") or "")
            entry["timeout"] = _session_commands_clamp_timeout(item.get("timeout"))
            env = item.get("env")
            if isinstance(env, dict) and env:
                entry["env"] = {
                    str(k): expand_session_placeholders(v, c) for k, v in env.items() if str(k).strip()
                }
        if not session_command_matches(item.get("when"), c):
            entry["skip"] = "when"
        elif mode == "chat" and engine and engine not in _SESSION_COMMANDS_CHAT_ENGINES:
            entry["skip"] = "no-session"
        elif mode == "process":
            if spent >= budget:
                entry["skip"] = "budget"
            else:
                entry["timeout"] = min(entry["timeout"], budget - spent)
                spent += entry["timeout"]
        if not entry.get("skip") and mode == "chat" and entry.get("strategy") == "bundle":
            bundled.append({"id": entry["id"], "run": entry["run"]})
        else:
            out.append(entry)
    if bundled:
        revision = (_session_commands_revision(data) if "_session_commands_revision" in globals()
                    else session_commands_revision(data))
        out.append({
            "id": "agent-startup-actions",
            "mode": "chat",
            "strategy": "bundle",
            "run": render_session_command_bundle(bundled, revision),
            "on_error": "warn",
            "skip": None,
            "bundled_ids": [b["id"] for b in bundled],
        })
    return out


def _run_session_process_command(entry: dict) -> "tuple[bool, str]":
    """process モードの 1 件を実行する。(成功か, 失敗理由) を返す。"""
    env = dict(os.environ)
    env.update(entry.get("env") or {})
    cwd = entry.get("cwd") or None
    if cwd and not os.path.isdir(cwd):
        cwd = None  # 消えている cwd で落とさない
    try:
        proc = subprocess.run(
            entry["run"], shell=True, cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=entry.get("timeout") or _SESSION_COMMANDS_DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"{entry.get('timeout')} 秒でタイムアウトしました"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"終了コード {proc.returncode}: {tail[-1] if tail else '出力なし'}"
    return True, ""


def run_session_commands(who: str, ctx: "dict | None") -> bool:
    """ワーカープロセスの入口でセッション開始コマンドを配列順に逐次実行する。

    戻り値は「このプロセスを続けてよいか」。on_error='fail' のコマンドが失敗したときだけ
    False を返す。それ以外はすべて True（不在・破損・無効・個々の失敗はフェイルセーフで続行）。
    chat モードは単発系にセッションが無いためスキップし、その旨をログに残す。
    環境変数 AGENT_FLOW_NO_SESSION_COMMANDS=1 で丸ごと無効化できる。
    """
    global _SESSION_COMMANDS_REV_APPLIED
    if os.environ.get("AGENT_FLOW_NO_SESSION_COMMANDS") == "1":
        return True
    try:
        data = load_session_commands()
        entries = plan_session_commands(data, ctx)
    except Exception:  # noqa: BLE001 — 計画の失敗でワーカーを止めない
        return True
    if not entries:
        return True
    rev = session_commands_revision(data)
    for entry in entries:
        if entry.get("skip"):
            log(who, f"セッション開始コマンド '{entry['id']}' をスキップ（理由: {entry['skip']}）")
            continue
        if entry["mode"] == "chat":
            continue  # plan で skip 済みだが、engine 未指定の呼び出しに備えて二重に守る
        log(who, f"セッション開始コマンド '{entry['id']}' を実行: {entry['run']}")
        ok, reason = _run_session_process_command(entry)
        if ok:
            continue
        if entry["on_error"] == "fail":
            log(who, f"セッション開始コマンド '{entry['id']}' が失敗したため起動を中止: {reason}")
            return False
        log(who, f"セッション開始コマンド '{entry['id']}' が失敗（続行）: {reason}")
    _SESSION_COMMANDS_REV_APPLIED = rev
    return True
