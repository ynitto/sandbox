"""chain.py の単体テスト。スキル置き場は一時フォルダに作り、本物のスキルには触れない。

最後の 2 件だけはリポジトリの `.github/skills` を見て、同梱の chain.json が検査を通ることと、
生成した DAG を実際に Python で順に流すと成果物ができることを確かめる（Dagu は起動しない）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "chain.py"
REPO_SKILLS = HERE.parent.parent

spec = importlib.util.spec_from_file_location("dagu_chain", SCRIPT)
chain = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["dagu_chain"] = chain
spec.loader.exec_module(chain)


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = chain.main(list(argv))
    return code, out.getvalue(), err.getvalue()


# 標準入力の代わりに引数のファイルを読み、加工して書く小さなスクリプト。
TOOL = r'''
import sys
args = sys.argv[1:]
src = args[args.index("--in") + 1] if "--in" in args else None
tag = args[args.index("--tag") + 1] if "--tag" in args else "x"
text = open(src, encoding="utf-8").read() if src else ""
out = f"{text}[{tag}]"
if "--out" in args:
    open(args[args.index("--out") + 1], "w", encoding="utf-8").write(out)
else:
    sys.stdout.write(out)
'''


class SkillTree:
    """一時フォルダにスキル置き場を作る。"""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "skills"
        self.root.mkdir()

    def add(self, name: str, actions: list[dict], root: Path | None = None) -> Path:
        d = (root or self.root) / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        (d / "scripts" / "tool.py").write_text(TOOL, encoding="utf-8")
        (d / "chain.json").write_text(json.dumps({"chain": 1, "actions": actions}), encoding="utf-8")
        return d

    def close(self) -> None:
        self.tmp.cleanup()


def act(aid: str, ins: dict, outs: dict, *, stdout: str | None = None, **extra) -> dict:
    run = ["python", "scripts/tool.py"]
    for n in ins:
        run += ["--in", "{in:%s}" % n]
    for n in outs:
        if n != stdout:
            run += ["--out", "{out:%s}" % n]
    outputs = {n: {"type": t, "ext": "txt", **({"stdout": True} if n == stdout else {})} for n, t in outs.items()}
    a = {"id": aid, "summary": f"{aid} する", "run": run, "inputs": ins, "outputs": outputs}
    a.update(extra)
    return a


class ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = SkillTree()

    def tearDown(self) -> None:
        self.t.close()

    def test_valid_contract_loads(self) -> None:
        d = self.t.add("conv", [act("a", {"src": "text"}, {"dst": "upper"})])
        actions, problems = chain.load_contract(d)
        self.assertEqual(problems, [])
        self.assertEqual([a.key for a in actions], ["conv/a"])

    def test_undeclared_placeholder_and_unused_input_are_reported(self) -> None:
        bad = act("a", {"src": "text"}, {"dst": "upper"})
        bad["run"] = ["python", "scripts/tool.py", "--in", "{in:other}", "--out", "{out:dst}"]
        d = self.t.add("conv", [bad])
        actions, problems = chain.load_contract(d)
        self.assertEqual(actions, [])
        joined = "\n".join(problems)
        self.assertIn("{in:other} が宣言されていない", joined)
        self.assertIn("入力 src が run で使われていない", joined)

    def test_file_output_must_appear_in_run(self) -> None:
        bad = act("a", {"src": "text"}, {"dst": "upper"})
        bad["run"] = ["python", "scripts/tool.py", "--in", "{in:src}"]
        _, problems = chain.load_contract(self.t.add("conv", [bad]))
        self.assertTrue(any("出力 dst が run に無い" in p for p in problems))

    def test_missing_script_and_ok_exit_without_zero(self) -> None:
        bad = act("a", {"src": "text"}, {"dst": "upper"}, ok_exit=[1])
        bad["run"][1] = "scripts/nope.py"
        _, problems = chain.load_contract(self.t.add("conv", [bad]))
        joined = "\n".join(problems)
        self.assertIn("scripts/nope.py が無い", joined)
        self.assertIn("ok_exit", joined)

    def test_self_loop_is_rejected(self) -> None:
        _, problems = chain.load_contract(self.t.add("conv", [act("a", {"src": "text"}, {"dst": "text"})]))
        self.assertTrue(any("チェーンが閉じない" in p for p in problems))

    def test_check_cli_exit_codes(self) -> None:
        self.t.add("good", [act("a", {"src": "text"}, {"dst": "upper"})])
        code, out, _ = run_cli("--skills-dir", str(self.t.root), "check")
        self.assertEqual(code, 0, out)
        self.t.add("bad", [{"id": "Bad"}])
        code, _, err = run_cli("--skills-dir", str(self.t.root), "check")
        self.assertEqual(code, 1)
        self.assertIn("bad", err)


class DiscoveryTest(unittest.TestCase):
    def test_first_root_wins_and_skills_without_contract_are_ignored(self) -> None:
        t = SkillTree()
        try:
            second = Path(t.tmp.name) / "other"
            second.mkdir()
            t.add("conv", [act("a", {"src": "text"}, {"dst": "upper"})])
            t.add("conv", [act("b", {"src": "text"}, {"dst": "upper"})], root=second)
            plain = t.root / "plain"
            plain.mkdir()
            (plain / "SKILL.md").write_text("---\nname: plain\n---\n", encoding="utf-8")
            found, shadowed = chain.discover([t.root, second, Path(t.tmp.name) / "missing"])
            self.assertEqual(list(found), ["conv"])
            self.assertEqual(found["conv"], t.root / "conv")
            self.assertEqual([n for n, _ in shadowed], ["conv"])
        finally:
            t.close()

    def test_env_var_overrides_agent_homes(self) -> None:
        old = os.environ.get(chain.SKILLS_ENV)
        os.environ[chain.SKILLS_ENV] = os.pathsep.join(["/a", "/b"])
        try:
            self.assertEqual(chain.skill_roots(None), [Path("/a"), Path("/b")])
            self.assertEqual(chain.skill_roots(["/c"]), [Path("/c")])
        finally:
            if old is None:
                os.environ.pop(chain.SKILLS_ENV)
            else:
                os.environ[chain.SKILLS_ENV] = old


class SolveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = SkillTree()

    def tearDown(self) -> None:
        self.t.close()

    def actions(self):
        actions, problems, _ = chain.load_all([self.t.root])
        self.assertEqual(problems, [])
        return actions

    def test_picks_shortest_chain_and_orders_dependencies(self) -> None:
        self.t.add("a-long", [act("one", {"s": "text"}, {"o": "mid"}), act("two", {"s": "mid"}, {"o": "html"})])
        self.t.add("b-short", [act("direct", {"s": "text"}, {"o": "html"})])
        self.t.add("c-pub", [act("pub", {"s": "html"}, {"o": "site"})])
        plan = chain.solve(self.actions(), {"text": "in.txt"}, ["site"], [])
        self.assertEqual([a.key for a in plan.steps], ["b-short/direct", "c-pub/pub"])

    def test_tie_is_broken_by_name_and_avoid_switches_route(self) -> None:
        self.t.add("zeta", [act("conv", {"s": "text"}, {"o": "html"})])
        self.t.add("alpha", [act("conv", {"s": "text"}, {"o": "html"})])
        acts = self.actions()
        self.assertEqual([a.key for a in chain.solve(acts, {"text": ""}, ["html"], []).steps], ["alpha/conv"])
        self.assertEqual([a.key for a in chain.solve(acts, {"text": ""}, ["html"], ["alpha"]).steps], ["zeta/conv"])
        self.assertEqual([a.key for a in chain.solve(acts, {"text": ""}, ["html"], ["alpha/conv"]).steps], ["zeta/conv"])

    def test_fan_in_shares_upstream_step(self) -> None:
        self.t.add("p", [act("parse", {"s": "text"}, {"ast": "ast", "toc": "toc"})])
        self.t.add("r", [act("render", {"a": "ast", "t": "toc"}, {"o": "html"})])
        plan = chain.solve(self.actions(), {"text": ""}, ["html"], [])
        self.assertEqual([a.key for a in plan.steps], ["p/parse", "r/render"])

    def test_unreachable_goal_explains_missing_type(self) -> None:
        self.t.add("r", [act("render", {"a": "ast", "c": "css"}, {"o": "html"})])
        with self.assertRaises(chain.ChainError) as cm:
            chain.solve(self.actions(), {"ast": ""}, ["html", "pdf"], [])
        msg = str(cm.exception)
        self.assertIn("足りない型 css", msg)
        self.assertIn("pdf を作るアクションが", msg)

    def test_given_type_is_not_rebuilt(self) -> None:
        self.t.add("a", [act("one", {"s": "text"}, {"o": "mid"}), act("two", {"s": "mid"}, {"o": "html"})])
        plan = chain.solve(self.actions(), {"text": "", "mid": "m.txt"}, ["html"], [])
        self.assertEqual([a.key for a in plan.steps], ["a/two"])


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = SkillTree()

    def tearDown(self) -> None:
        self.t.close()

    def build(self, *argv: str) -> tuple[int, str, str]:
        return run_cli("--skills-dir", str(self.t.root), "build", *argv)

    def test_dag_shape(self) -> None:
        self.t.add("a", [act("up", {"s": "text"}, {"o": "upper"}, stdout="o",
                             ok_exit=[0, 1], timeout_sec=60)])
        wrap = act("wrap", {"s": "upper"}, {"o": "html"},
                   params={"tag": {"default": "p", "description": "包む印"}})
        wrap["run"] += ["--tag", "{param:tag}"]
        self.t.add("b", [wrap])

        code, text, err = self.build("--from", "text=in.txt", "--to", "html", "--param", "tag=div")
        self.assertEqual(code, 0, err)
        self.assertNotIn("\nname:", text)  # v2 の入口の文書は name を持てない
        self.assertIn('type: "graph"', text)
        self.assertIn('- name: "IN_TEXT"', text)
        self.assertIn(json.dumps(Path("in.txt").resolve().as_posix()), text)
        self.assertIn('- name: "B_WRAP_TAG"', text)
        self.assertIn('default: "div"', text)
        self.assertIn('action: "exec"', text)
        self.assertIn('stdout: "a-up.o.txt"', text)
        self.assertIn('depends: ["a-up"]', text)
        self.assertIn('"--in", "a-up.o.txt"', text)
        self.assertIn('"--out", "b-wrap.o.txt"', text)
        self.assertIn('"--tag", "${params.B_WRAP_TAG}"', text)
        self.assertIn("exit_code: [0, 1]", text)
        self.assertIn("timeout_sec: 60", text)
        self.assertIn("html → b-wrap.o.txt", text)

    def test_input_without_path_becomes_required_param(self) -> None:
        self.t.add("a", [act("up", {"s": "text"}, {"o": "upper"})])
        code, text, err = self.build("--from", "text", "--to", "upper")
        self.assertEqual(code, 0, err)
        self.assertIn("required: true", text)
        self.assertIn("実行時に渡す: IN_TEXT=", err)

    def test_nothing_to_do_is_an_error(self) -> None:
        self.t.add("a", [act("up", {"s": "text"}, {"o": "upper"})])
        code, _, err = self.build("--from", "upper=x", "--to", "upper")
        self.assertEqual(code, 1)
        self.assertIn("ステップが無い", err)

    def test_output_is_valid_yaml_when_pyyaml_is_available(self) -> None:
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("PyYAML が無い")
        self.t.add("a", [act("up", {"s": "text"}, {"o": "upper"}, stdout="o")])
        self.t.add("b", [act("wrap", {"s": "upper"}, {"o": "html"})])
        code, text, _ = self.build("--from", 'text=/tmp/a "b".txt', "--to", "html", "--description", "説明: 'x'")
        self.assertEqual(code, 0)
        doc = yaml.safe_load(text)
        self.assertEqual(doc["description"], "説明: 'x'")
        self.assertEqual([s["name"] for s in doc["steps"]], ["a-up", "b-wrap"])
        self.assertEqual(doc["steps"][1]["depends"], ["a-up"])
        self.assertTrue(doc["params"][0]["default"].endswith('a "b".txt'))


def simulate(dag_text: str, workdir: Path) -> None:
    """生成した DAG を Dagu の代わりに順に流す（${params.X} の置き換え・stdout・作業フォルダだけ真似る）。"""
    import yaml  # type: ignore

    doc = yaml.safe_load(dag_text)
    params = {p["name"]: p.get("default", "") for p in doc.get("params", [])}
    done: set[str] = set()
    for step in doc["steps"]:
        for d in step.get("depends", []):
            assert d in done, f"{step['name']} が {d} より前に来ている"
        args = list(step["with"]["args"])
        for k, v in params.items():
            args = [a.replace("${params.%s}" % k, v) for a in args]
        ok = step.get("continue_on", {}).get("exit_code", [0])
        stdout = open(workdir / step["stdout"], "wb") if "stdout" in step else subprocess.DEVNULL
        try:
            rc = subprocess.run([step["with"]["command"], *args], cwd=workdir, stdout=stdout).returncode
        finally:
            if stdout is not subprocess.DEVNULL:
                stdout.close()
        assert rc in ok, f"{step['name']} が {rc} で終わった"
        done.add(step["name"])


class EndToEndTest(unittest.TestCase):
    def test_generated_dag_runs_in_order(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML が無い")
        t = SkillTree()
        try:
            t.add("a", [act("up", {"s": "text"}, {"o": "upper"}, stdout="o")])
            t.add("b", [act("wrap", {"s": "upper"}, {"o": "html"})])
            src = Path(t.tmp.name) / "in.txt"
            src.write_text("hello", encoding="utf-8")
            code, text, err = run_cli("--skills-dir", str(t.root), "build", "--from", f"text={src}", "--to", "html")
            self.assertEqual(code, 0, err)
            work = Path(t.tmp.name) / "run"
            work.mkdir()
            simulate(text, work)
            self.assertEqual((work / "b-wrap.o.txt").read_text(encoding="utf-8"), "hello[x][x]")
        finally:
            t.close()


def agent_act(aid: str, ins: dict, outs: dict, prompt: str, **agent) -> dict:
    outputs = {n: {"type": t, "ext": "md", "stdout": True} for n, t in outs.items()}
    return {"id": aid, "summary": f"{aid} する", "agent": {"prompt": prompt, **agent},
            "inputs": ins, "outputs": outputs}


# kiro.json と同じ形の定義（テストでは偽の CLI 名にして、実物を起動しない）
FAKE_KIRO = {
    "command": ["fake-kiro-cli", "chat", "--no-interactive"],
    "prompt_via": "argv",
    "model_flag": "--model",
    "output": "stdout",
    "write_args": ["--trust-all-tools"],
    "readonly_args": ["--trust-tools=fs_read"],
}


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.t = SkillTree()
        self.agents = Path(self.t.tmp.name) / "agents"
        self.agents.mkdir()
        (self.agents / "kiro.json").write_text(json.dumps(FAKE_KIRO), encoding="utf-8")

    def tearDown(self) -> None:
        self.t.close()

    def build(self, *argv: str) -> tuple[int, str, str]:
        return run_cli("--skills-dir", str(self.t.root), "build", "--agents-dir", str(self.agents), *argv)

    def test_contract_rules_for_agent_actions(self) -> None:
        both = agent_act("a", {"s": "text"}, {"o": "note"}, "{in:s} を読む")
        both["run"] = ["python", "scripts/tool.py"]
        undeclared = agent_act("b", {"s": "text"}, {"o": "note"}, "{in:other} を読む")
        ro_write = {"id": "c", "summary": "c", "agent": {"prompt": "{in:s} から {out:o} を書く", "readonly": True},
                    "inputs": {"s": "text"}, "outputs": {"o": {"type": "html", "ext": "html"}}}
        _, problems = chain.load_contract(self.t.add("ag", [both, undeclared, ro_write]))
        joined = "\n".join(problems)
        self.assertIn("run と agent はどちらか一方", joined)
        self.assertIn("prompt の {in:other} が宣言されていない", joined)
        self.assertIn("readonly が true なのにファイルの出力 o", joined)

    def test_readonly_defaults_to_whether_files_are_written(self) -> None:
        reads = agent_act("r", {"s": "text"}, {"o": "note"}, "{in:s} を要約する")
        writes = {"id": "w", "summary": "w", "agent": {"prompt": "{in:s} から {out:o} を書く"},
                  "inputs": {"s": "text"}, "outputs": {"o": {"type": "html", "ext": "html"}}}
        actions, problems = chain.load_contract(self.t.add("ag", [reads, writes]))
        self.assertEqual(problems, [])
        self.assertEqual({a.id: a.agent.readonly for a in actions}, {"r": True, "w": False})

    def test_script_route_wins_and_agent_fills_only_the_gap(self) -> None:
        self.t.add("s", [act("conv", {"s": "text"}, {"o": "html"}), act("pub", {"s": "html"}, {"o": "site"})])
        self.t.add("ag", [agent_act("direct", {"s": "text"}, {"o": "site"}, "{in:s} からサイトを作る"),
                          agent_act("note", {"s": "site"}, {"o": "note"}, "{in:s} の所感を書く")])
        acts, _, _ = chain.load_all([self.t.root])
        plan = chain.solve(acts, {"text": ""}, ["note"], [])
        self.assertEqual([a.key for a in plan.steps], ["s/conv", "s/pub", "ag/note"])
        with self.assertRaises(chain.ChainError) as cm:
            chain.solve(acts, {"text": ""}, ["note"], [], allow_agent=False)
        self.assertIn("--no-agent", str(cm.exception))

    def test_agent_step_is_rendered_from_the_definition(self) -> None:
        self.t.add("s", [act("up", {"s": "text"}, {"o": "upper"}, stdout="o")])
        self.t.add("ag", [agent_act("note", {"s": "upper"}, {"o": "note"}, "{in:s} を {param:lang} で要約して")])
        cj = self.t.root / "ag" / "chain.json"
        data = json.loads(cj.read_text(encoding="utf-8"))
        data["actions"][0]["params"] = {"lang": {"default": "日本語"}}
        cj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        code, text, err = self.build("--from", "text=in.txt", "--to", "note", "--agent-model", "m1")
        self.assertEqual(code, 0, err)
        self.assertIn('command: "fake-kiro-cli"', text)
        self.assertIn('args: ["chat", "--no-interactive", "--trust-tools=fs_read", "--model", "m1", '
                      '"s-up.o.txt を ${params.AG_NOTE_LANG} で要約して"]', text)
        self.assertIn('stdout: "ag-note.o.md"', text)
        self.assertIn('depends: ["s-up"]', text)
        self.assertIn("ag-note（kiro）", text)

    def test_write_mode_env_timeout_and_cli_override(self) -> None:
        other = dict(FAKE_KIRO, command=["other-cli", "run"], env={"FOO": "1"}, timeout=120)
        (self.agents / "other.json").write_text(json.dumps(other), encoding="utf-8")
        w = {"id": "w", "summary": "w", "agent": {"prompt": "{in:s} から {out:o} を書く", "cli": "other"},
             "inputs": {"s": "text"}, "outputs": {"o": {"type": "html", "ext": "html"}}}
        self.t.add("ag", [w])
        code, text, err = self.build("--from", "text=in.txt", "--to", "html")
        self.assertEqual(code, 0, err)
        self.assertIn('args: ["run", "--trust-all-tools", ', text)
        self.assertIn('"--trust-all-tools"', text)
        self.assertIn('"${params.IN_TEXT} から ag-w.o.html を書く"', text)
        self.assertIn('- FOO: "1"', text)
        self.assertIn("timeout_sec: 120", text)
        self.assertIn("ag-w（other）", text)

    def test_unsupported_or_missing_definition_is_explained(self) -> None:
        self.t.add("ag", [agent_act("n", {"s": "text"}, {"o": "note"}, "{in:s}")])
        code, _, err = self.build("--from", "text", "--to", "note", "--agent-cli", "nope")
        self.assertEqual(code, 1)
        self.assertIn("nope.json が見つからない", err)
        (self.agents / "stdin.json").write_text(json.dumps({"command": ["x"]}), encoding="utf-8")
        code, _, err = self.build("--from", "text", "--to", "note", "--agent-cli", "stdin")
        self.assertEqual(code, 1)
        self.assertIn("標準入力で受け取る CLI はまだ使えない", err)

    def test_extra_actions_file(self) -> None:
        self.t.add("s", [act("up", {"s": "text"}, {"o": "upper"})])
        extra = Path(self.t.tmp.name) / "mine.json"
        extra.write_text(json.dumps({"chain": 1, "actions": [
            agent_act("note", {"s": "upper"}, {"o": "note"}, "{in:s} を要約して")]}), encoding="utf-8")
        code, out, err = run_cli("--skills-dir", str(self.t.root), "--actions", str(extra),
                                 "plan", "--from", "text", "--to", "note", "--json")
        self.assertEqual(code, 0, err)
        steps = json.loads(out)["steps"]
        self.assertEqual([(s["action"], s["agent_cli"]) for s in steps], [("s/up", None), ("mine/note", "kiro")])
        clash = Path(self.t.tmp.name) / "s.json"
        clash.write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
        code, _, err = run_cli("--skills-dir", str(self.t.root), "--actions", str(clash), "check")
        self.assertEqual(code, 1)
        self.assertIn("スキルと重なる", err)

    def test_bundled_kiro_definition_is_usable(self) -> None:
        repo_agents = REPO_SKILLS.parent.parent / "agents"
        if not (repo_agents / "kiro.json").is_file():
            self.skipTest("リポジトリの agents/ が無い")
        cli = chain.AgentResolver(dirs=[repo_agents]).get("kiro")
        self.assertEqual(cli.argv("やって", True, "")[:2], ["kiro-cli", "chat"])
        self.assertEqual(cli.argv("やって", True, "")[-1], "やって")


class BundledContractsTest(unittest.TestCase):
    """リポジトリに同梱した chain.json が検査を通り、実際にチェーンを組めること。"""

    def test_bundled_contracts_pass_check(self) -> None:
        code, out, err = run_cli("--skills-dir", str(REPO_SKILLS), "check")
        self.assertEqual(code, 0, err)
        self.assertNotIn("アクション 0 件", out)

    def test_openapi_report_chain(self) -> None:
        code, out, err = run_cli("--skills-dir", str(REPO_SKILLS), "plan", "--from", "openapi", "--to", "openapi-report", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([s["action"] for s in json.loads(out)["steps"]], ["api-designer/validate"])


if __name__ == "__main__":
    unittest.main()
