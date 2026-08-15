from __future__ import annotations
# recovery.py — remote 公開失敗を、検証済みの手動 push から復旧する。


class PublicationRecoveryError(ValueError):
    """force-complete の安全条件を満たさない。"""


def _failed_publications(bus: Bus) -> list:
    found = []
    for node_id in bus.task_ids():
        result = bus.read_result(node_id) or {}
        if result.get("status") != "failed":
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        publication = data.get("publication") if isinstance(data.get("publication"), dict) else {}
        if data.get("error_class") != "workspace_publish" or publication.get("state") != "failed":
            raise PublicationRecoveryError(
                f"node {node_id} は publication failure ではないため強制復旧できません")
        found.append((node_id, result, publication))
    if not found:
        raise PublicationRecoveryError("復旧可能な publication failure がありません")
    return found


def _recovered_final(bus: Bus, audit: dict) -> dict:
    graph = bus.read_graph() or {}
    results = {node_id: (bus.read_result(node_id) or {}) for node_id in bus.task_ids()}
    summary = "\n".join(
        f"- {node_id} [{result.get('status')}]: {str(result.get('output', ''))[:200]}"
        for node_id, result in results.items())
    meta = bus.run_meta(bus.run_id) or {}
    final = {
        "request": meta.get("request", ""),
        "finished_at": now_iso(),
        "iterations": int(graph.get("iteration", 0) or 0),
        "strategy": graph.get("strategy", {}),
        "summary": summary,
        "results": results,
        "manual_publication_recovery": audit,
    }
    knowledge = meta.get("knowledge")
    if isinstance(knowledge, dict) and knowledge:
        final["knowledge"] = knowledge
    write_json_atomic(bus.final_path, final)
    return final


def verify_remote_publication(publication: dict) -> dict:
    """対象 remote branch が expected commit を含むことを Git で確認する。"""
    url = str(publication.get("url") or "").strip()
    branch = str(publication.get("branch") or "").strip()
    expected = str(publication.get("commit") or "").strip()
    recovery = publication.get("recovery") if isinstance(publication.get("recovery"), dict) else {}
    repository = str(recovery.get("repository") or "").strip()
    if not url or not branch or not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected):
        raise PublicationRecoveryError("publication の URL・branch・commit が不正です")
    if not repository or not os.path.isdir(repository):
        raise PublicationRecoveryError("手動 push を検証するローカルリポジトリがありません")
    if _ws_git(repository, "check-ref-format", "--branch", branch).returncode != 0:
        raise PublicationRecoveryError(f"publication branch が不正です: {branch}")
    fetched = _ws_git(repository, "fetch", "--quiet", url, f"refs/heads/{branch}")
    if fetched.returncode != 0:
        detail = (fetched.stderr or fetched.stdout).strip()[:300]
        raise PublicationRecoveryError(f"remote branch {branch} を確認できません: {detail}")
    remote_tip = _ws_git(repository, "rev-parse", "FETCH_HEAD").stdout.strip()
    included = _ws_git(repository, "merge-base", "--is-ancestor", expected, "FETCH_HEAD")
    if included.returncode != 0:
        raise PublicationRecoveryError(
            f"remote branch {branch} に期待 commit {expected[:12]} が含まれていません")
    return {"url": url, "branch": branch, "expected_commit": expected,
            "remote_tip": remote_tip}


def force_complete_publication(bus: Bus, run_id: str, reason: str, verifier=None) -> dict:
    """手動 push を remote で検証し、publication failure だけを done へ修復する。

    後続 node が残れば run は running へ戻す。全 node が done の場合だけ final を再生成して
    run を done にする。汎用 set_status の終端保護は迂回せず、この専用遷移だけが failed を直す。
    """
    why = str(reason or "").strip()
    if not why:
        raise PublicationRecoveryError("強制復旧の理由を指定してください")
    meta = bus.run_meta(run_id) or {}
    if meta.get("status") != "failed":
        raise PublicationRecoveryError("failed の run だけを強制復旧できます")
    failures = _failed_publications(bus)
    verify = verifier or verify_remote_publication
    verified = []
    for node_id, _result, publication in failures:
        receipt = verify(publication)
        if not isinstance(receipt, dict) or not receipt.get("remote_tip"):
            raise PublicationRecoveryError(f"node {node_id} の remote 公開を確認できません")
        verified.append({"node": node_id, **receipt})

    stamp = now_iso()
    audit = {"reason": why, "verified_at": stamp, "publications": verified}
    for node_id, result, publication in failures:
        original_output = str(result.get("output") or "")
        data = dict(result.get("data") or {})
        repaired_publication = dict(publication)
        repaired_publication["state"] = "published-manually"
        repaired_publication["verified_at"] = stamp
        repaired_publication["reason"] = why
        receipt = next(item for item in verified if item["node"] == node_id)
        repaired_publication["remote_tip"] = receipt["remote_tip"]
        data.pop("error_class", None)
        data["publication"] = repaired_publication
        data["forced_completion"] = {"reason": why, "verified_at": stamp,
                                     "original_output": original_output}
        result["status"] = "done"
        result["output"] = f"手動 push を remote で検証済み（{why}）"
        result["finished_at"] = stamp
        result["data"] = data
        write_json_atomic(bus.result_path(node_id), result)

    remaining = [node_id for node_id in bus.task_ids() if bus.node_state(node_id) != "done"]
    meta = bus.run_meta(run_id) or {}
    meta.pop("failure_reason", None)
    meta["updated_at"] = stamp
    meta["manual_publication_recovery"] = audit
    if remaining:
        meta["status"] = "running"
        try:
            os.remove(bus.final_path)
        except OSError:
            pass
        status = "running"
    else:
        meta["status"] = "done"
        status = "done"
    write_json_atomic(bus.meta_path, meta)
    if status == "done":
        _recovered_final(bus, audit)
    bus.event("force-complete", "publication-recovered", run=run_id, status=status,
              reason=why, nodes=[node_id for node_id, _result, _publication in failures])
    bus.sync_push(f"force-complete publication run {run_id}: {why}")
    return {"run_id": run_id, "status": status, "remaining": remaining,
            "publications": verified}


def _launch_recovered_run(args, bus: Bus, run_id: str) -> int:
    """後続 node が残る復旧 run を、通常の resume 経路で非同期に再開する。"""
    command = _child_base(args, os.path.abspath(args.bus))
    command += ["--run-id", run_id, "run", "--from-inbox"]
    log_path = os.path.join(bus.run_dir, "force-complete-resume.log")
    with open(log_path, "a", encoding="utf-8") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    return int(process.pid)


def cmd_force_complete(args) -> int:
    """手動 push 後に remote を検証し、publication failure の run を復旧する。"""
    bus = make_bus(args, f"force-complete-{os.getpid()}")
    bus.sync_pull()
    try:
        result = force_complete_publication(bus, args.run_id, args.reason)
    except PublicationRecoveryError as exc:
        print(f"[agent-flow] force-complete を拒否しました: {exc}", file=sys.stderr)
        return 2
    if result["status"] == "running":
        result["resume_pid"] = _launch_recovered_run(args, bus, args.run_id)
        print(f">>> run {args.run_id} の手動公開を確認しました。"
              f"後続 {len(result['remaining'])} node を再開します（pid={result['resume_pid']}）。")
    else:
        print(f">>> run {args.run_id} の手動公開を確認し、status=done に復旧しました。")
    return 0
