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


def test_contract_version_has_a_single_definition():
    """ノード契約バージョンと互換判定の正典は `agentcore.board` の 1 箇所（P2-1）。

    以前は板の判定（agentcore）・板への宣言（resident.status）・画面の期待値（dashboard）の
    3 箇所に同じ数が居た。片方だけ上げると「版 2 と宣言しつつ版 1 で判定する」が作れ、
    入札選別は fail-close なので**誤動作ではなく無言の不参加**として出る。写しを作ったら
    ここが落ちる（値の一致ではなく**同一オブジェクト**を見る）。"""
    from agentcore import board as boardrules

    assert CONTRACT_VERSION is boardrules.CONTRACT_VERSION
    assert contract_compatible is boardrules.contract_compatible
    # 宣言（NodeCapability / EngineStatus）も同じ数から出ていること
    assert NodeCapability("pc-a").contract_version is boardrules.CONTRACT_VERSION
    assert EngineStatus("pc-a").contract_version is boardrules.CONTRACT_VERSION


def test_node_capability_path_matches_the_board_naming_rule():
    """`nodes/<id>.json` の綴りは読む側（`BoardRepo.node_path`）と同じ規則（P2-5）。"""
    from agentcore.protocol import safe_name

    tmp = tempfile.mkdtemp(prefix="resident-status-")
    try:
        path = NodeCapability("pc a/b").write(tmp)
        assert os.path.basename(path) == f"{safe_name('pc a/b')}.json"
        assert os.path.isfile(path)
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


# モジュール直下の `def test_*` を `unittest discover` に拾わせる（既定の収集は
# `unittest.TestCase` のサブクラスだけで、関数形式は黙って無視される）。
from _functest import module_load_tests  # noqa: E402

load_tests = module_load_tests(globals())


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
