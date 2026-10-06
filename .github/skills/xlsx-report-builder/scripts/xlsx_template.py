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


def _is_date_code(code: str) -> bool:
    stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
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
            a = f"{first.group(2)}{rowmap.map(int(first.group(4)), False)}"
            b = f"{last.group(2)}{rowmap.map(int(last.group(4)), True)}"
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
    return {
        "version": DEF_VERSION,
        "template": posixpath.basename(template),
        "sheets": sheets_def,
    }


def _grid(root, sst):
    grid: dict[int, dict[int, dict]] = {}
    for row in root.find(q("sheetData")):
        r = int(row.get("r"))
        cols = {}
        for c in row:
            f = c.find(q("f"))
            cols[cell_col(c)] = {
                "ref": c.get("r"),
                "s": c.get("s", "0"),
                "value": cell_value(c, sst),
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
            if len(body_cols) * 2 >= len(span) and (emph or (same_style and len(span) >= 3 and differs)) \
                    and not _is_total_row(nxt):
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
    grid = _grid(root, sst)
    found = _find_tables(grid, styles)
    notes: list[str] = []
    tables = []
    covered: set[int] = set()
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
            else:
                spec["key"] = _unique_key(spec["header"], letter, cols_def)
                spec["_sample"] = next((s["value"] for s in samples if s["value"] is not None), None)
            if samples:
                spec["_format"] = styles.code.get(int(samples[0]["s"]), "General")
            cols_def[letter] = spec
        for r in [t["header_row"]] + t["body"]:
            covered.add(r)
        if t["total_row"]:
            covered.add(t["total_row"])
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
    candidates = _candidates(grid, covered)
    notes += _sheet_warnings(pkg, part, root, tables)
    return {"name": name, "tables": tables, "cells": {}, "_candidates": candidates, "_notes": notes}


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


def _candidates(grid, covered: set[int]) -> list[dict]:
    out = []
    for r in sorted(grid):
        if r in covered:
            continue
        for c in sorted(grid[r]):
            i = grid[r][c]
            if i["value"] is None or i["formula"]:
                continue
            label = None
            left = grid[r].get(c - 1)
            if left and isinstance(left["value"], str):
                label = left["value"]
            out.append({"ref": i["ref"], "value": i["value"], "label": label})
    return out


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
                w.append(f"サンプル行内の複数行にまたがる結合セル {mc.get('ref')} は複製できず、取り除かれる")
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
                lines.append(f"      {letter}列 「{c.get('header')}」 → {kind}  [{c.get('_format', '')}]")
            if t["_total_row"]:
                lines.append(f"      合計行: {t['_total_row']} 行（表の下へずらし、SUM の範囲は表に合わせて伸ばす）")
            for n in t["needs_confirm"]:
                lines.append(f"      ? {n}")
        if s["_candidates"]:
            lines.append("  表の外の値（流し込む欄なら cells に ref とデータのキーを足す）:")
            for c in s["_candidates"][:30]:
                lab = f"（左隣: {c['label']}）" if c["label"] else ""
                lines.append(f"      {c['ref']} = {c['value']!r} {lab}")
        for n in s["_notes"]:
            lines.append(f"  ! {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# inspect（テンプレートの事実だけを、判断する側（LLM・人）が読める形で出す）
# ---------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"(〇〇|○○|●●|◯◯|△△|□□|＊＊|\*\*|xxx|サンプル|ダミー|仮|例[:：)）]|sample|dummy|yyyy|\bTBD\b)", re.I)
NOTE_RE = re.compile(r"^\s*(※|＊|\*|注[:：）)]|備考|Note)")


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
        grid = _grid(root, sst)
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
    return {"style_legend": legend, "sheets": sheets}


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


def format_facts(facts: dict) -> str:
    L = ["書式の種類（S0〜: 同じ見た目は同じ番号）:"]
    L += [f"  {k} = {v}" for k, v in sorted(facts["style_legend"].items(), key=lambda kv: int(kv[0][1:]))]
    for sh in facts["sheets"]:
        L.append(f"\n■ シート「{sh['name']}」")
        for key, label in (("merges", "結合"), ("conditional_formats", "条件付き書式"), ("validations", "入力規則")):
            if sh[key]:
                L.append(f"  {label}: {' '.join(sh[key])}")
        L.append("  行（値/数式 [書式の種類]）:")
        for row in sh["rows"]:
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


def set_value(c, value: Any, styles: Styles) -> None:
    for child in list(c):
        if child.tag != q("f"):
            c.remove(child)
    c.attrib.pop("t", None)
    if value is None:
        return
    if isinstance(value, bool):
        c.set("t", "b")
        etree.SubElement(c, q("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        etree.SubElement(c, q("v")).text = repr(value)
    else:
        text = str(value)
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
    warnings: list[str] = []

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
            _render_sheet(pkg, part, root, plans[name], maps[name], rw, data, styles, warnings)
        elif maps:
            for f in root.iter(q("f")):
                if f.text and "!" in f.text:
                    f.text = rw.formula(f.text)
        pkg.put_xml(part, root)

    # --- 3. ブック全体の参照（定義名・グラフ）と再計算の設定 --------------------
    _fix_workbook(pkg, maps)
    pkg.save(output)
    return warnings


def _render_sheet(pkg, part, root, plan, rowmap, rw, data, styles, warnings) -> None:
    sd, tables = plan["def"], plan["tables"]
    expand_shared_formulas(root)
    sheet_data = root.find(q("sheetData"))
    orig = sheet_rows(root)
    tbl_of: dict[int, dict] = {}
    for t in tables:
        for r in range(t["first"], t["end"] + 1):
            tbl_of[r] = t

    fixed_cells = {}
    for ref, key in (sd.get("cells") or {}).items():
        col, r = split_ref(ref)
        if r in tbl_of:
            raise TemplateError(f"cells の {ref} は表のサンプル行の中です")
        try:
            fixed_cells[(col, r)] = dig(data, key)
        except KeyError:
            raise TemplateError(f"データに {key!r} がありません（cells の {ref}）")

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
                set_value(_get_or_make_cell(row, col, new_r), value, styles)
        _strip_cached(row)
        new_rows.append(row)

    done_tables: set[int] = set()
    for r in sorted(set(orig) | {rr for _, rr in fixed_cells}):
        if r in tbl_of:
            t = tbl_of[r]
            if id(t) in done_tables:
                continue
            done_tables.add(id(t))
            _emit_table(t, orig, rowmap, rw, styles, new_rows, pattern_map, warnings)
            continue
        if r in orig:
            emit_fixed(r, orig[r])
        else:  # 行そのものが無い固定セルへの書き込み
            emit_fixed(r, etree.Element(q("row"), r=str(r)))
    # 行そのものが無い表（サンプル行が未作成）にも対応
    for t in tables:
        if id(t) not in done_tables:
            _emit_table(t, orig, rowmap, rw, styles, new_rows, pattern_map, warnings)
    new_rows.sort(key=lambda x: int(x.get("r")))

    for child in list(sheet_data):
        sheet_data.remove(child)
    sheet_data.extend(new_rows)

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


def _emit_table(t, orig, rowmap, rw, styles, out_rows, pattern_map, warnings) -> None:
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
                    value = row_data.get(key)
                else:
                    raise TemplateError(f"{td['key']!r} の要素はオブジェクトである必要があります")
                if isinstance(value, (dict, list)):
                    raise TemplateError(f"{td['key']}[{rec}].{key} に配列・オブジェクトは入れられません")
                set_value(c, value, styles)
            _strip_cached(row)
            out_rows.append(row)


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
                if r1 == r2:
                    for target in pattern_map.get(r1, []):
                        keep.append(f"{get_column_letter(c1)}{target}:{get_column_letter(c2)}{target}")
                else:
                    warnings.append(f"複数行にまたがる結合セル {m.get('ref')} は表のサンプル行内のため取り除きました")
                continue
            na = f"{get_column_letter(c1)}{rowmap.map(r1, False)}"
            nb = f"{get_column_letter(c2)}{rowmap.map(r2, True)}"
            keep.append(f"{na}:{nb}")
        for ref in keep:
            etree.SubElement(mc, q("mergeCell"), ref=ref)
        mc.set("count", str(len(keep)))
        if not keep:
            root.remove(mc)

    # 範囲を持つ要素
    for tag in ("conditionalFormatting", "dataValidation", "ignoredError"):
        for el in root.iter(q(tag)):
            if el.get("sqref"):
                el.set("sqref", rw.sqref(el.get("sqref")))
    for el in root.iter(q("hyperlink")):
        el.set("ref", rw.sqref(el.get("ref")))
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
        pkg.removed.add("xl/calcChain.xml")
        rels = pkg.xml("xl/_rels/workbook.xml.rels")
        for r in list(rels):
            if r.get("Target", "").endswith("calcChain.xml"):
                rels.remove(r)
        pkg.put_xml("xl/_rels/workbook.xml.rels", rels)
        ct = pkg.xml("[Content_Types].xml")
        for o in list(ct):
            if o.get("PartName", "").endswith("calcChain.xml"):
                ct.remove(o)
        pkg.put_xml("[Content_Types].xml", ct)


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
        for key in (sd.get("cells") or {}).values():
            _set_path(out, key, None)
        for t in sd.get("tables", []):
            colmaps = t["block"] if t.get("block_rows", 1) > 1 and t.get("block") else [t.get("columns")]
            row = {c["key"]: None for cols in colmaps for c in (cols or {}).values()
                   if c.get("key") and c["key"] != "$index" and not c.get("keep") and not c.get("clear")}
            _set_path(out, t["key"], [row])
    return out


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


# ---------------------------------------------------------------------------
# スタンドアローン（固有の render スクリプトを書き出す）
# ---------------------------------------------------------------------------

STANDALONE_HEADER = '''#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["lxml", "openpyxl", "pyyaml"]
# ///
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
    validate_definition(raw, definition)
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        engine = f.read()
    # エンジン本体（CLI の入口より前）だけを取り込む
    engine = engine.split('\nif __name__ == "__main__":')[0]
    engine = engine.split("\n", 1)[1] if engine.startswith("#!") else engine
    # docstring を 2 つ持てないため、エンジンの docstring は取り除く
    engine = re.sub(r'^"""[\s\S]*?"""\n', "", engine, count=1)
    shape = "\n".join("    " + line for line in
                       json.dumps(skeleton_data(definition), ensure_ascii=False, indent=2).splitlines())
    title = f"{os.path.splitext(template_name or 'template')[0]} の render スクリプト"
    note = ("テンプレートも埋め込み済み（--template で差し替えられる）。" if embed
            else f"テンプレート（{rel}）は、このスクリプトからの相対パスで読む。")
    header = STANDALONE_HEADER.format(title=title, name=os.path.basename(output), shape=shape, template_note=note)
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_analyze(args) -> int:
    definition = analyze(args.template)
    out = args.output or re.sub(r"\.xlsx?$", "", args.template, flags=re.I) + ".def.json"
    dump_structured(definition, out)
    print(summarize(definition))
    print(f"\n定義ファイルの下書きを書きました: {out}")
    print("（? の項目をユーザーに確認してから、定義ファイルを直して確定する）")
    return 0


def cmd_inspect(args) -> int:
    facts = inspect_template(args.template)
    if args.json:
        json.dump(facts, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(format_facts(facts))
    return 0


def _template_arg(args, definition) -> str:
    template = args.template or definition.get("template")
    if not template:
        raise TemplateError("--template か定義ファイルの template が必要です")
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


def add_subcommands(sub) -> None:
    a = sub.add_parser("analyze", help="テンプレートを解析して表構造の定義ファイル（下書き）を作る")
    a.add_argument("template", help="テンプレート .xlsx")
    a.add_argument("-o", "--output", help="定義ファイルの出力先（.json / .yaml。省略時は <テンプレート>.def.json）")
    a.set_defaults(func=cmd_analyze)
    i = sub.add_parser("inspect", help="テンプレートの事実（値・数式・書式の種類・結合・仮値の疑い）を、判断用に出す")
    i.add_argument("template", help="テンプレート .xlsx")
    i.add_argument("--json", action="store_true", help="JSON で出す")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="xlsx テンプレートへデータを流し込む")
    add_subcommands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args()
    try:
        return args.func(args)
    except TemplateError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
