from __future__ import annotations
# prioritize.py — 元 kiro-project.py の 2610-3159 行目（機械分割・内容無改変）。
# 単体 import しない。kiro_project/__init__.py が共有名前空間へ順に exec 合成する。
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
# （kiro-flow の _configure_thresholds と同じ流儀）。
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
    設定 agents: の該当キー ＞ グローバル agent_cli（model 上書きは無ければ None＝呼び出し値）。"""
    ov = _AGENT_OVERRIDES.get(purpose) or {}
    return (str(ov.get("agent_cli") or _AGENT_CLI).lower(), ov.get("model") or None)


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
        fd, out_file = tempfile.mkstemp(prefix="kiro-project-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        return cmd + ["-"], prompt, out_file
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    return cmd + [prompt], None, None


# エージェント CLI が返す失敗のうち、人が対処しないと全処理が落ち続ける既知の原因。
# 本文から拾って明示しないと「なぜか全部失敗する」にしか見えない。
_AGENT_FATAL_PATTERNS = (
    (re.compile(r"usage limit|quota exceeded|rate.?limit|too many requests", re.I),
     "利用上限に達しています（時間をおくか、プラン・クレジットを見直してください）"),
    (re.compile(r"AccessDenied|Unauthorized|authentication failed|not authenticated"
                r"|SendMessageError|please (re)?login", re.I),
     "認証に失敗しています（再ログインが必要です）"),
    (re.compile(r"issue with the selected model|invalid model|model .{0,40}(not found|does not exist)"
                r"|may not have access to it", re.I),
     "指定したモデルを使えません（モデル名・利用権限を確認してください）"),
)


def _agent_failure(cli: str, rc: int, out: str, err: str) -> str:
    """エージェント CLI の失敗を、人が原因に辿り着ける文言にする。

    CLI は起動バナー（workdir / model / プロンプト全文）を stderr へ流す。先頭だけを切り取ると
    肝心のエラーがバナーに埋もれて消える — 実際 codex の「利用上限に達した」を丸ごと取り逃し、
    全ノードが理由不明の failed になった。エラーは末尾に出るので末尾を拾い、既知の致命的原因は
    見出しに添える。"""
    blob = f"{out or ''}\n{err or ''}"
    hints = [msg for pat, msg in _AGENT_FATAL_PATTERNS if pat.search(blob)]
    head = f"{cli} 失敗 (rc={rc})"
    if hints:
        head += ": " + " / ".join(dict.fromkeys(hints))   # 重複を畳む
    tail = (err or out or "").strip()
    return f"{head}\n{tail[-500:]}" if tail else head


def _run_kiro_cli(prompt: str, model: "str | None", purpose: str = "") -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    このツールの LLM 呼び出し（分解・優先順位・裁定・ルーティング等）はすべてここを通る。
    purpose（AGENT_PURPOSES のいずれか）を渡すと、設定 agents: の処理毎上書き
    （agent_cli / model）が効く。model は 上書き ＞ 呼び出し値（通常グローバル model）。"""
    cli, model_ov = _agent_for(purpose)
    cmd, stdin_text, out_file = _agent_cmd(cli, model_ov or model, prompt)
    # 発生源で色を抑止（NO_COLOR/TERM=dumb）。残った ANSI は strip_ansi で除去する二段構え。
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text,
                              timeout=(_AGENT_TIMEOUT if _AGENT_TIMEOUT > 0 else None), env=env)
        if proc.returncode != 0:
            raise RuntimeError(_agent_failure(cmd[0], proc.returncode, proc.stdout, proc.stderr))
        text = strip_ansi(proc.stdout).strip()
        if out_file:   # codex: 最終応答ファイルが取れればそれを正とする（stdout はイベントログ）
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
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


def rank_agent(ready: "list[Task]", model: "str | None", kiro_run=None) -> "list[Task] | None":
    kiro_run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="prioritize"))
    if len(ready) <= 1:
        return list(ready)     # 0/1 件は並べ替えの余地が無い＝LLM を呼ばない（コスト・レイテンシ削減）
    listing = "\n".join(
        f"- {t.id}: {t.title}（priority={t.priority}, source={t.source}）" for t in ready)
    prompt = ("あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係に加え、"
              "**外部で付与された priority（大きいほど高優先）も加味**して優先順位の高い順に並べ替え、"
              "**タスクID の JSON 配列だけ**を出力してください（説明文なし）。\n\nタスク:\n" + listing)
    try:
        order_ids = _extract_id_array(kiro_run(prompt, model))
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
                          kiro_run=None) -> "tuple[str, str]":
    """needs（人の判断）に落とす直前の kiro-cli 裁定ゲート。
    『ループ内で自律的に積み直して解けるか／人の判断が要るか』を判断させる。
    返り値: ("requeue", guidance) なら自律的に積み直す、("escalate", "") なら従来どおり人へ。
    判断不能・エラー・曖昧は **必ず escalate にフォールバック**（安全側＝人を飛ばさない）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="adjudicate"))
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
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は人へ
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
        f"note: {task.get('note') or '（なし）'}\n\n"
        '出力は JSON オブジェクトのみ（説明文なし）: {"c": 1, "r": 1, "a": 1}')


def assess_task(cfg: "Config", task: Task, kiro_run=None) -> "str | None":
    """投入時アセスメント。採点は情報であり、それ自体は実行可否・done 条件を変えない
    （読むのは plan-review 票・リスクダイジェスト・spec ルーティング）。知能は委譲し、
    失敗・stub は決定的ヒューリスティックへフォールバック。1 タスク 1 回（既存はスキップ）。"""
    if task.get("assess"):
        return task.get("assess")
    scores = None
    if cfg.executor != "stub":
        run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="assess"))
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
        f"  verify は『履歴』でなく『望む最終状態/差分』を見ること。after は任意（配列内の先行タスク）。")


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
            for k in ("accept", "verify_template", "note", "priority"):
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
