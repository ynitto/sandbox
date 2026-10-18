"""repositories[].skills で外部リポジトリから一部のスキルだけを取り込み、最新へ追従する。"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import auto_update
import pull
import registry as reg_mod


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


def upstream(tmp_path):
    """skills/ に 2 スキル、直下に LICENSE と copilot-instructions.md を持つ外部リポジトリ。"""
    repo = tmp_path / "upstream"
    for name in ("wanted", "other"):
        (repo / "skills" / name).mkdir(parents=True)
        (repo / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\nv1\n")
    (repo / "LICENSE").write_text("MIT")
    (repo / ".github").mkdir()
    (repo / ".github" / "copilot-instructions.md").write_text("upstream instructions")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "v1")
    return repo


def test_pull_takes_only_listed_skills_and_follows_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(pull, "_auto_save_snapshot", lambda: None)
    repo = upstream(tmp_path)
    reg = reg_mod.load_registry()
    reg["repositories"].append({
        "name": "ext", "url": str(repo), "branch": "main", "skill_root": "skills",
        "skills": ["wanted"], "readonly": True, "priority": 50,
    })
    reg_mod.save_registry(reg)

    pull.pull_skills(repo_name="ext", interactive=False)

    home = reg_mod._skill_home()
    assert os.path.isfile(os.path.join(home, "wanted", "SKILL.md"))
    assert not os.path.exists(os.path.join(home, "other"))
    assert open(os.path.join(home, "wanted", "LICENSE")).read() == "MIT"
    agent_home = os.path.dirname(home)
    assert not os.path.exists(os.path.join(agent_home, "copilot-instructions.md"))
    reg = reg_mod.load_registry()
    assert [s["name"] for s in reg["installed_skills"]] == ["wanted"]
    assert [s["name"] for s in reg["remote_index"]["ext"]["skills"]] == ["wanted"]

    # 上流が進むと auto-update が気づき、pull で最新になる
    (repo / "skills" / "wanted" / "SKILL.md").write_text("---\nname: wanted\ndescription: wanted\n---\nv2\n")
    (repo / "skills" / "other" / "SKILL.md").write_text("---\nname: other\ndescription: other\n---\nv2\n")
    git(repo, "commit", "-q", "-am", "v2")
    updates = auto_update.check_updates(force=True)
    assert [u["name"] for u in updates] == ["wanted"]
    pull.pull_skills(repo_name="ext", interactive=False)
    assert open(os.path.join(home, "wanted", "SKILL.md")).read().endswith("v2\n")


def test_pull_replaces_symlinked_skill_without_touching_its_target(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(pull, "_auto_save_snapshot", lambda: None)
    repo = upstream(tmp_path)
    target = tmp_path / "shared" / "wanted"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("linked")
    os.makedirs(reg_mod._skill_home())
    os.symlink(target, os.path.join(reg_mod._skill_home(), "wanted"))
    reg = reg_mod.load_registry()
    reg["repositories"].append({"name": "ext", "url": str(repo), "branch": "main",
                                "skill_root": "skills", "skills": ["wanted"]})
    reg_mod.save_registry(reg)

    pull.pull_skills(repo_name="ext", interactive=False)

    dest = os.path.join(reg_mod._skill_home(), "wanted")
    assert not os.path.islink(dest)
    assert (target / "SKILL.md").read_text() == "linked"


def test_agent_home_is_found_for_opencode_and_aider(tmp_path, monkeypatch):
    for agent_dir in (os.path.join(".config", "opencode"), ".agents"):
        scripts = tmp_path / agent_dir / "skills" / "git-skill-manager" / "scripts"
        scripts.mkdir(parents=True)
        monkeypatch.setattr(reg_mod, "__file__", str(scripts / "registry.py"))
        assert reg_mod._agent_home() == str(tmp_path / agent_dir)
