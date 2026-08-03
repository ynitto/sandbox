#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""設定の読み込み・資格情報・リポジトリの重複排除（グループ分け）のテスト。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitlab_repo_sync import config as cfgmod  # noqa: E402
from gitlab_repo_sync import planner  # noqa: E402


class TestUrlParsing(unittest.TestCase):
    def test_https_url(self):
        self.assertEqual(cfgmod.split_url("https://GitLab.Example.com/team/app.git"),
                         ("https", "gitlab.example.com", "", "team/app.git"))

    def test_url_with_port_and_credentials(self):
        self.assertEqual(cfgmod.split_url("http://oauth2:tok@gitlab.local:8080/team/app.git"),
                         ("http", "gitlab.local", ":8080", "team/app.git"))

    def test_scp_style_ssh(self):
        self.assertEqual(cfgmod.split_url("git@gitlab.example.com:team/app.git"),
                         ("ssh", "gitlab.example.com", "", "team/app.git"))

    def test_local_path(self):
        self.assertEqual(cfgmod.split_url("/srv/repos/app.git"),
                         (None, "", "", "/srv/repos/app.git"))


class TestCanonicalUrl(unittest.TestCase):
    def test_credentials_and_git_suffix_are_ignored(self):
        self.assertEqual(cfgmod.canonical_url("https://oauth2:tok@gitlab.example.com/team/app.git"),
                         cfgmod.canonical_url("https://gitlab.example.com/team/app"))

    def test_host_case_is_ignored(self):
        self.assertEqual(cfgmod.canonical_url("https://GitLab.example.com/team/app"),
                         cfgmod.canonical_url("https://gitlab.example.com/team/app"))

    def test_scheme_is_significant(self):
        # 片方だけ到達可能な構成があるので http と https は同一視しない
        self.assertNotEqual(cfgmod.canonical_url("http://gitlab.local/team/app"),
                            cfgmod.canonical_url("https://gitlab.local/team/app"))

    def test_absolute_local_paths_keep_their_root(self):
        self.assertNotEqual(cfgmod.canonical_url("/srv/a/app.git"),
                            cfgmod.canonical_url("/srv/b/app.git"))
        self.assertTrue(cfgmod.canonical_url("/srv/a/app.git").startswith("/srv/a"))


class TestRepoSlug(unittest.TestCase):
    def test_same_repo_same_slug(self):
        self.assertEqual(cfgmod.repo_slug("https://oauth2:t@gitlab.example.com/team/app.git"),
                         cfgmod.repo_slug("https://gitlab.example.com/team/app"))

    def test_same_path_on_different_hosts_differs(self):
        self.assertNotEqual(cfgmod.repo_slug("https://a.example.com/team/app"),
                            cfgmod.repo_slug("https://b.example.com/team/app"))

    def test_slug_is_ref_and_filename_safe(self):
        slug = cfgmod.repo_slug("https://gitlab.example.com/team/sub group/app.git")
        self.assertRegex(slug, r"^[a-z0-9][a-z0-9-]*$")


class TestCredentials(unittest.TestCase):
    def store(self):
        return cfgmod.CredentialStore([
            cfgmod.Credential(host="gitlab.local", token="local-tok"),
            cfgmod.Credential(host="*.example.com", token="cloud-tok", username="deploy"),
        ])

    def test_exact_host(self):
        self.assertEqual(self.store().authenticated_url("http://gitlab.local/team/app.git"),
                         "http://oauth2:local-tok@gitlab.local/team/app.git")

    def test_wildcard_host_and_username(self):
        self.assertEqual(self.store().authenticated_url("https://git.example.com/team/app.git"),
                         "https://deploy:cloud-tok@git.example.com/team/app.git")

    def test_unknown_host_is_left_alone(self):
        url = "https://other.invalid/team/app.git"
        self.assertEqual(self.store().authenticated_url(url), url)

    def test_ssh_and_local_paths_are_left_alone(self):
        for url in ("git@gitlab.local:team/app.git", "/srv/repos/app.git"):
            self.assertEqual(self.store().authenticated_url(url), url)

    def test_existing_credentials_are_respected(self):
        url = "http://me:pw@gitlab.local/team/app.git"
        self.assertEqual(self.store().authenticated_url(url), url)

    def test_secrets_are_collected_for_redaction(self):
        self.assertEqual(sorted(self.store().secrets()), ["cloud-tok", "local-tok"])


class TestBuildConfig(unittest.TestCase):
    def base(self, **overrides):
        data = {
            "store_dir": "/tmp/store",
            "state_dir": "/tmp/state",
            "credentials": [
                {"host": "gitlab.local", "token": "local-tok"},
                {"host": "gitlab.example.com", "token": "cloud-tok"},
            ],
            "defaults": {"include": ["refs/heads/main"], "exclude": []},
            "rules": {
                "release": {"include": ["refs/heads/main", "refs/tags/*"]},
                "push-out": {"strategy": planner.A_TO_B, "allow_force": True},
            },
            "pairs": [
                {"name": "app", "a": "http://gitlab.local/team/app.git",
                 "b": "https://gitlab.example.com/team/app.git", "rule": "release"},
            ],
        }
        data.update(overrides)
        return data

    def test_rule_is_resolved_by_name(self):
        pair = cfgmod.build_config(self.base()).pair("app")
        self.assertEqual(pair.rule.name, "release")
        self.assertEqual(pair.rule.include, ["refs/heads/main", "refs/tags/*"])
        self.assertEqual(pair.rule.strategy, planner.BIDIRECTIONAL)

    def test_rules_inherit_from_defaults(self):
        cfg = cfgmod.build_config(self.base())
        # defaults.exclude が rules.release にも効いている
        self.assertEqual(cfg.rules["release"].exclude, [])

    def test_pair_level_keys_override_the_rule(self):
        data = self.base()
        data["pairs"][0]["strategy"] = planner.B_TO_A
        pair = cfgmod.build_config(data).pair("app")
        self.assertEqual(pair.rule.strategy, planner.B_TO_A)
        self.assertEqual(pair.rule.include, ["refs/heads/main", "refs/tags/*"])

    def test_pat_is_global_not_per_pair(self):
        cfg = cfgmod.build_config(self.base())
        pair = cfg.pair("app")
        # ペアの URL にはトークンが載らない（表示・ログ用の素の URL）
        self.assertNotIn("local-tok", pair.a_url)
        # 認証は資格情報表から引かれる
        self.assertIn("local-tok", cfg.credentials.authenticated_url(pair.a_url))
        self.assertIn("cloud-tok", cfg.credentials.authenticated_url(pair.b_url))

    def test_credentials_map_form(self):
        data = self.base(credentials={"gitlab.local": "tok-a", "gitlab.example.com": "tok-b"})
        cfg = cfgmod.build_config(data)
        self.assertEqual(sorted(cfg.credentials.secrets()), ["tok-a", "tok-b"])

    def test_env_expansion(self):
        os.environ["GRS_TEST_TOKEN"] = "from-env"
        try:
            data = self.base()
            data["credentials"][1]["token"] = "${GRS_TEST_TOKEN}"
            cfg = cfgmod.build_config(data)
            self.assertIn("from-env", cfg.credentials.secrets())
        finally:
            del os.environ["GRS_TEST_TOKEN"]

    def test_default_name_is_derived_from_the_url(self):
        data = self.base()
        del data["pairs"][0]["name"]
        cfg = cfgmod.build_config(data)
        self.assertEqual(cfg.pairs[0].name, cfgmod.repo_slug(cfg.pairs[0].a_url))

    def test_unknown_rule_is_rejected(self):
        data = self.base()
        data["pairs"][0]["rule"] = "nope"
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(data)

    def test_unknown_strategy_is_rejected(self):
        data = self.base()
        data["rules"]["release"]["strategy"] = "sideways"
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(data)

    def test_self_pair_is_rejected(self):
        data = self.base()
        data["pairs"][0]["b"] = "http://oauth2:x@gitlab.local/team/app"
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(data)

    def test_duplicate_pair_name_is_rejected(self):
        data = self.base()
        data["pairs"].append(dict(data["pairs"][0], b="https://gitlab.example.com/team/other"))
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(data)

    def test_missing_url_is_rejected(self):
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(self.base(pairs=[{"name": "x", "a": "http://h/a"}]))

    def test_no_pairs_is_rejected(self):
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.build_config(self.base(pairs=[]))


class TestGrouping(unittest.TestCase):
    """clone を重複させないためのグループ分け。"""

    def config(self, pairs):
        return cfgmod.build_config({
            "store_dir": "/tmp/store", "state_dir": "/tmp/state", "pairs": pairs,
        })

    def test_independent_pairs_get_their_own_store(self):
        cfg = self.config([
            {"name": "app", "a": "http://l/team/app", "b": "https://c/team/app"},
            {"name": "lib", "a": "http://l/team/lib", "b": "https://c/team/lib"},
        ])
        groups = cfgmod.build_groups(cfg.pairs)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(len(g.repos) == 2 for g in groups))

    def test_a_repo_shared_by_two_pairs_is_cloned_once(self):
        # 同じローカルリポジトリを 2 か所へ配る構成（local ⇄ cloud, local ⇄ backup）
        cfg = self.config([
            {"name": "to-cloud", "a": "http://l/team/app", "b": "https://c/team/app"},
            {"name": "to-backup", "a": "http://l/team/app", "b": "https://bk/team/app"},
        ])
        groups = cfgmod.build_groups(cfg.pairs)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].repos), 3)      # local / cloud / backup で 3 個
        self.assertEqual(len(cfg.repos()), 3)          # ペアは 2 組でもリポジトリは 3 個

    def test_the_same_repo_written_differently_is_still_one_entry(self):
        cfg = self.config([
            {"name": "p1", "a": "http://l/team/app.git", "b": "https://c/team/app"},
            {"name": "p2", "a": "http://l/team/app/", "b": "https://bk/team/app"},
        ])
        self.assertEqual(len(cfg.repos()), 3)
        self.assertEqual(len(cfgmod.build_groups(cfg.pairs)), 1)

    def test_group_id_is_stable_regardless_of_pair_order(self):
        pairs = [
            {"name": "p1", "a": "http://l/team/app", "b": "https://c/team/app"},
            {"name": "p2", "a": "http://l/team/app", "b": "https://bk/team/app"},
        ]
        first = cfgmod.build_groups(self.config(pairs).pairs)[0].gid
        second = cfgmod.build_groups(self.config(list(reversed(pairs))).pairs)[0].gid
        self.assertEqual(first, second)

    def test_disabled_pairs_still_define_the_group(self):
        # enabled: false で ID が変わると、有効化した瞬間に clone をやり直すことになる
        pairs = [
            {"name": "p1", "a": "http://l/team/app", "b": "https://c/team/app"},
            {"name": "p2", "a": "http://l/team/app", "b": "https://bk/team/app"},
        ]
        enabled = cfgmod.build_groups(self.config(pairs).pairs)[0].gid
        disabled_pairs = [dict(pairs[0]), dict(pairs[1], enabled=False)]
        disabled = cfgmod.build_groups(self.config(disabled_pairs).pairs)[0].gid
        self.assertEqual(enabled, disabled)


if __name__ == "__main__":
    unittest.main()
