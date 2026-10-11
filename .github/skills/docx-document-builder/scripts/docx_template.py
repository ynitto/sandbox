#!/usr/bin/env python3
"""docx テンプレートへ内容を流し込む（文書のスタイル・章・段落・改行はテンプレートのまま保つ）。

サブコマンド（xlsx-report-builder・pptx-presentation-builder と同じ並び）:

    inspect  テンプレートの事実（段落・見出し・リスト・表・文字の書式・収まる字数・仮値の疑い・来歴）を出す
    analyze  定義ファイルの下書きを作る（章・繰り返す節・本文の段落・リスト・表を機械的に拾う）
    check    定義とテンプレートの整合、決め忘れの値を検査する
    render   テンプレート + 定義 + データから docx を再構成する
    extract  記入済みの docx から、定義に沿ってデータを取り出す（render の逆）
    export   定義を埋め込んだ、単体で動く専用スクリプトを書き出す

文字を差し替えるときは、段落（スタイル・字下げ・行間・番号）と文字（フォント・大きさ・色・太字）の書式を、
テンプレートの見本の段落から取る。データの改行（\\n）は段落の中の改行、空行（\\n\\n）は段落の区切りになる。
テンプレートのサンプルと同じ粒度（max_chars・max_items）を超えたら止める。定義ファイルとデータの書式は
references/template.md。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import math
import os
import re
import sys
import unicodedata
import zipfile
from copy import deepcopy
from typing import Any

from lxml import etree
import docx

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

DEF_VERSION = 1
TWIPS_PER_PT = 20
TWIPS_PER_CM = 567
EMU_PER_PT = 12700
LINE_FACTOR = 1.2          # 1 行の高さ（文字の大きさに対する倍率）の既定
LATIN_WIDTH = 0.55         # 半角文字の幅（全角を 1 とした目安）
GRANULARITY = 1.5          # サンプルの文字数・段落の数の何倍までを、同じ粒度と見るか
DEFAULT_SIZE = 10.5        # 文字の大きさが、どこにも書かれていないとき
DEFAULT_PAGE = (11906, 16838, 1985, 1701, 1701, 1701)   # A4 縦。幅・高さ・上・下・左・右（twips）
CELL_MARGIN = 108          # 表のセルの左右の余白の既定（twips）

PLACEHOLDER_RE = re.compile(r"(〇〇|○○|●●|◯◯|△△|□□|＊＊|\*\*|(?<![A-Za-z])x{2,}(?![A-Za-z])|サンプル|ダミー|記入例|"
                            r"テキストを入力|ここに|を書く|を入力|sample|dummy|lorem|yyyy|YYYY|xxxx|XXXX|mm/dd|20\d\d[/-]0?1[/-]0?1)", re.I)
NOTE_RE = re.compile(r"^\s*(※|＊|注[:：）)]|Note[:：])")
LABEL_RE = re.compile(r"^(?:[^:：]{1,16}[:：]|【[^】]{1,16}】|■[^:：]{1,16})$")
LABEL_VALUE_RE = re.compile(r"^([^:：\t]{1,16}[:：][ 　\t]*|[^:：\t]{1,16}\t+)(?=\S)")
HUMAN_RE = re.compile(r"(印$|押印|捺印|検印|署名|サイン|自署|承認者?$|決裁|確認者|受付者?$|手書き|記入欄)")
DATE_RE = re.compile(r"^(令和|平成)?\s*[0-9０-９元yYｙＹ〇○]{1,4}\s*[年/.\-]\s*[0-9０-９mMｍＭ〇○]{1,2}\s*[月/.\-]\s*"
                     r"[0-9０-９dDｄＤ〇○]{1,2}\s*日?$")
ADDRESSEE_RE = re.compile(r"(御中|様|殿)$")
FORM_TITLE_RE = re.compile(r"(書|届|票|簿|願|伺)$")   # 申請書・届・伝票… 様式の名前（毎回同じ表題）
NUMBERING_RE = re.compile(r"^\s*((第\s*[0-9０-９一二三四五六七八九十]+\s*[章節項部]|[0-9０-９]+(?:[.．][0-9０-９]+)*[.．]?|"
                          r"[（(][0-9０-９]+[）)]|[①-⑳]|[A-ZＡ-Ｚ][.．])\s*)+")
MARK_ON = ("○", "◯", "〇", "●", "◎", "✓", "✔", "レ", "☑", "■")
MARK_OFF = ("×", "✕", "✖", "☐", "□", "-", "－", "ー", "―")
DATE_PARTS = ("year", "month", "day", "hour", "minute")
TEXT_CHILDREN = {"rPr", "t", "br", "tab", "cr", "noBreakHyphen", "softHyphen", "lastRenderedPageBreak"}
SKIP_ANCESTORS = {"txbxContent", "del", "moveFrom", "fldSimple"}


class TemplateError(Exception):
    """定義ファイル・データ・テンプレートの不整合。"""


def qw(tag: str) -> str:
    return f"{{{NS_W}}}{tag}"


def local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def wval(el, tag: str, attr: str = "val") -> "str | None":
    if el is None:
        return None
    c = el.find(qw(tag))
    return c.get(qw(attr)) if c is not None else None


def open_doc(source: "str | bytes"):
    return docx.Document(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)


def read_bytes(source: "str | bytes") -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    with open(source, "rb") as f:
        return f.read()


def _short(s: str, n: int) -> str:
    s = s.replace("\n", " / ").replace("\t", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------------------
# 文字（段落の中の run）
# ---------------------------------------------------------------------------

def _ancestors_to(el, stop) -> list[str]:
    out = []
    a = el.getparent()
    while a is not None and a is not stop:
        out.append(local(a))
        a = a.getparent()
    return out


def text_runs(p) -> list:
    """段落の中の、手で書かれた文字の run（フィールドの結果・削除した文字・図形の中の文字・改ページは除く）。"""
    out, depth = [], 0
    for r in p.iter(qw("r")):
        if SKIP_ANCESTORS & set(_ancestors_to(r, p)):
            continue
        fc = r.find(qw("fldChar"))
        if fc is not None:
            kind = fc.get(qw("fldCharType"))
            depth += 1 if kind == "begin" else (-1 if kind == "end" else 0)
            continue
        if depth > 0 or r.find(qw("instrText")) is not None:
            continue
        if all(local(c) in TEXT_CHILDREN for c in r) and \
                not any(local(c) == "br" and c.get(qw("type")) in ("page", "column") for c in r):
            out.append(r)
    return out


def run_text(r) -> str:
    out = []
    for c in r:
        tag = local(c)
        if tag == "t":
            out.append(c.text or "")
        elif tag in ("br", "cr"):
            out.append("\n")
        elif tag == "tab":
            out.append("\t")
        elif tag == "noBreakHyphen":
            out.append("-")
    return "".join(out)


def para_text(p) -> str:
    return "".join(run_text(r) for r in text_runs(p))


def has_field(p) -> bool:
    return p.find(f".//{qw('fldChar')}") is not None or p.find(f".//{qw('fldSimple')}") is not None


def has_drawing(el) -> bool:
    return any(local(e) in ("drawing", "pict", "object") for e in el.iter())


def has_page_break(p) -> bool:
    if any(local(c) == "br" and c.get(qw("type")) == "page" for c in p.iter(qw("br"))):
        return True
    ppr = p.find(qw("pPr"))
    return ppr is not None and ppr.find(qw("pageBreakBefore")) is not None and wval(ppr, "pageBreakBefore") not in ("0", "false")


def has_section_break(p) -> bool:
    ppr = p.find(qw("pPr"))
    return ppr is not None and ppr.find(qw("sectPr")) is not None


def split_paras(text: str) -> list[str]:
    """データの文字を段落に分ける。空行（\\n\\n）が段落の区切り。1 つの \\n は段落の中の改行のまま。"""
    if not text:
        return []
    return [s.strip("\n") for s in re.split(r"\n[ \t　]*\n", text.replace("\r\n", "\n"))]


def _make_run(rpr, text: str):
    r = etree.Element(qw("r"))
    if rpr is not None:
        r.append(deepcopy(rpr))
    buf = []

    def flush():
        if buf:
            t = etree.SubElement(r, qw("t"))
            t.text = "".join(buf)
            if t.text != t.text.strip(" ") or "  " in t.text:
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            buf.clear()

    for ch in text:
        if ch == "\n":
            flush()
            etree.SubElement(r, qw("br"))
        elif ch == "\t":
            flush()
            etree.SubElement(r, qw("tab"))
        else:
            buf.append(ch)
    flush()
    return r


def _set_run_text(r, text: str) -> None:
    for c in list(r):
        if local(c) != "rPr":
            r.remove(c)
    tmp = _make_run(None, text)
    for c in list(tmp):
        r.append(c)


def _mark_rpr(p):
    """段落記号の文字の書式（空の段落の書式）を、run の書式として使える形で。"""
    ppr = p.find(qw("pPr"))
    rpr = ppr.find(qw("rPr")) if ppr is not None else None
    if rpr is None:
        return None
    rpr = deepcopy(rpr)
    for c in list(rpr):
        if local(c) in ("ins", "del", "moveFrom", "moveTo", "rPrChange"):
            rpr.remove(c)
    return rpr


def set_para_text(p, text: str, after: "str | None" = None) -> None:
    """段落の文字を差し替える。段落の書式（pPr）と、文字の書式（最初の文字の run の rPr）を保つ。

    after があれば、段落の頭のその文字（`件名：` のようなラベル）までの run を残し、後ろだけを差し替える。
    """
    runs = text_runs(p)
    kept: list = []
    if after:
        acc = ""
        for r in runs:
            if len(acc) >= len(after):
                break
            t = run_text(r)
            if len(acc) + len(t) <= len(after):
                kept.append(r)
                acc += t
                continue
            cut = len(after) - len(acc)
            head = deepcopy(r)
            _set_run_text(head, t[:cut])
            _set_run_text(r, t[cut:])
            r.addprevious(head)
            kept.append(head)
            acc = after
        runs = [r for r in text_runs(p) if r not in kept]
    # 書式は、差し替える部分の最初の run から（書式の無い run ならスタイルのまま）。差し替える文字が無かった段落は、
    # 段落記号の書式から取る（ラベルの太字を値に写さない）
    rpr = runs[0].find(qw("rPr")) if runs else _mark_rpr(p)
    new = _make_run(rpr, text) if text else None
    if new is not None:
        if runs:
            runs[0].addprevious(new)
        elif kept:
            kept[-1].addnext(new)
        else:
            p.append(new)
    for r in runs:
        r.getparent().remove(r)
    for e in list(p.iter(qw("proofErr"), qw("lastRenderedPageBreak"))):
        e.getparent().remove(e)
    for tag in ("hyperlink", "smartTag"):
        for e in list(p.iter(qw(tag))):
            if e.find(f".//{qw('r')}") is None:
                e.getparent().remove(e)


def _fresh(p):
    """複製した段落から、同じ文書に 2 つあってはいけないもの（ブックマーク・段落の id）を外す。"""
    for e in list(p.iter(qw("bookmarkStart"), qw("bookmarkEnd"))):
        e.getparent().remove(e)
    for e in p.iter():
        for attr in (f"{{{NS_W14}}}paraId", f"{{{NS_W14}}}textId"):
            e.attrib.pop(attr, None)
    return p


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "\n\n".join(to_text(v) for v in value)
    return str(value)


def text_units(s: str) -> float:
    """表示幅（全角 1・半角 LATIN_WIDTH）。"""
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else LATIN_WIDTH
               for ch in s if ch not in "\n")


# ---------------------------------------------------------------------------
# 文書の見え方（スタイル・番号・用紙・収まる量）
# ---------------------------------------------------------------------------

class Capacity:
    def __init__(self, cpl: float, size: float, line_pt: float):
        self.cpl, self.size, self.line_pt = cpl, size, line_pt

    def lines_for(self, text: str) -> int:
        """1 つの段落が何行になるか（段落の中の改行で行が分かれる）。"""
        return sum(max(1, math.ceil(text_units(seg) / self.cpl - 1e-9)) for seg in (text or "").split("\n"))

    def describe(self) -> str:
        return f"1 行 約 {int(self.cpl)} 字（{self.size:g}pt）"


class DocView:
    """文書の段落・表と、スタイルをたどった書式（文字の大きさ・フォント・字下げ・用紙）を引く。"""

    def __init__(self, doc):
        self.doc = doc
        self.body = doc.element.body
        self.blocks = [c for c in self.body if local(c) in ("p", "tbl", "sdt")]
        self.number = {id(el): n for n, el in enumerate(self.blocks, start=1)}
        styles = doc.styles.element
        self.styles = {s.get(qw("styleId")): s for s in styles.findall(qw("style"))}
        self.default_style = next((sid for sid, s in self.styles.items()
                                   if s.get(qw("type")) == "paragraph" and s.get(qw("default")) in ("1", "true")), "")
        dd = styles.find(qw("docDefaults"))
        self.rpr_default = dd.find(f"{qw('rPrDefault')}/{qw('rPr')}") if dd is not None else None
        self.ppr_default = dd.find(f"{qw('pPrDefault')}/{qw('pPr')}") if dd is not None else None
        try:
            self.numbering = doc.part.numbering_part.element
        except (KeyError, NotImplementedError):
            self.numbering = None
        self._theme = None

    # --- スタイル ---
    def style_of(self, p) -> str:
        return wval(p.find(qw("pPr")), "pStyle") or self.default_style

    def style_name(self, sid: str) -> str:
        s = self.styles.get(sid)
        return (wval(s, "name") or sid) if s is not None else (sid or "")

    def chain(self, sid: str) -> list:
        out, seen = [], set()
        while sid and sid not in seen and sid in self.styles:
            seen.add(sid)
            out.append(self.styles[sid])
            sid = wval(self.styles[sid], "basedOn")
        return out

    def _pprs(self, p) -> list:
        out = [p.find(qw("pPr"))] + [s.find(qw("pPr")) for s in self.chain(self.style_of(p))] + [self.ppr_default]
        return [e for e in out if e is not None]

    def _rprs(self, p, r=None) -> list:
        out = []
        if r is not None:
            out.append(r.find(qw("rPr")))
            rs = wval(r.find(qw("rPr")), "rStyle")
            if rs:
                out += [s.find(qw("rPr")) for s in self.chain(rs)]
        else:
            ppr = p.find(qw("pPr"))
            out.append(ppr.find(qw("rPr")) if ppr is not None else None)
        out += [s.find(qw("rPr")) for s in self.chain(self.style_of(p))] + [self.rpr_default]
        return [e for e in out if e is not None]

    def heading_level(self, p) -> "int | None":
        """見出しの段（1 始まり）。アウトラインの段（outlineLvl）か、スタイルの名前（Heading 1・見出し 1）で見る。"""
        if local(p) != "p":
            return None
        for ppr in self._pprs(p):
            v = wval(ppr, "outlineLvl")
            if v is not None:
                return int(v) + 1 if v.isdigit() and int(v) < 9 else None
        m = re.match(r"^(heading|見出し)\s*(\d)$", self.style_name(self.style_of(p)).lower())
        return int(m.group(2)) if m else None

    def numpr(self, p) -> "tuple[str, int] | None":
        """番号・記号の (numId, 段)。段落に直接か、スタイルに書かれたもの。numId 0 は番号なし。"""
        for ppr in self._pprs(p):
            np_ = ppr.find(qw("numPr"))
            if np_ is not None:
                num = wval(np_, "numId")
                if num is None:
                    continue
                if num == "0":
                    return None
                lvl = wval(np_, "ilvl")
                if lvl is None:
                    name = self.style_name(self.style_of(p))
                    m = re.search(r"\s(\d)$", name)
                    lvl = str(int(m.group(1)) - 1) if m else "0"
                return num, int(lvl)
        return None

    def is_list(self, p) -> bool:
        return self.numpr(p) is not None and self.heading_level(p) is None

    def list_level(self, p) -> int:
        n = self.numpr(p)
        return n[1] if n else 0

    def _lvl(self, num_id: str, ilvl: int):
        if self.numbering is None:
            return None
        num = next((n for n in self.numbering.findall(qw("num")) if n.get(qw("numId")) == str(num_id)), None)
        if num is None:
            return None
        for ov in num.findall(qw("lvlOverride")):
            if ov.get(qw("ilvl")) == str(ilvl) and ov.find(qw("lvl")) is not None:
                return ov.find(qw("lvl"))
        aid = wval(num, "abstractNumId")
        absn = next((a for a in self.numbering.findall(qw("abstractNum")) if a.get(qw("abstractNumId")) == aid), None)
        if absn is None:
            return None
        return next((lv for lv in absn.findall(qw("lvl")) if lv.get(qw("ilvl")) == str(ilvl)), None)

    def is_bullet(self, num_id: str, ilvl: int) -> bool:
        lv = self._lvl(num_id, ilvl)
        return lv is not None and wval(lv, "numFmt") in ("bullet", "none")

    # --- 文字の書式 ---
    def font_size(self, p, r=None) -> float:
        r = r if r is not None else next(iter(text_runs(p)), None) if local(p) == "p" else None
        for rpr in self._rprs(p, r):
            v = wval(rpr, "sz")
            if v and v.isdigit():
                return int(v) / 2
        return DEFAULT_SIZE

    def font_name(self, p, r=None) -> str:
        r = r if r is not None else next(iter(text_runs(p)), None)
        for rpr in self._rprs(p, r):
            rf = rpr.find(qw("rFonts"))
            if rf is None:
                continue
            for attr in ("eastAsia", "ascii"):
                if rf.get(qw(attr)):
                    return rf.get(qw(attr))
            for attr in ("eastAsiaTheme", "asciiTheme"):
                if rf.get(qw(attr)):
                    return self.theme_font(rf.get(qw(attr)))
        return ""

    def theme_font(self, which: str) -> str:
        if self._theme is None:
            self._theme = False
            for rel in self.doc.part.rels.values():
                if rel.reltype.endswith("/theme") and not rel.is_external:
                    self._theme = etree.fromstring(rel.target_part.blob)
        if self._theme is False:
            return ""
        group = "majorFont" if which.startswith("major") else "minorFont"
        font = self._theme.find(f".//{{{NS_A}}}{group}")
        if font is None:
            return ""
        if "EastAsia" in which:
            ea = font.find(f"{{{NS_A}}}ea")
            if ea is not None and ea.get("typeface"):
                return ea.get("typeface")
            jp = font.find(f"{{{NS_A}}}font[@script='Jpan']")
            return jp.get("typeface") if jp is not None else ""
        latin = font.find(f"{{{NS_A}}}latin")
        return latin.get("typeface", "") if latin is not None else ""

    def _ppr_attr(self, p, tag: str, attrs: tuple, numbered: bool = True) -> "int | None":
        """字下げ・行間などの値。段落 → 番号の段 → スタイル → 既定の順。"""
        pprs = self._pprs(p)
        order = pprs[:1]
        n = self.numpr(p) if numbered else None
        if n:
            lv = self._lvl(*n)
            if lv is not None and lv.find(qw("pPr")) is not None:
                order.append(lv.find(qw("pPr")))
        order += pprs[1:]
        for ppr in order:
            e = ppr.find(qw(tag))
            if e is None:
                continue
            for a in attrs:
                v = e.get(qw(a))
                if v is not None and re.match(r"^-?\d+$", v):
                    return int(v)
        return None

    def indents(self, p) -> int:
        left = self._ppr_attr(p, "ind", ("left", "start")) or 0
        right = self._ppr_attr(p, "ind", ("right", "end")) or 0
        return max(left, 0) + max(right, 0)

    def align(self, p) -> str:
        for ppr in self._pprs(p):
            v = wval(ppr, "jc")
            if v:
                return {"center": "中央", "right": "右", "end": "右", "both": "両端", "distribute": "均等"}.get(v, "")
        return ""

    # --- 用紙 ---
    def section_of(self, el):
        cur = el
        while cur is not None:
            if local(cur) == "p" and has_section_break(cur):
                return cur.find(qw("pPr")).find(qw("sectPr"))
            if local(cur) == "sectPr":
                return cur
            cur = cur.getnext()
        return self.body.find(qw("sectPr"))

    @staticmethod
    def page(sect) -> dict:
        w, h, top, bottom, left, right = DEFAULT_PAGE
        pitch, grid = 0, ""
        if sect is not None:
            sz, mar, g = sect.find(qw("pgSz")), sect.find(qw("pgMar")), sect.find(qw("docGrid"))
            if sz is not None:
                w = int(sz.get(qw("w"), w))
                h = int(sz.get(qw("h"), h))
            if mar is not None:
                top = abs(int(mar.get(qw("top"), top)))
                bottom = abs(int(mar.get(qw("bottom"), bottom)))
                left = int(mar.get(qw("left"), mar.get(qw("start"), left)))
                right = int(mar.get(qw("right"), mar.get(qw("end"), right)))
            if g is not None and g.get(qw("type")) in ("lines", "linesAndChars", "snapToChars"):
                grid = g.get(qw("type"))
                pitch = int(g.get(qw("linePitch"), "0") or 0)
        return {"w": w, "h": h, "top": top, "bottom": bottom, "left": left, "right": right, "pitch": pitch, "grid": grid}

    def text_width(self, el) -> int:
        """その段落・表が置かれる場所の幅（twips）。表のセルの中なら、セルの幅から余白を引く。"""
        tc = next((a for a in el.iterancestors() if local(a) == "tc"), None)
        if tc is not None:
            return max(self.cell_width(tc) - 2 * CELL_MARGIN, TWIPS_PER_PT)
        top = el
        while top.getparent() is not None and top.getparent() is not self.body:
            top = top.getparent()
        pg = self.page(self.section_of(top))
        return pg["w"] - pg["left"] - pg["right"]

    @staticmethod
    def cell_width(tc) -> int:
        tcw = tc.find(f"{qw('tcPr')}/{qw('tcW')}")
        if tcw is not None and tcw.get(qw("type")) in (None, "dxa") and (tcw.get(qw("w")) or "").isdigit() \
                and int(tcw.get(qw("w"))) > 0:
            return int(tcw.get(qw("w")))
        tr = tc.getparent()
        tbl = tr.getparent() if tr is not None else None
        grid = [int(g.get(qw("w"), "0") or 0) for g in tbl.findall(f"{qw('tblGrid')}/{qw('gridCol')}")] if tbl is not None else []
        col = 0
        for other in tr.findall(qw("tc")):
            span = int(wval(other.find(qw("tcPr")), "gridSpan") or 1)
            if other is tc:
                return sum(grid[col:col + span]) or 2000
            col += span
        return 2000

    def line_pt(self, p, size: float) -> float:
        """1 行の高さ（pt）。行間の指定と、行の格子（docGrid）を見る。"""
        line, rule = None, "auto"
        for ppr in self._pprs(p):
            sp = ppr.find(qw("spacing"))
            if sp is not None and sp.get(qw("line")):
                line, rule = int(sp.get(qw("line"))), sp.get(qw("lineRule"), "auto")
                break
        h = size * LINE_FACTOR
        if line is not None:
            if rule == "exact":
                return line / TWIPS_PER_PT
            h = max(h, line / TWIPS_PER_PT) if rule == "atLeast" else h * line / 240
        top = p
        while top.getparent() is not None and top.getparent() is not self.body:
            top = top.getparent()
        pg = self.page(self.section_of(top))
        if pg["pitch"]:
            pitch = pg["pitch"] / TWIPS_PER_PT
            h = math.ceil(h / pitch - 0.05) * pitch
        return h

    def capacity(self, p) -> Capacity:
        size = self.font_size(p)
        width = self.text_width(p) - self.indents(p)
        return Capacity(max(width / TWIPS_PER_PT / size, 1.0), size, self.line_pt(p, size))

    def spacing(self, p) -> float:
        before = self._ppr_attr(p, "spacing", ("before",), numbered=False) or 0
        after = self._ppr_attr(p, "spacing", ("after",), numbered=False) or 0
        return (before + after) / TWIPS_PER_PT

    def para_height(self, p) -> float:
        cap = self.capacity(p)
        lines = cap.lines_for(para_text(p)) if para_text(p) else 1
        h = lines * cap.line_pt + self.spacing(p)
        for ext in p.iter(f"{{{NS_WP}}}extent"):   # 行の中の図
            if any(local(a) == "inline" for a in ext.iterancestors()):
                h += int(ext.get("cy", "0")) / EMU_PER_PT
        return h

    def table_height(self, tbl) -> float:
        total = 0.0
        for tr in tbl.findall(qw("tr")):
            trh = tr.find(f"{qw('trPr')}/{qw('trHeight')}")
            fixed = int(trh.get(qw("val"), "0")) / TWIPS_PER_PT if trh is not None else 0.0
            if trh is not None and trh.get(qw("hRule")) == "exact":
                total += fixed
                continue
            h = fixed
            for tc in tr.findall(qw("tc")):
                h = max(h, sum(self.para_height(p) for p in tc.findall(qw("p"))) + 4)
            total += h
        return total

    def estimate_pages(self) -> int:
        """ページ数の見積もり（行数 × 行の高さを、用紙の本文の高さで割る。改ページ・セクションの区切りで次のページへ）。"""
        pages, used = 1, 0.0
        for el in self.blocks:
            pg = self.page(self.section_of(el))
            avail = (pg["h"] - pg["top"] - pg["bottom"]) / TWIPS_PER_PT
            if local(el) == "p":
                if has_page_break(el) and used > 0:
                    pages, used = pages + 1, 0.0
                h = self.para_height(el)
            elif local(el) == "tbl":
                h = self.table_height(el)
            else:
                h = sum(self.para_height(p) for p in el.iter(qw("p")))
            used += h
            while used > avail:
                pages, used = pages + 1, used - avail
            if local(el) == "p" and has_section_break(el):
                st = wval(el.find(qw("pPr")).find(qw("sectPr")), "type")
                if st not in ("continuous",):
                    pages, used = pages + 1, 0.0
        return pages

    # --- 参照 ---
    def block(self, n: int):
        if not 1 <= n <= len(self.blocks):
            raise TemplateError(f"ブロック #{n} はテンプレートにありません（{len(self.blocks)} ブロック）")
        return self.blocks[n - 1]


def parse_range(ref: Any) -> tuple[int, int]:
    """`#5`・`#5-#8`・`5-8` → (5, 8)。"""
    m = re.match(r"^\s*#?(\d+)\s*(?:[-〜~]\s*#?(\d+))?\s*$", str(ref))
    if not m:
        raise TemplateError(f"ブロックの指定は #5 か #5-#8 の形で書いてください: {ref!r}")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if b < a:
        raise TemplateError(f"ブロックの範囲が逆です: {ref!r}")
    return a, b


def fmt_range(a: int, b: int) -> str:
    return f"#{a}" if a == b else f"#{a}-#{b}"


def block_kind(view: DocView, el) -> str:
    tag = local(el)
    if tag == "tbl":
        return "表"
    if tag == "sdt":
        return "コンテンツコントロール"
    lvl = view.heading_level(el)
    if lvl:
        return f"見出し {lvl}"
    if view.is_list(el):
        n = view.numpr(el)
        return f"リスト（{'記号' if view.is_bullet(*n) else '番号'}・段 {n[1]}）"
    return "段落"


def literal_text(el) -> str:
    """段落・表・コンテンツコントロールの、手で書かれた文字（フィールドの結果を除く）。"""
    if local(el) == "p":
        return para_text(el)
    return "\n".join(para_text(p) for p in el.iter(qw("p")) if para_text(p)).strip()


def cell_text(tc) -> str:
    return "\n\n".join(para_text(p) for p in tc.findall(qw("p"))).strip("\n")


def table_grid(tbl) -> list[list[str]]:
    return [[cell_text(tc) for tc in tr.findall(qw("tc"))] for tr in tbl.findall(qw("tr"))]


def n_cols(tbl) -> int:
    return len(tbl.findall(f"{qw('tblGrid')}/{qw('gridCol')}"))


def sdt_name(el) -> str:
    pr = el.find(qw("sdtPr"))
    return (wval(pr, "alias") or wval(pr, "tag") or "") if pr is not None else ""


def sdt_paras(el) -> list:
    content = el.find(qw("sdtContent"))
    return content.findall(qw("p")) if content is not None else []


# ---------------------------------------------------------------------------
# 来歴（前の文書の値・メタデータ）
# ---------------------------------------------------------------------------

NS_DC = "http://purl.org/dc/elements/1.1/"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CORE_FIELDS = {"title": f"{{{NS_DC}}}title", "subject": f"{{{NS_DC}}}subject", "creator": f"{{{NS_DC}}}creator",
               "keywords": f"{{{NS_CP}}}keywords", "description": f"{{{NS_DC}}}description",
               "lastModifiedBy": f"{{{NS_CP}}}lastModifiedBy", "category": f"{{{NS_CP}}}category"}
APP_FIELDS = {"company": f"{{{NS_APP}}}Company", "manager": f"{{{NS_APP}}}Manager"}
APP_STALE = ("HeadingPairs", "TitlesOfParts", "Pages", "Words", "Characters", "CharactersWithSpaces", "Lines",
             "Paragraphs", "TotalTime")
TOC_FIELD_RE = re.compile(r"^\s*(TOC|REF|PAGEREF|SEQ|NOTEREF)\b")


def read_properties(raw: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = set(z.namelist())
        for part, fields in (("docProps/core.xml", CORE_FIELDS), ("docProps/app.xml", APP_FIELDS)):
            if part not in names:
                continue
            root = etree.fromstring(z.read(part))
            for name, tag in fields.items():
                el = root.find(tag)
                if el is not None and (el.text or "").strip():
                    out[name] = el.text.strip()
    return out


def _parts_of(doc, kind: str) -> list:
    return [rel.target_part for rel in doc.part.rels.values() if not rel.is_external and rel.reltype.endswith("/" + kind)]


def _part_paras(part) -> list:
    root = part.element if hasattr(part, "element") else etree.fromstring(part.blob)
    return list(root.iter(qw("p")))


def count_revisions(root) -> int:
    return sum(1 for e in root.iter() if local(e) in ("ins", "del", "moveFrom", "moveTo") and e.get(qw("author")) is not None)


def has_update_fields(root) -> bool:
    """目次・相互参照のフィールド（開いたときに更新が要るもの）があるか。"""
    for e in root.iter(qw("instrText")):
        if TOC_FIELD_RE.match(e.text or ""):
            return True
    for e in root.iter(qw("fldSimple")):
        if TOC_FIELD_RE.match(e.get(qw("instr"), "")):
            return True
    return any(wval(e, "docPartGallery") == "Table of Contents" for e in root.iter(qw("docPartObj")))


def provenance(raw: bytes) -> list[str]:
    """前の文書から持ち越しうるもの（プロパティ・コメント・変更履歴・ヘッダー・リンク・埋め込み）。"""
    notes: list[str] = []
    for k, v in read_properties(raw).items():
        notes.append(f"文書のプロパティ {k}: {v}")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        if "docProps/app.xml" in names:
            titles = [e.text for e in etree.fromstring(z.read("docProps/app.xml")).iter(f"{{{NS_VT}}}lpstr") if e.text]
            if titles:
                notes.append(f"文書の情報に、前の文書の題が残っている（{_short(' / '.join(titles), 60)}）")
    if any(n.startswith("docProps/thumbnail") for n in names):
        notes.append("プレビュー画像（docProps/thumbnail）がある。元の文書の見た目が残る")
    if "docProps/custom.xml" in names:
        notes.append("カスタムプロパティ（docProps/custom.xml）がある")
    if any(n.endswith("vbaProject.bin") for n in names):
        notes.append("マクロ（VBA）がある。出力の拡張子は .docm にする")
    doc = open_doc(raw)
    body = doc.element.body
    comments = _parts_of(doc, "comments")
    n_comments = sum(len(list(p.element.iter(qw("comment")))) if hasattr(p, "element") else
                     len(list(etree.fromstring(p.blob).iter(qw("comment")))) for p in comments)
    if n_comments:
        texts = [para_text(p) for part in comments for p in _part_paras(part) if para_text(p)]
        notes.append(f"コメント {n_comments} 件（{_short(' / '.join(texts), 50)}）")
    revs = count_revisions(body)
    if revs:
        authors = sorted({e.get(qw("author")) for e in body.iter() if e.get(qw("author"))})
        notes.append(f"変更履歴 {revs} か所（{', '.join(authors)}）。properties.scrub なら承諾して作成者を消す")
    for kind, what in (("header", "ヘッダー"), ("footer", "フッター")):
        seen = set()
        for part in _parts_of(doc, kind):
            text = " / ".join(t for t in (para_text(p) for p in _part_paras(part)) if t.strip())
            if text and text not in seen:
                seen.add(text)
                notes.append(f"{what}: 「{_short(text, 40)}」（どのページにも出る。render は書き換えない）")
    for kind, what in (("footnotes", "脚注"), ("endnotes", "文末脚注")):
        for part in _parts_of(doc, kind):
            texts = [para_text(p) for p in _part_paras(part) if para_text(p).strip()]
            if texts:
                notes.append(f"{what} {len(texts)} 件（{_short(' / '.join(texts), 40)}。render は書き換えない）")
    for rel in doc.part.rels.values():
        if rel.is_external and rel.reltype.endswith("/hyperlink"):
            notes.append(f"外部へのリンク {rel.target_ref}")
        elif rel.reltype.endswith("/chart"):
            notes.append("グラフ（値は元の文書のまま。render は書き換えない）")
        elif rel.reltype.endswith(("/oleObject", "/package")):
            notes.append("埋め込みのオブジェクト（元の文書の中身が入っている）")
    settings = doc.settings.element
    att = settings.find(qw("attachedTemplate"))
    if att is not None:
        rid = att.get(f"{{{NS_R}}}id")
        rel = doc.settings.part.rels.get(rid) if rid else None
        target = rel.target_ref if rel is not None else ""
        if target and target.lower() not in ("normal.dotm", "normal.dot"):
            notes.append(f"元にした Word テンプレートの場所: {target}（properties.scrub で外す）")
    if settings.find(qw("docVars")) is not None:
        notes.append("文書の変数（docVars）がある。中身は元の文書のまま")
    boxes = [para_text(p) for box in body.iter(qw("txbxContent")) for p in box.iter(qw("p"))]
    if any(t.strip() for t in boxes):
        notes.append(f"テキストボックスの中の文字: 「{_short(' / '.join(t for t in boxes if t.strip()), 40)}」（render は書き換えない）")
    if any(rpr.find(qw("vanish")) is not None for rpr in body.iter(qw("rPr"))):
        notes.append("隠し文字（非表示の書式の文字）がある。印刷されないが、文書には残る")
    if has_update_fields(body):
        notes.append("目次・相互参照のフィールドがある。render は、開いたときに更新するよう設定する")
    return notes


def drop_comments(doc) -> int:
    """コメント（本文の範囲の印・参照と、コメントの部品・作成者の一覧）を取り除く。"""
    count = 0
    for part in _parts_of(doc, "comments"):
        root = part.element if hasattr(part, "element") else etree.fromstring(part.blob)
        count += len(list(root.iter(qw("comment"))))
    body = doc.element.body
    for e in list(body.iter(qw("commentRangeStart"), qw("commentRangeEnd"))):
        e.getparent().remove(e)
    for ref in list(body.iter(qw("commentReference"))):
        r = ref.getparent()
        if local(r) == "r" and r.getparent() is not None:
            r.getparent().remove(r)
    for rId, rel in list(doc.part.rels.items()):
        if "comments" in rel.reltype.lower() or rel.reltype.lower().endswith("/people"):
            doc.part.rels.pop(rId, None)
    return count


def accept_revisions(root) -> int:
    """変更履歴を承諾する（挿入は残し、削除は消す。書式の変更の記録は外す）。作成者の名前を残さない。"""
    n = 0
    for e in list(root.iter(qw("del"), qw("moveFrom"))):
        if e.getparent() is None:
            continue
        n += 1
        if local(e.getparent()) == "rPr":   # 段落記号の削除の印
            e.getparent().remove(e)
        else:
            e.getparent().remove(e)
    for e in list(root.iter(qw("ins"), qw("moveTo"))):
        parent = e.getparent()
        if parent is None:
            continue
        n += 1
        if local(parent) == "rPr":
            parent.remove(e)
            continue
        idx = parent.index(e)
        for k, c in enumerate(list(e)):
            parent.insert(idx + k, c)
        parent.remove(e)
    for tag in ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
                "tblGridChange", "numberingChange", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
                "moveToRangeEnd"):
        for e in list(root.iter(qw(tag))):
            n += 1
            e.getparent().remove(e)
    return n


SETTINGS_AFTER_UPDATE = ("hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids", "mathPr",
                         "attachedSchema", "themeFontLang", "clrSchemeMapping", "doNotIncludeSubdocsInStats",
                         "doNotAutoCompressPictures", "forceUpgrade", "captions", "readModeInkLockDown", "smartTagType",
                         "schemaLibrary", "shapeDefaults", "doNotEmbedSmartTags", "decimalSymbol", "listSeparator")


def set_update_fields(doc) -> None:
    settings = doc.settings.element
    if settings.find(qw("updateFields")) is not None:
        return
    el = etree.Element(qw("updateFields"))
    el.set(qw("val"), "true")
    nxt = next((c for c in settings if local(c) in SETTINGS_AFTER_UPDATE), None)
    if nxt is not None:
        nxt.addprevious(el)
    else:
        settings.append(el)


def drop_attached_template(doc) -> None:
    settings = doc.settings.element
    att = settings.find(qw("attachedTemplate"))
    if att is None:
        return
    rid = att.get(f"{{{NS_R}}}id")
    settings.remove(att)
    if rid and rid in doc.settings.part.rels:
        doc.settings.part.rels.pop(rid)


def finish_package(raw: bytes, props: dict) -> bytes:
    """文書のプロパティを空にする・与える。プレビュー画像とカスタムプロパティを取り除く（scrub のとき）。"""
    scrub = bool(props.get("scrub"))
    given = {k: v for k, v in props.items() if k != "scrub" and k != "keep"}
    keep = set(props.get("keep") or [])
    if not scrub and not given:
        return raw
    src = zipfile.ZipFile(io.BytesIO(raw))
    drop: set[str] = set()
    if scrub:
        drop = {n for n in src.namelist() if n.startswith("docProps/thumbnail") or n == "docProps/custom.xml"}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename in drop:
                continue
            data = src.read(info.filename)
            if info.filename in ("docProps/core.xml", "docProps/app.xml"):
                root = etree.fromstring(data)
                fields = CORE_FIELDS if info.filename.endswith("core.xml") else APP_FIELDS
                for name, tag in fields.items():
                    el = root.find(tag)
                    if name in given:
                        if el is None:
                            el = etree.SubElement(root, tag)
                        el.text = str(given[name])
                    elif scrub and name not in keep and el is not None:
                        el.text = ""
                if scrub and info.filename.endswith("app.xml"):   # 前の文書の題・ページ数・文字数
                    for tag in APP_STALE:
                        for el in root.findall(f"{{{NS_APP}}}{tag}"):
                            root.remove(el)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            elif drop and info.filename == "_rels/.rels":
                root = etree.fromstring(data)
                for rel in list(root):
                    if rel.get("Target", "").lstrip("/") in drop:
                        root.remove(rel)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            elif drop and info.filename == "[Content_Types].xml":
                root = etree.fromstring(data)
                for o in list(root):
                    if o.get("PartName", "").lstrip("/") in drop:
                        root.remove(o)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            dst.writestr(info, data)
    return out.getvalue()


# ---------------------------------------------------------------------------
# inspect（判断用の事実）
# ---------------------------------------------------------------------------

def _cm(twips: float) -> str:
    return f"{twips / TWIPS_PER_CM:.1f}"


def _block_fact(view: DocView, n: int, el) -> dict:
    fact: dict[str, Any] = {"n": n, "kind": block_kind(view, el)}
    tag = local(el)
    if tag == "p":
        text = para_text(el)
        fact["style"] = view.style_name(view.style_of(el))
        fact["text"] = text
        if text:
            fact["font"] = view.font_name(el)
            fact["size"] = view.font_size(el)
            cap = view.capacity(el)
            fact["fits"] = cap.describe()
            fact["lines_used"] = cap.lines_for(text)
            if "\n" in text:
                fact["line_breaks"] = text.count("\n")
        if view.align(el):
            fact["align"] = view.align(el)
        flags = []
        if has_page_break(el):
            flags.append("改ページ")
        if has_section_break(el):
            flags.append("セクションの区切り")
        if has_drawing(el):
            flags.append("図")
        if has_field(el):
            flags.append("フィールド")
        if flags:
            fact["flags"] = flags
    elif tag == "tbl":
        rows = table_grid(el)
        fact["table"] = rows
        fact["cols"] = n_cols(el)
        if any(tr.find(f"{qw('trPr')}/{qw('tblHeader')}") is not None for tr in el.findall(qw("tr"))):
            fact["header_repeat"] = True
        text = "\n".join(c for r in rows for c in r)
    else:
        text = literal_text(el)
        fact["name"] = sdt_name(el)
        fact["text"] = text
    if PLACEHOLDER_RE.search(text or ""):
        fact["placeholder_suspect"] = True
    if tag == "p" and NOTE_RE.search(text or ""):
        fact["note_suspect"] = True
    return fact


def outline(view: DocView) -> list[dict]:
    """章の構成（見出しと、その章が持つブロックの範囲）。"""
    heads = [(n, view.heading_level(el), para_text(el)) for n, el in enumerate(view.blocks, start=1)
             if view.heading_level(el)]
    out = []
    for i, (n, lvl, text) in enumerate(heads):
        end = next((m - 1 for m, l2, _ in heads[i + 1:] if l2 <= lvl), len(view.blocks))
        out.append({"n": n, "level": lvl, "text": text, "end": end})
    return out


def inspect_template(template: "str | bytes") -> dict:
    raw = read_bytes(template)
    doc = open_doc(raw)
    view = DocView(doc)
    pg = view.page(view.body.find(qw("sectPr")))
    probe = etree.fromstring(f'<w:p xmlns:w="{NS_W}"><w:r><w:t>x</w:t></w:r></w:p>')   # 既定のスタイルだけの段落（直接の書式を見ない）
    base = {"font": view.font_name(probe), "size": view.font_size(probe)}
    sections = [view.page(s) for s in view.body.iter(qw("sectPr"))]
    return {"page_cm": [round(pg["w"] / TWIPS_PER_CM, 1), round(pg["h"] / TWIPS_PER_CM, 1)],
            "margins_cm": [round(pg[k] / TWIPS_PER_CM, 1) for k in ("top", "bottom", "left", "right")],
            "text_width_cm": round((pg["w"] - pg["left"] - pg["right"]) / TWIPS_PER_CM, 1),
            "line_grid": pg["grid"] and f"{pg['pitch'] / TWIPS_PER_PT:g}pt",
            "sections": len(sections), "base": base, "pages": view.estimate_pages(),
            "blocks": [_block_fact(view, n, el) for n, el in enumerate(view.blocks, start=1)],
            "outline": outline(view), "repeat_candidates": [
                {"samples": [fmt_range(a, b) for a, b in c["ranges"]], "evidence": c["evidence"]}
                for c in find_repeats(view)],
            "provenance": provenance(raw)}


def format_facts(facts: dict) -> str:
    w, h = facts["page_cm"]
    t, b, l, r = facts["margins_cm"]
    lines = [f"用紙: {w} × {h} cm・余白 上 {t} 下 {b} 左 {l} 右 {r} cm・本文の幅 {facts['text_width_cm']} cm"
             + (f"・行の格子 {facts['line_grid']}" if facts.get("line_grid") else "")
             + (f"・セクション {facts['sections']}" if facts["sections"] > 1 else ""),
             f"本文の文字: {facts['base']['font'] or '（既定）'} {facts['base']['size']:g}pt",
             f"ページ数の見積もり: 約 {facts['pages']} ページ", ""]
    for f in facts["blocks"]:
        flags = "".join([" ?仮値" if f.get("placeholder_suspect") else "", " ※注記" if f.get("note_suspect") else ""])
        head = f"#{f['n']:<3} {f['kind']}"
        if "style" in f:
            head += f" [{f['style']}]"
        if f.get("name"):
            head += f" [{f['name']}]"
        if "table" in f:
            rows = f["table"]
            lines.append(f"{head} {f['cols']} 列 × {len(rows)} 行{'（見出しの行を繰り返す）' if f.get('header_repeat') else ''}{flags}")
            for k, row in enumerate(rows, start=1):
                lines.append(f"       行{k}: " + " | ".join(_short(c, 16) for c in row))
            continue
        text = f.get("text") or ""
        if not text:
            extra = "・".join(f.get("flags") or [])
            lines.append(f"{head} （空{'。' + extra if extra else ''}）")
            continue
        fmt = " ".join(x for x in (f.get("font") or "", f"{f['size']:g}pt" if f.get("size") else "", f.get("align") or "") if x)
        lines.append(f"{head} {fmt}「{_short(text, 50)}」{flags}")
        detail = [f"収まる量: {f['fits']}（いま {f['lines_used']} 行）"] if f.get("fits") else []
        if f.get("line_breaks"):
            detail.append(f"段落の中の改行 {f['line_breaks']}")
        if f.get("flags"):
            detail.append("・".join(f["flags"]))
        if detail:
            lines.append("       " + "、".join(detail))
    if facts["outline"]:
        lines.append("")
        lines.append("章の構成:")
        for o in facts["outline"]:
            lines.append(f"  {'  ' * (o['level'] - 1)}{_short(o['text'], 40)}（#{o['n']}〜#{o['end']}）")
    for c in facts["repeat_candidates"]:
        lines.append(f"同じ形の節の並び: {' と '.join(c['samples'])}（{c['evidence']}）")
    if facts["provenance"]:
        lines.append("")
        lines.append("来歴・持ち越しの注意:")
        lines.extend(f"  - {p}" for p in facts["provenance"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# analyze（定義の下書き）
# ---------------------------------------------------------------------------

def _unique(key: str, used: set) -> str:
    base, n = key, 2
    while key in used:
        key, n = f"{base}{n}", n + 1
    used.add(key)
    return key


def _budget(samples: list[str]) -> "int | None":
    n = max((len(s.replace("\n", "")) for s in samples if s), default=0)
    return max(math.ceil(n * GRANULARITY), n + 4) if n else None


def _line_budget(view: DocView, el, samples: list[str]) -> "int | None":
    """1 段落の字数の上限。サンプルの 1.5 倍までだが、サンプルが使う行数を超えない（1 行の見出しは 1 行のまま）。"""
    b = _budget(samples)
    if not b:
        return None
    n = max(len(s.replace("\n", "")) for s in samples)
    cpl = int(view.capacity(el).cpl)
    if cpl < 1:
        return b
    lines = cpl * math.ceil(n / cpl)
    if any(PLACEHOLDER_RE.search(s) for s in samples):   # 仮の値（〇〇）の字数は目安にならない。使う行に入るだけ
        return lines
    return max(n, min(b, lines))


def _cell_budget(view: DocView, tr, tc, samples: list[str]) -> "int | None":
    """セルの字数の上限。高さの決まった行なら、入る行数ぶん。ほかはセルの幅で 1 段落と同じに見る。"""
    ex = _exact_lines(view, tr, tc)
    if ex:
        return int(ex[0].cpl * ex[1])
    p = tc.find(qw("p"))
    return _line_budget(view, p, samples) if p is not None else _budget(samples)


def _items_budget(n: int) -> int:
    return max(n, math.ceil(n * GRANULARITY))


def _index_format(samples: list[str]) -> "str | None":
    """1, 2, 3 …（STEP 1・STEP 2 … も）と並ぶなら、その書式（`{}` が番号）。"""
    if len(samples) < 2:
        return None
    fmts, nums = set(), []
    for s in samples:
        m = list(re.finditer(r"\d+", s))
        if len(m) != 1:
            return None
        fmts.add(s[:m[0].start()] + "{}" + s[m[0].end():])
        nums.append(int(m[0].group()))
    return fmts.pop() if len(fmts) == 1 and nums == list(range(nums[0], nums[0] + len(nums))) and nums[0] in (0, 1) else None


def _heading_format(samples: list[str]) -> "str | None":
    """`2.1 施策 A`・`2.2 施策 B` のように、手で打った番号が 1 つずつ増える見出しなら `2.{n} {}`。"""
    if len(samples) < 2:
        return None
    heads, nums = set(), []
    for s in samples:
        m = re.match(r"^((?:\d+[.．])*)(\d+)([.．]?\s*)\S", s)
        if not m:
            return None
        heads.add((m.group(1), m.group(3)))
        nums.append(int(m.group(2)))
    if len(heads) != 1 or nums != list(range(nums[0], nums[0] + len(nums))) or nums[0] != 1:
        return None
    pre, sep = heads.pop()
    return f"{pre}{{n}}{sep}{{}}"


def heading_key(text: str) -> str:
    """見出しの文字から、データのキーにする言葉（章の番号を除く）。"""
    t = NUMBERING_RE.sub("", text or "").strip()
    t = re.sub(r"[\s　]+", "", t)
    return t[:16]


def _sig(view: DocView, el) -> tuple:
    """ブロックの形（並びを見比べる・記入済みの文書と突き合わせるのに使う）。"""
    tag = local(el)
    if tag == "p":
        return ("p", view.style_of(el), view.is_list(el))
    if tag == "tbl":
        return ("tbl", n_cols(el))
    return (tag, sdt_name(el) if tag == "sdt" else "")


def _segments(view: DocView) -> list[tuple[int, int]]:
    """見出しごとに区切った範囲（1 始まり）。最初の見出しの前（表紙）も 1 つの範囲。"""
    heads = [n for n, el in enumerate(view.blocks, start=1) if view.heading_level(el)]
    starts = sorted(set([1] + heads))
    return [(a, (starts[i + 1] - 1) if i + 1 < len(starts) else len(view.blocks)) for i, a in enumerate(starts)]


def _shape(view: DocView, a: int, b: int) -> tuple:
    """範囲の形。空の段落を除き、同じ形の段落が続くところは 1 つにまとめる（本文の段落の数が違っても同じ形）。"""
    out: list = []
    for n in range(a, b + 1):
        el = view.blocks[n - 1]
        if local(el) == "p" and not para_text(el).strip() and not has_drawing(el):
            continue
        s = _sig(view, el) + ((view.heading_level(el),) if local(el) == "p" else ())
        if not out or out[-1] != s or local(el) != "p":
            out.append(s)
    return tuple(out)


def _constants(view: DocView, ranges: list[tuple[int, int]]) -> list[str]:
    """同じ形の範囲のどれにも、同じ位置に同じ文字があるもの（ラベル・表の見出し）。"""
    def texts(a, b):
        out = []
        for n in range(a, b + 1):
            el = view.blocks[n - 1]
            if local(el) == "tbl":
                grid = table_grid(el)
                out.append(" | ".join(grid[0]) if grid else "")
            elif local(el) == "p" and not view.heading_level(el):
                t = para_text(el).strip()
                m = LABEL_VALUE_RE.match(t)
                if m:
                    out.append(m.group(1).strip())
                elif LABEL_RE.match(t) or NOTE_RE.search(t):
                    out.append(t)
        return out
    lists = [texts(a, b) for a, b in ranges]
    if any(len(x) != len(lists[0]) for x in lists):
        return []
    return [t for k, t in enumerate(lists[0]) if t and all(x[k] == t for x in lists)]


def _common_affix(texts: list[str]) -> str:
    stripped = [NUMBERING_RE.sub("", t).strip() for t in texts]
    pre = os.path.commonprefix(stripped).strip()
    suf = os.path.commonprefix([s[::-1] for s in stripped])[::-1].strip()
    return pre if len(pre) >= 2 else (suf if len(suf) >= 2 else "")


def find_repeats(view: DocView) -> list[dict]:
    """同じ見出しの段で、同じ形の節が続くもの。繰り返しと見る手がかり（共通の言葉・ラベル・表の見出し）も返す。"""
    segs = _segments(view)
    out = []
    i = 0
    while i < len(segs):
        a, b = segs[i]
        el = view.blocks[a - 1]
        lvl = view.heading_level(el)
        shape = _shape(view, a, b)
        j = i
        while lvl and len(shape) > 1 and j + 1 < len(segs) and _shape(view, *segs[j + 1]) == shape:
            j += 1
        if j > i:
            ranges = segs[i:j + 1]
            heads = [para_text(view.blocks[x - 1]) for x, _ in ranges]
            affix = _common_affix(heads)
            consts = _constants(view, ranges)
            ev = []
            if affix:
                ev.append(f"見出しに共通の「{affix}」")
            if consts:
                ev.append("どの節にも同じ「" + "」「".join(_short(c, 12) for c in consts[:3]) + "」")
            out.append({"ranges": ranges, "evidence": "・".join(ev) or "形だけが同じ（別々の章かもしれない）",
                        "likely": bool(ev)})
        i = j + 1
    return out


def _table_rows(tbl) -> list:
    return tbl.findall(qw("tr"))


def _tcs(tr) -> list:
    return tr.findall(qw("tc"))


def _analyze_table(view: DocView, n: int, el, used: set, confirm: list) -> "dict | None":
    trs = _table_rows(el)
    grid = table_grid(el)
    ref, cols_n = f"#{n}", max((len(r) for r in grid), default=0)
    head_runs = [r for tc in _tcs(trs[0]) for p in tc.findall(qw("p")) for r in text_runs(p)] if trs else []
    bold_head = bool(head_runs) and all(r.find(f"{qw('rPr')}/{qw('b')}") is not None for r in head_runs)
    shaded = bool(trs) and all(tc.find(f"{qw('tcPr')}/{qw('shd')}") is not None
                               and (tc.find(f"{qw('tcPr')}/{qw('shd')}").get(qw("fill")) or "auto") not in ("auto", "FFFFFF")
                               for tc in _tcs(trs[0]))
    repeat_head = bool(trs) and trs[0].find(f"{qw('trPr')}/{qw('tblHeader')}") is not None
    where = f"#{n} の表"
    if cols_n == 2 and not (bold_head or shaded or repeat_head) and len(trs) >= 2:
        cells = {}
        for r, row in enumerate(grid, start=1):
            if row and row[0].strip() and len(row) > 1:
                cells[f"{r},2"] = {"key": _unique(re.sub(r"\s+", "", row[0].strip().rstrip(":：")), used)}
                tcs = _tcs(trs[r - 1]) if r <= len(trs) else []
                b = _cell_budget(view, trs[r - 1], tcs[1], [row[1]]) if len(tcs) > 1 else _budget([row[1]])
                if b:
                    cells[f"{r},2"]["max_chars"] = b
        confirm.append(f"{where}: 2 列の表を、左の列を見出しにした記入欄（行は増やさない）と見た。行を繰り返す表なら key と columns に直す")
        return {"block": ref, "cells": cells}
    header = 1 if len(trs) >= 2 else 0
    footer = 1 if len(trs) - header >= 2 and grid[-1] and any(
        w in (grid[-1][0] or "").lower() for w in ("合計", "小計", "計", "total")) else 0
    body = grid[header:len(grid) - footer]
    first = trs[header] if header < len(trs) else None
    columns: list = []
    col_keys: set = set()
    for j in range(cols_n):
        head = re.sub(r"\s+", " ", grid[0][j].strip()) if header and j < len(grid[0]) else ""
        samples = [row[j] for row in body if j < len(row)]
        filled = [s for s in samples if s.strip()]
        if not head and not filled:
            columns.append(None)
            continue
        if HUMAN_RE.search(head):
            columns.append({"clear": True, "header": head})
            continue
        fmt = _index_format(filled) if len(filled) == len(samples) else None
        if fmt is not None:
            spec = {"key": "$index", "header": head}
            if fmt != "{}":
                spec["format"] = fmt
            columns.append(spec)
            continue
        if len(samples) >= 2 and len(set(samples)) == 1 and filled and not PLACEHOLDER_RE.search(samples[0]):
            columns.append({"keep": True, "header": head})
            continue
        if filled and all(s.strip() in MARK_ON + MARK_OFF for s in filled):
            on = next((s.strip() for s in filled if s.strip() in MARK_ON), "○")
            off = next((s.strip() for s in filled if s.strip() in MARK_OFF), "")
            spec = {"key": _unique(head or f"col{j + 1}", col_keys), "map": {True: on, False: off or None}, "header": head}
            confirm.append(f"{where}: 列「{head}」は ○ の印の列。データは true / false で書く")
            columns.append(spec)
            continue
        spec = {"key": _unique(re.sub(r"\s+", "", head) or f"col{j + 1}", col_keys), "header": head}
        tcs = _tcs(first) if first is not None else []
        b = _cell_budget(view, first, tcs[j], filled) if j < len(tcs) else _budget(filled)
        if b:
            spec["max_chars"] = b
        columns.append(spec)
    if all(c is None or c.get("clear") for c in columns) and not any(s.strip() for row in body for s in row):
        confirm.append(f"{where}: 印・署名の欄だけの表と見て、そのまま残す（keep）")
        return None
    out = {"block": ref, "key": _unique("rows", used), "header_rows": header, "footer_rows": footer, "columns": columns}
    cells = {}
    base = None
    for r in range(len(grid) - footer, len(grid)):
        for j, text in enumerate(grid[r]):
            spec = columns[j] if j < len(columns) else None
            if j == 0 or not text.strip() or not spec or not spec.get("key") or spec.get("keep") or spec["key"] == "$index":
                continue
            base = base or _unique((grid[r][0] or "").strip() or "合計", used)
            cells[f"{r + 1},{j + 1}"] = {"key": f"{base}.{spec['key']}"}
            tcs = _tcs(trs[r]) if r < len(trs) else []
            b = _cell_budget(view, trs[r], tcs[j], [text]) if j < len(tcs) else _budget([text])
            if b:
                cells[f"{r + 1},{j + 1}"]["max_chars"] = b
    if cells:
        out["cells"] = cells
        confirm.append(f"{where}: 最後の行（{grid[-1][0].strip()}）の値は、データの `{base}` から入れる"
                       "（前の値を残さない。計算はしないので、データに書く）")
    return out


def _cover_key(view: DocView, el, text: str, biggest: float) -> str:
    style = view.style_name(view.style_of(el)).lower()
    if style in ("title", "表題") or (view.font_size(el) >= biggest and view.font_size(el) >= 14):
        return "title"
    if style in ("subtitle", "副題"):
        return "subtitle"
    if DATE_RE.match(text.strip()):
        return "日付"
    if ADDRESSEE_RE.search(text.strip()):
        return "宛先"
    return "text"


def _analyze_part(view: DocView, a: int, b: int, confirm: list, cover: bool, doc: "DocValues | None" = None) -> dict:
    doc = doc if doc is not None else DocValues()
    texts, lists, tables, keep, clear = {}, {}, [], [], []
    used: set = set()
    label = None
    biggest = max((view.font_size(view.blocks[k - 1]) for k in range(a, b + 1)
                   if local(view.blocks[k - 1]) == "p" and para_text(view.blocks[k - 1]).strip()), default=0)
    n = a
    while n <= b:
        el = view.blocks[n - 1]
        tag = local(el)
        if tag == "tbl":
            t = _analyze_table(view, n, el, used, confirm)
            if t:
                tables.append(t)
            else:
                keep.append(f"#{n}")
            label = None
            n += 1
            continue
        if tag == "sdt":
            text = literal_text(el)
            if sdt_paras(el):
                ps = sdt_paras(el)
                spec = {"key": _unique(re.sub(r"\s+", "", sdt_name(el)) or label or "text", used),
                        "max_items": _items_budget(len(ps))}
                pr = el.find(qw("sdtPr"))
                shown = pr is not None and pr.find(qw("showingPlcHdr")) is not None
                bud = _line_budget(view, ps[0], [text]) if text and not shown else None   # 説明の文字の字数は目安にならない
                if bud:
                    spec["max_chars"] = bud
                texts[f"#{n}"] = spec
            elif text:
                keep.append(f"#{n}")
            label = None
            n += 1
            continue
        if tag != "p":
            n += 1
            continue
        text = para_text(el)
        where = f"#{n}"
        if not text.strip():
            n += 1
            continue
        if view.heading_level(el):
            got = None if PLACEHOLDER_RE.search(text) else variable_parts(text)
            if PLACEHOLDER_RE.search(text) or got:
                # 見出しの中の年度・期間（「2025年度の実績」）は、可変の部分だけを文書の値にする
                texts[where] = {"text": doc.template(*got)} if got else {"key": _unique("title", used)}
                texts[where]["max_items"] = 1
                bud = _line_budget(view, el, [text])
                if bud:
                    texts[where]["max_chars"] = bud
                if got:
                    confirm.append(f"{where}: 見出し「{_short(text, 20)}」は、可変の部分だけを文書の値として流し込む"
                                   f"（{texts[where]['text']}）。毎回同じ文字なら keep に移す")
            else:
                keep.append(where)
            n += 1
            continue
        if LABEL_RE.match(text.strip()):
            keep.append(where)
            label = re.sub(r"[\s　]+", "", text.strip().strip("【】■").rstrip(":：")) or None
            n += 1
            continue
        if NOTE_RE.search(text):
            if PLACEHOLDER_RE.search(text):
                clear.append(where)
                confirm.append(f"{where}: 書き方の注記（{_short(text, 20)}）と見て clear にした")
            else:
                keep.append(where)
            n += 1
            continue
        if n < b and local(view.blocks[n]) in ("sdt", "tbl") and len(text.strip()) <= 10 \
                and not PLACEHOLDER_RE.search(text) and not view.is_list(el):
            keep.append(where)   # 入力欄・表のすぐ前の短い段落は、その欄の名前（備考・内訳）
            label = re.sub(r"[\s　]+", "", text.strip().strip("【】■").rstrip(":：")) or None
            n += 1
            continue
        m = LABEL_VALUE_RE.match(text)
        if m and not view.is_list(el):
            key = re.sub(r"[\s　]+", "", m.group(1).strip().rstrip(":：\t"))
            if key and DOC_LABEL_RE.match(key):
                # 作成日・作成者・版など、文書全体の値。どの部分でも同じキーにし、データの「文書」にまとめる
                value = text[len(m.group(1)):]
                dkey = doc.key(key, value)
                got = variable_parts(value)
                whole = got and len(got[1]) == 1 and got[0].startswith("{" + got[1][0][0] + ":") and got[0].endswith("}")
                spec = {"text": "{" + dkey + got[0][len(got[1][0][0]) + 1:]} if whole else {"key": dkey}
                spec.update({"after": m.group(1), "max_items": 1})
            else:
                spec = {"key": _unique(key or "text", used), "after": m.group(1), "max_items": 1}
            bud = _line_budget(view, el, [text])
            if bud:
                spec["max_chars"] = max(bud - len(m.group(1)), 1)
            texts[where] = spec
            label = None
            n += 1
            continue
        if view.is_list(el):
            e = n
            while e + 1 <= b and local(view.blocks[e]) == "p" and view.is_list(view.blocks[e]) \
                    and para_text(view.blocks[e]).strip():
                e += 1
            samples = [para_text(view.blocks[k - 1]) for k in range(n, e + 1)]
            spec = {"key": _unique(label or "points", used), "max_items": _items_budget(len(samples))}
            bud = _line_budget(view, el, samples)
            if bud:
                spec["max_chars"] = bud
            lists[fmt_range(n, e)] = spec
            label = None
            n = e + 1
            continue
        got = variable_parts(text) if cover else None
        if got:
            # 表題・宛名・日付の中の、年度・四半期・日付・宛先。文の残りはそのまま、可変の部分だけを流し込む
            spec = {"text": doc.template(*got), "max_items": 1}
            bud = _line_budget(view, el, [text])
            if bud:
                spec["max_chars"] = bud
            texts[where] = spec
            confirm.append(f"{where}: 「{_short(text, 20)}」は、可変の部分だけを文書の値として流し込む（{spec['text']}）。"
                           "毎回同じ文字なら keep に移す")
            n += 1
            continue
        if cover:
            key = _cover_key(view, el, text, biggest)
            if key == "title" and FORM_TITLE_RE.search(text.strip()) and not PLACEHOLDER_RE.search(text) \
                    and not re.search(r"[0-9０-９]", text):
                keep.append(where)
                confirm.append(f"{where}: 表題「{_short(text, 20)}」は様式の名前と見て keep にした。毎回変えるなら texts に移す")
                n += 1
                continue
            spec = {"key": _unique(key, used), "max_items": 1}
            bud = _line_budget(view, el, [text])
            if bud:
                spec["max_chars"] = bud
            texts[where] = spec
            n += 1
            continue
        style = view.style_of(el)
        e = n
        while e + 1 <= b:
            nx = view.blocks[e]
            t2 = para_text(nx) if local(nx) == "p" else ""
            if local(nx) != "p" or not t2.strip() or view.style_of(nx) != style or view.is_list(nx) \
                    or view.heading_level(nx) or LABEL_RE.match(t2.strip()) or NOTE_RE.search(t2) \
                    or LABEL_VALUE_RE.match(t2) or has_section_break(nx):
                break
            e += 1
        samples = [para_text(view.blocks[k - 1]) for k in range(n, e + 1)]
        spec = {"key": _unique(label or "body", used), "max_items": _items_budget(len(samples))}
        bud = _line_budget(view, el, samples)
        if bud:
            spec["max_chars"] = bud
        texts[fmt_range(n, e)] = spec
        label = None
        n = e + 1
    out: dict[str, Any] = {}
    for name, val in (("texts", texts), ("lists", lists), ("tables", tables), ("keep", keep), ("clear", clear)):
        if val:
            out[name] = val
    return out


def _merge_budgets(base: dict, other: dict) -> None:
    """繰り返す節のサンプルが複数あれば、粒度（max_chars・max_items）は大きい方に合わせる。"""
    a_specs = list((base.get("texts") or {}).values()) + list((base.get("lists") or {}).values())
    b_specs = list((other.get("texts") or {}).values()) + list((other.get("lists") or {}).values())
    for x, y in zip(a_specs, b_specs):
        for k in ("max_chars", "max_items"):
            if isinstance(x.get(k), int) and isinstance(y.get(k), int):
                x[k] = max(x[k], y[k])
    for t1, t2 in zip(base.get("tables") or [], other.get("tables") or []):
        for c1, c2 in zip(t1.get("columns") or [], t2.get("columns") or []):
            if c1 and c2 and isinstance(c1.get("max_chars"), int) and isinstance(c2.get("max_chars"), int):
                c1["max_chars"] = max(c1["max_chars"], c2["max_chars"])


def analyze(template: "str | bytes") -> dict:
    raw = read_bytes(template)
    doc = open_doc(raw)
    view = DocView(doc)
    confirm: list[str] = []
    dv = DocValues()   # 文書の値（年度・作成日・宛先など）の名前は、文書全体で 1 つ
    segs = _segments(view)
    repeats = {c["ranges"][0]: c for c in find_repeats(view)}
    parts: list[dict] = []
    keys: set = set()
    chapter = None
    parents: dict[int, str] = {}
    k = 0
    pid = 0
    while k < len(segs):
        a, b = segs[k]
        el = view.blocks[a - 1]
        lvl = view.heading_level(el)
        head = para_text(el) if lvl else ""
        if lvl == 1 or (lvl and chapter is None):
            chapter = heading_key(head) or None
        if lvl:
            parents = {L: v for L, v in parents.items() if L < lvl}
            parents[lvl] = heading_key(head)
        pid += 1
        part: dict[str, Any] = {"id": f"p{pid}"}
        rep = repeats.get((a, b))
        if rep is not None and rep["likely"]:
            ranges = rep["ranges"]
            body = _analyze_part(view, a, b, confirm, cover=False, doc=dv)
            for ra, rb in ranges[1:]:
                _merge_budgets(body, _analyze_part(view, ra, rb, [], cover=False))
            heads = [para_text(view.blocks[x - 1]) for x, _ in ranges]
            fmt = _heading_format(heads)
            if fmt or len(set(heads)) > 1:   # 見出しは節ごとに違う → 流し込む
                keep = [r for r in body.get("keep", []) if r != f"#{a}"]
                if keep:
                    body["keep"] = keep
                else:
                    body.pop("keep", None)
                spec = {"key": "title", "max_items": 1}
                if fmt:
                    spec["format"] = fmt
                bud = _line_budget(view, el, [NUMBERING_RE.sub("", h) if fmt else h for h in heads])
                if bud:
                    spec["max_chars"] = bud
                body["texts"] = {f"#{a}": spec, **(body.get("texts") or {})}
            name = parents.get(lvl - 1) or _common_affix(heads) or heading_key(heads[0]) or f"p{pid}"
            part.update({"blocks": fmt_range(a, b), "key": _unique(name, keys), "repeat": True, **body})
            if chapter and lvl > 1:
                part["group"] = chapter
            parts.append(part)
            confirm.append(f"{fmt_range(a, b)} の節を見本に、データの件数だけ繰り返す（`{part['key']}` は配列。"
                           f"{rep['evidence']}）。" + "・".join(fmt_range(x, y) for x, y in ranges[1:]) + " はサンプルとして取り除く")
            for ra, rb in ranges[1:]:
                pid += 1
                parts.append({"id": f"p{pid}", "blocks": fmt_range(ra, rb), "drop": True})
            k += len(ranges)
            continue
        if rep is not None:
            confirm.append(f"{' と '.join(fmt_range(x, y) for x, y in rep['ranges'])} は同じ形の節（{rep['evidence']}）。"
                           "同じ種類の節を並べたものなら、1 つめを repeat にして残りを drop にする")
        body = _analyze_part(view, a, b, confirm, cover=not lvl, doc=dv)
        part["blocks"] = fmt_range(a, b)
        if any(body.get(s) for s in ("texts", "lists", "tables")):
            part["key"] = _unique(heading_key(head) if lvl else "表紙", keys)
        part.update(body)
        if chapter and lvl:
            part["group"] = chapter
        parts.append(part)
        k += 1
    if view.estimate_pages() <= 1 and not any(p.get("repeat") for p in parts):
        confirm.append("テンプレートは約 1 ページ。1 ページに収める様式なら max_pages: 1 を書く")
    if has_update_fields(view.body):
        confirm.append("目次・相互参照がある。render のあと、Word で開いてフィールドを更新する（自動で更新を求める設定にする）")
    if any(p.get("group") for p in parts):
        confirm.append("データを分けるときは、章（見出し 1）ごとに 1 つのファイルにまとめる（group）。章で分かれない意味のまとまりは、"
                       "group を書き直す")
    header_footer = {}
    for ref, p in header_footer_paras(doc).items():
        got = None if has_field(p) else variable_parts(para_text(p))
        if got:   # ヘッダー・フッターの表題・年度（ページ番号などのフィールドを持つ段落は触らない）
            header_footer[ref] = dv.template(*got)
            confirm.append(f"ヘッダー・フッター {ref} の「{_short(para_text(p), 24)}」は、可変の部分を文書の値として流し込む"
                           f"（{header_footer[ref]}）")
    out = {"version": DEF_VERSION, "template": "", "strict": True, "properties": {"scrub": True}, "parts": parts}
    if header_footer:
        out["header_footer"] = header_footer
    out["needs_confirm"] = confirm
    return out


def summarize(definition: dict) -> str:
    lines = []
    for pd in definition.get("parts", []):
        head = f"{pd.get('blocks')}"
        if pd.get("drop"):
            lines.append(f"{head}: 取り除く（サンプル）")
            continue
        if not pd.get("key"):
            lines.append(f"{head}: 残す" + (f"（{', '.join(pd.get('keep') or [])}）" if pd.get("keep") else ""))
            continue
        lines.append(f"{head}: データ `{pd['key']}`{'（配列。1 件 1 節）' if pd.get('repeat') else ''}")
        for ref, spec in (pd.get("texts") or {}).items():
            spec = _spec(spec)
            lines.append(f"  文字 {ref} → " + (f"文 {spec['text']!r}" if "text" in spec else str(spec.get("key"))) + _limit(spec))
        for ref, spec in (pd.get("lists") or {}).items():
            spec = _spec(spec)
            lines.append(f"  リスト {ref} → {spec.get('key')}[]{_limit(spec, '項目')}")
        for t in pd.get("tables") or []:
            if t.get("key"):
                cols = ", ".join(c.get("key", "残す" if c.get("keep") else "空欄") if c else "-" for c in t["columns"])
                lines.append(f"  表 {t['block']} → {t['key']}[]（列: {cols}）")
            else:
                lines.append(f"  表 {t['block']} の記入欄 → {', '.join(_spec(s)['key'] for s in (t.get('cells') or {}).values())}")
        if pd.get("keep"):
            lines.append(f"  残す: {', '.join(pd['keep'])}")
        if pd.get("clear"):
            lines.append(f"  空にする: {', '.join(pd['clear'])}")
    if definition.get("needs_confirm"):
        lines.append("")
        lines.append("確認すること:")
        lines.extend(f"  ? {c}" for c in definition["needs_confirm"])
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# 文書の値（表紙・タイトルの年度・期間・宛名・作成日など）。データでもラベルでもなく、文書ごとに変わる値
# ---------------------------------------------------------------------------

DOC_PREFIX = "文書"
# ラベルがこれなら、その右の値は表の外の 1 件の値ではなく、文書全体の値（どのタブでも同じ値が入る）
DOC_LABEL_RE = re.compile(r"^(作成日|作成者|作成部署|発行日|発行者|提出日|提出先|報告日|報告者|更新日|改訂日|日付|"
                          r"文書番号|文書名|資料番号|管理番号|版|版数|バージョン|Ver\.?|宛先|宛名|件名|表題|タイトル|"
                          r"プロジェクト名?|案件名|システム名|対象期間|期間|報告期間|年度)$", re.I)


def _date_fmt(text: str, m: "re.Match", codes: dict) -> str:
    """一致した日付の文字から、同じ見た目に戻す書式（`2025/07/01` → `%Y/%m/%d`、`7月` → `%-m月`）を作る。"""
    # 月・日のどれかが 0 埋め（07）なら 0 埋めの書式、どれも 1 桁か 10 以上なら 0 を付けない書式
    padded = any(m.group(g).startswith("0") for g, code in codes.items() if code != "Y")
    out, pos = "", m.start()
    for g, code in codes.items():
        a, b = m.span(g)
        out += text[pos:a].replace("%", "%%") + (f"%{code}" if code == "Y" or padded else f"%-{code}")
        pos = b
    return out + text[pos:m.end()].replace("%", "%%")


# 文の中の可変の部分。(名前, 正規表現, 種類)。上から順に、重ならないものを拾う
VAR_PATTERNS = [
    ("日付", re.compile(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "date"),
    ("日付", re.compile(r"(?<![\d./-])(\d{4})([/.\-])(\d{1,2})\2(\d{1,2})(?![\d./-])"), "date_sep"),
    ("年月", re.compile(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月(?!\s*\d)"), "ym"),
    ("年月", re.compile(r"(?<![\d./-])(\d{4})/(\d{1,2})(?![\d./-])"), "ym_sep"),
    ("年度", re.compile(r"(?<!\d)(\d{4})\s*年度"), "int1"),
    ("和暦年度", re.compile(r"(?:令和|平成)\s*(\d{1,2}|元)\s*年度"), "wareki"),
    ("和暦年", re.compile(r"(?:令和|平成)\s*(\d{1,2}|元)\s*年(?!度)"), "wareki"),
    ("年", re.compile(r"(?<!\d)(\d{4})\s*年(?![度\d])"), "int1"),
    ("四半期", re.compile(r"第\s*([1-4])\s*四半期"), "int1"),
    ("四半期", re.compile(r"(?<![A-Za-z0-9])Q([1-4])(?![A-Za-z0-9])"), "int1"),
    ("四半期", re.compile(r"(?<![A-Za-z0-9])([1-4])Q(?![A-Za-z0-9])"), "int1"),
    ("半期", re.compile(r"(上|下)半?期"), "str1"),
    ("月", re.compile(r"(?<![\d/.\-])(\d{1,2})\s*月(?=度|分|[\s）)]|$)"), "int1"),
    ("版", re.compile(r"第\s*(\d+(?:\.\d+)*)\s*版"), "str1"),
    ("版", re.compile(r"(?<![A-Za-z])(?:Ver\.?|ver\.?|[vV])\s?(\d+(?:\.\d+)+)"), "str1"),
]
VAR_ADDRESSEE_RE = re.compile(r"^(?P<name>\S.*?)\s*(?P<tail>御中|様|殿)\s*$")
LABELLED_RE = re.compile(r"^(?P<label>[^:：\d]{1,15})\s*[:：]\s*")


def variable_parts(text: str) -> "tuple[str, list[tuple[str, Any]]] | None":
    """文の中の、文書ごとに変わりそうな部分（年度・四半期・年月・日付・版・宛名）を、欄（{名前}）にしたひな形にする。

    返すのは (ひな形, [(欄の名前, 今の値), …])。可変の部分が無ければ None。欄の名前は、まだ `文書.` を付けない仮の名前。
    注記（※）や長い文は対象にしない。
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 80 or NOTE_RE.match(text) or PLACEHOLDER_RE.search(text):
        return None
    hits: list[tuple[int, int, str, str, Any]] = []   # (先頭, 末尾, 名前, 欄の中身, 値)
    taken: list[tuple[int, int]] = []
    for name, rx, kind in VAR_PATTERNS:
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            if kind == "date":
                fmt = _date_fmt(text, m, {1: "Y", 2: "m", 3: "d"})
                value = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                span = m.span()
            elif kind == "date_sep":
                fmt = _date_fmt(text, m, {1: "Y", 3: "m", 4: "d"})
                value = f"{int(m.group(1)):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
                span = m.span()
            elif kind in ("ym", "ym_sep"):
                fmt = _date_fmt(text, m, {1: "Y", 2: "m"})
                value = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
                span = m.span()
            else:
                fmt = ""
                g = m.group(1)
                value = 1 if g == "元" else int(g) if kind in ("int1", "wareki") else g
                span = m.span(1)
            taken.append(m.span())
            hits.append((span[0], span[1], name, fmt, value))
    if not hits:
        m = VAR_ADDRESSEE_RE.match(text)
        if m and len(m.group("name").strip()) >= 2 and not DOC_LABEL_RE.match(m.group("name").strip()):
            return "{宛先}" + text[m.end("name"):], [("宛先", m.group("name").strip())]
        return None
    hits.sort()
    lab = LABELLED_RE.match(text)
    dates = [h for h in hits if h[2] == "日付"]
    names: list[str] = []
    for h in hits:
        name = h[2]
        if lab and len(hits) == len(dates) and dates:
            label = lab.group("label").strip()
            name = label if len(dates) == 1 else (f"{label}.開始" if h is dates[0] else f"{label}.終了") if len(dates) == 2 \
                else label
        names.append(name)
    # 同じ名前が 2 つ以上なら 2, 3 … を付ける（同じ文の中で、違う値を同じ欄にしない）
    seen: dict[str, int] = {}
    out, fields, pos = "", [], 0
    for (a, b, _, fmt, value), name in zip(hits, names):
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}{seen[name]}"
        out += text[pos:a].replace("{", "{{").replace("}", "}}") + "{" + name + (f":{fmt}" if fmt else "") + "}"
        fields.append((name, value))
        pos = b
    out += text[pos:].replace("{", "{{").replace("}", "}}")
    return out, fields


class DocValues:
    """ブック全体の文書の値の名前。同じ名前で同じ値なら、同じキーにする（表紙とヘッダーの年度に、1 つの値が入る）。"""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def key(self, name: str, value: Any) -> str:
        base = re.sub(r"[\s]+", "_", name.strip(" :：")) or "値"
        cand, n = base, 2
        while cand in self.values and self.values[cand] != value and value is not None and self.values[cand] is not None:
            cand, n = f"{base}{n}", n + 1
        if self.values.get(cand) is None:
            self.values[cand] = value
        return f"{DOC_PREFIX}.{cand}"

    def template(self, tpl: str, fields: list[tuple[str, Any]]) -> str:
        """variable_parts の仮の名前を、文書の値のキーに置き換える。"""
        for name, value in fields:
            key = self.key(name, value)
            tpl = re.sub(r"\{" + re.escape(name) + r"(?=[:}])", "{" + key, tpl, count=1)
        return tpl




# ---------------------------------------------------------------------------
# 文の中の一部を差し替える（`{文書.年度}年度 第{文書.四半期}四半期 売上報告書`）
# ---------------------------------------------------------------------------

TEXT_FIELD_RE = re.compile(r"\{\{|\}\}|\{([^{}:]+)(?::([^{}]*))?\}")


def text_fields(tpl: str) -> list[tuple[str, str]]:
    """文のひな形の中の欄 [(データのキー, 書式), …]。`{{`・`}}` は文字の波かっこ。"""
    out, rest, pos = [], "", 0
    for m in TEXT_FIELD_RE.finditer(str(tpl)):
        rest += str(tpl)[pos:m.start()]
        pos = m.end()
        if m.group(1) is not None:
            out.append((m.group(1).strip(), m.group(2) or ""))
    rest += str(tpl)[pos:]
    if "{" in rest or "}" in rest:
        raise TemplateError(f"文のひな形 {tpl!r} の波かっこが閉じていません（文字の波かっこは {{{{ と }}}} と書く）")
    return out


def spec_keys(spec: Any) -> list[str]:
    """固定セルの指定が使う、データのキーの一覧（文のひな形なら、その中の欄のキー）。"""
    if not isinstance(spec, dict):
        return [spec]
    if "text" in spec:
        return list(dict.fromkeys(k for k, _ in text_fields(spec["text"])))
    return [spec["key"]]


def _as_date(value: Any, where: str) -> "dt.date | dt.datetime":
    if isinstance(value, (dt.date, dt.datetime)):
        return value
    s = str(value).strip().replace("/", "-")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)   # 年月だけ（2026-10）
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), 1)
    try:
        return dt.datetime.fromisoformat(s) if (" " in s or "T" in s) else dt.date.fromisoformat(
            "-".join(p.zfill(2) for p in s.split("-")))
    except ValueError:
        raise TemplateError(f"{where} の値 {value!r} を日付として読めません（2026-10-08 か、年月なら 2026-10 の形で書く）")


def _strftime(d: "dt.date | dt.datetime", fmt: str) -> str:
    """strftime と同じ。ただし `%-m`・`%-d`・`%-H`（0 を付けない）は、どの OS でも使える。"""
    def sub(m: "re.Match") -> str:
        code = m.group(1)
        if code.startswith("-"):
            return str(int(d.strftime("%" + code[1:])))
        return "%" if code == "%" else d.strftime("%" + code)
    return re.sub(r"%(-?[A-Za-z%])", sub, fmt)


def _field_text(value: Any, fmt: str, where: str) -> str:
    if value is None:
        return ""
    if "%" in fmt:
        return _strftime(_as_date(value, where), fmt)
    if fmt:
        try:
            return format(value, fmt)
        except (ValueError, TypeError):
            raise TemplateError(f"{where} の値 {value!r} に書式 {fmt!r} を使えません")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def fill_text(tpl: str, data: Any, where: str, escape=None) -> str:
    """文のひな形の欄を、データの値で埋める。値が null の欄は空にする。

    data はデータか、キーから値を引く関数（無ければ KeyError）。
    """
    lookup = data if callable(data) else (lambda key: dig(data, key))
    text_fields(tpl)   # 閉じていない波かっこを先に止める

    def sub(m: "re.Match") -> str:
        if m.group(1) is None:
            return m.group(0)[0]
        key = m.group(1).strip()
        try:
            value = lookup(key)
        except KeyError:
            raise TemplateError(f"データに {key!r} がありません（{where}）")
        text = _field_text(value, m.group(2) or "", f"{where} の {key}")
        return escape(text) if escape else text
    return TEXT_FIELD_RE.sub(sub, str(tpl))


def read_text(tpl: str, text: Any) -> "dict | None":
    """fill_text の逆。文から欄の値を読み戻す（合わなければ None）。数字だけは数値、日付の書式は 2026-10-08 の形にする。"""
    if text is None:
        return None
    pattern, names, n = "", {}, 0
    pos = 0
    for m in TEXT_FIELD_RE.finditer(str(tpl)):
        pattern += re.escape(tpl[pos:m.start()])
        pos = m.end()
        if m.group(1) is None:
            pattern += re.escape(m.group(0)[0])
            continue
        key = m.group(1).strip()
        if key in names:
            pattern += f"(?P={names[key][0]})"
            continue
        names[key] = (f"g{n}", m.group(2) or "")
        pattern += f"(?P<g{n}>.*?)"
        n += 1
    pattern += re.escape(tpl[pos:])
    m = re.fullmatch(pattern, str(text).strip(), re.S) or re.fullmatch(pattern, str(text), re.S)
    if not m:
        return None
    out = {}
    for key, (g, fmt) in names.items():
        v = m.group(g).strip()
        if not v:
            out[key] = None
        elif "%" in fmt:
            try:
                d = dt.datetime.strptime(v, re.sub(r"%-", "%", fmt))
            except ValueError:
                out[key] = v
                continue
            if "%d" in fmt or "%-d" in fmt:
                out[key] = d.date().isoformat() if not re.search(r"%-?[HM]", fmt) else d.isoformat(sep=" ", timespec="minutes")
            else:
                out[key] = f"{d.year:04d}-{d.month:02d}"
        else:
            out[key] = int(v) if re.fullmatch(r"[+-]?\d+", v) and not (len(v) > 1 and v[0] == "0") else v
    return out




_DOC_DATA: "dict | None" = None   # render・check の間だけ、データ全体（文書の値は、部分のデータでなくここから引く）


def _lookup(obj: Any, key: str) -> Any:
    """欄の値。`文書.` で始まるキーは、部分のデータではなく、データ全体の `文書:` から引く。"""
    if key.split(".")[0] == DOC_PREFIX and _DOC_DATA is not None:
        return dig(_DOC_DATA, key)
    return dig(obj, key)


def _hoist_doc(data: dict) -> dict:
    """部分のデータの中に入った `文書:` を、データ全体の `文書:` に移す（extract・雛形）。"""
    doc: dict = {}
    for key, val in list(data.items()):
        for obj in (val if isinstance(val, list) else [val]):
            if isinstance(obj, dict) and isinstance(obj.get(DOC_PREFIX), dict):
                for k, v in obj.pop(DOC_PREFIX).items():
                    if doc.get(k) is None:
                        doc[k] = v
    if doc:
        data = {DOC_PREFIX: {**doc, **(data.get(DOC_PREFIX) or {})}, **{k: v for k, v in data.items() if k != DOC_PREFIX}}
    return data


def header_footer_paras(doc) -> dict:
    """ヘッダー・フッターの段落。`footer1.xml#1`（部品の名前#段落の番号）→ 段落。"""
    out = {}
    for kind in ("header", "footer"):
        for part in _parts_of(doc, kind):
            name = str(part.partname).rsplit("/", 1)[-1]
            for i, p in enumerate(_part_paras(part), start=1):
                out[f"{name}#{i}"] = p
    return out


def _spec(spec: Any) -> dict:
    return spec if isinstance(spec, dict) else {"key": spec}


def _limit(spec: dict, unit: str = "段落") -> str:
    parts = []
    if spec.get("max_items"):
        parts.append(f"最大 {spec['max_items']} {unit}" if spec.get("max_items") != 1 or unit != "段落" else "1 段落")
    if spec.get("max_chars"):
        parts.append(f"{spec['max_chars']} 字まで")
    return f"（{'・'.join(parts)}）" if parts else ""


# ---------------------------------------------------------------------------
# 値の変換（データの意味 ⇔ 文書の見た目）
# ---------------------------------------------------------------------------

def has_path(data: Any, path: str) -> bool:
    """データに、そのキーが書いてあるか（null も書いてあるうち）。"""
    if isinstance(data, dict) and path in data:
        return True
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False
    return True


def dig(data: Any, path: str) -> Any:
    """`a.b.0` のように、ドットと添字でたどる。キーそのものがあれば、そちらを優先する。"""
    if isinstance(data, dict) and path in data:
        return data[path]
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _norm_key(k: Any) -> str:
    if isinstance(k, bool):
        return "true" if k else "false"
    return str(k)


def convert_value(spec: dict, value: Any, where: str = "") -> Any:
    """when（選ばれた見出しに印）・map（表記の置き換え）・part（日付の一部）。"""
    if "when" in spec:
        chosen = value if isinstance(value, list) else ([] if value is None else [value])
        hit = _norm_key(spec["when"]) in {_norm_key(v) for v in chosen}
        return spec.get("mark", "○") if hit else spec.get("unmark", "")
    if "map" in spec:
        table = {_norm_key(k): v for k, v in spec["map"].items()}
        if value is None and "null" not in table and "None" not in table:
            return table.get("false") if "false" in table and spec.get("null_as_false", True) else None
        key = _norm_key(value)
        if key not in table:
            raise TemplateError(f"{where}{spec.get('key')} の値「{value}」は map にありません（{' / '.join(table)} のどれか）")
        return table[key]
    if "part" in spec:
        if value in (None, ""):
            return None
        d = value
        if isinstance(value, str):
            try:
                d = dt.datetime.fromisoformat(value)
            except ValueError:
                raise TemplateError(f"{where}{spec.get('key')} は日付（2026-10-08）で書いてください: {value!r}")
        if spec["part"] not in DATE_PARTS:
            raise TemplateError(f"part は {', '.join(DATE_PARTS)} のどれかです: {spec['part']!r}")
        return getattr(d, spec["part"], None)
    return value


def field_text(spec: dict, obj: Any, index: int, where: str) -> str:
    if "text" in spec:   # 文の一部だけを差し替える（表題の年度・期間など）
        return fill_text(spec["text"], lambda k: _lookup(obj, k), where.strip(". ") or "文")
    key = spec.get("key")
    if key == "$index":
        value: Any = index + 1
    else:
        value = convert_value(spec, _lookup(obj, key) if key else None, where)
    text = to_text(value)
    if spec.get("format") and text:
        text = spec["format"].replace("{n}", str(index + 1)).replace("{}", text)
    return text


def _as_items(value: Any, where: str) -> list[tuple[str, int]]:
    """リストのデータ（文字列の配列、{text, level}、children の入れ子）を (文字, 段) に。"""
    out: list[tuple[str, int]] = []

    def rec(v, lvl):
        if v is None:
            return
        if isinstance(v, list):
            for x in v:
                rec(x, lvl)
        elif isinstance(v, dict):
            out.append((to_text(v.get("text")), int(v.get("level", lvl))))
            rec(v.get("children"), int(v.get("level", lvl)) + 1)
        else:
            out.append((to_text(v), lvl))

    rec(value, 0)
    return out


def _format_extra(spec: "dict | None") -> int:
    fmt = (spec or {}).get("format")
    if not fmt or "{}" not in fmt:
        return 0
    return len(re.sub(r"\{n\}", "0", fmt).replace("{}", ""))


class Fit:
    """収まらない値（サンプルの粒度を超える）を集める。"""

    def __init__(self):
        self.problems: list[str] = []
        self.missing: list[str] = []

    def chars(self, where: str, text: str, limit: "int | None", spec: "dict | None" = None) -> None:
        """字数は値だけを数える（format の決まった文字は数えない）。段落の中の改行は数えず、段落全体で数える。"""
        n = len(text.replace("\n", "")) - _format_extra(spec)
        if limit and n > limit:
            br = "（段落の中の改行は字数を分けない。段落を分けるなら空行）" if "\n" in text else ""
            self.problems.append(f"{where}: {n} 字。サンプルの粒度は {limit} 字まで（{n - limit} 字減らす）{br}")

    def items(self, where: str, n: int, limit: "int | None", unit: str = "段落") -> None:
        if limit and n > limit:
            self.problems.append(f"{where}: {n} {unit}。サンプルの粒度は {limit} {unit}まで（{n - limit} {unit}減らすか、まとめる）")

    def lines(self, where: str, cap: Capacity, text: str, limit: int) -> None:
        need = cap.lines_for(text)
        if need > limit:
            self.problems.append(f"{where}: {need} 行になる。高さの決まったセルに収まるのは {limit} 行"
                                 f"（1 行 約 {int(cap.cpl)} 字・{cap.size:g}pt）")


# ---------------------------------------------------------------------------
# 定義の検査
# ---------------------------------------------------------------------------

def _part_range(pd: dict) -> tuple[int, int]:
    if "blocks" not in pd:
        raise TemplateError(f"parts の {pd.get('id')} に blocks（#5-#8 のようなブロックの範囲）がありません")
    return parse_range(pd["blocks"])


def _field_refs(pd: dict) -> list[str]:
    return list(pd.get("texts") or {}) + list(pd.get("lists") or {}) + [t.get("block") for t in pd.get("tables") or []] \
        + list(pd.get("keep") or []) + list(pd.get("clear") or [])


def _has_fields(pd: dict) -> bool:
    return bool(pd.get("texts") or pd.get("lists") or pd.get("tables"))


def _pkey(pd: dict) -> str:
    return pd.get("key") or pd.get("id")


def validate_definition(template: "str | bytes", definition: dict) -> None:
    """テンプレートと定義の整合（ブロックの範囲・段落・表・見出し）を確かめる。"""
    if definition.get("version") != DEF_VERSION:
        raise TemplateError(f"定義ファイルの version が未対応です: {definition.get('version')!r}")
    view = DocView(open_doc(read_bytes(template)))
    total = len(view.blocks)
    ranges = []
    keys: dict[str, str] = {}
    for pd in definition.get("parts", []):
        a, b = _part_range(pd)
        if b > total:
            raise TemplateError(f"{pd.get('id')} の範囲 {pd['blocks']} がテンプレートを超えます（{total} ブロック）")
        for x, y, other in ranges:
            if a <= y and x <= b:
                raise TemplateError(f"{pd.get('id')}（{pd['blocks']}）と {other}（{fmt_range(x, y)}）の範囲が重なります")
        ranges.append((a, b, pd.get("id")))
        if pd.get("drop"):
            continue
        if _has_fields(pd):
            key = _pkey(pd)
            if key in keys:
                raise TemplateError(f"{keys[key]} と {pd.get('id')} のデータのキー `{key}` が同じです")
            keys[key] = pd.get("id")
        where = f"{pd.get('id')}（{pd['blocks']}）"
        for ref in _field_refs(pd):
            if ref is None:
                raise TemplateError(f"{where} の表に block がありません")
            s, e = parse_range(ref)
            if s < a or e > b:
                raise TemplateError(f"{where}: {ref} は、この部分の範囲の外です")
        for section in ("texts", "lists"):
            for ref, spec in (pd.get(section) or {}).items():
                s, e = parse_range(ref)
                for k in range(s, e + 1):
                    el = view.block(k)
                    if local(el) == "tbl" or (local(el) == "sdt" and (section == "lists" or e > s)):
                        raise TemplateError(f"{where}: #{k} は段落ではありません（{block_kind(view, el)}）。表は tables に書く")
                    if local(el) == "sdt" and not sdt_paras(el):
                        raise TemplateError(f"{where}: #{k} のコンテンツコントロールは、段落を持ちません")
                after = _spec(spec).get("after")
                if after and not para_text(view.block(s)).startswith(after):
                    raise TemplateError(f"{where}: {ref} の after「{after}」が、テンプレートの段落の頭にありません"
                                        f"（「{_short(para_text(view.block(s)), 20)}」）")
        for t in pd.get("tables") or []:
            s, e = parse_range(t["block"])
            el = view.block(s)
            if local(el) != "tbl" or e != s:
                raise TemplateError(f"{where}: {t['block']} は表ではありません（{block_kind(view, el)}）")
            trs = _table_rows(el)
            if t.get("key"):
                head, foot = int(t.get("header_rows", 1)), int(t.get("footer_rows", 0))
                if len(trs) - head - foot < 1:
                    raise TemplateError(f"{where}: {t['block']} の表に見本の行がありません（header_rows・footer_rows を確かめる）")
                for tr in trs[head:len(trs) - foot]:
                    if any(tc.find(f"{qw('tcPr')}/{qw('vMerge')}") is not None for tc in _tcs(tr)):
                        raise TemplateError(f"{where}: {t['block']} の表の見本の行に、縦の結合があります（繰り返せない）")
                cols = t.get("columns") or []
                width = max(len(_tcs(tr)) for tr in trs)
                if len(cols) > width:
                    raise TemplateError(f"{where}: {t['block']} の columns が {len(cols)} 列あります（表は {width} 列）")
                for j, c in enumerate(cols):
                    if c and c.get("header") is not None and head:
                        tcs = _tcs(trs[head - 1])
                        actual = re.sub(r"\s+", " ", cell_text(tcs[j]).strip()) if j < len(tcs) else ""
                        if actual != c["header"]:
                            raise TemplateError(f"{where}: {t['block']} の表の {j + 1} 列目の見出しが定義と違います"
                                                f"（定義「{c['header']}」・テンプレート「{actual}」）")
            for rc in (t.get("cells") or {}):
                r, c = _rc(rc)
                if r > len(trs) or c > len(_tcs(trs[r - 1])):
                    raise TemplateError(f"{where}: {t['block']} の表に {rc} のセルがありません")
    if definition.get("max_pages") is not None and int(definition["max_pages"]) < 1:
        raise TemplateError("max_pages は 1 以上にしてください")


def _rc(spec: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*$", str(spec))
    if not m:
        raise TemplateError(f"表のセルは「行,列」（1 始まり）で書いてください: {spec!r}")
    return int(m.group(1)), int(m.group(2))


def find_leftovers(view: DocView, definition: dict) -> list[str]:
    """strict: 定義のどこにも入らないまま、テンプレートの文字が残るブロック。"""
    owner: dict[int, dict] = {}
    for pd in definition.get("parts", []):
        a, b = _part_range(pd)
        for n in range(a, b + 1):
            owner[n] = pd
    out = []
    for n, el in enumerate(view.blocks, start=1):
        pd = owner.get(n) or {}
        if pd.get("drop"):
            continue
        covered = set()
        for ref in list(pd.get("texts") or {}) + list(pd.get("lists") or {}) + list(pd.get("keep") or []) \
                + list(pd.get("clear") or []):
            s, e = parse_range(ref)
            covered.update(range(s, e + 1))
        tables = {parse_range(t["block"])[0]: t for t in pd.get("tables") or []}
        if n in covered:
            continue
        if local(el) == "tbl" and n in tables:
            t = tables[n]
            if not t.get("key"):
                continue
            trs = _table_rows(el)
            lo, hi = int(t.get("header_rows", 1)), len(trs) - int(t.get("footer_rows", 0))
            cols = t.get("columns") or []
            cells = {_rc(rc) for rc in (t.get("cells") or {})}
            for r in range(hi, len(trs)):   # 合計の行: 数字の残る値の欄は、cells で入れ直す
                for j, tc in enumerate(_tcs(trs[r])):
                    spec = cols[j] if j < len(cols) else None
                    text = cell_text(tc)
                    if j and spec and spec.get("key") and not spec.get("keep") and (r + 1, j + 1) not in cells \
                            and re.search(r"\d", text):
                        out.append(f"#{n} の表 {r + 1} 行 {j + 1} 列（最後の行）: 「{_short(text, 20)}」")
            for r in range(lo, hi):
                for j, tc in enumerate(_tcs(trs[r])):
                    if (j >= len(cols) or cols[j] is None) and cell_text(tc).strip():
                        out.append(f"#{n} の表 {r + 1} 行 {j + 1} 列: 「{_short(cell_text(tc), 20)}」")
            continue
        text = literal_text(el)
        if text.strip():
            out.append(f"#{n}（{block_kind(view, el)}）: 「{_short(text, 24)}」")
    return out


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _replace(old: list, new: list) -> None:
    parent = old[0].getparent()
    idx = parent.index(old[0])
    for o in old:
        o.getparent().remove(o)
    for k, e in enumerate(new):
        parent.insert(idx + k, e)


def fill_paragraphs(samples: list, paras: list[str], after: "str | None" = None, levels: "list[int] | None" = None,
                    view: "DocView | None" = None) -> list:
    """段落を入れ替える。段落・文字の書式は、同じ位置の見本の段落（リストなら同じ段の見本）から取る。

    見本より多い段落は、最後の見本（リストなら同じ段の最後の見本）に合わせる。セクションの区切りは最後の段落に残す。
    """
    sect = None
    last = samples[-1]
    if local(last) == "p" and has_section_break(last):
        sect = deepcopy(last.find(qw("pPr")).find(qw("sectPr")))
    by_level: dict[int, Any] = {}
    if levels is not None and view is not None:
        for s in samples:
            by_level.setdefault(view.list_level(s), s)
    new = []
    for i, text in enumerate(paras or [""]):
        if levels is not None and view is not None:
            lvl = levels[i] if paras else 0
            lower = [k for k in by_level if k <= lvl]
            proto = by_level.get(lvl)
            if proto is None:
                proto = by_level[max(lower)] if lower else samples[0]
            p = _fresh(deepcopy(proto))
            if view.list_level(proto) != lvl:
                _set_ilvl(view, p, proto, lvl)
        else:
            p = _fresh(deepcopy(samples[min(i, len(samples) - 1)]))
        ppr = p.find(qw("pPr"))
        if ppr is not None and ppr.find(qw("sectPr")) is not None:
            ppr.remove(ppr.find(qw("sectPr")))
        set_para_text(p, text, after if i == 0 else None)
        new.append(p)
    if sect is not None:
        ppr = new[-1].find(qw("pPr"))
        if ppr is None:
            ppr = etree.Element(qw("pPr"))
            new[-1].insert(0, ppr)
        ppr.append(sect)
    _replace(samples, new)
    return new


def _set_ilvl(view: DocView, p, proto, lvl: int) -> None:
    num = view.numpr(proto)
    if not num:
        return
    ppr = p.find(qw("pPr"))
    if ppr is None:
        ppr = etree.Element(qw("pPr"))
        p.insert(0, ppr)
    np_ = ppr.find(qw("numPr"))
    if np_ is None:
        np_ = etree.Element(qw("numPr"))
        nxt = next((c for c in ppr if local(c) not in ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr",
                                                        "widowControl")), None)
        if nxt is not None:
            nxt.addprevious(np_)
        else:
            ppr.append(np_)
    for c in list(np_):
        np_.remove(c)
    il = etree.SubElement(np_, qw("ilvl"))
    il.set(qw("val"), str(lvl))
    ni = etree.SubElement(np_, qw("numId"))
    ni.set(qw("val"), str(num[0]))


def restart_numbering(view: DocView, els: list, cache: dict) -> None:
    """繰り返した節の中の番号つきリスト（1. 2. 3.）を、節ごとに 1 から振り直す。見出しの番号は続ける。"""
    if view.numbering is None:
        return
    for p in [x for el in els for x in el.iter(qw("p"))]:
        if view.heading_level(p) is not None:
            continue
        num = view.numpr(p)
        if not num or view.is_bullet(*num):
            continue
        if num[0] not in cache:
            cache[num[0]] = _new_num(view, num[0])
        new_id = cache[num[0]]
        if new_id is None:
            continue
        _set_ilvl(view, p, p, num[1])
        p.find(qw("pPr")).find(qw("numPr")).find(qw("numId")).set(qw("val"), new_id)


def _new_num(view: DocView, num_id: str) -> "str | None":
    root = view.numbering
    src = next((n for n in root.findall(qw("num")) if n.get(qw("numId")) == str(num_id)), None)
    if src is None:
        return None
    ids = [int(n.get(qw("numId"))) for n in root.findall(qw("num")) if (n.get(qw("numId")) or "").isdigit()]
    new_id = str(max(ids + [0]) + 1)
    num = etree.SubElement(root, qw("num"))
    num.set(qw("numId"), new_id)
    an = etree.SubElement(num, qw("abstractNumId"))
    an.set(qw("val"), wval(src, "abstractNumId") or "0")
    for lvl in range(9):
        ov = etree.SubElement(num, qw("lvlOverride"))
        ov.set(qw("ilvl"), str(lvl))
        so = etree.SubElement(ov, qw("startOverride"))
        lv = view._lvl(num_id, lvl)
        so.set(qw("val"), wval(lv, "start") or "1")
    return new_id


def render(template: "str | bytes", definition: dict, data: Any, output: str,
           allow_overflow: bool = False) -> list[str]:
    """テンプレート + 定義 + データ → docx。警告を返す。収まらない値があれば止める（allow_overflow で警告に）。"""
    raw = read_bytes(template)
    validate_definition(raw, definition)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise TemplateError("データは、部分のキーを持つオブジェクトにしてください")
    doc = open_doc(raw)
    view = DocView(doc)
    warnings: list[str] = []
    global _DOC_DATA
    _DOC_DATA = data
    if definition.get("strict"):
        left = find_leftovers(view, definition)
        if left:
            raise TemplateError("テンプレートの値が、定義のどこにも入らないまま残ります（keep・texts・clear などに入れる）:\n  "
                                + "\n  ".join(left))
    parts = sorted(definition.get("parts", []), key=lambda pd: _part_range(pd)[0])
    props = {k: fill_text(v, lambda key: dig(data, key), f"properties の {k}") if isinstance(v, str) else v
             for k, v in (definition.get("properties") or {}).items()}
    known = {_pkey(pd) for pd in parts if not pd.get("drop") and _has_fields(pd)} | {DOC_PREFIX}
    hf = header_footer_paras(doc)
    for ref, tpl in (definition.get("header_footer") or {}).items():   # ヘッダー・フッターの表題・年度
        if ref not in hf:
            raise TemplateError(f"header_footer の {ref} は、テンプレートのヘッダー・フッターにありません（使えるもの: "
                                f"{', '.join(hf) or 'なし'}）")
        set_para_text(hf[ref], fill_text(tpl, lambda key: dig(data, key), f"header_footer の {ref}"))
    unknown = [k for k in data if k not in known]
    if unknown:
        warnings.append(f"データの {', '.join(unknown)} は、どの部分のキーにもありません（綴りの違いを疑う）")
    for pd in parts:
        key = _pkey(pd)
        if pd.get("drop") or key not in data or not _has_fields(pd):
            continue
        objs = data[key] if isinstance(data[key], list) else [data[key]]
        fields = _top_keys(pd)
        extra = sorted({k for o in objs if isinstance(o, dict) for k in o if k not in fields})
        if extra:
            warnings.append(f"データ `{key}` の {', '.join(extra)} は、{pd.get('id')}（{pd['blocks']}）のどの欄にもありません"
                            "（綴りの違いを疑う）")
        for t in pd.get("tables") or []:
            if not t.get("key"):
                continue
            names = {str(c["key"]).split(".")[0] for c in t.get("columns") or [] if c and c.get("key")}
            items = [x for o in objs if isinstance(o, dict) for x in (dig(o, t["key"]) or []) if isinstance(x, dict)]
            extra = sorted({k for x in items for k in x if k not in names})
            if extra and names:
                warnings.append(f"データ `{key}.{t['key']}` の {', '.join(extra)} は、{t['block']} の表のどの欄にもありません"
                                "（綴りの違いを疑う）")
    instances, to_delete = [], []
    for pd in parts:
        a, b = _part_range(pd)
        els = view.blocks[a - 1:b]
        if pd.get("drop"):
            to_delete.extend(els)
            continue
        key = _pkey(pd)
        val = data.get(key) if _has_fields(pd) else None
        if pd.get("repeat"):
            if val is not None and not isinstance(val, list):
                raise TemplateError(f"{pd.get('id')}（{pd['blocks']}）は繰り返す部分です。`{key}` は配列にしてください")
            if _has_fields(pd) and key not in data:
                raise TemplateError(f"データに `{key}`（{pd.get('id')}・{pd['blocks']} を繰り返す部分）がありません。"
                                    f"0 件にして節を取り除くなら `{key}: []` と書いてください")
            objs = list(val or [])
            if not objs:
                to_delete.extend(els)
                continue
            copies = [els] + [[deepcopy(e) for e in els] for _ in objs[1:]]
            anchor = els[-1]
            for i, cp in enumerate(copies[1:], start=1):
                for e in cp:
                    _fresh(e)
                    anchor.addnext(e)
                    anchor = e
            for i, (obj, cp) in enumerate(zip(objs, copies)):
                instances.append((pd, a, cp, obj, i, f"{key}[{i}]"))
                if i:
                    restart_numbering(view, cp, {})
        else:
            if val is not None and not isinstance(val, dict):
                raise TemplateError(f"`{key}` はオブジェクト（欄: 値）にしてください")
            instances.append((pd, a, els, val or {}, 0, key))
    fit = Fit()
    for pd, a, els, obj, i, label in instances:
        _fill_part(view, pd, a, els, obj, i, label, fit)
    if fit.missing:
        warnings.append("データに無いので空にした欄: " + "、".join(fit.missing)
                        + "（空にするつもりなら null と書くと、この警告は出ない）")
    for el in to_delete:
        if el.getparent() is not None:
            el.getparent().remove(el)
    if definition.get("max_pages"):
        pages = DocView(doc).estimate_pages()
        if pages > int(definition["max_pages"]):
            fit.problems.append(f"文書全体: 約 {pages} ページになる（見積もり）。max_pages は {definition['max_pages']} ページ"
                                "（長い欄を要約するか、段落・行をまとめる）")
    if fit.problems:
        msg = "収まらない値があります。テンプレートの粒度に合わせて、データの量を減らすか言い換えてください:\n  " \
              + "\n  ".join(fit.problems)
        if not allow_overflow:
            raise TemplateError(msg)
        warnings.append(msg)
    if props.get("scrub"):
        n_comments = drop_comments(doc)
        if n_comments:
            warnings.append(f"コメント {n_comments} 件を取り除きました")
        n_rev = accept_revisions(doc.element.body)
        if n_rev:
            warnings.append(f"変更履歴 {n_rev} か所を承諾しました（作成者の名前を残さない）")
        drop_attached_template(doc)
    if has_update_fields(doc.element.body):
        set_update_fields(doc)
        warnings.append("目次・相互参照があります。Word で開くと更新を求めます（LibreOffice では、ツールの「更新」で直す）")
    buf = io.BytesIO()
    doc.save(buf)
    out = finish_package(buf.getvalue(), props)
    with open(output, "wb") as f:
        f.write(out)
    return warnings


def _top_keys(pd: dict) -> set:
    keys = [_spec(s).get("key") for s in list((pd.get("texts") or {}).values()) + list((pd.get("lists") or {}).values())]
    for t in pd.get("tables") or []:
        keys += [t.get("key")] + [_spec(s).get("key") for s in (t.get("cells") or {}).values()]
    return {k.split(".")[0] for k in keys if k}


def _fill_part(view: DocView, pd: dict, a: int, els: list, obj: dict, index: int, label: str, fit: Fit) -> None:
    def at(ref):
        s, e = parse_range(ref)
        return els[s - a:e - a + 1]

    targets = []
    for ref, spec in (pd.get("texts") or {}).items():
        targets.append(("texts", ref, _spec(spec), at(ref)))
    for ref, spec in (pd.get("lists") or {}).items():
        targets.append(("lists", ref, _spec(spec), at(ref)))
    for t in pd.get("tables") or []:
        targets.append(("tables", t["block"], t, at(t["block"])))
    for ref in pd.get("clear") or []:
        targets.append(("clear", ref, {}, at(ref)))
    for kind, ref, spec, blocks in targets:
        where = f"{label}.{spec.get('key') or spec.get('text')}（{ref}）"
        if kind == "texts":
            if spec.get("keep"):
                continue
            if not spec.get("clear") and spec.get("key") not in (None, "$index") and not has_path(obj, spec["key"]):
                fit.missing.append(where)
            text = "" if spec.get("clear") else field_text(spec, obj, index, f"{label}.")
            paras = split_paras(text)
            fit.items(where, len(paras), spec.get("max_items"))
            for k, t in enumerate(paras):
                fit.chars(where if len(paras) == 1 else f"{where} {k + 1} 段落め", t, spec.get("max_chars"),
                          spec if len(paras) == 1 else None)
            if local(blocks[0]) == "sdt":
                _fill_sdt(blocks[0], paras)
            else:
                fill_paragraphs(blocks, paras, spec.get("after"))
        elif kind == "lists":
            if not has_path(obj, spec["key"]):
                fit.missing.append(where)
            items = _as_items(dig(obj, spec["key"]), where)
            fit.items(where, len(items), spec.get("max_items"), "項目")
            for k, (t, _) in enumerate(items):
                fit.chars(f"{where} {k + 1} 項目め", t, spec.get("max_chars"))
            base = min(view.list_level(s) for s in blocks)
            fill_paragraphs(blocks, [t for t, _ in items], levels=[base + lvl for _, lvl in items], view=view)
        elif kind == "tables":
            _fill_table(view, blocks[0], spec, obj, label, fit)
        else:
            for el in blocks:
                for p in ([el] if local(el) == "p" else list(el.iter(qw("p")))):
                    set_para_text(p, "")


def _showing_placeholder(el) -> bool:
    return local(el) == "sdt" and el.find(f"{qw('sdtPr')}/{qw('showingPlcHdr')}") is not None


def _fill_sdt(el, paras: list[str]) -> None:
    pr = el.find(qw("sdtPr"))
    shown = pr is not None and pr.find(qw("showingPlcHdr")) is not None
    if shown and not paras:
        return   # 値が無ければ、説明の文字（クリックして入力…）のまま残す
    if shown:
        pr.remove(pr.find(qw("showingPlcHdr")))
    ps = sdt_paras(el)
    fill_paragraphs(ps, paras)
    for p in sdt_paras(el):   # 説明の文字の書式（プレースホルダーの文字のスタイル・灰色）を外す
        for r in text_runs(p):
            rs = r.find(f"{qw('rPr')}/{qw('rStyle')}")
            if rs is not None and "placeholder" in (rs.get(qw("val")) or "").lower():
                rs.getparent().remove(rs)
            color = r.find(f"{qw('rPr')}/{qw('color')}")
            if shown and color is not None:
                color.getparent().remove(color)


def _cell_rpr(tc):
    for p in tc.iter(qw("p")):
        for r in text_runs(p):
            if r.find(qw("rPr")) is not None:
                return r.find(qw("rPr"))
        m = _mark_rpr(p)
        if m is not None and len(m):
            return m
    return None


def _borrow_format(tc, pool) -> None:
    """空のセル（文字も書式も持たない）に、pool のセル（同じ列の本文の行・同じ行）の文字の書式を写す。

    文字のあるセルは、その書式（書式の無い run ならスタイルの書式）のまま。見出しの行の書式（太字・白抜き）は借りない。
    """
    if any(text_runs(p) for p in tc.findall(qw("p"))) or _cell_rpr(tc) is not None:
        return
    for other in pool:
        src = _cell_rpr(other) if other is not tc else None
        if src is not None:
            for p in tc.findall(qw("p")):
                ppr = p.find(qw("pPr"))
                if ppr is None:
                    ppr = etree.Element(qw("pPr"))
                    p.insert(0, ppr)
                old = ppr.find(qw("rPr"))
                if old is not None:
                    ppr.remove(old)
                ppr.append(deepcopy(src))
            return


def _format_pool(trs, row, col: int, head: int) -> list:
    """書式を借りるセルの順: 同じ列の本文の行 → 同じ行。"""
    def at(r):
        tcs = _tcs(r)
        return [tcs[col]] if col < len(tcs) else []
    return [c for r in trs[head:] for c in at(r)] + _tcs(row)


def set_cell_text(tc, text: str) -> None:
    ps = tc.findall(qw("p"))
    if not ps:
        ps = [etree.SubElement(tc, qw("p"))]
    fill_paragraphs(ps, split_paras(text))


def _exact_lines(view: DocView, tr, tc) -> "tuple[Capacity, int] | None":
    trh = tr.find(f"{qw('trPr')}/{qw('trHeight')}")
    if trh is None or trh.get(qw("hRule")) != "exact":
        return None
    p = tc.find(qw("p"))
    if p is None:
        return None
    cap = view.capacity(p)
    return cap, max(int(int(trh.get(qw("val"), "0")) / TWIPS_PER_PT / cap.line_pt + 0.15), 1)


def _fill_table(view: DocView, tbl, t: dict, obj: dict, label: str, fit: Fit) -> None:
    trs = _table_rows(tbl)
    where = f"{label}（{t['block']} の表）"
    for rc, spec in (t.get("cells") or {}).items():
        spec = _spec(spec)
        if spec.get("keep"):
            continue
        r, c = _rc(rc)
        tc = _tcs(trs[r - 1])[c - 1]
        cw = f"{label}.{spec.get('key')}（{t['block']} の表 {rc}）"
        text = field_text(spec, obj, 0, f"{label}.")
        fit.chars(cw, text, spec.get("max_chars"), spec)
        ex = _exact_lines(view, trs[r - 1], tc)
        if ex and text:
            fit.lines(cw, ex[0], text, ex[1])
        _borrow_format(tc, _format_pool(trs, trs[r - 1], c - 1, int(t.get("header_rows", 1))))
        set_cell_text(tc, text)
    if not t.get("key"):
        return
    head, foot = int(t.get("header_rows", 1)), int(t.get("footer_rows", 0))
    samples = trs[head:len(trs) - foot]
    rows = dig(obj, t["key"]) or []
    if not isinstance(rows, list):
        raise TemplateError(f"{label}.{t['key']}（{t['block']} の表）は配列にしてください")
    fit.items(f"{label}.{t['key']}（{t['block']} の表）", len(rows), t.get("max_rows"), "行")
    cols = t.get("columns") or []
    parent = samples[0].getparent()
    insert_at = list(parent).index(samples[0])
    for s in samples:
        parent.remove(s)
    new_rows = []
    for i, row in enumerate(rows or [None]):
        tr = _fresh(deepcopy(samples[i % len(samples)]))
        for j, tc in enumerate(_tcs(tr)):
            spec = cols[j] if j < len(cols) else None
            if not spec or spec.get("keep"):
                continue
            if row is None or spec.get("clear"):
                set_cell_text(tc, "")
                continue
            rw = f"{label}.{t['key']}[{i}].{spec.get('key')}（{t['block']} の表）"
            text = field_text(spec, row, i, f"{label}.{t['key']}[{i}].")
            fit.chars(rw, text, spec.get("max_chars"), spec)
            ex = _exact_lines(view, tr, tc)
            if ex and text:
                fit.lines(rw, ex[0], text, ex[1])
            _borrow_format(tc, _format_pool(trs[:head] + samples, tr, j, head))
            set_cell_text(tc, text)
        new_rows.append(tr)
    for k, tr in enumerate(new_rows):
        parent.insert(insert_at + k, tr)


# ---------------------------------------------------------------------------
# extract（記入済みの文書からデータを取り出す。render の逆）
# ---------------------------------------------------------------------------

def _read_back(pairs: list[tuple[dict, str]]) -> dict:
    """(欄の指定, 文書の文字) の組から、データの値を起こす。when は選ばれた見出しに、part は日付にまとめる。"""
    out: dict[str, Any] = {}
    choices: dict[str, list] = {}
    parts: dict[str, dict] = {}
    for spec, text in pairs:
        if "text" in spec:
            got = read_text(spec["text"], text.strip()) or {}
            for k in spec_keys(spec):
                if out.get(k) is None:
                    out[k] = got.get(k)
            continue
        key = spec.get("key")
        if not key or key == "$index" or spec.get("keep") or spec.get("clear"):
            continue
        text = text.strip()
        if "map" in spec:
            hit = [k for k, v in spec["map"].items() if text == to_text(v).strip()]
            if hit or not text:
                k = hit[0] if hit else None
                out[key] = {"true": True, "false": False}.get(k, k) if isinstance(k, str) else k
                continue
        if text in MARK_OFF:
            text = ""
        if "when" in spec:
            choices.setdefault(key, [])
            if text and text == str(spec.get("mark", "○")).strip() or (text in MARK_ON and "mark" not in spec):
                choices[key].append(spec["when"])
            continue
        if "part" in spec:
            if text.isdigit():
                parts.setdefault(key, {})[spec["part"]] = int(text)
            else:
                parts.setdefault(key, {})
            continue
        if spec.get("format") and text:
            pat = "^" + re.escape(spec["format"]).replace(re.escape("{n}"), r"\d+").replace(re.escape("{}"), "(.*)") + "$"
            m = re.match(pat, text, re.S)
            text = m.group(1) if m and m.groups() else text
        out[key] = text if text else None
    for key, chosen in choices.items():
        out[key] = chosen[0] if len(chosen) == 1 else (chosen or None)
    for key, p in parts.items():
        if {"year", "month", "day"} <= set(p):
            try:
                d = dt.datetime(p["year"], p["month"], p["day"], p.get("hour", 0), p.get("minute", 0))
                out[key] = d.date().isoformat() if not ({"hour", "minute"} & set(p)) else d.isoformat(timespec="minutes")
            except ValueError:
                out[key] = None
        else:
            out[key] = None
    nested: dict = {}
    for k, v in out.items():
        _set_path(nested, k, v)
    return nested


def _set_path(root: dict, path: str, value: Any) -> None:
    cur = root
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _as_lists(obj: Any) -> Any:
    if not isinstance(obj, dict):
        return [_as_lists(x) for x in obj] if isinstance(obj, list) else obj
    obj = {k: _as_lists(v) for k, v in obj.items()}
    if obj and set(obj) == {str(i) for i in range(len(obj))}:
        return [obj[str(i)] for i in range(len(obj))]
    return obj


def _is_empty(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_is_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_empty(v) for v in value)
    return value in (None, False, "")


class _Slot:
    def __init__(self, kind: str, **kw):
        self.kind = kind
        self.sigs: set = kw.get("sigs", set())
        self.any_list = kw.get("any_list", False)
        self.n = kw.get("n", 0)
        self.lo, self.hi = kw.get("lo", 1), kw.get("hi", 1)
        self.body: list = kw.get("body", [])
        self.part = kw.get("part")

    def accepts(self, tok: tuple) -> bool:
        return tok in self.sigs or (self.any_list and tok[0] == "p" and tok[2])


def _part_slots(tview: DocView, pd: dict, a: int, b: int) -> list:
    runs: dict[int, tuple[int, str]] = {}
    for section in ("texts", "lists"):
        for ref, spec in (pd.get(section) or {}).items():
            s, e = parse_range(ref)
            many = section == "lists" or (_spec(spec).get("max_items") or 2) > 1 or e > s
            runs[s] = (e, section if many else "")
    out = []
    n = a
    while n <= b:
        el = tview.blocks[n - 1]
        if n in runs and runs[n][1]:
            e, section = runs[n]
            sigs = {_sig(tview, tview.blocks[k - 1]) for k in range(n, e + 1)}
            out.append(_Slot("run", sigs=sigs, any_list=section == "lists", n=n, lo=0, hi=10 ** 6))
            n = e + 1
            continue
        empty = local(el) == "p" and not para_text(el).strip() and not has_drawing(el) and n not in runs
        out.append(_Slot("run" if empty else "fixed", sigs={_sig(tview, el)}, n=n, lo=0 if empty else 1, hi=1))
        n += 1
    return out


class _Matcher:
    """テンプレートの並び（部分・繰り返し・段落の続き）と、記入済みの文書のブロックの並びを突き合わせる。"""

    def __init__(self, toks: list):
        self.toks = toks
        self.far = 0

    def seq(self, slots: list, i: int, pos: int, k):
        if i == len(slots):
            return k(pos)
        s = slots[i]
        if s.kind in ("fixed", "run"):
            j = pos
            while j < len(self.toks) and j - pos < s.hi and s.accepts(self.toks[j]):
                j += 1
            self.far = max(self.far, j)
            for end in range(j, pos + s.lo - 1, -1):
                r = self.seq(slots, i + 1, end, k)
                if r is not None:
                    return [("slot", s, pos, end)] + r
            return None
        if s.kind == "part":
            r = self.seq(s.body, 0, pos, lambda p: self._close(s, slots, i + 1, p, k))
            return None if r is None else [("begin", s, pos, pos)] + r

        def again(p0):   # 繰り返し（rep・drop）: 1 回ずつ、少なくとも 1 ブロックを読む。読めなくなったら次へ
            r = self.seq(s.body, 0, p0, lambda p: None if p == p0 else self._close(s, None, 0, p, lambda q: again(q)))
            if r is not None:
                return [("begin", s, p0, p0)] + r
            return self.seq(slots, i + 1, p0, k)
        return again(pos)

    def _close(self, s, slots, i, p, k):
        rest = self.seq(slots, i, p, k) if slots is not None else k(p)
        return None if rest is None else [("end", s, p, p)] + rest


def _template_slots(tview: DocView, definition: dict) -> list:
    owner = {}
    for pd in definition.get("parts", []):
        a, b = _part_range(pd)
        owner[a] = (pd, a, b)
    slots = []
    n = 1
    while n <= len(tview.blocks):
        if n not in owner:
            el = tview.blocks[n - 1]
            empty = local(el) == "p" and not para_text(el).strip() and not has_drawing(el)
            slots.append(_Slot("run" if empty else "fixed", sigs={_sig(tview, el)}, n=n, lo=0 if empty else 1, hi=1))
            n += 1
            continue
        pd, a, b = owner[n]
        body = _part_slots(tview, pd, a, b)
        kind = "drop" if pd.get("drop") else ("rep" if pd.get("repeat") else "part")
        slots.append(_Slot(kind, body=body, part=pd, n=a))
        n = b + 1
    return slots


def _read_part(view: DocView, pd: dict, a: int, mapping: dict, notes: list, label: str, index: int) -> dict:
    def blocks(ref):
        return mapping.get(parse_range(ref)[0], [])

    pairs: list[tuple[dict, str]] = []
    for ref, spec in (pd.get("texts") or {}).items():
        spec = _spec(spec)
        els = blocks(ref)
        ps = [p for el in els if not _showing_placeholder(el)   # 説明の文字のままの入力欄は、値なし
              for p in (sdt_paras(el) if local(el) == "sdt" else [el])]
        texts = [para_text(p) for p in ps]
        if texts and spec.get("after") and texts[0].startswith(spec["after"]):
            texts[0] = texts[0][len(spec["after"]):]
        text = "\n\n".join(t for t in texts if t.strip())
        if spec.get("format") and "{n}" in spec["format"]:
            spec = {**spec, "format": spec["format"].replace("{n}", str(index + 1))}
        pairs.append((spec, text))
    obj = _read_back(pairs)
    for ref, spec in (pd.get("lists") or {}).items():
        spec = _spec(spec)
        ps = [p for p in blocks(ref) if para_text(p).strip()]
        base = min((view.list_level(p) for p in ps), default=0)
        items = [(para_text(p), view.list_level(p) - base) for p in ps]
        _set_path(obj, spec["key"], [t if lvl == 0 else {"text": t, "level": lvl} for t, lvl in items])
    for t in pd.get("tables") or []:
        els = blocks(t["block"])
        if not els:
            continue
        trs = _table_rows(els[0])
        tlen = t.get("_template_rows", len(trs))
        cell_pairs = []
        for rc, spec in (t.get("cells") or {}).items():
            if _spec(spec).get("keep"):
                continue
            r, c = _rc(rc)
            if r > tlen - int(t.get("footer_rows", 0)):   # 合計の行は、行が増えても表の最後から数える
                r = len(trs) - (tlen - r)
            if 1 <= r <= len(trs) and c <= len(_tcs(trs[r - 1])):
                cell_pairs.append((_spec(spec), cell_text(_tcs(trs[r - 1])[c - 1])))
        for k, v in _read_back(cell_pairs).items():
            obj[k] = v
        if not t.get("key"):
            continue
        head, foot = int(t.get("header_rows", 1)), int(t.get("footer_rows", 0))
        rows = []
        cols = t.get("columns") or []
        for r, tr in enumerate(trs[head:len(trs) - foot], start=head + 1):
            tcs = _tcs(tr)
            rec = _read_back([(c, cell_text(tcs[j])) for j, c in enumerate(cols) if c and j < len(tcs)])
            if _is_empty(rec):
                notes.append(f"{label} {t['block']} の表 {r} 行め: 空の行なので書かない")
                continue
            rows.append(rec)
        keys = [c["key"] for c in cols if c and c.get("key") and c["key"] != "$index" and not c.get("keep") and not c.get("clear")]
        for rec in rows:
            for k in keys:
                if dig(rec, k) is None and k not in rec:
                    _set_path(rec, k, None)
        _set_path(obj, t["key"], rows)
    return _as_lists(obj)


def extract(source: "str | bytes", definition: dict, template: "str | bytes | None" = None) -> tuple[dict, list[str]]:
    """記入済みの docx から、定義の key の欄だけをデータにする。テンプレートの並びと突き合わせて読む。"""
    if template is None:
        raise TemplateError("並びを突き合わせるのに、テンプレートが要ります（--template か、定義ファイルの template）")
    validate_definition(read_bytes(template), definition)
    tview = DocView(open_doc(read_bytes(template)))
    view = DocView(open_doc(read_bytes(source)))
    for pd in definition.get("parts", []):   # 合計の行を表の最後から数えるため、テンプレートの行数を覚える
        for t in pd.get("tables") or []:
            t["_template_rows"] = len(_table_rows(tview.block(parse_range(t["block"])[0])))
    toks = [_sig(view, el) for el in view.blocks]
    m = _Matcher(toks)
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 20000 + 40 * len(toks)))
    try:
        events = m.seq(_template_slots(tview, definition), 0, 0, lambda p: [] if p == len(toks) else None)
    finally:
        sys.setrecursionlimit(limit)
    if events is None:
        at = min(m.far, len(view.blocks) - 1)
        el = view.blocks[at] if view.blocks else None
        what = f"#{at + 1}（{block_kind(view, el)}「{_short(literal_text(el), 20)}」）" if el is not None else "文書の最後"
        raise TemplateError(f"文書の並びが、テンプレートと合いません。{what} のあたりから先が、定義のどの部分にも当たりません"
                            "（テンプレートと違う段落・表が足されていないか確かめる）")
    data: dict[str, Any] = {}
    notes: list[str] = []
    stack: list = []
    for kind, s, start, end in events:
        if kind == "begin":
            stack.append((s, {}))
        elif kind == "slot":
            if stack:
                stack[-1][1][s.n] = view.blocks[start:end]
        elif kind == "end":
            slot, mapping = stack.pop()
            pd = slot.part
            if slot.kind == "drop" or not _has_fields(pd):
                continue
            key = _pkey(pd)
            a = _part_range(pd)[0]
            if pd.get("repeat"):
                lst = data.setdefault(key, [])
                lst.append(_read_part(view, pd, a, mapping, notes, f"{key}[{len(lst)}]", len(lst)))
            else:
                data[key] = _read_part(view, pd, a, mapping, notes, key, 0)
    for pd in definition.get("parts", []):
        for t in pd.get("tables") or []:
            t.pop("_template_rows", None)
        if pd.get("repeat") and _has_fields(pd):
            data.setdefault(_pkey(pd), [])
    data = _hoist_doc(data)
    hf = header_footer_paras(view.doc)
    for ref, tpl in (definition.get("header_footer") or {}).items():
        got = read_text(tpl, para_text(hf[ref])) if ref in hf else None
        for key, _ in text_fields(tpl):
            if dig(data, key) is None:
                _set_path(data, key, (got or {}).get(key))
    return data, notes


# ---------------------------------------------------------------------------
# データの形（定義から導く）
# ---------------------------------------------------------------------------

def _field_keys(specs) -> list[str]:
    return [s["key"] for s in specs if s and s.get("key") and s["key"] != "$index" and not s.get("keep") and not s.get("clear")]


def skeleton_data(definition: dict) -> dict:
    """定義が必要とするデータの雛形（値は null。配列は 1 件）。"""
    out: dict = {}
    for pd in definition.get("parts", []):
        if pd.get("drop") or not _has_fields(pd):
            continue
        obj: dict = {}
        for k in _field_keys(_spec(s) for s in (pd.get("texts") or {}).values()):
            _set_path(obj, k, None)
        for k in (k for s in (pd.get("texts") or {}).values() if "text" in _spec(s) for k in spec_keys(s)):
            _set_path(obj, k, None)
        for s in (pd.get("lists") or {}).values():
            _set_path(obj, _spec(s)["key"], [None])
        for t in pd.get("tables") or []:
            for k in _field_keys(_spec(s) for s in (t.get("cells") or {}).values()):
                _set_path(obj, k, None)
            if t.get("key"):
                row: dict = {}
                for k in _field_keys(t.get("columns") or []):
                    _set_path(row, k, None)
                _set_path(obj, t["key"], [row])
        out[_pkey(pd)] = [_as_lists(obj)] if pd.get("repeat") else _as_lists(obj)
    for tpl in list((definition.get("header_footer") or {}).values()) \
            + [v for v in (definition.get("properties") or {}).values() if isinstance(v, str)]:
        for k, _ in text_fields(tpl):
            _set_path(out, k, None)
    return _hoist_doc(out)


def value_notes(definition: dict) -> list[str]:
    """値の書き方と、収まる量（サンプルの粒度）。雛形の null だけでは分からないものを補う。"""
    notes: dict[str, str] = {}

    def note(name: str, spec: dict, items_word: str = "") -> None:
        bits = []
        if "when" in spec:
            return
        if "map" in spec:
            bits.append(" / ".join(_norm_key(k) for k in spec["map"]) + " のどれか")
        if "part" in spec:
            bits.append("日付（2026-10-08）")
        if spec.get("max_items"):
            bits.append("1 段落（空行で分けない）" if spec["max_items"] == 1 and not items_word
                        else f"最大 {spec['max_items']} {items_word or '段落'}")
        if spec.get("max_chars"):
            bits.append(f"{'1 項目 ' if items_word else ('1 段落 ' if (spec.get('max_items') or 1) > 1 else '')}"
                        f"{spec['max_chars']} 字まで")
        if bits:
            notes[name] = f"{name}: {'・'.join(bits)}"

    for pd in definition.get("parts", []):
        if pd.get("drop") or not _has_fields(pd):
            continue
        base = _pkey(pd) + ("[]" if pd.get("repeat") else "")
        for s in (pd.get("texts") or {}).values():
            s = _spec(s)
            if s.get("key"):
                note(s["key"] if s["key"].startswith(DOC_PREFIX + ".") else f"{base}.{s['key']}", s)
            for key, fmt in text_fields(s["text"]) if "text" in s else []:
                if "%" in fmt:
                    notes[key] = f"{key}: 日付（2026-10-08）" if re.search(r"%-?d", fmt) else f"{key}: 年月（2026-10）"
        for s in (pd.get("lists") or {}).values():
            s = _spec(s)
            note(f"{base}.{s['key']}[]", s, "項目")
        for t in pd.get("tables") or []:
            for s in (t.get("cells") or {}).values():
                note(f"{base}.{_spec(s)['key']}", _spec(s))
            if t.get("key"):
                if t.get("max_rows"):
                    notes[f"{base}.{t['key']}"] = f"{base}.{t['key']}: 最大 {t['max_rows']} 行"
                for c in t.get("columns") or []:
                    if c and c.get("key") and c["key"] != "$index":
                        note(f"{base}.{t['key']}[].{c['key']}", c)
    if definition.get("max_pages"):
        notes["*"] = f"文書全体: {definition['max_pages']} ページまで（見積もり）"
    return list(notes.values())


def check_definition(template: "str | bytes", definition: dict) -> list[str]:
    """定義の検査。整合を確かめ、雛形データで試しに再構成する（strict なら、残る値の漏れもここで見つかる）。"""
    import tempfile
    raw = read_bytes(template)
    validate_definition(raw, definition)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "check.docx")
        warnings = render(raw, definition, skeleton_data(definition), out, allow_overflow=True)
        left = provenance(read_bytes(out))   # 出力に残るものだけ（scrub・drop で消えるものは挙げない）
    warnings = [w for w in warnings if not w.startswith(("収まらない値", "目次・相互参照"))]
    return warnings + [f"来歴（出力に残る）: {p}" for p in left]


# ---------------------------------------------------------------------------
# 入出力（JSON / YAML）
# ---------------------------------------------------------------------------

def _yaml():
    try:
        import yaml
    except ImportError:
        raise TemplateError("YAML の入出力には PyYAML が必要です（uv add pyyaml / pip install pyyaml）")
    return yaml


def parse_structured(text: str, name: str = "") -> Any:
    lower = name.lower()
    try:
        if lower.endswith((".yaml", ".yml")):
            return _yaml().safe_load(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if lower.endswith(".json"):
                raise
            return _yaml().safe_load(text)
    except TemplateError:
        raise
    except Exception as e:  # json.JSONDecodeError・yaml.YAMLError
        raise TemplateError(f"{name or '入力'} を読めません: {e}")


def load_structured(path: str) -> Any:
    if path == "-":
        return parse_structured(sys.stdin.read())
    with open(path, encoding="utf-8") as f:
        return parse_structured(f.read(), path)


def _yaml_dump(obj: Any, f=None):
    """YAML に書く。段落の区切り・改行を含む文字は、読みやすいブロック（|）で書く。"""
    yaml = _yaml()

    class Dumper(yaml.SafeDumper):
        pass

    def text(dumper, s):
        if "\n" in s and not s.startswith((" ", "\n")) and not s.endswith(" ") and "\t" not in s:
            return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", s)

    Dumper.add_representer(str, text)
    return yaml.dump(obj, f, Dumper=Dumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)


def dump_structured(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if path.lower().endswith((".yaml", ".yml")):
            _yaml_dump(obj, f)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")


DATA_EXTS = (".json", ".yaml", ".yml")


def data_files(paths: "str | list[str]") -> list[str]:
    """データの指定（ファイル・フォルダ・-）を、読む順のファイルの並びにする。フォルダは中のデータファイルを名前順に。"""
    out: list[str] = []
    for p in [paths] if isinstance(paths, str) else paths:
        if p != "-" and os.path.isdir(p):
            found = sorted(n for n in os.listdir(p) if n.lower().endswith(DATA_EXTS) and not n.startswith("."))
            if not found:
                raise TemplateError(f"フォルダ {p} にデータファイル（{' / '.join(DATA_EXTS)}）がありません")
            out += [os.path.join(p, n) for n in found]
        else:
            out.append(p)
    return out


def merge_data(parts: list, groups: "dict | None" = None) -> dict:
    """分けたデータ（[(ファイル名, 中身), …]）を 1 つにする。

    オブジェクトはキーごとに合わせ、配列（繰り返す節・リスト・表の行）はファイルの順につなぐ。
    同じ欄に違う値があれば止める（どちらが正しいか分からない）。null は、ほかのファイルの値を消さない。
    配列の同じ項目が 2 つのファイルにあっても止める（前のデータを写したまま、ほかのファイルに残っている）。
    groups（部分のキー → まとまり）があれば、ほかのまとまりのファイルにも書かれた繰り返しの節も止める。
    食い違いは、まとめて挙げる。
    """
    merged: dict = {}
    seen: dict[str, str] = {}
    problems: list[str] = []

    def mark(v, path, src):
        seen.setdefault(path, src)
        if isinstance(v, dict):
            for k, x in v.items():
                mark(x, f"{path}.{k}", src)

    def put(a, b, path, src):
        if isinstance(a, dict) and isinstance(b, dict):
            for k, v in b.items():
                sub = f"{path}.{k}" if path else str(k)
                if k in a:
                    a[k] = put(a[k], v, sub, src)
                else:
                    a[k] = v
                    mark(v, sub, src)
            return a
        if isinstance(a, list) and isinstance(b, list):
            dup = [x for x in b if isinstance(x, dict) and x in a]
            if dup:
                problems.append(f"{path} の同じ項目が、{seen.get(path, '前のファイル')} と {src} の両方にあります"
                                f"（{len(dup)} 件。前のデータを写したままなら、片方から消す）")
            return a + b
        if b is None or a == b:
            return a
        if a is None:
            seen[path] = src
            return b
        problems.append(f"{path} の値が、{seen.get(path, '前のファイル')} と {src} で違います: {a!r} / {b!r}")
        return a

    for src, obj in parts:
        if obj is None:
            continue
        if not isinstance(obj, dict):
            raise TemplateError(f"{src} の中身はオブジェクト（キーと値）にしてください")
        put(merged, deepcopy(obj), "", src)
    if groups:
        where: dict[str, list] = {}
        for src, obj in parts:
            for k, v in (obj or {}).items():
                if isinstance(v, list) and k in groups:
                    where.setdefault(k, []).append(src)
        for k, srcs in where.items():
            if len(srcs) < 2:
                continue
            mixed = [src for src, obj in parts if src in srcs and {groups.get(x) for x in obj if x in groups} != {groups[k]}]
            if mixed:
                problems.append(f"{k}（{groups[k]}）が {', '.join(srcs)} にあります。{', '.join(mixed)} は別のまとまりのファイルなので、"
                                "前のデータを写したままなら消す")
    if problems:
        raise TemplateError("分けたデータが食い違います:\n" + "\n".join(f"  {p}" for p in problems))
    return merged


def load_data(paths: "str | list[str]", definition: "dict | None" = None) -> dict:
    """データを読む。複数のファイル・フォルダなら、1 つにまとめる（merge_data）。定義があれば、まとまり（group）も確かめる。"""
    files = data_files(paths)
    if len(files) == 1:
        return load_structured(files[0])
    groups = {_pkey(pd): pd["group"] for pd in (definition or {}).get("parts", []) if pd.get("group")}
    return merge_data([(f if f != "-" else "標準入力", load_structured(f)) for f in files], groups)


def split_data(data: dict, definition: dict) -> list:
    """データを、部分のまとまり（parts[].group。無ければ部分ごと）のファイルに分ける。[(ファイル名, 中身), …]。

    定義に無いキーは 00-common に置く。名前順に読めば元の順に戻る。
    """
    order: list[str] = []
    owner: dict[str, str] = {}
    for pd in definition.get("parts", []):
        if pd.get("drop") or not _has_fields(pd):
            continue
        group = str(pd.get("group") or _pkey(pd))
        if group not in order:
            order.append(group)
        owner[_pkey(pd)] = group
    parts: dict[str, dict] = {g: {} for g in order}
    rest = {}
    for key, value in data.items():
        if value == {} and key in owner:
            continue
        (parts[owner[key]] if key in owner else rest)[key] = value
    out = [("00-common", rest)] if rest else []
    for n, g in enumerate(order, start=1):
        if parts[g]:
            out.append((f"{n:02d}-" + (re.sub(r"[^\w-]+", "_", g).strip("_") or "part"), parts[g]))
    return out


# ---------------------------------------------------------------------------
# スタンドアローン（この文書専用の render スクリプトを書き出す）
# ---------------------------------------------------------------------------

PEP723 = "\n".join("#" + line for line in (
    " /// script", ' requires-python = ">=3.10"', ' dependencies = ["lxml", "python-docx", "pyyaml"]', " ///"))
STANDALONE_HEADER = '''#!/usr/bin/env python3
{pep723}
"""{title}

docx テンプレートへ内容を流し込む、専用の render スクリプト（docx-document-builder の export で生成）。
スキルは不要で動く。定義はこのファイルに埋め込み済み。{template_note}

    uv run {name} --data data.yaml -o out.docx
    uv run {name} --data data/ -o out.docx            # 分けたデータ（フォルダの中を名前順に）をまとめて流し込む
    python {name} --data data.json -o out.docx        # lxml・python-docx・pyyaml が必要
    python {name} --example-data > data.yaml          # データの雛形を出す
    python {name} --extract-def def.yaml              # 埋め込みの定義を取り出す（直したら export --from-script で再生成）

データの形:
{shape}
"""
'''
ENGINE_VERSION = 1


def _wrap_b64(raw: bytes) -> str:
    text = base64.b64encode(raw).decode("ascii")
    lines = [text[i:i + 100] for i in range(0, len(text), 100)] or [""]
    return "(\n" + "\n".join(f"    {line!r}" for line in lines) + "\n)"


def read_exported_script(path: str) -> tuple[dict, "bytes | None", str]:
    """export が書き出したスクリプトから (定義, 埋め込みのテンプレート or None, テンプレートの相対パス) を読む（実行しない）。"""
    import ast
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "DEFINITION":
                call = node.value
                if isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant):
                    found[name] = json.loads(call.args[0].value)
            elif name in ("TEMPLATE_B64", "TEMPLATE_PATH") and isinstance(node.value, ast.Constant):
                found[name] = node.value.value
    if "DEFINITION" not in found:
        raise TemplateError(f"{path} は export が書き出したスクリプトではありません（DEFINITION が無い）")
    b64 = found.get("TEMPLATE_B64") or ""
    return found["DEFINITION"], (base64.b64decode(b64) if b64 else None), found.get("TEMPLATE_PATH", "")


def export_script(template: "str | bytes", definition: dict, output: str, embed: bool = False,
                  template_name: str | None = None) -> None:
    if isinstance(template, (bytes, bytearray)):
        if not embed:
            raise TemplateError("テンプレートのパスが無いため、埋め込みでしか書き出せません")
        raw, rel = bytes(template), template_name or ""
    else:
        raw = read_bytes(template)
        rel = os.path.relpath(os.path.abspath(template), os.path.dirname(os.path.abspath(output))).replace(os.sep, "/")
        template_name = os.path.basename(template)
    check_definition(raw, definition)
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        engine = f.read()
    engine = engine.split('\nif __name__ == "__main__":')[0]
    engine = engine.split("\n", 1)[1] if engine.startswith("#!") else engine
    engine = re.sub(r'^"""[\s\S]*?"""\n', "", engine, count=1)
    shape_lines = json.dumps(skeleton_data(definition), ensure_ascii=False, indent=2).splitlines()
    notes = value_notes(definition)
    shape = "\n".join("    " + line for line in shape_lines + ([""] + notes if notes else []))
    title = f"{os.path.splitext(template_name or 'template')[0]} の render スクリプト"
    note = ("テンプレートも埋め込み済み（--template で差し替えられる）。" if embed
            else f"テンプレート（{rel}）は、このスクリプトからの相対パスで読む。")
    header = STANDALONE_HEADER.format(pep723=PEP723, title=title, name=os.path.basename(output),
                                      shape=shape.replace('"""', "'''").replace("\\", "\\\\"), template_note=note)
    footer = (
        "\n\n# ---------------------------------------------------------------------------\n"
        "# 埋め込み（export が書き出した部分。手で直さず、定義を直して export をやり直す）\n"
        "# ---------------------------------------------------------------------------\n"
        f"ENGINE_VERSION = {ENGINE_VERSION}\n"
        f"DEFINITION = json.loads({json.dumps(definition, ensure_ascii=False, default=str)!r})\n"
        f"TEMPLATE_B64 = {_wrap_b64(raw) if embed else chr(34) * 2}\n"
        f"TEMPLATE_PATH = {rel!r}  # 埋め込まない場合の、このスクリプトからの相対パス\n"
        "\n\nif __name__ == \"__main__\":\n"
        "    raise SystemExit(standalone_main(DEFINITION, base64.b64decode(TEMPLATE_B64) or None, TEMPLATE_PATH, __doc__))\n"
    )
    with open(output, "w", encoding="utf-8") as f:
        f.write(header + engine.rstrip() + footer)
    os.chmod(output, 0o755)


def standalone_main(definition: dict, template_bytes: "bytes | None", template_path: str, doc: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=(doc or "").split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n\n".join((doc or "").split("\n\n")[1:]))
    parser.add_argument("--data", nargs="+", action="extend",
                        help="データ（.json / .yaml / .yml。- で標準入力）。複数のファイルかフォルダを渡すと 1 つにまとめる")
    parser.add_argument("-o", "--output", help="出力 .docx")
    parser.add_argument("--template", help="埋め込みのテンプレートの代わりに使う .docx（定義と構造が同じものに限る）")
    parser.add_argument("--allow-overflow", action="store_true", help="収まらない値があっても止めず、警告にする")
    parser.add_argument("--example-data", action="store_true", help="データの雛形（YAML）を標準出力に出す")
    parser.add_argument("--extract-template", metavar="PATH", help="埋め込みのテンプレートを書き出す")
    parser.add_argument("--extract-def", metavar="PATH", help="埋め込みの定義を書き出す（.json / .yaml）")
    args = parser.parse_args()
    try:
        if args.extract_def:
            dump_structured(definition, args.extract_def)
            print(f"書き出しました: {args.extract_def}")
            return 0
        if args.example_data:
            print(_yaml_dump(skeleton_data(definition)), end="")
            for n in value_notes(definition):
                print(f"# {n}")
            return 0
        here = os.path.dirname(os.path.abspath(sys.argv[0]))
        if args.template:
            template = read_bytes(args.template)
        elif template_bytes:
            template = template_bytes
        else:
            path = os.path.join(here, template_path)
            if not os.path.exists(path):
                raise TemplateError(f"テンプレートが見つかりません: {path}（--template で指定するか、export し直す）")
            template = read_bytes(path)
        if args.extract_template:
            if not template_bytes and not args.template:
                raise TemplateError("テンプレートは埋め込まれていません（元の .docx を使う）")
            with open(args.extract_template, "wb") as f:
                f.write(template)
            print(f"書き出しました: {args.extract_template}")
            return 0
        if not args.data or not args.output:
            parser.error("--data と -o が必要です")
        for w in render(template, definition, load_data(args.data, definition), args.output, args.allow_overflow):
            print(f"警告: {w}", file=sys.stderr)
        print(f"生成しました: {args.output}")
        return 0
    except TemplateError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"エラー: ファイルが見つかりません: {e.filename}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _template_arg(args, definition) -> str:
    """--template はそのまま、定義の template は定義ファイルのある場所からの相対パスとして読む。"""
    if getattr(args, "template", None):
        return args.template
    template = definition.get("template")
    if not template:
        raise TemplateError("--template か定義ファイルの template が必要です")
    def_path = getattr(args, "definition", None)
    if os.path.isabs(template) or not def_path or def_path == "-":
        return template
    return os.path.join(os.path.dirname(os.path.abspath(def_path)), template)


def cmd_analyze(args) -> int:
    definition = analyze(args.template)
    out = args.output or re.sub(r"\.docx?$", "", args.template, flags=re.I) + ".def.json"
    definition["template"] = os.path.relpath(os.path.abspath(args.template),
                                             os.path.dirname(os.path.abspath(out))).replace(os.sep, "/")
    dump_structured(definition, out)
    print(summarize(definition))
    print(f"\n定義ファイルの下書きを書きました: {out}")
    print("（? の項目と、流し込む欄・残す段落・繰り返す節の分け方をユーザーに確認してから、定義ファイルを直して確定する）")
    return 0


def cmd_inspect(args) -> int:
    facts = inspect_template(args.template)
    if args.json:
        json.dump(facts, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(format_facts(facts))
    return 0


def cmd_check(args) -> int:
    definition = load_structured(args.definition)
    for w in check_definition(_template_arg(args, definition), definition):
        print(f"警告: {w}")
    print("定義は問題ありません")
    notes = value_notes(definition)
    if notes:
        print("\nデータの書き方と、収まる量:")
        for n in notes:
            print(f"  {n}")
    return 0


def cmd_render(args) -> int:
    definition = load_structured(args.definition)
    data = load_data(args.data, definition)
    for w in render(_template_arg(args, definition), definition, data, args.output, args.allow_overflow):
        print(f"警告: {w}", file=sys.stderr)
    print(f"生成しました: {args.output}")
    return 0


def cmd_export(args) -> int:
    old_def, old_bytes, old_rel, old_dir = None, None, "", ""
    if args.from_script:
        old_def, old_bytes, old_rel = read_exported_script(args.from_script)
        old_dir = os.path.dirname(os.path.abspath(args.from_script))
    if args.definition:
        definition = load_structured(args.definition)
    elif old_def is not None:
        definition = old_def
    else:
        raise TemplateError("--def か --from-script が必要です")
    embed = args.embed
    given = bool(args.definition and definition.get("template") and os.path.exists(_template_arg(args, definition)))
    if args.template or given or (definition.get("template") and not args.from_script):
        template: "str | bytes" = _template_arg(args, definition)
    elif old_bytes is not None:
        template, embed = old_bytes, True
    elif old_rel:
        template = os.path.normpath(os.path.join(old_dir, old_rel))
    else:
        template = _template_arg(args, definition)
    export_script(template, definition, args.output, embed=embed,
                  template_name=os.path.basename(old_rel) if isinstance(template, bytes) else None)
    print(f"書き出しました: {args.output}")
    print(f"  使い方: uv run {os.path.basename(args.output)} --data data.yaml -o out.docx")
    return 0


def cmd_extract(args) -> int:
    definition = load_structured(args.definition)
    try:
        template = _template_arg(args, definition)
    except TemplateError:
        template = None
    data, notes = extract(args.source, definition, template if template and os.path.exists(template) else None)
    for n in notes:
        print(n, file=sys.stderr)
    if args.split:
        os.makedirs(args.split, exist_ok=True)
        for name, part in split_data(data, definition):
            path = os.path.join(args.split, f"{name}.{args.format}")
            dump_structured(part, path)
            print(f"取り出しました: {path}")
    elif args.output:
        dump_structured(data, args.output)
        print(f"取り出しました: {args.output}")
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    return 0


def add_subcommands(sub) -> None:
    i = sub.add_parser("inspect", help="テンプレートの事実（段落・見出し・表・書式・収まる量・仮値の疑い・来歴）を、判断用に出す")
    i.add_argument("template", help="テンプレート .docx")
    i.add_argument("--json", action="store_true", help="JSON で出す")
    i.set_defaults(func=cmd_inspect)
    a = sub.add_parser("analyze", help="テンプレートを解析して定義ファイル（下書き）を作る")
    a.add_argument("template", help="テンプレート .docx")
    a.add_argument("-o", "--output", help="定義ファイルの出力先（.json / .yaml。省略時は <テンプレート>.def.json）")
    a.set_defaults(func=cmd_analyze)
    c = sub.add_parser("check", help="定義の検査（整合・strict の漏れ）。雛形データで試しに再構成する")
    c.add_argument("--template", help="テンプレート .docx（省略時は定義ファイルの template）")
    c.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("render", help="テンプレート + 定義 + データから docx を再構成する")
    r.add_argument("--template", help="テンプレート .docx（省略時は定義ファイルの template）")
    r.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    r.add_argument("--data", required=True, nargs="+", action="extend",
                   help="データ（.json / .yaml。- で標準入力）。複数のファイルかフォルダ（中を名前順に）を渡すと 1 つにまとめる")
    r.add_argument("-o", "--output", required=True, help="出力 .docx")
    r.add_argument("--allow-overflow", action="store_true", help="収まらない値があっても止めず、警告にする")
    r.set_defaults(func=cmd_render)
    x = sub.add_parser("extract", help="記入済みの docx から、定義に沿ってデータを取り出す（render の逆）")
    x.add_argument("source", help="記入済みの .docx（テンプレートと同じ形の文書）")
    x.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    x.add_argument("--template", help="テンプレート .docx（省略時は定義ファイルの template。並びを突き合わせるのに使う）")
    x.add_argument("-o", "--output", help="データの出力先（.json / .yaml。省略時は標準出力に JSON）")
    x.add_argument("--split", metavar="DIR", help="データを、部分のまとまり（group。無ければ部分）ごとのファイルに分けて DIR に書く")
    x.add_argument("--format", choices=("yaml", "json"), default="yaml", help="--split で書く形式（既定 yaml）")
    x.set_defaults(func=cmd_extract)
    e = sub.add_parser("export", help="この文書専用の、単体で動く render スクリプトを書き出す")
    e.add_argument("--template", help="テンプレート .docx（省略時は定義ファイルの template）")
    e.add_argument("--def", dest="definition", help="確定した定義ファイル。--from-script と併用すると、その定義を置き換える")
    e.add_argument("--from-script", help="以前に export したスクリプト。定義・テンプレートを引き継いで、最新のエンジンで書き出し直す")
    e.add_argument("-o", "--output", required=True, help="書き出す .py")
    e.add_argument("--embed", action="store_true", help="テンプレートもスクリプトに埋め込む（既定は別ファイルを相対パスで参照）")
    e.set_defaults(func=cmd_export)


def main() -> int:
    parser = argparse.ArgumentParser(description="docx テンプレートへ内容を流し込む")
    add_subcommands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args()
    try:
        return args.func(args)
    except TemplateError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"エラー: ファイルが見つかりません: {e.filename}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
