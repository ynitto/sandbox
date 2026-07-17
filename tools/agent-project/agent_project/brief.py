from __future__ import annotations
# brief.py — run/branch スコープの「run ブリーフ」（差し戻しの意図＋各ノードが発見した制約の蓄積・伝播）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 目的:
#   分散生成した成果の一貫性を、**事後の集約ノード**（agent-flow の reduce/synthesize が全出力を
#   1 コンテキストへ読む＝規模が大きくなるほどコンテキスト制約で破綻する）に頼らず、**事前伝播**で
#   担保する。差し戻し（リトライ）の意図と、各ノードが実行中に発見した恒常制約を、タスクの
#   ターゲットブランチ ap/<task-id> と同じキーで **run ブリーフ**（<root>/brief/<task-id>.md）へ
#   **追記のみ**で蓄積し、build_request 経由で以後の全 run・全分散ノードへ均一に注入する。各ノードは
#   この小さく正規化された共有ブリーフに個別準拠すればよく、集約ノードが全出力を読み直す必要がない。
#
# rules.md（正本）との違い:
#   rules.md は人が書く**恒久**ルール（全タスク常時注入・hit 閾値で learn を昇格）。run ブリーフは
#   その一段手前——**タスク/ブランチ・スコープで一時・自動蓄積・追記のみ**——の層で、成果が done/マージ
#   したら役目を終える（一般化できる項目は既存の learn→rules 昇格で正本へ格上げされる）。feedback が
#   差し戻しのたびに上書きで消えるのに対し、ブリーフは追記のみ＝過去の差し戻し制約が失われない。
#   置き場所も rules.md と同じ <root> 直下に並べ、「正本 rules.md ↔ 一時 brief/」の対比を明確にする。
#   注入はプロンプト文脈を足すだけで done 条件・予算・policy には触れない。
# ---------------------------------------------------------------------------

BRIEF_DIRNAME = "brief"
_BRIEF_MAX_ITEMS = 40               # 注入・保持するブリーフ項目の上限件数（有界。超過分は古いものから落とす）
_BRIEF_ITEM_MAX_LEN = 400           # 1 項目あたりの最大長（argv/プロンプト肥大を防ぐ）


def brief_path(cfg: "Config", task: "Task") -> Path:
    """タスクの run ブリーフのパス（<root>/brief/<task-id>.md。root は rules.md と同じ backlog の親）。
    ap/<task-id> ブランチと同じ task.id キーなので、リトライ（新 run-id）でも同じブリーフを指す
    ＝ブランチとブリーフが一緒にリトライ横断で引き継がれる。"""
    return cfg.backlog.parent / BRIEF_DIRNAME / f"{task.id}.md"


def _norm_brief_item(text) -> str:
    """ブリーフ項目を 1 行へ正規化する（改行畳み込み・空白圧縮・箇条書き記号除去・長さ制限）。
    重複排除と有界注入のため。改行は ⏎ に畳んで 1 項目 1 行を保つ。"""
    s = str(text or "").replace("\r", " ").replace("\n", " ⏎ ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[-*・#>\s]+", "", s).strip()      # 先頭の箇条書き記号・見出し記号を落とす
    return s[:_BRIEF_ITEM_MAX_LEN]


def _brief_items(cfg: "Config", task: "Task") -> "list[str]":
    """ブリーフの既存項目（正規化本文）のリスト。無ければ空。重複判定・注入の双方に使う。
    出典の HTML コメント（<!-- … -->）は本文から除く。"""
    p = brief_path(cfg, task)
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


def append_brief_item(cfg: "Config", task: "Task", text, source: str = "") -> bool:
    """ブリーフへ項目を 1 件追記する（正規化・重複排除・出典コメント付き・冪等）。追記したら True。
    差し戻し（feedback / revise / gitlab-reject / cohort）とノード発見の双方から呼ばれる。
    追記のみ＝過去の項目を上書きしないので、複数回のリトライでも最初の指摘が失われない。"""
    body = _norm_brief_item(text)
    if not body:
        return False
    if body in _brief_items(cfg, task):             # 既出は冪等に無視（決定的）
        return False
    p = brief_path(cfg, task)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if not p.exists():
        header = (f"# run ブリーフ: {task.id}\n\n"
                  "<!-- 差し戻し（リトライ）の意図と、各ノードが発見した恒常制約を追記のみで蓄積する。\n"
                  "     ap/<task-id> ブランチと同じキー＝リトライ横断で引き継がれ、build_request 経由で\n"
                  "     以後の全 run・全分散ノードへ注入される（有界・一時）。人が編集・削除してよい。 -->\n\n")
    date = datetime.now().strftime("%Y-%m-%d")
    note = f"  <!-- {source} {date} -->" if source else f"  <!-- {date} -->"
    with p.open("a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(f"- {body}{note}\n")
    append_journal(cfg.journal, f"run ブリーフ: {task.id} に追記（{source or 'manual'}）: {body[:80]}")
    return True


def add_brief_items(cfg: "Config", task: "Task", texts, source: str = "") -> int:
    """複数の項目をまとめて追記する（ノード発見制約の回収経路用）。実際に追記できた件数を返す。"""
    n = 0
    for t in (texts or []):
        if append_brief_item(cfg, task, t, source=source):
            n += 1
    return n


def brief_context(cfg: "Config", task: "Task", limit: int = 1200) -> str:
    """build_request 注入用の run ブリーフ本文（末尾 _BRIEF_MAX_ITEMS 件を有界に）。無ければ空（後方互換）。
    正規化済み 1 項目 1 行の箇条書き。長さは limit で頭打ち（プロンプト肥大を防ぐ）。"""
    items = _brief_items(cfg, task)
    if not items:
        return ""
    items = items[-_BRIEF_MAX_ITEMS:]
    return "\n".join(f"- {it}" for it in items)[:limit]


def retire_brief(cfg: "Config", task: "Task") -> str:
    """タスク完了（archive）時に run ブリーフを**退役**させる: 蓄積項目の本文を返してファイルを
    削除する。呼び出し側（archive_task）が納品書へ転記するので、蓄積された制約・教訓は
    archive/<id>.md に成果物として残る（一般化できる項目は capture_insight の learn 射影で
    既に decisions/ に居り、learn→rules 昇格ラダーで正本へ格上げされる）。

    削除する理由: ブリーフは task/branch スコープの一時層で、成果が done/マージしたら役目を
    終える。残すと (1) <root>/brief/ に死蔵ファイルが溜まる (2) 同じ task-id を再利用したとき
    **前世代の古いブリーフが新タスクへ注入される**（brief_path は task.id キーのため）。
    ブリーフが無ければ空文字（後方互換・冪等）。"""
    p = brief_path(cfg, task)
    if not p.exists():
        return ""
    items = _brief_items(cfg, task)
    try:
        p.unlink()
    except OSError:
        return ""
    append_journal(cfg.journal, f"run ブリーフ退役: {task.id}（{len(items)} 件を納品書へ転記）")
    return "\n".join(f"- {it}" for it in items)


def capture_insight(cfg: "Config", task: "Task", text, source: str,
                    learn: bool = False, learn_action: str = "") -> bool:
    """指摘・制約の**捕捉の単一入口**。1 つの指摘を 2 つのスコープへ射影する:

      - **task スコープ（run ブリーフ）**: 常に追記。同一タスクの以後の全 run・全分散ノードへ
        無条件・全文で注入される（空間方向＝今のタスクの一貫性）。
      - **project スコープ（learn）**: learn=True かつ learn_capture 有効なら decisions/ に
        learn 行を残す。タイトル類似の別タスクへの auto-resolve → hits 閾値で rules.md 昇格 →
        （opt-in）ltm、という既存の昇格ラダーに乗る（時間方向＝将来のタスクの再発防止）。

    従来 feedback / revise / gitlab-reject は両スコープへ別々のコードで書き、ノード発見制約と
    cohort 波及は brief のみ＝learn へ届く道が無かった（タスク完了とともに教訓が死蔵される）。
    新しい捕捉元はこの入口を使うこと。追記できたら True（重複は冪等に False）。"""
    added = append_brief_item(cfg, task, text, source=source)
    if added and learn and getattr(cfg, "learn_capture", True):
        body = _norm_brief_item(text)
        append_decision(cfg, task.id, "system",
                        context=f"{task.id}（{task.title}）の {source} 由来の教訓を捕捉",
                        action=learn_action or f"capture:{source}",
                        reason=body[:200],
                        affects=f"{task.id} の run ブリーフ＋横断 learn",
                        learn=(task.title, body))
    return added


# ---------------------------------------------------------------------------
