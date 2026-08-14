from __future__ import annotations
# knowledge.py — knowledge-observation envelope（Phase 3）。
# 単体 import しない。agent_project/__init__.py が共有名前空間へ順に exec 合成する。
#
# 新しい知識ストアは作らない。既存 brief / decisions 経路へ observation ID と provenance を
# additive に載せ、build_request の rules hash / skill 参照を run meta へ渡す（agent-flow は
# 解釈せず素通し）。取込は observation ID で冪等。linked / ltm へ渡す前に privacy と scope を検査。
# 生プロンプトは必須にしない（参照は rules_hash・skill 名・receipt digest のみ）。
# ---------------------------------------------------------------------------
KNOWLEDGE_CONTRACT_VERSION = 1
_OBS_ID_RE = re.compile(r"^obs-[0-9a-f]{16}$")
_OBS_LINE_RE = re.compile(r"^- observation:\s*(?P<id>obs-[0-9a-f]{16})\s*$")
_PROVENANCE_LINE_RE = re.compile(r"^- provenance:\s*(?P<json>\{.*\})\s*$")
KNOWLEDGE_INJECTION_MARKER = "<!-- knowledge-injection "


def rules_content_hash(cfg: "Config") -> str:
    """rules.md 全文の content hash（`sha256:<hex>`）。無ければ空。mtime ではなく内容。"""
    p = rules_path(cfg)
    if not p.exists():
        return ""
    try:
        raw = p.read_text(encoding="utf-8").encode("utf-8")
    except OSError:
        return ""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def skill_refs_for_request(cfg: "Config", task: "Task | None" = None) -> "list[dict]":
    """この run で参照する skill 名（解釈はしない・名前と role だけ）。version は任意。"""
    refs: "list[dict]" = []
    seen: set[str] = set()

    def _add(name: str, role: str) -> None:
        n = str(name or "").strip()
        if not n or n in seen:
            return
        seen.add(n)
        refs.append({"name": n, "role": role})

    _add(getattr(cfg, "flow_planner", None) or "flow-planner", "planner")
    _add(getattr(cfg, "planner_skill", None) or "", "backlog-planner")
    # タスク側の明示 skill タグがあれば名前だけ拾う（未定義なら無視）
    if task is not None:
        for key in ("skill", "skills"):
            raw = task.get(key)
            if not raw:
                continue
            if isinstance(raw, str):
                for part in re.split(r"[,;\s]+", raw):
                    _add(part, "task")
            elif isinstance(raw, list):
                for part in raw:
                    _add(str(part), "task")
    return refs


def knowledge_injection_meta(cfg: "Config", task: "Task | None" = None) -> dict:
    """build_request 注入時の rules hash + skill 参照（run meta.knowledge へ載せる形）。
    生プロンプトや本文は含めない。"""
    return {
        "contract_version": KNOWLEDGE_CONTRACT_VERSION,
        "rules_hash": rules_content_hash(cfg),
        "skills": skill_refs_for_request(cfg, task),
    }


def observation_id(*, kind: str, body: str, source: str = "",
                   task_id: str = "", rules_hash: str = "",
                   extra: str = "") -> str:
    """内容アドレスの観測 ID（`obs-` + sha256 先頭 16 hex）。同一入力 → 同一 ID。"""
    payload = json.dumps(
        {"kind": str(kind or ""), "body": str(body or ""), "source": str(source or ""),
         "task_id": str(task_id or ""), "rules_hash": str(rules_hash or ""),
         "extra": str(extra or "")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "obs-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_provenance(cfg: "Config | None", task: "Task | None" = None,
                     *, outcome: "dict | None" = None,
                     learn_refs: "list[str] | None" = None) -> dict:
    """brief/decisions に残す provenance（秘密・生プロンプト無し）。
    outcome は receipt の plan_digest / result_rev 参照のみ。"""
    inj = knowledge_injection_meta(cfg, task) if cfg is not None else {
        "rules_hash": "", "skills": [],
    }
    prov: dict = {
        "contract_version": KNOWLEDGE_CONTRACT_VERSION,
        "rules_hash": inj.get("rules_hash") or "",
        "skills": list(inj.get("skills") or []),
    }
    if learn_refs:
        prov["learn_refs"] = [str(x) for x in learn_refs if str(x).strip()]
    out: dict = {}
    src_out = outcome if isinstance(outcome, dict) else {}
    if task is not None and not src_out:
        pd = task.get("verify_plan_digest") or task.get("plan_digest")
        rr = task.get("result_rev") or task.get("verify_result_rev")
        vd = task.get("verify_verdict")
        src_out = {}
        if pd:
            src_out["plan_digest"] = str(pd)
        if rr:
            src_out["result_rev"] = str(rr)
        if vd:
            src_out["verdict"] = str(vd)
    for key in ("plan_digest", "result_rev", "verdict"):
        if src_out.get(key):
            out[key] = str(src_out[key])
    if out:
        prov["outcome"] = out
    ident: dict = {}
    if task is not None:
        tid = getattr(task, "id", None) or task.get("id")
        if tid:
            ident["task_id"] = str(tid)
        rid = task.get("last_run") or task.get("flow_run") or ""
        if rid:
            ident["run_id"] = str(rid)
    if cfg is not None and getattr(cfg, "node", None):
        ident["node"] = str(cfg.node)
    if ident:
        prov["identity"] = ident
    return prov


def format_provenance_line(prov: dict) -> str:
    """decisions 追記用の 1 行 JSON（キー昇順・最小区切り）。"""
    blob = json.dumps(prov, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"- provenance: {blob}"


def parse_observation_ids(text: str) -> "list[str]":
    """Markdown 本文から observation ID を出現順・重複なしで集める。"""
    out: "list[str]" = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        m = _OBS_LINE_RE.match(line.strip())
        if not m:
            # brief HTML コメント: <!-- obs:obs-… source date -->
            m2 = re.search(r"\bobs:(obs-[0-9a-f]{16})\b", line)
            if not m2:
                continue
            oid = m2.group(1)
        else:
            oid = m.group("id")
        if oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def file_has_observation(path: Path, obs_id: str) -> bool:
    if not obs_id or not path.exists():
        return False
    try:
        return obs_id in path.read_text(encoding="utf-8")
    except OSError:
        return False


def may_export_guide(guide: str) -> bool:
    """linked / ltm へ渡してよい scope か。scope タグ付き（charter/repo）はプロジェクト外へ出さない。"""
    _body, (kind, _name) = split_learn_scope(str(guide or ""))
    return not kind


def prepare_share_text(text, path: str) -> "str | None":
    """共有前に redact + assert。失敗・空なら None（渡さない）。"""
    try:
        body = redact_for_share(text, path)
    except ShareSafetyError:
        return None
    body = str(body or "").strip()
    return body or None


def knowledge_injection_comment(meta: dict) -> str:
    """要求文末尾へ載せる機械可読コメント（meta.request 経由でも辿れる）。"""
    blob = json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{KNOWLEDGE_INJECTION_MARKER}{blob} -->"


def write_knowledge_file(cfg: "Config", task: "Task", meta: "dict | None" = None) -> "str | None":
    """agent-flow `--knowledge-file` 用の一時 JSON。空なら None。"""
    payload = meta if isinstance(meta, dict) else knowledge_injection_meta(cfg, task)
    if not payload.get("rules_hash") and not payload.get("skills"):
        # rules も skill も無いならファイルを渡さない（従来 argv を汚さない）
        # skills は通常 planner が 1 件以上あるので、ほぼ常に書く。
        pass
    path = os.path.join(tempfile.gettempdir(), f"agent-project-knowledge-{task.id}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except OSError:
        return None
    return path


# ---------------------------------------------------------------------------
