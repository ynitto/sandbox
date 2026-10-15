"""文の処理 — 正規化・似ている度合い・肯定否定・数値の条件。

形態素解析は Janome（純 Python、辞書同梱、Apache-2.0）。似ている度合いは文字の 2〜3 文字組
（n-gram）の余弦で測る。分かち書きに頼らないので、表記が少し揺れても拾える。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

_tokenizer = None


def tokenizer():
    global _tokenizer
    if _tokenizer is None:
        try:
            from janome.tokenizer import Tokenizer
        except ImportError as e:  # pragma: no cover - 導入手順の誤り
            raise SystemExit("Janome が入っていません。`pip install janome` で入れてください") from e
        _tokenizer = Tokenizer()
    return _tokenizer


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


@dataclass(frozen=True)
class Tok:
    surface: str
    base: str
    pos: str
    pos2: str


@lru_cache(maxsize=50000)
def tokens(text: str) -> tuple:
    out = []
    for t in tokenizer().tokenize(normalize(text)):
        p = t.part_of_speech.split(",")
        base = t.base_form if t.base_form != "*" else t.surface
        out.append(Tok(t.surface, base, p[0], p[1]))
    return tuple(out)


# ---- 値（数値・引用）と、それを伏せた文 ----

_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|\"[^\"]*\"|“[^”]*”|'[^'\s][^']*'")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def values(text: str) -> tuple:
    t = normalize(text)
    quoted = _QUOTED.findall(t)
    rest = _QUOTED.sub(" ", t)
    return tuple(sorted(quoted + _NUMBER.findall(rest)))


def mask_values(text: str) -> str:
    t = _QUOTED.sub('"', normalize(text))
    return _NUMBER.sub("0", t)


# ---- 似ている度合い ----

def _ngrams(text: str) -> Counter:
    t = re.sub(r"\s+", "", text)
    c = Counter()
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            c[t[i:i + n]] += 1
    if len(t) < 2 and t:
        c[t] += 1
    return c


def vectorize(texts: list) -> list:
    """文の列を、L2 正規化した n-gram のベクトル（dict）の列にする。

    IDF で重みを付けない。LLM が出すケースは同じ言い回しの繰り返しが多く、IDF を掛けると
    共通部分が軽くなって、1 文字違いの重複まで「似ていない」と出てしまうため。
    """
    vecs = []
    for c in (_ngrams(t) for t in texts):
        v = {f: 1 + math.log(tf) for f, tf in c.items()}
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        vecs.append({f: w / norm for f, w in v.items()})
    return vecs


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(f, 0.0) for f, w in a.items())


def similar_pairs(vecs: list, threshold: float) -> list:
    """余弦が threshold 以上の組 (i, j, 類似度) を、取りこぼしなく返す。

    全組を比べると件数の 2 乗かかるので、前置フィルタ（All-Pairs）で候補を絞る。
    ベクトルは長さ 1 なので、各文の「よく出る特徴」側の前半部分のノルムが threshold 未満なら、
    その前半だけで余弦が threshold に届くことはない（コーシー＝シュワルツ）。そこで後半だけを
    索引に入れ、共有する特徴で集めた部分和に前半のノルムを足しても届かない組は捨てる。
    残った候補は正確な余弦で確かめる。
    """
    n = len(vecs)
    if n < 2:
        return []
    df = Counter(f for v in vecs for f in v)
    postings = defaultdict(list)
    prefix_norm = [0.0] * n
    for j, v in enumerate(vecs):
        feats = sorted(v.items(), key=lambda fw: (-df[fw[0]], fw[0]))  # よく出る特徴が先
        acc = 0.0
        k = 0
        while k < len(feats) and math.sqrt(acc + feats[k][1] ** 2) < threshold:
            acc += feats[k][1] ** 2
            k += 1
        prefix_norm[j] = math.sqrt(acc)
        for f, w in feats[k:]:
            postings[f].append((j, w))
    out = []
    for i, v in enumerate(vecs):
        if not v:
            continue
        acc = defaultdict(float)
        for f, w in v.items():
            for j, wj in postings.get(f, ()):
                if j > i:
                    acc[j] += w * wj
        for j, partial in acc.items():
            if partial + prefix_norm[j] >= threshold:
                s = cosine(v, vecs[j])
                if s >= threshold - 1e-9:
                    out.append((i, j, s))
    return out


# ---- 肯定・否定 ----

FUNC_VERBS = {"する", "れる", "られる", "なる", "いる", "ある", "できる", "くる", "いく", "おる", "せる",
              "させる", "もらう", "しまう", "みる", "おく", "くれる", "やる"}
NEG_AUX = {"ない", "ぬ", "ん"}
NEG_PREFIX = {"未", "不", "非", "無"}
STOP_NOUNS = {"こと", "もの", "よう", "ため", "とき", "時", "場合", "の", "ん", "それ", "これ", "そう"}
# 反対語は (正の形, 否定か) にそろえる
ANTONYMS = {
    "無効": ("有効", True), "有効": ("有効", False), "非表示": ("表示", True), "失敗": ("成功", True),
    "成功": ("成功", False), "不可": ("可能", True), "不可能": ("可能", True), "可能": ("可能", False),
    "拒否": ("許可", True), "許可": ("許可", False), "不一致": ("一致", True), "非活性": ("活性", True),
    "なし": ("あり", True), "無し": ("あり", True), "あり": ("あり", False), "有り": ("あり", False),
    "不正": ("正当", True), "off": ("on", True), "on": ("on", False), "オフ": ("オン", True),
    "オン": ("オン", False), "hidden": ("visible", True), "visible": ("visible", False),
    "disabled": ("enabled", True), "enabled": ("enabled", False), "invisible": ("visible", True),
    "fail": ("succeed", True), "fails": ("succeed", True), "failed": ("succeed", True),
    "succeed": ("succeed", False), "succeeds": ("succeed", False), "success": ("succeed", False),
    "not": (None, True), "no": (None, True), "cannot": (None, True), "never": (None, True),
}
_CLAUSE_SPLIT = re.compile(r"[。;；\n]|(?<=[れしてりきけせい])[、,]")


@dataclass(frozen=True)
class Clause:
    text: str
    core: frozenset
    negative: bool


@lru_cache(maxsize=50000)
def clauses(text: str) -> tuple:
    """文を節に分け、節ごとに（中身の語の集合, 否定か）を返す。

    否定は数で持つ（偶奇）。「未入力でエラーが表示されない」は 未 と ない で打ち消し合う。
    """
    out = []
    for part in _CLAUSE_SPLIT.split(normalize(text)):
        part = part.strip(" 、,.")
        if not part:
            continue
        core, neg, prev_prefix = set(), 0, False
        for t in tokens(part):
            w = t.base
            if t.pos == "接頭詞" and t.surface in NEG_PREFIX:
                neg ^= 1
                prev_prefix = True
                continue
            if w in ANTONYMS:
                canon, is_neg = ANTONYMS[w]
                neg ^= int(is_neg)
                if canon:
                    core.add(canon)
            elif t.pos == "助動詞" and w in NEG_AUX:
                neg ^= 1
            elif t.pos == "形容詞" and w in ("ない", "無い"):
                neg ^= 1
            elif t.pos == "名詞" and t.pos2 not in ("非自立", "代名詞", "接尾") and w not in STOP_NOUNS:
                core.add(w)
            elif t.pos == "名詞" and t.pos2 == "接尾" and prev_prefix:
                core.add(w)
            elif t.pos == "動詞" and t.pos2 == "自立" and w not in FUNC_VERBS:
                core.add(w)
            elif t.pos == "形容詞" and t.pos2 == "自立":
                core.add(w)
            prev_prefix = False
        if core:
            out.append(Clause(part, frozenset(core), bool(neg)))
    return tuple(out)


def same_core(a: frozenset, b: frozenset) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    return len(a & b) / len(a | b) >= 0.8


def opposite(a_text: str, b_text: str):
    """2 つの文の中に、同じ中身で肯定と否定が逆の節があれば (節a, 節b) を返す。"""
    ca, cb = clauses(a_text), clauses(b_text)
    for x in ca:
        for y in cb:
            if x.negative != y.negative and same_core(x.core, y.core):
                # 同じ文の中に同じ向きの節もあるなら、言い換えの重なりとみなして食い違いにしない
                if any(z.negative == x.negative and same_core(z.core, x.core) for z in cb):
                    continue
                if any(z.negative == y.negative and same_core(z.core, y.core) for z in ca):
                    continue
                return x, y
    return None


# ---- 数値の条件 ----

UNITS = ("文字", "桁", "件", "個", "回", "円", "秒", "分", "時間", "日", "週間", "か月", "ヶ月", "カ月",
         "年", "歳", "才", "名", "人", "行", "列", "ページ", "枚", "点", "ポイント", "バイト", "byte", "bytes",
         "kb", "mb", "gb", "px", "%", "ms", "characters", "chars", "items", "times", "seconds")
_UNIT = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
_BOUND = r"以上|以下|未満|超え|超|を超える|より多い|より少ない|まで|以内|or more|or less|at least|at most"
_PAT_SUFFIX = re.compile(rf"(\d+)\s*({_UNIT})?\s*({_BOUND})")
_PAT_PREFIX = re.compile(rf"(最大|上限|最長|最多|最小|下限|最短|最少|max(?:imum)?|min(?:imum)?)\s*(?:は|が|で|:|=)?\s*(\d+)\s*({_UNIT})?")
_PAT_POINT = re.compile(rf"(\d+)\s*({_UNIT})")

MAX_WORDS = ("以下", "未満", "まで", "以内", "より少ない", "or less", "at most")
_UNIT_ALIAS = {"才": "歳", "ヶ月": "か月", "カ月": "か月", "chars": "文字", "characters": "文字", "bytes": "バイト",
               "byte": "バイト"}


@dataclass(frozen=True)
class Constraint:
    subject: str
    unit: str
    kind: str  # "max" / "min"（どちらも境界を含む整数に直す）
    value: int
    text: str


def _unit(u):
    u = (u or "").strip()
    return _UNIT_ALIAS.get(u, u)


def _subject(prefix: str) -> str:
    """数値の手前の、いちばん近い名詞のまとまり（「パスワードは」なら パスワード）。"""
    toks = tokens(prefix[-40:])
    words, started = [], False
    for k in range(len(toks) - 1, -1, -1):
        t = toks[k]
        after_number = k > 0 and toks[k - 1].pos2 == "数"
        is_unit = re.fullmatch(rf"({_UNIT}|{_BOUND}|最大|最小|上限|下限|\d+)", t.surface) and \
            (after_number or t.pos2 != "接尾")
        is_noun = t.pos == "名詞" and t.pos2 not in ("数", "非自立", "代名詞") and t.surface not in ("の", "ん") \
            and not is_unit
        if is_noun:
            words.append(t.surface)
            started = True
        elif started:
            break
        elif t.pos in ("助詞", "記号") or t.pos2 == "数" or re.fullmatch(rf"({_UNIT}|{_BOUND})", t.surface) \
                or t.surface in ("最大", "最小", "上限", "下限", "の", "で"):
            continue
        elif t.pos == "名詞":
            continue
        else:
            break
    return "".join(reversed(words))


def constraints(text: str) -> list:
    t = normalize(text)
    out = []
    for m in _PAT_SUFFIX.finditer(t):
        n, unit, bound = int(m.group(1)), _unit(m.group(2)), m.group(3)
        if bound in ("未満", "より少ない"):
            kind, value = "max", n - 1
        elif bound in MAX_WORDS:
            kind, value = "max", n
        elif bound in ("超", "超え", "を超える", "より多い"):
            kind, value = "min", n + 1
        else:
            kind, value = "min", n
        out.append(Constraint(_subject(t[:m.start()]), unit, kind, value, m.group(0)))
    for m in _PAT_PREFIX.finditer(t):
        word = m.group(1)
        kind = "max" if word in ("最大", "上限", "最長", "最多") or word.startswith("max") else "min"
        out.append(Constraint(_subject(t[:m.start()]), _unit(m.group(3)), kind, int(m.group(2)), m.group(0)))
    seen, uniq = set(), []
    for c in out:
        key = (c.subject, c.unit, c.kind, c.value)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def tested_points(text: str, unit: str) -> set:
    """文に出てくる「N 単位」の N（条件として書かれたものは除く）。"""
    t = normalize(text)
    spans = [m.span() for p in (_PAT_SUFFIX, _PAT_PREFIX) for m in p.finditer(t)]
    out = set()
    for m in _PAT_POINT.finditer(t):
        if _unit(m.group(2)) != unit:
            continue
        if any(a <= m.start() < b for a, b in spans):
            continue
        out.add(int(m.group(1)))
    return out


def quoted_lengths(text: str) -> set:
    return {len(q) - 2 for q in _QUOTED.findall(unicodedata.normalize("NFKC", text or ""))}
