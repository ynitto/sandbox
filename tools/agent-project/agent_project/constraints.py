from __future__ import annotations
# constraints.py — run/branch スコープの制約台帳（差し戻しの意図＋各ノードが発見した制約の蓄積・伝播）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 目的:
#   分散生成した成果の一貫性を、**事後の集約ノード**（agent-flow の reduce/synthesize が全出力を
#   1 コンテキストへ読む＝規模が大きくなるほどコンテキスト制約で破綻する）に頼らず、**事前伝播**で
#   担保する。差し戻し（リトライ）の意図と、各ノードが実行中に発見した恒常制約を、タスクの
#   ターゲットブランチ ap/<task-id> と同じキーで台帳に**追記のみ**で蓄積し、build_request 経由で
#   以後の全 run・全分散ノードへ均一に注入する。各ノードはこの小さく正規化された共有制約に個別に
#   準拠すればよく、集約ノードが全出力を読み直す必要がない。
#
#   なぜ台帳が要るか: feedback フィールドは差し戻しのたびに drop→set で**上書き**され、過去の差し戻し
#   制約が消える（複数回の手戻りで最初の指摘が失われる）。rules.md はプロジェクト横断で hit 閾値の
#   昇格を要し即時には効かない。台帳は両者の隙間——**タスク/ブランチ・スコープで即時・追記のみ**の
#   一段細かい層——を埋める。注入はプロンプト文脈を足すだけで done 条件・予算・policy には触れない。
# ---------------------------------------------------------------------------

CONSTRAINTS_DIRNAME = "constraints"
_CONSTRAINT_MAX_LINES = 40          # 注入・保持する制約の上限件数（有界。超過分は古いものから落とす）
_CONSTRAINT_MAX_LEN = 400           # 1 制約あたりの最大長（argv/プロンプト肥大を防ぐ）


def constraints_path(cfg: "Config", task: "Task") -> Path:
    """タスクの制約台帳のパス（decisions/constraints/<task-id>.md）。
    ap/<task-id> ブランチと同じ task.id キーなので、リトライ（新 run-id）でも同じ台帳を指す
    ＝ブランチと制約が一緒にリトライ横断で引き継がれる。"""
    return cfg.decisions / CONSTRAINTS_DIRNAME / f"{task.id}.md"


def _norm_constraint(text) -> str:
    """制約テキストを 1 行へ正規化する（改行畳み込み・空白圧縮・箇条書き記号除去・長さ制限）。
    重複排除と有界注入のため。改行は ⏎ に畳んで 1 制約 1 行を保つ。"""
    s = str(text or "").replace("\r", " ").replace("\n", " ⏎ ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[-*・#>\s]+", "", s).strip()      # 先頭の箇条書き記号・見出し記号を落とす
    return s[:_CONSTRAINT_MAX_LEN]


def _constraint_lines(cfg: "Config", task: "Task") -> "list[str]":
    """台帳の既存制約（正規化本文）のリスト。無ければ空。重複判定・注入の双方に使う。
    出典の HTML コメント（<!-- … -->）は本文から除く。"""
    p = constraints_path(cfg, task)
    if not p.exists():
        return []
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: "list[str]" = []
    for line in txt.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = re.sub(r"\s*<!--.*?-->\s*$", "", line[2:]).strip()
        if body:
            out.append(body)
    return out


def append_constraint(cfg: "Config", task: "Task", text, source: str = "") -> bool:
    """制約を 1 件、台帳へ追記する（正規化・重複排除・出典コメント付き・冪等）。追記したら True。
    差し戻し（feedback / revise / gitlab-reject / cohort）とノード発見の双方から呼ばれる。
    追記のみ＝過去の制約を上書きしないので、複数回のリトライでも最初の指摘が失われない。"""
    body = _norm_constraint(text)
    if not body:
        return False
    if body in _constraint_lines(cfg, task):        # 既出は冪等に無視（決定的）
        return False
    p = constraints_path(cfg, task)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if not p.exists():
        header = (f"# 制約台帳: {task.id}\n\n"
                  "<!-- 差し戻し（リトライ）の意図と、各ノードが発見した恒常制約を追記のみで蓄積する。\n"
                  "     ap/<task-id> ブランチと同じキー＝リトライ横断で引き継がれ、build_request 経由で\n"
                  "     以後の全 run・全分散ノードへ注入される（有界）。人が編集・削除してよい。 -->\n\n")
    date = datetime.now().strftime("%Y-%m-%d")
    note = f"  <!-- {source} {date} -->" if source else f"  <!-- {date} -->"
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(f"- {body}{note}\n")
    append_journal(cfg.journal, f"制約台帳: {task.id} に追記（{source or 'manual'}）: {body[:80]}")
    return True


def add_constraints(cfg: "Config", task: "Task", texts, source: str = "") -> int:
    """複数の制約をまとめて追記する（ノード発見制約の回収経路用）。実際に追記できた件数を返す。"""
    n = 0
    for t in (texts or []):
        if append_constraint(cfg, task, t, source=source):
            n += 1
    return n


def constraints_context(cfg: "Config", task: "Task", limit: int = 1200) -> str:
    """build_request 注入用の台帳本文（末尾 _CONSTRAINT_MAX_LINES 件を有界に）。無ければ空（後方互換）。
    正規化済み 1 制約 1 行の箇条書き。長さは limit で頭打ち（プロンプト肥大を防ぐ）。"""
    lines = _constraint_lines(cfg, task)
    if not lines:
        return ""
    lines = lines[-_CONSTRAINT_MAX_LINES:]
    return "\n".join(f"- {ln}" for ln in lines)[:limit]


# ---------------------------------------------------------------------------
