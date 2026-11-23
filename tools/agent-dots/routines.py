"""業務係: 依頼に合うステートマシンを選んで回し、無ければスキルを組み合わせて作り、記録から直して育てる。

dot.py から呼ばれる。判定（一覧・入力の確認・検証・承認・版・記録・フックの範囲）はここで決め、LLM は使わない。
ステートを回すのも、作る・直すのも Copilot アプリ（statemachine-use の実行モード・作成モード）がする。

    目標のフォルダ/
      routines/<名前>/            作っている・直している下書き（作る・直す回だけエージェントが書ける）
      .statemachine/<名前>/       登録したマシン（policy.json の routines.publish_to で変えられる）
      .dot/routines.json          台帳（状態・承認した指紋・使うスキル・版・実行の集計）
      .dot/runs/<名前>/<rN>.json  実行の記録
      .dot/routines-history/<名前>/v<版>/  前の版

標準ライブラリだけで動く。ステートマシンの定義（YAML）を読むときだけ PyYAML を使う（statemachine-use と同じ）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

WORK = ".dot"
REGISTRY = "routines.json"
REVIEW = "routines-review.json"
DRAFTS = "routines"
LIST_LIMIT = 10
MAX_CREATE_TRIES = 3
RECENT_KEEP = 10
AUTO_IMPROVE = (3, 2)                 # 直近 3 回で 2 回失敗したら、聞かずに直す
JOB_STALE_SEC = 12 * 3600             # 進まない業務を取り下げるまで
DRAFT_STALE_SEC = 7 * 24 * 3600
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
SKILL_MENTION = re.compile(r"`([a-z0-9][a-z0-9._-]*)`\s*スキル")
SCRIPT_PATH = re.compile(r"(?:^|[\s\"'`(])((?:[A-Za-z]:)?[\w.\-/\\]+\.(?:py|js|sh))\b")
SKIP_FILES = {"teaching.json", "judge.json"}
SKIP_DIRS = {"recordings", "__pycache__"}
INTERPRETERS = {"python", "python3", "py", "node", "bash", "sh"}
FIX_RE = re.compile(r"^直す[ \t　]+(\S+)[ \t　]*(.*)$", re.S)
ROLLBACK_RE = re.compile(r"^戻す[ \t　]+(\S+)\s*$")
MODE_WORDS = {"run": "実行", "trial": "試運転", "create": "作成", "improve": "改善"}


class RoutineError(Exception):
    pass


# ---------------------------------------------------------------- 設定と台帳


def settings(policy: dict) -> dict:
    r = policy.get("routines")
    return r if isinstance(r, dict) else {}


def enabled(policy: dict) -> bool:
    return bool(settings(policy).get("skills_dirs"))


def skills_dirs(policy: dict) -> "list[Path]":
    return [Path(p) for p in settings(policy).get("skills_dirs") or []]


def publish_root(d: Path, policy: dict) -> Path:
    raw = settings(policy).get("publish_to")
    return Path(raw) if raw else d / ".statemachine"


def sources(d: Path, policy: dict) -> "list[Path]":
    return [publish_root(d, policy)] + [Path(p) for p in settings(policy).get("sources") or []]


def statemachine_dir(policy: dict) -> "Path | None":
    for root in skills_dirs(policy):
        if (root / "statemachine-use" / "scripts" / "next_state.py").is_file():
            return root / "statemachine-use"
    return None


def load_registry(d: Path) -> dict:
    try:
        data = json.loads((d / WORK / REGISTRY).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(d: Path, reg: dict) -> None:
    _save_json(d / WORK / REGISTRY, reg)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 定義を読む


def read_workflow(folder: Path) -> dict:
    try:
        import yaml  # statemachine-use と同じく PyYAML で読む
    except ImportError as e:                      # pragma: no cover - 環境の問題
        raise RoutineError("ステートマシンを読むには PyYAML が要ります（pip install pyyaml）") from e
    try:
        data = yaml.safe_load((folder / "workflow.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise RoutineError(f"workflow.yaml が読めません: {folder}: {e}") from e
    return data if isinstance(data, dict) else {}


def machine_files(folder: Path) -> "list[Path]":
    out = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).parts
        if p.is_file() and p.name not in SKIP_FILES and not (set(rel[:-1]) & SKIP_DIRS):
            out.append(p)
    return out


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for p in machine_files(folder):
        h.update(p.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def describe(name: str, folder: Path) -> dict:
    wf = read_workflow(folder)
    inputs = {}
    for key, spec in (wf.get("inputs") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        inputs[str(key)] = {"label": str(spec.get("label") or key), "required": spec.get("required", True) is not False,
                            "default": spec.get("default"), "type": spec.get("type", "text"),
                            "options": spec.get("options") or []}
    states = wf.get("states") or {}
    desc = str(wf.get("description") or "").strip()
    if not desc:
        desc = "・".join(str((s or {}).get("description") or k) for k, s in list(states.items())[:5] if isinstance(s, dict) or s is None)
    return {"name": name, "title": str(wf.get("name") or name), "description": desc[:300],
            "inputs": inputs, "states": len(states), "path": str(folder)}


def catalog(d: Path, policy: dict) -> "list[dict]":
    """手持ちのマシンの一覧。名前が重なれば先の置き場（登録先 → sources の順）を使う。下書きは台帳から。"""
    reg = load_registry(d)
    seen: dict[str, dict] = {}
    for root in sources(d, policy):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if folder.name in seen or not (folder / "workflow.yaml").is_file():
                continue
            try:
                entry = describe(folder.name, folder)
            except RoutineError as e:
                entry = {"name": folder.name, "title": folder.name, "description": f"（読めません: {e}）",
                         "inputs": {}, "states": 0, "path": str(folder), "broken": True}
            rec = reg.get(folder.name) or {}
            same = rec.get("path") == str(folder) and rec.get("digest") == digest(folder)
            entry["status"] = "ready" if rec.get("status") == "ready" and same else "unapproved"
            entry["version"] = int(rec.get("version") or 0)
            entry["runs"], entry["ok"] = int(rec.get("runs") or 0), int(rec.get("ok") or 0)
            entry["draft_status"] = (rec.get("draft") or {}).get("status", "")
            seen[folder.name] = entry
    for name, rec in reg.items():
        draft = rec.get("draft") or {}
        if name in seen or draft.get("status") not in ("draft", "trial", "awaiting"):
            continue
        folder = Path(draft.get("path") or "")
        entry = {"name": name, "title": name, "description": draft.get("purpose", ""), "inputs": {}, "states": 0,
                 "path": str(folder), "status": draft["status"], "version": 0, "runs": 0, "ok": 0, "draft_status": ""}
        if (folder / "workflow.yaml").is_file():
            try:
                entry.update({k: v for k, v in describe(name, folder).items() if k != "name"})
            except RoutineError:
                pass
        seen[name] = entry
    return list(seen.values())


def bigrams(text: str) -> "set[str]":
    t = re.sub(r"\s+", "", str(text or "").lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} | set(re.findall(r"[a-z0-9]{3,}", str(text or "").lower()))


def rank(text: str, entries: "list[dict]", limit: int = LIST_LIMIT) -> "list[dict]":
    want = bigrams(text)

    def score(e: dict) -> int:
        words = " ".join([e["name"], e["title"], e["description"], " ".join(i["label"] for i in e["inputs"].values())])
        return len(want & bigrams(words))
    scored = sorted(entries, key=lambda e: (-score(e), e["name"]))
    return scored[:limit]


def missing_inputs(entry: dict, values: dict) -> "list[str]":
    out = []
    for key, spec in entry.get("inputs", {}).items():
        if spec["required"] and spec.get("default") in (None, "") and str(values.get(key) or "").strip() == "":
            out.append(key)
    return out


# ---------------------------------------------------------------- 使うスキル


def find_skill(policy: dict, name: str) -> "Path | None":
    for root in skills_dirs(policy):
        if (root / name / "SKILL.md").is_file():
            return root / name
    return None


def skills_used(folder: Path, policy: dict, declared_writes: "list[str] | None" = None) -> "list[dict]":
    """マシンが名指ししているスキル（`名前` スキル、スキルの置き場のスクリプトのパス）。statemachine-use は必ず入れる。"""
    names: set[str] = set()
    for p in machine_files(folder):
        if p.suffix not in (".md", ".yaml", ".yml"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        names.update(SKILL_MENTION.findall(text))
        for raw in SCRIPT_PATH.findall(text):
            parts = Path(raw.replace("\\", "/")).parts
            if "skills" in parts and parts.index("skills") + 1 < len(parts):
                names.add(parts[parts.index("skills") + 1])
    writes = set(settings(policy).get("write_skills") or []) | set(declared_writes or [])
    out = []
    for name in sorted(names | {"statemachine-use"}):
        found = find_skill(policy, name)
        out.append({"name": name, "dir": str(found) if found else "", "writes": name in writes})
    return out


def skills_line(skills: "list[dict]") -> str:
    parts = []
    for s in skills:
        if s["name"] == "statemachine-use":
            continue
        mark = "（外へ書く）" if s["writes"] else ""
        miss = "（この PC に無い）" if not s["dir"] else ""
        parts.append(s["name"] + mark + miss)
    return "、".join(parts) or "なし"


# ---------------------------------------------------------------- 依頼文


def prompt_section(d: Path, state: dict, policy: dict, events: "list[dict]") -> "list[str]":
    if not enabled(policy):
        return []
    sm = statemachine_dir(policy)
    if sm is None:
        return ["", "## 業務", "statemachine-use スキルが routines.skills_dirs に見つかりません。業務は扱えません。"]
    job = state.get("routine")
    out = [""]
    if job:
        out += job_prompt(d, state, policy, job, sm)
        return out
    text = " ".join(str(e.get("text") or "") for e in events)
    entries = rank(text, catalog(d, policy))
    out.append("## 業務（手持ちのステートマシン）")
    if entries:
        for e in entries:
            ins = "・".join(f"{v['label']}（{k}{'' if v['required'] else '・任意'}）" for k, v in e["inputs"].items()) or "なし"
            status = {"ready": "登録済み", "unapproved": "未承認（初回に承認が要る）", "draft": "作成中",
                      "trial": "試運転中", "awaiting": "確認待ち"}.get(e["status"], e["status"])
            if e.get("draft_status"):
                status += "・直した版を確かめ中"
            out.append(f"- {e['name']}: {e['title']} — {e['description']}（入力: {ins}／{status}）")
    else:
        out.append("- まだありません")
    out.append("")
    out.append("業務の依頼が届いていたら、次のどれかを提案する。")
    out.append('- 合うマシンがある: {"kind": "routine", "action": "run", "machine": "<名前>", "inputs": {"<キー>": "依頼から読み取った値"}, "request": "依頼の要約"}')
    out.append("  値は依頼に書かれていることだけを写し、推測で補わない。足りない入力は聞き返してもらえる。")
    out.append('- 合うマシンが無い: {"kind": "routine", "action": "create", "name": "<英小文字・数字・ハイフンの名前>", "purpose": "何を自動化するか", "inputs": {…}, "request": "依頼の要約"}')
    out.append("  作成中・確認待ちのものと同じ業務なら、作らずに利用者の返事を待つ。")
    return out


def job_prompt(d: Path, state: dict, policy: dict, job: dict, sm: Path) -> "list[str]":
    rid, mode = job["id"], job["mode"]
    next_state = sm / "scripts" / "next_state.py"
    out = [f"## 今回は業務 {rid} の{MODE_WORDS.get(mode, mode)}"]
    if mode in ("run", "trial"):
        out.append(f"statemachine-use スキルの実行モードで、次のステートマシンを最後まで回す（1 ステートずつ。遷移は {next_state} で決める）。")
        out.append(f"- 定義: {Path(job['path']) / 'workflow.yaml'}（next_state.py にはこのパスを渡す）")
        out.append(f"- 入力: {json.dumps(job.get('inputs') or {}, ensure_ascii=False)}")
        if job.get("request"):
            out.append(f"- 依頼: {job['request']}")
        out.append(f"- 使ってよいスキル: {skills_line(job.get('skills') or [])}。ほかのスクリプトはフックが止める。")
        if mode == "trial":
            writes = [s["name"] for s in job.get("skills") or [] if s["writes"]]
            if writes:
                out.append(f"- 試運転なので、外へ書くスキル（{', '.join(writes)}）は動かさない。書く中身を作ってそのステートの出力にし、実際には送らない。")
        out.append("- 途中で止まった（遷移先が NONE・escalate・入力が足りない・スクリプトが止められた）ら、そこで終える。")
        out.append("終えたら outbox に結果を 1 件書く:")
        out.append('{"kind": "routine", "action": "result", "id": "' + rid + '", "ok": true, "final_state": "<最後のステート>", "summary": "利用者に伝える結果（3 行まで）", "escalate": false, "states": [{"state": "<ステート>", "output": "<出力の 1 行目>"}]}')
        return out
    draft = Path(job["draft"])
    out.append(f"statemachine-use スキルの作成モードで、{draft} にステートマシンを{'直す' if mode == 'improve' else '作る'}。")
    out.append(f"- scaffold.py は --dir {draft.parent} を付けて名前 {job['name']} で呼ぶ（既に workflow.yaml があれば、それを直す）。")
    out.append("- 手順に使うスキルは skill-selector スキルで選び、アクションに「`スキル名` スキル」と書く。")
    out.append("- 入力は inputs に宣言し、成果を測れるステートには check を付ける。")
    if job.get("purpose"):
        out.append(f"- 目的: {job['purpose']}")
    if job.get("request"):
        out.append(f"- 元の依頼: {job['request']}")
    if mode == "improve":
        out.append(f"- 直す理由: {job.get('reason') or '（なし）'}")
        if job.get("evidence"):
            out.append("- 直すきっかけの回の記録:")
            out.extend(f"  - {line}" for line in job["evidence"])
    if job.get("last_error"):
        out.append(f"- 前回の検証で落ちた理由: {job['last_error']}")
    out.append(f"- 書いてよいのは {draft} の中と notes/・outbox/ だけ。")
    out.append("できたら outbox に 1 件書く（試運転は利用者の承認の前にこちらで回す）:")
    out.append('{"kind": "routine", "action": "created", "id": "' + rid + '", "writes": ["<外へ書くスキル>"], "trial_inputs": {"<キー>": "試運転に使う値"}, "summary": "何を変えたか（1〜3 行）"}')
    return out


# ---------------------------------------------------------------- 提案を片付ける


def new_job(state: dict, mode: str, ts: float, **kw) -> dict:
    state["routine_seq"] = int(state.get("routine_seq") or 0) + 1
    job = {"id": f"r{state['routine_seq']}", "mode": mode, "started": ts, **kw}
    state["routine"] = job
    return job


def find_entry(d: Path, policy: dict, name: str) -> "dict | None":
    return next((e for e in catalog(d, policy) if e["name"] == name), None)


def ask_inputs(h, state: dict, entry: dict, missing: "list[str]", rid: str, comments: "list[str]", ts: float) -> None:
    labels = "、".join(f"{entry['inputs'][k]['label']}（{k}）" for k in missing)
    h["question"](state, f"業務 {entry['name']} の入力が足りません: {labels}。値を教えてください", [], comments, ts)


def handle(d: Path, state: dict, policy: dict, prop: dict, ts: float, comments: "list[str]", h: dict) -> dict:
    if not enabled(policy):
        return {"result": "denied", "why": "業務は使えるようにしていない（policy.json の routines）"}
    action = prop.get("action")
    job = state.get("routine")
    if action == "run":
        if job:
            return {"result": "denied", "why": f"ほかの業務（{job['id']}）が進行中"}
        name = str(prop.get("machine") or "")
        entry = find_entry(d, policy, name)
        if entry is None or entry.get("broken"):
            return {"result": "denied", "why": f"その名前のマシンはありません: {name}"}
        inputs = {str(k): v for k, v in (prop.get("inputs") or {}).items()} if isinstance(prop.get("inputs"), dict) else {}
        if entry["status"] in ("draft", "trial", "awaiting"):
            return {"result": "denied", "why": f"{name} はまだ登録されていない（{entry['status']}）"}
        missing = missing_inputs(entry, inputs)
        if missing:
            ask_inputs(h, state, entry, missing, "", comments, ts)
            return {"result": "asked", "missing": missing}
        folder = Path(entry["path"])
        if entry["status"] == "unapproved":
            skills = skills_used(folder, policy)
            aid = h["approval"](state, "routine.approve", {"machine": name, "path": str(folder), "inputs": inputs,
                                                         "request": str(prop.get("request") or ""), "skills": skills,
                                                         "digest": digest(folder)},
                                f"業務 {name} を使えるようにする（使うスキル: {skills_line(skills)}）", comments, ts)
            return {"result": "asked", "id": aid}
        start_run(d, state, policy, name, folder, inputs, str(prop.get("request") or ""), h, ts)
        return {"result": "accepted", "id": state["routine"]["id"]}
    if action == "create":
        if job:
            return {"result": "denied", "why": f"ほかの業務（{job['id']}）が進行中"}
        name = str(prop.get("name") or "")
        if not NAME_RE.match(name):
            return {"result": "denied", "why": f"名前は英小文字・数字・ハイフンで 3〜40 文字: {name}"}
        if find_entry(d, policy, name):
            return {"result": "denied", "why": f"{name} は既にある（直すなら「直す {name} 理由」）"}
        draft = d / DRAFTS / name
        draft.mkdir(parents=True, exist_ok=True)
        inputs = prop.get("inputs") if isinstance(prop.get("inputs"), dict) else {}
        reg = load_registry(d)
        reg[name] = {"draft": {"status": "draft", "path": str(draft), "purpose": str(prop.get("purpose") or "")}}
        save_registry(d, reg)
        job = new_job(state, "create", ts, name=name, draft=str(draft), purpose=str(prop.get("purpose") or ""),
                      request=str(prop.get("request") or ""), inputs=inputs, tries=0)
        h["event"](state, f"業務 {job['id']}: {name} を作ります", ts)
        return {"result": "accepted", "id": job["id"]}
    if action == "created":
        if not job or job.get("id") != prop.get("id") or job["mode"] not in ("create", "improve"):
            return {"result": "denied", "why": "作っている業務がない（id が違う）"}
        return created(d, state, policy, job, prop, ts, comments, h)
    if action == "result":
        if not job or job.get("id") != prop.get("id") or job["mode"] not in ("run", "trial"):
            return {"result": "denied", "why": "回している業務がない（id が違う）"}
        return result(d, state, policy, job, prop, ts, comments, h)
    return {"result": "denied", "why": f"知らない業務の提案: {action}"}


def start_run(d: Path, state: dict, policy: dict, name: str, folder: Path, inputs: dict, request: str, h: dict, ts: float,
              mode: str = "run", skills: "list[dict] | None" = None, extra: "dict | None" = None) -> dict:
    reg = load_registry(d)
    skills = skills if skills is not None else (reg.get(name) or {}).get("skills") or skills_used(folder, policy)
    job = new_job(state, mode, ts, name=name, path=str(folder), inputs=inputs, request=request, skills=skills, **(extra or {}))
    h["event"](state, f"業務 {job['id']}: {name} を{MODE_WORDS[mode]}します（入力: {json.dumps(inputs, ensure_ascii=False)}）", ts)
    return job


def dry_run(policy: dict, folder: Path) -> "tuple[bool, str]":
    sm = statemachine_dir(policy)
    if sm is None:
        return False, "statemachine-use が見つからない"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run([sys.executable, str(sm / "scripts" / "run_machine.py"), str(folder / "workflow.yaml"), "--dry-run"],
                              capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"検証を動かせない: {e}"
    out = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, out[-600:]


def created(d: Path, state: dict, policy: dict, job: dict, prop: dict, ts: float, comments: "list[str]", h: dict) -> dict:
    draft = Path(job["draft"])
    ok, out = (False, "workflow.yaml がない") if not (draft / "workflow.yaml").is_file() else dry_run(policy, draft)
    if not ok:
        job["tries"] = int(job.get("tries") or 0) + 1
        job["last_error"] = out[-300:]
        if job["tries"] >= MAX_CREATE_TRIES:
            state["routine"] = None
            comments.append(f"業務 {job['id']}（{job['name']}）を{MODE_WORDS[job['mode']]}できませんでした。検証が通りません:\n{out[-300:]}")
            return {"result": "denied", "why": "検証が通らないまま上限", "check": out}
        h["event"](state, f"業務 {job['id']}: 下書きの検証が通りません。直してください:\n{out[-300:]}", ts)
        return {"result": "denied", "why": "検証が通らない", "check": out}
    entry = describe(job["name"], draft)
    declared = [str(x) for x in prop.get("writes") or []]
    skills = skills_used(draft, policy, declared)
    inputs = dict(job.get("inputs") or {})
    if isinstance(prop.get("trial_inputs"), dict):
        inputs.update({str(k): v for k, v in prop["trial_inputs"].items()})
    missing = missing_inputs(entry, inputs)
    if missing:
        job["inputs"] = inputs
        ask_inputs(h, state, entry, missing, job["id"], comments, ts)
        return {"result": "asked", "missing": missing, "why": "試運転の入力が足りない。返事が来たら created をもう一度書く"}
    reg = load_registry(d)
    rec = reg.setdefault(job["name"], {}).setdefault("draft", {"path": str(draft)})
    rec.update({"status": "trial", "path": str(draft), "skills": skills, "summary": str(prop.get("summary") or "")[:500]})
    save_registry(d, reg)
    base = {k: job[k] for k in ("reason", "base_version", "base_path") if k in job}
    start_run(d, state, policy, job["name"], draft, inputs, job.get("request", ""), h, ts, mode="trial", skills=skills,
              extra={"from": job["mode"], **base})
    return {"result": "accepted", "id": state["routine"]["id"]}


def record_run(d: Path, job: dict, prop: dict, ts: float) -> dict:
    run = {"id": job["id"], "machine": job["name"], "mode": job["mode"], "path": job["path"], "inputs": job.get("inputs") or {},
           "request": job.get("request", ""), "ok": bool(prop.get("ok")) and not prop.get("escalate"),
           "escalate": bool(prop.get("escalate")), "final_state": str(prop.get("final_state") or ""),
           "summary": str(prop.get("summary") or "")[:1000],
           "states": [{"state": str(s.get("state") or ""), "output": str(s.get("output") or "")[:200]}
                      for s in (prop.get("states") or []) if isinstance(s, dict)][:100],
           "started": job.get("started"), "finished": ts}
    folder = d / WORK / "runs" / job["name"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job['id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run


def result(d: Path, state: dict, policy: dict, job: dict, prop: dict, ts: float, comments: "list[str]", h: dict) -> dict:
    run = record_run(d, job, prop, ts)
    state["routine"] = None
    reg = load_registry(d)
    name = job["name"]
    rec = reg.setdefault(name, {})
    word = "できました" if run["ok"] else ("上位に回す必要があります（escalate）" if run["escalate"] else "できませんでした")
    if job["mode"] == "run":
        rec["runs"] = int(rec.get("runs") or 0) + 1
        rec["ok"] = int(rec.get("ok") or 0) + (1 if run["ok"] else 0)
        rec["recent"] = ([*(rec.get("recent") or []), {"id": job["id"], "ok": run["ok"]}])[-RECENT_KEEP:]
        if not run["ok"]:
            rec.pop("steady", None)         # 失敗したら「安定」は外れる
        if not run["ok"] and run["final_state"]:
            fails = rec.setdefault("state_failures", {})
            fails[run["final_state"]] = int(fails.get(run["final_state"]) or 0) + 1
        save_registry(d, reg)
        msg = f"業務 {job['id']}（{name}）: {word}\n{run['summary']}".rstrip()
        if not run["ok"]:
            window, limit = AUTO_IMPROVE
            recent = rec["recent"][-window:]
            if sum(1 for x in recent if not x["ok"]) >= limit:
                queue_improve(state, name, f"直近 {len(recent)} 回で {sum(1 for x in recent if not x['ok'])} 回失敗", job["id"])
                msg += "\n失敗が続いているので、直す版を作って試運転します。"
            else:
                msg += f"\n直すなら「【{state['name']}】直す {job['id']} 理由」と返信してください。"
        comments.append(msg)
        return {"result": "done", "id": job["id"], "ok": run["ok"]}
    # 試運転
    rec.setdefault("draft", {"path": job["path"]})["status"] = "awaiting" if run["ok"] else "draft"
    save_registry(d, reg)
    if not run["ok"]:
        comments.append(f"業務 {job['id']}（{name} の試運転）: {word}\n{run['summary']}\n"
                        f"直す点があれば「【{state['name']}】直す {name} 理由」と返信してください。".rstrip())
        return {"result": "done", "id": job["id"], "ok": False}
    skills = job.get("skills") or []
    title = f"業務 {name} を{'直した版で差し替える' if job.get('from') == 'improve' else '登録する'}（使うスキル: {skills_line(skills)}）"
    detail = {"machine": name, "draft": job["path"], "skills": skills, "run": job["id"], "summary": run["summary"]}
    h["approval"](state, "routine.publish", detail, title + f"\n試運転の結果: {run['summary']}", comments, ts)
    return {"result": "done", "id": job["id"], "ok": True}


def queue_improve(state: dict, name: str, reason: str, run_id: str = "") -> None:
    queue = list(state.get("routine_queue") or [])
    if not any(q.get("name") == name for q in queue):
        queue.append({"name": name, "reason": reason, "run": run_id})
    state["routine_queue"] = queue


def advance(d: Path, state: dict, policy: dict, ts: float, comments: "list[str]", h: dict) -> None:
    """待っている改善の回を始める（業務が空いているときだけ）。"""
    if not enabled(policy):
        return
    job = state.get("routine")
    if job and ts - float(job.get("started") or ts) > JOB_STALE_SEC:
        state["routine"] = None                   # 返事の無いまま古くなった業務は取り下げる
        comments.append(f"業務 {job['id']}（{job.get('name', '')} の{MODE_WORDS.get(job['mode'], '')}）は進まないまま"
                        f" {JOB_STALE_SEC // 3600} 時間たったので取り下げました。")
        job = None
    if job or not state.get("routine_queue"):
        return
    item = state["routine_queue"].pop(0)
    name = item["name"]
    entry = find_entry(d, policy, name)
    reg = load_registry(d)
    rec = reg.get(name) or {}
    draft = d / DRAFTS / name
    if entry and entry["status"] in ("ready", "unapproved"):
        if draft.exists():
            shutil.rmtree(draft)
        shutil.copytree(entry["path"], draft, ignore=shutil.ignore_patterns("recordings", "teaching.json", "__pycache__"))
        base_path = entry["path"]
    elif (draft / "workflow.yaml").is_file():
        base_path = (rec.get("draft") or {}).get("base_path") or ""
    else:
        comments.append(f"業務 {name} を直せません（定義が見つかりません）")
        return
    evidence, inputs = [], {}
    run = load_run(d, name, item.get("run") or "")
    if run:
        inputs = run.get("inputs") or {}
        evidence = [f"{run['id']}: {'成功' if run['ok'] else '失敗'}（最後のステート {run['final_state']}）{run['summary'][:200]}"]
        evidence += [f"{s['state']}: {s['output']}" for s in run.get("states", [])[-10:]]
    rec["draft"] = {**(rec.get("draft") or {}), "status": "draft", "path": str(draft), "base_path": base_path}
    reg[name] = rec
    save_registry(d, reg)
    job = new_job(state, "improve", ts, name=name, draft=str(draft), reason=item.get("reason", ""), evidence=evidence,
                  inputs=inputs, request=(run or {}).get("request", ""), tries=0,
                  base_version=int(rec.get("version") or 0), base_path=base_path)
    h["event"](state, f"業務 {job['id']}: {name} を直します（理由: {item.get('reason', '')}）", ts)


def load_run(d: Path, name: str, rid: str) -> "dict | None":
    folder = d / WORK / "runs" / name
    files = [folder / f"{rid}.json"] if rid else sorted(folder.glob("r*.json"), key=lambda p: p.stat().st_mtime)[-1:]
    for p in files:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def find_run(d: Path, rid: str) -> "dict | None":
    for p in (d / WORK / "runs").glob(f"*/{rid}.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return None


# ---------------------------------------------------------------- 定期見直し


def _review_runs(d: Path) -> dict[str, dict[str, dict]]:
    records: dict[str, dict[str, dict]] = {}
    for path in sorted((d / WORK / "runs").glob("*/*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        except (OSError, ValueError, InvalidOperation, RecursionError):
            continue
        if (isinstance(run, dict) and run.get("id") == path.stem
                and run.get("machine") == path.parent.name and type(run.get("ok")) is bool):
            records.setdefault(path.parent.name, {})[path.stem] = run
    return records


def _input_key(value):
    """JSON のオブジェクトは順不同。真偽値と数値は別の型として比較する。"""
    if isinstance(value, dict):
        return dict, tuple(sorted((k, _input_key(v)) for k, v in value.items()))
    if isinstance(value, list):
        return list, tuple(map(_input_key, value))
    if type(value) in (int, Decimal):
        return int, value
    return type(value), value


def _stale_draft_runs(d: Path, draft: dict, runs: dict, ts: float) -> "list[str] | None":
    path = draft.get("path")
    if draft.get("status") not in ("trial", "awaiting") or not path:
        return None
    trials = [r for r in runs.values() if r.get("mode") == "trial" and r.get("path") == path]
    finished = [r for r in trials if type(r.get("finished")) in (int, Decimal)]
    if finished:
        latest = max(finished, key=lambda r: r["finished"])
        return [latest["id"]] if latest["finished"] <= ts - DRAFT_STALE_SEC else None
    # 試運転の回が未完了だけ（finished が無い）→ 下書きの更新日時にフォールバック
    try:
        target = Path(path).resolve()
        if not target.is_relative_to(d.resolve()):
            return None                     # 台帳の指す先が目標のフォルダの外なら見ない
        modified = (target / "workflow.yaml").stat().st_mtime
    except OSError:
        return None
    return [] if ts - modified >= DRAFT_STALE_SEC else None


def review_routines(d: Path, state: dict, policy: dict, ts: float) -> dict:
    """実行の記録を見直し、台帳の集計を整理して報告する。呼び出し元が状態のロックを持つ。"""
    reg = load_registry(d)
    records = _review_runs(d)
    known = reg.keys() | {e["name"] for e in catalog(d, policy)}
    findings = []

    def add(kind, machine, detail, runs, action):
        findings.append({"kind": kind, "machine": machine, "detail": detail, "runs": runs, "action": action})

    for name, runs in records.items():
        if name not in known:
            add("orphan_run", name, "台帳にも一覧にも無い業務の記録", list(runs), "needs_review")
        by_input: dict[tuple, list[dict]] = {}
        for run in runs.values():
            if "inputs" in run:
                try:
                    by_input.setdefault(_input_key(run["inputs"]), []).append(run)
                except RecursionError:
                    continue
        for group in by_input.values():
            if {r["ok"] for r in group} == {True, False}:
                add("contradiction", name, "同じ入力で成功と失敗が混在", [r["id"] for r in group], "needs_review")

    changed = False
    job = state.get("routine") or {}
    pending = {q["name"] for q in state.get("routine_queue") or []}
    if job.get("mode") == "improve" or (job.get("mode") == "trial" and job.get("from") == "improve"):
        pending.add(job["name"])
    # 承認を待っている下書きも、次の改善で上書きしない。
    pending.update(a["proposal"]["machine"] for a in state.get("approvals") or []
                   if a["action"] == "routine.publish" and a["status"] in ("pending", "approved", "running"))
    window, limit = AUTO_IMPROVE
    for name, rec in sorted(reg.items()):
        runs = records.get(name, {})
        recent = (rec.get("recent") or [])[-RECENT_KEEP:]
        last_runs = recent[-window:]
        failures = [r["id"] for r in last_runs if r["ok"] is False]
        if len(failures) >= limit and name not in pending:
            detail = f"直近 {len(last_runs)} 回で {len(failures)} 回失敗"
            queue_improve(state, name, detail, failures[-1])
            add("repeat_failure", name, detail, failures, "queued")

        draft_runs = _stale_draft_runs(d, rec.get("draft") or {}, runs, ts)
        if draft_runs is not None:
            add("stale_draft", name, "試運転または承認待ちの下書きが 7 日以上経過", draft_runs, "needs_review")

        counters = rec.get("state_failures") or {}
        recent_ids = [r["id"] for r in recent]
        steady = (len(recent) == RECENT_KEEP and rec.get("runs", 0) >= RECENT_KEEP
                  and all(r["ok"] is True for r in recent))
        if steady and (not rec.get("steady") or counters):
            rec.pop("state_failures", None)
            rec["steady"] = True
            changed = True
            add("steady", name, f"直近 {RECENT_KEEP} 回がすべて成功", recent_ids, "applied")
            continue

        failed_runs = [runs.get(r["id"], {}) for r in recent if r["ok"] is False]
        # 失敗の記録が欠けていれば、どのステートで失敗したか確かめられない。
        if recent and counters and all(r.get("ok") is False and isinstance(r.get("final_state"), str)
                                       for r in failed_runs):
            failed_states = {r["final_state"] for r in failed_runs}
            stale = sorted(counters.keys() - failed_states)
            if stale:
                for key in stale:
                    del counters[key]
                changed = True
                states = "、".join(stale)
                add("stale_failure", name, f"直近で失敗していないステートの集計を整理（{states}）",
                    recent_ids, "applied")

    if changed:
        save_registry(d, reg)
    # 要見直しは、原因の記録や下書きが変わるまで毎回出る。同じ項目は初出の日時で見分ける。
    previous = {}
    try:
        old = json.loads((d / WORK / REVIEW).read_text(encoding="utf-8"))
        for f in old.get("findings") or []:
            if f.get("action") == "needs_review":
                previous[(f.get("kind"), f.get("machine"), tuple(f.get("runs") or []))] = f.get("first_seen")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for f in findings:
        if f["action"] == "needs_review":
            f["first_seen"] = previous.get((f["kind"], f["machine"], tuple(f["runs"])), ts)
    report = {"reviewed_at": ts, "findings": findings}
    _save_json(d / WORK / REVIEW, report)
    return report


# ---------------------------------------------------------------- 承認のあと


def on_approved(d: Path, state: dict, policy: dict, entry: dict, ts: float, comments: "list[str]", h: dict) -> None:
    prop = entry.get("proposal") or {}
    reg = load_registry(d)
    name = prop.get("machine", "")
    if entry["action"] == "routine.approve":
        folder = Path(prop["path"])
        if digest(folder) != prop.get("digest"):
            comments.append(f"業務 {name} は承認のあとに中身が変わったので、使えるようにしませんでした。もう一度頼んでください。")
            return
        rec = reg.setdefault(name, {"version": 1})
        rec.update({"status": "ready", "path": str(folder), "digest": prop["digest"], "skills": prop.get("skills") or [],
                    "approved_at": ts, "version": int(rec.get("version") or 1)})
        save_registry(d, reg)
        if state.get("routine"):
            comments.append(f"業務 {name} を使えるようにしました。ほかの業務が進行中なので、終わってからもう一度頼んでください。")
            return
        start_run(d, state, policy, name, folder, prop.get("inputs") or {}, prop.get("request", ""), h, ts)
        return
    if entry["action"] == "routine.publish":
        draft = Path(prop["draft"])
        target = publish_root(d, policy) / name
        rec = reg.setdefault(name, {"version": 0})
        version = int(rec.get("version") or 0)
        history = list(rec.get("history") or [])
        current = Path(rec.get("path") or "") if rec.get("status") == "ready" else None
        if target.exists() or (current and (current / "workflow.yaml").is_file()):
            keep = d / WORK / "routines-history" / name / f"v{version}"
            if keep.exists():
                shutil.rmtree(keep)
            keep.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.move(str(target), str(keep))
            else:                                 # ほかの置き場のものを直した。元は触らず、写しを前の版にする
                shutil.copytree(current, keep, ignore=shutil.ignore_patterns("recordings", "teaching.json", "__pycache__"))
            history.append({"version": version, "path": str(keep), "at": ts})
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(draft, target)
        shutil.rmtree(draft, ignore_errors=True)
        rec.update({"status": "ready", "path": str(target), "digest": digest(target), "skills": prop.get("skills") or [],
                    "version": version + 1, "history": history[-5:], "approved_at": ts})
        rec.pop("draft", None)
        save_registry(d, reg)
        comments.append(f"業務 {name} を版 {version + 1} として登録しました。次から「【{state['name']}】…」の依頼で使えます。")


def on_rejected(state: dict, entry: dict, why: str) -> None:
    if entry.get("action") == "routine.publish":
        prop = entry.get("proposal") or {}
        queue_improve(state, prop.get("machine", ""), why or "試運転の結果が期待と違う", prop.get("run", ""))


# ---------------------------------------------------------------- 返事


def take_reply(d: Path, state: dict, policy: dict, text: str, base: dict, ts: float, h: dict) -> "str | None":
    if not enabled(policy):
        return None
    m = FIX_RE.match(text)
    if m:
        target, why = m.group(1), m.group(2).strip()
        run = find_run(d, target) if re.match(r"^r\d+$", target) else None
        name = run["machine"] if run else target
        if not find_entry(d, policy, name):
            h["event"](state, f"「直す {target}」: その業務が見つかりません", ts, base)
            return "unknown-routine"
        queue_improve(state, name, why or "利用者が直すよう頼んだ", run["id"] if run else "")
        h["event"](state, f"業務 {name} を直す依頼を受けました（理由: {why or 'なし'}）。手が空いたら直します", ts, base)
        return "improve"
    m = ROLLBACK_RE.match(text)
    if m:
        name = m.group(1)
        reg = load_registry(d)
        rec = reg.get(name) or {}
        history = list(rec.get("history") or [])
        if rec.get("status") != "ready" or not history:
            h["event"](state, f"「戻す {name}」: 戻せる前の版がありません", ts, base)
            return "no-history"
        prev = history.pop()
        target = Path(rec["path"])
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(prev["path"], target)
        rec.update({"version": prev["version"], "digest": digest(target), "history": history, "approved_at": ts})
        reg[name] = rec
        save_registry(d, reg)
        state.setdefault("held", []).append(f"業務 {name} を版 {prev['version']} に戻しました。")
        h["event"](state, f"業務 {name} を版 {prev['version']} に戻しました", ts, base)
        return "rollback"
    return None


# ---------------------------------------------------------------- フックの範囲


def hook_scope(d: Path, state: dict, policy: dict) -> dict:
    """業務の回で、フックが追加で通す範囲。"""
    scope = {"read": [], "scripts": [], "deny_scripts": [], "write_only": None}
    if not enabled(policy):
        return scope
    scope["read"] = [str(p) for p in skills_dirs(policy)] + [str(p) for p in sources(d, policy)]
    job = state.get("routine")
    if not job:
        return scope
    sm = statemachine_dir(policy)
    if sm:
        scope["scripts"].append(str(sm))
    if job["mode"] in ("run", "trial"):
        scope["read"].append(job["path"])
        for s in job.get("skills") or []:
            if not s.get("dir"):
                continue
            if job["mode"] == "trial" and s["writes"]:
                scope["deny_scripts"].append(s["dir"])
            else:
                scope["scripts"].append(s["dir"])
    else:
        selector = find_skill(policy, "skill-selector")
        if selector:
            scope["scripts"].append(str(selector))
        scope["write_only"] = job["draft"]
    return scope


SCRIPT_CMD = re.compile(r'^\s*(?:"([^"]+)"|(\S+))\s+(?:"([^"]+)"|(\S+))')


def script_of(command: str) -> "str | None":
    """「インタプリタ スクリプト …」の形なら、スクリプトのパス。"""
    m = SCRIPT_CMD.match(command)
    if not m:
        return None
    interp = Path((m.group(1) or m.group(2)).replace("\\", "/")).name.lower()
    interp = interp[:-4] if interp.endswith(".exe") else interp
    if interp not in INTERPRETERS:
        return None
    script = m.group(3) or m.group(4)
    return script if re.search(r"\.(py|js|sh)$", script, re.I) else None


def status_lines(d: Path, state: dict, policy: dict) -> "list[str]":
    if not enabled(policy):
        return []
    out = []
    job = state.get("routine")
    out.append(f"業務の進行: {job['id']} {job['name']} の{MODE_WORDS.get(job['mode'], job['mode'])}" if job else "業務の進行: なし")
    if state.get("routine_queue"):
        out.append("直す予定: " + "、".join(q["name"] for q in state["routine_queue"]))
    reg = load_registry(d)
    for e in catalog(d, policy):
        rate = f"{e['ok']}/{e['runs']} 回成功" if e["runs"] else "未実行"
        steady = "・安定" if (reg.get(e["name"]) or {}).get("steady") else ""
        out.append(f"  {e['name']}（{e['status']}・版 {e['version']}・{rate}{steady}）")
    try:
        report = json.loads((d / WORK / REVIEW).read_text(encoding="utf-8"))
        reviewed_at = dt.datetime.fromtimestamp(report["reviewed_at"]).astimezone().isoformat(timespec="seconds")
    except (OSError, ValueError, KeyError, TypeError):
        return out                       # 見直しの報告が壊れていても、様子を見るだけは止めない
    count = sum(f["action"] == "needs_review" for f in report["findings"])
    out.append(f"見直し: {reviewed_at}・" + (f"要見直し {count} 件" if count else "要見直しなし"))
    return out
