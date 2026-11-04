"""vault.py（設定・検索・ノート・メモ・lint）のテスト。Obsidian CLI は偽物で置き換える。"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vault  # noqa: E402

FAKE_CLI = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "down":
    print("Error: unable to connect to Obsidian"); sys.exit(0)
if args[1] == "search":
    if mode == "old" and any(a.startswith("path=") for a in args):
        print("Error: unknown parameter path"); sys.exit(1)
    paths = ["llm-wiki/knowledge/Attention.md", "other/Attention.md"]
    print(json.dumps(paths) if any(a == "format=json" for a in args) else "\\n".join(paths))
elif args[1] == "version":
    print("1.12.0")
elif args[1] == "orphans":
    print("llm-wiki/knowledge/Lonely.md\\nother/x.md")
elif args[1] == "unresolved":
    print("Missing note")
"""


class VaultTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home, self.vault_dir = t / "home", t / "vault"
        self.vault_dir.mkdir()
        cli = t / "obsidian"
        cli.write_text(FAKE_CLI, encoding="utf-8")
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        self.log = t / "cli.log"
        self.env = {"OBSIDIAN_USE_AGENT_HOME": str(self.home), "OBSIDIAN_CLI": str(cli), "FAKE_LOG": str(self.log),
                    "OBSIDIAN_USE_HOME": str(t / "state")}
        self._old = {k: os.environ.get(k) for k in [*self.env, "FAKE_MODE"]}
        os.environ.update(self.env)
        os.environ.pop("FAKE_MODE", None)
        vault.init(str(self.vault_dir), "llm-wiki", "Main")
        self.cfg = vault.load_config()
        self.project = Path(t / "proj")
        (self.project / ".git").mkdir(parents=True)
        (self.project / ".git" / "config").write_text(
            '[core]\n\tbare = false\n[remote "origin"]\n\turl = git@example.com:team/my-app.git\n', encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_main(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = vault.main(list(argv))
        return code, buf.getvalue()


class InitTest(VaultTestBase):
    def test_init_creates_layout_and_config(self):
        root = self.vault_dir / "llm-wiki"
        for d in ("knowledge", "projects", "envs", "meta"):
            self.assertTrue((root / d).is_dir(), d)
        self.assertIn('file.inFolder("llm-wiki/projects")', (root / "index.base").read_text(encoding="utf-8"))
        self.assertEqual(self.cfg["vault_name"], "Main")
        reg = json.loads((self.home / "skill-registry.json").read_text(encoding="utf-8"))
        self.assertEqual(reg["skill_configs"]["obsidian-use"]["wiki_dir"], "llm-wiki")

    def test_init_keeps_existing_files(self):
        schema = self.vault_dir / "llm-wiki" / "SCHEMA.md"
        schema.write_text("育てた決まり", encoding="utf-8")
        vault.init(str(self.vault_dir), "llm-wiki", "Main")
        self.assertEqual(schema.read_text(encoding="utf-8"), "育てた決まり")

    def test_project_name_from_origin(self):
        self.assertEqual(vault.project_name(str(self.project / "src")), "my-app")


class SearchTest(VaultTestBase):
    def test_search_goes_through_obsidian_and_stays_in_wiki(self):
        res = vault.search(self.cfg, "attention")
        self.assertEqual(res["via"], "obsidian")
        self.assertEqual(res["paths"], ["llm-wiki/knowledge/Attention.md"])
        args = json.loads(self.log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(args[0], "vault=Main")
        self.assertIn("path=llm-wiki", args)

    def test_search_retries_without_path_on_old_cli(self):
        os.environ["FAKE_MODE"] = "old"
        res = vault.search(self.cfg, "attention", scope="knowledge")
        self.assertEqual(res["via"], "obsidian")
        self.assertEqual(res["paths"], ["llm-wiki/knowledge/Attention.md"])

    def test_search_falls_back_when_obsidian_is_down(self):
        os.environ["FAKE_MODE"] = "down"
        vault.write_note(self.cfg, "knowledge", "注意機構", "Transformer の中心にある仕組み。", aliases=["attention"])
        res = vault.search(self.cfg, "transformer 仕組み")
        self.assertEqual(res["via"], "fallback")
        self.assertEqual(res["paths"], ["llm-wiki/knowledge/注意機構.md"])


class NoteTest(VaultTestBase):
    def test_note_create_then_append_merges_aliases(self):
        p, new = vault.write_note(self.cfg, "knowledge", "注意機構", "本文", note_type="concept", aliases=["attention"])
        self.assertTrue(new)
        p2, new2 = vault.write_note(self.cfg, "knowledge", "注意機構", "追加の話", aliases=["self-attention", "attention"])
        self.assertEqual(p, p2)
        self.assertFalse(new2)
        meta, body = vault.parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(meta["type"], "concept")
        self.assertEqual(meta["aliases"], ["attention", "self-attention"])
        self.assertIn("## 追記", body)

    def test_note_rejects_secret(self):
        code, _ = self.run_main("note", "--title", "x", "--body", "token: " + "glpat-" + "x" * 24)
        self.assertEqual(code, 2)

    def test_frontmatter_roundtrip_quotes(self):
        meta = {"title": "a: b", "aliases": ["x, y", "z"], "summary": "#tag で始まる"}
        parsed, _ = vault.parse_frontmatter(vault.frontmatter(meta) + "\nbody")
        self.assertEqual(parsed, meta)


class MemoTest(VaultTestBase):
    def memo(self, *extra, name="build-cmd", scope="project"):
        return self.run_main("memo", "--scope", scope, "--kind", "fact", "--name", name, "--title", "ビルドは make dist",
                             "--detail", "make dist で dist/ に出る", "--keywords", "make, build tool", "--cwd",
                             str(self.project), *extra)

    def test_memo_goes_to_project_folder_and_dedups_by_name(self):
        code, out = self.memo()
        self.assertEqual(code, 0, out)
        self.assertIn("llm-wiki/projects/my-app/", out)
        code, out = self.memo()
        self.assertIn("追記", out)
        files = list((self.vault_dir / "llm-wiki" / "projects" / "my-app").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertEqual(text.count("- 起きた回:"), 2)
        self.assertIn("tags: [make, build-tool]", text)
        items = vault.list_notes(files[0].parent)
        self.assertEqual(items[0]["name"], "build-cmd")
        self.assertEqual(items[0]["line"], "make dist で dist/ に出る")

    def test_memo_env_scope(self):
        code, out = self.memo(scope="env")
        self.assertEqual(code, 0)
        self.assertIn(f"llm-wiki/envs/{vault.env_name()}/", out)

    def test_memo_validates_arguments(self):
        code, out = self.run_main("memo", "--scope", "project", "--kind", "bug", "--name", "Bad Name", "--title", "t",
                                  "--detail", "a\nb")
        self.assertEqual(code, 1)
        self.assertIn("--name", out)
        self.assertIn("--detail", out)

    def test_memo_counts_into_session(self):
        sess = vault.session_file("s-1")
        vault._write_json(sess, {"notes": 0, "env": "Claude Code / test"})
        self.memo("--session", "s-1")
        data = json.loads(sess.read_text(encoding="utf-8"))
        self.assertEqual(data["notes"], 1)
        files = list((self.vault_dir / "llm-wiki" / "projects" / "my-app").glob("*.md"))
        self.assertIn("（Claude Code / test）", files[0].read_text(encoding="utf-8"))


class LintTest(VaultTestBase):
    def k(self, name, text):
        path = self.vault_dir / "llm-wiki" / "knowledge" / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_lint_reports_properties_links_orphans_and_duplicates(self):
        os.environ["FAKE_MODE"] = "down"   # Obsidian が閉じていても数えられる
        self.k("bare", "中身")
        vault.write_note(self.cfg, "knowledge", "注意機構", "[[Transformer]] と [[無いノート]] の話。`[[コードの中]]`",
                         note_type="concept", aliases=["attention"], summary="1 文")
        vault.write_note(self.cfg, "knowledge", "Transformer", "[[注意機構]] を重ねたモデル。十分に長い本文。",
                         note_type="product", summary="1 文")
        vault.write_note(self.cfg, "knowledge", "Attention", "別に作ってしまった同じ概念のノートです。",
                         note_type="concept", summary="1 文")
        problems = "\n".join(vault.lint(self.cfg))
        self.assertIn("プロパティが欠けている: llm-wiki/knowledge/bare.md（title, type, updated, summary）", problems)
        self.assertIn("リンク切れ: llm-wiki/knowledge/注意機構.md → [[無いノート]]", problems)
        self.assertNotIn("コードの中", problems)
        self.assertIn("孤立ノート（どこからもリンクされていない）: llm-wiki/knowledge/Attention.md", problems)
        self.assertNotIn("孤立ノート（どこからもリンクされていない）: llm-wiki/knowledge/注意機構.md", problems)
        self.assertIn("同じ名前を持つノート（統合の候補）", problems)
        self.assertEqual(self.log.read_text(encoding="utf-8") if self.log.exists() else "", "")

    def test_links_resolve_across_vault_and_memos_are_not_orphans(self):
        (self.vault_dir / "img.png").write_bytes(b"x")
        (self.vault_dir / "Daily.md").write_text("[[Transformer]] を読んだ", encoding="utf-8")
        vault.write_note(self.cfg, "knowledge", "Transformer", "![[img.png]] と [[Daily|日記]] を参照する本文。",
                         note_type="product", summary="1 文")
        self.memo_like = vault.write_note(self.cfg, "project", "手順", "どこからもリンクされないメモ本文です。",
                                          cwd=str(self.project))
        problems = "\n".join(vault.lint(self.cfg))
        self.assertNotIn("リンク切れ", problems)
        self.assertNotIn("孤立", problems)

    def test_title_must_match_file_name(self):
        self.k("x", "---\ntitle: 別の題\ntype: concept\nupdated: 2026-11-01\nsummary: s\n---\n十分に長い本文がここにあります。")
        self.assertTrue(any(p.startswith("題とファイル名が違う") for p in vault.lint(self.cfg)))


class LinksTest(VaultTestBase):
    def setUp(self):
        super().setUp()
        vault.write_note(self.cfg, "knowledge", "RAG", "検索して生成する。[[注意機構]] を使う。", note_type="concept",
                         aliases=["検索拡張生成"])
        vault.write_note(self.cfg, "knowledge", "注意機構", "[[RAG]] でも使う。", note_type="concept")
        vault.write_note(self.cfg, "knowledge", "LLM", "検索拡張生成で知識を補う。storage は関係ない。", note_type="concept")
        vault.write_note(self.cfg, "knowledge", "Storage", "storage の話だけ。storagerag や rag-like は別の語。",
                         note_type="concept")

    def test_links_lists_outgoing_backlinks_and_unlinked_mentions(self):
        res = vault.links(self.cfg, "rag")
        self.assertEqual(res["note"], "llm-wiki/knowledge/RAG.md")
        self.assertEqual(res["outgoing"], ["llm-wiki/knowledge/注意機構.md"])
        self.assertEqual(res["backlinks"], ["llm-wiki/knowledge/注意機構.md"])
        self.assertEqual(res["unlinked"], [{"path": "llm-wiki/knowledge/LLM.md", "term": "検索拡張生成"}])

    def test_links_cli(self):
        code, out = self.run_main("links", "RAG")
        self.assertEqual(code, 0)
        self.assertIn("リンクしていない言及:\n- llm-wiki/knowledge/LLM.md（検索拡張生成）", out)
        code, _ = self.run_main("links", "無い題")
        self.assertEqual(code, 1)


class IngestedTest(VaultTestBase):
    def test_ingested_matches_sources_loosely(self):
        code, _ = self.run_main("note", "--title", "注意機構", "--body", "本文", "--sources", "https://arxiv.org/abs/1706.03762/")
        self.assertEqual(code, 0)
        self.assertEqual(vault.ingested(self.cfg, "http://arxiv.org/abs/1706.03762"), ["llm-wiki/knowledge/注意機構.md"])
        self.assertEqual(vault.ingested(self.cfg, "https://example.com"), [])
        log = (self.vault_dir / "llm-wiki" / "meta" / "log.md").read_text(encoding="utf-8")
        self.assertIn("← https://arxiv.org/abs/1706.03762/", log)

    def test_append_does_not_repeat_title_heading(self):
        p, _ = vault.write_note(self.cfg, "knowledge", "注意機構", "本文")
        vault.write_note(self.cfg, "knowledge", "注意機構", "# 注意機構\n\n足す話")
        self.assertEqual(p.read_text(encoding="utf-8").count("# 注意機構"), 1)


if __name__ == "__main__":
    unittest.main()
