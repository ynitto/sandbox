from __future__ import annotations

import json
import os
import shutil
import time
import unittest
from unittest import mock

from _shared import AuditTestCase, collect, store as store_mod


def _write(path: str, text: str, *, age_days: float = 0.0) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if age_days:
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
    return path


def _day(offset_days: float) -> str:
    import datetime as dt
    d = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


def memory_md(**over) -> str:
    fm = {"id": "mem-20260801-001", "title": '"JWT の有効期限"', "created": _day(40),
          "updated": _day(40), "status": "active", "scope": "home", "tags": "[auth, jwt]",
          "access_count": 0, "share_score": 80, "retention_score": 0.9,
          "importance": "normal", "memory_type": "semantic",
          "summary": '"JWT の有効期限を 15 分にした理由"'}
    fm.update(over)
    body = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{body}\n---\n\n# タイトル\n\n本文は測定に使わない。\n"


def wiki_page(**over) -> str:
    fm = {"title": '"Attention"', "type": "concept", "created": _day(30),
          "updated": _day(30), "summary": '"注意機構"'}
    fm.update(over)
    body = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{body}\n---\n\n# Attention\n\n" + ("説明。" * 60) + "\n"


class _StoresMixin:
    """3 層 + 共有路のダミーストアを組み立てる共有前置き（ここ自体はテストを持たない）。"""

    def setUp(self):
        super().setUp()
        self.ltm = os.path.join(self.tmp, "memory", "home")
        self.wiki = os.path.join(self.tmp, "wiki-root")
        self.persona = os.path.join(self.tmp, "persona")
        self.moltbook = os.path.join(self.tmp, ".moltbook")

    def args_with_stores(self, **over):
        return self.make_args(memory_stores={
            "ltm_dirs": [self.ltm], "wiki_root": self.wiki,
            "persona_home": self.persona, "moltbook_home": self.moltbook}, **over)

    def build_stores(self):
        # ltm: publish 待ち 1 / 退役候補 2（うち 1 は忘却リスク）/ 類似 2 件
        _write(os.path.join(self.ltm, "auth", "jwt-expiry.md"), memory_md())
        _write(os.path.join(self.ltm, "auth", "jwt-expiry-2.md"),
               memory_md(id="mem-20260801-002", share_score=10, access_count=3,
                         retention_score=0.2))
        _write(os.path.join(self.ltm, "general", "docker-build.md"),
               memory_md(id="mem-20260801-003", title='"Docker ビルドの高速化"',
                         summary='"レイヤキャッシュの順序"', share_score=90,
                         access_count=5, moltbook_published="true"))
        _write(os.path.join(self.wiki, "wiki", "atoms", "attention.md"), wiki_page())
        _write(os.path.join(self.wiki, "wiki", "atoms", "orphan.md"),
               wiki_page(title='"Orphan"', created=_day(1), updated=_day(1)))
        _write(os.path.join(self.wiki, "wiki", "topics", "llm.md"),
               wiki_page(title='"LLM"', type="topic") + "\n[[missing-page]]\n")
        _write(os.path.join(self.wiki, "index.md"),
               "# index\n\n## atoms\n- [[attention]]\n\n## topics\n- [[llm]]\n- [[gone]]\n")
        _write(os.path.join(self.wiki, "wiki", "meta", "queries.md"),
               "# queries\n\n- **注意機構とは** → [[attention]] (2026-08-01)\n"
               "- **知らない語** (2026-08-02)\n")
        _write(os.path.join(self.persona, "profile.md"), "# profile\n")
        _write(os.path.join(self.persona, "preferences.md"), "# preferences\n")
        _write(os.path.join(self.persona, f"{_day(3)}-update.md"), "観察ログ（本文）\n")
        _write(os.path.join(self.moltbook, "outbox", "share-1.md"),
               "---\ntitle: x\n---\n本文\n", age_days=5)
        _write(os.path.join(self.moltbook, "outbox", "published", "old.md"), "済\n")
        _write(os.path.join(self.moltbook, "state.json"),
               json.dumps({"date": "2026-08-15", "replies_today": 2}))

    def summary(self, **over):
        from agent_audit import memory
        return memory.knowledge_summary(self.args_with_stores(**over), now=time.time())


class MemoryStoreScanTests(_StoresMixin, AuditTestCase):

    def test_ltm_metrics_count_publish_backlog_dormancy_and_similar_clusters(self):
        self.build_stores()
        ltm = self.summary()["layers"]["ltm"]["stores"][0]
        self.assertEqual(ltm["total"], 3)
        self.assertEqual(ltm["publish_waiting"], 1,
                         "share_score>=70 かつ moltbook_published 無しだけが publish 待ち")
        self.assertEqual(ltm["never_accessed"], 1)
        self.assertEqual(ltm["dormant"], 1, "未参照のまま 30 日超が退役候補")
        self.assertEqual(ltm["forgetting_risk"], 1, "retention<0.3 かつ importance 通常")
        self.assertEqual(ltm["retention_bands"]["0.85-1.0"], 2)
        self.assertEqual(ltm["similar_clusters"], 1, "jwt 2 件だけが重複候補")
        self.assertEqual(ltm["similar_clustered"], 2)
        self.assertEqual(ltm["categories"], {"auth": 2, "general": 1})
        self.assertIsNone(ltm["index_entries"], "索引が無いことは None（0 件と区別する）")

    def test_ltm_index_drift_is_reported_against_actual_files(self):
        self.build_stores()
        _write(os.path.join(self.ltm, ".memory-index.json"),
               json.dumps({"version": 2, "built_at": "2026-08-01", "entries": [{"id": "a"}]}))
        ltm = self.summary()["layers"]["ltm"]["stores"][0]
        self.assertEqual((ltm["index_entries"], ltm["index_drift"]), (1, 2))

    def test_thresholds_are_configurable(self):
        self.build_stores()
        loose = self.summary(memory_dormant_days=90, memory_share_threshold=95)
        ltm = loose["layers"]["ltm"]["stores"][0]
        self.assertEqual((ltm["publish_waiting"], ltm["dormant"]), (0, 0))

    def test_wiki_metrics_cover_index_drift_lint_and_query_hit_rate(self):
        self.build_stores()
        wiki = self.summary()["layers"]["wiki"]["stores"][0]
        self.assertEqual((wiki["atoms"], wiki["topics"], wiki["total"]), (2, 1, 3))
        self.assertEqual(wiki["new_last_7d"], 1)
        self.assertEqual(wiki["index_orphans"], 1, "index.md 未登録の atoms が 1 件")
        self.assertEqual(wiki["index_missing_files"], 1, "index.md にあって実体が無いリンク")
        self.assertEqual(wiki["index_drift"], 2)
        self.assertEqual(wiki["dangling_links"], 1)
        self.assertEqual(wiki["queries_total"], 2)
        self.assertEqual(wiki["queries_hit"], 1)
        self.assertEqual(wiki["queries_hit_rate"], 0.5)

    def test_persona_metrics_carry_counts_only_and_never_the_user_model(self):
        self.build_stores()
        persona = self.summary()["layers"]["persona"]["stores"][0]
        self.assertEqual(persona["pending_updates"], 1)
        self.assertGreaterEqual(persona["pending_oldest_days"], 3.0)
        self.assertEqual(persona["managed_files"], 2)
        blob = json.dumps(persona, ensure_ascii=False)
        self.assertNotIn("観察ログ", blob, "persona の本文は集計にも入れない（C1）")
        self.assertNotIn("-update.md", blob, "ファイル名（観察日）も持ち出さない")

    def test_moltbook_metrics_name_what_cannot_be_measured_locally(self):
        self.build_stores()
        mb = self.summary()["layers"]["moltbook"]["stores"][0]
        self.assertEqual(mb["outbox_pending"], 1)
        self.assertGreaterEqual(mb["outbox_oldest_days"], 5.0)
        self.assertEqual(mb["published_local"], 1)
        self.assertEqual(mb["replies_today"], 2)
        self.assertEqual(mb["uncollected"], ["未回答メンション", "goods"])

    def test_unset_stores_are_reported_as_uncollected_not_as_zero(self):
        from agent_audit import memory
        summary = memory.knowledge_summary(self.make_args(), now=time.time())
        for layer in memory.LAYERS:
            entry = summary["layers"][layer]
            self.assertFalse(entry["configured"])
            self.assertEqual(entry["stores"], [])
            self.assertIn("未収集", entry["status"])


class MemoryStoreCollectTests(_StoresMixin, AuditTestCase):
    def test_collect_is_incremental_and_idempotent(self):
        self.build_stores()
        st = self.make_store()
        args = self.args_with_stores()
        self.assertEqual(collect.collect_memory_stores(args, st), 4, "4 層ぶんの snapshot")
        self.assertEqual(collect.collect_memory_stores(args, st), 0, "変化が無ければ増えない")
        _write(os.path.join(self.ltm, "auth", "new-one.md"),
               memory_md(id="mem-20260801-004", title='"新しい記憶"'))
        self.assertEqual(collect.collect_memory_stores(args, st), 1, "変わった層だけ 1 行")
        records = [r for r in st.iter_records() if r.get("kind") == "memory"]
        self.assertEqual({r["layer"] for r in records}, {"ltm", "wiki", "persona", "moltbook"})
        latest = [r for r in records if r["layer"] == "ltm"][-1]   # records は追記順
        self.assertEqual(latest["metrics"]["total"], 4)
        self.assertEqual(latest["source"], "memory-store")

    def test_records_never_carry_memory_bodies_or_persona_details(self):
        self.build_stores()
        st = self.make_store()
        collect.collect_memory_stores(self.args_with_stores(), st)
        blob = json.dumps([r for r in st.iter_records()], ensure_ascii=False)
        self.assertNotIn("本文は測定に使わない", blob)
        self.assertNotIn("観察ログ", blob)
        self.assertNotIn("JWT の有効期限", blob, "title / summary もレコードには入れない")

    def test_missing_configured_store_fails_close(self):
        st = self.make_store()
        args = self.make_args(memory_stores={"wiki_root": os.path.join(self.tmp, "nope")})
        with self.assertRaises(collect.SourceError):
            collect.collect_memory_stores(args, st)

    def test_weekly_growth_comes_from_snapshot_history(self):
        from agent_audit import memory
        self.build_stores()
        st = self.make_store()
        args = self.args_with_stores()
        old = time.time() - 10 * 86400
        memory.collect_memory_stores(args, st, now=old)
        _write(os.path.join(self.ltm, "auth", "grown.md"), memory_md(id="mem-20260801-009"))
        st2 = store_mod.Store(self.audit_dir)
        summary = memory.knowledge_summary(args, now=time.time(), store=st2)
        self.assertEqual(summary["layers"]["ltm"]["stores"][0]["growth_7d"], 1)


class KnowledgeReportTests(_StoresMixin, AuditTestCase):
    def test_report_kind_knowledge_shows_backlog_and_uncollected_layers(self):
        from agent_audit import report
        self.build_stores()
        st = self.make_store()
        args = self.args_with_stores()
        text = report.build_report(args, st, "knowledge")
        self.assertIn("publish 待ち", text)
        self.assertIn("queries ヒット率", text)
        self.assertIn("未回答メンション", text)
        no_stores = report.build_report(self.make_args(), st, "knowledge")
        self.assertIn("未収集", no_stores)

    def test_report_json_is_scrubbed_and_fails_close_on_unreadable_store(self):
        from agent_audit import report
        self.build_stores()
        st = self.make_store()
        data = report.knowledge_json(self.args_with_stores(), st)
        self.assertEqual(data["layers"]["ltm"]["stores"][0]["publish_waiting"], 1)
        self.assertNotIn(os.path.expanduser("~") + "/", json.dumps(data))
        bad = self.make_args(memory_stores={"persona_home": os.path.join(self.tmp, "nope")})
        bad.kind, bad.json, bad.out = "knowledge", True, None
        self.assertEqual(report.cmd_report(bad), 2)


class SkillRegistryDiscoveryTests(AuditTestCase):
    """memory_stores を書かなくても skill-registry.json から既定値が引ける（二重メンテナンス回避）。

    HOME はテストプロセス全体で 1 つの一時ディレクトリを共有する（_shared.py）ため、
    `~/.claude` を作ったら必ず片付ける——他のテストへ漏らさない。
    """

    def setUp(self):
        super().setUp()
        self.home = os.path.expanduser("~")
        self.agent_home = os.path.join(self.home, ".claude")
        self.addCleanup(shutil.rmtree, self.agent_home, True)
        self.registry_env = mock.patch.dict("os.environ", {}, clear=False)
        self.registry_env.start()
        os.environ.pop("KIRO_SKILL_REGISTRY", None)
        self.addCleanup(self.registry_env.stop)

    def _write_registry(self, skill_configs):
        os.makedirs(self.agent_home, exist_ok=True)
        with open(os.path.join(self.agent_home, "skill-registry.json"), "w", encoding="utf-8") as f:
            json.dump({"skill_configs": skill_configs}, f)

    def test_discovers_wiki_persona_moltbook_paths_from_skill_registry(self):
        wiki_root = os.path.join(self.tmp, "wiki-root")
        persona_home = os.path.join(self.tmp, "persona")
        os.makedirs(wiki_root)
        os.makedirs(persona_home)
        os.makedirs(os.path.join(self.agent_home, ".moltbook"))
        self._write_registry({
            "wiki-use": {"wiki_root": wiki_root},
            "persona-use": {"persona_home": persona_home},
            "moltbook-use": {},   # home 省略 → 既定 {agent_home}/.moltbook
        })
        from agent_audit import memory
        discovered = memory.discover_memory_stores()
        self.assertEqual(discovered["wiki_root"], wiki_root)
        self.assertEqual(discovered["persona_home"], persona_home)
        self.assertEqual(discovered["moltbook_home"], os.path.join(self.agent_home, ".moltbook"))

    def test_ltm_home_is_always_agent_home_memory_home_not_configurable(self):
        os.makedirs(os.path.join(self.agent_home, "memory", "home"))
        self._write_registry({})
        from agent_audit import memory
        discovered = memory.discover_memory_stores()
        self.assertEqual(discovered["ltm_dirs"], [os.path.join(self.agent_home, "memory", "home")])

    def test_missing_directories_are_silently_skipped_not_invented(self):
        # registry あり・moltbook home 未作成 → 発見しない（存在チェックを通さない）
        self._write_registry({"moltbook-use": {}})
        from agent_audit import memory
        discovered = memory.discover_memory_stores()
        self.assertEqual(discovered, {"ltm_dirs": [], "wiki_root": "", "persona_home": "",
                                      "moltbook_home": ""})

    def test_explicit_memory_stores_in_agent_audit_yaml_overrides_discovery(self):
        os.makedirs(os.path.join(self.agent_home, "memory", "home"))
        self._write_registry({"wiki-use": {"wiki_root": os.path.join(self.tmp, "auto-wiki")}})
        os.makedirs(os.path.join(self.tmp, "auto-wiki"))
        explicit_wiki = os.path.join(self.tmp, "explicit-wiki")
        os.makedirs(explicit_wiki)
        from agent_audit import memory
        args = self.make_args(memory_stores={"wiki_root": explicit_wiki})
        cfg = memory.memory_stores_config(args)
        self.assertEqual(cfg["wiki_root"], explicit_wiki, "明示設定が発見結果より優先される")
        self.assertEqual(cfg["ltm_dirs"], [os.path.join(self.agent_home, "memory", "home")],
                         "ltm_dirs は未設定なら発見結果で埋まる")

    def test_knowledge_summary_works_with_zero_yaml_configuration(self):
        """agent-audit.yaml に memory_stores を一切書かなくても集計できる。"""
        os.makedirs(os.path.join(self.agent_home, "memory", "home"))
        _write(os.path.join(self.agent_home, "memory", "home", "auth", "a.md"), memory_md())
        self._write_registry({})
        from agent_audit import memory
        summary = memory.knowledge_summary(self.make_args(), now=time.time())
        self.assertTrue(summary["layers"]["ltm"]["configured"])
        self.assertEqual(summary["layers"]["ltm"]["stores"][0]["total"], 1)

    def test_kiro_skill_registry_env_narrows_to_one_home(self):
        other_home = os.path.join(self.tmp, "other-agent-home")
        os.makedirs(os.path.join(other_home, "memory", "home"))
        with open(os.path.join(other_home, "skill-registry.json"), "w", encoding="utf-8") as f:
            json.dump({"skill_configs": {}}, f)
        os.makedirs(os.path.join(self.agent_home, "memory", "home"))
        self._write_registry({})
        with mock.patch.dict("os.environ", {"KIRO_SKILL_REGISTRY": other_home}):
            from agent_audit import memory
            discovered = memory.discover_memory_stores()
        self.assertEqual(discovered["ltm_dirs"], [os.path.join(other_home, "memory", "home")],
                         "KIRO_SKILL_REGISTRY 指定時は探索を 1 か所へ絞る")


if __name__ == "__main__":
    unittest.main()
