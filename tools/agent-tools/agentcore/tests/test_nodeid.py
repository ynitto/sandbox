"""agentcore.nodeid — node_id 正規化の単体テスト（実装計画 W1-10）。

板は名義でファイルを分割するため「同じ PC からは常に同じ綴りが出る」ことが不変条件。
エンジンごとに正規化を持つと同一 PC が 2 ノードとして現れる（flow: `Mac` / amigos: `mac`）。
"""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore import nodeid  # noqa: E402
from agentcore.nodeid import default_node_id, normalize_node_id  # noqa: E402


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


class DefaultNodeIdTests(unittest.TestCase):
    """既定採番（P0-3）。**正規化だけでなく「どこから PC 名を取るか」も 1 実装**にする。

    以前は agent-project の設定層が独自に導出しており（小文字化しない・60 文字で切る）、
    大文字を含むホスト名の PC がプロジェクト状態側と板側で 2 名義に割れていた。"""

    def test_hostname_is_normalized(self):
        with mock.patch.object(nodeid.socket, "gethostname", return_value="DESKTOP-X"):
            self.assertEqual(default_node_id(), "desktop-x")

    def test_falls_back_to_env_when_hostname_is_empty(self):
        # 一部のコンテナは gethostname() が空を返す。ここで諦めると全ノードが "node" を
        # 名乗り、板の上で互いを上書きする。
        with mock.patch.object(nodeid.socket, "gethostname", return_value=""), \
             mock.patch.dict(os.environ, {"COMPUTERNAME": "My PC"}, clear=False):
            self.assertEqual(default_node_id(), "my-pc")

    def test_falls_back_to_node_when_nothing_is_available(self):
        with mock.patch.object(nodeid.socket, "gethostname", return_value=""), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(default_node_id(), "node")

    def test_is_idempotent_through_normalize(self):
        # 既定採番の結果をもう一度正規化しても変わらない（板・状態ファイル名の綴りが
        # どこを経由しても 1 つに定まることの前提）。
        with mock.patch.object(nodeid.socket, "gethostname", return_value="DESKTOP-X.local"):
            nid = default_node_id()
        self.assertEqual(normalize_node_id(nid), nid)

    def test_does_not_truncate(self):
        # 旧 `_auto_node_name` は 60 文字で切っていた。長い FQDN の PC が切替対象になる
        # 理由なので、上限を足し戻さないことを固定する（足すと flow / amigos の既存名義まで
        # 変わってしまう）。
        long_host = "a" * 80
        with mock.patch.object(nodeid.socket, "gethostname", return_value=long_host):
            self.assertEqual(default_node_id(), long_host)


if __name__ == "__main__":
    unittest.main()
