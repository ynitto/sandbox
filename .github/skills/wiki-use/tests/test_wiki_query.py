"""wiki_query.py の検索強化（K4）— 0 件ヒット時の近傍提示の回帰テスト。

計画: docs/plans/2026-08-15-agent-tools-cross-agent-knowledge-operation-plan.md §3.5-1
採用戦略: docs/plans/2026-05-30-wiki-use-adoption-strategy.md Phase 1（RC5）

title/aliases 重み付け・トークン化・日本語正規化は既存実装（本テストの追加以前から
実装済み）。ここで足すのは「0 件ヒットでも list-pages を近傍順に提示する」の回帰だけ。

CI には未接続（このリポジトリの `.github/skills/*` は現状テスト対象外）だが、
ファイルシステムのみで完結する（ネットワーク・skill-registry.json 不要）。
実行: python3 -m unittest discover -s .github/skills/wiki-use/tests
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wq = _load("wiki_query", "wiki_query.py")


def _mk_page(wiki_root: Path, category: str, stem: str, *, title: str,
            aliases=None, body: str = "本文\n") -> Path:
    d = wiki_root / "wiki" / category
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {title}"]
    if aliases:
        fm.append("aliases:")
        fm.extend(f"  - {a}" for a in aliases)
    text = "---\n" + "\n".join(fm) + "\n---\n" + body
    p = d / f"{stem}.md"
    p.write_text(text, encoding="utf-8")
    return p


class WikiSearchNeighborTests(unittest.TestCase):
    def _args(self, keyword: str, *, as_json: bool = False, suggest: bool = False) -> Namespace:
        return Namespace(keyword=keyword, json=as_json, suggest=suggest)

    def test_zero_hit_shows_weak_matches_ranked_by_coverage(self):
        """しきい値未満の弱一致があれば、それを近傍候補として出す（list-pages 誘導だけで終わらせない）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mk_page(root, "atoms", "auth", title="認証トークンの発行手順")
            _mk_page(root, "atoms", "deploy", title="デプロイ手順書")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                wq.cmd_search(self._args("認証 デプロイ 監査ログ"), root)
            out = buf.getvalue()
            self.assertIn("マッチするページはありません", out)
            self.assertIn("近傍候補", out)
            # 単独キーワードでは採用ライン未満（0.5 未満）でも、部分一致した方が先に出る
            self.assertIn("認証トークンの発行手順", out)

    def test_zero_hit_with_no_token_overlap_falls_back_to_alphabetical(self):
        """トークンが 1 つも重ならなければ、タイトルのアルファベット順で近傍を出す。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mk_page(root, "atoms", "b", title="Bravo")
            _mk_page(root, "atoms", "a", title="Alpha")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                wq.cmd_search(self._args("zzz9999"), root)
            out = buf.getvalue()
            self.assertIn("手がかり無し", out)
            alpha_pos = out.index("Alpha")
            bravo_pos = out.index("Bravo")
            self.assertLess(alpha_pos, bravo_pos, "アルファベット順（Alpha が先）で並ぶこと")

    def test_zero_hit_json_includes_neighbors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mk_page(root, "atoms", "auth", title="認証トークンの発行手順")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                wq.cmd_search(self._args("認証 デプロイ 監査ログ", as_json=True), root)
            out = json.loads(buf.getvalue())
            self.assertEqual(out["count"], 0)
            self.assertTrue(out["neighbors"], "JSON でも近傍候補を返す")

    def test_hit_still_returned_normally_when_present(self):
        """既存の一致（重み付けスコア）は近傍提示の追加で壊れない。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mk_page(root, "atoms", "auth", title="認証トークン", aliases=["JWT"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                wq.cmd_search(self._args("JWT"), root)
            out = buf.getvalue()
            self.assertIn("完全一致", out)
            self.assertNotIn("近傍候補", out)


class WikiSearchGraphTests(unittest.TestCase):
    """graphify のグラフでつながるページを足す。graphify かグラフが無ければ従来どおり。"""

    QUERY_OUT = (
        "[graphify] note: old ids\n"
        "NODE 自己注意 [src=wiki/atoms/transformer.md loc=L8 community=x]\n"
        "NODE 翻訳 [src=wiki/topics/mt-history.md loc=L1 community=x]\n"
        "NODE 消えた [src=wiki/atoms/gone.md loc=L1 community=x]\n"
        "NODE 目録 [src=index.md loc=L1 community=x]\n"
    )

    def _wiki(self, root: Path) -> None:
        _mk_page(root, "atoms", "transformer", title="Transformer", body="自己注意を重ねる。\n")
        _mk_page(root, "topics", "mt-history", title="機械翻訳の歴史", body="RNN から。\n")
        (root / "graphify-out").mkdir()
        (root / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")

    def _search(self, root: Path, keyword: str, *, which="/usr/bin/graphify", as_json=False):
        run = mock.Mock(return_value=mock.Mock(stdout=self.QUERY_OUT))
        buf = io.StringIO()
        with mock.patch.object(wq.shutil, "which", return_value=which), \
                mock.patch.object(wq.subprocess, "run", run), contextlib.redirect_stdout(buf):
            wq.cmd_search(Namespace(keyword=keyword, json=as_json, suggest=False), root)
        return buf.getvalue(), run

    def test_adds_linked_pages_not_found_by_keyword(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._wiki(root)
            out, run = self._search(root, "自己注意")
            self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/graphify", "query", "自己注意"])
            self.assertIn("グラフでつながるページ（graphify）: 1 件", out)
            self.assertIn("wiki/topics/mt-history.md", out)
            self.assertNotIn("gone.md", out)    # 消したページは出さない
            self.assertNotIn("index.md", out)   # ページでないものは出さない

    def test_graph_only_hits_replace_neighbors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._wiki(root)
            out, _ = self._search(root, "注意機構", as_json=True)
            res = json.loads(out)
            self.assertEqual(res["count"], 0)
            self.assertEqual([g["id"] for g in res["graph"]],
                             ["wiki/atoms/transformer", "wiki/topics/mt-history"])
            self.assertNotIn("neighbors", res)

    def test_falls_back_without_graphify_or_graph(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._wiki(root)
            out, run = self._search(root, "自己注意", which=None)
            run.assert_not_called()
            self.assertNotIn("グラフ", out)
            (root / "graphify-out" / "graph.json").unlink()
            out, run = self._search(root, "自己注意")
            run.assert_not_called()
            self.assertNotIn("グラフ", out)


if __name__ == "__main__":
    unittest.main()
