from __future__ import annotations
# prioritize.py — 元 agent-project.py の 2610-3159 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# 優先順位付け（正準ループ ①②）
# ---------------------------------------------------------------------------
def consumable_tasks(tasks: "list[Task]") -> "list[Task]":
    return [t for t in tasks if t.consumable()]


def task_deps(task: "Task") -> "list[str]":
    """`- after: T1, T2` の依存 ID 群（カンマ/空白区切り）。無ければ空。"""
    raw = task.get("after", "")
    return [d for d in re.split(r"[,\s]+", raw.strip()) if d]


def unmet_deps(task: "Task", tasks: "list[Task]") -> "list[str]":
    """`after` の依存のうち、まだ未完（backlog に done 以外で残っている）ID。done は退避済みなので満たし。"""
    pending = {t.id for t in tasks if t.norm_status() != "done"}
    return [d for d in task_deps(task) if d in pending]


def ready_after_deps(tasks: "list[Task]") -> "list[Task]":
    """消化対象（ready）のうち、依存が満たされたものだけ（DAG 順序）。"""
    return [t for t in consumable_tasks(tasks) if not unmet_deps(t, tasks)]


def _extract_id_array(text: str) -> "list[str] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return [str(x) for x in arr] if isinstance(arr, list) else None


def _extract_json_obj(text: str) -> "dict | None":
    """応答から最初の JSON オブジェクト {...} を取り出す（説明文が混じっても拾う）。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


# LLM 実行に使うエージェント CLI とタイムアウト（設定 agent_cli / agent_timeout）。
# rank_agent 等の free 関数は args を持たないため、build_config が設定値をここへ確定する
# （agent-flow の _configure_thresholds と同じ流儀）。
_AGENT_CLI: str = "kiro"
_AGENT_TIMEOUT: float = 300.0
# 処理（purpose）毎の上書き（設定 agents: の正規化済みマップ。build_config が確定する）。
# 例: {"plan": {"agent_cli": "claude", "model": "opus"}, "assess": {"model": "haiku"}}
_AGENT_OVERRIDES: "dict[str, dict]" = {}
# エージェントを使用する処理の一覧（設定 agents: のキー）。ここに無いキーは無視される。
AGENT_PURPOSES = ("plan", "review", "prioritize", "route", "adjudicate", "verify",
                  "distill", "assess", "repo_map", "doctor")
# agent_cli の設定値 → doctor が PATH 確認すべき実行ファイル名（未知の agent_cli はそのまま使う）。
_AGENT_CLI_BINARIES = {"kiro": "kiro-cli", "claude": "claude", "copilot": "copilot",
                       "codex": "codex"}


def _normalize_agent_overrides(raw) -> "dict[str, dict]":
    """設定 agents:（処理毎の agent_cli/model 上書き）を正規化する。未知の処理キー・
    不正な値は黙って落とす（設定ミスでループを殺さない。有効キーは AGENT_PURPOSES）。"""
    out: "dict[str, dict]" = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in AGENT_PURPOSES or not isinstance(v, dict):
            continue
        ov: dict = {}
        if v.get("agent_cli"):
            ov["agent_cli"] = str(v["agent_cli"]).strip().lower()
        if v.get("model"):
            ov["model"] = str(v["model"]).strip()
        if ov:
            out[key] = ov
    return out


def _agent_for(purpose: str) -> "tuple[str, str | None]":
    """処理（purpose）の実効エージェント。(agent_cli, model 上書き) を返す。
    agent-control（管理面の横断上書き）＞ 設定 agents: の該当キー ＞ グローバル agent_cli。
    soft/縮退中は control の degraded を重ねる（model 上書きは無ければ None＝呼び出し値）。"""
    ov = _AGENT_OVERRIDES.get(purpose) or {}
    cli = str(ov.get("agent_cli") or _AGENT_CLI).lower()
    model = ov.get("model") or None
    c_cli, c_model = _control_override(purpose)
    if c_cli:
        cli = c_cli.lower()
    if c_model:
        model = c_model
    nb = _node_budget_state()
    if nb and (nb.get("soft") or (nb.get("exceeded") and nb.get("on_exhausted") == "degrade")):
        d_cli, d_model = _control_degraded()
        if d_cli:
            cli = d_cli.lower()
        if d_model:
            model = d_model
    return cli, model


def _agent_cmd(cli: str, model: "str | None",
               prompt: str) -> "tuple[list[str], str | None, str | None]":
    """エージェント CLI 1 回分の (argv, stdin テキスト, 最終応答ファイル) を組み立てる
    （実行はしない・決定的）。最終応答ファイルは codex のみ使う（stdout がイベントログのため）。"""
    if cli == "claude":
        # Claude Code ヘッドレス。プロンプトは stdin 渡し（ARG_MAX に当たらない）。
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        return cmd, prompt, None
    if cli == "copilot":
        # GitHub Copilot CLI ヘッドレス。-s で応答本文のみ、--allow-all-tools は
        # 非対話モードの必須フラグ（--allow-all-paths はファイル読み書きの許可）。
        cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        if model:
            cmd += ["--model", model]
        return cmd + ["-p", prompt], None, None
    if cli == "codex":
        # OpenAI Codex CLI ヘッドレス（codex exec）。プロンプトは stdin 渡し（"-"）。
        # stdout には実行イベントログが混ざるため、最終応答は --output-last-message の
        # ファイルから読む。--skip-git-repo-check は git リポジトリ外でも動かすため。
        fd, out_file = tempfile.mkstemp(prefix="agent-project-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        return cmd + ["-"], prompt, out_file
    if cli in ("kiro", ""):
        cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
        if model:
            cmd += ["--model", model]
        return cmd + [prompt], None, None
    # 組み込み以外 → プラグイン定義（agents/<name>.json・契約は schemas/agent-cli.schema.json）。
    # 以前は未知の agent_cli が黙って kiro-cli に落ちていた（設定ミスに気づけない罠）。
    # 定義が無ければ明示エラーにする。
    plug = load_agent_plugin(cli)
    if plug is None:
        raise RuntimeError(
            f"未知の agent_cli です: {cli!r}（組み込みは kiro/claude/copilot/codex。"
            f"それ以外は agents/{cli}.json 定義が必要です — 契約: schemas/agent-cli.schema.json・"
            f"探索順: $KIRO_AGENTS_DIR → <root>/agents → ~/.agent/agents → ~/.kiro/agents）")
    return _plugin_agent_cmd(plug, model, prompt)


# --- エージェント CLI プラグイン（データ契約: schemas/agent-cli.schema.json） -----------------
# 組み込み（kiro/claude/copilot/codex）以外の CLI（cursor / ollama / hermes …）を、
# 定義ファイルだけで差し込む公式の口。agent-flow も同じ契約を読む（結合はデータ契約のみ・
# ローダは各ツールが自前で持つ = ツール間のコード依存を作らない）。
_AGENT_PLUGIN_CACHE: "dict[str, dict | None]" = {}


def _agent_plugin_dirs() -> "list[Path]":
    dirs: "list[Path]" = []
    envd = os.environ.get("KIRO_AGENTS_DIR")
    if envd:
        dirs.append(Path(envd).expanduser())
    dirs.append(Path.cwd() / "agents")            # プロジェクトルート（run は cwd=root で動く）
    dirs.append(Path.home() / ".agent" / "agents")
    dirs.append(Path.home() / ".kiro" / "agents")
    return dirs


def _normalize_agent_plugin(name: str, raw: dict, path: Path) -> dict:
    cmd = raw.get("command")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: command は文字列配列が必須です")
    output = str(raw.get("output", "stdout"))
    if output == "file" and not any("{output_file}" in c for c in cmd):
        raise RuntimeError(f"エージェント定義 {path}: output=file には command 中の "
                           "{output_file} プレースホルダが必要です")
    errors = []
    for e in (raw.get("errors") or []):
        try:
            errors.append((str(e.get("class", "env")),
                           re.compile(str(e.get("match", "")), re.I),
                           str(e.get("hint", ""))))
        except re.error as ex:
            raise RuntimeError(f"エージェント定義 {path}: errors.match が正規表現として不正です: {ex}")
    return {"name": name, "command": list(cmd),
            "prompt_via": str(raw.get("prompt_via", "stdin")),
            "prompt_flag": raw.get("prompt_flag"),
            "model_flag": raw.get("model_flag"),
            "default_model": raw.get("default_model"),
            "output": output, "env": dict(raw.get("env") or {}),
            "timeout": raw.get("timeout"),
            "empty_output_is_error": bool(raw.get("empty_output_is_error", True)),
            "errors": errors, "path": str(path)}


def load_agent_plugin(name: str) -> "dict | None":
    """agents/<name>.json を探索順に読み、正規化して返す（無ければ None・プロセス内キャッシュ）。
    壊れた定義は黙って無視せず RuntimeError（設定ミスの静かな握り潰しを作らない）。"""
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _AGENT_PLUGIN_CACHE:
        return _AGENT_PLUGIN_CACHE[key]
    spec = None
    for d in _agent_plugin_dirs():
        p = d / f"{key}.json"
        try:
            if not p.is_file():
                continue
            raw = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise RuntimeError(f"エージェント定義 {p} を JSON として読めません: {e}")
        except OSError:
            continue
        spec = _normalize_agent_plugin(key, raw, p)
        break
    _AGENT_PLUGIN_CACHE[key] = spec
    return spec


def _plugin_agent_cmd(plug: dict, model: "str | None",
                      prompt: str) -> "tuple[list[str], str | None, str | None]":
    """プラグイン定義から (argv, stdin テキスト, 最終応答ファイル) を組み立てる
    （_agent_cmd と同じ契約・決定的）。"""
    model = model or plug.get("default_model") or None
    out_file = None
    cmd: "list[str]" = []
    used_model = False
    for part in plug["command"]:
        if "{output_file}" in part:
            if out_file is None:
                fd, out_file = tempfile.mkstemp(prefix=f"agent-project-agent-{plug['name']}-", suffix=".txt")
                os.close(fd)
            part = part.replace("{output_file}", out_file)
        if "{model}" in part:
            if not model:
                continue                          # モデル未指定 → トークンごと省く
            part = part.replace("{model}", model)
            used_model = True
        cmd.append(part)
    if model and not used_model and plug.get("model_flag"):
        cmd += [str(plug["model_flag"]), model]
    if plug["prompt_via"] == "argv":
        if plug.get("prompt_flag"):
            cmd += [str(plug["prompt_flag"]), prompt]
        else:
            cmd.append(prompt)
        return cmd, None, out_file
    return cmd, prompt, out_file


def _plugin_error_patterns() -> "tuple":
    """読み込み済みプラグインの errors 規則（CLI 固有のトリアージ知識）をまとめて返す。"""
    out = []
    for spec in _AGENT_PLUGIN_CACHE.values():
        if spec:
            out.extend(spec.get("errors") or [])
    return tuple(out)


# --- 失敗トリアージ（決定的） -------------------------------------------------------------
# エラー本文から「誰が直すか」を分類する。分類はメッセージ先頭の機械可読タグ
# [agent-error:<class>] として運び、agent-flow の run 打ち切り・agent-project のリトライ節約・
# viewer の行動提示が同じ判定を共有する（CLI 固有規則はプラグイン定義の errors）。
#   control  : 管理設定による停止 — 明示的に run へ戻すまで継続する
#   quota    : 利用上限 — 時間をおけば回復する（タスクのリトライを焼かない）
#   auth     : 認証切れ — 人が環境を直すまで全タスク共倒れ（即座に人へ）
#   env      : 実行環境の問題（CLI 不在・モデル不正 等）— 人が環境を直す
#   transient: 一時的（タイムアウト・接続断）— 通常リトライで解ける
#   （どれにも当たらなければ「内容の問題」= 従来どおりタスク単位の retry / 裁定）
AGENT_ERROR_ENV_CLASSES = ("control", "quota", "auth", "env")
_AGENT_ERROR_TAG_RE = re.compile(r"\[agent-error:(control|quota|auth|env|transient)\]")
_AGENT_ERROR_PATTERNS = (
    ("control", re.compile(r"\[agent-control\]", re.I),
     "管理設定で実行が停止されています（dashboard で実行を許可してください）"),
    ("quota", re.compile(r"usage limit|quota exceeded|rate.?limit|too many requests", re.I),
     "利用上限に達しています（時間をおくか、プラン・クレジットを見直してください）"),
    ("auth", re.compile(r"AccessDenied|Unauthorized|authentication failed|not authenticated"
                        r"|SendMessageError|please (re)?login", re.I),
     "認証に失敗しています（再ログインが必要です）"),
    ("env", re.compile(r"issue with the selected model|invalid model"
                       r"|model .{0,40}(not found|does not exist)|may not have access to it"
                       r"|command not found|No such file or directory", re.I),
     "実行環境の問題です（モデル名・CLI の導入・PATH を確認してください）"),
    ("transient", re.compile(r"timed? ?out|connection (reset|refused|closed)|ECONNRESET"
                             r"|ETIMEDOUT|temporarily unavailable|service unavailable|overloaded",
                             re.I),
     "一時的なエラーです（自動でやり直します）"),
)


def classify_agent_failure(blob: str) -> "tuple[str, str] | None":
    """エラー本文を (class, hint) に分類する（該当なしは None＝内容の問題）。
    既に [agent-error:] タグ付き（agent-flow 経由）ならそれが正。プラグイン定義の
    errors（CLI 固有知識）を汎用パターンより先に評価する。"""
    text = str(blob or "")
    m = _AGENT_ERROR_TAG_RE.search(text)
    if m:
        hint = next((h for c, _, h in _AGENT_ERROR_PATTERNS if c == m.group(1)), "")
        return m.group(1), hint
    for cls, pat, hint in _plugin_error_patterns() + _AGENT_ERROR_PATTERNS:
        if pat.search(text):
            return cls, hint
    return None


def _agent_failure(cli: str, rc: int, out: str, err: str) -> str:
    """エージェント CLI の失敗を、人が原因に辿り着ける文言にする。

    CLI は起動バナー（workdir / model / プロンプト全文）を stderr へ流す。先頭だけを切り取ると
    肝心のエラーがバナーに埋もれて消える — 実際 codex の「利用上限に達した」を丸ごと取り逃し、
    全ノードが理由不明の failed になった。エラーは末尾に出るので末尾を拾い、分類（トリアージ）は
    機械可読タグとして先頭に載せる。"""
    blob = f"{out or ''}\n{err or ''}"
    triage = classify_agent_failure(blob)
    head = f"{cli} 失敗 (rc={rc})"
    if triage:
        cls, hint = triage
        head = f"[agent-error:{cls}] {head}" + (f": {hint}" if hint else "")
    tail = (err or out or "").strip()
    return f"{head}\n{tail[-500:]}" if tail else head


# --- ノード予算 v2（node-budget 契約: schemas/node-budget.schema.json） --------------------
# ノード（マシン）単位の共有台帳。定常業務（kiro-loop）・agent-project・agent-flow・
# agent-amigos が同じ台帳（$AGENT_BUDGET_DIR、既定 ~/.agent/budget/）に記帳し、合計が上限
# （0 = 無制限）を超えたら新規の LLM 実行を控える。v2 で一次単位をトークンへ拡張（時間上限は
# v1 互換で AND）。台帳には実測のみ（実測秒＋実測できたトークン）を書き、未報告行は rates で
# 読み出し時に推定する。配分・較正の知能は管理面（dashboard）にあり、エンジンは単純比較のみ。
# 読み書きは各ツールが自前で持つ（データ契約のみ・コード共有なし）。
_NODE_BUDGET_WORKLOAD = "project"
_NODE_BUDGET_TOOL = "agent-project"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _node_budget_dir() -> str:
    return os.path.abspath(os.path.expanduser(
        os.environ.get("AGENT_BUDGET_DIR", os.path.join("~", ".agent", "budget"))))


def _node_budget_rate(cfg: dict, cli: str, model: str) -> float:
    """トークン未報告行の推定レート（tokens/秒）。解決順 cli:model → cli → default。"""
    rates = cfg.get("rates") or {}
    per = rates.get("per_cli") or {}
    for key in (f"{cli}:{model}" if model else None, cli or None):
        if key and per.get(key):
            try:
                return float(per[key])
            except (TypeError, ValueError):
                pass
    try:
        return float(rates.get("default_tokens_per_second") or 0)
    except (TypeError, ValueError):
        return 0.0


def _row_tokens(rec: dict, cfg: dict) -> float:
    """1 記帳のトークン消費。実測（tokens_in+tokens_out）があればその値、無ければ秒 × レート。"""
    ti, to = rec.get("tokens_in"), rec.get("tokens_out")
    if ti is not None or to is not None:
        try:
            return float(ti or 0) + float(to or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        sec = float(rec.get("seconds") or 0)
    except (TypeError, ValueError):
        return 0.0
    if sec <= 0:
        return 0.0
    return sec * _node_budget_rate(cfg, str(rec.get("agent_cli") or ""), str(rec.get("model") or ""))


def _node_budget_state() -> "dict | None":
    """ノード予算の消費状況。設定が無い/上限が全て 0 なら None（= 無制限・チェック不要）。
    exceeded は時間上限・トークン上限（合計 or 自ワークロードの実効上限）のいずれか到達。
    soft は縮退開始（soft_ratio 到達・未超過）。on_exhausted は超過時の方針。"""
    base = _node_budget_dir()
    try:
        with open(os.path.join(base, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    limit_min = float(cfg.get("execution_minutes") or 0)
    wl_limit_min = float((cfg.get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or 0)
    token_limit = float(cfg.get("tokens") or 0)
    alloc = cfg.get("allocation") or {}
    wl_alloc = (alloc.get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {}
    computed = ((cfg.get("computed") or {}).get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {}
    eff_wl_tokens = float(computed.get("tokens") or 0) or float(wl_alloc.get("max_tokens") or 0)
    on_exhausted = str(wl_alloc.get("on_exhausted") or "pause")
    try:
        soft_ratio = float(alloc.get("soft_ratio") or 0.9)
    except (TypeError, ValueError):
        soft_ratio = 0.9
    if limit_min <= 0 and wl_limit_min <= 0 and token_limit <= 0 and eff_wl_tokens <= 0:
        return None
    period = str(cfg.get("period") or "day")
    prefix = (time.strftime("%Y%m%d", time.gmtime()) if period == "day"
              else time.strftime("%Y%m", time.gmtime()) if period == "month" else "")
    total = wl_total = tok_total = wl_tok = 0.0
    led = os.path.join(base, "ledger")
    try:
        names = sorted(n for n in os.listdir(led)
                       if n.endswith(".jsonl") and n.startswith(prefix))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(led, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        sec = float(rec.get("seconds") or 0)
                    except (ValueError, TypeError):
                        continue
                    toks = _row_tokens(rec, cfg)
                    is_wl = rec.get("workload") == _NODE_BUDGET_WORKLOAD
                    if sec > 0:
                        total += sec
                        if is_wl:
                            wl_total += sec
                    if toks > 0:
                        tok_total += toks
                        if is_wl:
                            wl_tok += toks
        except OSError:
            continue
    time_exceeded = ((limit_min > 0 and total >= limit_min * 60)
                     or (wl_limit_min > 0 and wl_total >= wl_limit_min * 60))
    token_exceeded = ((token_limit > 0 and tok_total >= token_limit)
                      or (eff_wl_tokens > 0 and wl_tok >= eff_wl_tokens))
    exceeded = bool(time_exceeded or token_exceeded)
    soft_cap = eff_wl_tokens or token_limit
    soft_spent = wl_tok if eff_wl_tokens else tok_total
    soft = bool(soft_cap > 0 and soft_spent >= soft_ratio * soft_cap and not exceeded)
    return {"exceeded": exceeded, "soft": soft, "on_exhausted": on_exhausted,
            "spent_min": total / 60, "limit_min": limit_min,
            "spent_tokens": tok_total, "token_limit": token_limit, "period": period}


def _node_budget_record(seconds: float, ref: str = "", agent_cli: str = "",
                        model: str = "", tokens_in=None, tokens_out=None, usd=None) -> None:
    """台帳へ 1 記帳を追記する（O_APPEND — 複数プロセスの同時追記でも行は壊れない）。
    tokens_* は実測できたときだけ渡す（推定値は書かない）。agent_cli / model は帰属。"""
    if seconds <= 0 and not tokens_in and not tokens_out:
        return
    d = os.path.join(_node_budget_dir(), "ledger")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"ts": _utc_iso(), "workload": _NODE_BUDGET_WORKLOAD,
               "tool": _NODE_BUDGET_TOOL, "seconds": round(float(seconds), 3), "ref": ref}
        if agent_cli:
            rec["agent_cli"] = str(agent_cli)
        if model:
            rec["model"] = str(model)
        if tokens_in is not None:
            rec["tokens_in"] = float(tokens_in)
        if tokens_out is not None:
            rec["tokens_out"] = float(tokens_out)
        if usd is not None:
            rec["usd"] = float(usd)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fd = os.open(os.path.join(d, time.strftime("%Y%m%d", time.gmtime()) + ".jsonl"),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass    # 記帳失敗で実行を止めない（台帳は best-effort、上限は次の実行前チェックで効く）


# --- agent-control（管理面→エンジンの宣言的オーケストレーション契約） ----------------------
# schemas/agent-control.schema.json。$AGENT_CONTROL_DIR（既定 ~/.agent/control/）の control.json
# に管理面が「望ましい状態」を書き、各エンジンが mtime を見て pull で適用する（push 型 IPC なし）。
# 優先順位 control > CLI 引数 > 設定ファイル > 組み込み既定。適用状況は status/<tool>-<pid>.json へ。
_CONTROL_CACHE = {"mtime": None, "data": {}}


def _control_dir() -> str:
    return os.path.abspath(os.path.expanduser(
        os.environ.get("AGENT_CONTROL_DIR", os.path.join("~", ".agent", "control"))))


def _load_control() -> dict:
    """control.json を mtime キャッシュ付きで読む。無ければ {}。"""
    path = os.path.join(_control_dir(), "control.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _CONTROL_CACHE["mtime"], _CONTROL_CACHE["data"] = None, {}
        return {}
    if _CONTROL_CACHE["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                _CONTROL_CACHE["data"] = json.load(f) or {}
        except (OSError, ValueError):
            _CONTROL_CACHE["data"] = {}
        _CONTROL_CACHE["mtime"] = mtime
    return _CONTROL_CACHE["data"]


def _control_workload() -> dict:
    return dict((_load_control().get("workloads") or {}).get(_NODE_BUDGET_WORKLOAD) or {})


def _control_lifecycle() -> str:
    """このワークロードの望ましい lifecycle（run|pause|stop）。既定 run。"""
    return str(_control_workload().get("lifecycle") or "run")


def _control_override(key: str = "") -> "tuple[str | None, str | None]":
    """(agent_cli, model) の上書き。解決 workloads[wl].agents[key] > workloads[wl] > defaults。"""
    ctl = _load_control()
    wl = _control_workload()
    agents = wl.get("agents") or {}
    layers = ([agents.get(key) or {}] if key else []) + [wl, ctl.get("defaults") or {}]
    cli = model = None
    for layer in layers:
        if cli is None and layer.get("agent_cli"):
            cli = str(layer.get("agent_cli"))
        if model is None and layer.get("model"):
            model = str(layer.get("model"))
    return cli, model


def _control_degraded() -> "tuple[str | None, str | None]":
    d = _control_workload().get("degraded") or {}
    return (str(d["agent_cli"]) if d.get("agent_cli") else None,
            str(d["model"]) if d.get("model") else None)


def _write_status(effective_cli: str = "", effective_model: str = "", lifecycle: str = "run",
                  budget: "dict | None" = None, fresh_after_sec: int = 120) -> None:
    """status/<tool>-<pid>.json へ適用状況ハートビートを原子書換する（best-effort）。"""
    ctl = _load_control()
    d = os.path.join(_control_dir(), "status")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"tool": _NODE_BUDGET_TOOL, "workload": _NODE_BUDGET_WORKLOAD,
               "pid": os.getpid(), "lifecycle": lifecycle,
               "effective": {"agent_cli": effective_cli or None, "model": effective_model or None},
               "fresh_after_sec": fresh_after_sec, "ts": _utc_iso()}
        if ctl.get("revision") is not None:
            rec["revision_applied"] = ctl.get("revision")
        if budget is not None:
            rec["budget"] = {"exceeded": bool(budget.get("exceeded")),
                             "soft": bool(budget.get("soft"))}
        target = os.path.join(d, f"{_NODE_BUDGET_TOOL}-{os.getpid()}.json")
        tmp = target + f".tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass


def _run_agent_cli(prompt: str, model: "str | None", purpose: str = "") -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    このツールの LLM 呼び出し（分解・優先順位・裁定・ルーティング等）はすべてここを通る。
    purpose（AGENT_PURPOSES のいずれか）を渡すと、設定 agents: の処理毎上書き
    （agent_cli / model）が効く。model は 上書き ＞ 呼び出し値（通常グローバル model）。"""
    # agent-control: このワークロードが pause/stop 指定なら新規実行を控える（環境要因として運ぶ）。
    lifecycle = _control_lifecycle()
    if lifecycle in ("pause", "stop"):
        _write_status(lifecycle=lifecycle)
        raise RuntimeError(
            f"[agent-error:control] [agent-control] このワークロード（project）は管理面により "
            f"lifecycle={lifecycle} 指定です。dashboard のオーケストレーションタブで run に戻して"
            "ください")
    nb = _node_budget_state()
    # 超過かつ on_exhausted != degrade なら控える。degrade は縮退指定で継続（_agent_for が適用）。
    if nb and nb["exceeded"] and nb.get("on_exhausted") != "degrade":
        # ノード予算超過は環境要因（時間経過か人の上限変更で回復）— quota 分類で全層に運ぶ
        # （リトライ・裁定を焼かず needs へ。環境を直せば続きから、の既存フローに乗る）。
        _write_status(lifecycle=lifecycle, budget=nb)
        unit = ("トークン" if nb.get("token_limit") else "実行時間")
        raise RuntimeError(
            f"[agent-error:quota] [node-budget] このノードの{unit}予算を超過しています"
            f"（{nb['spent_min']:.1f}分/{nb['limit_min']:.0f}分・"
            f"{nb['spent_tokens']:.0f}tok/{nb['token_limit']:.0f}tok・period={nb['period']}）。"
            "上限を上げる（dashboard のオーケストレーションタブ / agent-amigos budget node）か"
            "期間の更新を待ってください")
    cli, model_ov = _agent_for(purpose)
    _write_status(effective_cli=cli, effective_model=(model_ov or model or ""),
                  lifecycle=lifecycle, budget=nb)
    cmd, stdin_text, out_file = _agent_cmd(cli, model_ov or model, prompt)
    plug = _AGENT_PLUGIN_CACHE.get(cli)   # _agent_cmd がロード済み（組み込み CLI は None）
    # 発生源で色を抑止（NO_COLOR/TERM=dumb）。残った ANSI は strip_ansi で除去する二段構え。
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", **((plug or {}).get("env") or {})}
    timeout = (plug or {}).get("timeout") or (_AGENT_TIMEOUT if _AGENT_TIMEOUT > 0 else None)
    try:
        t0 = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin_text,
                              timeout=timeout, env=env)
        # トークン実測: エージェントが `@cost tokens=… usd=…` を吐けば台帳へ帰属付きで記帳する。
        _cost_tokens, _cost_usd = parse_cost(proc.stdout or "")
        _node_budget_record(time.monotonic() - t0, ref=purpose or "agent",
                            agent_cli=cli, model=(model_ov or model or ""),
                            tokens_out=(_cost_tokens or None) if _cost_tokens else None,
                            usd=(_cost_usd or None) if _cost_usd else None)
        if proc.returncode != 0:
            raise RuntimeError(_agent_failure(cmd[0], proc.returncode, proc.stdout, proc.stderr))
        text = strip_ansi(proc.stdout).strip()
        if out_file:   # codex 等: 最終応答ファイルが取れればそれを正とする（stdout はイベントログ）
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
        if not text and plug is not None and not plug.get("empty_output_is_error", True):
            return ""
        if not text:
            # rc=0 でも本文が空で返る CLI がある（kiro-cli は AWS 認証が切れるとバナーだけ出して
            # rc=0 で終わる）。空を成功として扱うと、verify 合成も分解も「静かに失敗」して
            # 決定的フォールバックへ落ちる＝LLM を呼べていないのに動いて見える。失敗にする。
            raise RuntimeError(_agent_failure(cmd[0], 0, proc.stdout, proc.stderr)
                               .replace("失敗 (rc=0)", "が空の応答を返しました (rc=0)"))
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)


def rank_agent(ready: "list[Task]", model: "str | None", agent_run=None) -> "list[Task] | None":
    agent_run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="prioritize"))
    if len(ready) <= 1:
        return list(ready)     # 0/1 件は並べ替えの余地が無い＝LLM を呼ばない（コスト・レイテンシ削減）
    listing = "\n".join(
        f"- {t.id}: {t.title}（priority={t.priority}, source={t.source}）" for t in ready)
    prompt = ("あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係に加え、"
              "**外部で付与された priority（大きいほど高優先）も加味**して優先順位の高い順に並べ替え、"
              "**タスクID の JSON 配列だけ**を出力してください（説明文なし）。\n\nタスク:\n" + listing)
    try:
        order_ids = _extract_id_array(agent_run(prompt, model))
    except Exception:  # noqa: BLE001
        return None
    if not order_ids:
        return None
    by_id = {t.id: t for t in ready}
    ordered = [by_id[i] for i in order_ids if i in by_id]
    seen = {t.id for t in ordered}
    ordered += [t for t in ready if t.id not in seen]
    return ordered


def _tail_matching(path: "Path | None", needle: str, limit: int) -> "list[str]":
    """ファイルから needle を含む行を末尾 limit 件返す（best-effort・無ければ空）。"""
    if not path or not path.exists():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if needle in ln]
    except OSError:
        return []
    return lines[-limit:]


def adjudication_context(cfg: "Config", task: Task,
                         journal_lines: int = 8, decision_chars: int = 1200) -> str:
    """裁定の判断材料を decisions/journal/task から決定的に集める（LLM 不要・有界）。
    『過去にどう試して何を人が判断したか』を門番へ渡し、的外れな requeue や再エスカレを減らす。"""
    parts: list[str] = []
    jl = _tail_matching(cfg.journal, task.id, journal_lines)
    if jl:
        parts.append("これまでのサイクル履歴(journal):\n" + "\n".join(jl))
    dp = decision_path(cfg, task.id)
    if dp.exists():
        try:
            txt = dp.read_text(encoding="utf-8").strip()
        except OSError:
            txt = ""
        if txt:
            if len(txt) > decision_chars:        # 直近の判断が重要なので末尾を残す
                txt = "…\n" + txt[-decision_chars:]
            parts.append("過去の決定記録(decisions):\n" + txt)
    fb = task.feedback()
    if fb:
        parts.append("適用済みの直近フィードバック: " + fb)
    note = next((v for k, v in task.extra if k == "note"), None)
    if note:
        parts.append("タスクのメモ(note): " + note)
    return "\n\n".join(parts)


def adjudicate_escalation(cfg: "Config", task: Task, reason: str,
                          agent_run=None) -> "tuple[str, str]":
    """needs（人の判断）に落とす直前の エージェント CLI 裁定ゲート。
    『ループ内で自律的に積み直して解けるか／人の判断が要るか』を判断させる。
    返り値: ("requeue", guidance) なら自律的に積み直す、("escalate", "") なら従来どおり人へ。
    判断不能・エラー・曖昧は **必ず escalate にフォールバック**（安全側＝人を飛ばさない）。"""
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="adjudicate"))
    ctx = adjudication_context(cfg, task)        # journal/decisions/feedback の文脈を渡す
    prompt = (
        "あなたは自律バックログ・ループの『人の判断を呼ぶ前の門番』です。次のタスクが検証(verify)に"
        "失敗し、通常なら人の判断待ち(needs)へ送られます。これを **ループ内で自律的に積み直して解決を試みる"
        "価値があるか** を判断してください。\n"
        "- requeue（積み直す）: 失敗が実装の不足・取り違え等で、明確な追加指示があれば次の試行で解けそうな場合。\n"
        "- escalate（人へ）: 要件が曖昧／意思決定や承認が要る／リスクが高い／同じ失敗の繰り返しで打開策が無い場合。\n"
        "**判断は厳しめに。少しでも人の意思決定が要るなら escalate。過去に同じ案件を積み直して解けていない"
        "なら escalate。**\n\n"
        f"タスクID: {task.id}\nタイトル: {task.title}\nverify: {task.verify}\n"
        f"これまでの試行回数(retries): {task.retries}\n失敗理由: {reason}\n\n"
        + (f"--- 参考文脈（既存の試行・判断の履歴）---\n{ctx}\n\n" if ctx else "")
        + '出力は次の JSON オブジェクトだけ（説明文なし）:\n'
        '{"decision": "requeue" | "escalate", "guidance": "requeue の場合のみ、次の試行への具体的な指示"}')
    try:
        obj = _extract_json_obj(run(prompt, cfg.model))
    except Exception:  # noqa: BLE001  エージェント CLI 不在・タイムアウト等は人へ
        return ("escalate", "")
    if not obj or obj.get("decision") != "requeue":
        return ("escalate", "")
    return ("requeue", str(obj.get("guidance", "")).strip())


# ---------------------------------------------------------------------------
# 投入時アセスメント（Spec Orchestrator の採点段）— c=複雑さ / r=リスク / a=曖昧さ（各1-3）
# ---------------------------------------------------------------------------
def _assess_heuristic(cfg: "Config", task: Task) -> dict:
    """エージェント不在・失敗・stub 時の決定的採点。材料はタスク定義と decisions/ の走査のみ。
    c: cohort（同種の繰り返し）は多対象＝3。r: 過去の回避判断（avoid）に類似＝3。
    a: 決定的 verify あり=1 / accept（自然言語）のみ=2 / どちらも無し=3。"""
    c = 3 if (task.get("cohort_items") or task.get("cohort")) else 1
    r = 3 if find_avoidance(cfg, task) else 1
    a = 1 if (task.verify or task.get("verify_template")) else (2 if task.get("accept") else 3)
    return {"c": c, "r": r, "a": a}


def _assess_prompt(task: Task) -> str:
    return (
        "あなたはタスクの事前アセスメント役です。以下のタスクを 3 軸で採点してください（各 1〜3 の整数）。\n"
        "- c=複雑さ: 関与するファイル・コンポーネント・手順の多さ（3=多岐にわたる）\n"
        "- r=リスク: 壊したときの影響の大きさ（認証・決済・データ移行・本番設定などは 3）\n"
        "- a=曖昧さ: 完了条件・やり方の不確かさ（verify が具体的なら 1）\n\n"
        f"タイトル: {task.title}\n"
        f"verify: {task.verify or '（未定義）'}\n"
        f"accept: {task.get('accept') or '（なし）'}\n"
        f"note: {task.get('note') or '（なし）'}\n"
        + "".join(f"{k}: {task.get(k)}\n"       # 誘導・レビュー記述があれば採点材料に足す（有るものだけ）
                  for k in ("why", "desc", "scope", "constraints") if task.get(k))
        + '\n出力は JSON オブジェクトのみ（説明文なし）: {"c": 1, "r": 1, "a": 1}')


def assess_task(cfg: "Config", task: Task, agent_run=None) -> "str | None":
    """投入時アセスメント。採点は情報であり、それ自体は実行可否・done 条件を変えない
    （読むのは plan-review 票・リスクダイジェスト・spec ルーティング）。知能は委譲し、
    失敗・stub は決定的ヒューリスティックへフォールバック。1 タスク 1 回（既存はスキップ）。"""
    if task.get("assess"):
        return task.get("assess")
    scores = None
    if cfg.executor != "stub":
        run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="assess"))
        try:
            obj = _extract_json_obj(run(_assess_prompt(task), cfg.model)) or {}
            got = {k: int(obj[k]) for k in ("c", "r", "a") if k in obj}
            if len(got) == 3:
                scores = {k: min(3, max(1, v)) for k, v in got.items()}
        except Exception:  # noqa: BLE001  エージェント不在・タイムアウト・非 JSON はヒューリスティックへ
            scores = None
    if scores is None:
        scores = _assess_heuristic(cfg, task)
    val = f"c={scores['c']} r={scores['r']} a={scores['a']}"
    task.extra.append(("assess", val))
    return val


# ---------------------------------------------------------------------------
# spec ルーティング（Spec Driven の前段・opt-in `spec_track`）
#   採点が spec_threshold に達した（または policy `spec:` に一致する）タスク T に、spec 作成
#   タスク（specs/<T>/ の spec.md/design.md/tasks.md・review: human）を前置する。人が spec を
#   承認して done になったら、tasks.md（enqueue --json 互換）を実装タスク群へ展開し、T は
#   after: 実装タスク群 の総合検証として最後に走る。すべて既存プリミティブ（after DAG・
#   plan_review/delivery_review・enqueue）の組み合わせで、S0–S7 のゲートは無改造。
# ---------------------------------------------------------------------------
def specs_root(cfg: "Config") -> Path:
    """spec 成果物の置き場（<workdir>/specs）。act の指示（相対パス）と verify（workdir 実行）が
    同じ場所を指すよう workdir 基準にする。既定 workdir=root なので状態リポジトリに載り、
    git 同期で viewer からも読める。"""
    return cfg.workdir / "specs"


def _assess_max(task: Task) -> int:
    """`- assess: c=N r=N a=N` の最大値（未採点は 0）。spec ルーティングのしきい値判定に使う。"""
    vals = re.findall(r"[cra]=(\d)", task.get("assess", "") or "")
    return max((int(v) for v in vals), default=0)


def _spec_verify(cfg: "Config", tid: str) -> str:
    """spec 作成タスクの決定的 verify: 3 ファイルが非空で、tasks.md が JSON タスク分解を含む。
    workdir 相対（specs_root と同じ基準）。"""
    rel = f"specs/{tid}"
    return (f"test -s {rel}/spec.md -a -s {rel}/design.md -a -s {rel}/tasks.md"
            f" && grep -q '\"title\"' {rel}/tasks.md")


def _spec_instructions(cfg: "Config", task: Task) -> str:
    """spec 作成タスク（`- spec_for:` 持ち）の act 要求文に足す作成指示。実装はさせない。"""
    tid = task.get("spec_for", "")
    rel = f"specs/{tid}"
    return (
        f"これは実装前の Spec 作成タスクです。{task.get('note') or ''}\n"
        f"作業ディレクトリ直下の {rel}/ に次の 3 ファイルを作成すること（コードの実装はしない）:\n"
        f"- {rel}/spec.md   … 要求仕様（背景・要求・受け入れ観点）\n"
        f"- {rel}/design.md … 設計（方針・影響範囲・代替案と選定理由）\n"
        f"- {rel}/tasks.md  … 実装タスク分解。次の形式の JSON 配列を含む Markdown:\n"
        f'  [{{"title": "…", "verify": "終了コード0で合否が決まるシェルコマンド",'
        f' "after": ["先行タスクの title"]}}]\n'
        f"  verify は『履歴』でなく『望む最終状態/差分』を見ること。after は任意（配列内の先行タスク）。\n"
        f'  各タスクには任意で "why"（必要な理由・1 文）・"out_of_scope"（やらないこと）・'
        f'"hints"（実装の手がかり）を付けてよい（実装ワーカーへの指示と人のレビュー材料になる）。')


def route_spec_tasks(cfg: "Config", tasks: "list[Task]", policy: "Policy") -> "list[Task]":
    """spec ルーティング（S0）。決定はタスクの `- route:` に記録して再ルーティングしない
    （人の `- route: direct` が採点に常に勝つ＝policy/明示 > エージェント）。ルーティングは
    「タスクを足す」方向のみで、done 条件・予算には触れない。作成した spec タスクを返す。"""
    if not cfg.spec_track:
        return []
    created: "list[Task]" = []
    for t in list(tasks):
        if t.norm_status() not in ("proposed", "ready", "inbox"):
            continue
        if t.get("route") or t.get("spec_for") or t.get("spec"):   # 決定済み・spec 系タスクは対象外
            continue
        forced = any(t.matches(p) for p in policy.spec)
        if not forced and _assess_max(t) < cfg.spec_threshold:
            continue
        spec_dict = {"id": f"{t.id}-spec", "title": f"Spec 作成: {t.title}",
                     "verify": _spec_verify(cfg, t.id), "review": "human",
                     "spec_for": t.id, "route": "direct", "source": "spec",
                     "priority": t.priority + 1,
                     "note": f"対象タスク {t.id}: {t.title}"
                             + (f"（最終 verify: {t.verify}）" if t.verify else "")}
        if t.get("charter"):
            spec_dict["charter"] = t.get("charter")
        s = enqueue_task(cfg, spec_dict)
        deps = task_deps(t) + [s.id]
        t.set("route", "spec")
        t.set("spec_task", s.id)
        t.set("after", ", ".join(deps))
        persist_task(cfg, t)
        created.append(s)
        why = "policy spec 一致" if forced else \
            f"採点 {t.get('assess')} が spec_threshold({cfg.spec_threshold}) 以上"
        append_decision(cfg, t.id, "auto",
                        context=f"{t.id}（{t.title}）を spec ルートへ",
                        action="spec-route", reason=why,
                        affects=f"{s.id} を前置（承認後 tasks.md を実装タスクへ展開）")
        append_journal(cfg.journal, f"spec ルート: {t.id} に {s.id} を前置（{why}）")
    return created


def expand_spec_tasks(cfg: "Config", tasks: "list[Task]") -> "list[Task]":
    """spec 前段が done（archive へ・承認済み）になったタスクの tasks.md を実装タスク群へ展開する。
    tasks.md は enqueue --json 互換の JSON 配列。展開後、元タスクの after を実装タスク群へ
    付け替え＝元タスクは自らの verify を持つ総合検証として最後に走る。JSON が無ければ展開なし
    （元タスクが spec を文脈注入されて自力実装する・安全側）。展開数は max_spawn の傘の下。"""
    if not cfg.spec_track:
        return []
    by_id = {t.id: t for t in tasks}
    created_all: "list[Task]" = []
    for t in list(tasks):
        if t.get("route") != "spec" or t.get("spec_expanded"):
            continue
        sid = t.get("spec_task", "")
        if not sid or sid in by_id:                    # spec 前段が未決着（backlog に現存）
            continue
        arch = cfg.archive_dir() / f"{sid}.md"
        try:
            adone = (arch.exists() and
                     parse_task(arch.read_text(encoding="utf-8"), sid).norm_status() == "done")
        except OSError:
            adone = False
        if not adone:
            continue                                   # 却下等は展開しない（再審査は既存機構が担う）
        tmd = specs_root(cfg) / t.id / "tasks.md"
        try:
            items = _extract_json_array(tmd.read_text(encoding="utf-8")) if tmd.exists() else None
        except OSError:
            items = None
        specs = [it for it in (items or []) if isinstance(it, dict)
                 and str(it.get("title", "")).strip()][: max(0, cfg.max_spawn)]
        if not specs:
            t.set("spec_expanded", "none")             # 展開なし＝元タスクが spec 文脈で自力実装
            persist_task(cfg, t)
            append_journal(cfg.journal, f"spec 展開なし（tasks.md に有効な JSON 無し）: {t.id}")
            continue
        pairs: "list[tuple[dict, Task]]" = []
        title_to_id: "dict[str, str]" = {}
        for it in specs:
            sp = {"title": str(it["title"]).strip(),
                  "verify": _strip_code(str(it.get("verify", "") or "").strip()),
                  "source": "spec", "spec": t.id, "route": "direct"}
            # tasks.md は「enqueue --json 互換」の契約なので、誘導・レビュー記述（why 等）も落とさない
            for k in ("accept", "verify_template", "note", "priority", *TASK_GUIDE_KEYS):
                if it.get(k) not in (None, "", []):
                    sp[k] = it[k]
            for k in ("charter", "workspace"):         # 成果の行き先・スコープは元タスクを引き継ぐ
                if t.get(k):
                    sp[k] = t.get(k)
            try:
                nt = enqueue_task(cfg, sp)
            except ValueError:
                continue
            pairs.append((it, nt))
            title_to_id[sp["title"]] = nt.id
        # 2 パス目: 配列内 after（先行タスクの title）を id へ決定的に解決（未知 title は落とす・循環は拒否）
        new_tasks = [nt for _, nt in pairs]
        for it, nt in pairs:
            deps = [title_to_id[w] for w in _coerce_titles(it.get("after"))
                    if w in title_to_id and title_to_id[w] != nt.id]
            if deps:
                nt.set("after", ", ".join(deps))
                if _after_introduces_cycle(new_tasks, nt):
                    nt.drop("after")
                persist_task(cfg, nt)
        impl_ids = [nt.id for nt in new_tasks]
        t.set("after", ", ".join(impl_ids))            # 元タスク＝総合検証として最後に走る
        t.set("spec_expanded", str(len(impl_ids)))
        persist_task(cfg, t)
        append_decision(cfg, t.id, "auto",
                        context=f"{t.id}（{t.title}）の spec 承認に伴う実装タスク展開",
                        action="spec-expand",
                        reason=f"specs/{t.id}/tasks.md から {len(impl_ids)} 件",
                        affects=f"{t.id} は総合検証（after: {', '.join(impl_ids)}）")
        append_journal(cfg.journal, f"spec 展開: {t.id} ← {len(impl_ids)} 件（{sid} 承認）")
        created_all.extend(new_tasks)
    return created_all


def spec_context(cfg: "Config", task: Task, limit: int = 1200) -> str:
    """act 要求文へ注入する spec 文脈（spec.md/design.md・有界）。対象は spec 展開で生まれた
    実装タスク（`- spec:`）と、展開後の総合検証タスク（route: spec）。spec 作成タスク自身は対象外。"""
    tid = task.get("spec") or ("" if task.get("spec_for")
                               else (task.id if task.get("route") == "spec" else ""))
    if not tid:
        return ""
    parts: "list[str]" = []
    for name in ("spec.md", "design.md"):
        p = specs_root(cfg) / tid / name
        try:
            txt = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            txt = ""
        if txt:
            parts.append(f"--- specs/{tid}/{name} ---\n{txt[:limit]}")
    return "\n".join(parts)


def apply_policy_order(ordered: "list[Task]", policy: Policy) -> "list[Task]":
    def hit(t, pats):
        return any(t.matches(p) for p in pats)
    pinned = [t for t in ordered if hit(t, policy.pin)]
    deferred = [t for t in ordered if not hit(t, policy.pin) and hit(t, policy.defer)]
    middle = [t for t in ordered if t not in pinned and t not in deferred]
    return pinned + middle + deferred


def by_priority_then_age(ready: "list[Task]") -> "list[Task]":
    """優先度降順、同値は最古優先（ready は mtime 昇順で渡される＝安定ソートで age が効く）。"""
    return sorted(ready, key=lambda t: -t.priority)


def prioritize(tasks, policy, planner, model=None, ranker=None) -> "list[Task]":
    """planner=none: priority＋古さ。planner=agent: エージェント委譲（priority も加味）。policy が最終上書き。"""
    ready = ready_after_deps(tasks)  # mtime 昇順（最古優先）。依存(after)未達は除外
    # 0/1 件は並べ替えの余地が無く順序が自明＝planner を問わず LLM 優先順位付けを呼ばない
    # （エージェント CLI 起動のコスト・レイテンシを丸ごと省く）。policy（pin/defer）は後段で必ず効く。
    if planner == "none" or len(ready) <= 1:
        base = by_priority_then_age(ready)
    else:  # agent（エージェント委譲の順位付け。失敗時は priority＋古さにフォールバック）
        rank = (ranker or rank_agent)(ready, model)
        base = rank if rank is not None else by_priority_then_age(ready)
    return apply_policy_order(base, policy)


# ---------------------------------------------------------------------------
# triage（inbox→ready 昇格・policy deny の適用）
# ---------------------------------------------------------------------------
def triage(tasks, policy, plan_review: bool = False) -> "list[tuple[Task, str]]":
    transitions = []
    for t in tasks:
        st = t.norm_status()
        if st == "inbox" and has_verify_plan(t):   # verify か、用意できる材料(accept/verify_template)があれば昇格
            # 実行前レビュー時は ready でなく proposed へ（人の承認で初めて実行可能になる）
            t.status = "proposed" if plan_review else "ready"
            st = t.status
        if st in CONSUMABLE and any(t.matches(p) for p in policy.deny):
            t.status = "blocked"
            transitions.append((t, "policy:deny（人の判断待ち）"))
    return transitions


# ---------------------------------------------------------------------------
