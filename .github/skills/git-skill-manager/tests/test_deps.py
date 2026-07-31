"""deps.py のユニットテスト。"""
import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import deps as deps_mod


# ---------------------------------------------------------------------------
# _parse_dep_list
# ---------------------------------------------------------------------------

class TestParseDepList:
    def test_valid_entries(self):
        raw = [
            {"name": "skill-a", "reason": "前提"},
            {"name": "skill-b", "reason": "推奨"},
        ]
        result = deps_mod._parse_dep_list(raw)
        assert result == [
            {"name": "skill-a", "reason": "前提"},
            {"name": "skill-b", "reason": "推奨"},
        ]

    def test_non_dict_skipped(self):
        raw = ["skill-a", {"name": "skill-b", "reason": "ok"}, 42]
        result = deps_mod._parse_dep_list(raw)
        assert len(result) == 1
        assert result[0]["name"] == "skill-b"

    def test_empty_list(self):
        assert deps_mod._parse_dep_list([]) == []

    def test_missing_reason(self):
        raw = [{"name": "skill-a"}]
        result = deps_mod._parse_dep_list(raw)
        assert result[0]["reason"] == ""

    def test_missing_name(self):
        raw = [{"reason": "something"}]
        result = deps_mod._parse_dep_list(raw)
        assert result[0]["name"] == ""


# ---------------------------------------------------------------------------
# _read_deps
# ---------------------------------------------------------------------------

class TestReadDeps:
    def test_no_meta_yaml(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        result = deps_mod._read_deps(str(skill_dir))
        assert result == {"depends_on": [], "recommends": []}

    def test_reads_meta_yaml(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        meta = (
            "depends_on:\n"
            "  - name: skill-a\n"
            "    reason: 前提\n"
            "recommends:\n"
            "  - name: skill-b\n"
            "    reason: 推奨\n"
        )
        (skill_dir / "meta.yaml").write_text(meta, encoding="utf-8")

        try:
            import yaml
            has_yaml = True
        except ImportError:
            has_yaml = False

        result = deps_mod._read_deps(str(skill_dir))
        if has_yaml:
            assert len(result["depends_on"]) == 1
            assert result["depends_on"][0]["name"] == "skill-a"
            assert len(result["recommends"]) == 1
            assert result["recommends"][0]["name"] == "skill-b"
        else:
            # yaml 未インストール時は簡易パースのみ（ネストには非対応）
            # エラーにならないことを確認
            assert "depends_on" in result
            assert "recommends" in result

    def test_empty_meta_yaml(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "meta.yaml").write_text("", encoding="utf-8")
        result = deps_mod._read_deps(str(skill_dir))
        assert result == {"depends_on": [], "recommends": []}


# ---------------------------------------------------------------------------
# check_deps（_all_skill_paths をモックして単体テスト）
# ---------------------------------------------------------------------------

class TestCheckDeps:
    def _make_skill_with_deps(self, tmp_path, name, depends_on=None, recommends=None):
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")

        try:
            import yaml
            meta_content = {"depends_on": depends_on or [], "recommends": recommends or []}
            content = yaml.dump(meta_content, allow_unicode=True)
        except ImportError:
            # yaml なし時は手書き
            lines = ["depends_on:"]
            for d in (depends_on or []):
                lines.append(f"  - name: {d['name']}")
                lines.append(f"    reason: {d.get('reason', '')}")
            lines.append("recommends:")
            for r in (recommends or []):
                lines.append(f"  - name: {r['name']}")
                lines.append(f"    reason: {r.get('reason', '')}")
            content = "\n".join(lines) + "\n"

        (skill_dir / "meta.yaml").write_text(content, encoding="utf-8")
        return str(skill_dir)

    def test_no_deps_no_output(self, tmp_path, capsys):
        skill_dir = tmp_path / "simple-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: simple-skill\n---\n", encoding="utf-8")
        paths = {"simple-skill": str(skill_dir)}

        with patch.object(deps_mod, "_all_skill_paths", return_value=paths):
            rc = deps_mod.check_deps()
        assert rc == 0
        out = capsys.readouterr().out
        assert "simple-skill" not in out  # 依存なしのスキルは表示されない

    def test_satisfied_deps(self, tmp_path, capsys):
        try:
            import yaml
        except ImportError:
            pytest.skip("yaml が必要なテスト")

        self._make_skill_with_deps(
            tmp_path, "skill-a",
            depends_on=[{"name": "skill-b", "reason": "前提"}],
        )
        skill_b = tmp_path / "skill-b"
        skill_b.mkdir()
        (skill_b / "SKILL.md").write_text("---\nname: skill-b\n---\n", encoding="utf-8")

        paths = {
            "skill-a": str(tmp_path / "skill-a"),
            "skill-b": str(skill_b),
        }
        with patch.object(deps_mod, "_all_skill_paths", return_value=paths):
            rc = deps_mod.check_deps()
        assert rc == 0
        out = capsys.readouterr().out
        assert "✅" in out

    def test_missing_required_dep(self, tmp_path, capsys):
        try:
            import yaml
        except ImportError:
            pytest.skip("yaml が必要なテスト")

        self._make_skill_with_deps(
            tmp_path, "skill-a",
            depends_on=[{"name": "skill-missing", "reason": "前提"}],
        )
        paths = {"skill-a": str(tmp_path / "skill-a")}

        with patch.object(deps_mod, "_all_skill_paths", return_value=paths):
            rc = deps_mod.check_deps()
        assert rc == 1
        out = capsys.readouterr().out
        assert "❌" in out

    def test_unknown_skill_returns_1(self, tmp_path, capsys):
        with patch.object(deps_mod, "_all_skill_paths", return_value={}):
            rc = deps_mod.check_deps("ghost-skill")
        assert rc == 1
        out = capsys.readouterr().out
        assert "見つかりません" in out


# ---------------------------------------------------------------------------
# show_graph
# ---------------------------------------------------------------------------

class TestShowGraph:
    def test_no_deps_no_graph(self, tmp_path, capsys):
        skill_dir = tmp_path / "simple-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: simple-skill\n---\n", encoding="utf-8")
        paths = {"simple-skill": str(skill_dir)}

        with patch.object(deps_mod, "_all_skill_paths", return_value=paths):
            deps_mod.show_graph()
        out = capsys.readouterr().out
        assert "依存関係の定義がありません" in out

    def test_unknown_skill_shows_error(self, tmp_path, capsys):
        with patch.object(deps_mod, "_all_skill_paths", return_value={}):
            deps_mod.show_graph("ghost-skill")
        out = capsys.readouterr().out
        assert "見つかりません" in out
