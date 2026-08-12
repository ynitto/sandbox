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
PATTERN_LABELS = {
    "classify-and-act": "分類して実行",
    "fan-out-and-synthesize": "並列実行して統合",
    "adversarial-verification": "生成して検証",
    "generate-and-filter": "候補を生成して選別",
    "tournament": "複数案から選択",
    "loop-until-done": "完了まで反復",
    "map-reduce": "分割して集約",
}
# ノード種別: work=通常実行 / generate=候補生成 / classify=分類 / synthesize=統合 /
#            verify=検証 / filter=絞り込み / judge=最良選択 / reduce=構造化データの集約 /
#            split=リスト化（データ駆動 fan-out の起点）/ map=要素ごとの処理
PATTERN_LIST = list(PATTERNS)


def pattern_catalog() -> list:
    """Dashboard 等の選択 UI が読む標準パターンの正典。"""
    return [{
        "id": pid,
        "label": PATTERN_LABELS[pid],
        "description": description,
        "template": {
            "name": PATTERN_LABELS[pid],
            "nodes": _strategy_to_graph(pid, "{{request}}", 3, False),
        },
    } for pid, description in PATTERNS.items()]


def cmd_patterns(args) -> int:
    rows = pattern_catalog()
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for row in rows:
            print(f"{row['id']}\t{row['label']}\t{row['description']}")
    return 0

# 公開 kind は agentcore が唯一の実装。human はユーザー定義フロー専用で planner には出さない。
VALID_KINDS = _nodecontract.VALID_KINDS
PLANNER_KINDS = _nodecontract.PLANNER_KINDS

# 構造化データ（data）を成果として意図する kind。これら以外（work/generate/
# classify/synthesize）の自由記述出力では、散文中に紛れた JSON 風断片を data に
# 昇格させない（例: 本文の "issues": [] を空リスト data と誤抽出して下流を汚す事故を防ぐ）。
STRUCTURED_KINDS = _nodecontract.STRUCTURED_KINDS


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
        if kind not in PLANNER_KINDS:
            kind = "work"
        node = {
            "id": tid,
            "goal": str(t.get("goal", "")),
            "deps": [str(d) for d in (t.get("deps") or [])],
            "kind": kind,
        }
        reads = normalize_read_allocation(t.get("read_allocation"))
        if reads:
            node["read_allocation"] = reads
        # full も digest も明示宣言として運ぶ。既定は kind で決まるので、`digest` は
        # 「判断役だが要約で足りる」を宣言する側の意思表示になる（既定より強い）。
        if str(t.get("dependency_input") or "").strip().lower() in ("full", "digest"):
            node["dependency_input"] = str(t["dependency_input"]).strip().lower()
        replaces = str(t.get("replaces") or "").strip()
        if replaces:
            node["replaces"] = replaces
        try:
            retries = int(t.get("retries") or 0)
        except (TypeError, ValueError):
            retries = 0
        if retries > 0:
            node["retries"] = retries
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


def _request_head(request: str) -> str:
    """パターン判定に使う要求本体（先頭の段落）。

    agent-project 由来の要求は、本体のあとに charter・ブリーフ・対象リポジトリ一覧といった
    定型セクションが続く。定型には「書込先候補（owns: …）」のように毎回同じ語が入るため、
    全文をキーワード判定に掛けると要求の中身と無関係に同じパターンが選ばれ続ける
    （実測: 15 件中 9 件が定型の「候補」だけで generate-and-filter に倒れていた）。"""
    for block in request.split("\n\n"):
        if block.strip():
            return block
    return request


def _detect_pattern(request: str) -> str:
    # 要求がパターン名を名指ししていれば尊重する（本体の外＝完了条件の但し書き等でも拾う）
    named = request.lower()
    for name in PATTERN_LIST:
        if name in named:
            return name
    t = _request_head(request).lower()
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
# 分解の粒度（granularity）— coarse/fine/finest は明示倍率、auto（既定）は倍率1
#   （flow-planner が complexity から目標レンジを導出）。factor は stub/agent planner の
#   並列ノード数スケールに効く。flow-planner 経路ではタスク数はスキル側が決める。
# --------------------------------------------------------------------------
GRANULARITY_FACTORS = {"auto": 1, "coarse": 1, "fine": 2, "finest": 3}
GRANULARITY_SCOPE_DIRECTIVES = {
    "coarse": (
        "分解の粒度: 各成果ノード（work/generate/map）は1モジュール相当まで。"
        "個数の目安は1–3。goal 先頭に [scope] と [out_of_scope] を付けること。"
    ),
    "fine": (
        "分解の粒度: 各成果ノードは単機能・単モジュール（想定変更は約30行以内）。"
        "個数の目安は3–8。goal 先頭に [scope] と [out_of_scope] を付けること。"
    ),
    "finest": (
        "分解の粒度: 各成果ノードは1ファイル/1関数/1結合点まで（想定変更は約30行以内）。"
        "個数の目安は6–12（上限16）。goal 先頭に [scope] と [out_of_scope] を付けること。"
    ),
    "auto": "",  # flow-planner が complexity から導出。agent planner では指示なし
}


def granularity_factor(level: "str | None") -> int:
    """粒度レベルを倍率に。未知値・None は既定 auto（倍率1）。"""
    return GRANULARITY_FACTORS.get((level or "auto").lower(), 1)


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
    """プランナーへ渡す分解の細かさ指示。auto は空（flow-planner が導出）。"""
    lv = (level or "auto").lower()
    return GRANULARITY_SCOPE_DIRECTIVES.get(lv, "")


# auto は「flow-planner が complexity から導出する」という意味なので、flow-planner を通らない
# 経路（スキル未導入・スキル失敗のフォールバック）では導出者が居ない。そのまま渡すと粒度指示も
# 並列数倍率も効かず、設定を何も変えていない利用者の計画だけが黙って粗くなる（auto 導入前の
# 既定は finest だった）。フォールバックでは旧既定へ解決して、従来の粒度を維持する。
_AUTO_FALLBACK_GRANULARITY = "finest"


def fallback_granularity(level: "str | None") -> str:
    """flow-planner を経ない planner に渡す粒度。auto/未指定は旧既定（finest）へ解決する。"""
    lv = (level or "auto").lower()
    return lv if lv in GRANULARITY_SCOPE_DIRECTIVES and lv != "auto" else _AUTO_FALLBACK_GRANULARITY


# --------------------------------------------------------------------------
# 実行 tier のお膳立て（tier: basic）— 予算逼迫の緊急時、普段は任せない役割・作業へ
# basic（最小能力）ワーカーを投入せざるを得なくなる。そのとき planner/evaluator が
# 「basic でも渡せる形」に計画・評価を寄せる。tier は agent-control（dashboard の宣言）
# の workloads.flow.tier を読むだけで、モデル選定自体はルーティング側の仕事のまま。
# --------------------------------------------------------------------------
BASIC_TIER = "basic"

TIER_PLANNER_DIRECTIVES = {
    BASIC_TIER: (
        "実行ティア: basic（最小能力のワーカーが実行する）。各成果ノードは 1 つの短い手順"
        "だけを任せられる粒度にし、goal には対象パス・期待する成果・確認方法まで具体的に"
        "書くこと（ワーカーの推測・判断に任せない）。判断や統合が要る工程は verify/"
        "synthesize 等の別ノードへ分け、1 ノードに複数手順を積まないこと。"
    ),
}

TIER_EVALUATOR_DIRECTIVES = {
    BASIC_TIER: (
        "実行ティア: basic（最小能力のワーカーが実行する）。new_tasks を足すときは"
        " 1 ノード = 1 つの短い手順に分解し、goal に対象・期待する成果・確認方法を明記する"
        "こと。能力不足に見える失敗は、大きな作り直し 1 つではなく、より小さく明確な手順への"
        "分割で対処すること。"
    ),
}

# split（データ駆動 fan-out の分解役）向け。各要素は map ノード 1 つになるので、
# 「1 要素 = basic ワーカーが 1 手順で終えられる大きさ」まで割らせる。出力契約
# （トップレベルが JSON 配列。崩れると _expand_splits が展開されず run が空振りする）は
# プロンプト末尾への後置になるため、指示文の中でも配列のみをもう一度言う。
TIER_SPLIT_DIRECTIVES = {
    BASIC_TIER: (
        "実行ティア: basic（各要素は最小能力のワーカーが処理する）。各要素は 1 つの短い手順"
        "だけで完了できる大きさまで細かく分解し、判断・統合や複数手順を 1 要素に詰め込まない"
        "こと。出力契約は変わらない——説明文・前置きを付けず、JSON 配列だけを出力すること。"
    ),
}


def flow_tier() -> str:
    """flow ワークロードがいま走っている実行 tier（agent-control 宣言。未宣言は空文字）。"""
    return _methodlib.current_tier(_control_dir(), "flow")


def tier_planning_granularity(level: "str | None", tier: str) -> str:
    """basic tier では auto（complexity 導出）を finest へ倒す。

    basic に「complexity から導けば moderate=fine でよい」は成り立たない——ノードの大きさは
    要求の複雑さではなくワーカーの能力が律速になる。明示指定（coarse/fine/finest）は
    人の意思なので tier では覆さない。"""
    lv = (level or "auto").lower()
    if lv == "auto" and str(tier or "") == BASIC_TIER:
        return "finest"
    return lv


def tier_planner_directive(tier: "str | None") -> str:
    return TIER_PLANNER_DIRECTIVES.get(str(tier or ""), "")


def tier_evaluator_directive(tier: "str | None") -> str:
    return TIER_EVALUATOR_DIRECTIVES.get(str(tier or ""), "")


def tier_split_directive(tier: "str | None") -> str:
    return TIER_SPLIT_DIRECTIVES.get(str(tier or ""), "")


def tier_review_decision(review_setting, patterns, tier: "str | None") -> bool:
    """review 三値解決の tier 対応版。basic では auto を常時有効へ倒す
    （basic の成果を無検証で集約・終端しない）。明示 True/False は従来どおり尊重する。"""
    if not isinstance(review_setting, bool) and str(tier or "") == BASIC_TIER:
        return True
    return _review_decision(review_setting, patterns)


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


def plan_strategy_stub(request: str, review="auto", granularity="auto", tier=""):
    """要求からパターンと並列数を選び、初期グラフを作る（LLM 無し版）。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効
    （tier=basic では常時有効へ倒す）。
    granularity で並列ノード数をスケールする（auto/coarse=×1, fine=×2, finest=×3）。"""
    pattern = _detect_pattern(request)
    base = plan_stub(request)
    par = maybe_scale_parallelism(request, _parallelism(request, len([t for t in base if not t["deps"]])),
                                  granularity)
    review = tier_review_decision(review, [pattern], tier)
    tasks = _strategy_to_graph(pattern, request, par, review)
    patterns = [pattern] + (["adversarial-verification"] if review and pattern != "adversarial-verification" else [])
    strategy = {"patterns": patterns, "parallelism": par, "review": review,
                "reason": f"stub heuristic → {pattern}（粒度 {granularity}）"
                          + ("（統合前レビュー有）" if review else "")}
    if tier:
        strategy["tier"] = str(tier)
    return strategy, tasks


def plan_strategy_pattern(pattern: str, request: str, review="auto", granularity="auto", tier=""):
    """人が選んだ標準パターンを、既存の正準グラフ生成へそのまま通す。"""
    if pattern not in PATTERNS:
        raise UserPlanError(f"標準パターンが不正です: {pattern}")
    base = plan_stub(request)
    par = maybe_scale_parallelism(
        request, _parallelism(request, len([t for t in base if not t["deps"]])), granularity)
    include_review = tier_review_decision(review, [pattern], tier)
    tasks = _strategy_to_graph(pattern, request, par, include_review)
    patterns = [pattern] + (["adversarial-verification"]
                            if include_review and pattern != "adversarial-verification" else [])
    strategy = {"patterns": patterns, "parallelism": par, "review": include_review,
                "reason": f"ユーザー選択 → {pattern}（粒度 {granularity}）"}
    if tier:
        strategy["tier"] = str(tier)
    return strategy, tasks


class UserPlanError(ValueError):
    """ユーザー定義フロー（plan）の検証エラー。planner へフォールバックさせない
    （黙って別の計画へ差し替えるとユーザーの意図が失われる）。呼び出し側は run を
    failure_reason 付きで失敗終端する。"""


# ユーザー定義フローのノード数上限。planner 経路の max_fanout（既定 50）と同じ量級の
# 暴走止めで、ビルダー UI が誤って巨大グラフを投げても実行前に弾く。
_USER_PLAN_MAX_NODES = 64


def plan_strategy_user(plan: dict, request: str, tier: str = ""):
    """ユーザー定義フロー（inbox 要求の `plan` / `--plan-file`）を検証して (strategy, tasks) に固定する。

    planner（LLM / stub / flow-planner）を通らない第 3 の計画経路。ビルダー（dashboard）や人が
    書いたグラフをそのまま実行するため、検証は planner 出力の防御（`_coerce_tasks` の丸め）とは
    逆に**厳格に失敗させる**——丸めて実行すると「意図と違う形で走った」ことに気付けない。

    per-node の `agent`（{agent_cli, model}）はここでだけ受ける。LLM planner の出力からは
    従来どおり剥がす（モデル選定はルーティング・実測格付けの仕事で、planner に選ばせない）。
    goal 中の `{{request}}` は要求テキストへ置換する（プリセットの使い回し口。置換は
    エンジン側のこの 1 か所だけで行い、投入側は複製実装しない）。"""
    if not isinstance(plan, dict):
        raise UserPlanError("plan はオブジェクトである必要があります")
    raw_nodes = plan.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise UserPlanError("plan.nodes（1 件以上のノード配列）が必要です")
    if len(raw_nodes) > _USER_PLAN_MAX_NODES:
        raise UserPlanError(f"ノード数が上限を超えています（{len(raw_nodes)} > {_USER_PLAN_MAX_NODES}）")
    tasks, seen = [], set()
    for i, t in enumerate(raw_nodes):
        if not isinstance(t, dict):
            raise UserPlanError(f"nodes[{i}] がオブジェクトではありません")
        tid = str(t.get("id") or "").strip()
        if not tid:
            raise UserPlanError(f"nodes[{i}] に id がありません")
        if tid in seen:
            raise UserPlanError(f"ノード id が重複しています: {tid}")
        seen.add(tid)
        goal = str(t.get("goal") or "").replace("{{request}}", request).strip()
        if not goal:
            raise UserPlanError(f"ノード {tid} の goal が空です")
        kind = str(t.get("kind") or "work")
        if kind not in VALID_KINDS:
            raise UserPlanError(f"ノード {tid} の kind が不正です: {kind}"
                                f"（有効: {', '.join(sorted(VALID_KINDS))}）")
        node = {"id": tid, "goal": goal,
                "deps": [str(d) for d in (t.get("deps") or [])], "kind": kind}
        agent = t.get("agent")
        if kind == "human":
            if agent is not None:
                raise UserPlanError(f"ノード {tid} の human には agent を指定できません")
            try:
                node["interaction"] = _interaction.normalize_spec(t.get("interaction"))
            except _interaction.InteractionError as e:
                raise UserPlanError(f"ノード {tid} の interaction が不正です: {e}") from None
        if agent is not None:
            if not (isinstance(agent, dict) and str(agent.get("agent_cli") or "").strip()):
                raise UserPlanError(f"ノード {tid} の agent が不正です（agent_cli が必要）")
            spec = {"agent_cli": str(agent["agent_cli"]).strip()}
            if str(agent.get("model") or "").strip():
                spec["model"] = str(agent["model"]).strip()
            node["agent"] = spec
        # 固定 tier（実行レベル）。候補の解決は管理面（dashboard）の仕事で、エンジンは
        # 「この段として実行された」事実の記録（pinned-tier）と手法判定（when.tiers の
        # ノード tier 優先）にだけ使う。human は tier を持たない（agent と同じ扱い）。
        # 変数名は引数 tier（workload 宣言の実行 tier）と衝突させない。
        node_tier = str(t.get("tier") or "").strip()
        if node_tier:
            if kind == "human":
                raise UserPlanError(f"ノード {tid} の human には tier を指定できません")
            node["tier"] = node_tier
        reads = normalize_read_allocation(t.get("read_allocation"))
        if reads:
            node["read_allocation"] = reads
        if str(t.get("dependency_input") or "").strip().lower() in ("full", "digest"):
            node["dependency_input"] = str(t["dependency_input"]).strip().lower()
        try:
            retries = int(t.get("retries") or 0)
        except (TypeError, ValueError):
            raise UserPlanError(f"ノード {tid} の retries が数値ではありません")
        if retries > 0:
            node["retries"] = retries
        tasks.append(node)
    ids = {t["id"] for t in tasks}
    splits = {t["id"] for t in tasks if t["kind"] == "split"}
    for t in tasks:
        for d in t["deps"]:
            if d not in ids:
                raise UserPlanError(f"ノード {t['id']} の依存が未知です: {d}")
            if d == t["id"]:
                raise UserPlanError(f"ノード {t['id']} が自分自身に依存しています")
            if d in splits:
                # split の後段（map→reduce）は実行時に動的生成される。静的な後段を許すと
                # fan-out と二重化するため _collapse_split_successors が黙って除去する——
                # ユーザー定義フローでは黙った除去でなく投入時に弾く。
                raise UserPlanError(
                    f"ノード {t['id']} が split ノード {d} に依存しています"
                    "（split の後段は実行時に自動生成されるため、静的な依存は張れません）")
    # 循環検査（Kahn 法）。_sanitize_graph は黙って断ち切るが、ここでは失敗させる。
    pending = {t["id"]: set(t["deps"]) for t in tasks}
    ready = [i for i, d in pending.items() if not d]
    done = set()
    while ready:
        x = ready.pop()
        done.add(x)
        for i, d in pending.items():
            if x in d:
                d.discard(x)
                if not d and i not in done and i not in ready:
                    ready.append(i)
    cyc = sorted(ids - done)
    if cyc:
        raise UserPlanError(f"依存が循環しています: {', '.join(cyc)}")
    name = str(plan.get("name") or "").strip()
    roots = sum(1 for t in tasks if not t["deps"])
    # review 三値。plan が明示すれば尊重（ビルダー側に宣言口を作るときの受け口）、未指定は
    # auto として tier 判定へ通す。"user-defined" は AGGREGATING_PATTERNS に含まれないため
    # basic 以外の auto は従来どおり False（後方互換）。basic のときだけ True へ倒れ、
    # _emit_reduce_tree の verify gate が動的 fan-out（map→reduce 間）にだけ入る——
    # 人が描いた静的な形は変わらない。
    review = plan.get("review", "auto")
    if not isinstance(review, bool) and review != "auto":
        raise UserPlanError(f"plan.review が不正です: {review!r}（true / false / \"auto\" のみ）")
    strategy = {
        "patterns": ["user-defined"], "parallelism": max(1, roots),
        "review": tier_review_decision(review, ["user-defined"], tier),
        "user_plan": True,
        "reason": f"ユーザー定義フロー{f'「{name}」' if name else ''}（{len(tasks)} ノード）",
    }
    if tier:
        strategy["tier"] = str(tier)
    if name:
        strategy["plan_name"] = name
    # evaluate: true で評価役（evaluator-optimizer）の継続判断を有効化する。既定は無効——
    # ユーザー定義フローは形が意図そのものなので、再計画でノードを足して形を変えない。
    if plan.get("evaluate") is True:
        strategy["user_plan_evaluate"] = True
    return strategy, tasks


def _record_rule_agreement(strategy: dict, request: str, granularity: str) -> dict:
    """LLM の判断を現行ルールと照合して記録する。実行に使う strategy は変更しない。

    **ルール側の入力は要求と、人が指定した granularity だけ**にする。LLM の分析段が導出した
    粒度を渡すと「決定的ルール」の入力に LLM の出力が混ざり、一致率が上振れする——S16 は
    この数字を見て「LLM に聞くのをやめてよい判断」を選ぶので、上振れはそのまま誤った
    置き換えになる。planner の系統によって基準が変わらないよう、どちらの呼び出し元からも
    同じ granularity（呼び出し引数そのもの）を渡す。"""
    llm_pattern = (strategy.get("patterns") or [""])[0]
    llm_parallelism = int(strategy.get("parallelism") or 0)
    rule_pattern = _detect_pattern(request)
    rule_parallelism = maybe_scale_parallelism(request, _parallelism(request, 2), granularity)
    strategy["decision_comparisons"] = [
        {"decision": "planner.pattern", "llm": llm_pattern,
         "rule": rule_pattern, "agree": llm_pattern == rule_pattern},
        {"decision": "planner.parallelism", "llm": llm_parallelism,
         "rule": rule_parallelism, "agree": llm_parallelism == rule_parallelism,
         "rule_input_granularity": str(granularity)},
    ]
    return strategy


def plan_strategy_agent(request: str, model: str | None, review="auto", granularity="auto",
                        context: str = "", tier=""):
    """kiro-cli にパターン選択・並列数・初期グラフを決めさせる。
    review は 'auto'（既定）/True/False の三値。auto は集約パターンで自動有効
    （tier=basic では常時有効へ倒す）。
    granularity で分解の細かさを指示し、返ってきた並列数も粒度倍率でスケールする。
    tier=basic では basic ワーカー向けの分解指示（1 ノード = 1 短手順・具体的な goal）を足す。
    ワークスペース（唯一の書込先）は run 単位なので、ノードへの repo 割当はしない。

    `context`（案 H・オプトイン）: agent-flow が run の meta へ固定したプロジェクト文脈
    （charter/rules.md/リポジトリ理解）スナップショット。stable_prefix 有効時、
    agent-project が request 本体から外した分をここで補い、分解の質を落とさない。
    プロンプトの先頭（安定部）へ前置する——agentcore.promptcompose と同じ規約。"""
    catalog = "\n".join(f"- {k}: {v}" for k, v in PATTERNS.items())
    compose = ("必要なら複数パターンを多段に複合してよい（例: classify-and-act の各分岐を "
               "fan-out-and-synthesize にする / generate-and-filter の通過案で tournament を行う）。")
    # 明示 OFF でなければレビューの意図を planner に伝える（最終的な有効/無効は
    # 返ってきた patterns を見て _review_decision で確定する）。
    review_note = ("統合（synthesize/reduce）を伴うパターンでは、集約の前に verify ノードを 1 つ挟み、"
                   "事前チェック・敵対的レビューを行ってください。" if review is not False else "")
    gran_note = granularity_directive(granularity)
    tier_note = tier_planner_directive(tier)
    prompt = (
        "あなたは分散 Dynamic Workflow の計画役です。以下のワークフローパターンを知っています:\n"
        f"{catalog}\n\n"
        "patterns に書けるのは上記 7 つのパターン名だけです。派生語・同義語は使わず、"
        "近いものは必ず上記の正規名へ読み替えてください（例: 'panel of verifiers'→adversarial-verification）。\n"
        + (tier_note + "\n" if tier_note else "")
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
        '"tasks": [{"id": "t1", "goal": "...", "deps": [], "kind": "work", '
        '"read_allocation": [{"path": "src/x.py", "range": "10-40", "reason": "変更点"}], '
        '"dependency_input": "digest"}]}\n'
        "work/generate ノードには、最初に読むべき path・任意の range・reason を read_allocation に割り付けてください。\n\n"
        "依存成果は work/generate では要約と成果物参照だけの digest です"
        "（verify/reduce/synthesize/judge/filter は判断対象そのものなので既定で全文）。"
        "work/generate でも完全な構造化データが不可欠なノードだけ dependency_input=full を宣言してください。\n\n"
        f"要求: {request}"
    )
    prompt = _promptcompose.compose([context], [prompt])
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
            "review": tier_review_decision(review, patterns, tier),
            "reason": str(data.get("reason", "")),
        }
        if tier:
            strategy["tier"] = str(tier)
        method_app = _last_methods("planner")
        if method_app.get("methods"):
            strategy["methods"] = method_app["methods"]
        if isinstance(method_app.get("trial"), dict):
            strategy["trial"] = method_app["trial"]
        return _record_rule_agreement(strategy, request, granularity), tasks

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
        # LLM 経路が全滅した最後の砦。ここは要求のキーワードだけで決めるので、選ばれた
        # パターンは「要求の分析結果」ではない。そう読めるよう reason にも残す。
        log("planner", f"エージェント planner が計画を返しませんでした → stub へ縮退: {str(e)[:200]}")
        strategy, tasks = plan_strategy_stub(request, review, granularity, tier)
        strategy["reason"] = f"[agent planner 失敗: {str(e)[:120]}] {strategy.get('reason', '')}".strip()
        return strategy, tasks


def _find_skill_script(skill: str, script: str):
    """スキルの scripts/{script} を探す（flow-planner / flow-worker 共通）。
    検索順: .github/skills/{skill}/ → git root/.github/skills/ → ~/.agents/skills/ → ~/.kiro/skills/
    → 各エージェントホームの skill-registry.json の {skill_home}（`_AGENT_HOME_DIRS`）"""
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
    # ~/.agents/skills → ~/.kiro/skills（共有スキル配置）を確認
    for skills_home in ("~/.agents/skills", "~/.kiro/skills"):
        candidates.append(os.path.join(os.path.expanduser(skills_home), skill, "scripts", script))
    # skill-registry.json から skill_home を読む
    for d in _AGENT_HOME_DIRS:
        reg = os.path.join(os.path.expanduser("~"), d, "skill-registry.json")
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
    """計画スキルの plan.py を探す（スキル名は設定 planner_skill。既定 flow-planner）。"""
    skill = str(_PLANNER_SKILL or "").strip() or "flow-planner"
    return _find_skill_script(skill, "plan.py")


def _skill_env() -> dict:
    """計画スキル（独立プロセス）へ渡す環境。agentcore を import できる PYTHONPATH を足す。

    スキル側に組み込み CLI の argv をハードコードさせないための経路（S9・C7）。開発木では
    tools/agent-tools/agentcore、zipapp 配布ではアーカイブ自身が sys.path に載るため、
    どちらでも `agentcli` の親ディレクトリを渡せば解決できる。"""
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    root = os.path.dirname(os.path.dirname(os.path.abspath(_agentcli.__file__)))
    prev = env.get("PYTHONPATH", "")
    if root not in prev.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
    return env


def _planner_fallback(request: str, model: "str | None", review, granularity: "str | None",
                      context: str, why: str, tier=""):
    """計画スキルを使えなかったときの縮退。**必ず記録を残す**。

    以前はここが黙って落ちていたため、スキルが一度も起動していないのに「計画できた」ように
    見えていた（agent_cli を差し替えた環境で 4 run 連続。stub のキーワード判定が同じパターンを
    選び続けても気づけない）。ログと strategy.reason の両方へ理由を残す。"""
    log("planner", f"flow-planner を使えませんでした → エージェント planner へ縮退: {why[:200]}")
    strategy, tasks = plan_strategy_agent(request, model, review,
                                          fallback_granularity(granularity), context, tier)
    strategy["reason"] = f"[flow-planner 不使用: {why[:120]}] {strategy.get('reason', '')}".strip()
    return strategy, tasks


def _skill_flag_supported(script: str, flag: str) -> bool:
    """インストール済みスキルの版ずれ防御。スキルが知らない引数を渡すと argparse が rc=2 で
    落ち、計画全体がエージェント planner へ縮退してしまう。スクリプト本文に該当フラグが
    現れるときだけ渡す（決定的・追加コストはファイル読み 1 回）。"""
    try:
        with open(script, encoding="utf-8") as f:
            return flag in f.read()
    except OSError:
        return False


def plan_strategy_flow_planner(request: str, model: str | None, review="auto", granularity="auto",
                               context: str = "", tier=""):
    """flow-planner スキルの3段パイプラインを呼び出す。
    スキルが見つからない / 失敗した場合は plan_strategy_agent にフォールバック。
    granularity はスキルへ `--granularity` で渡す（auto=complexity 導出 / 明示は優先）。
    タスク数・スコープ契約はスキル側が決めるため、ここでの並列数倍率は掛けない。
    tier はスキルが `--tier` を知っている版のときだけ渡す（basic の分解指示・review 強制）。

    `context`（案 H・オプトイン）はスキルへ `--context` で渡す（スキルの Phase 1/3 が
    プロンプト先頭へ前置する。独立プロセスのため agentcore は import させず、
    「安定部を先に」の規約だけをスキル側に手で踏襲させている）。"""
    script = _find_flow_planner_script()
    if not script:
        # flow-planner スキル未インストール → エージェント planner にフォールバック
        return _planner_fallback(request, model, review, granularity, context,
                                 f"{_PLANNER_SKILL or 'flow-planner'} スキルが見つかりません",
                                 tier)
    # 計画に使う CLI/モデルは planner の設定（agents: planner: {agent_cli, model}）に従わせる。
    # スキル側の既定は kiro-cli だが、それを黙って使うと agent_cli を claude/codex にしていても
    # 計画だけ kiro-cli で走り、kiro-cli が使えない環境では毎回失敗して stub へ落ちていた。
    cli, model_ov = _agent_for("planner")
    cmd = [sys.executable, script, request, "--granularity", str(granularity or "auto"),
           "--agent-cli", cli]
    if tier and _skill_flag_supported(script, "--tier"):
        cmd += ["--tier", str(tier)]
    model = model_ov or model
    if model:
        cmd += ["--model", model]
    if isinstance(review, bool):
        cmd += ["--review", "true" if review else "false"]
    else:
        cmd += ["--review", str(review)]
    if context:
        cmd += ["--context", context]
    try:
        # スキルは独立プロセスだが、エージェント CLI の argv 知識だけは agentcore へ委譲させる
        # （組み込み 4 種の白リストを持たせない＝定義ファイルを足した CLI がそのまま使える）。
        # 3 フェーズぶん LLM を呼ぶので、待ち時間は 1 回分のタイムアウトの 3 倍を見込む
        # （agent_timeout=0 で無効化されているなら、こちらも待ち続ける＝同じ意思に従う）。
        one = _agent_timeout("planner")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=max(300.0, one * 3) if one else None,
                              env=_skill_env())
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "")[:500])
        data = json.loads(proc.stdout)
        strategy = data.get("strategy", {})
        tasks = _coerce_tasks(data.get("tasks", []))
        if not tasks:
            raise ValueError("flow-planner returned empty tasks")
        # strategy を正規化（粒度・タスク数は flow-planner が確定済み → 倍率を掛けない）
        patterns = [p for p in (strategy.get("patterns") or []) if p in PATTERNS] or ["fan-out-and-synthesize"]
        resolved = strategy.get("granularity") or granularity or "auto"
        final_strategy = {
            "patterns": patterns,
            "parallelism": int(strategy.get("parallelism", 2) or 2),
            "review": tier_review_decision(review, patterns, tier)
                      if not isinstance(strategy.get("review"), bool)
                      else strategy["review"],
            "reason": f"[flow-planner] {strategy.get('reason', '')}（粒度 {resolved}）",
            "granularity": resolved,
        }
        if tier:
            final_strategy["tier"] = str(strategy.get("tier") or tier)
        # ルール側の入力は `resolved`（LLM 由来）ではなく呼び出し引数の granularity。
        return _record_rule_agreement(final_strategy, request, granularity), tasks
    except Exception as e:  # noqa: BLE001 — flow-planner 失敗時はエージェント planner にフォールバック
        return _planner_fallback(request, model, review, granularity, context, str(e), tier)


# --------------------------------------------------------------------------
# 計画承認ゲート（plan gate）— 人間の承認・差し戻し（オプトイン・既定 off）
# --------------------------------------------------------------------------
# planner が作った初期グラフの実行前に human 承認ノードを 1 つ挟む。root タスクを全て
# ゲート依存に付け替えるため、承認（approved）までワーカーは何も実行しない。
# 差し戻し（rejected＋コメント）は orchestrator が決定的に検知し、指摘を planner へ渡して
# 再計画する（max_retries で有界）。期限切れ・未承認は failed 終端（フェイルクローズ）。
# 「自動 planner は human を生成しない」という不変条件はそのまま——ゲートは LLM 出力では
# なく、人がオプトインしたときに agent-flow が決定的に挿入する。
PLAN_GATE_PREFIX = "plan-gate"
_PLAN_GATE_ID_RE = re.compile(r"^plan-gate(?:-(\d+))?$")
# interaction request 全体の上限は 64KB（agentcore.interaction）。要約は余裕を持って抑える。
_PLAN_GATE_PROMPT_LIMIT = 16000
PLAN_GATE_FEEDBACK_MARK = "[計画への差し戻し指摘（必ず反映すること）]"


def plan_gate_id(attempt: int) -> str:
    """試行 1 は従来 id 系と衝突しない素の plan-gate、以降は -N を付ける。
    `-r<数字>` を含めないこと（`_retry_depth` が作り直し回数と誤読する）。"""
    return PLAN_GATE_PREFIX if attempt <= 1 else f"{PLAN_GATE_PREFIX}-{attempt}"


def plan_gate_attempt(nid: str) -> "int | None":
    """plan gate ノード id なら試行番号を、それ以外は None を返す。"""
    m = _PLAN_GATE_ID_RE.match(str(nid or ""))
    return int(m.group(1) or 1) if m else None


def render_plan_gate_prompt(request: str, strategy: dict, tasks: list) -> str:
    """人が承認/差し戻しを判断するための計画要約。ゲート自身（human ノード）は載せない。"""
    strat = strategy or {}
    head = [
        f"要求: {_first_line(request, 200)}",
        f"patterns: {', '.join(strat.get('patterns') or [])} / 並列 {strat.get('parallelism', '?')}"
        + (f" / 粒度 {strat['granularity']}" if strat.get("granularity") else "")
        + (f" / tier {strat['tier']}" if strat.get("tier") else ""),
    ]
    reason = str(strat.get("reason") or "").strip()
    if reason:
        head.append(f"選定理由: {reason[:300]}")
    rows = [f"- {t['id']} [{t.get('kind', 'work')}]"
            + (f" deps={','.join(t['deps'])}" if t.get("deps") else "")
            + f": {' '.join(str(t.get('goal', '')).split())[:160]}"
            for t in tasks if t.get("kind") != "human"]
    body = "\n".join(head) + "\n\nタスクグラフ:\n" + "\n".join(rows) + (
        "\n\n承認（approved）で実行を開始します。差し戻し（rejected）はコメントの指摘を"
        "反映して再計画します（粒度・分け方への指摘もコメントで伝えられます）。")
    if len(body) > _PLAN_GATE_PROMPT_LIMIT:
        body = body[:_PLAN_GATE_PROMPT_LIMIT] + "…（省略）"
    return body


def plan_gate_task(request: str, strategy: dict, tasks: list,
                   attempt: int = 1, timeout: float = 0.0) -> dict:
    """計画承認ゲートの human ノードを組み立てる（interaction は正規化済みで運ぶ）。"""
    spec = {"mode": "approval",
            "prompt": render_plan_gate_prompt(request, strategy, tasks),
            "audience": ["reviewer"]}
    if timeout and float(timeout) > 0:
        spec["timeout_seconds"] = int(float(timeout))
    return {"id": plan_gate_id(attempt), "kind": "human", "deps": [],
            "goal": "計画の承認待ち（承認で実行開始・差し戻しで再計画）",
            "interaction": _interaction.normalize_spec(spec)}


def apply_plan_gate(nodes: dict, tasks: list, gate: dict) -> None:
    """graph nodes と task 列の両方へゲートを差し込み、root（deps 空）をゲート依存へ付け替える。
    base-sync 注入の**後**に呼ぶこと——base-sync も承認前に走らせない（ワークスペースへの
    書き込みを人の承認前に始めない）。"""
    gid = gate["id"]
    for t in tasks:
        if t.get("id") != gid and not t.get("deps"):
            t["deps"] = [gid]
    for nid, node in nodes.items():
        if nid != gid and not node.get("deps"):
            node["deps"] = [gid]
    nodes[gid] = _node_entry(gate)
    tasks.append(gate)


def plan_gate_state(nodes: dict, results: dict) -> "dict | None":
    """グラフ中の（最新の）plan gate の決着状態。ゲートが無ければ None。

    返り値: {"state": "waiting"|"approved"|"rejected"|"unapproved",
             "id", "attempt", "comment"}。rejected は人の差し戻し（comment に指摘）、
    unapproved は期限切れ・interaction エラー等の「承認に至らない失敗」。"""
    gates = [(plan_gate_attempt(nid), nid) for nid in nodes
             if nodes[nid].get("kind") == "human" and plan_gate_attempt(nid) is not None]
    if not gates:
        return None
    attempt, gid = max(gates)
    r = results.get(gid) or {}
    status = r.get("status")
    out = {"id": gid, "attempt": attempt, "comment": ""}
    if status == "done":
        return {**out, "state": "approved"}
    if status != "failed":
        return {**out, "state": "waiting"}
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    answer = data.get("answer") if isinstance(data.get("answer"), dict) else {}
    out["comment"] = str(answer.get("comment") or "").strip()
    if str(data.get("outcome") or "") == "rejected":
        return {**out, "state": "rejected"}
    return {**out, "state": "unapproved"}


def plan_gate_feedback_request(request: str, comment: str) -> str:
    """差し戻しコメントを planner へ渡す要求文。元要求へ**毎回作り直しで**付ける
    （前回の注釈へ積み増さない＝最新の指摘だけが権威）。"""
    body = str(comment or "").strip() or "（コメント無し。分解の粒度と分け方を見直して再計画すること）"
    return f"{request}\n\n{PLAN_GATE_FEEDBACK_MARK}\n{body}"


def dedupe_task_ids(tasks: list, taken: "set[str]") -> list:
    """再計画の新タスク id が「結果を確定して残るノード」と衝突したらサフィックスで避け、
    deps も同じ改名で付け替える（結果ファイルは node id がキーなので、衝突すると旧結果を
    新タスクの成果と誤読する）。"""
    renamed: dict = {}
    used = set(taken)
    for t in tasks:
        tid = str(t["id"])
        if tid in used:
            n = 2
            while f"{tid}-g{n}" in used:
                n += 1
            renamed[tid] = f"{tid}-g{n}"
            t["id"] = renamed[tid]
        used.add(t["id"])
    if renamed:
        for t in tasks:
            t["deps"] = [renamed.get(d, d) for d in (t.get("deps") or [])]
    return tasks
