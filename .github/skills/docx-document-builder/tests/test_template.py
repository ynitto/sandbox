"""docx_template（inspect / analyze / check / render / extract / export）のテスト。

テンプレートは tests/fixtures.py で組み立てる（build の骨組み + ラベルの太字・コメント・ヘッダー）。
実行: cd .github/skills/docx-document-builder && uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from docx.oxml.ns import qn
from lxml import etree

import docx_builder as db
import docx_template as dt
import fixtures

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

DATA = {
    # 表題の年度・四半期、日付、件名は文書の値（表題の「業務報告書」は残る）
    "文書": {"年度": 2026, "四半期": 3, "日付": "2026-10-08", "件名": "基幹システムの定例報告"},
    "表紙": {"宛先": "北斗製薬株式会社 御中"},
    "概要": {"body": "夜間バッチの遅延が 2 回あった。\nどちらも翌朝に復旧した。\n\n来月から監視を 5 分間隔に変える。"},
    "施策": [
        {"title": "監視の強化", "目的": "遅延に早く気づく", "担当": "小林", "points": ["間隔を決める", "通知先を決める", "試す"]},
        {"title": "ログの保管", "目的": "調査の期間を延ばす", "担当": "佐藤", "points": ["容量を見積もる", {"text": "費用を出す", "level": 1}]},
        {"title": "停止計画", "目的": "年末の停止を周知する", "担当": "鈴木", "points": ["日程を決める"]},
    ],
    "進捗": {"rows": [{"作業": "原因の調査", "担当": "小林", "完了": True}, {"作業": "監視の変更", "担当": "佐藤", "完了": False}]},
    "連絡先": {"担当部署": "運用チーム", "電話": "03-1234-5678"},
}


def texts(path: str) -> list[str]:
    view = dt.DocView(dt.open_doc(path))
    return [dt.literal_text(el) for el in view.blocks]


def blocks(path: str):
    view = dt.DocView(dt.open_doc(path))
    return view, view.blocks


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.template = os.path.join(self.dir, "報告.docx")
        fixtures.make_template(self.template)
        self.definition = dt.analyze(self.template)
        self.definition["template"] = "報告.docx"

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def render(self, data=None, definition=None, **kw) -> str:
        out = self.path("out.docx")
        self.warnings = dt.render(self.template, definition or self.definition, deepcopy(DATA if data is None else data), out, **kw)
        return out


class InspectTest(Base):
    def test_facts(self):
        facts = dt.inspect_template(self.template)
        first = facts["blocks"][0]
        self.assertEqual(first["style"], "Title")
        self.assertEqual(first["font"], "游ゴシック")
        self.assertEqual(first["size"], 24)
        self.assertEqual(facts["base"], {"font": "游明朝", "size": 10.5})
        self.assertTrue(facts["blocks"][2]["placeholder_suspect"])      # サンプル株式会社
        self.assertEqual(facts["blocks"][5]["line_breaks"], 1)          # 段落の中の改行
        self.assertEqual([o["text"] for o in facts["outline"]][:3], ["1. 概要", "2. 施策", "2.1 確認の自動化"])
        self.assertEqual(facts["repeat_candidates"][0]["samples"], ["#9-#13", "#14-#19"])
        prov = "\n".join(facts["provenance"])
        for word in ("前任者", "コメント 1 件", "サンプル株式会社 社外秘"):
            self.assertIn(word, prov)
        text = dt.format_facts(facts)
        self.assertIn("章の構成", text)
        self.assertIn("1 行 約 43 字", text)


class AnalyzeTest(Base):
    def part(self, pid):
        return next(p for p in self.definition["parts"] if p["id"] == pid)

    def test_parts(self):
        d = self.definition
        self.assertTrue(d["strict"])
        self.assertEqual(d["properties"], {"scrub": True})
        cover = self.part("p1")
        self.assertEqual(cover["key"], "表紙")
        self.assertEqual([s.get("key", s.get("text")) for s in cover["texts"].values()],
                         ["{文書.年度}年度 第{文書.四半期}四半期 業務報告書", "{文書.日付:%Y年%-m月%-d日}", "宛先", "文書.件名"])
        self.assertEqual(cover["texts"]["#4"]["after"], "件名：")
        self.assertEqual(self.part("p2")["texts"]["#6-#7"]["key"], "body")
        self.assertEqual(self.part("p2")["keep"], ["#5"])

    def test_repeat_sections(self):
        rep = self.part("p4")
        self.assertTrue(rep["repeat"])
        self.assertEqual(rep["key"], "施策")
        self.assertEqual(rep["blocks"], "#9-#13")
        self.assertEqual(rep["texts"]["#9"]["format"], "2.{n} {}")
        self.assertEqual(rep["texts"]["#10"]["after"], "目的：")
        self.assertEqual(rep["lists"]["#12-#13"]["max_items"], 5)    # サンプルの多い方（3 項目）の 1.5 倍
        self.assertTrue(self.part("p5")["drop"])
        self.assertEqual(rep["group"], "施策")

    def test_tables(self):
        t = self.part("p6")["tables"][0]
        self.assertEqual(t["columns"][0]["key"], "$index")
        self.assertEqual(t["columns"][3]["map"], {True: "○", False: "×"})
        self.assertEqual(t["columns"][4], {"clear": True, "header": "確認印"})
        form = self.part("p7")["tables"][0]
        self.assertEqual(sorted(s["key"] for s in form["cells"].values()), ["担当部署", "電話"])

    def test_check(self):
        warnings = dt.check_definition(self.template, self.definition)
        self.assertTrue(any("ヘッダー" in w for w in warnings))
        notes = dt.value_notes(self.definition)
        self.assertIn("施策[].points[]: 最大 5 項目・1 項目 12 字まで", notes)


class RenderTest(Base):
    def test_text_and_style(self):
        out = self.render()
        view, els = blocks(out)
        t = [dt.literal_text(e) for e in els]
        self.assertEqual(t[0], "2026年度 第3四半期 業務報告書")
        self.assertEqual(view.style_name(view.style_of(els[0])), "Title")
        self.assertEqual(view.font_name(els[0]), "游ゴシック")
        self.assertEqual(t[3], "件名：基幹システムの定例報告")
        runs = dt.text_runs(els[3])
        self.assertEqual(dt.run_text(runs[0]), "件名：")
        self.assertIsNotNone(runs[0].find(f"{qn('w:rPr')}/{qn('w:b')}"))   # ラベルの太字は残る
        self.assertIsNone(runs[1].find(f"{qn('w:rPr')}/{qn('w:b')}"))
        self.assertEqual(t[5], "夜間バッチの遅延が 2 回あった。\nどちらも翌朝に復旧した。")   # 段落の中の改行
        self.assertEqual(len(list(els[5].iter(qn("w:br")))), 1)
        self.assertEqual(t[6], "来月から監視を 5 分間隔に変える。")
        self.assertEqual(view.style_of(els[6]), view.style_of(els[5]))

    def test_repeat_and_numbering(self):
        out = self.render()
        view, els = blocks(out)
        t = [dt.literal_text(e) for e in els]
        heads = [x for x, e in zip(t, els) if view.heading_level(e) == 2]
        self.assertEqual(heads, ["2.1 監視の強化", "2.2 ログの保管", "2.3 停止計画"])
        self.assertNotIn("確認の自動化", "".join(t))       # 見本の節は残らない
        self.assertNotIn("様式の統一", "".join(t))
        lists = [e for e in els if view.is_list(e)]
        self.assertEqual([dt.literal_text(e) for e in lists], ["間隔を決める", "通知先を決める", "試す", "容量を見積もる",
                                                              "費用を出す", "日程を決める"])
        self.assertEqual(view.list_level(lists[4]), 1)
        ids = [view.numpr(e)[0] for e in lists]
        self.assertEqual(len({ids[0], ids[3], ids[5]}), 3)   # 節ごとに番号を 1 から振り直す
        self.assertEqual(ids[3], ids[4])

    def test_tables(self):
        out = self.render()
        view, els = blocks(out)
        tbls = [e for e in els if dt.local(e) == "tbl"]
        self.assertEqual(dt.table_grid(tbls[0]), [["No", "作業", "担当", "完了", "確認印"],
                                                   ["1", "原因の調査", "小林", "○", ""], ["2", "監視の変更", "佐藤", "×", ""]])
        self.assertEqual(dt.table_grid(tbls[1]), [["担当部署", "運用チーム"], ["電話", "03-1234-5678"]])
        rpr = dt.text_runs(tbls[0].findall(qn("w:tr"))[1].findall(qn("w:tc"))[1].find(qn("w:p")))[0].find(qn("w:rPr"))
        self.assertTrue(rpr is None or rpr.find(qn("w:color")) is None)   # 見出しの行の白い文字を借りない

    def test_scrub(self):
        out = self.render()
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            doc = z.read("word/document.xml").decode()
            core = z.read("docProps/core.xml").decode()
        self.assertFalse(any("comments" in n for n in names))
        self.assertNotIn("commentReference", doc)
        self.assertNotIn("前任者", core)
        self.assertNotIn("docProps/thumbnail.jpeg", names)
        self.assertTrue(any("コメント 1 件" in w for w in self.warnings))
        self.assertIn("サンプル株式会社 社外秘", "\n".join(dt.provenance(dt.read_bytes(out))))   # ヘッダーは書き換えない

    def test_overflow(self):
        data = deepcopy(DATA)
        data["文書"]["四半期"] = "3（基幹システム・改訂版）"
        data["概要"]["body"] = "一\n\n二\n\n三\n\n四"
        data["施策"][0]["points"] = list("abcdef")
        with self.assertRaises(dt.TemplateError) as cm:
            self.render(data)
        msg = str(cm.exception)
        self.assertIn("文書.年度・文書.四半期（表紙 の #1）: 30 字。サンプルの粒度は 18 字まで（12 字減らす）", msg)
        self.assertIn("概要.body（#6-#7）: 4 段落。サンプルの粒度は 3 段落まで", msg)
        self.assertIn("施策[0].points（#12-#13）: 6 項目", msg)
        self.render(data, allow_overflow=True)
        self.assertTrue(any("収まらない値" in w for w in self.warnings))

    def test_strict_leftover(self):
        d = deepcopy(self.definition)
        del d["parts"][0]["texts"]["#3"]
        with self.assertRaises(dt.TemplateError) as cm:
            self.render(definition=d)
        self.assertIn("#3（段落）: 「サンプル株式会社 御中」", str(cm.exception))

    def test_empty_repeat_and_unknown_keys(self):
        data = deepcopy(DATA)
        data["施策"] = []
        data["概要"]["本文"] = "綴りの違い"
        out = self.render(data)
        t = "\n".join(texts(out))
        self.assertNotIn("目的：", t)
        self.assertIn("2. 施策", t)
        self.assertTrue(any("本文" in w for w in self.warnings))

    def test_after_must_match(self):
        d = deepcopy(self.definition)
        d["parts"][0]["texts"]["#4"]["after"] = "題名："
        with self.assertRaises(dt.TemplateError) as cm:
            dt.validate_definition(self.template, d)
        self.assertIn("after「題名：」", str(cm.exception))

    def test_max_pages(self):
        d = deepcopy(self.definition)
        d["max_pages"] = 1
        data = deepcopy(DATA)
        data["施策"] = data["施策"] * 8
        with self.assertRaises(dt.TemplateError) as cm:
            self.render(data, definition=d)
        self.assertIn("max_pages は 1 ページ", str(cm.exception))


class ExtractTest(Base):
    def test_round_trip(self):
        out = self.render()
        data, notes = dt.extract(out, self.definition, self.template)
        self.assertEqual(data, DATA)

    def test_template_itself(self):
        data, _ = dt.extract(self.template, self.definition, self.template)
        self.assertEqual([x["title"] for x in data["施策"]], ["確認の自動化", "様式の統一"])   # 取り除く見本も 1 件として読む
        self.assertEqual(data["概要"]["body"], "今期は夜間の処理が 2 回遅れた。\n詳しくは別紙を参照。\n\n来期は監視の間隔を 5 分にする。")
        self.assertEqual(data["連絡先"], {"担当部署": "情報システム部", "電話": "03-0000-0000"})

    def test_mismatch(self):
        out = self.render()
        doc = dt.open_doc(out)
        doc.add_table(rows=1, cols=7)
        doc.save(out)
        with self.assertRaises(dt.TemplateError) as cm:
            dt.extract(out, self.definition, self.template)
        self.assertIn("テンプレートと合いません", str(cm.exception))

    def test_split_and_merge(self):
        parts = dt.split_data(DATA, self.definition)
        self.assertEqual([n for n, _ in parts], ["00-common", "01-表紙", "02-概要", "03-施策", "04-進捗", "05-連絡先"])
        self.assertEqual(dt.merge_data(parts), DATA)
        with self.assertRaises(dt.TemplateError):
            dt.merge_data([("a", {"表紙": {"title": "A"}}), ("b", {"表紙": {"title": "B"}})])


class ExportTest(Base):
    def test_standalone(self):
        def_path = self.path("def.yaml")
        dt.dump_structured(self.definition, def_path)
        data_path = self.path("data.yaml")
        dt.dump_structured(DATA, data_path)
        script = self.path("render_report.py")
        dt.export_script(self.template, self.definition, script)
        out = self.path("standalone.docx")
        r = subprocess.run([sys.executable, script, "--data", data_path, "-o", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(texts(out), texts(self.render()))
        r = subprocess.run([sys.executable, script, "--example-data"], capture_output=True, text=True)
        self.assertIn("施策:", r.stdout)
        self.assertIn("# 施策[].points[]", r.stdout)
        definition, embedded, rel = dt.read_exported_script(script)
        self.assertEqual(rel, "報告.docx")
        self.assertIsNone(embedded)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "docx_builder.py"), "export", "--from-script", script,
                            "-o", self.path("again.py"), "--embed"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(dt.read_exported_script(self.path("again.py"))[1])


class WordFeaturesTest(unittest.TestCase):
    """コンテンツコントロール・目次・変更履歴・見出しの自動の番号。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbered_headings_repeat(self):
        path = os.path.join(self.dir, "t.docx")
        db.build({"filename": path, "blocks": [
            {"type": "heading", "text": "案件", "level": 1},
            {"type": "heading", "text": "案件 A", "level": 2}, {"type": "paragraph", "text": "状況：進行中"},
            {"type": "heading", "text": "案件 B", "level": 2}, {"type": "paragraph", "text": "状況：完了"}]})
        d = dt.analyze(path)
        rep = next(p for p in d["parts"] if p.get("repeat"))
        self.assertEqual(rep["key"], "案件")
        self.assertNotIn("format", rep["texts"]["#2"])   # 番号は見出しのスタイルが振る
        out = os.path.join(self.dir, "o.docx")
        dt.render(path, d, {"案件": [{"title": "X", "状況": "a"}, {"title": "Y", "状況": "b"}, {"title": "Z", "状況": "c"}]}, out)
        view = dt.DocView(dt.open_doc(out))
        heads = [e for e in view.blocks if view.heading_level(e) == 2]
        self.assertEqual([dt.literal_text(e) for e in heads], ["X", "Y", "Z"])
        self.assertTrue(all(view.numpr(e) for e in heads))   # 章の番号（2.1 …）はスタイルの番号のまま続く

    def test_content_control_toc_and_revisions(self):
        path = os.path.join(self.dir, "t.docx")
        db.build({"filename": path, "blocks": [{"type": "heading", "text": "申請", "level": 1},
                                               {"type": "paragraph", "text": "氏名を書く"}, {"type": "paragraph", "text": "本文"}]})
        doc = dt.open_doc(path)
        body = doc.element.body
        ps = body.findall(qn("w:p"))
        # 2 つめの段落をコンテンツコントロールで包む
        sdt = etree.SubElement(body, qn("w:sdt"))
        pr = etree.SubElement(sdt, qn("w:sdtPr"))
        etree.SubElement(pr, qn("w:alias")).set(qn("w:val"), "氏名")
        etree.SubElement(pr, qn("w:showingPlcHdr"))
        content = etree.SubElement(sdt, qn("w:sdtContent"))
        ps[1].addprevious(sdt)
        content.append(ps[1])
        # 目次のフィールドと、変更履歴（挿入）
        p = ps[2]
        r = p.find(qn("w:r"))
        ins = etree.Element(qn("w:ins"))
        ins.set(qn("w:id"), "1")
        ins.set(qn("w:author"), "前任者")
        r.addprevious(ins)
        ins.append(r)
        toc = etree.SubElement(body, qn("w:p"))
        for kind in ("begin",):
            fr = etree.SubElement(toc, qn("w:r"))
            etree.SubElement(fr, qn("w:fldChar")).set(qn("w:fldCharType"), kind)
        ir = etree.SubElement(toc, qn("w:r"))
        etree.SubElement(ir, qn("w:instrText")).text = ' TOC \\o "1-3" '
        er = etree.SubElement(toc, qn("w:r"))
        etree.SubElement(er, qn("w:fldChar")).set(qn("w:fldCharType"), "end")
        body.remove(toc)
        ps[0].addprevious(toc)
        doc.save(path)
        prov = "\n".join(dt.provenance(dt.read_bytes(path)))
        self.assertIn("変更履歴", prov)
        self.assertIn("目次", prov)
        d = dt.analyze(path)
        part = next(p for p in d["parts"] if p.get("texts"))
        self.assertIn({"key": "氏名", "max_items": 2}, list(part["texts"].values()))   # 説明の文字の字数は上限にしない
        out = os.path.join(self.dir, "o.docx")
        key = part["key"]
        warnings = dt.render(path, d, {key: {"氏名": "山田 花子", "body": "新しい本文"}}, out)
        with zipfile.ZipFile(out) as z:
            xml = z.read("word/document.xml").decode()
            settings = z.read("word/settings.xml").decode()
        self.assertIn("山田 花子", xml)
        self.assertNotIn("showingPlcHdr", xml)
        self.assertNotIn("前任者", xml)                      # 変更履歴は承諾し、作成者を残さない
        self.assertIn("updateFields", settings)
        self.assertTrue(any("目次" in w for w in warnings))
        data, _ = dt.extract(out, d, path)
        self.assertEqual(data[key]["氏名"], "山田 花子")
        dt.render(path, d, {key: {"氏名": None, "body": "新しい本文"}}, out)   # 値が無ければ、説明の文字のまま
        with zipfile.ZipFile(out) as z:
            self.assertIn("showingPlcHdr", z.read("word/document.xml").decode())
        self.assertIsNone(dt.extract(out, d, path)[0][key].get("氏名"))


class FormTest(unittest.TestCase):
    """様式（表題・日付・印の欄）と、仮の値・高さの決まった行の上限。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "申請.docx")
        db.build({"filename": self.path, "blocks": [
            {"type": "title", "text": "備品購入申請書"},
            {"type": "paragraph", "text": "yyyy年mm月dd日"},
            {"type": "paragraph", "text": "内訳"},
            {"type": "table", "columns": ["品名", "作業"], "rows": [["〇〇", "請求書"], ["ノート", "確認"]]},
            {"type": "table", "columns": ["承認印", "確認印"], "rows": [["", ""]]},
        ]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze(self):
        d = dt.analyze(self.path)
        part = d["parts"][0]
        self.assertEqual(part["texts"]["#2"]["key"], "日付")              # yyyy年mm月dd日 も日付
        self.assertIn("#1", part["keep"])                                 # 様式の名前は keep
        self.assertIn("#3", part["keep"])                                 # 表のすぐ前の短い段落は欄の名前
        self.assertEqual([t["block"] for t in part["tables"]], ["#4"])  # 印の欄だけの表は keep
        cols = part["tables"][0]["columns"]
        view = dt.DocView(dt.open_doc(self.path))
        tc = dt._tcs(dt._table_rows(view.blocks[3])[1])[1]
        cpl = int(view.capacity(tc.find(qn("w:p"))).cpl)
        self.assertEqual(cols[1]["max_chars"], min(7, cpl))                # 列の幅で 1 行に収める
        self.assertEqual(cols[0]["max_chars"], int(view.capacity(dt._tcs(dt._table_rows(view.blocks[3])[1])[0]
                                                                .find(qn("w:p"))).cpl))   # 仮の値（〇〇）は 1 行ぶん

    def test_exact_height_cell(self):
        doc = dt.open_doc(self.path)
        tbl = dt.DocView(doc).blocks[3]
        tr = dt._table_rows(tbl)[1]
        trpr = tr.find(qn("w:trPr"))
        if trpr is None:
            trpr = etree.Element(qn("w:trPr"))
            tr.insert(0, trpr)
        h = etree.SubElement(trpr, qn("w:trHeight"))
        h.set(qn("w:val"), "1200")
        h.set(qn("w:hRule"), "exact")
        doc.save(self.path)
        view = dt.DocView(dt.open_doc(self.path))
        tr = dt._table_rows(view.blocks[3])[1]
        cap, lines = dt._exact_lines(view, tr, dt._tcs(tr)[1])
        cols = dt.analyze(self.path)["parts"][0]["tables"][0]["columns"]
        self.assertGreater(lines, 1)
        self.assertEqual(cols[1]["max_chars"], int(cap.cpl * lines))     # 入る行数ぶん

    def test_missing_keys(self):
        d = dt.analyze(self.path)
        out = os.path.join(self.tmp.name, "o.docx")
        w = dt.render(self.path, d, {"表紙": {"rows": []}}, out)
        self.assertTrue(any("日付" in x and "データに無い" in x for x in w))
        w = dt.render(self.path, d, {"表紙": {"日付": None, "rows": []}}, out)
        self.assertFalse(any("データに無い" in x for x in w))
        self.assertIn("備品購入申請書", texts(out))

    def test_inspect_base_font(self):
        doc = dt.open_doc(self.path)
        p = dt.DocView(doc).blocks[1]
        for r in p.iter(qn("w:r")):
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                rpr = etree.SubElement(r, qn("w:rPr"))
            etree.SubElement(rpr, qn("w:sz")).set(qn("w:val"), "32")
        doc.save(self.path)
        self.assertEqual(dt.inspect_template(self.path)["base"]["size"], 10.5)   # 直接の書式（16pt）ではなく既定のスタイル


class RepeatFitTest(Base):
    def test_heading_format_not_counted(self):
        spec = self.definition["parts"][3]["texts"]["#9"]
        self.assertEqual(spec["format"], "2.{n} {}")
        data = deepcopy(DATA)
        data["施策"][0]["title"] = "あ" * spec["max_chars"]   # 番号（2.1 ）は字数に数えない
        dt.render(self.template, self.definition, data, os.path.join(self.dir, "o.docx"))

    def test_missing_repeat_key(self):
        data = {k: v for k, v in DATA.items() if k != "施策"}
        with self.assertRaises(dt.TemplateError) as e:
            dt.render(self.template, self.definition, data, os.path.join(self.dir, "o.docx"))
        self.assertIn("施策: []", str(e.exception))


class DocumentValueTest(Base):
    """表題の年度・日付・件名、フッターの表題のように、データではないが文書ごとに変わる値（文書の値）。"""

    def test_one_line_title_budget_stays_on_one_line(self):
        from docx import Document
        d = Document(self.template)
        d.paragraphs[0].text = "ABCDEFGHIJ abcdefghij 報告"   # 半角が多く、字数（25）では 2 行ぶんに見える
        d.save(self.template)
        view = dt.DocView(dt.open_doc(self.template))
        cap = view.capacity(view.blocks[0])
        self.assertEqual(cap.lines_for(dt.para_text(view.blocks[0])), 1)
        spec = dt.analyze(self.template)["parts"][0]["texts"]["#1"]
        self.assertLessEqual(spec["max_chars"], 25)   # 1 行の表題は 1 行のまま（以前は 36 字まで通し、2 行に折り返した）

    def test_footer_text_is_a_document_value(self):
        from docx import Document
        d = Document(self.template)
        d.sections[0].footer.paragraphs[0].text = "2025年度 第1四半期 業務報告書"
        d.save(self.template)
        definition = dt.analyze(self.template)
        ref, tpl = next(iter(definition["header_footer"].items()))
        self.assertEqual(tpl, "{文書.年度}年度 第{文書.四半期}四半期 業務報告書")   # 表紙の表題と同じキー
        out = self.path("o.docx")
        dt.render(self.template, definition, deepcopy(DATA), out)
        self.assertEqual(Document(out).sections[0].footer.paragraphs[0].text, "2026年度 第3四半期 業務報告書")
        data, _ = dt.extract(out, definition, self.template)
        self.assertEqual(data["文書"], DATA["文書"])
        self.assertIn("文書.日付: 日付（2026-10-08）", dt.value_notes(definition))
        self.assertEqual(dt.skeleton_data(definition)["文書"], {"年度": None, "四半期": None, "日付": None, "件名": None})
        self.assertTrue([n for n in dt.value_notes(definition) if n.startswith("文書.年度:")])
        data = deepcopy(DATA)
        del data["文書"]["年度"]
        with self.assertRaises(dt.TemplateError) as cm:   # 書き忘れた文書の値は、崩れた文のまま出さずに止める
            dt.render(self.template, definition, data, out)
        self.assertIn("文書.年度", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
