from __future__ import annotations
# patterns.py — 元 agent-flow.py の 2558-2924 行目（機械分割・内容無改変）。
# 単体 import しない。agent_flow/__init__.py が共有名前空間へ順に exec 合成する。
# --------------------------------------------------------------------------
# ワークフローパターンのカタログ（7 パターン）
# --------------------------------------------------------------------------
# 最初の 6 つは Claude Dynamic Workflows の 6 パターン、map-reduce は agent-flow が
# 追加した 7 つ目の正規パターン（split→実行時に map×N を動的展開→reduce）。
# orchestrator はこのカタログを知っていて、要求に応じてパターンの組み合わせと
# 並列数（fan-out 幅）を決め、タスクグラフを形作る。各ノードには kind を付け、
# kind に応じて worker の実行プロンプトと評価役の継続判断が変わる。
PATTERNS = {
    "classify-and-act": "1 つの分類エージェントが種別を判定し、結果に応じて適切な専門タスクへ振り分ける（ルーティング）。",
    "fan-out-and-synthesize": "大きな仕事を独立な小片に分割し並列実行、最後に統合ノードでまとめる。",
    "adversarial-verification": "生成ノードの成果を別の検証ノードが批判的にチェックし、問題があれば作り直す。",
    "generate-and-filter": "候補を多数（並列）生成し、フィルタノードが基準を満たすものだけ残す。",
    "tournament": "複数案を並列生成し、判定ノードが比較して最良案を選ぶ。",
    "loop-until-done": "完了条件（テスト通過・指摘なし・品質達成）を満たすまで生成と検証を反復する。",
    "map-reduce": "split ノードが入力をリスト化し、実行時に要素数ぶんの map を動的に展開して "
                  "reduce で集約する（データ駆動の fan-out。件数を事前に固定しない）。",
}
# ノード種別: work=通常実行 / generate=候補生成 / classify=分類 / synthesize=統合 /
#            verify=検証 / filter=絞り込み / judge=最良選択 / reduce=構造化データの集約 /
#            split=リスト化（データ駆動 fan-out の起点）/ map=要素ごとの処理
PATTERN_LIST = list(PATTERNS)

# 有効なノード kind。planner（エージェント）が未知 kind を出したら work に丸める。
VALID_KINDS = {"work", "generate", "classify", "synthesize", "verify",
               "filter", "judge", "reduce", "split", "map"}

# 構造化データ（data）を成果として意図する kind。これら以外（work/generate/
# classify/synthesize）の自由記述出力では、散文中に紛れた JSON 風断片を data に
# 昇格させない（例: 本文の "issues": [] を空リスト data と誤抽出して下流を汚す事故を防ぐ）。
STRUCTURED_KINDS = {"split", "map", "reduce", "filter", "judge", "verify"}


def _coerce_tasks(raw, existing=()):
    """planner/評価役（エージェント）の生出力をタスク dict に正規化する。
    id 重複除去・既存 id 回避・不正 kind の work 丸め・deps の文字列化を行う。"""
    seen = set(existing)
    out = []
    for i, t in enumerate(raw or []):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i+1}")
        if tid in seen:
            continue
        seen.add(tid)
        kind = str(t.get("kind", "work"))
        if kind not in VALID_KINDS:
            kind = "work"
        node = {
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        }
        out.append(node)
    return out


def _first_line(text: str, limit: int = 48) -> str:
    """要求の先頭の非空行を limit 文字までで返す（イシューのタイトル等に使う簡潔な見出し）。
    構造化された複数行の要求でも、見出しを 1 行に保ち本来の目的が読めるようにする。"""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return text.strip()[:limit]


def plan_stub(request: str):
    """kiro-cli 無しの簡易分解。

    区切り記号で依存も表現:
      ';' / 改行 … 独立（並列）タスクの境界。**ただし改行は空行を含まないフラットな簡易
                   リストのときだけ区切りとみなす**（後述）。
      '->'        … 逐次依存チェーン（各タスクが直前のタスクに依存）

    区切り記号が無い単一文字列ならタスク数をランダム（2−5件）で決める。

    改行の扱い: 空行（段落 = "\\n\\n"）を含む**構造化された要求**（build_request が組み立てる
    charter 文脈・完了条件つきの要求など）は 1 件の要求として扱い、行ごとに細切れのタスクへ
    分割しない。さもないと対象リポジトリ一覧などの 1 行 1 行が別タスク（=別イシュー）になり、
    gitlab executor のタイトル/本文が文脈行で埋まってしまう。**';' / '->' の区切りも構造化
    要求では解釈しない**（verify コマンドや誘導・レビュー記述の本文に普通に混ざるため。
    区切りのミニ言語はフラットな 1 行/リスト要求専用）。空行の無いフラットなリスト
    （例 "task1\\ntask2\\ntask3"）は従来どおり改行を区切りとして扱う。"""
    structured = "\n\n" in request
    if structured:
        segments = [request.strip() or "no-op"]
    else:
        segments = [s.strip() for s in request.replace("\n", ";").split(";") if s.strip()]
        if not segments:
            segments = [request.strip() or "no-op"]
    # 単一セグメントかつ依存記号（'->')も無い場合はタスク数をランダム展開
    # （構造化要求は常にここを通る＝本文中の '->' を依存とは解釈しない）
    if len(segments) == 1 and (structured or "->" not in segments[0]):
        n = random.randint(2, 5)
        base = _first_line(segments[0])   # 構造化要求でも見出しを 1 行に保つ（文脈行で埋めない）
        segments = [f"{base}（サブタスク{j + 1}）" for j in range(n)]
    tasks = []
    idx = 0
    for seg in segments:
        chain = [c.strip() for c in seg.split("->") if c.strip()]
        prev = None
        for goal in chain:
            idx += 1
            tid = f"t{idx}"
            tasks.append({"id": tid, "goal": goal, "deps": [prev] if prev else [], "kind": "work"})
            prev = tid
    return tasks


def _detect_pattern(request: str) -> str:
    t = request.lower()
    table = [
        ("classify-and-act", ["classif", "route", "routing", "ルーティング", "分類", "振り分け", "triage", "トリアージ"]),
        ("map-reduce", ["それぞれ", "各", "per item", "per-item", "分割して", "一覧", "列挙", "map-reduce", "map reduce", "件ごと", "ごとに"]),
        ("tournament", ["tournament", "トーナメント", "対戦", "ベスト", "best of", "最良", "勝ち抜き"]),
        ("generate-and-filter", ["filter", "フィルタ", "候補", "絞り込", "candidate", "ふるい"]),
        ("adversarial-verification", ["verify", "検証", "レビュー", "review", "adversar", "批判", "critique", "監査"]),
        ("loop-until-done", ["loop", "until", "繰り返", "反復", "直るまで", "tests pass", "通るまで", "完了まで"]),
    ]
    for name, kws in table:
        if any(k in t for k in kws):
            return name
    return "fan-out-and-synthesize"


def _parallelism(request: str, default: int) -> int:
    m = re.search(r"[x×]\s*(\d+)", request) or re.search(r"並列\s*(\d+)", request)
    if m:
        return max(1, min(8, int(m.group(1))))
    return max(2, min(6, default))


# --------------------------------------------------------------------------
# 分解の粒度（granularity）— 設定ファイルで調整。coarse=現状 / fine=1段細かい /
#   finest=2段細かい（既定）。factor は並列ノード数の倍率＋プロンプトの分解指示に効く。
# --------------------------------------------------------------------------
GRANULARITY_FACTORS = {"coarse": 1, "fine": 2, "finest": 3}


def granularity_factor(level: "str | None") -> int:
    """粒度レベルを倍率（1/2/3）に。未知値は既定（finest=3）。"""
    return GRANULARITY_FACTORS.get((level or "finest").lower(), 3)


def scale_parallelism(par: int, level: "str | None") -> int:
    """並列ノード数を粒度倍率でスケールする（細かいほど多く・上限 16）。"""
    return max(1, min(16, int(par) * granularity_factor(level)))


def _explicit_parallelism(request: str) -> bool:
    """要求に並列数が明示（"x3"/"並列3"）されているか。明示なら粒度倍率を効かせない。"""
    return bool(re.search(r"[x×]\s*\d+", request) or re.search(r"並列\s*\d+", request))


def maybe_scale_parallelism(request: str, par: int, level: "str | None") -> int:
    """要求に明示が無いときだけ並列数を粒度倍率でスケールする（明示指定は尊重）。"""
    return par if _explicit_parallelism(request) else scale_parallelism(par, level)


def granularity_directive(level: "str | None") -> str:
    """プランナーへ渡す分解の細かさ指示。coarse は空（現状どおり）。"""
    f = granularity_factor(level)
    if f <= 1:
        return ""
    unit = "1ファイル/1関数/1観点" if f >= 3 else "意味のある最小単位"
    return (f"分解の粒度: 通常より細かく、各タスクを{unit}まで原子的に分解すること。"
            f"目安は通常の約{f}倍の数の小さなタスク（ただし無意味な細分化・重複は避け、"
            "各タスクは独立に検証可能に保つこと）。")


def _strategy_to_graph(pattern: str, request: str, par: int, review: bool = False):
    """選んだパターンを初期タスクグラフ（kind 付き）へ落とし込む。"""
    short = _first_line(request)   # 見出しは先頭の非空行（構造化要求でも目的が 1 行で読める）
    if pattern == "classify-and-act":
        # 分類ノードのみ。専門タスクは分類結果を見て継続段階で追加（ルーティング）
        return [{"id": "classify", "goal": f"分類: {short}", "deps": [], "kind": "classify"}]
    if pattern == "map-reduce":
        # split ノードのみ。map（要素ごと）と reduce は実行時に動的展開（データ駆動 fan-out）
        return [{"id": "split1", "goal": f"分解: {short}", "deps": [], "kind": "split"}]
    if pattern == "generate-and-filter":
        gens = [{"id": f"g{i+1}", "goal": f"候補{i+1}: {short}", "deps": [], "kind": "generate"}
                for i in range(par)]
        return gens + [{"id": "filter", "goal": "候補を基準でフィルタ",
                        "deps": [g["id"] for g in gens], "kind": "filter"}]
    if pattern == "tournament":
        gens = [{"id": f"c{i+1}", "goal": f"案{i+1}: {short}", "deps": [], "kind": "generate"}
                for i in range(par)]
        return gens + [{"id": "judge", "goal": "比較して最良案を選ぶ",
                        "deps": [g["id"] for g in gens], "kind": "judge"}]
    if pattern == "adversarial-verification":
        return [{"id": "gen1", "goal": short, "deps": [], "kind": "generate"},
                {"id": "verify1", "goal": "成果を批判的に検証", "deps": ["gen1"], "kind": "verify"}]
    if pattern == "loop-until-done":
        return [{"id": "work1", "goal": short, "deps": [], "kind": "work"},
                {"id": "check1", "goal": "完了条件を確認", "deps": ["work1"], "kind": "verify"}]
    # fan-out-and-synthesize（既定）: 並列ノード + （任意で gate）+ 統合ノード
    gens = plan_stub(request)
    if len(gens) < 2:  # 単一要求なら par 個に展開
        gens = [{"id": f"t{i+1}", "goal": f"{short}（観点{i+1}）", "deps": [], "kind": "work"}
                for i in range(par)]
    gen_ids = [g["id"] for g in gens]
    if review:
        # 統合前の事前チェック / 敵対的レビュー（adversarial-verification との複合）。
        # 統合ノードは成果（gens）＋ gate に依存し、gate 通過後に gens を統合する。
        gate = {"id": "gate", "goal": "統合前レビュー（成果を検証）",
                "deps": gen_ids, "kind": "verify"}
        synth = {"id": "synth", "goal": f"統合: {short}",
                 "deps": gen_ids + ["gate"], "kind": "synthesize"}
        return gens + [gate, synth]
    return gens + [{"id": "synth", "goal": f"統合: {short}",
                    "deps": gen_ids, "kind": "synthesize"}]


def plan_strategy_stub(request: str, review="auto", granularity="finest"):
    """要求からパターンと並列数を選び、初期グラフを作る（LLM 無し版）。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。
    granularity で並列ノード数（=分解の細かさ）をスケールする。"""
    pattern = _detect_pattern(request)
    base = plan_stub(request)
    par = maybe_scale_parallelism(request, _parallelism(request, len([t for t in base if not t["deps"]])),
                                  granularity)
    review = _review_decision(review, [pattern])
    tasks = _strategy_to_graph(pattern, request, par, review)
    patterns = [pattern] + (["adversarial-verification"] if review and pattern != "adversarial-verification" else [])
    strategy = {"patterns": patterns, "parallelism": par, "review": review,
                "reason": f"stub heuristic → {pattern}（粒度 {granularity}）"
                          + ("（統合前レビュー有）" if review else "")}
    return strategy, tasks


def plan_strategy_agent(request: str, model: str | None, review="auto", granularity="finest"):
    """kiro-cli にパターン選択・並列数・初期グラフを決めさせる。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効。
    granularity で分解の細かさを指示し、返ってきた並列数も粒度倍率でスケールする。
    ワークスペース（唯一の書込先）は run 単位なので、ノードへの repo 割当はしない。"""
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    compose = ("必要なら複数パターンを多段に複合してよい（例: classify-and-act の各分岐を "
               "fan-out-and-synthesize にする / generate-and-filter の通過案で tournament を行う）。")
    # 明示 OFF でなければレビューの意図を planner に伝える（最終的な有効/無効は
    # 返ってきた patterns を見て _review_decision で確定する）。
    review_note = ("統合（synthesize/reduce）を伴うパターンでは、集約の前に verify ノードを 1 つ挟み、"
                   "事前チェック・敵対的レビューを行ってください。" if review is not False else "")
    gran_note = granularity_directive(granularity)
    prompt = (
        "あなたは分散 Dynamic Workflow の計画役です。以下のワークフローパターンを知っています:\n"
        f"{catalog}\n\n"
        "patterns に書けるのは上記 7 つのパターン名だけです。派生語・同義語は使わず、"
        "近いものは必ず上記の正規名へ読み替えてください（例: 'panel of verifiers'→adversarial-verification）。\n"
        + (gran_note + "\n" if gran_note else "")
        + f"要求に最も適したパターンと並列数を選び、{compose}{review_note}"
        "それを反映した初期タスクグラフを作ってください。各タスクには kind を付けます"
        "（kind はノード種別であってパターン名ではありません。patterns には書かないこと）: "
        "work/generate/classify/synthesize/verify/filter/judge/reduce/split"
        "（reduce=構造化データの集約 / split=リスト化してデータ駆動 fan-out の起点）。"
        "重要: map-reduce では split ノードを1つだけ置き、要素ごとの map と reduce は"
        " split 完了後に実行時へ動的展開されるので、グラフに静的に書かないこと"
        "（split→work→reduce のような固定チェーンにすると並列展開されない）。"
        "並列にできるタスクは deps を空に、順序や統合が要るものは deps に先行 id を入れます。"
        "依存は既存タスク id のみ、循環は作らないこと。\n"
        + "出力は JSON オブジェクトのみ:\n"
        '{"patterns": ["..."], "parallelism": N, "reason": "...", '
        '"tasks": [{"id": "t1", "goal": "...", "deps": [], "kind": "work"}]}\n\n'
        f"要求: {request}"
    )
    def _interpret(data):
        # planner がオブジェクトでなくベア配列を返すことがある → tasks とみなす
        if isinstance(data, list):
            data = {"tasks": data}
        tasks = _coerce_tasks(data.get("tasks"))
        if not tasks:
            raise ValueError("tasks 空")
        patterns = [p for p in (data.get("patterns") or []) if p in PATTERNS] or ["fan-out-and-synthesize"]
        strategy = {
            "patterns": patterns,
            "parallelism": maybe_scale_parallelism(request, int(data.get("parallelism", 2) or 2), granularity),
            "review": _review_decision(review, patterns),
            "reason": str(data.get("reason", "")),
        }
        return strategy, tasks

    text = None
    try:
        text = run_agent(prompt, model, purpose="planner")
        return _interpret(extract_json(text))
    except Exception as e:  # noqa: BLE001
        # 出力契約違反（JSON 崩れ・tasks 空）→ レイヤ2: 契約違反を指摘して修復再呼び出し。
        # 修復でも解釈できなければ従来どおり stub の戦略に倒す（一時エラーは run_agent 内の
        # レイヤ1 が既に再試行済み＝ここへ来た時点で透明リトライは尽きている）。
        if text is not None:
            repaired = _repair_json_output(prompt, text, "planner", str(e), model)
            if repaired is not None:
                try:
                    return _interpret(repaired)
                except Exception:  # noqa: BLE001
                    pass
        return plan_strategy_stub(request, review, granularity)


def _find_skill_script(skill: str, script: str):
    """スキルの scripts/{script} を探す（flow-planner / flow-worker 共通）。
    検索順: .github/skills/{skill}/ → git root/.github/skills/ → ~/.agent/skills/ → ~/.kiro/skills/ → {skill_home}/"""
    candidates = []
    # ワークスペース内
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, ".github", "skills", skill, "scripts", script))
    # リポジトリルート（git rev-parse で探す）
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        ).stdout.strip()
        if root:
            candidates.append(os.path.join(root, ".github", "skills", skill, "scripts", script))
    except Exception:  # noqa: BLE001
        pass
    # ~/.agent/skills → ~/.kiro/skills（共有スキル配置）を確認
    for skills_home in ("~/.agent/skills", "~/.kiro/skills"):
        candidates.append(os.path.join(os.path.expanduser(skills_home), skill, "scripts", script))
    # skill-registry.json から skill_home を読む
    for agent_dir in [os.path.expanduser("~/.agent"), os.path.expanduser("~/.kiro"),
                      os.path.expanduser("~/.copilot"),
                      os.path.expanduser("~/.claude"), os.path.expanduser("~/.codex")]:
        reg = os.path.join(agent_dir, "skill-registry.json")
        if os.path.isfile(reg):
            try:
                with open(reg, encoding="utf-8") as f:
                    data = json.load(f)
                home = data.get("skill_home", "")
                if home:
                    candidates.append(os.path.join(home, skill, "scripts", script))
            except Exception:  # noqa: BLE001
                pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _find_flow_planner_script():
    """flow-planner スキルの plan.py を探す。"""
    return _find_skill_script("flow-planner", "plan.py")


def plan_strategy_flow_planner(request: str, model: str | None, review="auto", granularity="finest"):
    """flow-planner スキルの3段パイプラインを呼び出す。
    スキルが見つからない / 失敗した場合は plan_strategy_agent にフォールバック。
    granularity はスキルへ `--granularity` で渡し、返ってきた並列数も粒度倍率でスケールする。"""
    script = _find_flow_planner_script()
    if not script:
        # flow-planner スキル未インストール → エージェント planner にフォールバック
        return plan_strategy_agent(request, model, review, granularity)
    # 計画に使う CLI/モデルは planner の設定（agents: planner: {agent_cli, model}）に従わせる。
    # スキル側の既定は kiro-cli だが、それを黙って使うと agent_cli を claude/codex にしていても
    # 計画だけ kiro-cli で走り、kiro-cli が使えない環境では毎回失敗して stub へ落ちていた。
    cli, model_ov = _agent_for("planner")
    cmd = [sys.executable, script, request, "--granularity", str(granularity),
           "--agent-cli", cli]
    model = model_ov or model
    if model:
        cmd += ["--model", model]
    if isinstance(review, bool):
        cmd += ["--review", "true" if review else "false"]
    else:
        cmd += ["--review", str(review)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:500])
        data = json.loads(proc.stdout)
        strategy = data.get("strategy", {})
        tasks = _coerce_tasks(data.get("tasks", []))
        if not tasks:
            raise ValueError("flow-planner returned empty tasks")
        # strategy を正規化
        patterns = [p for p in (strategy.get("patterns") or []) if p in PATTERNS] or ["fan-out-and-synthesize"]
        final_strategy = {
            "patterns": patterns,
            "parallelism": maybe_scale_parallelism(request, int(strategy.get("parallelism", 2) or 2), granularity),
            "review": _review_decision(review, patterns) if not isinstance(strategy.get("review"), bool)
                      else strategy["review"],
            "reason": f"[flow-planner] {strategy.get('reason', '')}（粒度 {granularity}）",
        }
        return final_strategy, tasks
    except Exception:  # noqa: BLE001 — flow-planner 失敗時はエージェント planner にフォールバック
        return plan_strategy_agent(request, model, review, granularity)

