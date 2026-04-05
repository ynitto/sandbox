"""snapshot.py のユニットテスト。"""
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import snapshot as snap_mod
import registry as reg_mod


def _setup_env(tmp_path, monkeypatch, skills=None):
    """テスト用の USERPROFILE 環境を構築する。"""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    # スキルホームを作成
    skill_home = tmp_path / ".copilot" / "skills"
    skill_home.mkdir(parents=True, exist_ok=True)

    if skills:
        for name in skills:
            s = skill_home / name
            s.mkdir()
            (s / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")

    # レジストリを作成
    reg = reg_mod.load_registry()
    if skills:
        for name in skills:
            reg["installed_skills"].append({
                "name": name,
                "source_repo": "local",
                "commit_hash": "abc1234",
                "enabled": True,
                "pinned_commit": None,
                "feedback_history": [],
                "pending_refinement": False,
                "lineage": {"local_modified": False},
            })
    reg_mod.save_registry(reg)

    return tmp_path


# ---------------------------------------------------------------------------
# save_snapshot
# ---------------------------------------------------------------------------

class TestSaveSnapshot:
    def test_creates_snapshot_dir(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-a"])
        snap_id = snap_mod.save_snapshot()
        snap_dir = tmp_path / ".copilot" / "snapshots" / snap_id
        assert snap_dir.is_dir()

    def test_saves_meta_json(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-a"])
        snap_id = snap_mod.save_snapshot(label="test-label")
        meta_path = tmp_path / ".copilot" / "snapshots" / snap_id / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["snap_id"] == snap_id
        assert meta["label"] == "test-label"
        assert meta["skill_count"] == 1

    def test_saves_registry(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-a"])
        snap_id = snap_mod.save_snapshot()
        reg_path = tmp_path / ".copilot" / "snapshots" / snap_id / "skill-registry.json"
        assert reg_path.is_file()

    def test_saves_skills_dir(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-a", "skill-b"])
        snap_id = snap_mod.save_snapshot()
        skills_dir = tmp_path / ".copilot" / "snapshots" / snap_id / "skills"
        assert (skills_dir / "skill-a" / "SKILL.md").is_file()
        assert (skills_dir / "skill-b" / "SKILL.md").is_file()

    def test_auto_clean_enforces_max_keep(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch)
        # max_keep=2 で 3 件保存 → 最新 2 件だけ残る
        # 1秒ずつずらして ID 衝突を回避
        for i in range(3):
            time.sleep(1.1)
            snap_mod.save_snapshot(max_keep=2)
        snap_dir = tmp_path / ".copilot" / "snapshots"
        snaps = [e for e in snap_dir.iterdir() if e.is_dir()]
        assert len(snaps) == 2


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    def test_empty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = snap_mod.list_snapshots()
        assert result == []
        out = capsys.readouterr().out
        assert "スナップショットがありません" in out

    def test_lists_saved(self, tmp_path, monkeypatch, capsys):
        _setup_env(tmp_path, monkeypatch)
        snap_mod.save_snapshot(label="first")
        time.sleep(1.1)
        snap_mod.save_snapshot(label="second")
        result = snap_mod.list_snapshots()
        assert len(result) == 2
        out = capsys.readouterr().out
        assert "second" in out  # 最新が先頭に表示される
        assert "first" in out


# ---------------------------------------------------------------------------
# restore_snapshot
# ---------------------------------------------------------------------------

class TestRestoreSnapshot:
    def test_restore_latest(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-a"])
        snap_id = snap_mod.save_snapshot()

        # スキルを削除してから復元
        skill_a = tmp_path / ".copilot" / "skills" / "skill-a"
        import shutil
        shutil.rmtree(str(skill_a))

        result = snap_mod.restore_snapshot(latest=True)
        assert result is True
        assert skill_a.is_dir()

    def test_restore_by_id(self, tmp_path, monkeypatch):
        _setup_env(tmp_path, monkeypatch, skills=["skill-b"])
        snap_id = snap_mod.save_snapshot()
        result = snap_mod.restore_snapshot(snap_id=snap_id)
        assert result is True

    def test_restore_nonexistent_returns_false(self, tmp_path, monkeypatch, capsys):
        _setup_env(tmp_path, monkeypatch)
        result = snap_mod.restore_snapshot(snap_id="snapshot-nonexistent")
        assert result is False
        out = capsys.readouterr().out
        assert "見つかりません" in out

    def test_restore_no_args_returns_false(self, tmp_path, monkeypatch, capsys):
        _setup_env(tmp_path, monkeypatch)
        result = snap_mod.restore_snapshot()
        assert result is False

    def test_restore_when_no_snapshots_returns_false(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = snap_mod.restore_snapshot(latest=True)
        assert result is False


# ---------------------------------------------------------------------------
# clean_snapshots
# ---------------------------------------------------------------------------

class TestCleanSnapshots:
    def test_clean_removes_old(self, tmp_path, monkeypatch, capsys):
        _setup_env(tmp_path, monkeypatch)
        for _ in range(3):
            time.sleep(1.1)
            snap_mod.save_snapshot()
        snap_mod.clean_snapshots(keep=1)
        snap_dir = tmp_path / ".copilot" / "snapshots"
        snaps = [e for e in snap_dir.iterdir() if e.is_dir()]
        assert len(snaps) == 1

    def test_clean_no_excess(self, tmp_path, monkeypatch, capsys):
        _setup_env(tmp_path, monkeypatch)
        snap_mod.save_snapshot()
        snap_mod.clean_snapshots(keep=5, quiet=False)
        out = capsys.readouterr().out
        assert "削除対象なし" in out

    def test_clean_nonexistent_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # スナップショットディレクトリが存在しなくてもエラーにならない
        snap_mod.clean_snapshots(keep=5)


# ---------------------------------------------------------------------------
# Bug regression: 同一マイクロ秒レベルの連続呼び出し（タイムスタンプ衝突）
# ---------------------------------------------------------------------------

class TestSaveSnapshotRapidCalls:
    def test_rapid_calls_no_collision(self, tmp_path, monkeypatch):
        """sleep なしで連続呼び出ししてもスナップショット ID が衝突しない。"""
        _setup_env(tmp_path, monkeypatch, skills=["skill-a"])
        ids = [snap_mod.save_snapshot() for _ in range(5)]
        assert len(set(ids)) == 5, "スナップショット ID が重複しています"
        snap_base = tmp_path / ".copilot" / "snapshots"
        snaps = [e for e in snap_base.iterdir() if e.is_dir()]
        assert len(snaps) == 5
