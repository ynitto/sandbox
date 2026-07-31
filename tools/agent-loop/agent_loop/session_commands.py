from __future__ import annotations
# session_commands.py — セッション開始コマンド（agent-session-commands 契約）の読取・計画・実行。
# 単体 import しない。agent_loop/__init__.py が共有名前空間へ順に exec 合成する。
#
# 正典: schemas/agent-session-commands.schema.json。実体は $AGENT_SESSION_DIR
# （既定 ~/.agents/session/）の session.json（管理面＝agent-dashboard が原子書換）。
# agent-loop は tmux ペイン（＝セッション）を起こすたびに 1 回だけ、配列順に逐次実行する。
# process モードはペイン生成の前にホストで走らせ、chat モードは生成後の最初の送信として送る。
#
# 計画（プレースホルダ展開・when 判定・合計秒の有界化）は dashboard（JS の plan()）・
# agent-flow / kiro-loop（Python）と同一結果になるよう決定的に保つ。
# 共通指示（agent-instructions）と違い、これは委譲先ノードへ伝播しない — 副作用のある
# コマンドの到達範囲を、この端末に置いた設定ファイルの範囲へ閉じ込める。

_SESSION_COMMANDS_DEFAULT_TIMEOUT = 60
_SESSION_COMMANDS_DEFAULT_MAX_TOTAL = 120
_SESSION_COMMANDS_HARD_MAX_TOTAL = 600
# chat モードを送れるのは常駐系（セッションが長寿命なエンジン）だけ。
_SESSION_COMMANDS_CHAT_ENGINES = ("kiro-loop", "agent-loop", "dashboard")
_SESSION_COMMANDS_PLACEHOLDER_RE = re.compile(
    r"\{(cwd|workspace|engine|workload|agent_cli|model|run_id|node_id)\}"
)
_SESSION_COMMANDS_REV_APPLIED: "int | None" = None


def _session_commands_dir() -> str:
    return str(agent_home_subdir("AGENT_SESSION_DIR", "session").absolute())


def _load_session_commands() -> "dict | None":
    """session.json を読む。無ければ / 壊れていれば None（＝コマンドなし）。"""
    path = os.path.join(_session_commands_dir(), "session.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _session_commands_revision(data: "dict | None") -> int:
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
    """プレースホルダを展開する。未定義は空文字へ落とす（エラーにしない）。
    **クォートは足さない** — 空白を含むパスの引用は利用者の責任（`&&` でつないだ
    複合コマンドを書けるようにするための意図的な選択。schema にも明記）。"""
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
    """実行計画を組み立てる（決定的・副作用なし）。dashboard のプレビューと同一結果。
    各要素は skip 理由を持つため、呼び出し側は除外された行もログに残せる。"""
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
            # 単発系にはセッションが無い。黙って落とさず理由を残す。
            entry["skip"] = "no-session"
        elif mode == "process":
            if spent >= budget:
                entry["skip"] = "budget"
            else:
                # 残り予算を超える timeout は残りへ切り詰める（合計の有界化）。
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
        cwd = None  # 消えている cwd で落とさない（既定の場所で走らせる）
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


def run_session_commands(ctx: "dict | None", send_chat=None, modes=("process", "chat")) -> bool:
    """セッション開始コマンドを配列順に逐次実行する。

    戻り値は「セッションを開始してよいか」。on_error='fail' のコマンドが失敗したときだけ
    False を返し、呼び出し側はセッションの生成を中止する。それ以外はすべて True
    （不在・破損・無効・個々の失敗はフェイルセーフで続行）。

    send_chat は chat モードの送信関数（テキスト 1 引数）。None なら chat はスキップする。
    modes は今回の呼び出しで扱うモード。常駐系は process をペイン生成の前に、chat を
    生成後（プロンプト待機を通過してから）に走らせるため、2 回に分けて呼ぶ。
    """
    global _SESSION_COMMANDS_REV_APPLIED
    try:
        data = _load_session_commands()
        entries = plan_session_commands(data, ctx)
    except Exception:  # noqa: BLE001 — 計画の失敗でセッション開始を止めない
        return True
    if not entries:
        return True
    rev = _session_commands_revision(data)
    for entry in entries:
        if entry["mode"] not in modes:
            continue
        if entry.get("skip"):
            log.info("セッション開始コマンド '%s' をスキップします（理由: %s）。", entry["id"], entry["skip"])
            continue
        if entry["mode"] == "chat":
            if send_chat is None:
                continue
            try:
                send_chat(entry["run"])
            except Exception as exc:  # noqa: BLE001
                log.warning("セッション開始コマンド '%s' の送信に失敗しました: %s", entry["id"], exc)
            continue
        log.info("セッション開始コマンド '%s' を実行します: %s", entry["id"], entry["run"])
        ok, reason = _run_session_process_command(entry)
        if ok:
            continue
        if entry["on_error"] == "fail":
            log.error(
                "セッション開始コマンド '%s' が失敗したためセッションを開始しません: %s",
                entry["id"], reason,
            )
            return False
        log.warning("セッション開始コマンド '%s' が失敗しました（続行します）: %s", entry["id"], reason)
    _SESSION_COMMANDS_REV_APPLIED = rev
    return True
