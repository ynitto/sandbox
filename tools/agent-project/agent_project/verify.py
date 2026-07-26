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

    後方互換の優先順位を 1 か所に固定する:
      1. `- acceptance:` 行（複数可）… S6 の backlog-planner が生成し人が直す一次表現
      2. 無ければ `- accept:`（自然文 1 行）を 1 項目のチェックリストとして扱う
      3. どちらも無ければ空（＝決定的 verify の fast path だけ・従来どおり）

    `Task.extra` は (key, value) のリストなので、同名キーの複数行はそのまま往復する
    （`- {k}: {v}` で書き戻される）。スキーマもパーサも変えずに済む。
    """
    lines = [v.strip() for k, v in task.extra if k == "acceptance" and str(v).strip()]
    if lines:
        return lines
    accept = str(dict(task.extra).get("accept", "") or "").strip()
    return [accept] if accept else []


def has_verify_plan(task: "Task") -> bool:
    """concrete な verify か、検証の材料（acceptance / accept / verify_template）を持つか。"""
    if task.verify:
        return True
    ex = dict(task.extra)
    return bool(task_acceptance(task) or ex.get("verify_template", "").strip())


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


def verifier_input(cfg: "Config", task: "Task", vcwd: "Path") -> dict:
    """backlog-verifier スキルへ渡す入力（契約は .github/skills/backlog-verifier/SKILL.md）。"""
    ws = _workspace_spec_for(cfg, task) or {}
    ex = dict(task.extra)
    return {
        "task": {"id": task.id, "title": task.title,
                 "why": ex.get("why", ""), "desc": ex.get("desc", ""),
                 "scope": ex.get("scope", ""), "out_of_scope": ex.get("out_of_scope", "")},
        "acceptance": task_acceptance(task),
        "workspace": {"url": str(ws.get("url") or ""),
                      "branch": str(ws.get("branch") or task_branch_name(cfg, task)),
                      "base": str(ws.get("target") or ws.get("base") or ""),
                      "path": str(ws.get("path") or "")},
        "repo_context": detect_repo_context(vcwd),
        "rules": project_rules_context(cfg, limit=600),
        "recipes": find_verify_recipes(cfg, task),
        "feedback": str(ex.get("feedback", "") or ""),
        "side_effects": str(getattr(cfg, "verify_side_effects", "workspace") or "workspace"),
        # 解決済みの制約文も渡す（本文が正典・スキルは受け取る）。旧版のスキルはこのキーを
        # 読まず自前の表へ落ちるので、追加しても壊れない。
        "side_effects_text": verify_side_effect_rule(
            getattr(cfg, "verify_side_effects", "workspace")),
        # 差分の常設基準も同じ扱い（P2-5）。レポートの基準列（`verification_report_md`）と
        # エージェントが見る基準列が**同じ文字列から**組まれることを、入力で保証する。
        "diff_criterion": DIFF_CRITERION,
    }


def _prompt_block(title: str, body: str) -> str:
    """空でなければ `## <見出し>` の節にする（スキル側 `_block` と同じ）。"""
    body = str(body or "").strip()
    return f"\n## {title}\n{body}\n" if body else ""


def _builtin_verifier_prompt(spec: dict) -> str:
    """スキルが見つからないときの組み込みプロンプト（スキルと同じ入力・同じ出力契約）。

    スキルを必須にしないのは、検証が止まると全タスクが人へ倒れるから。育てる場所は
    スキル側（`.github/skills/backlog-verifier/`）だが、**入力は取りこぼさない**——
    以前はタイトルと受入基準しか使わず、副作用制約（`verify_side_effects`）が
    スキル未導入のノードで黙って落ちていた。検証は失敗するとリトライで何度も走るので、
    制約が落ちた回数だけ副作用が累積する。品質材料（rules / repo_context / recipes /
    feedback）も `verifier_input` が既に組んでいるので、載せない理由が無い。

    節の見出しと順序はスキルと揃える——検証レポートに出る本文を人が読み比べるとき、
    経路によって見出しが違うと「別の検証をした」ように見える。
    """
    task = spec.get("task") or {}
    ws = spec.get("workspace") or {}
    criteria = list(spec.get("acceptance") or []) + [
        str(spec.get("diff_criterion") or "").strip() or DIFF_CRITERION]
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(criteria))
    side = str(spec.get("side_effects_text") or "").strip() \
        or verify_side_effect_rule(spec.get("side_effects"))
    recipes = "\n".join(f"- `{r}`" for r in (spec.get("recipes") or [])[:10])

    head = (
        "あなたは成果物の検証エージェントです。下の受入基準それぞれについて、"
        "**実際にコマンドを実行して**充足を確かめ、証跡付きで判定してください。\n\n"
        "重要な原則:\n"
        "- 判定の根拠は **実行した結果**です。コードを読んだ印象や「妥当に見える」は根拠になりません。\n"
        "- 確かめられなかった基準は正直に fail か unverifiable にしてください。\n"
        "- **成果物を直さないでください。**（作業ツリーへの変更は破棄されます）\n"
        f"- {side}\n"
    )
    body = (
        "\n## タスク\n"
        f"- id: {task.get('id', '')}\n"
        f"- title: {task.get('title', '')}\n"
        + (f"- why: {task['why']}\n" if task.get("why") else "")
        + (f"- 作業概要: {task['desc']}\n" if task.get("desc") else "")
        + (f"- 変更してよい範囲: {task['scope']}\n" if task.get("scope") else "")
        + (f"- やらないこと: {task['out_of_scope']}\n" if task.get("out_of_scope") else "")
        + "\n## 検証する場所\n"
        f"- リポジトリ: {ws.get('url') or '(ワークスペース)'}\n"
        f"- 成果ブランチ: {ws.get('branch', '')}（比較元: {ws.get('base', '')}）\n"
        + (f"- 対象パス: {ws['path']}\n" if ws.get("path") else "")
        + f"\n## 受入基準（この順に判定する）\n{numbered}\n"
    )
    extras = (
        _prompt_block("参考: 過去に有効だった検証コマンド"
                      "（まずこれを試す。環境が違えば通らないので鵜呑みにしない）", recipes)
        + _prompt_block("前回の失敗", spec.get("feedback"))
        + _prompt_block("リポジトリの文脈", spec.get("repo_context"))
        + _prompt_block("プロジェクトの恒常ルール", spec.get("rules"))
    )
    tail = (
        "\n## 出力\n"
        "まず人が読むための本文を Markdown で書いてください（基準ごとに、何を実行して何が"
        "分かったか）。**そのあと、末尾に次の形の JSON を必ず 1 つ添えてください。**\n"
        '{"criteria": [{"id": 1, "verdict": "pass|fail|unverifiable", '
        '"evidence": {"commands": [], "output": "", "files": []}, "note": ""}]}\n'
        f"- criteria は上の基準と同じ順で {len(criteria)} 件すべて含めてください。\n"
        "- verdict=pass には **必ず** commands か files の証跡を入れてください"
        "（証跡の無い pass は機械的に fail へ落とされます）。\n"
        "- 環境にツールが無い等で確かめられない基準は unverifiable にし、"
        "note に何が足りないかを書いてください（リトライは消費されません）。\n"
    )
    return head + body + extras + tail


def build_verifier_prompt(cfg: "Config", spec: dict) -> str:
    """検証プロンプトを組み立てる（スキル優先・見つからなければ組み込み）。"""
    skill = str(getattr(cfg, "verifier_skill", "backlog-verifier") or "backlog-verifier")
    script = find_skill_script(skill, "prompt.py")
    if script:
        try:
            proc = subprocess.run([sys.executable, script], input=json.dumps(spec, ensure_ascii=False),
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
            print(f">>> 警告: {skill} スキルがプロンプトを返しませんでした（組み込みへ）: "
                  f"{(proc.stderr or '').strip()[:200]}", file=sys.stderr)
        except (OSError, subprocess.SubprocessError) as e:
            print(f">>> 警告: {skill} スキルを実行できませんでした（組み込みへ）: {e}", file=sys.stderr)
    return _builtin_verifier_prompt(spec)


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


# --- 検証レシピ（find_learned_verify の置き換え） -----------------------------------------
# verifier が見つけた有効なコマンド列を保存し、次回の**参考情報**として渡す。
# **決定的ゲートには昇格させない**——環境が変われば壊れるものを done の唯一の根拠にしない、
# というのが「昇格したコマンドの良し悪しを人が判断できない」問題への答え。

def _recipe_fingerprint(task: "Task") -> str:
    norm = re.sub(r"[^0-9a-z]+", "-", str(task.title or "").lower()).strip("-")
    return (norm or "task")[:60]


def recipes_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "verify-recipes"


def find_verify_recipes(cfg: "Config", task: "Task") -> "list[str]":
    """このタスクに近い過去の検証コマンド（無ければ空）。参考であってゲートではない。"""
    p = recipes_dir(cfg) / f"{_recipe_fingerprint(task)}.md"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip("- `").rstrip("`") for ln in lines if ln.startswith("- `")][:10]


def save_verify_recipes(cfg: "Config", task: "Task", result: dict) -> None:
    """pass した基準の証跡コマンドをレシピとして保存する（best-effort）。"""
    cmds: "list[str]" = []
    for c in result.get("criteria") or []:
        if c["verdict"] == "pass":
            cmds.extend(c["evidence"]["commands"])
    uniq = list(dict.fromkeys(x.strip() for x in cmds if x.strip()))[:10]
    if not uniq:
        return
    try:
        p = recipes_dir(cfg) / f"{_recipe_fingerprint(task)}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# 検証レシピ: {task.title}\n\n"
                     "次回の verifier への **参考**（まずこれを試す）。環境が変われば壊れるので、\n"
                     "決定的な verify へは昇格させない。\n\n"
                     + "\n".join(f"- `{c}`" for c in uniq) + "\n", encoding="utf-8")
    except OSError:
        pass


def run_verifier(cfg: "Config", task: "Task", vcwd: "Path",
                 agent_run=None) -> "tuple[dict, str]":
    """検証エージェントを 1 回走らせ、(判定レコード, 本文) を返す。

    LLM 呼び出しは 1 回（1 settle = 1 run）。呼び出しに失敗したら全基準 unverifiable に倒す
    ——「検証できなかった」を fail（＝リトライを焼く）と混同しない。
    """
    spec = verifier_input(cfg, task, vcwd)
    criteria = list(spec["acceptance"]) + [DIFF_CRITERION]
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="verify"))
    prompt = build_verifier_prompt(cfg, spec)
    try:
        body = run(prompt, cfg.model)
    except Exception as e:  # noqa: BLE001 — CLI 不在・上限・タイムアウトは環境要因
        body = ""
        result = {"criteria": [{"id": i + 1, "text": c, "verdict": "unverifiable",
                                "evidence": {"commands": [], "output": "", "files": []},
                                "note": f"検証エージェントを実行できませんでした: {str(e)[:200]}"}
                               for i, c in enumerate(criteria)],
                  "pass": 0, "fail": 0, "unverifiable": len(criteria), "ok": False}
        return result, body
    return normalize_verification(body, criteria), body


# ---------------------------------------------------------------------------
# verify の用意（人が書く負担を減らす）。完了条件は決定的なシェルが正典だが、人が書くのは難しい。
#   - `- verify_template: <名前> :: <引数...>` … 決定的に展開（エージェント不要）。
#   - `- accept: <自然言語の完了条件>`         … エージェントが決定的 verify を合成（偽 done 防止規則を織込）。
# どちらも最終的に concrete な `verify`（終了コード0=PASS）になり、done は verify のみが根拠の不変条件を保つ。
# 合成/展開できなければ verify は空のまま＝従来どおり人へ（done 不能）。
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


def detect_repo_context(workdir: "Path") -> str:
    """テスト/ビルド基盤を決定的に検出し、合成 verify のヒント文にする（grep 退化を防ぐ）。
    package.json scripts・pytest/pyproject・Makefile ターゲット・go/cargo 等を軽く走査（有界）。"""
    hints: list = []
    try:
        pj = workdir / "package.json"
        if pj.exists():
            data = json.loads(pj.read_text(encoding="utf-8"))
            scripts = list((data.get("scripts") or {}).keys())[:8]
            hints.append("package.json（npm/yarn）: scripts=" + (", ".join(scripts) or "なし"))
    except (OSError, ValueError):
        pass
    if (workdir / "pytest.ini").exists() or (workdir / "pyproject.toml").exists() \
            or (workdir / "tox.ini").exists() or (workdir / "tests").is_dir():
        hints.append("Python（pytest 等）: `pytest -q` が使えることが多い")
    mk = workdir / "Makefile"
    if mk.exists():
        try:
            targets = re.findall(r"^([a-zA-Z0-9_.-]+):", mk.read_text(encoding="utf-8"), re.M)[:10]
            hints.append("Makefile: targets=" + (", ".join(targets) or "なし"))
        except OSError:
            pass
    if (workdir / "go.mod").exists():
        hints.append("Go: `go test ./...` / `go build ./...`")
    if (workdir / "Cargo.toml").exists():
        hints.append("Rust: `cargo test` / `cargo build`")
    return "\n".join(f"- {h}" for h in hints)[:800]


def _synth_verify_prompt(title: str, accept: str, hint: str = "", repo_ctx: str = "",
                         retry_note: str = "") -> str:
    extra = ""
    if retry_note:
        extra += f"\n**前回の合成は不採用でした（{retry_note}）。今度は必ず改善すること。**\n"
    if repo_ctx:
        extra += ("\nこのリポジトリで検出したテスト/ビルド基盤（可能ならこれを使い、存在チェックの grep へ"
                  f"退化させない）:\n{repo_ctx}\n")
    if hint:
        extra += ("\n過去の類似タスクで人が示した『done の見方』（参考にしてよいが、望む最終状態/差分を"
                  f"検査する原則は保つ）:\n- {hint}\n")
    return (
        "次のタスクの『完了条件（自然言語）』を、**決定的なシェルコマンド**に変換してください。"
        "終了コード 0 を PASS とみなします。\n"
        "規則: ①「履歴」ではなく「望む最終状態 / 差分」を検査する"
        "（`git log|grep` で過去コミットに当てない）②差分を見るなら環境変数 `$KIRO_BASE_REV`"
        "（act 前の HEAD）を使い `git log \"$KIRO_BASE_REV\"..HEAD ...` の形にする"
        "③外部状態に依存せず再現可能にする。④単なる存在 grep や恒真式に退化させず、"
        "可能ならテスト/ビルドコマンドで実挙動を確かめる。"
        "⑤このコマンドは **POSIX sh** で実行される。`powershell.exe`/`pwsh`/`cmd.exe` など"
        "Windows シェルは使わず、`git`・テストランナー等のクロスプラットフォーム CLI を使う。\n"
        f"タスク: {title}\n完了条件: {accept}\n{extra}\n"
        "出力はコマンド 1 行のみ（説明・コードフェンス不要）。検証コマンドを書けない場合は空行を返す。")


# 全角の文/句読点。シェルコマンドにはまず現れず、自然言語（散文・拒否文）の強い指標。
_PROSE_PUNCT = "。、！？；：「」『』（）"

# 常に真＝何も検証しない恒真式。合成 verify がこれに退化すると done の唯一根拠が意味を失う。
_TAUTOLOGY_RE = re.compile(
    r"^(?:true|:|/bin/true"
    r"|test\s+1\s*=\s*1|test\s+-n\s+.\S*|\[\s+1\s*=\s*1\s+\]"
    r"|echo\b.*|printf\b.*|exit\s+0)$")


def _verify_is_degenerate(cmd: str) -> bool:
    """合成 verify が「常に PASS＝何も検証しない」恒真式に退化していないか（決定的スクリーン）。
    red-green（変更前 fail・変更後 pass）を実行で確かめられない enqueue 時点でも、明白な恒真式は弾く。
    複合（; && || | 含む）は個別判定が難しいので通し、単純トークンの恒真だけを弾く（false negative 寄り）。"""
    s = (cmd or "").strip().strip(";").strip()
    if not s:
        return True
    if any(op in s for op in ("&&", "||", "|", ";", "\n")):
        return False                              # 複合は退化と断定しない（誤棄却を避ける）
    return bool(_TAUTOLOGY_RE.match(s))


# verify は run_verify が `set -x` + `$KIRO_BASE_REV` 差分など **POSIX sh** 前提で実行する
# （subprocess.run(f"set -x\\n{cmd}", shell=True)）。エージェント CLI が Windows 上で動くと
# `powershell.exe -Command ...` / `pwsh` / `cmd.exe /c ...` を返すことがあり、これは sh 実行で
# 必ず壊れる。フルパス（/ または \\ 区切り）・.exe 付きも同一視して不採用にする。
_WINDOWS_SHELL_RE = re.compile(r"^(?:powershell|pwsh|cmd)(?:\.exe)?$", re.IGNORECASE)


def _is_windows_shell_command(cmd: str) -> bool:
    """先頭トークンが Windows シェル（powershell/pwsh/cmd、.exe 付き・フルパス可）か。"""
    s = (cmd or "").strip()
    if not s:
        return False
    token = s.split(maxsplit=1)[0]
    bare = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return bool(_WINDOWS_SHELL_RE.fullmatch(bare))


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


_FENCE_OPEN_RE = re.compile(r"```(\w*)\s*$")


def _code_fence_lines(out: str) -> list[str]:
    """Markdown コードフェンス内の行を、ブロックの出現順に返す。

    開始フェンスは言語タグの有無を問わない。「実行してください: ```bash」のように
    同一行にフェンスの前置き文が同居していても、行末が ``` (+言語タグ) であれば開始と
    認識する（行頭一致 startswith だけだと前置き同居ケースを取りこぼすため）。
    閉じフェンスがなければ、入力末尾までをそのブロックの内容として扱う。
    """
    fenced_lines: list[str] = []
    in_fence = False
    for line in (out or "").splitlines():
        marker = line.strip()
        if in_fence and marker == "```":
            in_fence = False
            continue
        if not in_fence and _FENCE_OPEN_RE.search(marker):
            in_fence = True
            continue
        if in_fence:
            fenced_lines.append(line)
    return fenced_lines


_SHELL_FENCE_LANGUAGE_TAGS = frozenset({"bash", "console", "sh", "shell", "zsh"})

# フェンス外では `sh -n` が英語の散文も単純コマンドとして受理するため、頻出する
# 実行語から始まる行だけを候補にする。ハイフンを含む CLI 名とパス指定も許可する。
_KNOWN_COMMAND_WORDS = frozenset({
    "awk", "bash", "cargo", "cd", "codd-gate", "diff", "docker", "find", "git", "go",
    "grep", "java", "make", "mvn", "node", "npm", "npx", "perl", "php", "pip", "pip3",
    "pnpm", "poetry", "pytest", "python", "python3", "rg", "ruby", "sed", "sh", "test", "tox",
    "uv", "yarn", "zsh",
})


_LEADING_SHELL_PROMPT_RE = re.compile(r"^\$\s+")


def _strip_leading_shell_prompt(line: str) -> str:
    """行頭のシェルプロンプト記号 `$ ` を1回だけ剥がす。
    `$(...)` や `$VAR` は `$` 直後が空白でないため対象外（誤剥離しない）。"""
    return _LEADING_SHELL_PROMPT_RE.sub("", line, count=1)


_VERIFY_COMMAND_LABEL_RE = re.compile(r"^.*?検証コマンド\s*[:：]\s*")


def _strip_leading_command_label(line: str) -> str:
    """行頭の日本語ラベル『検証コマンド:』（全角コロン可）を、変化がなくなるまで繰り返し剥がす。
    ラベルとコマンドが同一行にある形式（`検証コマンド: <command>`）を、ラベルが
    別行にある形式と同じ土俵で判定できるようにするため、コマンド判定・sh -n チェック
    の手前で適用する。行頭一致ではなく `^.*?検証コマンド` の最短一致にしているのは、
    「以下を実行してください。検証コマンド: <command>」のようにラベルの前に散文が
    同居する出力にも対応するため（`.*?` は非貪欲なので最初のラベル出現までしか消費しない）。
    繰り返し適用するのは、LLM がラベルを二重・多重に付けて返す出力（`検証コマンド: 検証
    コマンド: <command>`）を収束させるため。行内の任意のコロンではなくこの固定ラベル語
    だけを対象にするのは、`git commit -m "note: fix bug"` のようにコマンド自体に含まれる
    コロンを誤って割らないため。"""
    while True:
        stripped = _VERIFY_COMMAND_LABEL_RE.sub("", line, count=1)
        if stripped == line:
            return stripped
        line = stripped


def _has_command_like_leading_token(line: str) -> bool:
    """フェンス外の行が既知コマンド語または実行可能らしいトークンで始まるか判定する。"""
    if not line:
        return False
    token = line.split(maxsplit=1)[0]
    bare = token.rsplit("/", 1)[-1]
    return (
        bare in _KNOWN_COMMAND_WORDS
        or token.startswith(("./", "../", "/"))
        or bool(re.fullmatch(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+", bare))
    )


_TRAILING_BACKSLASH_RE = re.compile(r"\\\s*$")


def _join_continuations(lines: list[str]) -> list[str]:
    """行末バックスラッシュ `\\` による継続行を1つの論理コマンド文字列へ結合する。

    継続中でない行のうち、空行・`#` 始まりの純コメント行は結合対象にせず落とす
    （継続の起点にしない）。いったん継続に入った行（直前行が `\\` 終端）は、
    たとえ空行やコメント然とした内容でも連結対象として保持する — バックスラッシュ
    直後の行を無条件で落とすと結合済みコマンドが途中で壊れるため。戻り値は論理行
    ごとに1件のリストで、各行の末尾 `\\` は除去し、継続元と継続先はシェルの行
    継続と同じく半角スペース1つで連結する。
    """
    joined: list[str] = []
    parts: list[str] = []
    continuing = False
    for raw in lines:
        stripped = raw.strip()
        if not continuing and (not stripped or stripped.startswith("#")):
            continue
        m = _TRAILING_BACKSLASH_RE.search(stripped)
        if m:
            parts.append(stripped[: m.start()].rstrip())
            continuing = True
            continue
        parts.append(stripped)
        joined.append(" ".join(p for p in parts if p))
        parts = []
        continuing = False
    if parts:
        joined.append(" ".join(p for p in parts if p))
    return joined


def _first_executable_line(lines: list[str], *, require_shell_syntax: bool = True) -> Optional[str]:
    """候補行から最初のコマンドを返す。見つからなければ None。

    require_shell_syntax=False の場合は `_looks_like_shell_command` の sh -n 構文チェックを
    課さない。コードフェンスで明示的に区切られた行は LLM の意図（これがコマンドである）が
    明確なため、素通しで信頼する（フェンス外の地の文はこの限りでなく従来どおり厳格に見る）。
    """
    for raw_line in lines:
        line = _strip_leading_command_label(_strip_leading_shell_prompt(_strip_code(raw_line.strip())))
        if (
            line
            and not line.startswith("#")
            and line.casefold() not in _SHELL_FENCE_LANGUAGE_TAGS
            and (not require_shell_syntax or _looks_like_shell_command(line))
        ):
            return line
    return None


def _first_command_line(out: str) -> Optional[str]:
    """合成出力の先頭のコマンド行を返す。どの規則にも合わなければ None。

    コードフェンスを最優先でスキャンする: フェンスが見つかれば、フェンス内の最初の
    非空・非コメント行を無条件でコマンドとして採用する。フェンスが一つも無ければ、
    フェンス外の行を対象にした従来ロジック（既知コマンド語などの先頭トークン判定 +
    sh -n 構文チェック）へフォールバックする。行頭のシェルプロンプト記号 `$ ` および
    日本語ラベル『検証コマンド:』（ラベル単独行・`検証コマンド: <command>` のように
    コマンドと同一行の両形式・ラベルの前に散文が同居する形式・ラベルの二重/多重付与）は
    判定前に剥がす（LLM がプロンプト付き・ラベル付き・前置き散文付きでコマンド例を
    返す出力に対応するため）。

    ANSI エスケープは入口で落とす。エージェント CLI はカラーコード付きで返すことがあり、
    残したままだとフェンス開始の ``` も先頭トークン（`\x1b[36mgrep` → 既知コマンド語に
    一致しない）も認識できず、候補が 1 つも残らない。
    """
    out = strip_ansi(out)
    # 行末バックスラッシュの継続行は、候補を選ぶ前に 1 つの論理コマンドへ結合する。
    # 結合せずに行単位で選ぶと、`pytest -q \` のような**途中で切れたコマンド**が採用される
    # ——フェンス内は構文チェックを課さないので素通りし、壊れた verify がそのまま done の
    # 唯一の根拠になる（実行すれば必ず落ちるので、タスクは永久にリトライと人送りを繰り返す）。
    fenced = _first_executable_line(_join_continuations(_code_fence_lines(out)),
                                    require_shell_syntax=False)
    if fenced:
        return fenced
    lines = _join_continuations((out or "").splitlines())
    return _first_executable_line(
        [
            line
            for line in lines
            if _has_command_like_leading_token(
                _strip_leading_command_label(_strip_leading_shell_prompt(line.strip()))
            )
        ]
    )


def synth_verify(cfg: "Config", title: str, accept: str, agent_run=None,
                 hint: str = "", repo_ctx: str = "", attempts: int = 2) -> str:
    """自然言語の完了条件 accept からエージェント（エージェント CLI）が決定的 verify を合成する。
    失敗・不能・エージェント CLI 不在は空文字（→ verify 未定義のまま人へ）。テストは agent_run を注入する。
    hint（過去の類似 learn）・repo_ctx（検出したテスト/ビルド基盤）で grep 退化を抑える。
    **自己修復（多候補）**: 散文/シェル非妥当/恒真式に退化した候補は不採用とし、理由を添えて最大
    attempts 回まで再合成させる（1 回で諦めず、より良い候補を引き出す）。"""
    run = agent_run or (lambda p, m: _run_agent_cli(p, m, purpose="verify"))
    retry_note = ""
    for _ in range(max(1, attempts)):
        try:
            out = run(_synth_verify_prompt(title, accept, hint, repo_ctx, retry_note), cfg.model)
        except Exception:  # noqa: BLE001  エージェント CLI 不在・タイムアウト等は合成せず人へ
            return ""
        cand = _first_command_line(out)
        if not cand:
            retry_note = "応答に実行可能なコマンド行がなかった"; continue
        # PowerShell/cmd は sh -n を通ってしまう（valid な sh 構文）が、verify は POSIX sh で
        # 実行されるため必ず壊れる。フェンス付きで _first_command_line を素通りした場合もここで弾く。
        if _is_windows_shell_command(cand):
            retry_note = ("PowerShell/cmd は使えません（verify は POSIX sh で実行）。"
                          "git・テストランナー等のクロスプラットフォーム CLI で書くこと"); continue
        # 自然言語（説明・拒否文）を shell=True に流すと ; | && ` > rm 等が誤実行されうるため弾く。
        if not _looks_like_shell_command(cand):
            retry_note = "シェルコマンドでなかった"; continue
        # 恒真式（true / echo … 等）は done の根拠にならない＝不採用。実挙動を確かめる候補を求める。
        if _verify_is_degenerate(cand):
            retry_note = "恒真式に退化していた。テスト/ビルド/差分/最終状態で実挙動を確かめよ"; continue
        return cand
    print(f"[agent-project] verify 合成失敗: {retry_note}（task: {title}）", file=sys.stderr)
    return ""


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
