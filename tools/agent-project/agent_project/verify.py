from __future__ import annotations
# verify.py — 元 agent-project.py の 3160-3620 行目（機械分割・内容無改変）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
# verify ゲート / act（agent-flow 委譲）
# ---------------------------------------------------------------------------
def run_verify(cmd: str, workdir: Path, timeout: float, env: "dict | None" = None) -> "tuple[bool, str]":
    if not cmd.strip():
        return (False, "verify 未定義（自己申告では done にできない → 人の判断へ）")
    # `A && B && C` 連鎖の途中で沈黙する工程（grep -q / codd-gate 等）が落ちると、出力には
    # 成功した前段のものしか残らず「exit=1 なのにテストは全部通っている」という読めない失敗に
    # なる（実際にこの読めなさで 9 回のリトライが焼かれ、人も原因に辿り着けなかった）。
    # set -x のトレース（stderr の "+ <cmd>" 行）から最後に実行されたコマンド＝失敗した工程を
    # 特定してメッセージ先頭に載せる。トレース行は出力 tail から除く（本文を汚さない）。
    #
    # Windows ネイティブ（shell=True → cmd.exe）では `set -x` は環境変数代入になり、
    # verify コマンド全体が変質する（`$VAR` も展開されない）。トレースは POSIX shell の
    # 機能なので付けず、コマンドをそのまま実行する（cmd.exe 前提の verify 向け）。
    shell_cmd = cmd if os.name == "nt" else f"set -x\n{cmd}"
    try:
        proc = subprocess.run(shell_cmd, shell=True, cwd=str(workdir), timeout=timeout,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env={**os.environ, **env} if env else None)
    except subprocess.TimeoutExpired:
        return (False, f"verify タイムアウト（{timeout}s）")
    err_lines = (proc.stderr or "").splitlines()
    trace = [ln for ln in err_lines if ln.startswith("+")]
    clean_err = "\n".join(ln for ln in err_lines if not ln.startswith("+"))
    tail = ((proc.stdout or "")[-400:] + clean_err[-400:]).strip()
    if proc.returncode == 0:
        return (True, f"exit=0 {tail}"[:500])
    head = f"exit={proc.returncode}"
    if trace:
        step = trace[-1].lstrip("+ ").strip()
        if step:
            head += f" 失敗した工程: `{step[:200]}`"
            if len(trace) > 1:
                head += "（それより前の工程は成功）"
    return (False, f"{head} {tail}"[:600])


def run_verify_stable(cmd: str, workdir: Path, timeout: float,
                      confirm: int = 1, env: "dict | None" = None) -> "tuple[bool, bool, str]":
    """verify を最大 confirm 回まで実行し (ok, flaky, msg) を返す。confirm>1 で結果が PASS/FAIL を
    跨いだら flaky=True（不安定）。揺れる verify を NG 誤読して retry churn したり、flaky PASS を
    そのまま done にするのを防ぐ（一致したら確定、跨いだら人へ隔離）。"""
    ok, msg = run_verify(cmd, workdir, timeout, env)
    if confirm <= 1 or not cmd.strip():        # 既定(1)や verify 未定義は従来どおり1回
        return (ok, False, msg)
    for _ in range(confirm - 1):
        ok2, msg2 = run_verify(cmd, workdir, timeout, env)
        if ok2 != ok:                          # PASS/FAIL を跨いだ＝不安定（flake）
            return (ok, True, f"flaky: verify が不安定（{confirm} 回中で PASS/FAIL 混在）"
                              f" — 1回目:[{msg}] 別回:[{msg2}]"[:500])
    return (ok, False, msg)                    # 全回一致＝安定した結果


def run_verify_at_rev(cmd: str, workdir: Path, rev: str, timeout: float,
                      env: "dict | None" = None) -> "bool | None":
    """verify を workdir の rev（act 前 HEAD）のツリーで実行し PASS したか（True/False）を返す。
    detached worktree を temp に生やして実行し後始末する。git でない/worktree 作成失敗＝判定不能で None。
    red-green の『red（変更前は fail のはず）』を取るのに使う——base で PASS するなら変更を弁別していない。
    KIRO_BASE_REV は rev 自身に固定（差分基準 verify は base==HEAD で空差分＝正しく fail する）。"""
    if not cmd.strip() or not rev or not (workdir / ".git").exists():
        return None
    wt = tempfile.mkdtemp(prefix="agent-redgreen-")
    try:
        add = subprocess.run(["git", "-C", str(workdir), "worktree", "add", "--detach", wt, rev],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if add.returncode != 0:
            return None
        base_env = {**(env or {}), "KIRO_BASE_REV": rev}
        ok, _ = run_verify(cmd, Path(wt), timeout, base_env)
        return ok
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        subprocess.run(["git", "-C", str(workdir), "worktree", "remove", "--force", wt],
                       capture_output=True, timeout=30)
        shutil.rmtree(wt, ignore_errors=True)


def verify_undiscriminating(cfg: "Config", task: "Task", cwd: Path, is_temp_clone: bool,
                            git_base, env: "dict | None") -> bool:
    """verify が『act 前でも PASS＝変更を弁別しない偽 done』か（red-green の red 側検査）。

    **決定的 verify の fast path 専用**（S5）。verifier 経路では「差分が基準の対象範囲に実在
    すること」を常設基準（`DIFF_CRITERION`）として検証エージェントに判定させるので、act 前
    ツリーでの別実行は要らない。ここに残るのは `verify_template` 由来の**機械生成コマンドが
    done の唯一の根拠になる**経路で、そこは従来どおり弁別を実行で確かめる価値がある。
    対象は verify_validate ポリシー（off/synth/all）と per-task 上書きに従う。temp clone
    （workspace タスク）は act 前ツリーが手元に無いので対象外。判定不能は False。"""
    vv = str(dict(task.extra).get("verify_validate", "") or cfg.verify_validate).lower()
    if vv in ("off", "none", "false"):
        return False
    src = dict(task.extra).get("verify_source", "")
    if vv == "synth" and src not in ("synth", "template", "reused"):
        return False                                   # synth ポリシーは自動生成 verify のみ検証
    if is_temp_clone or not (cwd / ".git").exists():
        return False
    base_rev = git_base[0] if isinstance(git_base, (tuple, list)) and git_base else ""
    return run_verify_at_rev(task.verify, cwd, base_rev, cfg.verify_timeout, env) is True


def resolve_verify_cwd(cfg: "Config") -> Path:
    """verify/acceptance を実行する作業ディレクトリ。明示の `verify_cwd`（CLI/設定）があればそれを、
    無ければ従来どおり `workdir`。git-bus 等で workdir に成果が出ないとき、対象 repo のクローン先を指す。"""
    if cfg.verify_cwd:
        p = Path(cfg.verify_cwd)
        return p if p.is_absolute() else (cfg.workdir / p)
    return cfg.workdir


def _remote_branch_exists(url: str, branch: str, timeout: float = 30) -> "bool | None":
    """リモート url に branch が実在するか。判定不能（ネットワーク断・タイムアウト等）は None。
    False は「照会に成功し、無いことを確認した」ことを意味する（None と区別する）。"""
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", url, branch],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def _task_verify_cwd(cfg: "Config", task: "Task") -> "tuple[Path, str | None]":
    """このタスクの verify/回帰を実行する作業ディレクトリと、片付けが要る一時 clone のパス（無ければ None）を返す。
    優先順位: 明示 verify_cwd > タスクの `- workspace:` 該当 repo の一時 clone（target/base ブランチ）> workdir。
    workspace 指定タスクは worker が成果を該当 repo の作業ブランチへ push し、git-bus ルートの workdir には
    出ない。そこを検証先にすると「成果の無い場所」で誤判定するため、該当 repo を指定 branch で clone し
    その中で検証する。clone は worker の push 先を反映するため都度取り直す。clone 失敗・path 不在は
    RuntimeError（呼び出し側で NG 扱い・黙って workdir に倒さない）。

    cwd は常に **clone のルート**に取る。verify コマンドはリポジトリのルートからの相対（例
    `cd api && yarn test`）で書かれる規約で、プランナーの生成指示・owns 突き合わせ（_verify_paths）・
    agent-flow のワークスペース（エージェントはリポジトリ直下で path 配下のみ編集）と一致する。
    `path`（モノレポのサブフォルダ）は編集範囲/owns 用であり verify の cwd ではない。ここで
    `clone/path` に潜ると `cd api` 等の相対指定が二重になって verify が壊れ、$KIRO_BASE_REV を
    取り直す `.git` 判定（呼び出し側）も外れる。"""
    if cfg.verify_cwd:                              # 明示指定は常に最優先（運用の上書き）
        return resolve_verify_cwd(cfg), None
    spec = _workspace_spec_for(cfg, task)
    if spec and spec.get("url"):
        tmp = tempfile.mkdtemp(prefix="agent-verify-")
        dest = str(Path(tmp) / "repo")
        # worker の push 先は task_branch 時の `branch`（ap/<task-id>）。無ければ MR の
        # target、さらに無ければ base。ここを target/base だけにすると、成果は ap/ に
        # あるのに main を検証して永久に NG になる（journal の @main 誤検証）。
        branch = spec.get("branch") or spec.get("target") or spec.get("base") or ""
        # ただし task_branch（ap/<task-id>）は worker が push して初めて生まれる。push の無い
        # タスク（参照リポジトリへのルーティング・成果が別チャネルに出る作業）では origin に
        # 存在せず、そのまま clone すると「clone 失敗」という完了条件と無関係な NG で
        # リトライが焼き尽くされる。ls-remote で「無いことを確認できた」場合に限り
        # target/base へ倒す——ap/ が実在すれば従来どおりそちらを clone するので
        # @main 誤検証は再発しない。照会不能（None）は従来どおり ap/ を試し、
        # clone のエラーをそのまま人に見せる（無言の既定フォールバックはしない）。
        if branch and branch == task_branch_name(cfg, task) \
                and _remote_branch_exists(spec["url"], branch) is False:
            fallback = spec.get("target") or spec.get("base") or ""
            append_journal(cfg.journal, f"verify: {task.id} の作業ブランチ {branch} は "
                                        f"{spec['url']} に未作成（push なし）→ "
                                        f"{fallback or '既定ブランチ'} で検証")
            branch = fallback
        try:
            _clone_repo_shallow(spec["url"], branch, dest)
        except (OSError, RuntimeError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"workspace repo の clone 失敗（{spec['url']}@{branch or '既定'}）: {e}") from e
        root = Path(dest)
        sub = (spec.get("path") or "").strip().strip("/")       # path は編集範囲。誤設定検出のため在処だけ確認
        if sub and not (root / sub).is_dir():
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"workspace の path が clone 内に無い: {sub}"
                               f"（{spec['url']}@{branch or '既定'}）")
        append_journal(cfg.journal, f"verify: {task.id} を {spec['url']}@{branch or '既定'}"
                                    + (f"（path={sub}）" if sub else "") + " のクローン内で検証")
        return root, tmp
    return resolve_verify_cwd(cfg), None            # workspace 未指定は従来どおり workdir


# ---------------------------------------------------------------------------
# S5: 受入基準チェックリストと、証跡ベースのエージェント検証（verifier）
# ---------------------------------------------------------------------------
# 「1 行のシェルコマンドの exit 0」を done の根拠にする設計をやめ、「受入基準チェックリストに
# 対する、検証エージェントの証跡付き判定」を根拠にする。人がレビューする対象を
# 「コマンド（良し悪しを判断できない）」から「基準と証跡（判断できる）」へ移すのが目的。
#
# 不変条件は変えない: done は機械検証の PASS のみが根拠 / 必ず有限回で止まる。
# 変わるのは検証の**表現**（コマンド 1 行 → 基準リスト）と**実行者**（シェル → エージェント）。

# 差分の常設基準（red-green の代替）の**正典**。スキルへは `spec["diff_criterion"]` として
# この文をそのまま渡す（P2-5。副作用制約 `side_effects_text` と同じ手）。
#
# 2 箇所で育てると、**検証レポートに出る基準文とエージェントが見た基準文が黙ってずれる**
# ——判定は番号で突き合わせるので、ずれても機械は気付かない。スキル側にも同じ表が残るが、
# 入力にこの文があればそちらが勝つので、実害のある重複ではなくなる（スキルは単体でも
# 動く契約なので、受け皿としての定数は要る）。
DIFF_CRITERION = ("このタスクの差分が、上の基準の対象範囲に実在すること"
                  "（変更が無い・無関係な場所にしか無いなら fail）")


def no_diff_criterion(reason: str) -> str:
    """`- no_diff: <理由>` の差し替え基準（W4）。差分基準は消えず、述語だけ変わる:
    調査・方針・「変更しないこと」系は差分でなく、宣言した成果物の実在と参照で判定する。"""
    return (f"このタスクは差分を作らない宣言（no_diff: {reason}）。代わりに、タスクが宣言した"
            "成果物ファイルが対象 revision に実在し、その内容を上の基準の判定で参照したこと"
            "（成果物が実在しない・内容を参照できないなら fail）")

VERDICTS = ("pass", "fail", "unverifiable")

# 副作用の許容範囲（設定 verify_side_effects）の**正典**。DB・外部サービスへの書き込みは
# どちらでも禁じる: 検証が失敗すると何が壊れたか分からなくなるうえ、リトライで何度も走るので
# 副作用が累積する。そこまで要る検証は人が verify: に明示的に書く。
#
# スキル（backlog-verifier）へは `spec["side_effects_text"]` として**解決済みの文**を渡す。
# スキル側にも同じ表があるが、受け取った文があればそちらを優先する——本文が正典で、
# スキルは受け取る（同じ文言を 2 か所で育てると、経路によって制約が変わる）。
VERIFY_SIDE_EFFECT_RULES = {
    "workspace": (
        "作業ツリーの中だけで完結させてください。ビルド・テスト・grep・ローカル起動は可。"
        "ネットワーク到達（HTTP 取得・外部 API）と、作業ツリー外への書き込みはしないでください。"
        "それが必要な基準は verdict=unverifiable として理由を書いてください。"
    ),
    "network": (
        "作業ツリーの中の変更と、読み取りのためのネットワーク到達（HTTP 取得・疎通確認）まで可。"
        "DB や外部サービスへの **書き込み** はしないでください"
        "（失敗時に何が壊れたか分からなくなり、リトライで副作用が累積します）。"
        "それが必要な基準は verdict=unverifiable として理由を書いてください。"
    ),
}


def verify_side_effect_rule(value: "str | None") -> str:
    """設定 `verify_side_effects` の値 → 検証エージェントへ渡す制約文（未知の値は workspace）。"""
    return VERIFY_SIDE_EFFECT_RULES.get(str(value or "workspace"),
                                        VERIFY_SIDE_EFFECT_RULES["workspace"])


def task_acceptance(task: "Task") -> "list[str]":
    """このタスクの受入基準チェックリスト（自然文）。

    読み取り境界の正規化を 1 か所に固定する（統一 verify・新規書き込みは正規形だけ）:
      1. `- task_acceptance_criteria:` 行（複数可）… 正規形
      2. 無ければ `- acceptance:` 行（複数可）… 旧形式（読み替えのみ）
      3. 無ければ `- accept:`（自然文 1 行）を 1 項目のチェックリストとして扱う（旧形式）
      4. どれも無ければ空（＝固定検証コマンドの fast path だけ）

    `Task.extra` は (key, value) のリストなので、同名キーの複数行はそのまま往復する
    （`- {k}: {v}` で書き戻される）。スキーマもパーサも変えずに済む。
    """
    canonical = [v.strip() for k, v in task.extra
                 if k == "task_acceptance_criteria" and str(v).strip()]
    if canonical:
        return canonical
    lines = [v.strip() for k, v in task.extra if k == "acceptance" and str(v).strip()]
    if lines:
        return lines
    accept = str(dict(task.extra).get("accept", "") or "").strip()
    return [accept] if accept else []


def has_verify_plan(task: "Task") -> bool:
    """concrete な verify か、検証の材料（受入基準 / 固定コマンド / verify_template）を持つか。"""
    if task.verify:
        return True
    ex = dict(task.extra)
    return bool(task_acceptance(task) or ex.get("verify_template", "").strip()
                or str(ex.get("verification_commands", "") or "").strip())


def find_skill_script(skill: str, script: str) -> "str | None":
    """スキルの scripts/<script> を探す（agent-flow の `_find_skill_script` と同じ解決順）。

    プロジェクト → git root → ~/.agents/skills → ~/.kiro/skills → skill-registry.json の
    skill_home。**上位にプロジェクト独自のスキルを置けば全面的に差し替えられる**。
    """
    cands = [Path.cwd() / ".github" / "skills" / skill / "scripts" / script]
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=10).stdout.strip()
        if root:
            cands.append(Path(root) / ".github" / "skills" / skill / "scripts" / script)
    except (OSError, subprocess.SubprocessError):
        pass
    for home in ("~/.agents/skills", "~/.agent/skills", "~/.kiro/skills"):
        cands.append(Path(home).expanduser() / skill / "scripts" / script)
    for agent_dir in (Path.home() / ".agents", Path.home() / ".agent", Path.home() / ".kiro"):
        reg = agent_dir / "skill-registry.json"
        try:
            home = json.loads(reg.read_text(encoding="utf-8")).get("skill_home", "")
        except (OSError, ValueError):
            continue
        if home:
            cands.append(Path(home) / skill / "scripts" / script)
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _has_evidence(ev: dict) -> bool:
    return bool((ev.get("commands") or []) or (ev.get("files") or []))


def normalize_verification(text: str, criteria: "list[str]") -> dict:
    """検証エージェントの応答を判定レコードへ正規化する（フェイルクローズ）。

    決定的に効かせる護りは 2 つ:
      1. **フェイルクローズ** — 明示の pass が無い基準は fail（agent-flow の
         `_normalize_verify` と同じ規則。曖昧な出力を pass 扱いすると壊れた検証が素通りする）
      2. **証跡必須** — pass なのに実行コマンドも参照ファイルも無い基準は fail へ落とす
         （「確認しました」だけで pass にできる穴を塞ぐ。verifier の自己欺瞞への防御）

    戻り値: {"criteria": [{id,text,verdict,evidence,note}], "pass": n, "fail": n,
             "unverifiable": n, "ok": bool}
    """
    data = _extract_json_obj(strip_ansi(str(text or ""))) or {}
    raw = data.get("criteria") if isinstance(data.get("criteria"), list) else []
    by_id: "dict[int, dict]" = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id", i + 1))
        except (TypeError, ValueError):
            cid = i + 1
        by_id[cid] = item

    out: "list[dict]" = []
    for i, ctext in enumerate(criteria):
        item = by_id.get(i + 1, {})
        verdict = str(item.get("verdict") or "").strip().lower()
        ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        evidence = {
            "commands": [str(x) for x in (ev.get("commands") or [])][:10],
            "output": str(ev.get("output") or "")[:2000],
            "files": [str(x) for x in (ev.get("files") or [])][:20],
        }
        note = str(item.get("note") or "")[:500]
        if verdict not in VERDICTS:
            verdict, note = "fail", (note or "判定が読み取れませんでした（フェイルクローズ）")
        elif verdict == "pass" and not _has_evidence(evidence):
            verdict = "fail"
            note = (note + " ／ " if note else "") + "証跡（実行コマンド・参照ファイル）が無いため不採用"
        out.append({"id": i + 1, "text": ctext, "verdict": verdict,
                    "evidence": evidence, "note": note})
    counts = {v: sum(1 for c in out if c["verdict"] == v) for v in VERDICTS}
    return {"criteria": out, "pass": counts["pass"], "fail": counts["fail"],
            "unverifiable": counts["unverifiable"],
            "ok": bool(out) and counts["fail"] == 0 and counts["unverifiable"] == 0}


def verification_message(result: dict) -> str:
    """検証結果を 1 行の要約（vmsg）にする。失敗した基準を先に出す。"""
    crit = result.get("criteria") or []
    if not crit:
        return "検証レポートを読み取れませんでした（フェイルクローズ）"
    bad = [c for c in crit if c["verdict"] != "pass"]
    head = f"基準 {len(crit)} 件中 {result.get('pass', 0)} 件 pass"
    if not bad:
        return head
    detail = " ／ ".join(f"[{c['verdict']}] {c['text'][:60]}"
                         + (f" — {c['note'][:80]}" if c["note"] else "")
                         for c in bad[:4])
    return f"{head}: {detail}"[:600]


def verification_report_md(cfg: "Config", task: "Task", result: dict, rev: str, body: str) -> str:
    """状態リポジトリへ保存する検証レポート（人が読む一次資料）。"""
    lines = [f"# 検証レポート: {task.id} — {task.title}", "",
             f"- rev: `{rev or '(不明)'}`", f"- 判定: {verification_message(result)}", "",
             "| # | 受入基準 | 判定 | 証跡 |", "|---|---|---|---|"]
    for c in result.get("criteria") or []:
        ev = c["evidence"]
        cells = " / ".join(x for x in [", ".join(ev["commands"]), ", ".join(ev["files"])] if x)
        lines.append(f"| {c['id']} | {c['text'][:120]} | {c['verdict']} | {cells[:160] or '—'} |")
    lines += ["", "## 検証エージェントの本文", "", (body or "").strip()]
    return "\n".join(lines) + "\n"


def verifications_dir(cfg: "Config") -> Path:
    """検証レポートの置き場（状態リポジトリ直下）。root は backlog の親（archive 等と同じ導出）。"""
    return cfg.backlog.parent / "verifications"


def external_verdict_path(cfg: "Config", task: "Task", rev: str) -> Path:
    """他ノードが確かめた結果の置き場（`verifications/<task-id>/<rev>.external.json`）。

    検証委譲（P4-b）の受理点。**成果コミット（rev）ごとに 1 件**なのが要点で、成果が
    進めば別のファイルになる——「昔の版で通った」を今の版の根拠に使えない。"""
    safe_rev = re.sub(r"[^0-9A-Za-z._-]", "-", str(rev or "norev"))[:40] or "norev"
    return verifications_dir(cfg) / task.id / f"{safe_rev}.external.json"


def save_external_verdict(cfg: "Config", task: "Task", rev: str, record: dict) -> str:
    """他ノードの検証結果を受理点へ保存する（相対パスを返す。失敗しても回収は止めない）。"""
    path = external_verdict_path(cfg, task, rev)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(path), record)
    except OSError as e:
        append_journal(cfg.journal, f"外部検証の記録を保存できませんでした（無視）: {task.id}: {e}")
        return ""
    return str(path.relative_to(cfg.backlog.parent))


def read_external_verdict(cfg: "Config", task: "Task", rev: str) -> "dict | None":
    """この成果 rev について他ノードが返した receipt（採用できなければ None）。

    受理の条件は内蔵 verifier とまったく同じ——`receipt_errors` の照合（schema、plan digest、
    result revision、証跡付き pass）を通ることだけ。誰が確かめたかは見ない。かつては
    「板の run が成功終端で終わった」を根拠に `{"verdict": "pass"}` を書いていたため、
    証跡が 1 つも無い pass が done へ通っていた。"""
    if not str(rev or "").strip():
        return None                     # rev が取れない環境では受理しない（照合の根拠が無い）
    try:
        rec = json.loads(external_verdict_path(cfg, task, rev).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    receipt = rec.get("receipt") if isinstance(rec.get("receipt"), dict) else rec
    plan = build_task_verification_plan(cfg, task)
    errs = _verifycontract.receipt_errors(receipt, plan=plan, expected_rev=rev)
    if errs:
        append_journal(cfg.journal,
                       f"外部検証を不採用（fail-close）: {task.id} rev={rev[:12]} — "
                       f"{'; '.join(errs)[:200]}")
        return None
    return receipt


def save_verification_report(cfg: "Config", task: "Task", result: dict, rev: str,
                             body: str) -> str:
    """`verifications/<task-id>/<rev>.md` へ保存し、相対パスを返す（失敗しても検証は止めない）。"""
    safe_rev = re.sub(r"[^0-9A-Za-z._-]", "-", str(rev or "norev"))[:40] or "norev"
    rel = f"verifications/{task.id}/{safe_rev}.md"
    try:
        dest = verifications_dir(cfg) / task.id / f"{safe_rev}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(verification_report_md(cfg, task, result, rev, body), encoding="utf-8")
    except OSError as e:
        append_journal(cfg.journal, f"検証レポートを保存できませんでした（無視）: {task.id}: {e}")
        return ""
    return rel


# ---------------------------------------------------------------------------
# verify の用意（人が書く負担を減らす）。
#   - `- verify_template: <名前> :: <引数...>` … 決定的に展開（エージェント不要）。
# 展開結果は concrete な `verify`（終了コード0=PASS）になり、読み取りアダプタ
# （task_verification_commands）が source=template の固定コマンドとして plan へ載せる。
# `accept:` からの LLM 一発合成（synth_verify）は P1-A8 で撤去した——自然文基準は
# verification_plan の criterion として agent-flow runner の verifier が判定する。
# ---------------------------------------------------------------------------
def _sh_q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def expand_verify_template(spec: str) -> "str | None":
    """`<名前> :: <引数...>` を決定的なシェル verify に展開する（エージェント不要）。未知の名前は None。
    鉄則どおり「履歴でなく最終状態/差分」を見る形にする（diff-contains は $KIRO_BASE_REV を使う）。"""
    name, _, rest = (spec or "").partition("::")
    name = name.strip().lower()
    rest = rest.strip()
    args = [x.strip() for x in rest.split("::")] if rest else []
    if name in ("file-contains", "contains") and len(args) >= 2:
        return f"grep -qF -- {_sh_q(args[1])} {_sh_q(args[0])}"        # path に needle を含む
    if name in ("file-exists", "exists") and args:
        return f"test -e {_sh_q(args[0])}"
    if name in ("defines", "symbol") and len(args) >= 2:               # path に symbol を定義
        sym, path = args[0], args[1]
        pat = f"def +{sym}|function +{sym}|{sym} *=|class +{sym}"
        return f"grep -qE {_sh_q(pat)} {_sh_q(path)}"
    if name in ("diff-contains", "grep-diff") and args:               # act 後の差分に needle（履歴に騙されない）
        return f'git log "$KIRO_BASE_REV"..HEAD -p 2>/dev/null | grep -qF -- {_sh_q(args[0])}'
    if name in ("cmd-succeeds", "tests-pass", "cmd", "run",            # 残り全体をコマンドとして実行
                "test-passes", "builds", "exit-zero") and rest:       # test-passes/builds/exit-zero は意図を明示する別名
        return rest
    if name in ("endpoint-returns", "http-status") and len(args) >= 2:  # <url> が <status> を返す
        url, status = args[0], args[1]
        return (f'test "$(curl -s -o /dev/null -w \'%{{http_code}}\' -- {_sh_q(url)})"'
                f' = {_sh_q(status)}')
    return None


# 全角の文/句読点。シェルコマンドにはまず現れず、自然言語（散文・拒否文）の強い指標。
_PROSE_PUNCT = "。、！？；：「」『』（）"


def _looks_like_shell_command(line: str) -> bool:
    """合成された 1 行が「決定的なシェルコマンド」か、エージェントの自然言語かを判定する。
    全角の文/句読点を含むものは散文とみなして弾き、残りは `sh -n`（構文解析のみ・非実行）で
    妥当性を確認する。疑わしきは False（→ verify 未定義のまま人の判断へ）。"""
    s = line.strip()
    if not s:
        return False
    if any(ch in s for ch in _PROSE_PUNCT):       # 全角の文/句読点 → 自然言語
        return False
    try:
        # sh -n は構文チェックのみで実行しない。不完全な if/未閉じクォート等の散文を弾く。
        chk = subprocess.run(["sh", "-n", "-c", s], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True          # 構文チェック不能な環境では句読点判定のみで通す（best-effort）
    return chk.returncode == 0


def ensure_verify(cfg: "Config", task: "Task", agent_run=None) -> bool:
    """task に concrete な verify が無ければ `verify_template` から決定的に展開する。

    **`accept:` からの LLM 一発合成は S5 で廃止した。** 環境差で大半が失敗して人へ倒れるうえ、
    合成されたコマンドが「たまたま通る劣化した検証」でも人にはそれを見抜く材料が無い、という
    のが根本問題だった。自然文の完了条件は `task_acceptance`（受入基準チェックリスト）として
    verifier が証跡付きで判定する——人がレビューする対象を「コマンド」から「基準と証跡」へ移す。
    """
    if task.verify:
        return False
    tmpl = dict(task.extra).get("verify_template", "").strip()
    if tmpl:
        cmd = expand_verify_template(tmpl)
        if cmd:
            task.verify = cmd
            task.extra.append(("verify_source", "template"))
            return True
    return False



# ---------------------------------------------------------------------------
# 統一 verify（P1-A3）: verification_plan の生成と receipt の検算
# 正典設計: docs/plans/2026-07-30-unified-task-verify-design.md
# schema: schemas/verification-plan.schema.json / schemas/verification-receipt.schema.json
# digest・採番・判定の規則は agentcore.verifycontract の 1 実装（agent-flow runner と共有）。
# agent-project は plan を確定して run に渡し、返った receipt の digest・revision・証跡を
# 検算して状態を確定する。receipt が無い・照合を通らない pass は採用しない（fail-close）。
# ---------------------------------------------------------------------------

def task_verification_commands(task: "Task") -> "list[dict]":
    """このタスクの固定検証コマンド（正規形＋旧形式の読み替え）。

      1. `- verification_commands:` 行（複数可・正規形）… source=user
      2. `- verify:`（旧形式 1 行）… source=legacy（verify_template 展開由来なら template）
    """
    out: "list[dict]" = []
    for k, v in task.extra:
        if k == "verification_commands" and str(v).strip():
            out.append({"command": str(v).strip(), "source": "user"})
    if task.verify:
        src = "template" if dict(task.extra).get("verify_source") == "template" else "legacy"
        out.append({"command": task.verify, "source": src})
    return out


def build_task_verification_plan(cfg: "Config", task: "Task") -> "dict | None":
    """タスクの検証材料から verification_plan（digest 付き）を確定する。材料が無ければ None。

    自然文基準があるときは差分の常設基準（DIFF_CRITERION）を最後尾に足す（verifier 経路の
    既存規則と同じ）。固定コマンドだけのタスクには足さない——criterion が 1 つでもあると
    実行側で verifier セッション（LLM）が要るため、fast path の費用を変えない。"""
    criteria = list(task_acceptance(task))
    commands = task_verification_commands(task)
    if not criteria and not commands:
        return None
    if criteria:
        nd = str(task.get("no_diff") or "").strip()
        criteria.append(no_diff_criterion(nd) if nd else DIFF_CRITERION)
    ws = _workspace_spec_for(cfg, task) or {}
    integration = ({"target": str(ws.get("target"))}
                   if ws.get("branch") and ws.get("target")
                   and ws.get("branch") != ws.get("target") else None)
    return _verifycontract.build_plan(
        task.id, criteria=criteria, commands=commands,
        workspace=str(ws.get("url") or ""),
        policy={"timeout_sec": cfg.verify_timeout, "confirm": cfg.verify_confirm},
        integration=integration)


def read_flow_receipt(cfg: "Config", task: "Task") -> "dict | None":
    """このタスクの最後の agent-flow run が返した receipt（runs/<rid>/receipt.json）。無ければ None。"""
    rid = str(task.get("last_run") or "").strip()
    if not rid:
        return None
    try:
        rec = json.loads((cfg.bus / "runs" / rid / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def receipt_to_verification(receipt: dict) -> dict:
    """receipt を既存の検証レコード形（normalize_verification の戻り値と同形）へ変換する。

    settle から needs 票・検証レポート・unverifiable 経路（検証不能→委譲/人送り）までの下流を
    1 本のまま使うための変換で、判定は変えない（inconclusive → unverifiable の語だけ読み替える。
    表示・ルーティングの既存語彙が unverifiable のため）。固定コマンドの結果も criterion と同じ
    行として並べ、人が 1 つの表で読めるようにする。"""
    out: "list[dict]" = []
    integration = receipt.get("integration")
    if isinstance(integration, dict):
        raw = str(integration.get("verdict") or "")
        verdict = "unverifiable" if raw == "inconclusive" else raw if raw in ("pass", "fail") else "fail"
        target = str(integration.get("target") or "target")
        target_rev = str(integration.get("target_rev") or "")
        out.append({"id": len(out) + 1, "text": f"最新 target `{target}` が成果 revision に統合済み",
                    "verdict": verdict,
                    "evidence": {"commands": [], "output": target_rev[:40], "files": []},
                    "note": ("target revision を取得できませんでした" if verdict == "unverifiable"
                             else "最新 target が成果 revision の祖先ではありません" if verdict == "fail"
                             else "")})
    for c in receipt.get("commands") or []:
        cmd = str(c.get("command") or "")
        if c.get("inconclusive"):
            verdict, note = "unverifiable", str(c.get("note") or "実行できませんでした")
        elif c.get("exit_code") == 0 and not c.get("flaky"):
            verdict, note = "pass", ""
        else:
            verdict, note = "fail", f"exit={c.get('exit_code')}"
        out.append({"id": len(out) + 1, "text": f"固定検証コマンド: `{cmd[:160]}`",
                    "verdict": verdict,
                    "evidence": {"commands": [cmd][:10] if verdict == "pass" else [],
                                 "output": str(c.get("output_tail") or "")[:2000], "files": []},
                    "note": note[:500]})
    for c in receipt.get("criteria") or []:
        verdict = str(c.get("verdict") or "").strip().lower()
        if verdict not in _verifycontract.CRITERION_VERDICTS:
            verdict = "fail"
        elif verdict == "pass" and not _verifycontract.criterion_has_evidence(c):
            verdict = "fail"
        elif verdict == "inconclusive":
            verdict = "unverifiable"
        evs = c.get("evidence") if isinstance(c.get("evidence"), list) else []
        out.append({"id": len(out) + 1, "text": str(c.get("text") or c.get("id") or "")[:300],
                    "verdict": verdict,
                    "evidence": {
                        "commands": [str(e.get("command")) for e in evs
                                     if isinstance(e, dict) and e.get("kind") == "command"
                                     and e.get("command")][:10],
                        "output": " / ".join(str(e.get("summary") or e.get("output_tail") or "")
                                             for e in evs if isinstance(e, dict))[:2000],
                        "files": [str(e.get("path")) for e in evs
                                  if isinstance(e, dict) and e.get("kind") in ("file", "diff")
                                  and e.get("path")][:20]},
                    "note": str(c.get("note") or "")[:500]})
    counts = {v: sum(1 for c in out if c["verdict"] == v) for v in VERDICTS}
    return {"criteria": out, "pass": counts["pass"], "fail": counts["fail"],
            "unverifiable": counts["unverifiable"],
            "ok": bool(out) and counts["fail"] == 0 and counts["unverifiable"] == 0,
            "receipt": True, "integration": receipt.get("integration")}


# 固定コマンド実行の 1 実装（agent-flow runner と共有）。モジュール名を経由させるのは
# テストが `km.run_plan_command` を差し替えられるようにするため（旧 run_verify と同じ流儀）。
run_plan_command = _verifycontract.run_plan_command


def run_local_receipt(cfg: "Config", task: "Task", plan: dict, rev: str,
                      vcwd: "Path", env: "dict | None") -> dict:
    """agent-flow runner の receipt を採用できなかったタスクの local runner（P1-A8）。

    plan の固定コマンドをこのノードで一度だけ実行し、同じ契約の receipt を組む
    （実行セマンティクスは agentcore.verifycontract.run_plan_command の 1 実装——runner と
    割れない）。dry-run・stub 実行・旧 agent-flow との混在フリートなど「run が receipt を
    返さない」経路の受け皿で、判定は必ず receipt の検算（採用側と同じ規則）を通る。
    自然文 criterion の判定（verifier セッション）は agent-flow runner だけが持つため、
    ここでは inconclusive——採用側で unverifiable（委譲/人送り）へ流れ、pass/fail を
    発明しない。rev は無ければ空のまま記録する（git の無い workdir。旧 fast path と同じ
    扱いで、成果 revision の照合は「取れる環境でだけ」効く）。"""
    commands = [run_plan_command(str(c.get("command") or ""), str(vcwd),
                                 cfg.verify_timeout, env, cfg.verify_confirm)
                for c in plan.get("commands") or []]
    criteria = [{"id": c["id"], "text": str(c.get("text") or ""), "verdict": "inconclusive",
                 "note": "自然文基準の判定は agent-flow runner の receipt が必要"
                         "（local runner は固定コマンドのみ実行）"}
                for c in plan.get("criteria") or []]
    integration = _verifycontract.run_plan_integration(plan, str(vcwd), rev)
    return _verifycontract.build_receipt(
        plan, result_rev=rev, commands=commands, criteria=criteria,
        verified_by=str(getattr(cfg, "node", "") or "local"), integration=integration)


def _adopt_receipt(cfg: "Config", task: "Task", receipt: dict,
                   how: str) -> "tuple[bool, bool, str, dict]":
    """検算を通った receipt を検証レコード・レポート・journal へ確定する（採用の 1 実装）。"""
    overall = _verifycontract.receipt_overall(receipt)
    result = receipt_to_verification(receipt)
    rev = str(receipt.get("result_rev") or "")
    result["rev"] = rev
    body = (f"- plan digest: `{receipt.get('plan_digest')}`\n"
            f"- result revision: `{rev or '(なし)'}`\n"
            f"- 判定: {overall}（{how}）\n")
    report = save_verification_report(cfg, task, result, rev, body)
    result["report"] = report
    task.drop("verification")
    task.extra.append(("verification", json.dumps(
        {"pass": result["pass"], "fail": result["fail"],
         "unverifiable": result["unverifiable"], "report": report,
         "receipt": True, "plan_digest": str(receipt.get("plan_digest") or "")},
        ensure_ascii=False)))
    # flaky（confirm 実行で PASS/FAIL を跨いだ）は receipt_overall では fail だが、settle の
    # 隔離経路（リトライを焼かず人へ）に乗せるため個別に立てて返す（旧 run_verify_stable と同じ）。
    flaky = any(bool(c.get("flaky")) for c in receipt.get("commands") or []
                if isinstance(c, dict))
    # 経路（誰の receipt か）を判定文にも残す。needs 票と受領書はこの 1 行しか読まないので、
    # 経路がレポート本文にしか無いと「別の端末が確かめた」ことが人の目に届かない。
    vmsg = f"{verification_message(result)}（{how}）"
    if flaky:
        vmsg = f"flaky: 検証コマンドが不安定（複数回実行で PASS/FAIL 混在） — {vmsg}"[:500]
    append_journal(cfg.journal, f"検証（receipt）: {task.id} — {vmsg}")
    return overall == "pass", flaky, vmsg, result


def settle_from_receipt(cfg: "Config", task: "Task", plan: "dict | None", expected_rev: str,
                        vcwd: "Path | None" = None,
                        env: "dict | None" = None) -> "tuple[bool, bool, str, dict] | None":
    """統一 verify の検算（W3 の 1 実装）。plan が無ければ None（検証材料の無いタスク）。

    第一経路は agent-flow runner の receipt: digest・成果 revision・証跡の検算を通った判定
    だけを採用する（fail-close: 通らない receipt の pass は決して採用しない）。採用できず
    vcwd が与えられていれば local runner（run_local_receipt）が固定コマンドを実行して同じ
    契約で判定する——旧 fast path / 旧 verifier の実行は P1-A8 で撤去済みで、done の根拠は
    どの経路でも receipt の検算だけ。

    戻り値は既存 settle と同形 (ok, flaky, vmsg, verification)。"""
    if not isinstance(plan, dict):
        return None
    receipt = read_flow_receipt(cfg, task)
    if receipt is not None:
        errs = _verifycontract.receipt_errors(receipt, plan=plan, expected_rev=expected_rev)
        if not errs:
            return _adopt_receipt(cfg, task, receipt,
                                  "agent-flow runner の receipt を検算して採用")
        append_journal(cfg.journal,
                       f"検証 receipt を不採用（fail-close・local runner へ）: "
                       f"{task.id} — {'; '.join(errs[:3])}")
    if vcwd is None:
        return None
    local = run_local_receipt(cfg, task, plan, str(expected_rev or ""), vcwd, env)
    return _adopt_receipt(cfg, task, local,
                          "local runner がこのノードで実行（run の receipt なし）")


# --- verify 失敗の解釈（単一の解釈点） ---------------------------------------------------
# 以前この解釈は agent-dashboard 側にあり、**agent-project が書いた判断材料の散文を正規表現で
# 読み直して**いた。書き手が文言を少し変えるだけで読み手の正規表現が外れ、表示だけが静かに
# 壊れる（走ってもいない検証を「失敗しました」と言い切る等）。生データを持っているここで
# 一度だけ解釈し、結果を needs へ構造化して渡す。特定ツール名には依存しない。
_VERIFY_DIAG_EMPTY = {"summary": "", "resolution": "", "category": "", "owner": "",
                      "command": "", "workdir": "", "exit_code": "",
                      "target": "", "resolved_target": ""}


def diagnose_verify_failure(cmd: str, vmsg: str, workdir: "Path | str | None" = None) -> dict:
    """verify の生出力を「原因・対処・根拠」へ正規化する。解釈できなければ空 dict を返す
    （空は「分からない」であって「失敗していない」ではない。呼び出し側で断定しないこと）。"""
    raw = str(vmsg or "")
    if not raw.strip():
        return dict(_VERIFY_DIAG_EMPTY)
    wd = str(workdir or "")
    step = (re.search(r"失敗した工程:\s*`([^`]+)`", raw) or [None, ""])[1] if "失敗した工程" in raw else ""
    exit_code = (re.search(r"exit=(\d+)", raw) or [None, ""])[1] if "exit=" in raw else ""
    passed = (re.search(r"(\d+)\s+passed", raw) or [None, ""])[1] if "passed" in raw else ""
    failed = (re.search(r"(\d+)\s+failed", raw) or [None, ""])[1] if "failed" in raw else ""
    cmd_missing = (re.search(r"([\w./-]+):\s*command not found", raw) or [None, ""])[1]
    miss_en = (re.search(r"(?:file or directory not found|No such file or directory)[:\s]+([^\s)）]+)",
                         raw, re.I) or [None, ""])[1]
    miss_ja = (re.search(r"(?:エラー\s*[:：]\s*)?[^\n]*?(?:見つかりません|存在しません)\s*[:：]\s*([^\s)）]+)",
                         raw) or [None, ""])[1]
    not_found = miss_en or miss_ja

    d = dict(_VERIFY_DIAG_EMPTY)
    d["command"] = step or str(cmd or "")
    d["workdir"] = wd
    d["exit_code"] = exit_code

    if cmd_missing:
        d.update(category="実行環境", owner="検査設定・実行環境",
                 summary=f"検証に必要なコマンド「{cmd_missing}」が実行環境に見つかりません。",
                 resolution=f"「{cmd_missing}」がインストール済みか、検証プロセスの PATH から"
                            "実行できるかを確認してから再実行してください。")
        return d
    if not_found:
        resolved = (os.path.join(wd, not_found) if wd and not os.path.isabs(not_found) else not_found)
        d.update(category="パス・入力", owner="検査設定・実行環境",
                 target=not_found, resolved_target=os.path.normpath(resolved) if resolved else "",
                 summary=f"検証コマンドが必要なパス「{d['resolved_target'] or not_found}」を"
                         "見つけられませんでした。",
                 resolution="対象が実際に存在する場所を確認し、コマンドのパス指定を実行ディレクトリ"
                            "基準の正しい相対パス、または絶対パスへ変更して再実行してください。")
        return d
    if failed:
        d.update(category="テスト失敗", owner="成果物",
                 summary=f"テストが {failed} 件失敗しました。",
                 resolution="失敗したテスト名と最初のエラーを生ログで確認し、成果物を修正して"
                            "同じ検証コマンドを再実行してください。")
        return d
    if re.search(r"no tests ran", raw, re.I):
        d.update(category="検証対象なし", owner="検査設定・実行環境",
                 summary="テストが 1 件も実行されませんでした（対象が見つからないか、条件に一致しません）。",
                 resolution="テスト対象のパス、選択条件、実行ディレクトリを確認してから再実行してください。")
        return d
    if step:
        d.update(category="検証工程", owner="要確認",
                 summary=f"検証コマンドの工程「{step}」で失敗しました（それより前の工程は成功しています）。",
                 resolution="生ログの該当工程を確認し、表示された作業ディレクトリで同じコマンドを"
                            "再現して原因を切り分けてください。")
        return d
    if exit_code and passed and exit_code != "0":
        # 「テストは通っているのに exit≠0」: && 連鎖の後段（grep・外部チェック等）が沈黙して
        # 失敗した記録。どこが落ちたかは残っていないが、少なくとも「テストの失敗ではない」ことを言う
        # （テスト成功の出力だけを見せられて混乱するのが一番まずい）。
        d.update(category="検証工程", owner="検査設定・実行環境",
                 summary=f"テストは {passed} 件成功していますが、検証コマンドの後段の工程"
                         f"（grep や外部チェックなど）が失敗しています（終了コード {exit_code}）。",
                 resolution="テスト後に実行される工程を生ログで確認し、その工程を単独で再実行してください。")
        return d
    if exit_code:
        d.update(category="不明な検証失敗", owner="要確認",
                 summary=f"検証コマンドが失敗しました（終了コード {exit_code}）。",
                 resolution="実行コマンド・作業ディレクトリ・生ログを確認し、同じ条件で再現して"
                            "原因を切り分けてください。")
        return d
    return dict(_VERIFY_DIAG_EMPTY)
