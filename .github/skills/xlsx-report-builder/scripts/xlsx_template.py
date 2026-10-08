#!/usr/bin/env python3
"""xlsx テンプレートへデータを流し込む（書式・不変部分はテンプレートのまま保つ）。

2 つのコマンド:

    analyze  テンプレートを解析し、表構造の定義ファイル（JSON）の下書きと確認用の要約を出す
    render   テンプレート + 定義ファイル + データ から xlsx を再構成する

openpyxl で読み書きせず、xlsx（zip）内のシート XML の「可変の行」だけを書き換える。
スタイル・テーマ・図形・画像・マクロ・他シートなど、触らない部品は元のバイト列のままコピーする。
定義ファイルとデータの書式は references/template.md を参照。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import posixpath
import re
import sys
import zipfile
from copy import deepcopy
from typing import Any

from lxml import etree
from openpyxl.formula.tokenizer import Tokenizer, Token
from openpyxl.formula.translate import Translator
from openpyxl.utils import column_index_from_string, get_column_letter

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

DEF_VERSION = 1
CELL_RE = re.compile(r"^([A-Za-z]{1,3})(\d+)$")
REF_PART_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")
TOTAL_WORDS = ("合計", "小計", "総計", "計", "total", "sum", "subtotal")
TOTAL_FUNCS = re.compile(r"^(SUM|SUBTOTAL|AVERAGE|COUNT|COUNTA|MAX|MIN)\(", re.I)
BUILTIN_FORMATS = {1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?",
                   14: "m/d/yyyy", 15: "d-mmm-yy", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yyyy h:mm", 49: "@"}
BUILTIN_DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


class TemplateError(Exception):
    """定義ファイル・データ・テンプレートの不整合。"""


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


# ---------------------------------------------------------------------------
# パッケージ（zip）の読み書き
# ---------------------------------------------------------------------------

class Package:
    def __init__(self, path: "str | bytes"):
        with zipfile.ZipFile(io.BytesIO(path) if isinstance(path, (bytes, bytearray)) else path) as z:
            self.infos = z.infolist()
            self.data = {i.filename: z.read(i.filename) for i in self.infos}
        self.removed: set[str] = set()

    def xml(self, name: str) -> etree._Element:
        return etree.fromstring(self.data[name])

    def put_xml(self, name: str, root: etree._Element) -> None:
        self.data[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def save(self, path: str) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for info in self.infos:
                if info.filename in self.removed:
                    continue
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = info.external_attr
                z.writestr(zi, self.data[info.filename])

    def rels_of(self, part: str) -> dict[str, tuple[str, str]]:
        """part の rels を {rId: (type, 解決済みターゲットパス)} で返す。"""
        rels_name = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        if rels_name not in self.data:
            return {}
        out = {}
        for r in self.xml(rels_name):
            target = r.get("Target", "")
            if r.get("TargetMode") == "External":
                continue
            if target.startswith("/"):
                resolved = target.lstrip("/")
            else:
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
            out[r.get("Id")] = (r.get("Type", ""), resolved)
        return out

    def sheets(self) -> list[tuple[str, str]]:
        """[(シート名, シート part パス)] をブック順で返す。"""
        wb = self.xml("xl/workbook.xml")
        rels = self.rels_of("xl/workbook.xml")
        out = []
        for s in wb.find(q("sheets")):
            rid = s.get(f"{{{NS_R}}}id")
            if rid in rels:
                out.append((s.get("name"), rels[rid][1]))
        return out


def read_shared_strings(pkg: Package) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in pkg.data:
        return []
    out = []
    for si in pkg.xml(name):
        out.append("".join(t.text or "" for t in si.iter(q("t")) if t.getparent().tag != q("rPh")))
    return out


class Styles:
    """cellXfs の添字から、日付書式か・書式コード・太字/塗りの有無を引く。"""

    def __init__(self, pkg: Package):
        self.date: dict[int, bool] = {}
        self.code: dict[int, str] = {}
        self.emphasis: dict[int, bool] = {}
        self.desc: dict[int, str] = {}
        if "xl/styles.xml" not in pkg.data:
            return
        root = pkg.xml("xl/styles.xml")
        custom = {}
        nf = root.find(q("numFmts"))
        if nf is not None:
            custom = {int(n.get("numFmtId")): n.get("formatCode", "") for n in nf}
        fonts = root.find(q("fonts"))
        fills = root.find(q("fills"))
        xfs = root.find(q("cellXfs"))
        if xfs is None:
            return
        for i, xf in enumerate(xfs):
            fid = int(xf.get("numFmtId", "0"))
            code = custom.get(fid, "")
            self.code[i] = code or ("General" if fid == 0 else BUILTIN_FORMATS.get(fid, f"builtin:{fid}"))
            self.date[i] = fid in BUILTIN_DATE_IDS or bool(code and _is_date_code(code))
            bold = False
            if fonts is not None and int(xf.get("fontId", "0")) < len(fonts):
                bold = fonts[int(xf.get("fontId", "0"))].find(q("b")) is not None
            filled = False
            if fills is not None and int(xf.get("fillId", "0")) < len(fills):
                pf = fills[int(xf.get("fillId", "0"))].find(q("patternFill"))
                filled = pf is not None and pf.get("patternType") not in (None, "none")
            self.emphasis[i] = bold or filled
            parts = []
            if bold:
                parts.append("太字")
            if filled:
                fg = pf.find(q("fgColor")) if pf is not None else None
                color = (fg.get("rgb") or (f"theme{fg.get('theme')}" if fg.get("theme") else "")) if fg is not None else ""
                parts.append("塗り" + color[-6:] if color else "塗り")
            borders = root.find(q("borders"))
            bid = int(xf.get("borderId", "0"))
            if borders is not None and bid < len(borders) and any(
                    side.get("style") for side in borders[bid]):
                parts.append("罫線")
            if self.code[i] != "General":
                parts.append(self.code[i])
            self.desc[i] = "・".join(parts) or "標準"

    def describe(self, s: str | None) -> str:
        return self.desc.get(int(s or 0), "標準")

    def is_date(self, s: str | None) -> bool:
        return self.date.get(int(s or 0), False)

    def is_number(self, s: str | None) -> bool:
        """数値の書式（`#,##0`・`0.0%` など）のセルか。標準・文字列（@）・日付は含めない。"""
        code = self.code.get(int(s or 0), "General")
        return code not in ("General", "@") and not code.startswith("builtin:") and not self.is_date(s) \
            and bool(re.search(r"[0#]", re.sub(r"\[[^\]]*\]", "", _strip_literals(code))))


def _strip_literals(code: str) -> str:
    """書式コードから、書式の記号ではない文字（"文字列"・\\x・_x・*x）を除く。"""
    return re.sub(r'"[^"]*"|[\\_*].', "", code)


def _is_date_code(code: str) -> bool:
    # Excel の角括弧は色・条件・ロケール指定にも使われるが、
    # [h] / [m] / [s]（および繰り返し）は 24 時間を超える経過時間の書式。
    # それらだけは日付・時刻判定用に残し、他の角括弧指定は無視する。
    stripped = re.sub(
        r"\[([hms]+)\]",
        lambda m: m.group(1) if len(set(m.group(1).lower())) == 1 else "",
        _strip_literals(code),
        flags=re.I,
    )
    stripped = re.sub(r"\[[^\]]*\]", "", stripped)
    return bool(re.search(r"[ymdhs]", stripped, re.I))


# ---------------------------------------------------------------------------
# セル参照・数式の書き換え
# ---------------------------------------------------------------------------

def split_ref(ref: str) -> tuple[int, int]:
    m = CELL_RE.match(ref)
    if not m:
        raise TemplateError(f"セル参照が不正です: {ref!r}")
    return column_index_from_string(m.group(1).upper()), int(m.group(2))


def parse_row_spec(spec: Any) -> tuple[int, int]:
    """`20` / `"20"` / `"20:22"` / `"20-22"` / `[20, 22]` を (先頭, 末尾) にする。"""
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        a, b = int(spec[0]), int(spec[1])
    else:
        parts = re.split(r"[:\-]", str(spec))
        a, b = int(parts[0]), int(parts[-1])
    if a < 1 or b < a:
        raise TemplateError(f"行の指定が不正です: {spec!r}")
    return a, b


def parse_cell_spec(spec: str) -> list[tuple[int, int]]:
    """`B5` / `B5:D7` を (列, 行) の一覧にする。"""
    a, _, b = str(spec).partition(":")
    (c1, r1), (c2, r2) = split_ref(a), split_ref(b or a)
    return [(c, r) for r in range(min(r1, r2), max(r1, r2) + 1) for c in range(min(c1, c2), max(c1, c2) + 1)]


class RowMap:
    """可変表の行数の増減に応じた、元の行番号 → 新しい行番号 の対応。

    tables: dict(first, end, count, n) のリスト。first..end が元のサンプル行、n が出力行数。
    """

    def __init__(self, tables: list[dict]):
        self.tables = sorted(tables, key=lambda t: t["first"])

    def shift_before(self, r: int) -> int:
        return sum(t["n"] - t["count"] for t in self.tables if t["end"] < r)

    def new_last(self, t: dict) -> int:
        return t["first"] + self.shift_before(t["first"]) + t["n"] - 1

    def map(self, r: int, end_to_last: bool = True) -> int:
        if end_to_last:
            for t in self.tables:
                if r == t["end"]:
                    return self.new_last(t)
        return r + self.shift_before(r)

    def map_span(self, r1: int, r2: int) -> "tuple[int, int] | None":
        """範囲の行 r1..r2 を新しい行に写す。表の残らない行（縮めた・取り除いた分）は、表の新しい範囲に寄せる。
        範囲がすべて消えるなら None（縮めた表で、先頭と末尾が逆転した範囲を作らない）。"""
        def one(r: int, is_end: bool) -> int:
            for t in self.tables:
                if t["first"] <= r <= t["end"]:
                    new_first = t["first"] + self.shift_before(t["first"])
                    if is_end and (r == t["end"] or r - t["first"] >= t["n"]):
                        return new_first + t["n"] - 1
                    return new_first + min(r - t["first"], t["n"])
            return r + self.shift_before(r)
        a, b = one(r1, False), one(r2, True)
        return (a, b) if a <= b else None


class Rewriter:
    """数式・範囲文字列中の行参照を RowMap で書き換える。他シートの参照はそのシートの RowMap を使う。"""

    def __init__(self, this_sheet: str, maps: dict[str, RowMap]):
        self.this_sheet = this_sheet
        self.maps = maps

    def _split_sheet(self, operand: str) -> tuple[str | None, str, str]:
        if "!" not in operand:
            return None, "", operand
        prefix, _, rest = operand.rpartition("!")
        name = prefix
        if name.startswith("'") and name.endswith("'"):
            name = name[1:-1].replace("''", "'")
        return name, prefix + "!", rest

    def operand(self, operand: str, row_fn=None) -> str:
        """1 つの範囲オペランド（Sheet1!A1:B2 など）を書き換える。row_fn(rowmap, idx, last, abs, r) → 新行。"""
        sheet, prefix, rest = self._split_sheet(operand)
        target = sheet if sheet is not None else self.this_sheet
        rowmap = self.maps.get(target)
        if rowmap is None:
            return operand
        parts = rest.split(":")
        if not all(REF_PART_RE.match(p) for p in parts):
            return operand
        out = []
        for idx, p in enumerate(parts):
            m = REF_PART_RE.match(p)
            ca, col, ra, row = m.group(1), m.group(2), m.group(3), int(m.group(4))
            last = idx == len(parts) - 1
            if row_fn is None:
                new = rowmap.map(row, last)
            else:
                new = row_fn(rowmap, idx, last, bool(ra), row)
            out.append(f"{ca}{col}{ra}{new}")
        return prefix + ":".join(out)

    def formula(self, formula: str, row_fn=None) -> str:
        """`=` なしの数式文字列を書き換える。"""
        try:
            tok = Tokenizer("=" + formula)
        except Exception:
            return formula
        pieces = []
        for t in tok.items:
            if t.type == Token.OPERAND and t.subtype == Token.RANGE:
                pieces.append(self.operand(t.value, row_fn))
            else:
                pieces.append(t.value)
        return "".join(pieces)

    def sqref(self, sqref: str) -> str:
        """`A1:B2 D5` のような範囲リストを、範囲の先頭は据え置き・末尾はサンプル最終行 → 新最終行で書き換える。"""
        out = []
        for piece in sqref.split():
            parts = piece.split(":")
            if not all(REF_PART_RE.match(p.replace("$", "")) for p in parts):
                out.append(piece)
                continue
            rowmap = self.maps.get(self.this_sheet)
            if rowmap is None:
                out.append(piece)
                continue
            first = REF_PART_RE.match(parts[0].replace("$", ""))
            last = REF_PART_RE.match(parts[-1].replace("$", ""))
            span = rowmap.map_span(int(first.group(4)), int(last.group(4)))
            if span is None:
                continue   # 範囲の行がすべて消えた
            a, b = f"{first.group(2)}{span[0]}", f"{last.group(2)}{span[1]}"
            out.append(a if a == b else f"{a}:{b}")
        return " ".join(out)


# ---------------------------------------------------------------------------
# シート XML の読み取り補助
# ---------------------------------------------------------------------------

def cell_col(c) -> int:
    return split_ref(c.get("r"))[0]


def cell_value(c, sst: list[str]) -> Any:
    t = c.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(q("t")))
    v = c.find(q("v"))
    if v is None or v.text is None:
        return None
    if t == "s":
        return sst[int(v.text)]
    if t in ("str", "e"):
        return v.text
    if t == "b":
        return v.text == "1"
    try:
        n = float(v.text)
        return int(n) if n.is_integer() else n
    except ValueError:
        return v.text


def expand_shared_formulas(root: etree._Element) -> None:
    """共有数式を、各セルが自分の数式を持つ形に展開する（行の複製・移動のため）。"""
    masters: dict[str, tuple[str, str]] = {}
    for c in root.iter(q("c")):
        f = c.find(q("f"))
        if f is not None and f.get("t") == "shared" and f.text:
            masters[f.get("si")] = (f.text, c.get("r"))
    for c in root.iter(q("c")):
        f = c.find(q("f"))
        if f is None or f.get("t") != "shared":
            continue
        text, origin = masters[f.get("si")]
        if not f.text:
            f.text = Translator("=" + text, origin=origin).translate_formula(c.get("r"))[1:]
        for a in ("t", "si", "ref"):
            f.attrib.pop(a, None)


def sheet_rows(root: etree._Element) -> dict[int, etree._Element]:
    return {int(r.get("r")): r for r in root.find(q("sheetData"))}


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

def analyze(template: str) -> dict:
    pkg = Package(template)
    sst = read_shared_strings(pkg)
    styles = Styles(pkg)
    sheets_def = []
    for name, part in pkg.sheets():
        root = pkg.xml(part)
        expand_shared_formulas(root)
        sheets_def.append(_analyze_sheet(pkg, name, part, root, sst, styles))
    # 表のあるシートが 2 つ以上なら、データのキーをシート名にする（同じ items だと、どのシートにも同じ明細が入る）
    with_tables = [s for s in sheets_def if s["tables"]]
    if len(with_tables) > 1:
        for sd in with_tables:
            base = re.sub(r"[.\s]+", "_", sd["name"]).strip("_") or "sheet"
            for n, t in enumerate(sd["tables"], start=1):
                t["key"] = base if len(sd["tables"]) == 1 else f"{base}_{n}"
    # 固定セルのデータのキーは、ブック全体で重ならないようにする（別のシートの同じラベルに、黙って同じ値が入らない）
    used = {t["key"] for sd in sheets_def for t in sd["tables"]}
    for sd in sheets_def:
        for ref, key in sd["cells"].items():
            base, n = key, 2
            while key in used:
                key, n = f"{base}{n}", n + 1
            used.add(key)
            sd["cells"][ref] = key
    # テンプレートの値は、keep に挙げたもの（見出し・ラベル）以外を出力に残さない。来歴（作成者・コメントなど）も消す
    return {
        "version": DEF_VERSION,
        "template": posixpath.basename(template),
        "strict": True,
        "properties": {"scrub": True},
        "sheets": sheets_def,
    }


def _serial_to_iso(value) -> str:
    """日付の書式のセルの数値（Excel の通し番号）を、読める形（2025-01-01・2025-01-01 09:30）にする。"""
    d = dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value))
    return d.date().isoformat() if d.time() == dt.time() else d.isoformat(sep=" ", timespec="minutes")


def _grid(root, sst, styles: "Styles | None" = None):
    grid: dict[int, dict[int, dict]] = {}
    for row in root.find(q("sheetData")):
        r = int(row.get("r"))
        cols = {}
        for c in row:
            f = c.find(q("f"))
            value = cell_value(c, sst)
            if styles is not None and isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and styles.is_date(c.get("s")) and 0 < value < 2958466:
                value = _serial_to_iso(value)
            cols[cell_col(c)] = {
                "ref": c.get("r"),
                "s": c.get("s", "0"),
                "value": value,
                "formula": ("=" + f.text) if f is not None and f.text else None,
            }
        grid[r] = cols
    return grid


def _sig(cols: dict[int, dict]) -> tuple:
    return tuple(sorted((c, i["s"]) for c, i in cols.items()))


def _is_total_row(cols: dict[int, dict]) -> bool:
    for i in cols.values():
        if i["formula"] and TOTAL_FUNCS.match(i["formula"][1:]):
            return True
        v = i["value"]
        if isinstance(v, str) and v.strip().lower().replace(" ", "").replace("　", "") in TOTAL_WORDS:
            return True
    return False


def _find_tables(grid, styles) -> list[dict]:
    tables = []
    rows = sorted(grid)
    r_idx = 0
    while r_idx < len(rows):
        r = rows[r_idx]
        cols = grid[r]
        heads = {c: i for c, i in cols.items() if isinstance(i["value"], str) and i["value"].strip() and not i["formula"]}
        nxt = grid.get(r + 1)
        if len(heads) >= 2 and nxt is not None:
            span = sorted(heads)
            body_cols = [c for c in span if c in nxt]
            same_style = len({heads[c]["s"] for c in span}) == 1
            emph = all(styles.emphasis.get(int(heads[c]["s"]), False) for c in span)
            differs = any(nxt[c]["s"] != heads[c]["s"] for c in body_cols)
            # 2 段の見出し（上の段が「判定」「対象OS」のようなまとまり）なら、下の段を見出しにする
            nxt_heads = [c for c, i in nxt.items() if isinstance(i["value"], str) and i["value"].strip() and not i["formula"]]
            two_tier = len(nxt_heads) > len(span) and all(styles.emphasis.get(int(nxt[c]["s"]), False) for c in nxt_heads) \
                and (r + 2) in grid
            # 書式の強調が無い見出しは、隙間なく並ぶときだけ表と見る（「宛先 ○○ 承認」のような記入欄の行と取り違えない）
            no_gap = span[-1] - span[0] + 1 == len(span)
            if len(body_cols) * 2 >= len(span) and (emph or (same_style and len(span) >= 3 and differs and no_gap)) \
                    and not _is_total_row(nxt) and not two_tier:
                lo, hi = span[0], span[-1]
                body = []
                rr = r + 1
                while rr in grid:
                    g = grid[rr]
                    inside = [c for c in g if lo <= c <= hi]
                    if len(inside) * 2 < len(span) or _is_total_row(g):
                        break
                    body.append(rr)
                    rr += 1
                if body:
                    tables.append(_table_info(grid, r, body, lo, hi, rr, styles))
                    r_idx = next((k for k, x in enumerate(rows) if x >= rr), len(rows))
                    continue
        r_idx += 1
    return tables


def _table_info(grid, header_row, body, lo, hi, after, styles) -> dict:
    sigs = [_sig({c: i for c, i in grid[r].items() if lo <= c <= hi}) for r in body]
    period = next((p for p in (1, 2, 3) if p <= len(sigs) and all(sigs[k] == sigs[k % p] for k in range(len(sigs)))), None)
    total = after if after in grid and _is_total_row(grid[after]) else None
    return {
        "header_row": header_row, "body": body, "lo": lo, "hi": hi,
        "period": period, "total_row": total,
    }


def _analyze_sheet(pkg, name, part, root, sst, styles) -> dict:
    grid = _grid(root, sst, styles)
    found = _find_tables(grid, styles)
    notes: list[str] = []
    tables = []
    covered: set[int] = set()
    heads: set[int] = set()
    for n, t in enumerate(found, start=1):
        first, last = t["body"][0], t["body"][-1]
        confirm = ["データのキー（key）と各列の key が意図どおりか"]
        if t["period"] is None:
            pattern = [first]
            confirm.append("サンプル行の書式が揃っていない。複製元にする行（pattern）を選ぶ")
        else:
            pattern = t["body"][:t["period"]]
        if t["period"] and t["period"] > 1:
            confirm.append(f"サンプル行の書式が {t['period']} 行周期（縞模様）。pattern の行を順に繰り返す")
        cols_def = {}
        filled = _filled_rows(grid, t)
        for c in range(t["lo"], t["hi"] + 1):
            letter = get_column_letter(c)
            head = grid[t["header_row"]].get(c)
            samples = [grid[r][c] for r in t["body"] if c in grid[r]]
            fm = next((s for s in samples if s["formula"]), None)
            spec: dict[str, Any] = {"header": head["value"] if head else None}
            if fm:
                spec["formula"] = True
                spec["_sample"] = fm["formula"]
                for r in t["body"]:
                    cell = grid[r].get(c)
                    if cell and cell["formula"] and not _only_inside(cell["formula"], t, c):
                        confirm.append(f"{cell['ref']} の数式が表の外の行を相対参照している。固定するなら $ を付ける")
                        break
            elif not (head and head["value"] is not None) and all(_blankish(s["value"]) for s in samples):
                if not [s for s in samples if s["value"] is not None]:
                    continue   # 見出しも値も無い列は、体裁の余白。データにも定義にも入れない
                spec["keep"] = True   # 見出しが無く、× や - の既定の印だけの列も余白。印は書式としてそのまま残す
                spec["_sample"] = next(s["value"] for s in samples if s["value"] is not None)
                confirm.append(f"{letter}列は見出しが無く {spec['_sample']!r} だけなので、余白として残す（データには書かない）")
            elif head and isinstance(head["value"], str) and HUMAN_RE.search(head["value"]):
                spec["clear"] = True   # 押印・署名など、人が書き込む欄。データに入れず、空欄で出す
                confirm.append(f"{letter}列「{head['value']}」は人が書き込む欄として、データに入れず空欄で出す")
            elif _constant(samples, len(t["body"])):
                spec["keep"] = True    # どのサンプル行も同じ値（円・式など）。毎回同じなので、データに書かない
                spec["_sample"] = samples[0]["value"]
                confirm.append(f"{letter}列はどの行も {samples[0]['value']!r} の定数として残す（行ごとに変わるなら key にする）")
            elif len(samples) == len(t["body"]) and [s["value"] for s in samples] == list(range(1, len(samples) + 1)):
                spec["key"] = "$index"  # サンプルが 1, 2, 3 … の連番なら、連番の列
                spec["_sample"] = 1
            else:
                spec["key"] = _unique_key(spec["header"], letter, cols_def)
                # 例の値は、既定値（連番・× など）だけの空行を除いた行から取る
                spec["_sample"] = next((grid[r][c]["value"] for r in filled
                                        if c in grid[r] and not _blankish(grid[r][c]["value"])), None)
            if samples:
                spec["_format"] = styles.code.get(int(samples[0]["s"]), "General")
            cols_def[letter] = spec
        _readable_columns(cols_def, t, grid, _merge_origins(root), confirm)
        empty = [r for r in t["body"] if r not in filled]
        if empty and len(filled) < len(t["body"]):
            confirm.append(f"{', '.join(map(str, empty))} 行目は既定値（連番・× など）だけの空行。"
                           "記入例の行数に数えるだけで、データには書かない")
        covered.update(t["body"])
        heads.add(t["header_row"])
        above = grid.get(t["header_row"] - 1, {})
        if above and all(t["lo"] <= c <= t["hi"] for c, i in above.items() if i["value"] is not None):
            heads.add(t["header_row"] - 1)   # 2 段の見出しの上の段（「判定」「対象OS」のようなまとまり）
        tables.append({
            "id": f"table{n}",
            "header_row": t["header_row"],
            "first_row": first,
            "sample_rows": len(t["body"]),
            "pattern": pattern,
            "key": "items" if n == 1 else f"items{n}",
            "columns": cols_def,
            "_total_row": t["total_row"],
            "needs_confirm": confirm,
        })
    cells, keep, samples, clear = _fixed_cells(grid, covered, heads, root)
    notes += _sheet_warnings(pkg, part, root, tables)
    out = {"name": name, "tables": tables, "cells": cells, "keep": keep, "_cell_samples": samples, "_notes": notes}
    if clear:
        out["clear"] = clear
    return out


def _only_inside(formula: str, t: dict, col: int) -> bool:
    lo, hi = t["body"][0], t["body"][-1]
    try:
        tok = Tokenizer(formula)
    except Exception:
        return True
    for x in tok.items:
        if x.type == Token.OPERAND and x.subtype == Token.RANGE and "!" not in x.value:
            for p in x.value.split(":"):
                m = REF_PART_RE.match(p)
                if m and not m.group(3) and not (lo <= int(m.group(4)) <= hi) and int(m.group(4)) != lo - 1:
                    return False
    return True


def _unique_key(header, letter: str, cols_def: dict) -> str:
    base = (header or "").strip() or f"col_{letter}"
    used = {c.get("key") for c in cols_def.values()}
    key, i = base, 2
    while key in used:
        key, i = f"{base}{i}", i + 1
    return key


# 人が書き込む欄（押印・署名など）の見出し・ラベル。データに入れず、空欄のまま出す
HUMAN_RE = re.compile(r"(印$|押印|捺印|検印|署名|サイン|自署|承認者?$|決裁|確認者|受付者?$|手書き|記入欄)")


def _constant(samples: list[dict], n_rows: int) -> bool:
    """どのサンプル行も同じ文字（印・仮の値を除く）なら、行ごとに変わらない定数の列。"""
    vals = [s["value"] for s in samples]
    return n_rows >= 2 and len(vals) == n_rows and isinstance(vals[0], str) and vals[0].strip() != "" \
        and len(set(vals)) == 1 and vals[0].strip() not in MARK_ON + MARK_OFF and not PLACEHOLDER_RE.search(vals[0])


def _blankish(value) -> bool:
    """空欄、または × や - の「選ばれていない」既定の印。データとしては空と同じ。"""
    return value is None or isinstance(value, str) and (not value.strip() or value.strip() in MARK_OFF)


def _filled_rows(grid, t: dict) -> list[int]:
    """表のサンプル行のうち、既定値を除いても値がある行。

    連番の列・数式の列・どの行も同じ値の列（円・式など）と、× や - の印は、どの行にも最初から入っている既定値。
    それらを除いてから空かを判定する（連番と × だけの行は、記入例ではなく空の行）。
    すべての行が空なら、すべての行を返す。
    """
    body = t["body"]
    skip = set()
    for c in range(t["lo"], t["hi"] + 1):
        samples = [grid[r][c] for r in body if c in grid[r]]
        vals = [s["value"] for s in samples]
        if any(s["formula"] for s in samples) or _constant(samples, len(body)) \
                or len(vals) == len(body) and vals == list(range(1, len(body) + 1)):
            skip.add(c)
    filled = [r for r in body
              if any(not _blankish(i["value"]) for c, i in grid[r].items() if t["lo"] <= c <= t["hi"] and c not in skip)]
    return filled or list(body)


def _merge_origins(root) -> dict[tuple[int, int], tuple[int, int]]:
    """結合範囲の各セル（列, 行）→ 左上のセル。"""
    origin: dict[tuple[int, int], tuple[int, int]] = {}
    for mc in root.iter(q("mergeCell")):
        (c1, r1), (c2, r2) = (split_ref(p) for p in (mc.get("ref").split(":") * 2)[:2])
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                origin[(c, r)] = (c1, r1)
    return origin


MARK_ON = ("○", "◯", "〇", "●", "◎", "✓", "✔", "レ", "☑", "■")
MARK_OFF = ("×", "✕", "✖", "☐", "□", "-", "－", "ー", "―")
DASHES = ("-", "－", "ー", "―")      # 「なし」の意味の横棒。印の列とは限らない（備考の - など）
MARK_PAIR = {"☐": "☑", "□": "■"}  # × や □ だけの列で、選ばれたときの印
DATE_HEADS = {"年": "year", "月": "month", "日": "day", "時": "hour", "分": "minute"}


def _readable_columns(cols_def: dict, t: dict, grid, origin, confirm: list) -> None:
    """表の見た目に頼る列（○× の印・年月日に分かれた日付）を、データで意味が分かる書き方にする。

    - ○ の列が並ぶ → 見出しを値にした択一（`判定: 合格`）か、複数選択（`対象: [Windows, Linux]`）
    - ○ の列が 1 つ → true / false
    - 年・月・日の列が並ぶ → 1 つの日付（`2026-10-08`）
    """
    def text(r, c):
        v = grid.get(r, {}).get(c, {}).get("value")
        return str(v).strip() if v is not None and str(v).strip() else None

    def group_head(cols) -> "str | None":   # 見出しの 1 行上で、列のまとまりにかかる見出し（結合セル）
        above = {origin.get((c, t["header_row"] - 1), (c, t["header_row"] - 1)) for c in cols}
        if len(above) != 1:
            return None
        v = text(*reversed(next(iter(above))))
        return re.sub(r"[.\s]+", "_", v) if v else None

    def runs(cols):   # 隣り合う列のまとまり。見出しの上の段の結合セルが変わるところで切る
        out = []
        above = lambda c: origin.get((c, t["header_row"] - 1), (c, t["header_row"] - 1))
        for c in sorted(cols):
            if out and out[-1][-1] == c - 1 and (above(c) == above(c - 1) or above(c)[0] == c and not text(*reversed(above(c)))
                                                 and above(c - 1)[0] == c - 1 and not text(*reversed(above(c - 1)))):
                out[-1].append(c)
            else:
                out.append([c])
        return out

    used = {spec.get("key") for spec in cols_def.values()}

    def fresh(base):
        key, n = base, 2
        while key in used:
            key, n = f"{base}{n}", n + 1
        used.add(key)
        return key

    plain = {column_index_from_string(L): s for L, s in cols_def.items()
             if not s.get("formula") and s.get("key") not in (None, "$index")}
    marks, blank = {}, set()
    for c in plain:
        vals = [text(r, c) for r in t["body"]]
        hit = [v for v in vals if v]
        if hit and all(v in MARK_ON + MARK_OFF for v in hit) and (any(v in MARK_ON for v in hit)
                                                                    or not set(hit) & set(DASHES)):
            marks[c] = vals   # ○ がある列と、× や □ だけの列（どれも選ばれていない記入例）
        elif all(v in DASHES for v in hit):
            blank.add(c)   # 空か - だけの列は、空と同じ。印の列と同じまとまりの中なら選択肢にする
            plain[c]["_sample"] = None
    groups = []
    for run in runs(set(marks) | blank):
        if group_head(run):
            groups += [run] if any(c in marks for c in run) else []
        else:   # まとまりの見出しが無ければ、印の列だけが隣り合う範囲
            groups += runs([c for c in run if c in marks])
    for run in groups:
        for c in run:
            marks.setdefault(c, [None] * len(t["body"]))
        letters = [get_column_letter(c) for c in run]
        cells = [v for c in run for v in marks[c] if v]
        off = max((m for m in MARK_OFF if m in cells), key=cells.count, default=None)
        on = max((m for m in MARK_ON if m in cells), key=cells.count, default=MARK_PAIR.get(off, "○"))
        heads = [str(plain[c].get("header") or get_column_letter(c)) for c in run]
        if len(run) == 1:
            spec = plain[run[0]]
            spec["map"] = {True: on, False: off}
            spec["_sample"] = marks[run[0]][0] == on if marks[run[0]][0] else None
            confirm.append(f"{letters[0]}列の {on} を、データでは true / false で書く")
            continue
        picked = [[h for c, h in zip(run, heads) if marks[c][i] == on] for i in range(len(t["body"]))]
        multi = any(len(p) > 1 for p in picked)
        for c in run:
            used.discard(plain[c]["key"])
        key = fresh(group_head(run) or "/".join(heads))
        for c, h in zip(run, heads):
            spec = plain[c]
            spec.update({"key": key, "when": h, "mark": on})
            if off:
                spec["unmark"] = off
            spec["_sample"] = picked[0] if multi else (picked[0][0] if picked[0] else None)
        confirm.append(f"{letters[0]}〜{letters[-1]}列の {on} を、データでは {key}: "
                       + (f"[{', '.join(heads[:2])}] のような見出しの配列（複数選択）" if multi else f"{heads[0]} のような見出しの 1 つ（択一）")
                       + "で書く")
    plain.update({column_index_from_string(L): s for L, s in cols_def.items() if s.get("key") == "$index"})
    dates = [c for c in plain if str(plain[c].get("header") or "").strip() in DATE_HEADS]
    for run in runs(dates):
        if len(run) < 2:
            continue
        parts = [DATE_HEADS[str(plain[c]["header"]).strip()] for c in run]
        if len(set(parts)) < len(parts):
            continue
        for c in run:
            used.discard(plain[c]["key"])
        key = fresh(group_head(run) or "日付")
        got = dict(zip(parts, (text(t["body"][0], c) for c in run)))
        try:
            sample = dt.datetime(int(got.get("year", 2000)), int(got.get("month", 1)), int(got.get("day", 1)),
                                 int(got.get("hour", 0)), int(got.get("minute", 0)))
            sample = sample.date().isoformat() if not {"hour", "minute"} & set(parts) else sample.isoformat(sep=" ", timespec="minutes")
        except (TypeError, ValueError):
            sample = None
        for c, part in zip(run, parts):
            plain[c].update({"key": key, "part": part, "_sample": sample})
        confirm.append(f"{get_column_letter(run[0])}〜{get_column_letter(run[-1])}列の"
                       f"{'・'.join(str(plain[c]['header']).strip() for c in run)}を、データでは {key}: 2026-10-08 のような 1 つの日付で書く")


def _fixed_cells(grid, sample_rows: set[int], header_rows: set[int], root) -> tuple[dict, list[str], dict, list[str]]:
    """表のサンプル行の外のセルを、ラベル（keep）と、流し込む欄（cells）に分ける。

    ラベルの右隣のセルは、値があってもなくても流し込む欄にする（前の文書の値を持ち越さない・空欄の記入枠も落とさない）。
    ラベルの無い数値・日付も、流し込む欄にする。それ以外の文字（タイトル・見出し・注記）がラベル（keep）。
    ラベルと同じ書式の空欄（見出しの帯の続き）は、記入枠と見なさない。
    押印・署名など人が書き込む欄は、データに入れない（前の値があれば clear で消す）。
    """
    origin = _merge_origins(root)
    cells: dict[str, str] = {}
    keep: list[str] = []
    clear: list[str] = []
    samples: dict[str, dict] = {}
    for r in sorted(grid):
        if r in sample_rows:
            continue
        labels: dict[int, str] = {}   # この行のラベルの列 → 文字
        for c in sorted(grid[r]):
            i = grid[r][c]
            if origin.get((c, r), (c, r)) != (c, r) or i["formula"]:
                continue   # 結合の左上以外のセル・数式は、どちらにもしない
            lc, lr = origin.get((c - 1, r), (c - 1, r))
            label = labels.get(lc) if lr == r and r not in header_rows else None
            value = i["value"]
            if value is None and label is not None and grid[r][lc]["s"] == i["s"]:
                continue   # ラベルと同じ書式の空欄は、行の塗りの続き。記入枠ではない
            if r in header_rows:
                if value is not None:
                    keep.append(i["ref"])
                continue
            if label is not None and HUMAN_RE.search(label.strip(" :：")):
                if value is not None:
                    clear.append(i["ref"])   # 人が書き込む欄の前の値は消すだけ。データには入れない
                continue
            if label is not None or (value is not None and not isinstance(value, str)):
                base = re.sub(r"[.\s]+", "_", (label or "").strip(" :：")).strip("_") or i["ref"]
                key, n = base, 2
                while key in cells.values():
                    key, n = f"{base}{n}", n + 1
                cells[i["ref"]] = key
                samples[i["ref"]] = {"label": label, "value": value}
            elif value is not None:
                labels[c] = value
                keep.append(i["ref"])
    return cells, compress_cells(keep) if keep else [], samples, compress_cells(clear) if clear else []


def _sheet_warnings(pkg, part, root, tables) -> list[str]:
    w = []
    spans = [(t["first_row"], t["first_row"] + t["sample_rows"] - 1) for t in tables]

    def inside(ref_text: str) -> bool:
        for piece in ref_text.split():
            for p in piece.split(":"):
                m = REF_PART_RE.match(p.replace("$", ""))
                if m and any(a <= int(m.group(4)) <= b for a, b in spans):
                    return True
        return False
    for mc in root.iter(q("mergeCell")):
        if inside(mc.get("ref")):
            r1, r2 = (split_ref(p)[1] for p in (mc.get("ref").split(":") * 2)[:2])
            if r1 != r2:
                w.append(f"サンプル行内の複数行にまたがる結合セル {mc.get('ref')} は、1 件が複数行（block_rows）で"
                         "その 1 件の中に収まれば各件に複製される。収まらなければ取り除かれる")
            else:
                w.append(f"サンプル行内の結合セル {mc.get('ref')} は、各行に複製される")
    for cf in root.iter(q("conditionalFormatting")):
        if inside(cf.get("sqref", "")):
            w.append(f"条件付き書式 {cf.get('sqref')} は、表の行数に合わせて範囲が伸びる")
    for dv in root.iter(q("dataValidation")):
        if inside(dv.get("sqref", "")):
            w.append(f"入力規則 {dv.get('sqref')} は、表の行数に合わせて範囲が伸びる")
    rels = pkg.rels_of(part)
    for _, (typ, target) in rels.items():
        if typ.endswith("/comments"):
            w.append("コメント（メモ）は行がずれても位置が追従しない場合がある。確認すること")
        if typ.endswith("/table"):
            w.append(f"Excel のテーブル（{target}）は、範囲を表の行数に合わせて更新する")
    return w


def summarize(definition: dict) -> str:
    """ユーザー確認用の要約テキスト。"""
    lines = []
    for s in definition["sheets"]:
        lines.append(f"■ シート「{s['name']}」")
        if not s["tables"]:
            lines.append("  可変の表は見つかりませんでした（固定セルだけのシート）")
        for t in s["tables"]:
            end = t["first_row"] + t["sample_rows"] - 1
            lines.append(f"  [{t['id']}] 見出し {t['header_row']} 行 / サンプル {t['first_row']}-{end} 行"
                         f"（{t['sample_rows']} 行）/ 繰り返し元 {t['pattern']} / データのキー: {t['key']}")
            for letter, c in t["columns"].items():
                kind = f"数式 {c['_sample']}" if c.get("formula") else f"key={c.get('key')}  例: {c.get('_sample')!r}"
                if c.get("keep"):
                    kind = f"定数として残す（データに書かない）  値: {c.get('_sample')!r}"
                elif c.get("clear"):
                    kind = "人が書き込む欄（データに書かず、空欄で出す）"
                if "when" in c:
                    kind += f"（{c['when']!r} を含むとき {c.get('mark', '○')}）"
                elif "map" in c:
                    pairs = [f"{k}→{'空欄' if v is None else v}" for k, v in c["map"].items()]
                    kind += f"（{', '.join(pairs)}）"
                elif "part" in c:
                    kind += f"（日付の {c['part']}）"
                lines.append(f"      {letter}列 「{c.get('header')}」 → {kind}  [{c.get('_format', '')}]")
            if t["_total_row"]:
                lines.append(f"      合計行: {t['_total_row']} 行（表の下へずらし、SUM の範囲は表に合わせて伸ばす）")
            for n in t["needs_confirm"]:
                lines.append(f"      ? {n}")
        if s["cells"]:
            lines.append("  流し込む欄（cells。テンプレートの値は残さない。データで null なら空欄になる）:")
            for ref, key in s["cells"].items():
                v = s["_cell_samples"].get(ref, {}).get("value")
                lines.append(f"      {ref} → key={key}  " + (f"今の値: {v!r}" if v is not None else "（空欄の記入枠）"))
        if s["keep"]:
            lines.append(f"  残す（keep。見出し・ラベル・固定の文面）: {', '.join(s['keep'])}")
        if s.get("clear"):
            lines.append(f"  空にする（clear。人が書き込む欄の前の値）: {', '.join(s['clear'])}")
        for n in s["_notes"]:
            lines.append(f"  ! {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# inspect（テンプレートの事実だけを、判断する側（LLM・人）が読める形で出す）
# ---------------------------------------------------------------------------

# 「仮」は 1 文字だけでは見ない（仮払金・仮受消費税のような、帳票の見出しを仮の値と取り違えないため）
PLACEHOLDER_RE = re.compile(r"(〇〇|○○|●●|◯◯|△△|□□|＊＊|\*\*|(?<![A-Za-z])x{2,}(?![A-Za-z])|サンプル|ダミー|記入例|"
                            r"^仮$|[（(]仮[）)]|仮(?:の|入力|置き|名|データ)|例[:：)）]|sample|dummy|example|yyyy|"
                            r"\b0{4}[-/]0{2}[-/]0{2}\b|\bTBD\b)", re.I)
NOTE_RE = re.compile(r"^\s*(※|＊|\*|注[:：）)]|備考[:：]|Note[:：])")   # 列の見出しの「備考」は注記ではない


def _style_classes(styles: Styles) -> dict[str, str]:
    """書式の説明が同じスタイル ID を、同じ種類（S1, S2, ...）にまとめる。"""
    out: dict[str, str] = {}
    labels: dict[str, str] = {}
    for sid in sorted(styles.desc):
        d = styles.desc[sid]
        labels.setdefault(d, f"S{len(labels)}")
        out[str(sid)] = labels[d]
    return out


def inspect_template(template: "str | bytes") -> dict:
    """判断の材料になる事実（値・数式・書式の種類・結合・仮値の疑い・自動検出の下書き）を集める。"""
    pkg = Package(template)
    sst = read_shared_strings(pkg)
    styles = Styles(pkg)
    classes = _style_classes(styles)
    legend = {label: desc for desc, label in {styles.desc[i]: classes[str(i)] for i in styles.desc}.items()}
    sheets = []
    for name, part in pkg.sheets():
        root = pkg.xml(part)
        expand_shared_formulas(root)
        grid = _grid(root, sst, styles)
        rows = []
        for r in sorted(grid):
            cells, blanks = [], []
            for c in sorted(grid[r]):
                i = grid[r][c]
                cls = classes.get(i["s"], "S0")
                if i["value"] is None and not i["formula"]:
                    blanks.append(c)
                    continue
                item = {"ref": i["ref"], "style": cls}
                if i["formula"]:
                    item["formula"] = i["formula"]
                else:
                    item["value"] = i["value"]
                    if isinstance(i["value"], str):
                        if PLACEHOLDER_RE.search(i["value"]):
                            item["hint"] = "仮の値の疑い"
                        elif NOTE_RE.match(i["value"]):
                            item["hint"] = "注記の疑い"
                cells.append(item)
            rows.append({"row": r, "cells": cells,
                         "styled_blank": f"{get_column_letter(min(blanks))}-{get_column_letter(max(blanks))}" if blanks else None,
                         "shape": [(c, classes.get(grid[r][c]["s"], "S0")) for c in sorted(grid[r])]})
        draft = _analyze_sheet(pkg, name, part, root, sst, styles)
        sheets.append({
            "name": name,
            "merges": [m.get("ref") for m in root.iter(q("mergeCell"))],
            "conditional_formats": [c.get("sqref") for c in root.iter(q("conditionalFormatting"))],
            "validations": [d.get("sqref") for d in root.iter(q("dataValidation"))],
            "rows": rows,
            "same_shape_runs": _runs(rows),
            "auto_detected_tables": [{k: t[k] for k in ("id", "header_row", "first_row", "sample_rows", "pattern")}
                                     for t in draft["tables"]],
            "warnings": draft["_notes"],
        })
    return {"style_legend": legend, "provenance": provenance(pkg), "sheets": sheets}


def _runs(rows: list[dict]) -> list[dict]:
    """列の並びと書式の種類が同じ行が続く範囲（繰り返しの候補）。周期 2〜3 の縞模様も拾う。"""
    out = []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1]["row"] == rows[j]["row"] + 1 and rows[j + 1]["shape"] == rows[i]["shape"]:
            j += 1
        if j > i:
            out.append({"rows": f"{rows[i]['row']}-{rows[j]['row']}", "period": 1})
            i = j + 1
            continue
        for p in (2, 3):
            k = i
            while k + p < len(rows) + 0 and rows[k + p]["row"] == rows[k]["row"] + p and rows[k + p]["shape"] == rows[k]["shape"] \
                    and all(rows[k + m + 1]["row"] == rows[k + m]["row"] + 1 for m in range(p)):
                k += 1
            if k - i >= p:
                out.append({"rows": f"{rows[i]['row']}-{rows[k + p - 1]['row']}", "period": p})
                i = k + p
                break
        else:
            i += 1
    return out


FOLD_RUN = 6   # 同じ書式の行がこれより長く続くと、テキストでは先頭 3 行と末尾 1 行だけ見せる


def _folded_rows(sh: dict) -> dict[int, int]:
    """畳む行 → その範囲で畳んだ行数（最初の 1 行にだけ数を付け、残りは 0）。"""
    out: dict[int, int] = {}
    for run in sh["same_shape_runs"]:
        a, b = (int(x) for x in run["rows"].split("-"))
        if b - a + 1 > FOLD_RUN:
            # 仮の値・注記の疑いがある行は畳まない（流用のとき、持ち越しを見落とさない）
            hidden = [r["row"] for r in sh["rows"] if a + 3 <= r["row"] < b and not any("hint" in c for c in r["cells"])]
            for k, r in enumerate(hidden):
                out[r] = len(hidden) if k == 0 else 0
    return out


def format_facts(facts: dict, fold: bool = True) -> str:
    L = ["書式の種類（S0〜: 同じ見た目は同じ番号）:"]
    L += [f"  {k} = {v}" for k, v in sorted(facts["style_legend"].items(), key=lambda kv: int(kv[0][1:]))]
    if facts.get("provenance"):
        L.append("\n来歴・持ち越しの注意（analyze の下書きの properties.scrub で消える。外部リンク・非表示・変更履歴は残るので扱いを決める）:")
        L += [f"  - {p}" for p in facts["provenance"]]
    for sh in facts["sheets"]:
        L.append(f"\n■ シート「{sh['name']}」")
        for key, label in (("merges", "結合"), ("conditional_formats", "条件付き書式"), ("validations", "入力規則")):
            if sh[key]:
                L.append(f"  {label}: {' '.join(sh[key])}")
        L.append("  行（値/数式 [書式の種類]）:")
        folded = _folded_rows(sh) if fold else {}
        for row in sh["rows"]:
            if row["row"] in folded:
                if folded[row["row"]]:
                    L.append(f"         …（同じ書式の行 {folded[row['row']]} 行を省略。--all ですべて出す）")
                continue
            parts = []
            for c in row["cells"]:
                body = c["formula"] if "formula" in c else repr(c["value"])
                parts.append(f"{c['ref']}: {body} [{c['style']}]" + (f" ⚠{c['hint']}" if "hint" in c else ""))
            if row["styled_blank"]:
                parts.append(f"（値なし・書式のみ {row['styled_blank']}）")
            L.append(f"    {row['row']:>3}: " + "  ".join(parts) if parts else f"    {row['row']:>3}: （値なし）")
        if sh["same_shape_runs"]:
            L.append("  同じ書式の行が続く範囲（繰り返しの候補）: " + ", ".join(
                f"{r['rows']}（{r['period']} 行周期）" for r in sh["same_shape_runs"]))
        for t in sh["auto_detected_tables"]:
            end = t["first_row"] + t["sample_rows"] - 1
            L.append(f"  自動検出の下書き: 見出し {t['header_row']} 行 / サンプル {t['first_row']}-{end} 行 / 繰り返し元 {t['pattern']}（確定ではない）")
        for w in sh["warnings"]:
            L.append(f"  ! {w}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(path)
    return cur


def _excel_serial(value: str) -> float | None:
    for parser in (dt.datetime.fromisoformat, dt.date.fromisoformat):
        try:
            d = parser(value)
        except ValueError:
            continue
        if isinstance(d, dt.datetime):
            delta = d - dt.datetime(1899, 12, 30)
            return delta.days + delta.seconds / 86400
        return (d - dt.date(1899, 12, 30)).days
    return None


ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
MAX_CELL_TEXT = 32767  # Excel の 1 セルの文字数の上限
NUMERIC_TEXT_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")


def set_value(c, value: Any, styles: Styles, replace_formula: bool = False) -> None:
    """セルの値を置き換える。replace_formula なら、数式もデータの値で置き換える（流し込む欄）。"""
    for child in list(c):
        if child.tag != q("f") or replace_formula:
            c.remove(child)
    c.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, bool):
        c.set("t", "b")
        etree.SubElement(c, q("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise TemplateError(f"{c.get('r')} に入れる数値 {value!r} は Excel に保存できません")
        etree.SubElement(c, q("v")).text = repr(value)
    else:
        text = str(value)
        if styles.is_number(c.get("s")) and NUMERIC_TEXT_RE.match(text.strip()):
            # CSV などから来た `"1,200"` を文字のまま入れると、SUM が数えず合計が黙って狂う
            number = float(text.strip().replace(",", ""))
            etree.SubElement(c, q("v")).text = repr(int(number) if number.is_integer() and "." not in text else number)
            return
        if ILLEGAL_XML_RE.search(text):
            raise TemplateError(f"{c.get('r')} に入れる文字列に、Excel に保存できない制御文字があります: {text[:40]!r}")
        if len(text) > MAX_CELL_TEXT:
            raise TemplateError(f"{c.get('r')} に入れる文字列が {len(text)} 文字あり、Excel の上限 {MAX_CELL_TEXT} 文字を超えています")
        serial = _excel_serial(text) if styles.is_date(c.get("s")) else None
        if serial is not None:
            etree.SubElement(c, q("v")).text = repr(serial)
        else:
            c.set("t", "inlineStr")
            is_ = etree.SubElement(c, q("is"))
            t = etree.SubElement(is_, q("t"))
            t.text = text
            t.set(XML_SPACE, "preserve")


def _get_or_make_cell(row, col: int, r: int):
    ref = f"{get_column_letter(col)}{r}"
    for c in row:
        cc = cell_col(c)
        if cc == col:
            return c
        if cc > col:
            new = etree.Element(q("c"), r=ref)
            c.addprevious(new)
            return new
    return etree.SubElement(row, q("c"), r=ref)


def _set_row_number(row, r: int) -> None:
    row.set("r", str(r))
    row.attrib.pop("spans", None)
    for c in row:
        col = re.match(r"[A-Za-z]+", c.get("r")).group(0)
        c.set("r", f"{col}{r}")


def _strip_cached(row) -> None:
    for c in row:
        if c.find(q("f")) is not None:
            v = c.find(q("v"))
            if v is not None:
                c.remove(v)
            c.attrib.pop("t", None) if c.get("t") in ("str", "e", "b", "n") else None


def render(template: "str | bytes", definition: dict, data: dict, output: str) -> list[str]:
    """テンプレートへ流し込んで output に書く。警告メッセージのリストを返す。"""
    if definition.get("version") != DEF_VERSION:
        raise TemplateError(f"定義ファイルの version が未対応です: {definition.get('version')!r}")
    pkg = Package(template)
    sst = read_shared_strings(pkg)
    styles = Styles(pkg)
    sheet_parts = dict(pkg.sheets())
    warnings: list[str] = [f"来歴: {p}" for p in provenance(pkg)]
    leftovers: dict[str, set] = {}

    # --- 1. シートごとの表の出力行数を決め、RowMap を作る -----------------------
    plans: dict[str, dict] = {}
    maps: dict[str, RowMap] = {}
    for sd in definition["sheets"]:
        name = sd["name"]
        if name not in sheet_parts:
            raise TemplateError(f"テンプレートにシート「{name}」がありません")
        tbls = []
        for t in sd.get("tables", []):
            try:
                rows = dig(data, t["key"])
            except KeyError:
                raise TemplateError(f"データに {t['key']!r} がありません（シート「{name}」表 {t['id']}）")
            if not isinstance(rows, list):
                raise TemplateError(f"{t['key']!r} は配列である必要があります")
            first, count = int(t["first_row"]), int(t["sample_rows"])
            block = int(t.get("block_rows", 1))
            if block < 1 or count % block:
                raise TemplateError(f"sample_rows ({count}) は block_rows ({block}) の倍数にしてください（表 {t['id']}）")
            pattern = [int(p) for p in t.get("pattern") or [first]]
            for p in pattern:
                if not first <= p <= first + count - block:
                    raise TemplateError(f"pattern の行 {p} がサンプル行 {first}-{first + count - block}（ブロックの先頭になれる範囲）の外です")
            warnings += _missing_key_warnings(t, block, rows, name)
            tbls.append({"def": t, "first": first, "count": count, "end": first + count - 1, "block": block,
                         "n": max(len(rows), 1) * block, "rows": rows, "pattern": pattern})
        for spec in sd.get("drop_rows") or []:  # 無視する行: 出力から取り除く
            a, b = parse_row_spec(spec)
            tbls.append({"def": {}, "first": a, "count": b - a + 1, "end": b, "block": 1, "n": 0,
                         "rows": [], "pattern": [], "drop": True})
        tbls.sort(key=lambda x: x["first"])
        for a, b in zip(tbls, tbls[1:]):
            if a["end"] >= b["first"]:
                raise TemplateError(f"シート「{name}」で表のサンプル行が重なっています")
        plans[name] = {"def": sd, "tables": tbls}
        maps[name] = RowMap(tbls)

    # --- 2. シート XML を再構成 -----------------------------------------------
    for name, part in sheet_parts.items():
        root = pkg.xml(part)
        rw = Rewriter(name, maps)
        if name in plans:
            _render_sheet(pkg, part, root, plans[name], maps[name], rw, data, styles, warnings,
                          leftovers.setdefault(name, set()))
        elif maps:
            for f in root.iter(q("f")):
                if f.text and "!" in f.text:
                    f.text = rw.formula(f.text)
        pkg.put_xml(part, root)

    # --- 3. ブック全体の参照（定義名・グラフ）と再計算の設定 --------------------
    _fix_workbook(pkg, maps)
    props = definition.get("properties") or {}
    apply_properties(pkg, props)
    if props.get("scrub"):
        n = drop_comments(pkg)
        if n:
            warnings.append(f"コメント（メモ）{n} 件を取り除きました（properties.scrub）")
        scrub_leftover_text(pkg)
    if definition.get("strict"):
        total = sum(len(refs) for refs in leftovers.values())
        if total:
            by_sheet = "\n".join(f"  {n}: {', '.join(compress_cells(refs))}" for n, refs in leftovers.items() if refs)
            raise TemplateError(
                f"テンプレートの値がそのまま残るセルが {total} 個あります（シートごと・範囲にまとめて）:\n{by_sheet}\n"
                "残すなら keep、置き換えるなら cells / columns の key、空にするなら clear、行ごと消すなら drop_rows に入れてください")
    pkg.save(output)
    return warnings


def _missing_key_warnings(t: dict, block: int, rows: list, sheet: str) -> list[str]:
    """列の key が、データのどの行にも無い（綴りの違い）と、その列は黙って空欄になる。それを知らせる。"""
    records = [r for r in rows if isinstance(r, dict)]
    if not records:
        return []
    wanted = [spec["key"] for cols in _column_maps(t, block) for spec in cols.values()
              if spec.get("key") and spec["key"] != "$index" and not spec.get("formula")
              and not spec.get("keep") and not spec.get("clear")]
    def found(k: str) -> bool:
        return any(k in r or _row_value(r, k) is not None for r in records)
    missing = [k for k in dict.fromkeys(wanted) if not found(k)]
    heads = {k.split(".")[0] for k in wanted} | set(wanted)
    unused = [k for k in dict.fromkeys(k for r in records for k in r) if k not in heads]
    # 空欄がふつうの列（備考など）もあるので、綴り違いの手がかり（使われていないキー）があるか、どの key も当たらないときだけ
    if not missing or not unused and len(missing) < len(set(wanted)):
        return []
    return [f"シート「{sheet}」表 {t.get('id')}: 列の key {', '.join(map(repr, missing))} が、"
            f"データ {t['key']!r} のどの行にも無いため空欄になります"
            + (f"（データにあって使われていないキー: {', '.join(map(repr, unused))}）" if unused else "")]


def compress_cells(refs) -> list[str]:
    """セルの一覧を、keep などにそのまま書ける範囲（`A1:C1`・`A3:A5`）にまとめる。行ごとに横へ、同じ横幅は縦へつなぐ。"""
    rows: dict[int, list[int]] = {}
    for ref in refs:
        col, r = split_ref(ref)
        rows.setdefault(r, []).append(col)
    spans: list[tuple[int, int, int]] = []   # (行, 先頭の列, 末尾の列)
    for r in sorted(rows):
        cols = sorted(rows[r])
        start = prev = cols[0]
        for c in cols[1:] + [None]:
            if c is not None and c == prev + 1:
                prev = c
                continue
            spans.append((r, start, prev))
            if c is not None:
                start = prev = c
    blocks: list[list[int]] = []   # [先頭の行, 末尾の行, 先頭の列, 末尾の列]
    for r, a, b in spans:
        hit = next((x for x in blocks if x[1] == r - 1 and x[2] == a and x[3] == b), None)
        if hit:
            hit[1] = r
        else:
            blocks.append([r, r, a, b])
    out = []
    for r1, r2, a, b in sorted(blocks):
        first, last = f"{get_column_letter(a)}{r1}", f"{get_column_letter(b)}{r2}"
        out.append(first if first == last else f"{first}:{last}")
    return out


def _has_literal(c) -> bool:
    if c.find(q("f")) is not None:
        return False
    if c.find(q("is")) is not None:
        return True
    v = c.find(q("v"))
    return v is not None and v.text not in (None, "")


def _render_sheet(pkg, part, root, plan, rowmap, rw, data, styles, warnings, leftovers) -> None:
    sd, tables = plan["def"], plan["tables"]
    keep_set = {pos for spec in sd.get("keep") or [] for pos in parse_cell_spec(spec)}
    expand_shared_formulas(root)
    sheet_data = root.find(q("sheetData"))
    orig = sheet_rows(root)
    tbl_of: dict[int, dict] = {}
    for t in tables:
        for r in range(t["first"], t["end"] + 1):
            tbl_of[r] = t

    # 定義に残した列の見出し（analyze が書く header）と、テンプレートの見出しの行を突き合わせる。
    # 列を足した・並べ替えたテンプレートに差し替えると、黙って 1 列ずつずれて入るので止める
    sst = read_shared_strings(pkg)
    moved = []
    for t in tables:
        td, head_r = t["def"], t["def"].get("header_row")
        if t.get("drop") or not head_r or int(head_r) not in orig:
            continue
        heads = {cell_col(c): cell_value(c, sst) for c in orig[int(head_r)]}
        for letter, spec in (td.get("columns") or {}).items():
            want = spec.get("header") if isinstance(spec, dict) else None
            have = heads.get(column_index_from_string(letter.upper()))
            if want is not None and str(want).strip() != str(have if have is not None else "").strip():
                moved.append(f"{letter}{head_r}: 定義では「{want}」、テンプレートでは「{have if have is not None else '（空）'}」")
    if moved:
        raise TemplateError(f"シート「{sd['name']}」の表の見出しが、定義と合いません（列を足した・並べ替えたテンプレートでは、"
                            "値が別の列に入ります）。analyze から定義を作り直すか、columns の列を直してください:\n  "
                            + "\n  ".join(moved))

    fixed_cells = {}
    cell_specs = {ref: (spec if isinstance(spec, dict) else {"key": spec}) for ref, spec in (sd.get("cells") or {}).items()}
    for ref, spec in cell_specs.items():
        col, r = split_ref(ref)
        if r in tbl_of:
            raise TemplateError(f"cells の {ref} は表のサンプル行の中です")
        try:
            value = dig(data, spec["key"])
        except KeyError:
            raise TemplateError(f"データに {spec['key']!r} がありません（cells の {ref}）")
        _check_choice(spec, value, [s for s in cell_specs.values() if s.get("key") == spec["key"]], f"cells の {ref}")
        fixed_cells[(col, r)] = convert_value(spec, value, f"cells の {ref}")

    clear_cells: set[tuple[int, int]] = set()
    for spec in sd.get("clear") or []:  # 無視する値: 書式は残して空にする
        for col, r in parse_cell_spec(spec):
            if r in tbl_of:
                raise TemplateError(f"clear の {spec} は表のサンプル行の中です（列の clear を使う）")
            clear_cells.add((col, r))

    new_rows: list[etree._Element] = []
    pattern_map: dict[int, list[int]] = {}  # 元のサンプル行 → 出力行の一覧（結合セルの複製用）

    def emit_fixed(r: int, row) -> None:
        new_r = rowmap.map(r, False)
        row = deepcopy(row)
        for f in row.iter(q("f")):
            if f.text:
                f.text = rw.formula(f.text)
            if f.get("ref"):
                f.set("ref", rw.sqref(f.get("ref")))
        _set_row_number(row, new_r)
        for (col, rr) in clear_cells:
            if rr == r:
                for c in row:
                    if cell_col(c) == col and c.find(q("f")) is None:
                        set_value(c, None, styles)
        for (col, rr), value in fixed_cells.items():
            if rr == r:
                set_value(_get_or_make_cell(row, col, new_r), value, styles, replace_formula=True)
        for c in row:
            pos = (cell_col(c), r)
            if _has_literal(c) and pos not in keep_set and pos not in fixed_cells:
                leftovers.add(f"{get_column_letter(pos[0])}{r}")
        _strip_cached(row)
        new_rows.append(row)

    done_tables: set[int] = set()
    for r in sorted(set(orig) | {rr for _, rr in fixed_cells}):
        if r in tbl_of:
            t = tbl_of[r]
            if id(t) in done_tables:
                continue
            done_tables.add(id(t))
            _emit_table(t, orig, rowmap, rw, styles, new_rows, pattern_map, warnings, leftovers)
            continue
        if r in orig:
            emit_fixed(r, orig[r])
        else:  # 行そのものが無い固定セルへの書き込み
            emit_fixed(r, etree.Element(q("row"), r=str(r)))
    # 行そのものが無い表（サンプル行が未作成）にも対応
    for t in tables:
        if id(t) not in done_tables:
            _emit_table(t, orig, rowmap, rw, styles, new_rows, pattern_map, warnings, leftovers)
    new_rows.sort(key=lambda x: int(x.get("r")))

    for child in list(sheet_data):
        sheet_data.remove(child)
    sheet_data.extend(new_rows)

    filled = set(fixed_cells) | clear_cells
    for t in tables:
        if t.get("drop"):
            continue
        colmaps = _column_maps(t["def"], t["block"])
        for r in range(t["first"], t["end"] + 1):
            filled |= {(col, r) for col, spec in colmaps[(r - t["first"]) % t["block"]].items()
                       if not spec.get("formula") and not spec.get("keep")}
    _drop_stale_links(pkg, part, root, filled, warnings)
    dropped_rows = {r for t in tables if t.get("drop") for r in range(t["first"], t["end"] + 1)}
    _fix_comments(pkg, part, filled, set(tbl_of), rowmap, warnings, dropped_rows)
    _fix_sheet_parts(pkg, part, root, tables, rowmap, rw, pattern_map, warnings)


def _column_maps(td: dict, block: int) -> list[dict]:
    """行ごとの列指定 {列番号: spec} のリスト。block_rows=1 なら columns、2 以上なら block（行ごとの columns）。"""
    def conv(cols):
        return {column_index_from_string(letter.upper()): spec for letter, spec in (cols or {}).items()}
    if block == 1:
        return [conv(td.get("columns"))]
    rows = td.get("block")
    if not isinstance(rows, list) or len(rows) != block:
        raise TemplateError(f"block_rows={block} の表 {td.get('id')} には、行ごとの列指定 block を {block} 個書いてください")
    return [conv(r) for r in rows]


def _emit_table(t, orig, rowmap, rw, styles, out_rows, pattern_map, warnings, leftovers) -> None:
    if t.get("drop"):
        return
    td, k = t["def"], t["block"]
    colmaps = _column_maps(td, k)
    new_first = t["first"] + rowmap.shift_before(t["first"])
    for rec in range(t["n"] // k):
        start = t["pattern"][rec % len(t["pattern"])]
        row_data = t["rows"][rec] if rec < len(t["rows"]) else None
        for j in range(k):
            src_r = start + j
            target_r = new_first + rec * k + j
            row = deepcopy(orig[src_r]) if src_r in orig else etree.Element(q("row"), r=str(src_r))
            pattern_map.setdefault(src_r, []).append(target_r)
            offset = target_r - (src_r + rowmap.shift_before(src_r))

            def row_fn(rm, idx, last, absolute, r, _off=offset):
                if absolute:
                    return rm.map(r, last)
                if r <= t["end"]:
                    return rm.map(r, False) + _off
                return rm.map(r, last)
            for c in row:
                f = c.find(q("f"))
                if f is not None and f.text:
                    f.text = rw.formula(f.text, row_fn)
                if f is not None and f.get("ref"):  # 配列数式の範囲も、複製先の行へずらす
                    f.set("ref", re.sub(r"([A-Za-z]+)(\d+)",
                                        lambda m: f"{m.group(1)}{int(m.group(2)) - src_r + target_r}", f.get("ref")))
            _set_row_number(row, target_r)
            for col, spec in colmaps[j].items():
                if spec.get("formula") or spec.get("keep"):
                    continue
                c = _get_or_make_cell(row, col, target_r)
                if spec.get("clear") or "key" not in spec:
                    set_value(c, None, styles)
                    continue
                key = spec["key"]
                if key == "$index":
                    value = rec + 1 if row_data is not None else None
                elif row_data is None:
                    value = None
                elif isinstance(row_data, dict):
                    value = _row_value(row_data, key)
                else:
                    raise TemplateError(f"{td['key']!r} の要素はオブジェクトである必要があります")
                where = f"{td['key']}[{rec}].{key}"
                _check_choice(spec, value, [s for s in colmaps[j].values() if s.get("key") == key], where)
                value = convert_value(spec, value, where)
                if isinstance(value, (dict, list)):
                    raise TemplateError(f"{where} に配列・オブジェクトは入れられません")
                set_value(c, value, styles, replace_formula=True)
            for c in row:
                if cell_col(c) not in colmaps[j] and _has_literal(c):
                    leftovers.add(f"{get_column_letter(cell_col(c))}{src_r}")
            _strip_cached(row)
            out_rows.append(row)


# データを人が読める形で書き、表の見た目（○×・コード・年月日の分割）へは定義で変換する
DATE_PARTS = ("year", "month", "day", "hour", "minute")


def convert_value(spec: dict, value: Any, where: str = "") -> Any:
    """列・セルの指定（when / map / part）に従って、データの値をセルに入れる値に変える。

    - when: 値が when と同じ（配列なら when を含む）なら mark（既定 ○）、違えば unmark（既定 空欄）。
      択一（`判定: 合格`）・複数選択（`対象: [Windows, Linux]`）を、見出しの列ごとの ○ にする
    - map: 値を表の表記に置き換える（`true: ○` / `false: ×`、`高: 1`）
    - part: 日付・日時の一部（year / month / day / hour / minute）。年・月・日に分かれた欄へ 1 つの日付を入れる
    """
    if "when" in spec:
        if value is None:
            return None
        hit = spec["when"] in value if isinstance(value, list) else str(value) == str(spec["when"])
        return spec.get("mark", "○") if hit else spec.get("unmark")
    if value is None:
        return None
    if "map" in spec:
        m = spec["map"]
        for k in ((value, str(value).lower(), str(value)) if isinstance(value, bool) else (value, str(value))):
            if k in m:
                return m[k]
        raise TemplateError(f"{where} の値 {value!r} は、定義の map にありません（使える値: {', '.join(map(str, m))}）")
    if "part" in spec:
        part = spec["part"]
        if part not in DATE_PARTS:
            raise TemplateError(f"{where} の part は {', '.join(DATE_PARTS)} のどれかにしてください: {part!r}")
        d = value
        if isinstance(d, str):
            try:
                d = dt.datetime.fromisoformat(d.strip().replace("/", "-"))
            except ValueError:
                raise TemplateError(f"{where} の値 {value!r} を日付として読めません（2026-10-08 の形で書く）")
        if not isinstance(d, (dt.date, dt.datetime)):
            raise TemplateError(f"{where} の値 {value!r} は日付ではありません")
        if part in ("hour", "minute") and not isinstance(d, dt.datetime):
            return 0
        return getattr(d, part)
    return value


def _check_choice(spec: dict, value: Any, group: list, where: str) -> None:
    """択一・複数選択の値が、どの列の when にも当たらないと、黙ってどこにも ○ が付かない。それを止める。"""
    if "when" not in spec or value is None or group[0] is not spec:
        return   # 同じキーの列のうち、最初の列でだけ確かめる
    choices = [str(s["when"]) for s in group if "when" in s]
    for v in (value if isinstance(value, list) else [value]):
        if str(v) not in choices:
            raise TemplateError(f"{where} の値 {v!r} は、選べる値（{', '.join(choices)}）のどれでもありません")


def _row_value(row_data: dict, key: str) -> Any:
    """行オブジェクトから列の値を取る。キーそのもの（`No.` など）が無ければ、`test.0` のようにドット・添字でたどる。"""
    if key in row_data:
        return row_data[key]
    try:
        return dig(row_data, key)
    except KeyError:
        return None


def _drop_stale_links(pkg, part, root, filled: set, warnings: list) -> None:
    """データで置き換える・空にするセルのハイパーリンクを取り除く。残すと、新しい値に元の（サンプルの）リンク先が付く。"""
    links = root.find(q("hyperlinks"))
    if links is None:
        return
    dropped = []
    for h in list(links):
        if any(pos in filled for spec in (h.get("ref") or "").split() for pos in parse_cell_spec(spec)):
            links.remove(h)
            dropped.append(h.get("ref"))
    if not dropped:
        return
    warnings.append(f"データを入れるセルのハイパーリンク（{', '.join(dropped)}）は、元のリンク先のままになるため取り除きました")
    if not len(links):
        root.remove(links)
    rels_name = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if rels_name in pkg.data:  # どこからも指さなくなったリンク先（元の URL）も残さない
        used = {v for el in root.iter() for k, v in el.attrib.items() if k == f"{{{NS_R}}}id"}
        rels = pkg.xml(rels_name)
        for r in list(rels):
            if r.get("Type", "").endswith("/hyperlink") and r.get("Id") not in used:
                rels.remove(r)
        pkg.put_xml(rels_name, rels)


NS_VML_X = "urn:schemas-microsoft-com:office:excel"


def _fix_comments(pkg, part, filled: set, sample_rows: set, rowmap, warnings: list, dropped_rows: set = frozenset()) -> None:
    """コメント（メモ）を行のずれに追従させる。データを入れる・空にするセルのものは取り除く（新しい値に、元のメモが付く）。"""
    parts = {typ.rsplit("/", 1)[-1]: target for typ, target in pkg.rels_of(part).values() if target in pkg.data}
    if "comments" not in parts:
        return
    croot = pkg.xml(parts["comments"])
    vml = None
    if "vmlDrawing" in parts:
        try:
            vml = etree.fromstring(pkg.data[parts["vmlDrawing"]], etree.XMLParser(recover=True))
        except etree.XMLSyntaxError:
            vml = None
    shapes = {}
    for cd in (vml.iter(f"{{{NS_VML_X}}}ClientData") if vml is not None else []):
        r, c = cd.find(f"{{{NS_VML_X}}}Row"), cd.find(f"{{{NS_VML_X}}}Column")
        if r is not None and c is not None and (r.text or "").strip().isdigit() and (c.text or "").strip().isdigit():
            shapes[(int(c.text) + 1, int(r.text) + 1)] = (cd.getparent(), r)
    dropped, gone = [], []   # データを入れるセルのもの・取り除く行のもの
    for cm in list(croot.iter(q("comment"))):
        col, r = split_ref(cm.get("ref"))
        shape = shapes.get((col, r))
        if (col, r) in filled or r in dropped_rows:
            cm.getparent().remove(cm)
            if shape is not None and shape[0].getparent() is not None:
                shape[0].getparent().remove(shape[0])
            (gone if r in dropped_rows else dropped).append(cm.get("ref"))
        elif r not in sample_rows:   # 表の外の行は、行と一緒に動かす
            new_r = rowmap.map(r, False)
            cm.set("ref", f"{get_column_letter(col)}{new_r}")
            if shape is not None:
                shape[1].text = str(new_r - 1)
    if dropped:
        warnings.append(f"データを入れるセルのコメント（{', '.join(dropped)}）は、新しい値に元のメモが付くため取り除きました")
    if gone:
        warnings.append(f"取り除く行のコメント（{', '.join(gone)}）も取り除きました")
    pkg.put_xml(parts["comments"], croot)
    if vml is not None:
        pkg.data[parts["vmlDrawing"]] = etree.tostring(vml)


def _fix_sheet_parts(pkg, part, root, tables, rowmap, rw, pattern_map, warnings) -> None:
    # 結合セル: 表の外は移動、サンプル行内は複製
    mc = root.find(q("mergeCells"))
    if mc is not None:
        keep = []
        for m in list(mc):
            a, b = (m.get("ref").split(":") * 2)[:2]
            (c1, r1), (c2, r2) = split_ref(a), split_ref(b)
            t = next((t for t in tables if t["first"] <= r1 <= t["end"] or t["first"] <= r2 <= t["end"]), None)
            mc.remove(m)
            if t and t["first"] <= r1 and r2 <= t["end"]:
                same_block = (r1 - t["first"]) // t["block"] == (r2 - t["first"]) // t["block"]
                if r1 == r2 or same_block:   # 1 件（ブロック）の中の結合は、その行を複製した先ごとに複製する
                    for target in pattern_map.get(r1, []):
                        keep.append(f"{get_column_letter(c1)}{target}:{get_column_letter(c2)}{target + r2 - r1}")
                else:
                    warnings.append(f"複数行にまたがる結合セル {m.get('ref')} は表のサンプル行内のため取り除きました")
                continue
            span = rowmap.map_span(r1, r2)
            if span is not None:
                keep.append(f"{get_column_letter(c1)}{span[0]}:{get_column_letter(c2)}{span[1]}")
        for ref in keep:
            etree.SubElement(mc, q("mergeCell"), ref=ref)
        mc.set("count", str(len(keep)))
        if not keep:
            root.remove(mc)

    # 範囲を持つ要素
    # 範囲がすべて消えた要素は取り除く（空の sqref は、Excel が壊れたファイルと見なす）
    for tag in ("conditionalFormatting", "dataValidation", "ignoredError"):
        for el in list(root.iter(q(tag))):
            if el.get("sqref"):
                el.set("sqref", rw.sqref(el.get("sqref")))
                if not el.get("sqref"):
                    el.getparent().remove(el)
    for el in list(root.iter(q("hyperlink"))):
        el.set("ref", rw.sqref(el.get("ref")))
        if not el.get("ref"):
            el.getparent().remove(el)
    for tag in ("dataValidations", "hyperlinks"):   # 子が無くなった入れ物も取り除き、件数を合わせる
        for el in list(root.iter(q(tag))):
            if len(el) == 0:
                el.getparent().remove(el)
            elif el.get("count") is not None:
                el.set("count", str(len(el)))
    for el in list(root.iter(q("ignoredErrors"))):
        if len(el) == 0:
            el.getparent().remove(el)
    af = root.find(q("autoFilter"))
    if af is not None and af.get("ref"):
        af.set("ref", rw.sqref(af.get("ref")))
    # 条件付き書式・入力規則の数式は、表の下にある参照だけを動かす（相対参照の意味を壊さない）
    for tag in ("formula", "formula1", "formula2"):
        for el in root.iter(q(tag)):
            if el.text:
                el.text = rw.formula(el.text, lambda rm, idx, last, absolute, r: rm.map(r, False))
    for brk in root.iter(q("brk")):
        if brk.get("id"):
            brk.set("id", str(rowmap.map(int(brk.get("id")), False)))

    # dimension
    dim = root.find(q("dimension"))
    if dim is not None:
        refs = [(cell_col(c), int(c.getparent().get("r"))) for c in root.iter(q("c"))]
        if refs:
            cols, rows = [x[0] for x in refs], [x[1] for x in refs]
            dim.set("ref", f"{get_column_letter(min(cols))}{min(rows)}:{get_column_letter(max(cols))}{max(rows)}")

    for _, (typ, target) in pkg.rels_of(part).items():
        if typ.endswith("/table") and target in pkg.data:  # Excel テーブル
            troot = pkg.xml(target)
            for el in (troot, troot.find(q("autoFilter"))):
                if el is not None and el.get("ref"):
                    el.set("ref", rw.sqref(el.get("ref")))
            pkg.put_xml(target, troot)
        if typ.endswith("/drawing") and target in pkg.data:  # 図・グラフの位置
            droot = pkg.xml(target)
            for tag in ("from", "to"):
                for el in droot.iter(f"{{{NS_XDR}}}{tag}"):
                    row = el.find(f"{{{NS_XDR}}}row")
                    if row is not None:
                        row.text = str(rowmap.map(int(row.text) + 1, False) - 1)
            pkg.put_xml(target, droot)


def _fix_workbook(pkg: Package, maps: dict[str, RowMap]) -> None:
    # グラフの参照範囲
    for name in list(pkg.data):
        if name.startswith("xl/charts/chart") and name.endswith(".xml"):
            croot = pkg.xml(name)
            changed = False
            for f in croot.iter(f"{{{NS_C}}}f"):
                if f.text:
                    new = Rewriter("", maps).formula(f.text)
                    if new != f.text:
                        f.text, changed = new, True
            if changed:
                pkg.put_xml(name, croot)
    wb = pkg.xml("xl/workbook.xml")
    dn = wb.find(q("definedNames"))
    if dn is not None:
        for d in dn:
            if d.text:
                d.text = Rewriter("", maps).formula(d.text)
    # 再計算: 開いた時に全再計算させ、古い計算チェーンは捨てる
    calc = wb.find(q("calcPr"))
    if calc is None:
        calc = etree.Element(q("calcPr"))
        anchor = None
        for tag in ("definedNames", "externalReferences", "functionGroups", "sheets"):
            anchor = wb.find(q(tag))
            if anchor is not None:
                break
        anchor.addnext(calc)
    calc.set("fullCalcOnLoad", "1")
    pkg.put_xml("xl/workbook.xml", wb)
    if "xl/calcChain.xml" in pkg.data:
        _drop_part(pkg, "xl/calcChain.xml")


def _drop_part(pkg: Package, part: str) -> None:
    """part を取り除き、part 自身の rels、それを指す rels、[Content_Types].xml の Override も消す。"""
    pkg.removed.add(part)
    pkg.removed.add(posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"))
    for name in list(pkg.data):
        if not name.endswith(".rels") or name in pkg.removed:
            continue
        base = posixpath.dirname(posixpath.dirname(name))
        root = pkg.xml(name)
        changed = False
        for r in list(root):
            target = r.get("Target", "")
            if r.get("TargetMode") == "External":
                continue
            resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
            if resolved == part:
                root.remove(r)
                changed = True
        if changed:
            pkg.put_xml(name, root)
    ct = pkg.xml("[Content_Types].xml")
    for o in list(ct):
        if o.get("PartName", "").lstrip("/") == part:
            ct.remove(o)
    pkg.put_xml("[Content_Types].xml", ct)


# ---------------------------------------------------------------------------
# 来歴（他プロジェクトの成果物を流用するときの持ち越し）
# ---------------------------------------------------------------------------

NS_DC = "http://purl.org/dc/elements/1.1/"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
CORE_FIELDS = {"creator": f"{{{NS_DC}}}creator", "lastModifiedBy": f"{{{NS_CP}}}lastModifiedBy",
               "title": f"{{{NS_DC}}}title", "subject": f"{{{NS_DC}}}subject",
               "description": f"{{{NS_DC}}}description", "keywords": f"{{{NS_CP}}}keywords",
               "category": f"{{{NS_CP}}}category"}
APP_FIELDS = {"company": f"{{{NS_APP}}}Company", "manager": f"{{{NS_APP}}}Manager"}


def read_properties(pkg: Package) -> dict[str, str]:
    """文書のプロパティ（作成者・会社名など）のうち、値があるもの。"""
    out: dict[str, str] = {}
    for part, fields in (("docProps/core.xml", CORE_FIELDS), ("docProps/app.xml", APP_FIELDS)):
        if part not in pkg.data:
            continue
        root = pkg.xml(part)
        for key, tag in fields.items():
            el = root.find(tag)
            if el is not None and (el.text or "").strip():
                out[key] = el.text.strip()
    return out


def provenance(pkg: Package) -> list[str]:
    """出力に持ち越されると困るかもしれないものを、文で返す。"""
    out = []
    props = read_properties(pkg)
    if props:
        out.append("文書のプロパティ: " + "、".join(f"{k}={v!r}" for k, v in props.items()))
    names = pkg.data.keys()
    if "docProps/custom.xml" in names:
        out.append("カスタムプロパティ（docProps/custom.xml）がある")
    if any(n.startswith("docProps/thumbnail") for n in names):
        out.append("プレビュー画像（docProps/thumbnail）がある。元の内容が写っている")
    authors = set()
    n_comments = 0
    for _, sheet_part in pkg.sheets():  # 配置場所は Excel と openpyxl で違うので、rels の種類で探す
        for typ, target in pkg.rels_of(sheet_part).values():
            if typ.endswith("/comments") and target in pkg.data:
                root = pkg.xml(target)
                authors |= {a.text for a in root.iter(q("author")) if a.text}
                n_comments += len(list(root.iter(q("comment"))))
    if n_comments:
        out.append(f"コメント（メモ）が {n_comments} 件ある。作成者: {'、'.join(sorted(authors))}")
    if any(n.startswith("xl/threadedComments") for n in names):
        out.append("スレッド形式のコメントがある")
    if any(n.startswith("xl/externalLinks/") and n.endswith(".xml") for n in names):
        out.append("他のブックへの外部リンクがある（元のファイル名・パスが残る）")
    if "xl/vbaProject.bin" in names:
        out.append("マクロ（VBA）がある。出力の拡張子は .xlsm にする")
    wb = pkg.xml("xl/workbook.xml")
    hidden = [s.get("name") for s in wb.find(q("sheets")) if s.get("state") in ("hidden", "veryHidden")]
    if hidden:
        out.append("非表示のシートがある: " + "、".join(hidden))
    n_hidden = 0
    for _, part in pkg.sheets():
        root = pkg.xml(part)
        n_hidden += sum(1 for r in root.iter(q("row")) if r.get("hidden") == "1")
        n_hidden += sum(1 for c in root.iter(q("col")) if c.get("hidden") == "1")
    if n_hidden:
        out.append(f"非表示の行・列が {n_hidden} 個ある")
    if any(n.startswith("xl/revisions/") for n in names):
        out.append("変更履歴（リビジョン）がある")
    return out


def apply_properties(pkg: Package, props: dict) -> None:
    """定義の properties を反映する。scrub: true なら、指定の無い識別情報を空にし、プレビューとカスタムプロパティを取り除く。"""
    scrub = bool(props.get("scrub"))
    for part, fields in (("docProps/core.xml", CORE_FIELDS), ("docProps/app.xml", APP_FIELDS)):
        if part not in pkg.data:
            continue
        root = pkg.xml(part)
        changed = False
        for key, tag in fields.items():
            value = props[key] if key in props else ("" if scrub else None)
            if value is None:
                continue
            el = root.find(tag)
            if el is None:
                if not value:
                    continue
                el = etree.SubElement(root, tag)
            if (el.text or "") != str(value):
                el.text = str(value) or None
                changed = True
        if changed:  # 変えないものは、元のバイト列のまま
            pkg.put_xml(part, root)
    if scrub:
        for name in list(pkg.data):
            if name == "docProps/custom.xml" or name.startswith("docProps/thumbnail"):
                _drop_part(pkg, name)


def drop_comments(pkg: Package) -> int:
    """コメント（メモ・スレッド形式）を、吹き出しの図形と一緒にすべて取り除く。取り除いた件数を返す。"""
    total = 0
    for _, part in pkg.sheets():
        rels = pkg.rels_of(part)
        targets = {typ.rsplit("/", 1)[-1]: (rid, target) for rid, (typ, target) in rels.items() if target in pkg.data}
        if "comments" not in targets and "threadedComment" not in targets:
            continue
        for kind in ("comments", "threadedComment"):
            if kind in targets:
                target = targets[kind][1]
                if kind == "comments":
                    total += len(list(pkg.xml(target).iter(q("comment"))))
                _drop_part(pkg, target)
        if "vmlDrawing" not in targets:
            continue
        rid, vml_part = targets["vmlDrawing"]
        try:
            vml = etree.fromstring(pkg.data[vml_part], etree.XMLParser(recover=True))
        except etree.XMLSyntaxError:
            continue
        for cd in list(vml.iter(f"{{{NS_VML_X}}}ClientData")):
            if cd.get("ObjectType") == "Note" and cd.getparent() is not None and cd.getparent().getparent() is not None:
                shape = cd.getparent()
                shape.getparent().remove(shape)
        if any(cd.get("ObjectType") != "Note" for cd in vml.iter(f"{{{NS_VML_X}}}ClientData")):
            pkg.data[vml_part] = etree.tostring(vml)   # フォームのボタンなど、メモ以外の図形は残す
            continue
        _drop_part(pkg, vml_part)
        root = pkg.xml(part)
        for ld in list(root.iter(q("legacyDrawing"))):
            if ld.get(f"{{{NS_R}}}id") == rid:
                ld.getparent().remove(ld)
        pkg.put_xml(part, root)
    if not any(n.startswith("xl/threadedComments/") for n in pkg.data if n not in pkg.removed):
        for name in list(pkg.data):
            if name.startswith("xl/persons/") and name.endswith(".xml") and name not in pkg.removed:
                _drop_part(pkg, name)   # スレッド形式のコメントの作成者の一覧
    return total


def scrub_leftover_text(pkg: Package) -> None:
    """どのセルも参照しなくなった共有文字列と、グラフの古い値のキャッシュを取り除く。"""
    name = "xl/sharedStrings.xml"
    if name in pkg.data:
        sst = pkg.xml(name)
        items = list(sst)
        roots = {part: pkg.xml(part) for _, part in pkg.sheets()}
        used = sorted({int(c.find(q("v")).text) for r in roots.values() for c in r.iter(q("c"))
                       if c.get("t") == "s" and c.find(q("v")) is not None})
        remap = {old: new for new, old in enumerate(used)}
        total = 0
        for part, root in roots.items():
            for c in root.iter(q("c")):
                if c.get("t") == "s" and c.find(q("v")) is not None:
                    c.find(q("v")).text = str(remap[int(c.find(q("v")).text)])
                    total += 1
            pkg.put_xml(part, root)
        for it in items:
            sst.remove(it)
        for old in used:
            sst.append(items[old])
        sst.set("count", str(total))
        sst.set("uniqueCount", str(len(used)))
        pkg.put_xml(name, sst)
    for part in list(pkg.data):
        if part.startswith("xl/charts/chart") and part.endswith(".xml"):
            root = pkg.xml(part)
            for tag in ("numCache", "strCache", "multiLvlStrCache"):
                for el in list(root.iter(f"{{{NS_C}}}{tag}")):
                    el.getparent().remove(el)
            pkg.put_xml(part, root)


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
    """拡張子が .yaml / .yml なら YAML、.json なら JSON。名前が無い（標準入力）なら JSON → YAML の順に試す。"""
    lower = name.lower()
    try:
        if lower.endswith((".yaml", ".yml")):
            return _yaml().safe_load(text)
        if lower.endswith(".json") or not lower:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if lower:
                    raise
                return _yaml().safe_load(text)
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise TemplateError(f"{name or '入力'} を読めません: {e}")
    except Exception as e:  # yaml.YAMLError
        if isinstance(e, TemplateError):
            raise
        raise TemplateError(f"{name or '入力'} を読めません: {e}")


def load_structured(path: str) -> Any:
    if path == "-":
        return parse_structured(sys.stdin.read())
    with open(path, encoding="utf-8") as f:
        return parse_structured(f.read(), path)


def dump_structured(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if path.lower().endswith((".yaml", ".yml")):
            _yaml().safe_dump(obj, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")


# ---------------------------------------------------------------------------
# データの形（定義から導く）
# ---------------------------------------------------------------------------

def _set_path(root: dict, path: str, value: Any) -> None:
    cur = root
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def skeleton_data(definition: dict) -> dict:
    """定義が必要とするデータの雛形（値は null）。"""
    out: dict = {}
    for sd in definition.get("sheets", []):
        for spec in (sd.get("cells") or {}).values():
            _set_path(out, spec["key"] if isinstance(spec, dict) else spec, None)
        for t in sd.get("tables", []):
            colmaps = t["block"] if t.get("block_rows", 1) > 1 and t.get("block") else [t.get("columns")]
            row: dict = {}
            for c in (c for cols in colmaps for c in (cols or {}).values()):
                if c.get("key") and c["key"] != "$index" and not c.get("keep") and not c.get("clear"):
                    _set_path(row, c["key"], None)
            _set_path(out, t["key"], [row])
    return _as_lists(out)


def value_notes(definition: dict) -> list[str]:
    """値の書き方（択一・複数選択・true/false・日付）の説明。雛形の null だけでは分からないものを補う。"""
    notes: dict[str, str] = {}
    for sd in definition.get("sheets", []):
        specs = [(None, s if isinstance(s, dict) else {"key": s}) for s in (sd.get("cells") or {}).values()]
        for t in sd.get("tables", []):
            colmaps = t["block"] if t.get("block_rows", 1) > 1 and t.get("block") else [t.get("columns")]
            specs += [(t["key"], c) for cols in colmaps for c in (cols or {}).values()]
        for table, spec in specs:
            key = spec.get("key")
            if not key or key == "$index":
                continue
            name = f"{table}[].{key}" if table else key
            if "when" in spec:
                whens = [str(s["when"]) for tk, s in specs if tk == table and s.get("key") == key and "when" in s]
                notes[name] = f"{name}: {' / '.join(whens)} のどれか（複数なら配列）"
            elif "map" in spec:
                notes[name] = f"{name}: {' / '.join(str(k).lower() if isinstance(k, bool) else str(k) for k in spec['map'])} のどれか"
            elif "part" in spec:
                notes[name] = f"{name}: 日付（2026-10-08）"
    return list(notes.values())


def _as_lists(obj: Any) -> Any:
    """キーが 0, 1, 2 … だけのオブジェクトを配列にする（`test.0`・`test.1` の雛形を `test: [null, null]` に）。"""
    if not isinstance(obj, dict):
        return [_as_lists(x) for x in obj] if isinstance(obj, list) else obj
    obj = {k: _as_lists(v) for k, v in obj.items()}
    if obj and set(obj) == {str(i) for i in range(len(obj))}:
        return [obj[str(i)] for i in range(len(obj))]
    return obj


def _read_back(items: list) -> dict:
    """（列・セルの指定, セルの値）の組から、データの値に戻す。convert_value の逆。

    択一・複数選択は選ばれた見出しの配列で返す（1 つにするかは呼び出し側が決める）。
    × や - の「選ばれていない」印は、空（null）として読む。
    """
    out: dict = {}
    picks: dict = {}
    parts: dict = {}
    for spec, v in items:
        key = spec["key"]
        out.setdefault(key, None)   # 列の順に並べる
        if isinstance(v, str):
            v = v.strip() or None
        if "when" in spec:
            got = picks.setdefault(key, [])
            if v is not None and str(v) == str(spec.get("mark", "○")):
                got.append(spec["when"])
        elif "map" in spec:
            back = {str(m): ({"true": True, "false": False}.get(k, k) if isinstance(k, str) else k)
                    for k, m in spec["map"].items() if m is not None}
            out[key] = None if v is None else back.get(str(v), v)
        elif "part" in spec:
            parts.setdefault(key, {})[spec["part"]] = v
        else:
            out[key] = None if _blankish(v) else v
    out.update(picks)
    for key, p in parts.items():
        try:
            d = dt.datetime(int(p["year"]), int(p.get("month") or 1), int(p.get("day") or 1),
                            int(p.get("hour") or 0), int(p.get("minute") or 0))
            out[key] = d.date().isoformat() if not {"hour", "minute"} & set(p) else d.isoformat(sep=" ", timespec="minutes")
        except (KeyError, TypeError, ValueError):
            out[key] = None
    return out


def _settle_choices(records: list[dict], specs: list[dict]) -> None:
    """択一は見出し 1 つ（選ばれていなければ null）、どこかの行で 2 つ以上選ばれていれば配列にそろえる。"""
    for key in {s["key"] for s in specs if "when" in s}:
        multi = any(len(r.get(key) or []) > 1 for r in records)
        for r in records:
            got = r.get(key) or []
            r[key] = got if multi else (got[0] if got else None)


def _is_empty_record(values: dict) -> bool:
    """既定値（null・false・選ばれていない []）しか無い行。連番・定数・数式の列は、はじめから数えない。"""
    return all(v is None or v is False or v == [] for v in values.values())


def _to_data(flat: dict) -> dict:
    out: dict = {}
    for k, v in flat.items():
        _set_path(out, k, v)
    return _as_lists(out)


def extract(source: "str | bytes", definition: dict, template: "str | bytes | None" = None) -> tuple[dict, list[str]]:
    """記入済みの文書（テンプレートと同じ形）から、定義に沿ってデータを取り出す。render の逆。

    データに書くのは、定義で key を持つ欄だけ（連番・定数・数式・人が書き込む欄は書かない）。
    表の行は、既定値（連番・× など）を除いて値が無ければ空の行として書かない。
    欄（key）は、どの行も空でも記入枠として残す。
    """
    pkg = Package(source)
    sst = read_shared_strings(pkg)
    styles = Styles(pkg)
    sheet_parts = dict(pkg.sheets())
    tpl_grids: dict = {}
    if template is not None:
        tpkg = Package(template)
        tsst, tstyles = read_shared_strings(tpkg), Styles(tpkg)
        tpl_grids = {n: _grid(tpkg.xml(pt), tsst, tstyles) for n, pt in tpkg.sheets()}
    flat: dict = {}
    notes: list[str] = []
    for sd in definition.get("sheets", []):
        name = sd["name"]
        if name not in sheet_parts:
            raise TemplateError(f"文書にシート「{name}」がありません")
        grid = _grid(pkg.xml(sheet_parts[name]), sst, styles)
        tgrid = tpl_grids.get(name, {})
        shifts: list[tuple[int, int]] = []   # (テンプレートの表の最後の行, 文書で増えた行数)
        offset = 0
        for t in sorted(sd.get("tables", []), key=lambda x: int(x["first_row"])):
            first, count = int(t["first_row"]), int(t["sample_rows"])
            k = int(t.get("block_rows", 1))
            colmaps = _column_maps(t, k)
            used_cols = {c for cm in colmaps for c in cm}
            # 表の後ろの行（テンプレートで表のすぐ下にある見出し・ラベル）に来たら、表は終わり
            after = {c: i["value"] for c, i in tgrid.get(first + count, {}).items()
                     if isinstance(i["value"], str) and i["value"].strip()}
            records, empty = [], []
            r = first + offset
            while True:
                rows = [grid.get(r + j) for j in range(k)]
                head = rows[0]
                if head is None or _is_total_row(head) or not any(c in head for c in used_cols) \
                        or after and all(head.get(c, {}).get("value") == v for c, v in after.items()):
                    break
                items = []
                for j, cm in enumerate(colmaps):
                    for col, spec in cm.items():
                        if spec.get("formula") or spec.get("keep") or spec.get("clear") \
                                or not spec.get("key") or spec["key"] == "$index":
                            continue
                        items.append((spec, (rows[j] or {}).get(col, {}).get("value")))
                values = _read_back(items)
                (empty.append(r) if _is_empty_record(values) else records.append(values))
                r += k
            specs = [spec for cm in colmaps for spec in cm.values() if spec.get("key") and spec["key"] != "$index"]
            _settle_choices(records, specs)
            flat[t["key"]] = [_to_data(rec) for rec in records]
            if empty:
                notes.append(f"シート「{name}」表 {t.get('id')}: 既定値だけの空の行 {', '.join(map(str, empty))} は書きませんでした")
            grown = (r - first - offset) - count
            shifts.append((first + count - 1, grown))
            offset += grown
        cell_items: dict = {}
        for ref, spec in (sd.get("cells") or {}).items():
            spec = spec if isinstance(spec, dict) else {"key": spec}
            c, row = split_ref(ref)
            row += sum(g for end, g in shifts if row > end)   # 表の行数が変わった分、下の欄がずれる
            cell_items.setdefault(spec["key"], []).append((spec, grid.get(row, {}).get(c, {}).get("value")))
        for key, items in cell_items.items():
            values = _read_back(items)
            if "when" in items[0][0]:
                got = values[key]
                values[key] = got if len(got) > 1 else (got[0] if got else None)
            flat.update(values)
    return _to_data(flat), notes


def validate_definition(template: "str | bytes", definition: dict) -> None:
    """テンプレートと定義の整合（シート・行・列）を確かめる。"""
    if definition.get("version") != DEF_VERSION:
        raise TemplateError(f"定義ファイルの version が未対応です: {definition.get('version')!r}")
    sheets = dict(Package(template).sheets())
    for sd in definition.get("sheets", []):
        if sd["name"] not in sheets:
            raise TemplateError(f"テンプレートにシート「{sd['name']}」がありません")
        for ref in (sd.get("cells") or {}):
            split_ref(ref)
        for spec in sd.get("clear") or []:
            parse_cell_spec(spec)
        for spec in sd.get("drop_rows") or []:
            parse_row_spec(spec)
        for t in sd.get("tables", []):
            first, count = int(t["first_row"]), int(t["sample_rows"])
            block = int(t.get("block_rows", 1))
            if block < 1 or count % block:
                raise TemplateError(f"sample_rows ({count}) は block_rows ({block}) の倍数にしてください（表 {t['id']}）")
            for p in t.get("pattern") or [first]:
                if not first <= int(p) <= first + count - block:
                    raise TemplateError(f"pattern の行 {p} がサンプル行 {first}-{first + count - block} の外です（表 {t['id']}）")
            for cols in _column_maps(t, block):
                pass


def check_definition(template: "str | bytes", definition: dict) -> list[str]:
    """定義の検査。整合を確かめ、雛形データで試しに再構成する（strict なら、残る値の漏れもここで見つかる）。警告を返す。"""
    import tempfile
    validate_definition(template, definition)
    with tempfile.TemporaryDirectory() as d:
        return render(template, definition, skeleton_data(definition), os.path.join(d, "check.xlsx"))


# ---------------------------------------------------------------------------
# スタンドアローン（固有の render スクリプトを書き出す）
# ---------------------------------------------------------------------------

# PEP 723 の依存宣言。書き出したスクリプトはこのエンジンの本文も含むので、ここに `# /// script` を行頭のまま書くと、
# uv がメタデータを 2 つと数えて動かない。行頭にならないよう {pep723} で差し込む。
PEP723 = "\n".join("#" + line for line in (
    " /// script", ' requires-python = ">=3.10"', ' dependencies = ["lxml", "openpyxl", "pyyaml"]', " ///"))
STANDALONE_HEADER = '''#!/usr/bin/env python3
{pep723}
"""{title}

xlsx テンプレートへデータを流し込む、固有の render スクリプト（xlsx-report-builder の export で生成）。
スキルは不要で動く。表構造の定義はこのファイルに埋め込み済み。{template_note}

    uv run {name} --data data.yaml -o out.xlsx
    python {name} --data data.json -o out.xlsx        # lxml・openpyxl・pyyaml が必要
    python {name} --example-data > data.yaml          # データの雛形を出す
    python {name} --extract-def def.yaml              # 埋め込みの定義を取り出す（直したら export --from-script で再生成）

データの形:
{shape}
"""
'''


def _wrap_b64(raw: bytes) -> str:
    text = base64.b64encode(raw).decode("ascii")
    lines = [text[i:i + 100] for i in range(0, len(text), 100)] or [""]
    return "(\n" + "\n".join(f"    {line!r}" for line in lines) + "\n)"


ENGINE_VERSION = 1  # 書き出したスクリプトに入るエンジンの版。export --from-script で最新へ更新できる


def read_exported_script(path: str) -> tuple[dict, "bytes | None", str]:
    """export が書き出したスクリプトから (定義, 埋め込みテンプレート or None, テンプレートの相対パス) を取り出す。

    スクリプトは実行せず、ast で埋め込み部分の定数だけを読む。
    """
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
    """この文書専用の、単体で動く render スクリプトを書き出す。

    template はパス、または（埋め込み専用で）bytes。embed=False ならテンプレートは別ファイルのまま、
    スクリプトからの相対パスで参照する。
    """
    if isinstance(template, (bytes, bytearray)):
        if not embed:
            raise TemplateError("テンプレートのパスが無いため、埋め込みでしか書き出せません")
        raw, rel = bytes(template), template_name or ""
    else:
        with open(template, "rb") as f:
            raw = f.read()
        rel = os.path.relpath(os.path.abspath(template), os.path.dirname(os.path.abspath(output)))
        template_name = os.path.basename(template)
    check_definition(raw, definition)
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        engine = f.read()
    # エンジン本体（CLI の入口より前）だけを取り込む
    engine = engine.split('\nif __name__ == "__main__":')[0]
    engine = engine.split("\n", 1)[1] if engine.startswith("#!") else engine
    # docstring を 2 つ持てないため、エンジンの docstring は取り除く
    engine = re.sub(r'^"""[\s\S]*?"""\n', "", engine, count=1)
    shape = "\n".join("    " + line for line in
                       json.dumps(skeleton_data(definition), ensure_ascii=False, indent=2).splitlines()
                       + ([""] + value_notes(definition) if value_notes(definition) else []))
    title = f"{os.path.splitext(template_name or 'template')[0]} の render スクリプト"
    note = ("テンプレートも埋め込み済み（--template で差し替えられる）。" if embed
            else f"テンプレート（{rel}）は、このスクリプトからの相対パスで読む。")
    header = STANDALONE_HEADER.format(pep723=PEP723, title=title, name=os.path.basename(output), shape=shape, template_note=note)
    tpl_literal = _wrap_b64(raw) if embed else '""'
    footer = (
        "\n\n# ---------------------------------------------------------------------------\n"
        "# 埋め込み（export が書き出した部分。手で直さず、定義を直して export をやり直す）\n"
        "# ---------------------------------------------------------------------------\n"
        f"ENGINE_VERSION = {ENGINE_VERSION}\n"
        f"DEFINITION = json.loads({json.dumps(definition, ensure_ascii=False)!r})\n"
        f"TEMPLATE_B64 = {tpl_literal}\n"
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
    parser.add_argument("--data", help="データ（.json / .yaml / .yml。- で標準入力）")
    parser.add_argument("-o", "--output", help="出力 .xlsx")
    parser.add_argument("--template", help="埋め込みのテンプレートの代わりに使う .xlsx（定義と構造が同じものに限る）")
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
            print(_yaml().safe_dump(skeleton_data(definition), allow_unicode=True, sort_keys=False, default_flow_style=False), end="")
            return 0
        here = os.path.dirname(os.path.abspath(sys.argv[0]))
        if args.template:
            with open(args.template, "rb") as f:
                template = f.read()
        elif template_bytes:
            template = template_bytes
        else:
            path = os.path.join(here, template_path)
            if not os.path.exists(path):
                raise TemplateError(f"テンプレートが見つかりません: {path}（--template で指定するか、export し直す）")
            with open(path, "rb") as f:
                template = f.read()
        if args.extract_template:
            if not template_bytes and not args.template:
                raise TemplateError("テンプレートは埋め込まれていません（元の .xlsx を使う）")
            with open(args.extract_template, "wb") as f:
                f.write(template)
            print(f"書き出しました: {args.extract_template}")
            return 0
        if not args.data or not args.output:
            parser.error("--data と -o が必要です")
        warnings = render(template, definition, load_structured(args.data), args.output)
        for w in warnings:
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

def cmd_analyze(args) -> int:
    definition = analyze(args.template)
    out = args.output or re.sub(r"\.xlsx?$", "", args.template, flags=re.I) + ".def.json"
    # template は、定義ファイルのある場所からの相対パスで書く（render・check・export がそこから読む）
    definition["template"] = os.path.relpath(os.path.abspath(args.template),
                                             os.path.dirname(os.path.abspath(out))).replace(os.sep, "/")
    dump_structured(definition, out)
    print(summarize(definition))
    print(f"\n定義ファイルの下書きを書きました: {out}")
    print("（? の項目と、流し込む欄・残す範囲の分け方をユーザーに確認してから、定義ファイルを直して確定する）")
    return 0


def cmd_check(args) -> int:
    definition = load_structured(args.definition)
    for w in check_definition(_template_arg(args, definition), definition):
        print(f"警告: {w}")
    print("定義は問題ありません")
    return 0


def cmd_inspect(args) -> int:
    facts = inspect_template(args.template)
    if args.json:
        json.dump(facts, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(format_facts(facts, fold=not args.all))
    return 0


def _template_arg(args, definition) -> str:
    """--template はそのまま、定義の template は定義ファイルのある場所からの相対パスとして読む。"""
    if args.template:
        return args.template
    template = definition.get("template")
    if not template:
        raise TemplateError("--template か定義ファイルの template が必要です")
    def_path = getattr(args, "definition", None)
    if os.path.isabs(template) or not def_path or def_path == "-":
        return template
    beside = os.path.join(os.path.dirname(os.path.abspath(def_path)), template)
    if os.path.exists(beside) or not os.path.exists(template):  # 以前の、作業場所からの相対パスも読めるように残す
        return beside
    return template


def cmd_render(args) -> int:
    definition = load_structured(args.definition)
    data = load_structured(args.data)
    warnings = render(_template_arg(args, definition), definition, data, args.output)
    for w in warnings:
        print(f"警告: {w}", file=sys.stderr)
    print(f"生成しました: {args.output}")
    return 0


def cmd_export(args) -> int:
    old_def, old_bytes, old_rel = (None, None, "")
    old_dir = ""
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
    if args.template or definition.get("template") and not args.from_script:
        template: "str | bytes" = _template_arg(args, definition)
    elif old_bytes is not None:  # 以前のスクリプトに埋め込まれたテンプレートを引き継ぐ
        template, embed = old_bytes, True
    elif old_rel:
        template = os.path.normpath(os.path.join(old_dir, old_rel))
    else:
        template = _template_arg(args, definition)
    export_script(template, definition, args.output, embed=embed,
                  template_name=os.path.basename(old_rel) if isinstance(template, bytes) else None)
    print(f"書き出しました: {args.output}")
    print(f"  使い方: uv run {os.path.basename(args.output)} --data data.yaml -o out.xlsx")
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
    if args.output:
        dump_structured(data, args.output)
        print(f"取り出しました: {args.output}")
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    return 0


def add_subcommands(sub) -> None:
    a = sub.add_parser("analyze", help="テンプレートを解析して表構造の定義ファイル（下書き）を作る")
    a.add_argument("template", help="テンプレート .xlsx")
    a.add_argument("-o", "--output", help="定義ファイルの出力先（.json / .yaml。省略時は <テンプレート>.def.json）")
    a.set_defaults(func=cmd_analyze)
    c = sub.add_parser("check", help="定義の検査（整合・strict の漏れ）。雛形データで試しに再構成する")
    c.add_argument("--template", help="テンプレート .xlsx（省略時は定義ファイルの template）")
    c.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    c.set_defaults(func=cmd_check)
    i = sub.add_parser("inspect", help="テンプレートの事実（値・数式・書式の種類・結合・仮値の疑い）を、判断用に出す")
    i.add_argument("template", help="テンプレート .xlsx")
    i.add_argument("--json", action="store_true", help="JSON で出す（すべての行）")
    i.add_argument("--all", action="store_true", help="同じ書式が長く続く行も、省略せずにすべて出す")
    i.set_defaults(func=cmd_inspect)
    r = sub.add_parser("render", help="テンプレート + 定義 + データから xlsx を再構成する")
    r.add_argument("--template", help="テンプレート .xlsx（省略時は定義ファイルの template）")
    r.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    r.add_argument("--data", required=True, help="データ（.json / .yaml。- で標準入力）")
    r.add_argument("-o", "--output", required=True, help="出力 .xlsx")
    r.set_defaults(func=cmd_render)
    e = sub.add_parser("export", help="この文書専用の、単体で動く render スクリプトを書き出す")
    e.add_argument("--template", help="テンプレート .xlsx（省略時は定義ファイルの template）")
    e.add_argument("--def", dest="definition", help="確定した定義ファイル（.json / .yaml）。--from-script と併用すると、その定義を置き換える")
    e.add_argument("--from-script", help="以前に export したスクリプト。定義・テンプレートを引き継いで、最新のエンジンで書き出し直す")
    e.add_argument("-o", "--output", required=True, help="書き出す .py")
    e.add_argument("--embed", action="store_true", help="テンプレートもスクリプトに埋め込む（既定は別ファイルを相対パスで参照）")
    e.set_defaults(func=cmd_export)
    x = sub.add_parser("extract", help="記入済みの文書から、定義に沿ってデータ（.json / .yaml）を取り出す（render の逆）")
    x.add_argument("source", help="記入済みの .xlsx（テンプレートと同じ形の文書）")
    x.add_argument("--def", dest="definition", required=True, help="定義ファイル（.json / .yaml）")
    x.add_argument("--template", help="テンプレート .xlsx（省略時は定義ファイルの template。表の終わりを見分けるのに使う）")
    x.add_argument("-o", "--output", help="データの出力先（.json / .yaml。省略時は標準出力に JSON）")
    x.set_defaults(func=cmd_extract)


def main() -> int:
    parser = argparse.ArgumentParser(description="xlsx テンプレートへデータを流し込む")
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
