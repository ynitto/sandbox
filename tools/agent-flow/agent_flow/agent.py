from __future__ import annotations
# agent.py — 元 agent-flow.py の 2926-3481 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# Executor — タスク実行（エージェント CLI or stub）
# --------------------------------------------------------------------------
def _agent_timeout() -> float | None:
    """エージェント CLI 1 呼び出しのタイムアウト秒。設定ファイル `agent_timeout` で調整、0/負で無効化。
    設定が無ければ環境変数 AGENT_FLOW_TIMEOUT（旧名 AGENT_FLOW_KIRO_TIMEOUT も後方互換で受理）
    → 既定 600 にフォールバックする。心拍が lease を延長し続けるため、ハングしたエージェント CLI は
    このタイムアウトでしか止められない（無いと worker が無限ブロックし run 全体が停止する）。"""
    to = _AGENT_TIMEOUT
    if to is None:
        raw = os.environ.get("AGENT_FLOW_TIMEOUT") or os.environ.get("AGENT_FLOW_KIRO_TIMEOUT") or "600"
        try:
            to = float(raw)
        except ValueError:
            to = 600.0
    return to if to > 0 else None


# 設定ファイル/CLI で解決した閾値を、args を持たない free 関数（run_agent 等）が参照できる
# よう、main の resolve 後に _configure_thresholds がここへ反映する（既定は CONFIG_DEFAULTS）。
_ARGV_LIMIT = CONFIG_DEFAULTS["argv_limit"]
# レイヤ1（in-place リトライ）: transient 分類の失敗を run_agent 内で再試行する回数と
# 初回バックオフ秒（設定 transient_retries / transient_backoff）。
_TRANSIENT_RETRIES = int(CONFIG_DEFAULTS["transient_retries"])
_TRANSIENT_BACKOFF = float(CONFIG_DEFAULTS["transient_backoff"])
# レイヤ2（形式修復リトライ）: 出力契約違反の修復再呼び出し回数（設定 format_retries）。
_FORMAT_RETRIES = int(CONFIG_DEFAULTS["format_retries"])
# executor プラグインの追加検索ディレクトリ（設定 executor_dir）。
_EXECUTOR_DIR: "str | None" = None
# エージェント CLI タイムアウト秒 / stub スリープ上限秒（設定 agent_timeout / stub_sleep_max）。
# None のままなら _agent_timeout / _stub_sleep が環境変数→組み込み既定にフォールバックする。
_AGENT_TIMEOUT: "float | None" = None
_STUB_SLEEP_MAX: "float | None" = None
# LLM 実行に使うエージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）。
_AGENT_CLI: str = str(CONFIG_DEFAULTS["agent_cli"])
# 役割（purpose）毎の上書き（設定 agents: の正規化済みマップ）。キーは planner / evaluator /
# worker（全 kind の既定）/ 個別 kind（work/generate/classify/synthesize/verify/filter/judge/
# reduce/split/map）。値は {agent_cli, model}。子プロセスへは --config 伝搬で同じ設定が届く。
_AGENT_OVERRIDES: "dict[str, dict]" = {}
AGENT_ROLES = ("planner", "evaluator", "worker")
# executor=agent の実行系プロンプトを供給するスキル名（設定 worker_skill）。
# none/builtin/空 で無効＝常に組み込みプロンプト。
_WORKER_SKILL: str = str(CONFIG_DEFAULTS["worker_skill"])
# agent_cli の設定値 → doctor が PATH 確認すべき実行ファイル名（未知の agent_cli はそのまま使う）。
_AGENT_CLI_BINARIES = {"kiro": "kiro-cli", "claude": "claude", "copilot": "copilot",
                       "codex": "codex"}


def _normalize_agent_overrides(raw) -> "dict[str, dict]":
    """設定 agents:（役割毎の agent_cli/model 上書き）を正規化する。有効キーは AGENT_ROLES
    と各ノード kind（VALID_KINDS）。不正な値は黙って落とす（設定ミスで run を殺さない）。"""
    out: "dict[str, dict]" = {}
    if not isinstance(raw, dict):
        return out
    valid = set(AGENT_ROLES) | set(VALID_KINDS)
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in valid or not isinstance(v, dict):
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
    """役割（purpose）の実効エージェント (agent_cli, model 上書き)。解決順:
    agents[purpose] ＞（purpose がノード kind なら）agents["worker"] ＞ グローバル agent_cli。"""
    ov = _AGENT_OVERRIDES.get(purpose)
    if ov is None and purpose in VALID_KINDS:
        ov = _AGENT_OVERRIDES.get("worker")
    ov = ov or {}
    return (str(ov.get("agent_cli") or _AGENT_CLI).lower(), ov.get("model") or None)


def _configure_thresholds(args) -> None:
    """設定ファイル/CLI（resolve_config 済み）の閾値をモジュール変数へ確定させる。
    run_agent / executor 解決は args を受け取らないため、プロセス起動時に一度だけ値を固定する。"""
    global _ARGV_LIMIT, _EXECUTOR_DIR, _AGENT_TIMEOUT, _STUB_SLEEP_MAX, _AGENT_CLI, _AGENT_OVERRIDES
    global _WORKER_SKILL, _TRANSIENT_RETRIES, _TRANSIENT_BACKOFF, _FORMAT_RETRIES
    for name, attr, cast in (("_TRANSIENT_RETRIES", "transient_retries", int),
                             ("_TRANSIENT_BACKOFF", "transient_backoff", float),
                             ("_FORMAT_RETRIES", "format_retries", int)):
        v = getattr(args, attr, None)
        if v is not None:
            try:
                globals()[name] = cast(v)
            except (TypeError, ValueError):
                pass
    ac = getattr(args, "agent_cli", None)
    if ac:
        _AGENT_CLI = str(ac).lower()
    _AGENT_OVERRIDES = _normalize_agent_overrides(getattr(args, "agents", None))
    wsk = getattr(args, "worker_skill", None)
    if wsk is not None:
        _WORKER_SKILL = str(wsk).strip()
    v = getattr(args, "argv_limit", None)
    if v:
        try:
            _ARGV_LIMIT = int(v)
        except (TypeError, ValueError):
            pass
    d = getattr(args, "executor_dir", None)
    if d:
        _EXECUTOR_DIR = str(d)
    kt = getattr(args, "agent_timeout", None)
    if kt is not None:
        try:
            _AGENT_TIMEOUT = float(kt)
        except (TypeError, ValueError):
            pass
    ss = getattr(args, "stub_sleep_max", None)
    if ss is not None:
        try:
            _STUB_SLEEP_MAX = float(ss)
        except (TypeError, ValueError):
            pass


def _agent_argv_limit() -> int:
    """エージェント CLI へ argv（コマンドライン）で渡すプロンプトの最大バイト数。
    これを超えるプロンプトは一時ファイルへ退避し参照渡しに切り替える。依存タスクの
    成果物が大きいとプロンプトが肥大し、OS の ARG_MAX（コマンドライン長制限）に達して
    プロセス起動自体が失敗するため。設定 argv_limit / CLI --argv-limit で調整（既定 100000）。"""
    return _ARGV_LIMIT if _ARGV_LIMIT > 0 else CONFIG_DEFAULTS["argv_limit"]


# --- エージェント CLI プラグイン（データ契約: schemas/agent-cli.schema.json） -----------------
# 組み込み（kiro/claude/copilot/codex）以外の CLI（cursor / ollama / hermes …）を、
# 定義ファイル agents/<name>.json だけで差し込む公式の口。agent-project も同じ契約を読む
# （結合はデータ契約のみ・ローダは各ツールが自前で持つ = ツール間のコード依存を作らない）。
_AGENT_PLUGIN_CACHE: "dict[str, dict | None]" = {}


def _agent_plugin_dirs() -> list:
    dirs = []
    envd = os.environ.get("KIRO_AGENTS_DIR")
    if envd:
        dirs.append(os.path.expanduser(envd))
    dirs.append(os.path.join(os.getcwd(), "agents"))
    dirs.append(os.path.expanduser("~/.agent/agents"))
    dirs.append(os.path.expanduser("~/.kiro/agents"))
    return dirs


def _normalize_agent_plugin(name: str, raw: dict, path: str) -> dict:
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
    """agents/<name>.json を探索順（$KIRO_AGENTS_DIR → <cwd>/agents → ~/.agent/agents → ~/.kiro/agents）に読む。
    無ければ None（プロセス内キャッシュ）。壊れた定義は黙って無視せず RuntimeError。"""
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key in _AGENT_PLUGIN_CACHE:
        return _AGENT_PLUGIN_CACHE[key]
    spec = None
    for d in _agent_plugin_dirs():
        p = os.path.join(d, f"{key}.json")
        try:
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except ValueError as e:
            raise RuntimeError(f"エージェント定義 {p} を JSON として読めません: {e}")
        except OSError:
            continue
        spec = _normalize_agent_plugin(key, raw, p)
        break
    _AGENT_PLUGIN_CACHE[key] = spec
    return spec


def _plugin_agent_cmd(plug: dict, model: "str | None", prompt: str):
    """プラグイン定義から (argv, stdin テキスト, 最終応答ファイル) を組み立てる（決定的）。"""
    model = model or plug.get("default_model") or None
    out_file = None
    cmd = []
    used_model = False
    for part in plug["command"]:
        if "{output_file}" in part:
            if out_file is None:
                fd, out_file = tempfile.mkstemp(prefix=f"agent-flow-agent-{plug['name']}-", suffix=".txt")
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


def _plugin_error_patterns() -> tuple:
    out = []
    for spec in _AGENT_PLUGIN_CACHE.values():
        if spec:
            out.extend(spec.get("errors") or [])
    return tuple(out)


# --- 失敗トリアージ（決定的） -------------------------------------------------------------
# エラー本文から「誰が直すか」を分類し、メッセージ先頭の機械可読タグ [agent-error:<class>] で運ぶ。
# agent-flow は run の打ち切り（環境要因なら全ノードでリトライを焼かない）、agent-project は
# リトライ節約と人への説明、viewer は行動提示に同じ判定を使う。
#   quota=利用上限（時間をおけば回復）/ auth=認証切れ（人が直す）/ env=実行環境の問題（人が直す）
#   / transient=一時的（通常リトライで解ける）。該当なし＝内容の問題（タスク単位の retry / 再計画）。
AGENT_ERROR_ENV_CLASSES = ("quota", "auth", "env")
_AGENT_ERROR_TAG_RE = re.compile(r"\[agent-error:(quota|auth|env|transient)\]")
_AGENT_ERROR_PATTERNS = (
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
    既にタグ付きならそれが正。プラグイン定義の errors を汎用パターンより先に評価する。"""
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


def run_agent(prompt: str, model: str | None, purpose: str = "") -> str:
    """エージェント CLI を呼び出してテキスト応答を返す（このツールの全 LLM 呼び出しの単一チョーク
    ポイント: planner / evaluator / executor / verify / 裁定）。

    レイヤ1（自己回復リトライ）: 失敗が transient 分類（接続断・5xx・overloaded・timeout）なら、
    ここで指数バックオフ再試行して上位層（グラフ再計画の retries 予算）へ持ち上げない。
    quota/auth/env・内容の問題（タグ無し）は再試行せず即座に上位へ（従来どおり）。
    実行中は worker の Heartbeat が claim lease を延長し続けるため、再試行で実行が延びても
    分散環境で横取りされない。試行し尽くした失敗は例外に attempts 属性を載せて raise する
    （worker が data.attempts として failed result に構造化する）。"""
    last: "RuntimeError | None" = None
    for attempt in range(max(0, _TRANSIENT_RETRIES) + 1):
        try:
            return _run_agent_once(prompt, model, purpose)
        except RuntimeError as e:
            triage = classify_agent_failure(str(e))
            if triage is None or triage[0] != "transient" or attempt >= _TRANSIENT_RETRIES:
                if attempt > 0:  # レイヤ1 を経たことを上位・人が読めるようにする
                    e = RuntimeError(f"{e}（{attempt + 1} 回試行後）")
                e.attempts = attempt + 1  # type: ignore[attr-defined]
                raise e
            wait = _TRANSIENT_BACKOFF * (2 ** attempt) + random.uniform(0, 1.0)
            log("agent", f"transient エラーを再試行 #{attempt + 1}/{_TRANSIENT_RETRIES}"
                         f"（{wait:.0f}s 待機・purpose={purpose or 'worker'}）: {str(e)[:120]}")
            time.sleep(wait)
            last = e
    raise last if last else RuntimeError("run_agent: unreachable")  # pragma: no cover


def _run_agent_once(prompt: str, model: str | None, purpose: str = "") -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    purpose（planner / evaluator / ノード kind）を渡すと設定 agents: の役割毎上書きが効く
    （kind は agents["worker"] へフォールバック）。model は 上書き ＞ 呼び出し値。"""
    cli, model_ov = _agent_for(purpose)
    model = model_ov or model
    stdin_text = None
    spill = None
    out_file = None
    if cli == "claude":
        # Claude Code ヘッドレス。プロンプトは stdin 渡し（ARG_MAX に当たらないためスピル不要）。
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        stdin_text = prompt
    elif cli == "codex":
        # OpenAI Codex CLI ヘッドレス（codex exec）。プロンプトは stdin 渡し（"-"）。
        # stdout には実行イベントログが混ざるため、最終応答は --output-last-message の
        # ファイルから読む。--skip-git-repo-check は git リポジトリ外でも動かすため。
        fd, out_file = tempfile.mkstemp(prefix="agent-flow-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        cmd.append("-")
        stdin_text = prompt
    elif cli in ("copilot", "kiro", ""):
        if cli == "copilot":
            # GitHub Copilot CLI ヘッドレス。-s で応答本文のみ、--allow-all-tools は
            # 非対話モードの必須フラグ（--allow-all-paths はファイル読み書きの許可）。
            # プロンプトは -p の引数（argv）なので argv 渡しと同じスピル退避を適用する。
            cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        else:
            cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
        if model:
            cmd += ["--model", model]
        # プロンプトが大きすぎて argv 長制限に達する恐れがあれば、一時ファイルへ退避して
        # 「そのファイルを読んで実行」する短い指示に置き換える（成果物の受け渡しを参照渡しに）。
        if len(prompt.encode("utf-8")) > _agent_argv_limit():
            fd, spill = tempfile.mkstemp(prefix="agent-flow-prompt-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt = ("以下のファイルにこのタスクの全文（依存タスクの成果物を含む）があります。"
                      f"必ずファイルの内容を読み込み、その指示に従ってタスクを実行してください: {spill}")
        cmd += (["-p", prompt] if cli == "copilot" else [prompt])
    else:
        # 組み込み以外 → プラグイン定義（agents/<name>.json・契約は schemas/agent-cli.schema.json）。
        # 以前は未知の agent_cli が黙って kiro-cli に落ちていた（設定ミスに気づけない罠）。
        plug = load_agent_plugin(cli)
        if plug is None:
            raise RuntimeError(
                f"未知の agent_cli です: {cli!r}（組み込みは kiro/claude/copilot/codex。"
                f"それ以外は agents/{cli}.json 定義が必要です — 契約: schemas/agent-cli.schema.json・"
                f"探索順: $KIRO_AGENTS_DIR → <cwd>/agents → ~/.agent/agents → ~/.kiro/agents）")
        if plug["prompt_via"] == "argv" and len(prompt.encode("utf-8")) > _agent_argv_limit():
            fd, spill = tempfile.mkstemp(prefix="agent-flow-prompt-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt = ("以下のファイルにこのタスクの全文（依存タスクの成果物を含む）があります。"
                      f"必ずファイルの内容を読み込み、その指示に従ってタスクを実行してください: {spill}")
        cmd, stdin_text, out_file = _plugin_agent_cmd(plug, model, prompt)
    plug = _AGENT_PLUGIN_CACHE.get(cli)   # プラグインなら env/timeout の上書きが効く
    env = {**os.environ, **((plug or {}).get("env") or {})}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin_text,
                              timeout=(plug or {}).get("timeout") or _agent_timeout(), env=env)
    except subprocess.TimeoutExpired:
        # 失敗として上位へ。ハングは一時的な公算が高いので transient タグを明示付与し、
        # レイヤ1（in-place 再試行）の対象にする（従来は日本語文言が英語の transient パターンに
        # 掛からず「内容の問題」扱い＝再計画 retry の予算を焼いていた）。恒久ハングでも
        # 試行ごとに本タイムアウトで有界。
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)
        raise RuntimeError(f"[agent-error:transient] {cmd[0]} タイムアウト（{_agent_timeout():.0f}s 超過）")
    finally:
        if spill:
            with contextlib.suppress(OSError):
                os.remove(spill)
    try:
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
            # rc=0 で終わる）。空を成功として扱うと、worker は「空の成果物で done」、planner は
            # stub 戦略へ黙って落ちる＝LLM を呼べていないのに動いているように見える。失敗にする。
            raise RuntimeError(_agent_failure(cmd[0], 0, proc.stdout, proc.stderr)
                               .replace("失敗 (rc=0)", "が空の応答を返しました (rc=0)"))
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)


def _repair_json_output(prompt: str, bad_text: str, purpose: str, why,
                        model: "str | None" = None, want_list: bool = False):
    """レイヤ2（形式修復リトライ）: LLM 応答が出力契約（JSON）を満たさないとき、
    「前回の出力はこう契約違反だった」と指摘して同じ役割で呼び直す（format_retries 回・有界）。
    Claude Dynamic Workflows の structured output 検証リトライの移植。寛容パーサ
    （extract_json / _normalize_verify 等）で救える崩れはそもそもここへ来ない。
    修復できたら解釈済み JSON を、できなければ None を返す（呼び出し側が従来のフォールバックへ）。"""
    contract = "JSON 配列" if want_list else "JSON"
    for _ in range(max(0, _FORMAT_RETRIES)):
        repair = (f"{prompt}\n\n[前回の出力は契約違反でした]\n"
                  f"前回の出力（先頭 400 文字）: {str(bad_text)[:400]}\n"
                  f"違反: {why}\n"
                  f"説明・前置き・コードフェンスを付けず、指示された {contract} だけを再出力してください。")
        try:
            bad_text = run_agent(repair, model, purpose=purpose)
            data = extract_json(bad_text)
        except Exception as e:  # noqa: BLE001 — 修復呼び出し自体の失敗も「まだ壊れている」扱い
            why = str(e)
            continue
        if want_list and not isinstance(data, list):
            why = f"JSON としては解釈できたが配列でない（{type(data).__name__}）"
            continue
        log("agent", f"format repair 成功（purpose={purpose}）")
        return data
    return None


# dep_results は {dep_id: result_dict}（result_dict は output テキストと任意の data を持つ）。
# 実行結果は (text, data) を返す。data は構造化成果（JSON 可、無ければ None）。
def _dep_text(r: dict) -> str:
    return str((r or {}).get("output", ""))


def _dep_data(r: dict):
    return (r or {}).get("data")


def _stub_sleep() -> None:
    """stub の擬似実行時間。既定 1〜5 秒。設定ファイル `stub_sleep_max` で調整
    （テストや動作確認では 0 にして高速化できる）。設定が無ければ環境変数
    AGENT_FLOW_STUB_SLEEP_MAX → 既定 5 にフォールバックする。"""
    mx = _STUB_SLEEP_MAX
    if mx is None:
        try:
            mx = float(os.environ.get("AGENT_FLOW_STUB_SLEEP_MAX", "5"))
        except ValueError:
            mx = 5.0
    if mx > 0:
        time.sleep(random.uniform(min(1.0, mx), mx))


def execute_stub(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = ""):
    # repo_instruction（成果物リポジトリの clone 指示）は stub の判定に使わない（goal は本来の goal）。
    _stub_sleep()  # 実行時間を模す（AGENT_FLOW_STUB_SLEEP_MAX で調整可）
    # 失敗注入: "FAIL" を含むと失敗（retry される）/ "FLAKY" は一旦 issue を残す（verify loop 用）
    if "FAIL" in goal:
        raise RuntimeError(f"[stub] 意図的失敗: {goal}")
    # gate（verify の判定 {"ok":...}）は集約対象から除く
    def _is_gate(r):
        dv = _dep_data(r)
        return isinstance(dv, dict) and "ok" in dv
    agg = {d: r for d, r in dep_results.items() if not _is_gate(r)}
    texts = {d: _dep_text(r) for d, r in dep_results.items()}
    if kind == "split":
        # 入力をリストへ分解（データ駆動 fan-out の起点）。要素数は goal 中の数字 or 既定 3
        m = re.search(r"\d+", goal)
        k = max(1, min(int(m.group()) if m else 3, 8))
        items = [f"{goal[:30]} #{i + 1}" for i in range(k)]
        return f"[split] {k} 件に分解", items
    if kind == "classify":
        label = next((lbl for lbl in ("frontend", "backend", "security", "performance")
                      if lbl in goal.lower()), "general")
        return f"class={label}", {"label": label}
    if kind == "synthesize":
        return (f"[synth] {len(agg)} 件を統合: " + " | ".join(agg)[:80],
                {"merged": list(agg)})
    if kind == "filter":
        kept = [d for d, t in texts.items() if "FAIL" not in t and "issue" not in t]
        return f"[filter] 採用={','.join(kept)}", {"kept": kept}
    if kind == "judge":
        win = next(iter(dep_results), "")
        return f"[judge] winner={win}", {"winner": win}
    if kind == "verify":
        ok = all("issue" not in t and "fail" not in t.lower() for t in texts.values())
        return ("verify=pass" if ok else "verify=fail"), {"ok": ok}
    if kind == "reduce":
        # 依存の構造化 data を畳み込む（gate は除外。list は連結、その他は要素として収集）
        items = []
        for d, r in agg.items():
            dv = _dep_data(r)
            if isinstance(dv, list):
                items.extend(dv)
            elif dv is not None:
                items.append(dv)
            else:
                items.append(_dep_text(r))
        return f"[reduce] {len(items)} 件を集約", {"items": items, "count": len(items)}
    # work / generate
    if "FLAKY" in goal:
        return f"[stub] 未完(issue): {goal}", None
    return f"[stub] 完了: {goal}", None


# flow-worker スキルの prompt.py の解決結果メモ（プロセス内。未発見 = None も記憶する）。
_worker_skill_script: "dict[str, str | None]" = {}


def _flow_worker_prompt(payload: dict) -> "str | None":
    """flow-worker スキルのプロンプトビルダーを呼び、実行規律入りプロンプトを得る。

    flow-planner と同じ作戦: スキル未インストール・生成失敗なら None を返し、
    呼び出し側は組み込みプロンプトへフォールバックする（run を止めない）。
    ビルダーは決定的（LLM 無し）で、LLM 呼び出し・役割別ルーティングは従来どおり
    run_agent が担う。payload は stdin JSON 渡し（依存成果が大きくても ARG_MAX に当たらない）。"""
    skill = (_WORKER_SKILL or "").strip().lower()
    if not skill or skill in ("none", "builtin", "off"):
        return None
    if skill not in _worker_skill_script:
        _worker_skill_script[skill] = _find_skill_script(skill, "prompt.py")
    script = _worker_skill_script[skill]
    if not script:
        return None
    try:
        proc = subprocess.run([sys.executable, script],
                              input=json.dumps(payload, ensure_ascii=False, default=str),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:300])
        return proc.stdout.strip() or None
    except Exception:  # noqa: BLE001 — スキル失敗は組み込みプロンプトで続行
        return None


def execute_agent(kind: str, goal: str, dep_results: dict, model: str | None,
                 art_dir: "str | None" = None, dep_arts: "dict | None" = None,
                 repo_instruction: str = "", workspace: "dict | None" = None,
                 references: "list[dict] | None" = None, request: str = ""):
    role = {
        "classify": "分類役。入力を適切なカテゴリへ分類し『class=<ラベル>』形式で出力。",
        "synthesize": "統合役。依存タスクの成果を統合して 1 つの成果物にまとめる。",
        "filter": "選別役。依存の候補から基準を満たすものだけを残し、採用理由を述べる。",
        "judge": "審判役。依存の複数案を比較し最良案を選び理由を述べる。",
        "reduce": "集約役。依存タスクの構造化データを畳み込み、集約結果を JSON で出力。"
                  " 要素数を表す count を含める場合は、必ず集約後リストの実際の要素数と一致させること。",
        "split": "分解役。入力を独立に処理できる小片のリストへ分解し、"
                 "各要素を文字列とする JSON 配列のみを出力（例: [\"1-100\", \"101-200\"]）。"
                 " 説明文は付けず配列だけを返すこと。",
        "map": "map役。ゴールに示された本来のタスクを、与えられた1要素だけに適用して結果を返す。"
               " 勝手に別の処理（合計・件数など）に変えないこと。"
               " リスト状の成果は JSON 配列で出力し、後段の集約に渡せるようにする。",
        "verify": "検証役。依存の成果を鵜呑みにせず独立に検算する。"
                  "可能なら結果を自分で再導出して突き合わせ、最低限"
                  "(1)件数・合計の整合 (2)抜け漏れ・重複 (3)各要素の妥当性の抜き取り検査"
                  " を行う。問題が無ければ『verify=pass』、あれば『verify=fail』と"
                  "具体的な該当箇所を出力し、末尾に JSON"
                  ' {"ok": true|false, "issues": ["..."]} を必ず添える。',
    }.get(kind, "ワーカー。次のタスクだけを完了し成果物を出力。")
    # 集約・選別系では gate（verify の判定）を入力から除く（成果物に紛れ込ませない）
    deps = dep_results
    if kind in ("reduce", "synthesize", "filter", "judge"):
        deps = {d: r for d, r in dep_results.items() if not _is_gate_result(r)}
    art_note = artifact_instruction(art_dir, dep_arts)
    # flow-worker スキルがあれば実行規律入りプロンプトを使う（無ければ従来の組み込み）。
    # 出力契約（verify の JSON・split の配列等）はスキル側でも同一に保たれている。
    prompt = _flow_worker_prompt({
        "role": "worker", "kind": kind, "goal": goal, "request": request,
        "deps": {d: {"output": _dep_text(r), "data": _dep_data(r)} for d, r in deps.items()},
        "repo_instruction": repo_instruction, "artifact_note": art_note,
        "workspace": workspace, "references": references or [],
    })
    if not prompt:
        prompt = f"あなたは分散 Dynamic Workflow の{role}\nタスク({kind}): {goal}\n"
        if repo_instruction:  # 成果物リポジトリの clone 指示（ローカル実行のエージェントへ伝える）
            prompt += repo_instruction + "\n"
        if art_note:  # 中間成果物のファイル参照プロトコル（出力先・依存成果物のパス）
            prompt += art_note + "\n"
        if deps:
            lines = []
            for d, r in deps.items():
                line = f"[{d}] {_dep_text(r)}"
                dv = _dep_data(r)
                if dv is not None:
                    line += f"\n  data: {json.dumps(dv, ensure_ascii=False)[:400]}"
                lines.append(line)
            prompt += "\n依存タスクの成果:\n" + "\n".join(lines) + "\n"
        prompt += "\n成果物を簡潔に直接出力してください。"
    text = run_agent(prompt, model, purpose=kind)   # agents: の kind 別上書き（無ければ worker）
    # 構造化データを意図する kind のみ JSON を抽出（自由記述の本文から JSON 風断片を
    # data に誤昇格させない）。
    data = None
    if kind in STRUCTURED_KINDS:
        try:
            data = extract_json(text)
        except Exception as e:  # noqa: BLE001 — 構造化できなければテキストのみ
            data = None
            why = str(e)
        else:
            why = "JSON としては解釈できたが配列でない"
        # split は data が JSON 配列でないと fan-out（_expand_splits）が展開されず run が
        # 空振りする＝出力契約が固い。レイヤ2 の修復リトライで救う（verify/reduce は
        # _normalize_verify / _reconcile_count の寛容パーサがあるため修復不要）。
        if kind == "split" and not isinstance(data, list):
            repaired = _repair_json_output(prompt, text, kind, why, model, want_list=True)
            if isinstance(repaired, list):
                data = repaired
    if kind == "reduce":
        data = _reconcile_count(data)
    elif kind == "verify":
        data = _normalize_verify(text, data)
    return text, data

