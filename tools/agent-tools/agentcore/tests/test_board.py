"""agentcore.board の単体テスト（入札選別規則の 1 実装）。

    python -m unittest discover -s tools/agent-tools/agentcore/tests

移植元は agent-flow / agent-amigos の `board_eligible`（同じ仕様・別実装だったもの）。
両者の既存テストが通る判定であることに加え、契約（board.schema.json）にあって
どちらも見ていなかった `requires.agent_cli` / `requires.contract_version` を確かめる。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentcore import board  # noqa: E402

REGISTRY = {
    "_meta": {"generated_from": "charter"},
    "app": {"url": "https://git.example.com/team/app.git", "owns": ["src/**"]},
    "docs": {"url": "https://git.example.com/team/docs.git", "readonly": True},
    "ref": {"url": "https://git.example.com/team/ref.git"},          # owns 無し＝参照
}

HOST_REPOS = [
    {"url": "https://git.example.com/team/app.git", "local": "/home/me/mirrors/app"},
    {"url": "https://git.example.com/team/docs.git", "readonly": True},
]


def post(**kw):
    return {"op": "post", "id": "dg-1", "workload": "flow", **kw}


class DeclaredRepoTests(unittest.TestCase):
    def test_registry_form_drops_readonly_and_reference(self):
        have = board.declared_repo_ids(REGISTRY)
        self.assertIn("app", have)
        self.assertIn(board.normalize_repo_url(REGISTRY["app"]["url"]), have)
        self.assertNotIn("docs", have)      # readonly は書込先候補にしない
        self.assertNotIn("ref", have)       # owns 無し＝参照リポジトリ
        self.assertNotIn("_meta", have)     # メタデータ予約

    def test_host_repos_form_keeps_entries_without_owns(self):
        """host.yaml の `repos[]` は「手元にクローンがある」宣言で `owns` を持たない。
        レジストリと同じ条件で絞ると 1 件も残らず、常駐体が何にも入札できなくなる。"""
        have = board.declared_repo_ids(HOST_REPOS)
        self.assertIn(board.normalize_repo_url(HOST_REPOS[0]["url"]), have)
        self.assertNotIn(board.normalize_repo_url(HOST_REPOS[1]["url"]), have)   # readonly は除く

    def test_plain_url_list(self):
        have = board.declared_repo_ids(["https://git.example.com/team/app.git"])
        self.assertIn(board.normalize_repo_url("https://git.example.com/team/app"), have)


class EligibleTests(unittest.TestCase):
    def test_workspace_url_must_be_declared(self):
        p = post(workspace={"url": "https://git.example.com/team/app.git"})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        self.assertTrue(board.eligible(p, repos=HOST_REPOS))
        other = post(workspace={"url": "https://git.example.com/team/other.git"})
        self.assertFalse(board.eligible(other, repos=REGISTRY))

    def test_url_matching_absorbs_git_suffix_and_case(self):
        p = post(workspace={"url": "HTTPS://git.example.com/team/APP/"})
        self.assertTrue(board.eligible(p, repos=REGISTRY))

    def test_no_workspace_is_eligible(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY))

    def test_tags_must_be_subset(self):
        p = post(requires={"tags": ["python", "gpu"]})
        self.assertFalse(board.eligible(p, repos=REGISTRY, tags=["python"]))
        self.assertTrue(board.eligible(p, repos=REGISTRY, tags=["python", "gpu", "x"]))

    def test_requires_repos_all_must_be_declared(self):
        p = post(requires={"repos": ["https://git.example.com/team/app.git"]})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        p2 = post(requires={"repos": ["https://git.example.com/team/app.git",
                                      "https://git.example.com/team/docs.git"]})
        self.assertFalse(board.eligible(p2, repos=REGISTRY))   # docs は readonly

    def test_agent_cli_is_or(self):
        p = post(requires={"agent_cli": ["codex", "claude"]})
        self.assertTrue(board.eligible(p, repos=REGISTRY, agent_cli=["claude"]))
        self.assertFalse(board.eligible(p, repos=REGISTRY, agent_cli=["kiro"]))

    def test_agent_cli_undeclared_node_does_not_bid(self):
        """CLI 指定つきの公示を、使える CLI を宣言していないノードが拾う理由が無い。"""
        p = post(requires={"agent_cli": ["codex"]})
        self.assertFalse(board.eligible(p, repos=REGISTRY, agent_cli=[]))
        self.assertTrue(board.eligible(post(), repos=REGISTRY, agent_cli=[]))   # 要求が無ければ不問

    def test_contract_version_fail_close(self):
        p = post(requires={"contract_version": board.CONTRACT_VERSION})
        self.assertTrue(board.eligible(p, repos=REGISTRY))
        self.assertFalse(board.eligible(p, repos=REGISTRY, contract_version=None))
        self.assertFalse(board.eligible(p, repos=REGISTRY,
                                        contract_version=board.CONTRACT_VERSION + 1))

    def test_contract_version_absent_is_unrestricted(self):
        self.assertTrue(board.eligible(post(), repos=REGISTRY, contract_version=None))

    def test_broken_post_is_not_eligible(self):
        self.assertFalse(board.eligible(None, repos=REGISTRY))
        self.assertTrue(board.eligible(post(requires="こわれている"), repos=REGISTRY))


class ContractCompatibleTests(unittest.TestCase):
    def test_no_requirement_is_unrestricted(self):
        self.assertTrue(board.contract_compatible(None, declared=None))

    def test_undeclared_node_fails_close(self):
        self.assertFalse(board.contract_compatible(1, declared=None))

    def test_exact_match(self):
        self.assertTrue(board.contract_compatible(1, declared=1))
        self.assertFalse(board.contract_compatible(1, declared=2))


if __name__ == "__main__":
    unittest.main()
