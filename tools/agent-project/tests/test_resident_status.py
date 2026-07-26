"""resident.status / resident.gc の単体テスト（実装計画 W1-6）。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "agentcore"))

from agent_project.resident import (  # noqa: E402
    CONTRACT_VERSION, ChildStatus, EngineStatus, NodeCapability, SyncHealth,
    contract_compatible, run_gc)


def test_node_capability_writes_expected_path_and_shape():
    tmp = tempfile.mkdtemp(prefix="resident-status-")
    try:
        cap = NodeCapability("pc-a", workloads=["flow", "amigos"], tags=["gpu"],
                             agent_cli=["claude"], max_concurrent=2,
                             heartbeat="2026-01-01T00:00:00Z", fresh_after_sec=90.0)
        path = cap.write(tmp)
        assert path == os.path.join(tmp, "nodes", "pc-a.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["node"] == "pc-a"
        assert data["workloads"] == ["flow", "amigos"]
        assert data["contract_version"] == CONTRACT_VERSION
        assert data["max_concurrent"] == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_node_capability_omits_none_optional_fields():
    tmp = tempfile.mkdtemp(prefix="resident-status-")
    try:
        cap = NodeCapability("pc-b")
        path = cap.write(tmp)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "availability" not in data
        assert "repos" not in data
        assert "heartbeat" not in data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_contract_compatible():
    assert contract_compatible(CONTRACT_VERSION)
    assert not contract_compatible(CONTRACT_VERSION + 1)
    # 要求の無い公示（requires.contract_version 省略）は不問 — 契約バージョン導入前から
    # 板にある公示を、この項目の追加だけで一斉に入札不能にしない
    assert contract_compatible(None)
    # 要求のある公示に対して未宣言のノードは非互換（fail-close・設計 §9 C13）
    assert not contract_compatible(CONTRACT_VERSION, declared=None)


def test_engine_status_write_and_ring_buffer():
    tmp = tempfile.mkdtemp(prefix="resident-status-")
    try:
        st = EngineStatus("pc-a", heartbeat="2026-01-01T00:00:00Z",
                          tick_counts={"board": 10}, max_recent_errors=3)
        st.sync_health.append(SyncHealth("state-repo", ahead=0, behind=2))
        st.children.append(ChildStatus("proj-x", alive=True, quarantined=False, deaths=0))
        for i in range(5):
            st.record_error(f"err-{i}")
        path = st.write(tmp)
        assert path == os.path.join(tmp, ".agents", "engine", "status.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["node"] == "pc-a"
        assert data["tick_counts"] == {"board": 10}
        assert data["sync_health"] == [
            {"name": "state-repo", "ahead": 0, "behind": 2, "last_error": None}]
        assert data["children"] == [
            {"name": "proj-x", "alive": True, "quarantined": False, "deaths": 0,
             "root": None, "paused": False}]
        # リングバッファ: 直近 max_recent_errors 件だけ残る
        assert data["recent_errors"] == ["err-2", "err-3", "err-4"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_error_upper_bound_zero_keeps_nothing():
    # 上限 0 は「保持しない」。スライス任せだと `[:-0]` が効かず無制限に伸びる
    st = EngineStatus("pc-a", max_recent_errors=0)
    for i in range(5):
        st.record_error(f"err-{i}")
    assert st.recent_errors == []


def test_run_gc_aggregates_and_isolates_failures():
    def boom():
        raise RuntimeError("boom")

    events = []
    totals = run_gc([("flow", lambda: {"locks": 3, "tmp": 1}),
                     ("board", boom),
                     ("amigos", lambda: {"clones": 2})],
                    on_event=lambda name, ev, exc: events.append((name, ev)))
    assert totals == {"flow.locks": 3, "flow.tmp": 1, "amigos.clones": 2}
    assert ("flow", "ok") in events
    assert ("board", "failed") in events
    assert ("amigos", "ok") in events


def test_run_gc_keeps_lambda_sweepers_distinct():
    # 実際の渡し方（既存掃除関数への薄いラッパ = lambda）で、集計キーが混ざらないこと。
    # `__name__` 由来だと全て `<lambda>.runs` に潰れて 5 に合算されてしまう。
    totals = run_gc([("flow", lambda: {"runs": 2}), ("amigos", lambda: {"runs": 3})])
    assert totals == {"flow.runs": 2, "amigos.runs": 3}


def test_run_gc_handles_none_and_empty_sweepers():
    assert run_gc([("a", lambda: None), ("b", lambda: {})]) == {}
    assert run_gc([]) == {}


if __name__ == "__main__":
    test_node_capability_writes_expected_path_and_shape()
    test_node_capability_omits_none_optional_fields()
    test_contract_compatible()
    test_engine_status_write_and_ring_buffer()
    test_record_error_upper_bound_zero_keeps_nothing()
    test_run_gc_aggregates_and_isolates_failures()
    test_run_gc_keeps_lambda_sweepers_distinct()
    test_run_gc_handles_none_and_empty_sweepers()
    print("ok")
