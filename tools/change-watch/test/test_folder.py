"""フォルダの取得元: ファイルの追加・更新・削除の検知、書き込み中のファイル、フォルダが無いとき、設定の検査。"""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from helpers import Env

from changewatch import config as configmod
from changewatch.util import ConfigError

OLD = time.time() - 3600          # 書き込みが終わって十分たった更新時刻


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.inbox = self.env.tmp / "inbox"
        self.inbox.mkdir()
        self.inbox = self.inbox.resolve()           # Windows の短い名前（RUNNER~1）を、設定が解決した長い名前にそろえる
        self.initial = "baseline"
        self.found: list[dict] = []

    def write(self, folder: dict | None = None, initial: str = "baseline", **src):
        f = {"path": "inbox", "settle_sec": 5}
        f.update(folder or {})
        self.initial = initial
        cfg = {"version": 1, "state_dir": str(self.env.tmp / "state"),
               "sources": {"inbox": {"folder": f, "failure_backoff_sec": 0, **src}}}
        self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def put(self, rel: str, text: str, mtime: float | None = OLD) -> Path:
        p = self.inbox / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def handled(self) -> list[dict]:
        out = [{"rel": f["key"], "kinds": [f["kind"]], "record": f["record"]} for f in self.found]
        self.found = []
        return out

    def run_rule(self) -> None:
        """ワークフローの 1 回の起動の代わり: 変わったファイルを受け取り、全部を処理し終えたと記録する。"""
        self.found += self.env.take(self, "r", "--initial", self.initial, source="inbox")

    def test_added_changed_removed(self):
        self.write()
        self.put("a.csv", "1")
        self.run_rule()                                         # 基準値
        self.assertEqual(self.handled(), [])
        self.put("b.csv", "2")
        self.put("a.csv", "11", OLD + 10)
        self.run_rule()
        got = {h["rel"]: h for h in self.handled()}
        self.assertEqual(set(got), {"a.csv", "b.csv"})
        self.assertIn("appeared", got["b.csv"]["kinds"])
        self.assertIn("changed", got["a.csv"]["kinds"])
        rec = got["b.csv"]["record"]
        self.assertEqual(Path(rec["path"]), self.inbox / "b.csv")          # ワークフローにパスを渡せる
        self.assertEqual((rec["name"], rec["stem"], rec["ext"], rec["dir"], rec["size"]), ("b.csv", "b", ".csv", "", 1))
        self.run_rule()                                         # 変わっていなければ何もしない
        self.assertEqual(self.handled(), [])
        (self.inbox / "a.csv").unlink()
        self.run_rule()
        self.assertEqual([(h["rel"], h["kinds"]) for h in self.handled()], [("a.csv", ["removed"])])

    def test_file_being_written_is_not_picked_up_until_settled(self):
        self.write()
        self.run_rule()
        p = self.put("new.csv", "partial", mtime=None)          # いま書いている
        self.run_rule()
        self.assertEqual(self.handled(), [])
        os.utime(p, (OLD, OLD))
        self.run_rule()
        self.assertEqual([h["rel"] for h in self.handled()], ["new.csv"])
        self.put("new.csv", "rewriting...", mtime=None)         # 書き換え中は前回の姿のまま（消えた・変わったにしない）
        self.run_rule()
        self.assertEqual(self.handled(), [])
        os.utime(p, (OLD + 20, OLD + 20))
        self.run_rule()
        self.assertEqual([(h["rel"], h["kinds"]) for h in self.handled()], [("new.csv", ["changed"])])

    def test_missing_folder_is_transient_and_does_not_look_removed(self):
        self.write()
        self.put("a.csv", "1")
        self.run_rule()
        moved = self.env.tmp / "inbox-away"
        self.inbox.rename(moved)                                # ネットワークのフォルダが切れた
        code, out = self.env.run("fetch")
        self.assertEqual(code, 3, out)
        self.assertIn("フォルダがありません", out)
        moved.rename(self.inbox)
        self.run_rule()
        self.assertEqual(self.handled(), [])                    # 戻ってきても removed / appeared にならない

    def test_include_exclude_hidden_and_subfolders(self):
        self.write({"include": ["**/*.json"], "exclude": ["tmp/**", "*.skip.json"]}, initial="emit")
        for rel in ("a.json", "sub/b.json", "sub/deep/c.json", "tmp/d.json", "e.skip.json", "f.txt", ".g.json",
                    ".hidden/h.json"):
            self.put(rel, "{}")
        self.run_rule()
        self.assertEqual(sorted(h["rel"] for h in self.handled()), ["a.json", "sub/b.json", "sub/deep/c.json"])

    def test_top_level_only_by_default(self):
        self.write(initial="emit")
        self.put("a.csv", "1")
        self.put("sub/b.csv", "2")
        self.run_rule()
        self.assertEqual([h["rel"] for h in self.handled()], ["a.csv"])

    def test_content_and_hash(self):
        self.write({"content": "json", "hash": True, "max_bytes": 64}, initial="emit")
        self.put("ok.json", '{"n": 1}')
        self.put("bad.json", "{")
        self.put("big.json", '{"x": "' + "y" * 100 + '"}')
        self.run_rule()
        got = {h["rel"]: h["record"] for h in self.handled()}
        self.assertEqual(got["ok.json"]["content"], {"n": 1})
        self.assertEqual(len(got["ok.json"]["sha256"]), 64)
        self.assertEqual(got["bad.json"]["content_error"], "invalid_json")
        self.assertEqual(got["big.json"]["content_error"], "too_large")
        self.put("ok.json", '{"n": 2}')                         # 更新時刻も大きさも同じままの書き換え
        self.run_rule()
        self.assertEqual([(h["rel"], h["kinds"]) for h in self.handled()], [("ok.json", ["changed"])])

    def test_too_many_files_does_not_complete(self):
        self.write({"max_files": 2})
        for i in range(3):
            self.put(f"{i}.csv", "x")
        code, out = self.env.run("fetch")
        self.assertNotEqual(code, 0, out)
        self.assertIn("max_files", out)

    def test_config_checks(self):
        bad = [
            ({"folder": {"path": "x"}, "request": {"url": "http://x"}}, "どちらか一方"),
            ({"folder": {"path": "x"}, "connection": "fakeapi"}, "connection"),
            ({"folder": {"path": "x"}, "key": "id"}, "key"),
            ({"folder": {}}, "folder.path"),
            ({"folder": {"path": "x", "content": "xml"}}, "content"),
            ({"folder": {"path": "x", "colour": 1}}, "知らない項目"),
            ({"folder": {"path": "x", "include": []}}, "include"),
        ]
        for src, msg in bad:
            with self.subTest(src=src):
                cfg = {"version": 1, "sources": {"s": src}}
                self.env.config_path.write_text(json.dumps(cfg), encoding="utf-8")
                with self.assertRaises(ConfigError) as cm:
                    configmod.load(self.env.config_path)
                self.assertIn(msg, str(cm.exception))
        self.write(item={"url": "http://x"})
        with self.assertRaises(ConfigError) as cm:
            configmod.load(self.env.config_path)
        self.assertIn("item", str(cm.exception))
        self.write()
        cfg = configmod.load(self.env.config_path)
        self.assertEqual(cfg.sources["inbox"].folder.path, self.inbox)   # 相対パスは設定ファイルから
        self.assertEqual(cfg.sources["inbox"].key, "rel")

    def test_changing_folder_rebaselines(self):
        self.write()
        self.put("a.csv", "1")
        self.run_rule()
        other = self.env.tmp / "other"
        other.mkdir()
        (other / "z.csv").write_text("z", encoding="utf-8")
        os.utime(other / "z.csv", (OLD, OLD))
        self.write({"path": str(other)})
        self.run_rule()
        self.assertEqual(self.handled(), [])                    # 別のフォルダは基準値の取り直し（大量に返さない）


if __name__ == "__main__":
    unittest.main()
