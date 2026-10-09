"""テストで使うテンプレート（前の案件の値が入った報告書）を組み立てる。

build で骨組みを作り、ラベルの太字・コメント・ヘッダー・変更履歴など、Word の文書によくあるものを足す。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from docx.oxml.ns import qn
from lxml import etree

import docx_builder as db
import docx_template as dt

SPEC = {
    "numbered_headings": False,
    "style": {"font": "游明朝", "heading_font": "游ゴシック"},
    "blocks": [
        {"type": "title", "text": "2025年度 第1四半期 業務報告書"},
        {"type": "paragraph", "text": "2025年4月1日"},
        {"type": "paragraph", "text": "サンプル株式会社 御中"},
        {"type": "paragraph", "text": "件名：システム保守の定例報告"},
        {"type": "heading", "text": "1. 概要", "level": 1},
        {"type": "paragraph", "text": "今期は夜間の処理が 2 回遅れた。\n詳しくは別紙を参照。\n\n来期は監視の間隔を 5 分にする。"},
        {"type": "heading", "text": "2. 施策", "level": 1},
        {"type": "heading", "text": "2.1 確認の自動化", "level": 2},
        {"type": "paragraph", "text": "目的：確認の時間を半分にする"},
        {"type": "paragraph", "text": "担当：田中"},
        {"type": "numbered", "items": ["現状を調べる", "手順を決める"]},
        {"type": "heading", "text": "2.2 様式の統一", "level": 2},
        {"type": "paragraph", "text": "目的：差し戻しを減らす"},
        {"type": "paragraph", "text": "担当：佐藤"},
        {"type": "numbered", "items": ["様式を集める", "1 つにまとめる", "配る"]},
        {"type": "heading", "text": "3. 進捗", "level": 1},
        {"type": "table", "columns": ["No", "作業", "担当", "完了", "確認印"],
         "rows": [["1", "要件の整理", "田中", "○", ""], ["2", "設計", "佐藤", "×", ""], ["3", "試験", "鈴木", "×", ""]]},
        {"type": "paragraph", "text": "※ 完了の印は担当者が付ける。"},
        {"type": "heading", "text": "4. 連絡先", "level": 1},
        {"type": "table", "columns": ["担当部署", "情報システム部"], "rows": [["電話", "03-0000-0000"]]},
    ],
}


def _bold_label(p, label: str) -> None:
    """`目的：` の部分だけ太字の run に分ける（Word でよくある書き方）。"""
    runs = dt.text_runs(p)
    text = dt.run_text(runs[0])
    head = etree.fromstring(etree.tostring(runs[0]))
    dt._set_run_text(head, label)
    rpr = head.find(qn("w:rPr"))
    if rpr is None:
        rpr = etree.Element(qn("w:rPr"))
        head.insert(0, rpr)
    etree.SubElement(rpr, qn("w:b"))
    dt._set_run_text(runs[0], text[len(label):])
    runs[0].addprevious(head)


def make_template(path: str) -> None:
    b = db.Builder({**SPEC, "filename": path})
    doc = b.build()
    body = doc.element.body
    # 2 列の表は、見出しの塗りを外して記入欄の形にする
    tbl = body.findall(qn("w:tbl"))[1]
    for e in list(tbl.iter(qn("w:tblHeader"))):
        e.getparent().remove(e)
    for tc in tbl.findall(f"{qn('w:tr')}/{qn('w:tc')}"):
        for shd in tc.iter(qn("w:shd")):
            shd.getparent().remove(shd)
        for e in list(tc.iter(qn("w:b"), qn("w:color"))):
            e.getparent().remove(e)
    for p in body.iter(qn("w:p")):
        t = dt.para_text(p)
        for label in ("目的：", "担当：", "件名："):
            if t.startswith(label):
                _bold_label(p, label)
    # コメント・ヘッダー・作成者
    paras = [p for p in doc.paragraphs if p.text.startswith("今期は")]
    doc.add_comment(paras[0].runs, text="山田部長に先に伝える", author="前任者")
    doc.sections[0].header.paragraphs[0].text = "サンプル株式会社 社外秘"
    doc.core_properties.author = "前任者"
    doc.core_properties.title = "前の案件の報告"
    doc.save(path)
