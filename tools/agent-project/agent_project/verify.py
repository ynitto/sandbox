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
    try:
        proc = subprocess.run(f"set -x\n{cmd}", shell=True, cwd=str(workdir), timeout=timeout,
                              capture_output=True, text=True,
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
    wt = tempfile.mkdtemp(prefix="kiro-redgreen-")
    try:
        add = subprocess.run(["git", "-C", str(workdir), "worktree", "add", "--detach", wt, rev],
                             capture_output=True, text=True, timeout=timeout)
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
    """合成 verify が『act 前でも PASS＝変更を弁別しない偽 done』か（red-green の red 側検査）。
    対象は verify_validate ポリシー（off/synth/all）と per-task 上書きに従う。temp clone（workspace
    タスク）は act 前ツリーが手元に無いので対象外（既存の no-progress ガードに委ねる）。判定不能は False。"""
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
        tmp = tempfile.mkdtemp(prefix="kiro-verify-")
        dest = str(Path(tmp) / "repo")
        # worker の push 先は task_branch 時の `branch`（ap/<task-id>）。無ければ MR の
        # target、さらに無ければ base。ここを target/base だけにすると、成果は ap/ に
        # あるのに main を検証して永久に NG になる（journal の @main 誤検証）。
        branch = spec.get("branch") or spec.get("target") or spec.get("base") or ""
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
        "可能ならテスト/ビルドコマンドで実挙動を確かめる。\n"
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


_VERIFY_COMMAND_LABEL_RE = re.compile(r"^検証コマンド\s*[:：]\s*")


def _strip_leading_command_label(line: str) -> str:
    """行頭の日本語ラベル『検証コマンド:』（全角コロン可）を1回だけ剥がす。
    ラベルとコマンドが同一行にある形式（`検証コマンド: <command>`）を、ラベルが
    別行にある形式と同じ土俵で判定できるようにするため、コマンド判定・sh -n チェック
    の手前で適用する。行内の任意のコロンではなくこの固定ラベル語だけを行頭一致で
    対象にするのは、`git commit -m "note: fix bug"` のようにコマンド自体に含まれる
    コロンを誤って割らないため。"""
    return _VERIFY_COMMAND_LABEL_RE.sub("", line, count=1)


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
    コマンドと同一行の両形式）は判定前に剥がす（LLM がプロンプト付き・ラベル付きで
    コマンド例を返す出力に対応するため）。

    ANSI エスケープは入口で落とす。kiro-cli はカラーコード付きで返すことがあり、
    残したままだとフェンス開始の ``` も先頭トークン（`\x1b[36mgrep` → 既知コマンド語に
    一致しない）も認識できず、候補が 1 つも残らない。
    """
    out = strip_ansi(out)
    fenced = _first_executable_line(_code_fence_lines(out), require_shell_syntax=False)
    if fenced:
        return fenced
    lines = (out or "").splitlines()
    return _first_executable_line(
        [
            line
            for line in lines
            if _has_command_like_leading_token(
                _strip_leading_command_label(_strip_leading_shell_prompt(line.strip()))
            )
        ]
    )


def synth_verify(cfg: "Config", title: str, accept: str, kiro_run=None,
                 hint: str = "", repo_ctx: str = "", attempts: int = 2) -> str:
    """自然言語の完了条件 accept からエージェント（kiro-cli）が決定的 verify を合成する。
    失敗・不能・kiro-cli 不在は空文字（→ verify 未定義のまま人へ）。テストは kiro_run を注入する。
    hint（過去の類似 learn）・repo_ctx（検出したテスト/ビルド基盤）で grep 退化を抑える。
    **自己修復（多候補）**: 散文/シェル非妥当/恒真式に退化した候補は不採用とし、理由を添えて最大
    attempts 回まで再合成させる（1 回で諦めず、より良い候補を引き出す）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="verify"))
    retry_note = ""
    for _ in range(max(1, attempts)):
        try:
            out = run(_synth_verify_prompt(title, accept, hint, repo_ctx, retry_note), cfg.model)
        except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は合成せず人へ
            return ""
        cand = _first_command_line(out)
        if not cand:
            retry_note = "応答に実行可能なコマンド行がなかった"; continue
        # 自然言語（説明・拒否文）を shell=True に流すと ; | && ` > rm 等が誤実行されうるため弾く。
        if not _looks_like_shell_command(cand):
            retry_note = "シェルコマンドでなかった"; continue
        # 恒真式（true / echo … 等）は done の根拠にならない＝不採用。実挙動を確かめる候補を求める。
        if _verify_is_degenerate(cand):
            retry_note = "恒真式に退化していた。テスト/ビルド/差分/最終状態で実挙動を確かめよ"; continue
        return cand
    print(f"[agent-project] verify 合成失敗: {retry_note}（task: {title}）", file=sys.stderr)
    return ""


def ensure_verify(cfg: "Config", task: "Task", kiro_run=None) -> bool:
    """task に concrete な verify が無ければ `verify_template`（決定的）→ `accept`（合成）の順で用意する。
    用意できたら task.verify を埋め `verify_source` を記録して True を返す（呼び出し側が persist する）。"""
    if task.verify:
        return False
    ex = dict(task.extra)
    tmpl = ex.get("verify_template", "").strip()
    if tmpl:
        cmd = expand_verify_template(tmpl)
        if cmd:
            task.verify = cmd
            task.extra.append(("verify_source", "template"))
            return True
    accept = ex.get("accept", "").strip()
    if accept:
        # ① まず実績のある検証済み verify を再利用（毎回ゼロ合成しない。red-green が別途弁別を確かめる）。
        reused = find_learned_verify(cfg, task) if cfg.learn else None
        if reused:
            task.verify = reused
            task.extra.append(("verify_source", "reused"))
            return True
        # ② 無ければ合成。過去の類似 learn（done の見方）と検出したテスト/ビルド基盤を注入し grep 退化を防ぐ。
        matched = find_learned_resolution(cfg, task) if cfg.learn else None
        hint = matched[1] if matched else ""
        repo_ctx = detect_repo_context(resolve_verify_cwd(cfg))
        rm = repo_map_context(cfg, [task.get("workspace")] if task.get("workspace") else None,
                              limit=600, max_files=1)   # 理解の要約も合成の材料に（有界）
        if rm:
            repo_ctx = (repo_ctx + "\n" if repo_ctx else "") + rm
        pr = project_rules_context(cfg, limit=400)      # 恒常ルール（テスト実行方法等）も合成に効かせる
        if pr:
            repo_ctx = (repo_ctx + "\n" if repo_ctx else "") + pr
        cmd = synth_verify(cfg, task.title, accept, kiro_run, hint=hint, repo_ctx=repo_ctx)
        if cmd:
            task.verify = cmd
            task.extra.append(("verify_source", "synth"))
            return True
    return False


def has_verify_plan(task: "Task") -> bool:
    """concrete な verify か、それを用意する材料（accept / verify_template）を持つか。"""
    if task.verify:
        return True
    ex = dict(task.extra)
    return bool(ex.get("accept", "").strip() or ex.get("verify_template", "").strip())


