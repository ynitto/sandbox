"""規則の本体。どれも決定的（同じ入力なら同じ指摘）で、LLM は呼ばない。

- 不備 … 1 件のケースだけを見て分かるもの（項目の欠け・曖昧な表現・書きかけ・依存）
- 重複 … ケース同士を文字の n-gram で比べる
- 矛盾 … 前提と手順がほぼ同じ組の期待結果を、肯定・否定の向きで比べる。数値の条件も比べる
- 漏れ … 要件の網羅・異常系の有無・境界値・対になる条件
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import text as tx
from .model import Case, Finding, Item

# ---- 語の表（正規表現。NFKC と小文字化のあとの文に当てる） ----

VAGUE = [
    r"正しく", r"正常に", r"適切(に|な|で)", r"問題(なく|ない|がない|のない)", r"うまく", r"ちゃんと", r"きちんと",
    r"スムーズ", r"速やかに", r"すばやく|素早く", r"十分(に|な)", r"妥当(に|な)",
    r"(想定|期待|仕様|意図|設計|要件)(通り|どおり)", r"見やすい|わかりやすい|分かりやすい|使いやすい",
    r"違和感(が|の)?(ない|なく)", r"など", r"(?<![平同均対])等(?![しく級分価号])", r"適宜", r"必要に応じ",
    r"可能な限り|なるべく|できるだけ", r"ある程度", r"高速に|軽快", r"^.{0,8}(動作|機能)する(こと)?$",
    r"(画面|表示|内容|結果|動作|挙動|状態|画面表示)を確認(する|します)?$",
    r"\bcorrectly\b", r"\bproperly\b", r"\bappropriate(ly)?\b", r"\bas expected\b", r"works? (fine|well)",
    r"should work", r"user[- ]friendly", r"\betc\b", r"\bquickly\b",
]
PLACEHOLDER = (r"\btbd\b|\btodo\b|\bfixme\b|\bxxx+\b|未定|要確認|検討中|\?{2,}|●●|○○|〇〇|◯◯|△△|\*\*\*|"
               r"<[^>]*(ここ|記入|入力して)[^>]*>|\[(ここ|要|tbd)[^\]]*\]")
UNDEFINED_DATA = (r"任意の|適当な|適当に|何か(の|を)|なんらかの|何らかの|ランダムな|適宜|有効な(値|データ)|"
                  r"無効な(値|データ)|\bsome (value|data|text)\b|\bany (value|data|text)\b|\brandom\b")
DEPENDS = (r"同上|前述の|上記の?(手順|操作|ケース|状態)|前(の|回の)?(ケース|テスト|項目)|前項|"
           r"(tc|ケース|テスト|no\.?)[-_]?\d+\s*(と同(じ|様)|を(実施|実行)|の(後|あと|続き|結果|状態))")
OPERATION_END = r"(を|に)?(クリック|ダブルクリック|押下|入力|選択|タップ|ドラッグ|スクロール)(する|します)?$|を押す$|を開く$"
VERIFY_IN_STEP = (r"(ことを確認|を確認)(する|します)?$|(が|は)表示される(こと)?$|になっている(こと)?$|"
                  r"であること$|されること$|できること$")
MULTI_ACTION = r"して、|し、|て、|後、|たら、|てから"
NEGATIVE_WORDS = [
    r"エラー", r"失敗", r"異常", r"不正", r"無効", r"拒否", r"できない", r"警告", r"超え", r"超過", r"未入力",
    r"空欄", r"空の", r"範囲外", r"上限", r"下限", r"タイムアウト", r"切断", r"権限(が|の)?(ない|なし)", r"存在しない",
    r"重複", r"誤った|間違った|誤り", r"不足", r"禁止", r"ロック", r"invalid", r"error", r"fail", r"reject", r"denied",
    r"empty", r"timeout", r"forbidden", r"unauthori[sz]ed", r"not found", r"(status|ステータス|http|コード)\W{0,3}[45]\d\d", r"\b[45]\d\d\s?(エラー|error)",
]
# 対になる条件。a_label・b_label は指摘の文に使う。both=False は「a があるなら b も欲しい」だけを見る
PAIRS = [
    {"a_label": "ログインした状態", "b_label": "ログインしていない状態",
     "a": [r"ログイン済", r"ログイン状態", r"ログインした状態", r"ログインしている"],
     "b": [r"未ログイン", r"ログアウト(した|後|の状態|状態)", r"ログインしていない", r"ログイン前"], "both": True},
    {"a_label": "管理者", "b_label": "権限のない利用者", "a": [r"管理者", r"\badmin\b"],
     "b": [r"一般(ユーザー|利用者|会員)", r"権限(が|の)?(ない|なし)", r"権限不足", r"閲覧者"], "both": True},
    {"a_label": "オン（有効）にする", "b_label": "オフ（無効）にする",
     "a": [r"オンにする", r"有効にする", r"チェックを入れ", r"\bon\s?にする"],
     "b": [r"オフにする", r"無効にする", r"チェックを外", r"\boff\s?にする"], "both": True},
    {"a_label": "入力する", "b_label": "未入力・空欄のまま進める", "a": [r"(?<!未)入力す", r"(?<!未)入力し", r"\bfill\b"],
     "b": [r"未入力", r"空欄", r"空のまま", r"空に", r"ブランク", r"空文字", r"入力しない", r"入力せず", r"value=\"\""],
     "both": False},
    {"a_label": "一覧・検索結果", "b_label": "該当が 0 件のとき", "a": [r"一覧", r"検索結果", r"リスト"],
     "b": [r"\b0\s?件", r"ゼロ件", r"該当(なし|しない|がない|するものがない)", r"データ(が)?(ない|なし)", r"空の一覧",
           r"見つかりません", r"結果がない"], "both": False},
    {"a_label": "送信・通信", "b_label": "通信に失敗したとき", "a": [r"送信", r"\bapi\b", r"サーバー?", r"通信"],
     "b": [r"通信(エラー|失敗|断)", r"オフライン", r"タイムアウト", r"サーバー?エラー", r"ネットワーク", r"status=\"5\d\d\"",
           r"\b5\d\d\s?(エラー|error)"], "both": False},
    {"a_label": "作成・追加", "b_label": "削除", "a": [r"作成する", r"追加する", r"新規登録"], "b": [r"削除"], "both": False},
    {"a_label": "保存・送信", "b_label": "取り消し・キャンセル", "a": [r"保存", r"登録(する|ボタン)", r"送信", r"確定"],
     "b": [r"キャンセル", r"取り消", r"取消", r"中止", r"破棄"], "both": False},
]


def _rx(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns))


def _excerpt(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


class Checker:
    def __init__(self, cases, config, requirements=None, requirements_file="", requirement_lines=None):
        self.cases = cases
        self.cfg = config
        self.requirements = requirements  # {id: 本文} か None
        self._req_file = requirements_file
        self._req_lines = requirement_lines or {}
        self.findings = []
        self.vague = _rx(VAGUE + config.vague_words)
        self.negative = _rx(NEGATIVE_WORDS + config.negative_words)
        self.placeholder = re.compile(PLACEHOLDER)
        self.undefined = re.compile(UNDEFINED_DATA)
        self.depends = re.compile(DEPENDS)
        self.operation_end = re.compile(OPERATION_END)
        self.verify_in_step = re.compile(VERIFY_IN_STEP)
        self.multi_action = re.compile(MULTI_ACTION)

    def add(self, code, message, case: Case = None, item=None, hint="", excerpt="", others=(), file="", line=0):
        f = Finding(code, message, hint=hint, excerpt=excerpt)
        if case is not None:
            f.file, f.line = case.file, (item.line if item is not None and item.line else case.line)
            f.cases = [case.label, *[o.label for o in others]]
            f.related = [(o.file, o.line) for o in others]
        else:
            f.file, f.line = file, line
        self.findings.append(f)

    def run(self):
        self.check_ids()
        for c in self.cases:
            self.check_case(c)
        self.check_requirement_links()
        self.check_pairs()
        self.check_self_conflicts()
        self.check_numeric()
        self.check_negative_cases()
        self.check_pair_conditions()
        return self.findings

    # ---- 不備 ----

    def check_ids(self):
        by_file = defaultdict(list)
        for c in self.cases:
            by_file[c.file].append(c)
        for file, cs in by_file.items():
            with_id = [c for c in cs if c.id]
            if not with_id and len(cs) > 1:
                self.add("D01", f"どのケースにも ID がありません（{len(cs)} 件）", file=file, line=cs[0].line,
                         hint="TC-001 のような ID を振ると、指摘・不具合票・要件との対応を取りやすい")
            elif with_id:
                for c in cs:
                    if not c.id:
                        self.add("D01", "ID がありません", c, hint="ほかのケースと同じ形で ID を振る",
                                 excerpt=_excerpt(c.title))
        # ID はファイル（スイート）ごとに一意であればよい
        seen = defaultdict(list)
        for c in self.cases:
            if c.id:
                seen[(c.file, tx.normalize(c.id))].append(c)
        for cs in seen.values():
            if len(cs) > 1:
                self.add("D02", f"ID「{cs[0].id}」が {len(cs)} 件で使われています", cs[0], others=cs[1:],
                         hint="ID を振り直す")

    def check_case(self, c: Case):
        if c.kind == "case":
            if not c.title:
                self.add("D03", "タイトルがありません", c, hint="何を確かめるケースかを 1 行で書く")
            if not c.steps:
                self.add("D04", "手順がありません", c, hint="何をするかを順に書く")
            if not c.expected:
                self.add("D05", "期待結果がありません", c, hint="何が・どうなれば合格かを書く")
        texts = [(None, c.title, "title")] + [(i, i.text, i.field) for i in c.items()]
        for item, text, _field in texts:
            if text and self.placeholder.search(tx.normalize(text)):
                self.add("D07", "書きかけのまま残っています", c, item, hint="決めて書き切るか、ケースを外す",
                         excerpt=_excerpt(text))
            if text and self.depends.search(tx.normalize(text)):
                self.add("D09", "ほかのケースを前提にしていて、このケースだけでは実行できません", c, item,
                         hint="必要な前提・手順をこのケースに書き写す", excerpt=_excerpt(text))
        # 曖昧な表現は、合否を決める文（期待結果・チェック項目）に当てる
        judge = c.expected if c.kind == "case" else []
        if c.kind == "check" and c.title:
            judge = [Item(c.title, c.line, "title")]
        for item in judge:
            m = self.vague.search(tx.normalize(item.text))
            if m:
                self.add("D06", f"「{m.group(0)}」では合否を決められません", c, item,
                         hint="何が・どうなれば合格かを、見て判断できる形（表示される文言・値・件数など）で書く",
                         excerpt=_excerpt(item.text))
        for item in c.expected:
            for part in re.split(r"[。\n]", tx.normalize(item.text)):
                if self.operation_end.search(part.strip()):
                    self.add("D10", "期待結果に操作が書かれています", c, item,
                             hint="操作は手順へ移し、期待結果にはその結果どうなるかを書く", excerpt=_excerpt(item.text))
                    break
        for item in [*c.preconditions, *c.steps]:
            norm = tx.normalize(item.text)
            m = self.undefined.search(norm)
            if m:
                self.add("D08", f"「{m.group(0)}」では人によって使う値が変わります", c, item,
                         hint="使う値を具体的に書く（境界や種類を試すなら、値ごとにケースを分ける）",
                         excerpt=_excerpt(item.text))
        for item in c.steps:
            norm = tx.normalize(item.text)
            if self.verify_in_step.search(norm.rstrip("。. ")):
                self.add("D11", "手順に確認が書かれています", c, item, hint="確かめることは期待結果へ移す",
                         excerpt=_excerpt(item.text))
            if len(self.multi_action.findall(norm)) >= 3 or len(norm) > 150:
                self.add("D12", "1 つの手順に操作が多く、どこで失敗したか分かりにくくなります", c, item,
                         hint="1 手順 1 操作に分ける", excerpt=_excerpt(item.text))

    def check_requirement_links(self):
        body = [c for c in self.cases if c.kind == "case"]
        linked = [c for c in body if c.requirements]
        if body and linked and len(linked) * 2 >= len(body):
            for c in body:
                if not c.requirements:
                    self.add("D13", "どの要件を確かめるケースかが書かれていません", c,
                             hint="ほかのケースと同じ形で要件の ID を書く")
        if self.requirements is not None:
            known = {tx.normalize(r) for r in self.requirements}
            for c in self.cases:
                for r in c.requirements:
                    if tx.normalize(r) not in known:
                        self.add("D14", f"要件「{r}」は要件一覧にありません", c,
                                 hint="ID の綴りを確かめる（要件が消えたならケースも見直す）")
            covered = {tx.normalize(r) for c in self.cases for r in c.requirements}
            for rid, text in self.requirements.items():
                if tx.normalize(rid) not in covered:
                    self.add("G01", f"要件「{rid}」を確かめるケースがありません", file=self._req_file,
                             line=self._req_lines.get(rid, 0),
                             excerpt=_excerpt(text), hint="この要件を確かめるケースを足す")

    # ---- 重複・矛盾（ケースの組） ----

    def check_pairs(self):
        cs = self.cases
        if len(cs) < 2:
            return
        body = [tx.normalize(c.procedure_text() + "\n" + c.expected_text()) if c.kind == "case"
                else tx.normalize(c.title) for c in cs]
        exact = defaultdict(list)
        for i, b in enumerate(body):
            if b.strip():
                exact[b].append(i)
        in_exact = set()
        for idx in exact.values():
            if len(idx) > 1:
                in_exact.update(idx)
                first, *rest = [cs[i] for i in idx]
                self.add("U01", f"中身が完全に同じケースが {len(idx)} 件あります", first, others=rest,
                         hint="1 件にまとめる")
        exact_group = {i: tuple(idx) for idx in exact.values() for i in idx}

        def same_exact(i, j):
            return i in in_exact and exact_group.get(i) == exact_group.get(j)

        # 矛盾: 前提＋手順がほぼ同じ（値も同じ）組の期待結果を比べる
        conflict = set()
        proc_idx = [i for i, c in enumerate(cs) if c.kind == "case" and c.steps and c.expected]
        proc_vecs = tx.vectorize([tx.mask_values(cs[i].procedure_text()) for i in proc_idx])
        for a, b, sim in tx.similar_pairs(proc_vecs, self.cfg.same_steps):
            i, j = proc_idx[a], proc_idx[b]
            ci, cj = cs[i], cs[j]
            if same_exact(i, j) or tx.values(ci.procedure_text()) != tx.values(cj.procedure_text()):
                continue
            hit = tx.opposite(ci.expected_text(), cj.expected_text())
            if hit:
                conflict.add((i, j))
                self.add("C01", f"前提と手順が同じなのに、期待結果が逆です（「{_excerpt(hit[0].text, 30)}」と"
                                f"「{_excerpt(hit[1].text, 30)}」）", ci, others=[cj],
                         hint="どちらが正しいかを仕様で確かめる。条件が違うなら、その違いを前提に書く")
                continue
            ei, ej = tx.mask_values(ci.expected_text()), tx.mask_values(cj.expected_text())
            if ei != ej and tx.cosine(*tx.vectorize([ei, ej])) < 0.5:
                conflict.add((i, j))
                self.add("C03", "前提と手順が同じなのに、期待結果が違います", ci, others=[cj],
                         hint="同じ操作の結果なら 1 件にまとめる。結果を分ける条件があるなら前提に書く")

        # ほぼ同じ・値だけ違う。タイトルは比べない（同じ中身に別のタイトルが付くのが重複の典型）。
        # 長い共通の手順だけで似ていると判断しないよう、期待結果どうしも似ていることを求める
        full = [tx.mask_values(c.procedure_text() + "\n" + c.expected_text() if c.kind == "case" else c.title)
                for c in cs]
        vecs = tx.vectorize(full)
        exp_vecs = tx.vectorize([tx.mask_values(c.expected_text()) for c in cs])

        def values_of(c):
            return tx.values(c.procedure_text() + "\n" + c.expected_text() if c.kind == "case" else c.title)
        near, variants = [], []
        for i, j, sim in tx.similar_pairs(vecs, self.cfg.duplicate):
            if same_exact(i, j) or (i, j) in conflict or cs[i].kind != cs[j].kind:
                continue
            if cs[i].kind == "case" and tx.cosine(exp_vecs[i], exp_vecs[j]) < self.cfg.duplicate - 0.1:
                continue
            if values_of(cs[i]) == values_of(cs[j]):
                near.append((i, j, sim))
            elif cs[i].kind == "case":
                variants.append((i, j, sim))
        for pairs, code in ((near, "U02"), (variants, "U03")):
            for idx, sim in _groups(pairs):
                first, *rest = [cs[i] for i in idx]
                if code == "U02":
                    self.add(code, f"ほぼ同じケースが {len(idx)} 件あります（似ている度合い {sim:.2f}）", first,
                             others=rest, hint="違いが意図したものか確かめ、同じなら 1 件にまとめる")
                elif len(idx) >= 3:
                    self.add(code, f"値だけが違うよく似たケースが {len(idx)} 件あります", first, others=rest,
                             hint="1 件にまとめて、値と期待結果を表（パラメータ）にすると読みやすく、直し漏れも減る")

        titles = defaultdict(list)
        for i, c in enumerate(cs):
            if c.kind == "case" and c.title:
                titles[tx.normalize(c.title)].append(i)
        for idx in titles.values():
            if len(idx) > 1 and not all(i in in_exact for i in idx):
                first, *rest = [cs[i] for i in idx]
                self.add("U04", f"タイトル「{_excerpt(first.title, 30)}」が {len(idx)} 件で同じですが、中身が違います",
                         first, others=rest, hint="違いが分かるタイトルにする")

    def check_self_conflicts(self):
        """ケースの中の食い違い（同じ手順に付いた期待結果どうし・前提どうし）。"""
        for c in self.cases:
            for items in (c.expected, c.preconditions):
                hit = self._self_conflict(items)
                if hit:
                    self.add("C02", f"同じケースの中で「{_excerpt(hit[0], 30)}」と「{_excerpt(hit[1], 30)}」が食い違います",
                             c, hint="どちらかが誤り。時間の順に変わるなら手順を分けて書く")

    @staticmethod
    def _step_of(item):
        m = re.match(r"steps\[(\d+)\]", item.field)
        return m.group(1) if m else None

    def _self_conflict(self, items):
        cl = [(it, x) for it in items for x in tx.clauses(it.text)]
        for a in range(len(cl)):
            for b in range(a + 1, len(cl)):
                (ia, x), (ib, y) = cl[a], cl[b]
                # 別々の手順に付いた確認は時間の順に並ぶので、逆の向きでも食い違いではない
                if self._step_of(ia) != self._step_of(ib):
                    continue
                if x.negative != y.negative and tx.same_core(x.core, y.core):
                    return x.text, y.text
        return None

    # ---- 数値の条件 ----

    def _constraint_sources(self):
        """(制約, file, line, ラベル)。要件の本文とケースの文から集める。"""
        out = []
        if self.requirements:
            for rid, text in self.requirements.items():
                for k in tx.constraints(text):
                    out.append((k, self._req_file, self._req_lines.get(rid, 0), f"要件 {rid}"))
        for c in self.cases:
            for item_text, line in [(c.title, c.line)] + [(i.text, i.line or c.line) for i in c.items()]:
                for k in tx.constraints(item_text):
                    out.append((k, c.file, line, c.label))
        return out

    def check_numeric(self):
        sources = self._constraint_sources()
        by_key = defaultdict(list)
        for k, file, line, label in sources:
            if k.subject and k.unit:
                by_key[(k.subject, k.unit, k.kind)].append((k, file, line, label))
        for (subject, unit, kind), xs in by_key.items():
            vals = sorted({k.value for k, *_ in xs})
            if len(vals) > 1:
                where = "、".join(f"{lab}「{k.text}」" for k, _, _, lab in xs[:4])
                k, file, line, _ = xs[0]
                self.add("C04", f"「{subject}」の{'上限' if kind == 'max' else '下限'}が食い違います: {where}",
                         file=file, line=line, hint="仕様の値に合わせて、どちらかを直す")

        # 境界値。試した値は、条件として書いた所を除く数値と、入力に使った引用の文字数から拾う
        # （期待結果の文言の長さを、入力した値と取り違えないよう、期待結果は数えない）
        done = set()
        texts = [(c, "\n".join([c.title, c.procedure_text()])) for c in self.cases]
        for k, file, line, label in sources:
            if not k.unit:
                continue
            key = (k.subject, k.unit, k.kind, k.value)
            if key in done:
                continue
            done.add(key)
            points = {k.value, k.value + 1} if k.kind == "max" else {k.value - 1, k.value}
            points = {p for p in points if p >= 0}
            tested = set()
            for c, t in texts:
                if k.subject and k.subject not in tx.normalize(c.full_text()):
                    continue
                tested |= tx.tested_points(t, k.unit)
                if k.unit in ("文字", "桁"):
                    tested |= tx.quoted_lengths(t)
            missing = sorted(points - tested)
            if missing:
                name = f"「{k.subject}」の" if k.subject else ""
                pts = "・".join(f"{p}{k.unit}" for p in missing)
                self.add("G03", f"{name}条件「{k.text}」の境界 {pts} を試すケースがありません（{label}）",
                         file=file, line=line,
                         hint=f"境界のちょうど（{min(points)}{k.unit}）と、1 つ外（{max(points)}{k.unit}）の両方を試す"
                         if len(points) == 2 else "境界の値を試すケースを足す")

    # ---- 漏れ ----

    def _is_negative(self, c: Case) -> bool:
        if self.negative.search(tx.normalize(c.full_text())):
            return True
        cond = "\n".join([c.title, c.procedure_text()])
        return any(x.negative for x in tx.clauses(cond))

    def check_negative_cases(self):
        body = [c for c in self.cases if c.kind == "case"]
        # 機能のまとまり（ファイルと、その中の suite・feature）ごとに見る。要件ごとだと、
        # 「正しく入れたら通る」だけを書いた要件まで毎回指摘してしまう
        groups = defaultdict(list)
        for c in body:
            name = " / ".join(x for x in c.group if x) or c.file.rsplit("/", 1)[-1]
            groups[(c.file, f"「{name}」")].append(c)
        for (_file, name), cs in groups.items():
            if cs and not any(self._is_negative(c) for c in cs):
                self.add("G02", f"{name}のケース {len(cs)} 件はすべて正常系です", cs[0],
                         hint="誤った入力・権限なし・通信の失敗など、うまくいかないときのケースを足す")

    def check_pair_conditions(self):
        pairs = PAIRS + [{"both": True, **p} for p in self.cfg.pairs]
        texts = [(c, tx.normalize(c.full_text())) for c in self.cases]
        for p in pairs:
            a_label = p.get("a_label") or p.get("name") or "・".join(p["a"][:2])
            b_label = p.get("b_label") or "・".join(p["b"][:2])
            ra, rb = _rx(p["a"]), _rx(p["b"])
            a_hit = [c for c, t in texts if ra.search(t)]
            b_hit = [c for c, t in texts if rb.search(t)]
            if a_hit and not b_hit:
                self.add("G04", f"「{a_label}」を試すケースはありますが、「{b_label}」を試すケースがありません",
                         a_hit[0], hint=f"「{b_label}」のケースを足す（要らないなら設定でこの規則を止める）")
            elif b_hit and not a_hit and p.get("both"):
                self.add("G04", f"「{b_label}」を試すケースはありますが、「{a_label}」を試すケースがありません",
                         b_hit[0], hint=f"「{a_label}」のケースを足す（要らないなら設定でこの規則を止める）")


def _groups(pairs):
    """似ている組をつないだまとまりと、その中でいちばん高い似ている度合い。"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in pairs:
        parent[find(j)] = find(i)
    members, best = defaultdict(set), defaultdict(float)
    for i, j, s in pairs:
        r = find(i)
        members[r] |= {i, j}
        best[r] = max(best[r], s)
    return [(sorted(m), best[r]) for r, m in sorted(members.items(), key=lambda x: min(x[1]))]


def run_checks(cases, config, requirements=None, requirements_file="", requirement_lines=None):
    findings = Checker(cases, config, requirements, requirements_file, requirement_lines).run()
    out = []
    for f in findings:
        sev = config.severity(f.code)
        if sev is None:
            continue
        f.severity = sev
        out.append(f)
    return out
