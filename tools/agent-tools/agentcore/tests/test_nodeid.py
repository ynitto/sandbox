"""agentcore.nodeid — node_id 正規化の単体テスト（実装計画 W1-10）。

板は名義でファイルを分割するため「同じ PC からは常に同じ綴りが出る」ことが不変条件。
エンジンごとに正規化を持つと同一 PC が 2 ノードとして現れる（flow: `Mac` / amigos: `mac`）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore.nodeid import normalize_node_id  # noqa: E402


class NormalizeNodeIdTests(unittest.TestCase):
    def test_lowercases_so_case_insensitive_filesystems_do_not_self_collide(self):
        # macOS の既定 FS は大小文字を区別しない。`Mac.json` と `mac.json` が同じファイルに
        # なるため、大小の揺れを正規形の時点で潰す。
        self.assertEqual(normalize_node_id("Mac"), "mac")
        self.assertEqual(normalize_node_id("MAC"), normalize_node_id("mac"))

    def test_maps_unsafe_chars_to_single_separator(self):
        self.assertEqual(normalize_node_id("My PC"), "my-pc")
        self.assertEqual(normalize_node_id("pc/a:b"), "pc-a-b")

    def test_keeps_safe_chars(self):
        self.assertEqual(normalize_node_id("pc-a_1.local"), "pc-a_1.local")

    def test_strips_edge_separators(self):
        self.assertEqual(normalize_node_id(" pc-a "), "pc-a")
        self.assertEqual(normalize_node_id("!pc!"), "pc")

    def test_non_ascii_is_folded_to_ascii(self):
        # 非 ASCII の英数は isalnum() では真になるが、OS・git・転送経路で正規化（NFC/NFD）が
        # 割れるため板のファイル名には使わない。
        self.assertEqual(normalize_node_id("マシン"), "node")     # 全部落ちて fallback
        self.assertEqual(normalize_node_id("pc-マシン"), "pc")    # ASCII 部分は残る

    def test_empty_falls_back(self):
        self.assertEqual(normalize_node_id(""), "node")
        self.assertEqual(normalize_node_id(None), "node")
        self.assertEqual(normalize_node_id("---"), "node")

    def test_idempotent(self):
        # 正規形をもう一度通しても変わらない（各エンジンの _safe に掛けても不変であることの前提）。
        for raw in ("Mac", "My PC", "pc-a_1.local", "!!!", "マシン"):
            once = normalize_node_id(raw)
            self.assertEqual(normalize_node_id(once), once)

    def test_survives_engine_safe_functions_unchanged(self):
        # flow の _safe（不正文字→`_`）と amigos の _safe（不正文字→`-`）のどちらに掛けても
        # 正規形は変わらない＝板に書かれる綴りが両エンジンで一致する。
        def flow_safe(name):
            return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)

        def amigos_safe(s):
            return "".join(c if (c.isalnum() or c in "._-") else "-" for c in str(s)) or "x"

        for raw in ("Mac", "My PC", "pc-a_1.local", "localhost"):
            nid = normalize_node_id(raw)
            self.assertEqual(flow_safe(nid), nid)
            self.assertEqual(amigos_safe(nid), nid)


if __name__ == "__main__":
    unittest.main()
