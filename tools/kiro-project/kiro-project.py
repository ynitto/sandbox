#!/usr/bin/env python3
"""kiro-project — Loop Engineering MVP（単一プロジェクトのバックログを捌く制御層）

カレントディレクトリ（または --root）をプロジェクトルートとし、charter.md / repos.json を
入力に backlog を消化して成果物（archive/ / DELIVERY.md / needs/ / decisions/）を出力する。

正準ループ（設計書 docs/designs/kiro-project-design.md §2）:
  ① backlog/（案件毎ファイル）を読み優先順位をつけ、最優先タスクを kiro-flow に投げる
  ② 優先順位付けは原則 kiro-cli。stub 時は最古優先（FIFO）。人間は policy.md で上書きできる
  ③ kiro-flow の結果を verify ゲートで検証。done はファイル削除、NG は積み直す
  ④ backlog が尽きるか予算（サイクル数/実時間）が尽きるまで反復。--watch なら尽きても
     プロセスは生存して監視（エージェントは待機しない＝idle 中は kiro-cli/flow を起動しない）
  ⑤ ユーザーの判断は案件毎の decisions/<id>.md に保存。needs/<id>.md のフィードバック欄に
     書き込むと拾って再開する

二層構成: kiro-flow が実行（act）、kiro-project が優先順位付け・検証・収束・決定記録を担う。
複数プロジェクトは「1 プロジェクト = 1 ディレクトリ = 1 プロセス」で並べ、束ねた可視化・操作は
kiro-projects-viewer が git 越しに担う。
標準ライブラリのみ。kiro-cli が無くても --planner none / --flow-planner stub / --executor stub で動く。
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # 非 POSIX では daemon 検知不可（常に run にフォールバック）
    fcntl = None

VALID_STATUS = ("inbox", "draft", "proposed", "ready", "doing", "done", "blocked", "review",
                "offloaded", "rejected")
CONSUMABLE = ("ready", "todo")  # 実行待ち。todo は ready の後方互換エイリアス。draft は消化対象外
# proposed: 実行前レビュー待ち（plan_review・既定 on）。人の承認（approve）で初めて ready になり、
#   差し戻し（needs feedback）は kiro-project がタスクを修正して再提案、却下（reject）は廃止＋再計画。
# rejected: 却下済み（archive へ退避される終端。DELIVERY には載せない）。
# offloaded: 実行層 daemon へ非ブロッキングで submit 済み・結果待ち（act_async）。CONSUMABLE ではない
#   （再 submit しない）が「機械が実行中」＝人待ちでもない。次パスでポーリングして終端したら settle する。
TASK_HEADER_RE = re.compile(r"^##\s+(?P<id>\S+?):\s*(?P<title>.*)$")
FIELD_RE = re.compile(r"^-\s+(?P<key>\w+):\s*(?P<val>.*)$")
POLICY_RE = re.compile(r"^(?P<key>deny|pin|defer|offload|gate|protect|route|spec):\s*(?P<val>.+)$")
DR_HEADER_RE = re.compile(r"^##\s+DR-(\d+)\b")
LEARN_RE = re.compile(r"^- learn:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
# 回避知識（hold/deny 由来）。learn が「どう解けば良いか（auto-resolve 向け）」なのに対し、
# avoid は「この種のタスクは自動実行してはいけない（人の判断が要る）」を運ぶ。投入/triage 時に
# 類似タスクを検出して ready へ落とさず inbox（人の triage）へ寄せる予防リコールに使う。
# 第2グループ名を guide に揃え、learn 用の照合ヘルパ（_best_learn_match）をそのまま再利用する。
AVOID_RE = re.compile(r"^- avoid:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")
LTM_CATEGORY = "kiro-project"  # ltm-use home 内のカテゴリ（昇格先サブディレクトリ）
FEEDBACK_MARKER = "## フィードバック"                  # 旧形式（読み取りは継続サポート）
DECISION_MARKER = "## Decision Outcome"               # MADR 形式の決定記入欄（needs の生成はこちら）
FEEDBACK_MARKERS = (FEEDBACK_MARKER, DECISION_MARKER)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]")        # 確定チェックボックス行（任意状態）
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]")          # チェック済み（= 確定）

# 停止理由
REASON_DRAINED = "drained"  # 消化可能タスクが尽きた（実質完了）
REASON_BUDGET = "budget"    # 予算（サイクル数/実時間）が尽きた
REASON_COST = "cost"        # 予算（トークン/金額）が尽きた
REASON_THROTTLE = "throttle"  # ソフト予算（throttle 比率）超過＝自動スロットル（watch は report へ降格）


# ---------------------------------------------------------------------------
# タスク（案件毎ファイル backlog/<id>.md）
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    title: str
    status: str = "ready"
    source: str = "human"
    priority: int = 0      # 外部で付与する優先度（大きいほど高優先。none planner で使う）
    verify: str = ""
    retries: int = 0
    extra: "list[tuple[str, str]]" = field(default_factory=list)

    def norm_status(self) -> str:
        return self.status if self.status in VALID_STATUS or self.status == "todo" else "ready"

    def consumable(self) -> bool:
        return self.norm_status() in CONSUMABLE

    def matches(self, pattern: str) -> bool:
        p = pattern.strip().lower()
        return bool(p) and (p in self.id.lower() or p in self.title.lower())

    def feedback(self) -> "str | None":
        return self.get("feedback")

    def get(self, key: str, default=None):
        """追加フィールド（extra）の値。重複キーは dict と同じく最後を採る。"""
        return dict(self.extra).get(key, default)

    def set(self, key: str, value) -> None:
        """追加フィールドを 1 つに正規化して設定（既存の同名は落としてから付け直す）。"""
        self.drop(key)
        self.extra.append((key, str(value)))

    def drop(self, *keys: str) -> None:
        self.extra = [(k, v) for k, v in self.extra if k not in keys]


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """端末カラー等の ANSI エスケープを除去する。
    kiro-cli の出力にはカラーコードが混ざるため、合成した verify を
    シェルで実行する前に正規化しないと `\\x1b[..m` が混入してコマンドが壊れる。"""
    return _ANSI_RE.sub("", text or "")


def _strip_code(val: str) -> str:
    v = strip_ansi(val).strip()
    if len(v) >= 2 and v.startswith("`") and v.endswith("`"):
        return v[1:-1]
    return v


def _now_ts() -> str:
    """人間可読のローカル時刻 `YYYY-MM-DD HH:MM:SS`（journal/納品書/needs などの記録に使う共通形式）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_task(text: str, tid: str) -> Task:
    """1ファイル＝1タスク。id はファイル名（tid）を正とする。"""
    t = Task(id=tid, title=tid)
    for line in text.splitlines():
        m = TASK_HEADER_RE.match(line)
        if m:
            t.title = m.group("title").strip() or tid
            continue
        fm = FIELD_RE.match(line)
        if not fm:
            continue
        key, val = fm.group("key").strip(), fm.group("val").strip()
        if key == "status":
            t.status = val or "ready"
        elif key == "source":
            t.source = val or "human"
        elif key == "priority":
            try:
                t.priority = int(val)
            except ValueError:
                t.priority = 0
        elif key == "verify":
            t.verify = _strip_code(val)
        elif key == "retries":
            try:
                t.retries = int(val)
            except ValueError:
                t.retries = 0
        else:
            t.extra.append((key, val))
    return t


def serialize_task(task: Task) -> str:
    out = [
        f"## {task.id}: {task.title}",
        f"- status: {task.norm_status()}",
        f"- source: {task.source}",
        f"- priority: {task.priority}",
        f"- verify: {f'`{task.verify}`' if task.verify else ''}",
        f"- retries: {task.retries}",
    ]
    out += [f"- {k}: {v}" for k, v in task.extra]
    return "\n".join(out) + "\n"


def load_tasks(backlog_dir: Path) -> "list[Task]":
    """backlog/ の各 *.md を1タスクとして読む。最古優先（mtime 昇順）に並べる。"""
    if not backlog_dir.exists():
        return []
    files = sorted(backlog_dir.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    return [parse_task(p.read_text(encoding="utf-8"), p.stem) for p in files]


def persist_task(cfg: "Config", task: Task) -> None:
    cfg.backlog.mkdir(parents=True, exist_ok=True)
    (cfg.backlog / f"{task.id}.md").write_text(serialize_task(task), encoding="utf-8")


def delete_task_file(cfg: "Config", task: Task) -> None:
    p = cfg.backlog / f"{task.id}.md"
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
# enqueue（汎用の取り込み口）— 外部ソース(webhook/メール/issue 抽出)は薄いアダプタで
#   ここへ流し込む。コアは stdlib のみ・ネットワーク非依存・決定的を保つ。
# ---------------------------------------------------------------------------
ENQUEUE_KNOWN_KEYS = {"id", "title", "verify", "priority", "source", "status",
                      "after", "review", "note", "accept", "verify_template", "repos",
                      "workspace", "refs", "paths", "routed_by",
                      "cohort_items", "cohort", "cohort_role"}


def _slug_id(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (text or "").strip()).strip("-")
    return s[:48]


def _unique_task_id(cfg: "Config", base: str, include_archive: bool = False) -> str:
    existing = {p.stem for p in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    if include_archive:
        # 自動採番では archive も避ける: 退避済みと同じ id を採番すると、後で archive へ移す際に
        # 過去の記録を上書きしてしまう（複数 charter で同名タスクが並ぶと同秒衝突が現実に起きる）。
        adir = cfg.archive_dir()
        if adir.exists():
            existing |= {p.stem for p in adir.glob("*.md")}
    base = base or "task"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _gen_task_id(cfg: "Config", explicit: "str | None", title: str) -> str:
    if explicit:
        # 明示 id は**冪等キー**（intake の重複判定・再投入の追跡に使う）なので改名しない
        # （backlog 内の衝突だけ回避）。archive 側の上書きは退避時にファイル名で避ける。
        return _unique_task_id(cfg, _slug_id(explicit) or "task")
    slug = _slug_id(title)
    base = (f"{slug[:24]}-{datetime.now().strftime('%H%M%S')}" if slug
            else "enq-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    return _unique_task_id(cfg, base, include_archive=True)


def task_from_spec(cfg: "Config", spec: dict) -> Task:
    """spec(dict) を検証して Task を作る。title 必須。status 未指定なら verify 有→ready / 無→inbox。"""
    title = str(spec.get("title", "") or "").strip()
    if not title:
        raise ValueError("title は必須です")
    verify = _strip_code(str(spec.get("verify", "") or "").strip())
    accept = str(spec.get("accept", "") or "").strip()
    tmpl = str(spec.get("verify_template", "") or "").strip()
    tid = _gen_task_id(cfg, spec.get("id"), title)
    # verify が無くても accept / verify_template があれば「verify を用意できる」ので ready 扱い（後で展開/合成）
    has_plan = bool(verify or accept or tmpl)
    explicit = str(spec.get("status", "") or "").strip()
    default_status = "ready" if has_plan else "inbox"
    # 実行前レビュー（plan_review・既定 on）: status を明示しない新規投入はすべて proposed で入り、
    # 人の承認（approve）で初めて実行可能（ready/inbox）になる（plan/enqueue/inbox/followup/intake 全経路）。
    if not explicit and getattr(cfg, "plan_review", False):
        default_status = "proposed"
    status = explicit or default_status
    t = Task(id=tid, title=title, status=status, verify=verify,
             source=str(spec.get("source", "") or "enqueue"))
    try:
        t.priority = int(spec.get("priority", 0) or 0)
    except (TypeError, ValueError):
        t.priority = 0
    for k in ("after", "review", "note", "accept", "verify_template", "repos",   # 既知の追加フィールド
              "workspace", "refs", "paths", "routed_by"):   # ルーティング: 書込先・参照repo・触るパス・解決経路
        v = spec.get(k)
        if v not in (None, "", []):
            t.extra.append((k, ",".join(map(str, v)) if isinstance(v, list) else str(v)))
    for k, v in spec.items():                        # 未知キーも保持（取りこぼさない）
        if k not in ENQUEUE_KNOWN_KEYS and v not in (None, "", []):
            t.extra.append((str(k), str(v)))
    if not t.verify and tmpl:                        # テンプレは決定的＝enqueue 時に即展開（エージェント不要）
        ex = expand_verify_template(tmpl)
        if ex:
            t.verify = ex
            t.extra.append(("verify_source", "template"))
    return t


def enqueue_task(cfg: "Config", spec: dict) -> Task:
    # cohort_items があれば「pilot 先行 → 人レビューで指示を固める → 残りを生成」の cohort にする
    if spec.get("cohort_items"):
        t = create_cohort(cfg, spec)
    else:
        t = task_from_spec(cfg, spec)
    cfg.backlog.mkdir(parents=True, exist_ok=True)
    persist_task(cfg, t)
    return t


# ---------------------------------------------------------------------------
# cohort（pilot-then-batch）— タスク分解で生じた「同様手順の繰り返しタスク」を、
#   まず 1 件（pilot）だけ走らせて verify→review:human で指示を固め、その定義を元に
#   残りのタスク指示を生成して実行する。act 非依存（残りは通常ループが任意の act で消化）。
#   pilot 承認の検知は既存の review/approve を再利用し、承認時に残りを materialize する。
# ---------------------------------------------------------------------------
COHORT_ITEM_TOKEN = "{item}"


def _apply_item(template: str, item: str, fallback: bool = True) -> str:
    """テンプレ中の {item} を対象で差し込む。
    fallback=True（title 用）はプレースホルダ無しなら末尾に対象を付す。
    fallback=False（verify 用）はプレースホルダ無しならコマンドをそのまま使う（全要素で共通）。"""
    if COHORT_ITEM_TOKEN in template:
        return template.replace(COHORT_ITEM_TOKEN, item)
    if not fallback:
        return template
    return f"{template}（対象: {item}）" if template else item


def _cohort_path(cfg: "Config", cid: str) -> Path:
    return cfg.cohorts_dir() / f"{cid}.json"


def _write_cohort(cfg: "Config", state: dict) -> None:
    cfg.cohorts_dir().mkdir(parents=True, exist_ok=True)
    _cohort_path(cfg, state["id"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_cohort(cfg: "Config", cid: str) -> "dict | None":
    p = _cohort_path(cfg, cid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _unique_cohort_id(cfg: "Config", base: str) -> str:
    existing = {p.stem for p in cfg.cohorts_dir().glob("*.json")} if cfg.cohorts_dir().exists() else set()
    base = base or "cohort"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def create_cohort(cfg: "Config", spec: dict) -> Task:
    """同様手順の cohort を作る: 先頭要素を pilot（review:human）として 1 件だけ作り、
    残りは cohort 状態へ保持する。pilot 承認後に materialize_cohort_rest が残りを生成する。"""
    items = [str(x).strip() for x in (spec.get("cohort_items") or []) if str(x).strip()]
    title_t = str(spec.get("title", "") or "").strip()
    verify_t = _strip_code(str(spec.get("verify", "") or "").strip())
    if not title_t:
        raise ValueError("cohort には title が必要です")
    if not items:
        raise ValueError("cohort には cohort_items が必要です")
    cid = _unique_cohort_id(cfg, _slug_id(title_t) or "cohort")
    pilot_item, rest = items[0], items[1:]
    repos = spec.get("repos")
    pilot_spec = {
        "title": _apply_item(title_t, pilot_item),
        "verify": _apply_item(verify_t, pilot_item, fallback=False) if verify_t else "",
        "accept": spec.get("accept"),
        "review": "human",                 # pilot は人の承認（feedback）で指示を固める
        "source": str(spec.get("source", "") or "cohort"),
        "repos": repos,
        "priority": spec.get("priority", 0),
    }
    pilot = task_from_spec(cfg, pilot_spec)
    pilot.set("cohort", cid)
    pilot.set("cohort_role", "pilot")
    persist_task(cfg, pilot)
    _write_cohort(cfg, {
        "id": cid,
        "pilot_id": pilot.id,
        "title_template": title_t,
        "verify_template": verify_t,
        "accept": str(spec.get("accept", "") or ""),
        "items": rest,                     # pilot 承認後に生成する残り要素
        "repos": ",".join(repos) if isinstance(repos, list) else (repos or ""),
        "source": str(spec.get("source", "") or "cohort"),
        "status": "pending",
        "feedback": "",
    })
    append_journal(cfg.journal, f"cohort {cid}: pilot {pilot.id} を作成（残り {len(rest)} 件は承認後に生成）")
    return pilot


def materialize_cohort_rest(cfg: "Config", pilot: Task, feedback: str = "") -> "list[Task]":
    """pilot で固まった定義を元に残りの cohort タスクを生成して ready にする。
    pilot の承認理由・feedback を各メンバの feedback に載せ、固めた指示を必ず反映させる。"""
    cid = pilot.get("cohort")
    if not cid:
        return []
    state = _read_cohort(cfg, cid)
    if not state or state.get("status") != "pending":
        return []
    guidance = "\n".join(x for x in [state.get("feedback", ""), feedback, pilot.feedback() or ""] if x).strip()
    repos = state.get("repos") or None
    created: "list[Task]" = []
    for item in state.get("items", []):
        mspec = {
            "title": _apply_item(state["title_template"], item),
            "verify": _apply_item(state["verify_template"], item, fallback=False) if state.get("verify_template") else "",
            "accept": state.get("accept") or None,
            "source": str(state.get("source", "") or "cohort"),
            "repos": repos,
        }
        m = task_from_spec(cfg, mspec)
        m.set("cohort", cid)
        m.set("cohort_role", "member")
        if guidance:
            m.set("feedback", guidance)     # build_request が「必ず反映」として act へ渡す
        persist_task(cfg, m)
        created.append(m)
    state["status"] = "done"
    state["feedback"] = guidance
    _write_cohort(cfg, state)
    append_journal(cfg.journal,
                   f"cohort {cid}: pilot {pilot.id} 承認 → 固めた定義から残り {len(created)} 件を生成")
    return created


def cohort_reflux(cfg: "Config", task: "Task", guidance: str) -> int:
    """gitlab で cohort のメンバ/pilot が却下されたら、その指摘を**同 cohort の未完了の兄弟メンバ**へ波及させる。
    materialize_cohort_rest（pilot 承認からの一方向）に対し、こちらは却下起点で兄弟へ横展開する（双方向化）。
    波及した件数を返す。cohort でない/指摘が空なら 0。"""
    cid = task.get("cohort")
    if not cid or not guidance.strip():
        return 0
    n = 0
    for sib in load_tasks(cfg.backlog):
        if sib.id == task.id or sib.get("cohort") != cid:
            continue
        if sib.norm_status() == "done":
            continue
        sib.drop("feedback")
        sib.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
        persist_task(cfg, sib)
        n += 1
    state = _read_cohort(cfg, cid)
    if state is not None:
        merged = "\n".join(x for x in [state.get("feedback", ""), guidance] if x).strip()
        state["feedback"] = merged[:2000]
        _write_cohort(cfg, state)
    if n:
        append_journal(cfg.journal, f"cohort {cid}: {task.id} の却下指摘を未完了メンバ {n} 件へ波及")
    return n


def ingest_inbox(cfg: "Config") -> "list[Task]":
    """inbox/ に置かれたファイルを backlog タスクへ取り込む（.json=オブジェクト/配列 / .md=タスク形式）。
    取り込めたら元ファイルを消す。外部ソースの共通入口（watch がこの口を監視して起こす）。"""
    created: list[Task] = []
    inbox = cfg.inbox
    if not inbox or not inbox.exists():
        return created
    for f in sorted(inbox.glob("*")):
        if f.is_dir():
            continue
        try:
            if f.suffix.lower() == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
                for sp in (data if isinstance(data, list) else [data]):
                    if isinstance(sp, dict):
                        created.append(enqueue_task(cfg, sp))
            elif f.suffix.lower() in (".md", ".markdown", ".txt"):
                t = parse_task(f.read_text(encoding="utf-8"), f.stem)
                t.id = _unique_task_id(cfg, _slug_id(t.id) or "task")
                if t.source == "human":
                    t.source = "inbox"
                if t.norm_status() == "ready" and not has_verify_plan(t):
                    t.status = "inbox"               # verify も用意材料(accept/template)も無ければ人の triage へ
                if getattr(cfg, "plan_review", False) and t.norm_status() in ("ready", "inbox"):
                    t.status = "proposed"            # 実行前レビュー: 承認まで実行しない
                cfg.backlog.mkdir(parents=True, exist_ok=True)
                persist_task(cfg, t)
                created.append(t)
            else:
                continue
        except (OSError, ValueError) as e:
            append_journal(cfg.journal, f"inbox 取り込み失敗: {f.name}: {e}")
            continue
        try:
            f.unlink()
        except OSError:
            pass
    if created:
        append_journal(cfg.journal, f"inbox 取り込み {[t.id for t in created]}")
    return created


# intake の最終実行時刻（プロジェクト＝backlog パス毎。--project all の 1 プロセス多重化に対応）
_INTAKE_LAST: "dict[str, float]" = {}


def run_intake(cfg: "Config") -> "list[Task]":
    """取り込みコマンド（intake_cmd）を実行し、stdout の JSON（spec オブジェクト/配列＝
    `enqueue --json` と同形式）を backlog へ**冪等に**取り込む。外部の決定的ゲート/検出器
    （例: `codd-gate tasks --debt`）を watch の周期で汲み上げる汎用フック。

    - **冪等**: spec の `id` が現役 backlog（blocked/review 含む）に居れば飛ばす。定期実行しても
      同じ発見が重複投入されない（done→archive 後に同じ発見が再発したら新タスクとして積み直せる）。
    - **有限・無害**: verify_timeout で打ち切り、exit≠0・非 JSON・例外は journal に残して無視
      （ループは殺さない）。intake_interval（秒）で律速し、0 以下なら毎回。
    - 常駐（長期実行）は kiro-project 側が持つ。intake_cmd 自体は単発・有界であること。"""
    if not cfg.intake_cmd:
        return []
    interval = float(cfg.intake_interval or 0)
    key = str(cfg.backlog)
    now = time.time()
    if interval > 0 and now - _INTAKE_LAST.get(key, 0.0) < interval:
        return []
    _INTAKE_LAST[key] = now
    try:
        p = subprocess.run(cfg.intake_cmd, shell=True, cwd=str(cfg.workdir),
                           capture_output=True, text=True, timeout=cfg.verify_timeout)
    except (OSError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"intake 実行失敗: {e}")
        return []
    if p.returncode != 0:
        append_journal(cfg.journal, f"intake NG (exit {p.returncode}): {cfg.intake_cmd}")
        return []
    out = (p.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        append_journal(cfg.journal, "intake 出力が JSON でないため無視")
        return []
    created: "list[Task]" = []
    existing = {f.stem for f in cfg.backlog.glob("*.md")} if cfg.backlog.exists() else set()
    for sp in (data if isinstance(data, list) else [data]):
        if not isinstance(sp, dict):
            continue
        sid = _slug_id(str(sp.get("id", "") or ""))
        if sid and sid in existing:
            continue                        # 冪等: 現役 backlog に居る発見は再投入しない
        try:
            created.append(enqueue_task(cfg, sp))
        except ValueError as e:
            append_journal(cfg.journal, f"intake spec 無効: {e}")
            continue
        if sid:
            existing.add(sid)
    if created:
        append_journal(cfg.journal, f"intake 取り込み {[t.id for t in created]}")
    return created


_FOLLOWUP_LINE_RE = re.compile(r"^@followup\s+(?P<spec>.+)$")


def parse_followups(parent: "Task", act_msg: str) -> "list[tuple[str, str]]":
    """完了タスクから派生タスク仕様 (title, verify) を集める。2 経路:
    静的: 親タスクの `- followup: <title> [:: <verify>]`／
    動的: act 出力の `@followup <title> [:: <verify>]` 行（エージェントが「ついでに見つけた」を吐く）。"""
    specs: list[tuple[str, str]] = []

    def add(raw: str):
        raw = raw.strip()
        if not raw:
            return
        title, _, verify = raw.partition("::")
        specs.append((title.strip(), _strip_code(verify.strip())))

    for k, v in parent.extra:
        if k == "followup":
            add(v)
    for line in (act_msg or "").splitlines():
        m = _FOLLOWUP_LINE_RE.match(line.strip())
        if m:
            add(m.group("spec"))
    return specs


def spawn_followups(cfg: "Config", parent: "Task", specs: "list[tuple[str, str]]",
                    tasks: "list[Task] | None", cap: int) -> "list[Task]":
    """派生タスクを backlog/<parent>-fN.md として作る（source=followup）。verify があれば ready で
    即消化対象、無ければ inbox（triage で人へ）。cap でこの run の生成数を制限し暴走を防ぐ。
    tasks を渡すと同じ run 内で自走消化できるよう追記する。"""
    spawned: list[Task] = []
    existing = {p.stem for p in cfg.backlog.glob("*.md")}
    if tasks:
        existing |= {t.id for t in tasks}
    n = 0
    for title, verify in specs:
        if len(spawned) >= cap or not title:
            break
        n += 1
        while f"{parent.id}-f{n}" in existing:
            n += 1
        nid = f"{parent.id}-f{n}"
        existing.add(nid)
        child = Task(id=nid, title=title, status=("ready" if verify else "inbox"),
                     source="followup", verify=verify, extra=[("parent", parent.id)])
        persist_task(cfg, child)
        if tasks is not None:
            tasks.append(child)
        spawned.append(child)
        append_decision(cfg, nid, "auto", context=f"{parent.id}（{parent.title}）から派生生成",
                        action="spawn-followup", reason=title[:120],
                        affects=f"{nid} → {child.status}")
    return spawned


# ---------------------------------------------------------------------------
# policy.md（人間による順位付け・実行先の上書き）
# ---------------------------------------------------------------------------
@dataclass
class Policy:
    deny: "list[str]" = field(default_factory=list)
    pin: "list[str]" = field(default_factory=list)
    defer: "list[str]" = field(default_factory=list)
    offload: "list[str]" = field(default_factory=list)
    gate: "list[str]" = field(default_factory=list)   # verify PASS でも人の承認を要する（検収ゲート）
    protect: "list[str]" = field(default_factory=list)  # この**パス**を act が触ったら done にせず人の承認へ
    route: "list[str]" = field(default_factory=list)  # `<パターン> -> <repo名>`: タスク→書込先ワークスペースの割当ルール
    spec: "list[str]" = field(default_factory=list)   # 採点に依らず spec 前段を強制するタスクパターン（spec_track 時）


def parse_policy(text: str) -> Policy:
    pol = Policy()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = POLICY_RE.match(line)
        if m:
            getattr(pol, m.group("key")).append(m.group("val").strip())
    return pol


def load_policy(path: Path) -> Policy:
    return parse_policy(path.read_text(encoding="utf-8")) if path.exists() else Policy()


_REVIEW_VALUES = {"human", "manual", "required", "yes", "true", "1"}


def needs_human_review(task: "Task", policy: "Policy") -> bool:
    """verify PASS でも人の承認(検収)を要するか。タスクの `- review: human` か policy の
    `gate: <パターン>` 一致で gate（高リスク・不可逆・質的受け入れ等を人へ）。既定はゲート無し。"""
    if task.get("review", "").strip().lower() in _REVIEW_VALUES:
        return True
    return any(task.matches(p) for p in policy.gate)


# ---------------------------------------------------------------------------
# タスク単位の自律レベル — 実効 level = 明示 `- level:` > 自動昇格(track) > グローバル `--level`。
#   安全網（protect/gate/regression/review:human）は level に依らず締める方向で常時上乗せ。
LEVELS = ("report", "assisted", "unattended")     # 自律度の梯子（左ほど人の関与が大）


def _level_rank(level: str) -> int:
    try:
        return LEVELS.index((level or "").strip().lower())
    except ValueError:
        return LEVELS.index("unattended")          # 未知値は最も自律的（＝既定）に倒す


def _autonomy_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "autonomy"


def _autonomy_path(cfg: "Config", track: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", track)[:80] or "_"
    return _autonomy_dir(cfg) / f"{safe}.json"


def _autonomy_get(cfg: "Config", track: str, cache: "dict | None" = None) -> "dict | None":
    """track の自動昇格レコードを返す（無ければ None）。cache があれば読みを1回に抑える。"""
    if cache is not None and track in cache:
        return cache[track]
    p = _autonomy_path(cfg, track)
    rec = None
    if p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = None
    if cache is not None:
        cache[track] = rec
    return rec


def resolve_level(task: "Task", cfg: "Config", cache: "dict | None" = None) -> str:
    """タスクの実効自律レベル。明示 `- level:` を最優先（ピン）、次に track の自動昇格、無ければグローバル。"""
    explicit = task.get("level", "").strip().lower()
    if explicit in LEVELS:
        return explicit
    if cfg.auto_level:
        track = task.get("track", "").strip()
        if track:
            rec = _autonomy_get(cfg, track, cache)
            lvl = (rec or {}).get("level")
            if lvl in LEVELS:
                return lvl
    return cfg.level


def autonomy_record(cfg: "Config", task: "Task", clean: bool,
                    cache: "dict | None" = None) -> "tuple | None":
    """track の実績を1件記録し、必要なら level を昇格/降格する。`--auto-level` かつ `- track:` 付きのみ。
    clean=完了が手戻りなし（auto-done / approve）、False=手戻り（差し戻し/回帰/偽done/revert）。
    昇格: 連続 clean ≥ promote_after かつ rework_rate ≤ rework_max で ceiling まで1段上げ。
    降格: 手戻り1件で assisted を下限に1段下げ、累計2回で assisted にピンし自動管理を停止。"""
    if not cfg.auto_level:
        return None
    track = task.get("track", "").strip()
    if not track:
        return None
    rec = _autonomy_get(cfg, track, cache) or {
        "track": track, "level": cfg.level, "clean_streak": 0,
        "recent": [], "demotions": 0, "pinned": False}
    n = max(1, cfg.level_window)
    recent = (list(rec.get("recent", [])) + [bool(clean)])[-n:]
    rec["recent"] = recent
    cur = rec.get("level", cfg.level)
    transition = None
    if clean:
        rec["clean_streak"] = int(rec.get("clean_streak", 0)) + 1
        rework_rate = 1.0 - (sum(1 for x in recent if x) / len(recent))
        if (not rec.get("pinned") and rec["clean_streak"] >= cfg.level_promote_after
                and rework_rate <= cfg.level_rework_max
                and _level_rank(cur) < _level_rank(cfg.auto_level_max)
                and _level_rank(cur) < _level_rank("unattended")):
            new = LEVELS[_level_rank(cur) + 1]
            rec["level"], rec["clean_streak"] = new, 0
            transition = ("promote", cur, new, rework_rate)
    else:
        rec["clean_streak"] = 0
        if not rec.get("pinned"):
            rec["demotions"] = int(rec.get("demotions", 0)) + 1
            if rec["demotions"] >= 2:                      # 2回目の手戻り → assisted にピンし人へ
                rec["level"], rec["pinned"] = "assisted", True
                transition = ("pin", cur, "assisted", None)
            else:
                lowered = LEVELS[max(_level_rank("assisted"), _level_rank(cur) - 1)]
                if lowered != cur:
                    rec["level"] = lowered
                    transition = ("demote", cur, lowered, None)
    rec["updated"] = datetime.now().isoformat(timespec="seconds")
    _autonomy_dir(cfg).mkdir(parents=True, exist_ok=True)
    _autonomy_path(cfg, track).write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    if cache is not None:
        cache[track] = rec
    if transition:
        kind, old, new, rate = transition
        label = {"promote": "昇格", "demote": "降格", "pin": "人へピン(自動停止)"}[kind]
        append_decision(cfg, f"track:{track}", "auto",
                        context=f"track `{track}` の自律度を{label}",
                        action=f"autolevel-{kind}",
                        reason=f"{old}→{new}"
                               + (f" rework_rate={rate:.2f}" if rate is not None else "")
                               + f" recent={['o' if x else 'x' for x in recent]}",
                        affects=f"track:{track} → {new}")
    return transition


# ---------------------------------------------------------------------------
# パス保護ゲート（safety denylist）— act が触ったファイルが policy の `protect:` に
#   一致したら、verify=PASS でも done にせず人の承認(review)へ。無人運用の blast radius を縮める。
#   .env / secrets / auth / payments / migrations / infra など「自動で触らせない」場所を守る。
# ---------------------------------------------------------------------------
def _glob_to_regex(pat: str) -> str:
    """glob → 正規表現。`*`=スラッシュ以外の任意 / `**`=スラッシュ含む任意（`**/` は 0 階層も許容）。"""
    i, out = 0, []
    while i < len(pat):
        if pat[i] == "*":
            if pat[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if i < len(pat) and pat[i] == "/":   # `**/` は途中ディレクトリ 0 個も一致させる
                    out.append("/?")
                    i += 1
                continue
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return "(?s:" + "".join(out) + r")\Z"


def path_protected(path: str, patterns: "list[str]") -> "str | None":
    """path が protect パターン群のどれかに一致すれば、その（最初の）パターンを返す。無ければ None。"""
    p = path.replace("\\", "/").lstrip("/")
    if p.startswith("./"):
        p = p[2:]
    for pat in patterns:
        pat = (pat or "").strip().replace("\\", "/")
        if pat and re.match(_glob_to_regex(pat), p):
            return pat
    return None


def _git_out(workdir: "Path", *args: str, timeout: float = 30) -> str:
    try:
        r = subprocess.run(["git", "-C", str(workdir), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _git_dirty_paths(workdir: "Path") -> "set[str]":
    """作業ツリーの未コミット変更パス（git status --porcelain）。"""
    out: set[str] = set()
    for line in _git_out(workdir, "status", "--porcelain", "-uall").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:                       # rename: old -> new は new 側を見る
            path = path.split(" -> ", 1)[1].strip().strip('"')
        if path:
            out.add(path)
    return out


def git_change_baseline(workdir: "Path") -> "tuple[str, frozenset]":
    """act 前のスナップショット（HEAD と、その時点で既に dirty なパス集合）。"""
    return (_git_out(workdir, "rev-parse", "HEAD").strip(),
            frozenset(_git_dirty_paths(workdir)))


def changed_paths_since(workdir: "Path", baseline: "tuple[str, frozenset] | None") -> "set[str]":
    """baseline 以降に act が変更したパス集合（新規 dirty ＋ baseline 以降のコミット差分）。
    git でないと空（best-effort）。remote/daemon 実行は workdir に出ないので保護対象外。"""
    if baseline is None:
        return set()
    head0, dirty0 = baseline
    changed = _git_dirty_paths(workdir) - set(dirty0)     # act で新たに dirty 化した分
    head1 = _git_out(workdir, "rev-parse", "HEAD").strip()
    if head0 and head1 and head0 != head1:                # act がコミットした分
        for line in _git_out(workdir, "diff", "--name-only", f"{head0}..{head1}").splitlines():
            if line.strip():
                changed.add(line.strip())
    return changed


def _kiro_managed_rels(cfg: "Config") -> "set[str]":
    """kiro-project 自身の状態ファイル/ディレクトリの、workdir からの相対パス集合。
    backlog/needs/decisions/archive/claims/inbox/bus・journal/DELIVERY/run-log/policy は
    『成果物』ではなく運用状態なので、進捗判定（no-progress）や成果参照から除外する。"""
    wd = cfg.workdir.resolve()
    cand = [cfg.backlog, cfg.needs, cfg.decisions, cfg.archive_dir(), cfg.journal,
            Path(cfg.delivery) if cfg.delivery else None, cfg.runlog, cfg.policy,
            cfg.bus, cfg.inbox, _claims_dir(cfg), commands_dir(cfg)]
    rels: set[str] = set()
    for p in cand:
        if not p:
            continue
        try:
            rels.add(str(p.resolve().relative_to(wd)))
        except (ValueError, OSError):
            continue                                       # workdir 外 → git status に出ない
    return rels


def meaningful_changes(cfg: "Config", baseline: "tuple[str, frozenset] | None") -> "set[str]":
    """act が生んだ『成果物としての』変更（kiro-project 自身の状態ファイルを除いた差分）。"""
    changed = changed_paths_since(cfg.workdir, baseline)
    managed = _kiro_managed_rels(cfg)
    return {c for c in changed
            if not any(c == r or c.startswith(r + "/") for r in managed)}


def append_policy(path: Path, key: str, value: str) -> None:
    header = "" if path.exists() else "# kiro-project policy（人間による上書き）\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}{key}: {value}\n")


# ---------------------------------------------------------------------------
# 決定記録（案件毎 decisions/<id>.md）
# ---------------------------------------------------------------------------
def decision_path(cfg: "Config", tid: str) -> Path:
    return cfg.decisions / f"{tid}.md"


def next_dr_id(path: Path) -> str:
    n = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DR_HEADER_RE.match(line)
            if m:
                n = max(n, int(m.group(1)))
    return f"DR-{n + 1:04d}"


def append_decision(cfg: "Config", tid: str, actor: str, context: str,
                    action: str, reason: str, affects: str,
                    learn: "tuple[str, str] | None" = None,
                    avoid: "tuple[str, str] | None" = None) -> str:
    """決定記録を追記。learn=(title, guidance) を渡すと『- learn:』行を残し、
    将来 find_learned_resolution が類似タスクへ自動適用できる学習材料にする。
    avoid=(title, reason) を渡すと『- avoid:』行を残し、hold/deny の予防知識として
    投入/triage 時の類似タスク検出（find_avoidance）に使えるようにする。"""
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    path = decision_path(cfg, tid)
    dr = next_dr_id(path)
    date = datetime.now().strftime("%Y-%m-%d")
    block = (f"## {dr}  {date}  actor: {actor}\n"
             f"- context : {context}\n- action  : {action}\n"
             f"- reason  : {reason}\n- affects : {affects}\n")
    if learn:
        title, guide = learn
        block += f"- learn: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    if avoid:
        title, guide = avoid
        block += f"- avoid: {title.replace(chr(10), ' ')} :: {guide.replace(chr(10), ' ')}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")
    return dr


# ---------------------------------------------------------------------------
# DR 学習（過去の人の判断から類似案件を自動解決して通知を減らす）
# ---------------------------------------------------------------------------
def _title_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _best_learn_match(task: Task, threshold: float, files: "list[Path]",
                      label, skip_id: "str | None" = None,
                      pattern: "re.Pattern" = LEARN_RE) -> "tuple[str, str] | None":
    """与えた md 群の該当行（既定 `- learn:`／pattern で `- avoid:` 等に切替）を Jaccard で
    タイトル照合し最良を返す（決定的・LLM 不要）。pattern は title/guide の名前付きグループを持つこと。"""
    best, best_score = None, 0.0
    for f in sorted(files):
        if skip_id is not None and f.stem == skip_id:  # 自分の履歴は除く（自己ループ防止）
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            if not m:
                continue
            score = _title_overlap(task.title, m.group("title"))
            if score >= threshold and score > best_score:
                best, best_score = (label(f), m.group("guide").strip()), score
    return best


def count_gitlab_reject_recur(cfg: "Config", task: Task) -> int:
    """他タスクの決定記録から、**gitlab 却下**でありタイトルが Jaccard 類似の件数を数える（決定的）。
    同種の却下が反復しているか（＝分解/verify/policy を系として見直すべきか）の判断材料。自分の履歴は除く。"""
    if not cfg.decisions.exists():
        return 0
    n = 0
    for f in sorted(cfg.decisions.glob("*.md")):
        if f.stem == task.id:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in re.split(r"(?=^## DR)", text, flags=re.M):
            if "action  : gitlab-reject" not in block:
                continue
            # 却下された元タスクの**生タイトル**（context の （…） 内）で照合する。蒸留後の learn
            # タイトルは一般化されていて raw タイトルとの Jaccard が効きにくいため context を優先。
            m = re.search(r"（(?P<title>[^（）]+)）が gitlab で却下", block)
            if not m:
                m = re.search(r"^- learn:\s*(?P<title>.+?)\s*::", block, flags=re.M)
            cand = m.group("title") if m else ""
            if cand and _title_overlap(task.title, cand) >= cfg.learn_threshold:
                n += 1
                break                                  # 1 ファイル（=1 タスク）につき 1 回
    return n


def find_learned_resolution(cfg: "Config", task: Task) -> "tuple[str, str] | None":
    """過去の人の判断（learn）からタイトルが十分似た指示を探す。返り値 (出典, 指示文)。

    ① ローカル `decisions/` を照合 → ② ヒット無し かつ cfg.ltm なら ltm-use home を横断照合。
    どちらも決定的なファイル走査＋Jaccard で、エージェント（LLM）を一切起動しない。"""
    local = []
    if cfg.decisions.exists():
        local = _best_learn_match(task, cfg.learn_threshold,
                                  list(cfg.decisions.glob("*.md")),
                                  label=lambda f: f.stem, skip_id=task.id)
    if local:
        return local
    if cfg.ltm:
        mem_dir = ltm_memories_dir(cfg)
        if mem_dir and mem_dir.exists():
            return _best_learn_match(task, cfg.learn_threshold, list(mem_dir.glob("*.md")),
                                     label=lambda f: f"ltm:{f.stem}")
    return None


def find_avoidance(cfg: "Config", task: Task) -> "tuple[str, str] | None":
    """過去の hold/deny 判断（`- avoid:`）からタイトルが十分似た案件を探す。返り値 (出典, 理由)。

    learn（どう解けば良いか＝auto-resolve 向け）とは別軸で、『この種は自動実行させない＝人へ』の
    予防知識。投入/triage の段階で ready へ落とす前に照合し、一致すれば inbox（人の triage）へ寄せる。
    ローカル `decisions/` の決定的走査＋Jaccard のみ（エージェント不要）。"""
    if not cfg.decisions.exists():
        return None
    return _best_learn_match(task, cfg.learn_threshold, list(cfg.decisions.glob("*.md")),
                             label=lambda f: f.stem, skip_id=task.id, pattern=AVOID_RE)


def apply_intake_recall(cfg: "Config", task: Task) -> "str | None":
    """投入/triage 時の予防リコール（shift-left）。intake_recall 有効かつ task が消化対象(ready)で、
    過去の hold 判断（avoid）に類似するなら、実行前に **blocked＋needs（人の判断）へ寄せて**理由を残す。
    DR 学習が『失敗してから』人を絞るのに対し、これは『投入の時点で』先回りして止める。人は
    `approve`（実行を許可）か `hold`（恒久デニー化）で裁定できる。返り値は寄せた理由（表示用）。
    該当なし・無効・非消化なら None（タスクは素通り）。

    ※ inbox ではなく blocked にするのは、verify を持つタスクは triage が inbox→ready へ自動昇格する
    ため（人の判断を待たずに実行され得る）。hold と同じ blocked＋needs が『人の裁定待ち』の正しい状態。"""
    if not cfg.intake_recall or task.norm_status() not in CONSUMABLE:
        return None
    hit = find_avoidance(cfg, task)
    if not hit:
        return None
    src, reason = hit
    task.set("recall", f"{src} :: {reason}")   # 人が needs で見えるよう出典と理由を残す
    why = (f"予防リコール: 過去に hold した案件（{src}）に類似するため実行前に人の判断へ。"
           f"理由: {reason}（許可するなら approve、恒久デニーなら hold）")
    _block(cfg, task, why, {})                 # blocked＋needs/<id>.md（persist はここで行う）
    append_decision(cfg, task.id, "auto",
                    context=f"{task.id}（{task.title}）を投入時リコールで人の判断へ",
                    action="intake-recall", reason=f"過去の hold（{src}）に類似: {reason}",
                    affects=f"{task.id} → blocked, needs/{task.id}.md")
    return reason


# ---------------------------------------------------------------------------
# ltm-use への学習昇格（決定的・エージェント不要。home の Markdown を直接読み書き）
# ---------------------------------------------------------------------------
def resolve_ltm_home(arg: "str | None") -> Path:
    """ltm-use ストアのルート: 明示指定 → 環境変数 KIRO_LTM_HOME → ~/.claude。"""
    raw = arg or os.environ.get("KIRO_LTM_HOME") or "~/.claude"
    return Path(raw).expanduser()


def ltm_memories_dir(cfg: "Config") -> "Path | None":
    """昇格先 `<home>/memory/home/memories/kiro-project`。ltm 無効なら None。"""
    if not cfg.ltm or cfg.ltm_home is None:
        return None
    return cfg.ltm_home / "memory" / "home" / "memories" / LTM_CATEGORY


# ---------------------------------------------------------------------------
# 稼働インスタンスのレジストリ（外部から「いま見ているフォルダ」を発見可能にする）
#
# run（特に --watch 常駐）中、監視中のルートと OS/WSL 情報を共通 home に記録する。
# 外部の操作者（kiro-project スキル等）が `instances` で発見し、同じフォルダへ
# 読み書きできる。プロセスは WSL で動き操作側は Windows/WSL という構成を想定し、
# 可能なら Windows パス（wslpath -w）も併記する。
# ---------------------------------------------------------------------------
def resolve_state_home() -> Path:
    """インスタンス・レジストリ等の置き場: 環境変数 KIRO_PROJECT_HOME → ~/.kiro-project。"""
    raw = os.environ.get("KIRO_PROJECT_HOME") or "~/.kiro-project"
    return Path(raw).expanduser()


def instances_dir() -> Path:
    return resolve_state_home() / "instances"


# リモート（別ホスト）レコードは PID が当てにならないので heartbeat の鮮度で生死を見る。
INSTANCE_TTL = 90.0           # heartbeat からこの秒数を超えたリモートレコードは「停止」とみなす
REMOTE_PRUNE_GRACE = 86400.0  # これより古い（=長期間死んでいる）リモートレコードは誰が掃除してもよい


def resolve_registry_dirs(extra: "list | str | None" = None) -> "list[Path]":
    """レコードを書く/読むディレクトリ群。先頭が自分の書き込み先（ローカル home）。
    KIRO_PROJECT_REGISTRY（os.pathsep 区切り）と extra（--registry）を共有レジストリとして加える。
    共有先を NFS / 同期フォルダ / git バスのチェックアウト等にすると、別ホスト同士が相互発見できる
    （core は決定的なファイル操作のみ。ネットワークは共有先の仕組みが担うので不変条件④⑤を保つ）。"""
    dirs = [instances_dir()]
    seen = {dirs[0]}
    sources: list[str] = []
    env = os.environ.get("KIRO_PROJECT_REGISTRY")
    if env:
        sources += env.split(os.pathsep)
    if extra:
        sources += extra if isinstance(extra, list) else [extra]
    for s in sources:
        s = (s or "").strip()
        if not s:
            continue
        p = Path(s).expanduser()
        if p not in seen:
            dirs.append(p)
            seen.add(p)
    return dirs


def _split_registry(arg: "list | str | None") -> "list[str]":
    """--registry の値（os.pathsep 区切り文字列 / 繰り返しリスト）を正規化した list にする。"""
    if not arg:
        return []
    items = arg if isinstance(arg, list) else [arg]
    out: list[str] = []
    for it in items:
        out += [s for s in str(it).split(os.pathsep) if s.strip()]
    return out


def _instance_filename(rec: dict) -> str:
    """ホスト修飾のレコードファイル名（共有レジストリで別ホストの同一 PID と衝突しないように）。"""
    host = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(rec.get("host", "host")) or "host")
    return f"{host}-{rec.get('pid', 0)}.json"


def _record_alive(rec: dict) -> bool:
    """レコードの生死。ローカルホストは PID で、別ホストは heartbeat の鮮度（TTL）で判定する。"""
    if str(rec.get("host", "")) == socket.gethostname():
        return _pid_alive(int(rec.get("pid", -1)))
    hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
    ttl = float(rec.get("ttl", INSTANCE_TTL) or INSTANCE_TTL)
    return (time.time() - hb) <= max(ttl, INSTANCE_TTL)


def detect_runtime() -> dict:
    """実行環境（linux / wsl / windows / darwin）と WSL ディストロ名を判定する。"""
    info: dict = {"runtime": "linux", "wsl_distro": None}
    distro = os.environ.get("WSL_DISTRO_NAME")
    is_wsl = False
    try:
        with open("/proc/version", encoding="utf-8", errors="ignore") as f:
            is_wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    if distro or is_wsl:
        info["runtime"], info["wsl_distro"] = "wsl", distro
    elif sys.platform.startswith("win"):
        info["runtime"] = "windows"
    elif sys.platform == "darwin":
        info["runtime"] = "darwin"
    return info


def to_windows_path(p: "str | Path") -> "str | None":
    """WSL パス → Windows パス（`wslpath -w`）。wslpath が無ければ None。"""
    if not shutil.which("wslpath"):
        return None
    try:
        out = subprocess.run(["wslpath", "-w", str(p)], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def instance_record(cfg: "Config") -> dict:
    """このプロセスの監視対象（プロジェクトルートと主要パス・OS/WSL 情報）を表す発見用レコード。
    外部操作者が CLI を組むときは `root` を `--root` に渡す（1 プロジェクト = 1 ルート）。"""
    root = cfg.backlog.parent.resolve()
    rt = detect_runtime()
    rec = {
        "pid": os.getpid(),
        "root": str(root),
        "project": cfg.project_name or root.name,
        "backlog": str(cfg.backlog.resolve()),
        "needs": str(cfg.needs.resolve()),
        "commands": str(commands_dir(cfg).resolve()),
        "decisions": str(cfg.decisions.resolve()),
        "archive": str(cfg.archive_dir().resolve()),
        "policy": str(cfg.policy.resolve()),
        "delivery": str(Path(cfg.delivery).resolve()),
        "journal": str(cfg.journal.resolve()),
        "workdir": str(cfg.workdir.resolve()),
        "watch": cfg.watch,
        "started_at": time.time(),
        "started_iso": datetime.now().isoformat(timespec="seconds"),
        "heartbeat": time.time(),                               # 生存信号（リモート発見の鮮度判定に使う）
        "heartbeat_iso": datetime.now().isoformat(timespec="seconds"),
        "ttl": max(INSTANCE_TTL, cfg.poll * 3),                 # poll より十分長くしてフラッピングを防ぐ
        "host": socket.gethostname(),
        "python": sys.executable,
        **rt,
    }
    if rt["runtime"] == "wsl":
        rec["root_windows"] = to_windows_path(root)  # \\wsl.localhost\<distro>\... 等。無ければ None
    return rec


def register_instance(cfg: "Config", extra: "list | str | None" = None) -> "list[Path]":
    """全レジストリ（ローカル home＋共有先）に自分を登録し、書けたファイルパス一覧を返す。
    共有先にも書くことで別ホストから発見される（失敗しても run は止めない）。"""
    rec = instance_record(cfg)
    blob = json.dumps(rec, ensure_ascii=False, indent=2)
    fname = _instance_filename(rec)
    written: list[Path] = []
    for d in resolve_registry_dirs(extra):
        try:
            d.mkdir(parents=True, exist_ok=True)
            p = d / fname
            p.write_text(blob, encoding="utf-8")
            written.append(p)
        except OSError:
            continue
    return written


def refresh_instance(paths: "list[Path]") -> None:
    """登録済みレコードの heartbeat を更新する（watch の各パス/idle で呼ぶ＝リモートに生存を示す）。"""
    now = time.time()
    iso = datetime.now().isoformat(timespec="seconds")
    for p in paths:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["heartbeat"], rec["heartbeat_iso"] = now, iso
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            continue


def _start_heartbeat_thread(cfg: "Config", paths: "list[Path]",
                            interval: "float | None" = None) -> "threading.Event":
    """watch 中、本体が長い処理でブロックしている間も心拍を打ち続けるデーモンスレッドを起動し、
    停止用の Event を返す（set() で次のティックに終わる。プロセス終了は待たせない＝daemon）。

    従来 heartbeat は watch の各パスと idle でしか打てなかった。1 タスクの実行（エージェント
    CLI の呼び出しや kiro-flow run）は数分〜数十分ブロックするため、その間に INSTANCE_TTL
    （90 秒）を大きく超えて心拍が途切れる。外から見ると死んだように見え、kiro-projects-viewer
    では稼働中のプロジェクトが「停止中」や「別マシンで稼働中」と誤表示されていた
    （viewer は鮮度切れの instances レコードを捨て、status.json の鮮度判定へ落ちるため）。

    status.json は state_git のコミット対象なので、ここでは既存のポリシー
    （maybe_heartbeat_status＝設定 status_interval。既定 0＝無効）にそのまま従う。
    無効なら触らない＝idle の git 負荷は従来と変わらない。"""
    stop = threading.Event()
    if interval is None:
        interval = max(5.0, INSTANCE_TTL / 3.0)   # ttl の 1/3。切れる前に必ず 1 回は打つ

    def _beat() -> None:
        while not stop.wait(interval):
            with contextlib.suppress(Exception):   # 心拍の失敗で run を巻き込まない
                refresh_instance(paths)
            with contextlib.suppress(Exception):
                maybe_heartbeat_status(cfg)

    threading.Thread(target=_beat, name="kiro-project-heartbeat", daemon=True).start()
    return stop


def _maybe_prune(rec: dict, f: Path) -> None:
    """死んだレコードの掃除。自ホストのものは即削除、リモートは長期（grace 超）に限り削除。
    他ホストの最近のレコードは（共有先での競合を避け）触らない。"""
    try:
        if str(rec.get("host", "")) == socket.gethostname():
            f.unlink()
        else:
            hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
            if (time.time() - hb) > REMOTE_PRUNE_GRACE:
                f.unlink()
    except OSError:
        pass


def list_instances(prune: bool = True, extra: "list | str | None" = None) -> list:
    """生存中のインスタンス一覧（ローカル＋共有レジストリを横断）。同一インスタンスが複数ディレクトリに
    現れたら heartbeat が新しい方を採用。死んだレコードは _maybe_prune で掃除する。"""
    best: dict = {}                          # (host,pid,root) -> (rec, heartbeat)
    for d in resolve_registry_dirs(extra):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not _record_alive(rec):
                if prune:
                    _maybe_prune(rec, f)
                continue
            key = (str(rec.get("host", "")), int(rec.get("pid", -1)), str(rec.get("root", "")))
            hb = float(rec.get("heartbeat", rec.get("started_at", 0)) or 0)
            cur = best.get(key)
            if cur is None or hb > cur[1]:
                best[key] = (rec, hb)
    return [v[0] for v in best.values()]


def cmd_instances(as_json: bool = False, extra: "list | str | None" = None) -> int:
    """稼働中の kiro-project（監視中プロジェクトルート）を一覧。外部操作者の発見口。
    共有レジストリを併用すると別ホストのインスタンスも横断表示する。"""
    recs = list_instances(prune=True, extra=extra)
    recs.sort(key=lambda r: (str(r.get("host", "")), int(r.get("pid", 0))))
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print("稼働中の kiro-project はありません（run/--watch 起動時に登録されます）。")
        return 0
    me = socket.gethostname()
    for r in recs:
        rt = r.get("runtime", "?")
        if r.get("wsl_distro"):
            rt += f":{r['wsl_distro']}"
        flags = "watch" if r.get("watch") else "run"
        host = str(r.get("host", "?"))
        where = "" if host == me else f" @{host}(remote)"
        print(f"pid={r['pid']} [{rt}] {flags}{where}  root={r['root']}")
        if r.get("root_windows"):
            print(f"    Windows: {r['root_windows']}")
    return 0


# ---------------------------------------------------------------------------
# 常駐ライフサイクル（start / stop / restart）— レジストリ(§4)の上に起動・停止操作を一級化
# ---------------------------------------------------------------------------
def _self_script() -> str:
    """この CLI 本体スクリプトの絶対パス（子プロセス起動に使う）。"""
    return str(Path(__file__).resolve())


def _norm_root(root: str) -> str:
    return str(Path(root).expanduser().resolve())


def _drop_instance_record(rec: dict, extra: "list | str | None" = None) -> None:
    """このレコードのファイルを全レジストリから消す（ホスト修飾名＋旧 `<pid>.json` 形式の両方）。"""
    fname = _instance_filename(rec)
    pid = rec.get("pid")
    for d in resolve_registry_dirs(extra):
        for name in (fname, f"{pid}.json"):
            try:
                (d / name).unlink()
            except OSError:
                pass


def _reap(pid: int) -> None:
    """対象が自分の子なら回収してゾンビ化を防ぐ（他人の子・未対応は無視）。"""
    try:
        os.waitpid(pid, os.WNOHANG)
    except (OSError, ChildProcessError, AttributeError):
        pass


def select_instances(root: "str | None" = None, pid: "int | None" = None,
                     want_all: bool = False, extra: "list | str | None" = None) -> list:
    """稼働インスタンスを root / pid / 全件 で選ぶ。
    停止対象に使うため自ホストのレコードのみを返す（別ホストの PID へはシグナルを送れない）。"""
    me = socket.gethostname()
    recs = [r for r in list_instances(prune=True, extra=extra) if str(r.get("host", "")) == me]
    if want_all:
        return recs
    nr = _norm_root(root) if root else None
    out = []
    for r in recs:
        if pid is not None and int(r.get("pid", -1)) == pid:
            out.append(r)
            continue
        if nr is not None and str(r.get("root", "")) == nr:
            out.append(r)
    return out


def cmd_stop(root: "str | None" = None, pid: "int | None" = None,
             want_all: bool = False, timeout: float = 5.0,
             extra: "list | str | None" = None,
             config: "str | None" = None) -> int:
    """稼働インスタンスへ SIGTERM（必要なら SIGKILL）を送り、レジストリも掃除する（自ホストのみ）。"""
    if not pid and not want_all:                  # 既定は cwd（または --root/設定）のプロジェクトを止める
        root = _resolved_root(root, config)
    targets = select_instances(root, pid, want_all, extra=extra)
    if not targets:
        print("停止対象の稼働インスタンスが見つかりません（instances で確認できます）。", file=sys.stderr)
        return 1
    all_ok = True
    for r in targets:
        p = int(r["pid"])
        if p == os.getpid():                  # 自分自身は決して止めない（安全ガード）
            continue
        try:
            os.kill(p, signal.SIGTERM)        # graceful: 子側の SIGTERM ハンドラが finally で後始末
        except OSError as e:
            print(f"pid={p}: SIGTERM 失敗（{e}）", file=sys.stderr)
            all_ok = False
            continue
        deadline = time.time() + timeout
        while time.time() < deadline and _pid_alive(p):
            _reap(p)
            time.sleep(0.1)
        if _pid_alive(p) and hasattr(signal, "SIGKILL"):  # 居残りは強制終了（POSIX のみ）
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
            time.sleep(0.2)
            _reap(p)
        _drop_instance_record(r)
        ok = not _pid_alive(p)
        all_ok = all_ok and ok
        print(f"pid={p} {'停止しました' if ok else '停止できませんでした'}  root={r.get('root')}")
    return 0 if all_ok else 1


def _resolved_root(root: "str | None", config: "str | None" = None) -> str:
    """start/stop/restart 用にプロジェクトルートを絶対パス文字列で返す。
    build_config の root 計算と一致させ、稼働インスタンスの記録 root と突き合わせる。
    --root 未指定なら設定ファイルの root を読む（daemon 子プロセスは resolve_config 経由で
    設定の root に付くため、ここが cwd 固定だと重複検出・stop の照合が外れうる）。
    root は cwd 相対で解決する（workdir は root 配下の作業場所であってアンカーではない）。"""
    if not root:
        path = _find_config(config)
        filecfg = _load_config_file(path) if path else {}
        root = str(filecfg.get("root") or ".")
    p = Path(root).expanduser()
    p = p if p.is_absolute() else (Path.cwd() / p)
    return str(p.resolve())


def cmd_start(root: "str | None" = None, config: "str | None" = None,
              force: bool = False, extra: "list | str | None" = None) -> int:
    """`run --watch` を切り離して常駐起動する（detached）。重複監視は既定で拒否（--force で許可）。
    監視対象は cwd（または --root/設定ファイルの root）のプロジェクト 1 つ。"""
    expected = _resolved_root(root, config)
    me = socket.gethostname()
    dup = [r for r in list_instances(prune=True, extra=extra)
           if str(r.get("root", "")) == expected and str(r.get("host", "")) == me]
    if dup and not force:
        print(f"既に root={expected} を監視中です（pid={dup[0]['pid']}）。重複起動は --force、"
              f"再起動は restart を使ってください。", file=sys.stderr)
        return 1
    child = [sys.executable, _self_script(), "run", "--watch"]
    if root:
        child += ["--root", root]
    if config:
        child += ["--config", config]
    for r in _split_registry(extra):            # 共有レジストリを子 daemon にも引き継ぐ
        child += ["--registry", r]
    log_dir = resolve_state_home() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"{_slug(expected)}.log"
        logf = open(log, "a", encoding="utf-8")
    except OSError:
        log, logf = None, subprocess.DEVNULL
    try:
        proc = subprocess.Popen(child, stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"起動に失敗しました: {e}", file=sys.stderr)
        return 1
    finally:
        if hasattr(logf, "close"):
            try:
                logf.close()
            except OSError:
                pass
    deadline = time.time() + 5.0                # 登録（レジストリ出現）を確認
    registered = False
    while time.time() < deadline:
        if any(int(r.get("pid", -1)) == proc.pid for r in list_instances(prune=False, extra=extra)):
            registered = True
            break
        if not _pid_alive(proc.pid):
            break
        time.sleep(0.2)
    status = "起動しました" if (registered and _pid_alive(proc.pid)) else \
             "起動しましたが登録未確認（log を確認してください）"
    print(f"{status} pid={proc.pid} root={expected}" + (f" log={log}" if log else ""))
    return 0 if _pid_alive(proc.pid) else 1


def cmd_restart(root: "str | None" = None, config: "str | None" = None,
                extra: "list | str | None" = None) -> int:
    """同じプロジェクト root の監視を停止してから起動し直す。"""
    proot = _resolved_root(root, config)
    if select_instances(root=proot, extra=extra):
        cmd_stop(root=proot, extra=extra)
    return cmd_start(root=root, config=config, force=True, extra=extra)



def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "memory"


def count_learn_hits(cfg: "Config") -> "dict[str, int]":
    """各 learn ルール（出典 DR id）が auto-resolve で実際に効いた回数を数える（昇格の根拠）。"""
    hits: dict[str, int] = {}
    if not cfg.decisions.exists():
        return hits
    pat = re.compile(r"learned from (?:ltm:)?(?P<src>\S+?):")
    for df in cfg.decisions.glob("*.md"):
        for line in df.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- reason"):
                m = pat.search(line)
                if m:
                    src = m.group("src")
                    hits[src] = hits.get(src, 0) + 1
    return hits


def collect_learnings(cfg: "Config") -> "list[tuple[str, str, str]]":
    """decisions/ の全 learn ルールを (出典id, title, guide) で列挙。"""
    out: list[tuple[str, str, str]] = []
    if not cfg.decisions.exists():
        return out
    for df in sorted(cfg.decisions.glob("*.md")):
        for line in df.read_text(encoding="utf-8").splitlines():
            m = LEARN_RE.match(line)
            if m:
                out.append((df.stem, m.group("title").strip(), m.group("guide").strip()))
    return out


def _promote_marker(cfg: "Config", src: str) -> bool:
    p = decision_path(cfg, src)
    return p.exists() and "- promoted:" in p.read_text(encoding="utf-8")


def write_ltm_memory(mem_dir: Path, title: str, guide: str, src: str, hits: int) -> str:
    """ltm-use 記憶フォーマット（frontmatter＋本文）で1件書き出し、記憶IDを返す。

    本文に機械可読な `- learn: <title> :: <guide>` を残し、recall 時に同じ LEARN_RE で読み戻す。"""
    mem_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(mem_dir.glob("*.md"))) + 1
    date = datetime.now().strftime("%Y-%m-%d")
    memid = f"mem-{datetime.now().strftime('%Y%m%d')}-{n:03d}"
    name = f"{_slug(title)}-{n:03d}"
    summary = guide.replace("\n", " ")[:120]
    body = (
        f"---\n"
        f"id: {memid}\n"
        f"title: \"{title}\"\n"
        f"created: \"{date}\"\n"
        f"updated: \"{date}\"\n"
        f"status: active\n"
        f"scope: home\n"
        f"tags: [{LTM_CATEGORY}, learn]\n"
        f"memory_type: procedural\n"
        f"promoted_from: \"decisions/{src}.md\"\n"
        f"access_count: {hits}\n"
        f"summary: \"{summary}\"\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## コンテキスト\n"
        f"kiro-project の判断ノウハウ。出典 decisions/{src}.md で {hits} 回再利用され昇格。\n\n"
        f"## 学び・結論\n"
        f"- learn: {title} :: {guide}\n"
    )
    (mem_dir / f"{name}.md").write_text(body, encoding="utf-8")
    return memid


def promote_learnings(cfg: "Config") -> "list[tuple[str, str]]":
    """効果が再現した learn ルール（hits ≥ promote_threshold・未昇格）を ltm-use home へ昇格。

    返り値 [(出典id, 記憶id)]。ltm 無効や home 未解決なら何もしない（グレースフル no-op）。"""
    mem_dir = ltm_memories_dir(cfg)
    if mem_dir is None:
        return []
    hits = count_learn_hits(cfg)
    seen: set[str] = set()
    promoted: list[tuple[str, str]] = []
    for src, title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold or _promote_marker(cfg, src):
            continue
        seen.add(src)
        memid = write_ltm_memory(mem_dir, title, guide, src, hits[src])
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write(f"- promoted: {memid}（ltm-use home へ昇格 / hits={hits[src]}）\n")
        append_journal(cfg.journal, f"学習昇格: {src} → ltm-use {memid}（hits={hits[src]}）")
        promoted.append((src, memid))
    return promoted


# ---------------------------------------------------------------------------
# プロジェクトルール（rules.md）— フローを回して判明した恒常ルール（暗黙知）の明文化先。
#   learn/avoid（decisions/）が「タイトル類似のタスクにだけ recall される」のに対し、
#   rules.md は **全タスクの act / plan / verify 合成へ常時注入**される（charter と同列・有界）。
#   人が直接書くのが正で、システムは「効果が再現した learn」を決定的に昇格・追記するだけ
#   （出典コメント付き・人がいつでも編集/削除できる）。注入はプロンプト文脈を足すだけで、
#   done 条件・予算・policy には触れない（不変条件 1–3 を保つ）。
# ---------------------------------------------------------------------------
RULES_AUTO_SECTION = "## 自動昇格（システムが追記・人が編集/削除してよい）"


def rules_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "rules.md"


def project_rules_context(cfg: "Config", limit: int = 1200) -> str:
    """rules.md を有界に読み出す（出典の HTML コメントは注入しない）。無ければ空（後方互換）。
    charter の constraints が「目標の制約」なのに対し、rules.md は「運用で判明したやり方の規則」。"""
    p = rules_path(cfg)
    if not p.exists():
        return ""
    try:
        txt = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt[:limit]


def _rules_marker(cfg: "Config", src: str) -> bool:
    dp = decision_path(cfg, src)
    return dp.exists() and "- rules-promoted:" in dp.read_text(encoding="utf-8")


def promote_rules(cfg: "Config") -> "list[str]":
    """効果が再現した learn ルール（auto-resolve hits ≥ promote_threshold）を rules.md へ昇格する。
    ltm 昇格（プロジェクト横断・opt-in --ltm）とは独立の**プロジェクト内・常時注入層**で既定 on。
    決定的・冪等: 同一 guide は再追記せず、昇格済みは DR の `- rules-promoted:` マーカーで跳ぶ。"""
    if not getattr(cfg, "rules_capture", True):
        return []
    hits = count_learn_hits(cfg)
    if not hits:
        return []
    p = rules_path(cfg)
    text = ""
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return []
    promoted: "list[str]" = []
    seen: "set[str]" = set()
    for src, _title, guide in collect_learnings(cfg):
        if src in seen or hits.get(src, 0) < cfg.promote_threshold:
            continue
        seen.add(src)
        if guide in text or _rules_marker(cfg, src):
            continue
        if not text:
            text = ("# プロジェクトルール\n\n"
                    "<!-- フローを回して判明した恒常ルール（暗黙知）の明文化先。人が書くのが正。\n"
                    "     全タスクの act / plan / verify 合成に常時注入される（有界）。 -->\n")
        if RULES_AUTO_SECTION not in text:
            text = text.rstrip() + f"\n\n{RULES_AUTO_SECTION}\n"
        date = datetime.now().strftime("%Y-%m-%d")
        text = text.rstrip() + f"\n- {guide}  <!-- learn:{src} hits={hits[src]} {date} -->\n"
        with decision_path(cfg, src).open("a", encoding="utf-8") as f:
            f.write("- rules-promoted: rules.md\n")
        append_journal(cfg.journal, f"ルール昇格: {src} → rules.md（hits={hits[src]}）")
        promoted.append(src)
    if promoted:
        p.write_text(text, encoding="utf-8")
    return promoted


def normalize_title(t: Task) -> str:
    return re.sub(r"\s+", " ", t.title.strip().lower())


def file_age_days(cfg: "Config", tid: str) -> float:
    p = cfg.backlog / f"{tid}.md"
    return (time.time() - p.stat().st_mtime) / 86400.0 if p.exists() else 0.0


def detect_rot(cfg: "Config", tasks: "list[Task]") -> "list[tuple[Task, str]]":
    """腐ったタスクを検出: unverifiable（verify無）/ duplicate（同題）/ stale（古い）。"""
    out: list[tuple[Task, str]] = []
    seen: dict[str, str] = {}
    for t in tasks:
        if not t.consumable():
            continue
        if not has_verify_plan(t):                # accept / verify_template があれば verify を用意できる
            out.append((t, "unverifiable（verify 未定義）"))
            continue
        nt = normalize_title(t)
        if nt in seen:
            out.append((t, f"duplicate（{seen[nt]} と重複）"))
            continue
        seen[nt] = t.id
        if cfg.rot_age_days and file_age_days(cfg, t.id) > cfg.rot_age_days:
            out.append((t, f"stale（{cfg.rot_age_days:.0f}日以上未処理）"))
    return out


# ---------------------------------------------------------------------------
# 通知（案件毎 needs/<id>.md）＋ フィードバック往復
# ---------------------------------------------------------------------------
def needs_path(cfg: "Config", tid: str) -> Path:
    return cfg.needs / f"{tid}.md"


def _madr_frontmatter(rec_id: str, kind: str, risk: str = "") -> str:
    """needs/<id>.md の MADR（Markdown Any Decision Records）互換 frontmatter。
    status は常に proposed で生成し、人の確定（[x]）＝決定。ファイル自体は取り込み時に
    消費され、恒久の決定記録は decisions/<id>.md（DR）に残る。
    risk（low/med/high）は検収票のリスクダイジェスト総合値（viewer のバッジ用）。"""
    return (
        "---\n"
        "status: proposed\n"
        f"date: {_now_ts()[:10]}\n"
        "decision-makers: [human]\n"
        f"task-id: {rec_id}\n"
        f"kind: {kind}\n"
        + (f"risk: {risk}\n" if risk else "")
        + "---\n\n"
    )


def write_needs_file(cfg: "Config", task: Task, reason: str, review: bool = False,
                     evidence: str = "", kind: str = "",
                     risk: "tuple[str, str] | None" = None) -> None:
    cfg.needs.mkdir(parents=True, exist_ok=True)
    if kind == "plan-review":   # 実行前レビュー（proposed。承認されるまで実行しない）
        state = "proposed（実行前レビュー待ち・未実行）"
        hint = (f"<!-- 承認して実行を許可するなら `kiro-project approve {task.id}`（または空のまま [x]）。\n"
                f"     差し戻す（kiro-project にタスクを修正させる）なら下に修正指示を書いて [x]。\n"
                f"     却下（廃止して関連バックログを再計画）なら `kiro-project reject {task.id} --reason ...`。 -->\n")
        evidence_block = f"\n## タスク定義（レビュー対象）\n{evidence}\n" if evidence else ""
        body = (
            f"{_madr_frontmatter(task.id, kind)}"
            f"# 実行前レビュー: {task.id} — {task.title}\n\n"
            f"## Context and Problem Statement\n\n"
            f"- なぜ: {reason}\n"
            f"- 状態: {state}\n"
            f"{evidence_block}\n"
            f"{DECISION_MARKER}\n\n"
            f"<!-- 人の決定の記入欄。承認は空のまま [x]、差し戻しは修正指示を書いて [x]。 -->\n"
            f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
            f"{hint}"
        )
        needs_path(cfg, task.id).write_text(body, encoding="utf-8")
        return
    if review:    # verify=PASS の承認ゲート（検収待ち）
        state = "review（検収待ち・verify=PASS）"
        kind = "review"
        hint = (f"<!-- 承認して done 確定するなら `kiro-project approve {task.id}`。\n"
                f"     差し戻すなら下に修正方針を書いて [x] にする（再実行されます）。 -->\n")
    else:
        state = "blocked（kiro-project の判断待ち）"
        kind = "blocked"
        hint = (f"<!-- 上の [ ] を [x] にした時だけ反映されます（書きかけでの誤発火を防ぐため）。\n"
                f"     下に修正方針・指示を書いてください。空のままでも [x] なら『そのまま再実行』。\n"
                f"     コマンドなら `kiro-project approve {task.id}`。 -->\n")
    # 判断材料（成果物の所在・差分・検証）。人がレビューせずに済むよう「どこに・何が・なぜ」を載せる。
    evidence_block = f"\n## 判断材料（成果物の所在・差分・検証）\n{evidence}\n" if evidence else ""
    # リスクダイジェスト（検収票のみ・決定的な材料のみ）。総合値は frontmatter（viewer バッジ用）にも載せる。
    risk_block = f"\n## リスク\n{risk[1]}\n" if risk else ""
    body = (
        f"{_madr_frontmatter(task.id, kind, risk=risk[0] if risk else '')}"
        f"# 要対応: {task.id} — {task.title}\n\n"
        f"## Context and Problem Statement\n\n"
        f"- なぜ: {reason}\n"
        f"- 状態: {state}\n"
        f"{evidence_block}"
        f"{risk_block}\n"
        f"{DECISION_MARKER}\n\n"
        f"<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
        f"{hint}"
    )
    needs_path(cfg, task.id).write_text(body, encoding="utf-8")


def _task_definition_block(task: Task) -> str:
    """実行前レビュー票に載せるタスク定義（人がレビューする対象そのもの）。"""
    lines = [f"- title  : {task.title}",
             f"- verify : `{task.verify}`" if task.verify else "- verify : （未定義）"]
    for k in ("accept", "verify_template", "after", "note", "workspace", "charter",
              "assess", "route"):   # assess=投入時採点（c/r/a）・route=spec ルーティングの決定
        v = task.get(k)
        if v:
            lines.append(f"- {k}: {v}")
    if task.priority:
        lines.append(f"- priority: {task.priority}")
    lines.append(f"- source : {task.source}")
    return "\n".join(lines)


def ensure_plan_review_needs(cfg: "Config", tasks: "list[Task]") -> None:
    """proposed（実行前レビュー待ち）タスクに needs/<id>.md（レビュー票）を用意する。
    生成経路（plan/enqueue/inbox/followup/intake/cohort）に依らずここで一元的に整合させる
    （needs が既にあれば触らない＝人の記入を消さない）。"""
    for t in tasks:
        if t.norm_status() != "proposed":
            continue
        if needs_path(cfg, t.id).exists():
            continue
        write_needs_file(cfg, t, "新規タスクの実行前レビュー（承認されるまで実行しません）",
                         evidence=_task_definition_block(t), kind="plan-review")


def clear_needs_file(cfg: "Config", tid: str) -> None:
    p = needs_path(cfg, tid)
    if p.exists():
        p.unlink()


def read_feedback(path: Path) -> str:
    """決定記入欄（『## Decision Outcome』または旧『## フィードバック』）以降の人の記入
    （HTMLコメント・チェックボックス行は除く）を取り出す。"""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    hits = [(text.find(m), m) for m in FEEDBACK_MARKERS]
    hits = [(i, m) for i, m in hits if i >= 0]
    if not hits:
        return ""
    i, marker = min(hits)
    body = text[i + len(marker):]
    lines = [ln for ln in body.splitlines() if not CHECKBOX_RE.match(ln)]
    return "\n".join(lines).strip()


def feedback_submitted(path: Path) -> bool:
    """確定チェックボックスが [x] かどうか（= 人が編集を終えた明示シグナル）。"""
    return any(CHECKED_RE.match(ln) for ln in path.read_text(encoding="utf-8").splitlines())


def settled(cfg: "Config", f: Path) -> bool:
    """watch 中の静穏化ガード: 最終保存から debounce 秒経つまでは触らない（書きかけ保護）。

    人の入力（needs/ の記入・commands/ のドロップ）を「まだ触ってよいか」で判定する唯一の場所。
    has_work（起床するか）と各 ingest（処理するか）がこの述語を共有していないと、起床したのに
    何も処理しないパスが生まれ、そのパスが charter を再評価して承認済みマイルストーンを
    書き直してしまう（要対応が復活する原因）。"""
    if not (cfg.watch and cfg.debounce > 0):
        return True
    try:
        return (time.time() - f.stat().st_mtime) >= cfg.debounce
    except OSError:
        return False


def ingest_feedback(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """needs/<id>.md の確定（[x]）を検知したら、対象をブロック解除し内容を次の act に渡す。

    明示シグナル（チェックボックス [x]）必須。書きかけでの誤発火を防ぐため、watch 中は
    最終保存から cfg.debounce 秒が経過するまで待つ（静穏化）。"""
    ingested: list[str] = []
    if not cfg.needs.exists():
        return ingested
    by_id = {t.id: t for t in tasks}
    for nf in sorted(cfg.needs.glob("*.md")):
        if not feedback_submitted(nf):                 # [x] が無ければ確定していない
            continue
        if not settled(cfg, nf):                        # 直近に編集 → 静穏化を待つ
            continue
        t = by_id.get(nf.stem)
        if t is None:
            continue
        fb = read_feedback(nf)
        if t.norm_status() == "proposed":            # 実行前レビューの決着（承認 or 差し戻し）
            if fb:                                   # 差し戻し: kiro-project がタスクを修正して再提案
                plan_rework(cfg, t, fb)              # （新しいレビュー票を needs に書き直す）
            else:                                    # 空のまま [x] = 承認（実行を許可）
                _plan_approve(cfg, t, "チェックで承認")   # （needs は消える）
            ingested.append(t.id)
            continue
        was_review = t.norm_status() == "review"     # 検収待ちからの復帰か（自律度の clean/手戻り判定用）
        t.status = "ready"
        t.drop("feedback")
        if fb:
            t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
        if was_review:                               # review→ feedback あり=差し戻し(手戻り) / 無し=承認(clean)
            autonomy_record(cfg, t, clean=not bool(fb))
        persist_task(cfg, t)
        append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）に人のフィードバック",
                        action="feedback-resume", reason=fb[:200] if fb else "チェックで承認",
                        affects=f"{t.id} → ready", learn=(t.title, fb) if fb else None)
        nf.unlink()
        append_journal(cfg.journal, f"feedback 取り込み: {t.id} を再開")
        ingested.append(t.id)
    return ingested



def _extract_json_object_loose(text: str) -> "dict | None":
    """エージェント出力から最初の JSON オブジェクトを寛容に取り出す（_extract_json_array の単体版）。"""
    s = str(text or "")
    i = s.find("{")
    while i >= 0:
        depth = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[i:j + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
                    break
        i = s.find("{", i + 1)
    return None


def _plan_approve(cfg: "Config", t: Task, reason: str) -> None:
    """実行前レビューの承認: proposed → ready（verify を用意できなければ inbox＝triage 行き）。"""
    t.status = "ready" if has_verify_plan(t) else "inbox"
    persist_task(cfg, t)
    clear_needs_file(cfg, t.id)
    append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）の実行を承認",
                    action="plan-approve", reason=reason, affects=f"{t.id} → {t.status}")
    append_journal(cfg.journal, f"plan-review 承認: {t.id} → {t.status}")


_PLAN_REWORK_FIELDS = ("title", "verify", "accept", "after", "priority", "note")


def _plan_rework_prompt(t: Task, feedback: str) -> str:
    return (
        "あなたはバックログタスクの定義を人のレビュー指摘に沿って修正する編集者です。\n"
        "以下のタスク定義を、指摘を反映した形に修正してください。\n\n"
        f"## 現在のタスク定義\n{_task_definition_block(t)}\n\n"
        f"## 人のレビュー指摘（必ず反映する）\n{feedback}\n\n"
        "出力は JSON オブジェクトのみ: {\"title\": str, \"verify\": str（終了コード0=PASSのシェル。"
        "書けなければ空）, \"accept\": str（自然言語の完了条件・任意）, \"after\": str（依存タスクID・"
        "カンマ区切り・任意）, \"priority\": int（任意）, \"note\": str（任意）}。"
        "変更不要のフィールドは現在の値をそのまま返すこと。")


def plan_rework(cfg: "Config", t: Task, feedback: str) -> None:
    """実行前レビューの差し戻し: kiro-cli にタスク定義を修正させて**再び proposed** で提案し直す。
    kiro-cli 不在/失敗時は指摘を note に追記してそのまま再提案（人が approve/revise で確定できる）。"""
    reworked = False
    try:
        out = _run_kiro_cli(_plan_rework_prompt(t, feedback), cfg.model, purpose="plan")
        obj = _extract_json_object_loose(out)
        if isinstance(obj, dict) and str(obj.get("title", "")).strip():
            t.title = str(obj["title"]).strip()
            t.verify = _strip_code(str(obj.get("verify", "") or "").strip())
            for k in ("accept", "after", "note"):
                v = str(obj.get(k, "") or "").strip()
                t.drop(k)
                if v:
                    t.extra.append((k, v))
            try:
                t.priority = int(obj.get("priority", t.priority) or 0)
            except (TypeError, ValueError):
                pass
            reworked = True
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"plan-review 差し戻しの修正に失敗（生のまま再提案）: {t.id}: {e}")
    if not reworked:                       # 修正できなくても指摘は失わない（note に残して人が確定）
        note = (t.get("note") or "").strip()
        t.drop("note")
        t.extra.append(("note", (note + " ⏎ " if note else "") + f"[差し戻し] {feedback}"))
    t.status = "proposed"
    persist_task(cfg, t)
    write_needs_file(cfg, t, f"差し戻しを反映して再提案（指摘: {feedback[:200]}）",
                     evidence=_task_definition_block(t), kind="plan-review")
    append_decision(cfg, t.id, cfg.actor, context=f"{t.id}（{t.title}）を差し戻しで修正",
                    action="plan-rework", reason=feedback[:200],
                    affects=f"{t.id} → proposed（再提案）", learn=(t.title, feedback))
    append_journal(cfg.journal, f"plan-review 差し戻し: {t.id} を修正して再提案")


# ---------------------------------------------------------------------------
# 依存の影響範囲（after 逆辺）と却下（reject）
# ---------------------------------------------------------------------------
def dependents_of(tasks: "list[Task]", tid: str, transitive: bool = True) -> "list[Task]":
    """tid に依存する（after に tid を含む）タスク。transitive で推移閉包（影響範囲の一覧提示用）。"""
    out: "list[Task]" = []
    seen = {tid}
    frontier = {tid}
    while frontier:
        nxt: set = set()
        for t in tasks:
            if t.id in seen:
                continue
            if any(d in frontier for d in task_deps(t)):
                out.append(t)
                seen.add(t.id)
                nxt.add(t.id)
        if not transitive:
            break
        frontier = nxt
    return out


def prerequisites_of(tasks: "list[Task]", tid: str, transitive: bool = True) -> "list[str]":
    """tid の前提（after 上流）の ID 一覧。backlog に無い ID（done/外部）も含めて返す。"""
    by_id = {t.id: t for t in tasks}
    out: "list[str]" = []
    seen = {tid}
    frontier = [tid]
    while frontier:
        cur = frontier.pop(0)
        t = by_id.get(cur)
        if t is None:
            continue
        for d in task_deps(t):
            if d not in seen:
                seen.add(d)
                out.append(d)
                if transitive:
                    frontier.append(d)
    return out


def cmd_impact(cfg: Config, tid: str, as_json: bool = False) -> int:
    """タスクの依存関係（前提／依存先・推移）を一覧表示する。変更・却下の影響範囲を人が辿る用。"""
    tasks = load_tasks(cfg.backlog)
    if not any(t.id == tid for t in tasks):
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    ups = prerequisites_of(tasks, tid)
    downs = dependents_of(tasks, tid)
    if as_json:
        print(json.dumps({"id": tid, "prerequisites": ups,
                          "dependents": [{"id": t.id, "title": t.title,
                                          "status": t.norm_status()} for t in downs]},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"=== impact: {tid} ===")
    print(f"前提（after 上流・推移）: {', '.join(ups) or '（なし）'}")
    if downs:
        print("依存先（このタスクの変更が影響する・推移）:")
        for t in downs:
            print(f"  - {t.id} [{t.norm_status()}]: {t.title}")
    else:
        print("依存先: （なし）")
    return 0


def _rejected_record(t: Task, reason: str) -> str:
    return (f"\n## 却下記録\n- 却下: {reason}\n- 却下時の状態: {t.norm_status()}\n"
            f"- 却下時刻: {_now_ts()}\n")


def cmd_reject(cfg: Config, tid: str, reason: str) -> int:
    """タスクの却下: 廃止（rejected として archive へ退避）し、依存先を proposed に戻して再審査に
    かけ、charter があればバックログの再計画（replan）を要求する。実行前（proposed）にも
    成果物レビュー段（review）にも使える。理由は avoid（回避知識）として蓄積し、同種の再提案を
    予防リコールが弾く。"""
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    if t.norm_status() == "doing":
        print(f"エラー: {tid} は実行中（doing）です。先に revise で止めるか完了を待ってください。",
              file=sys.stderr)
        return 2
    release_claim(cfg, t)
    # 影響範囲（after 逆辺・推移）: 依存先は前提を失うため proposed に戻して人の再審査へ
    downs = dependents_of(tasks, tid)
    for d in downs:
        deps = [x for x in task_deps(d) if x != tid]
        d.drop("after")
        if deps:
            d.extra.append(("after", ", ".join(deps)))
        if d.norm_status() not in ("done", "doing"):
            d.status = "proposed"
            clear_needs_file(cfg, d.id)
            persist_task(cfg, d)
            write_needs_file(cfg, d, f"前提タスク {tid} が却下されたため再審査",
                             evidence=_task_definition_block(d), kind="plan-review")
        else:
            persist_task(cfg, d)
    close_task_mr(cfg, t, reason)   # タスク MR があればクローズ＋ブランチ削除（best-effort）
    # 本体を rejected として archive へ退避（納品ではないので DELIVERY には載せない）
    t.status = "rejected"
    _archive_write(cfg, t.id, serialize_task(t) + _rejected_record(t, reason))
    delete_task_file(cfg, t)
    clear_needs_file(cfg, tid)
    affected = ", ".join(d.id for d in downs) or "（なし）"
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を却下（廃止）",
                         action="reject", reason=reason,
                         affects=f"{tid} → rejected ／ 依存先を再審査へ: {affected}",
                         avoid=(t.title, reason) if cfg.learn_capture and reason else None)
    # charter があれば再計画を要求（却下で空いた穴を plan が埋め直す。rejected タイトルは
    # archive 経由で _existing_titles に含まれるため同一タスクは再提案されない）
    replanned = ""
    if charter_names(cfg):
        write_replan_request(cfg, f"タスク {tid} の却下に伴う再計画",
                             charter=(t.get("charter") or "").strip())
        replanned = "／charter からの再計画を要求しました"
    append_journal(cfg.journal, f"reject: {tid} を却下（依存先 {len(downs)} 件を再審査へ）")
    print(f"{dr}: {tid} を却下しました。影響（依存先→再審査）: {affected}{replanned}")
    return 0


def human_worklist(tasks: "list[Task]") -> "tuple[list[Task], list[Task], list[Task], list[Task]]":
    blocked = [t for t in tasks if t.norm_status() == "blocked"]
    intake = [t for t in tasks if t.norm_status() == "inbox" and not t.verify.strip()]
    review = [t for t in tasks if t.norm_status() == "review"]   # verify=PASS の承認待ち
    proposed = [t for t in tasks if t.norm_status() == "proposed"]   # 実行前レビュー待ち
    return blocked, intake, review, proposed


def render_digest(blocked, intake, reasons: dict, budget_stop: bool, review=None,
                  proposed=None) -> str:
    review = review or []
    proposed = proposed or []
    lines = ["# 要対応（kiro-project）", ""]
    if budget_stop:
        lines += ["⚠ 予算切れで未消化のまま停止しました。", ""]
    if proposed:
        lines.append("## 実行前レビュー待ち（proposed・承認されるまで実行しません）")
        for t in proposed:
            lines.append(f"- {t.id}: {t.title}")
            lines.append(f"    対応: `kiro-project approve {t.id}`（承認）／needs に修正指示を書いて差し戻し"
                         f"／`kiro-project reject {t.id} --reason ...`（却下）")
        lines.append("")
    if review:
        lines.append("## 検収待ち（verify=PASS・承認で done 確定）")
        for t in review:
            lines.append(f"- {t.id}: {t.title}")
            lines.append(f"    成果: {t.get('gate_ref', '')}")
            lines.append(f"    対応: `kiro-project approve {t.id}`（承認）／needs に方針を書いて差し戻し")
        lines.append("")
    if blocked:
        lines.append("## 判断待ち（blocked）")
        for t in blocked:
            why = reasons.get(t.id, "検証 NG / 判断不能")
            lines.append(f"- {t.id}: {t.title}\n    なぜ: {why}\n"
                         f"    対応: needs/{t.id}.md に方針を書く、または `approve {t.id}` / `hold {t.id}`")
    if intake:
        lines += ["", "## acceptance 未定義（need_intake）"]
        for t in intake:
            lines.append(f"- {t.id}: {t.title}\n    なぜ: verify 未定義 → verify を定義して ready 化")
    if not blocked and not intake and not review and not proposed:
        lines.append("（対応待ちなし）")
    return "\n".join(lines) + "\n"


def notify(cfg: "Config", tasks, reasons: dict, newly_blocked: set, budget_stop: bool) -> bool:
    """状態遷移時だけ stdout / notify-cmd へ要約を出す（案件毎の needs/<id>.md は別途書込済）。"""
    if not newly_blocked and not budget_stop:
        return False
    blocked, intake, review, proposed = human_worklist(tasks)
    digest = render_digest(blocked, intake, reasons, budget_stop, review, proposed)
    print("\n--- 通知（要対応）---\n" + digest, flush=True)
    if cfg.notify_cmd:
        try:
            subprocess.run(cfg.notify_cmd, shell=True, input=digest, text=True,
                           cwd=str(cfg.workdir), timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] notify-cmd 失敗: {e}", file=sys.stderr)
    return True


# ---------------------------------------------------------------------------
# 優先順位付け（正準ループ ①②）
# ---------------------------------------------------------------------------
def consumable_tasks(tasks: "list[Task]") -> "list[Task]":
    return [t for t in tasks if t.consumable()]


def task_deps(task: "Task") -> "list[str]":
    """`- after: T1, T2` の依存 ID 群（カンマ/空白区切り）。無ければ空。"""
    raw = task.get("after", "")
    return [d for d in re.split(r"[,\s]+", raw.strip()) if d]


def unmet_deps(task: "Task", tasks: "list[Task]") -> "list[str]":
    """`after` の依存のうち、まだ未完（backlog に done 以外で残っている）ID。done は退避済みなので満たし。"""
    pending = {t.id for t in tasks if t.norm_status() != "done"}
    return [d for d in task_deps(task) if d in pending]


def ready_after_deps(tasks: "list[Task]") -> "list[Task]":
    """消化対象（ready）のうち、依存が満たされたものだけ（DAG 順序）。"""
    return [t for t in consumable_tasks(tasks) if not unmet_deps(t, tasks)]


def _extract_id_array(text: str) -> "list[str] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return [str(x) for x in arr] if isinstance(arr, list) else None


def _extract_json_obj(text: str) -> "dict | None":
    """応答から最初の JSON オブジェクト {...} を取り出す（説明文が混じっても拾う）。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


# LLM 実行に使うエージェント CLI とタイムアウト（設定 agent_cli / agent_timeout）。
# rank_agent 等の free 関数は args を持たないため、build_config が設定値をここへ確定する
# （kiro-flow の _configure_thresholds と同じ流儀）。
_AGENT_CLI: str = "kiro"
_AGENT_TIMEOUT: float = 300.0
# 処理（purpose）毎の上書き（設定 agents: の正規化済みマップ。build_config が確定する）。
# 例: {"plan": {"agent_cli": "claude", "model": "opus"}, "assess": {"model": "haiku"}}
_AGENT_OVERRIDES: "dict[str, dict]" = {}
# エージェントを使用する処理の一覧（設定 agents: のキー）。ここに無いキーは無視される。
AGENT_PURPOSES = ("plan", "review", "prioritize", "route", "adjudicate", "verify",
                  "distill", "assess", "repo_map", "doctor")
# agent_cli の設定値 → doctor が PATH 確認すべき実行ファイル名（未知の agent_cli はそのまま使う）。
_AGENT_CLI_BINARIES = {"kiro": "kiro-cli", "claude": "claude", "copilot": "copilot",
                       "codex": "codex"}


def _normalize_agent_overrides(raw) -> "dict[str, dict]":
    """設定 agents:（処理毎の agent_cli/model 上書き）を正規化する。未知の処理キー・
    不正な値は黙って落とす（設定ミスでループを殺さない。有効キーは AGENT_PURPOSES）。"""
    out: "dict[str, dict]" = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in AGENT_PURPOSES or not isinstance(v, dict):
            continue
        ov: dict = {}
        if v.get("agent_cli"):
            ov["agent_cli"] = str(v["agent_cli"]).strip().lower()
        if v.get("model"):
            ov["model"] = str(v["model"]).strip()
        if ov:
            out[key] = ov
    return out


def _agent_for(purpose: str) -> "tuple[str, str | None]":
    """処理（purpose）の実効エージェント。(agent_cli, model 上書き) を返す。
    設定 agents: の該当キー ＞ グローバル agent_cli（model 上書きは無ければ None＝呼び出し値）。"""
    ov = _AGENT_OVERRIDES.get(purpose) or {}
    return (str(ov.get("agent_cli") or _AGENT_CLI).lower(), ov.get("model") or None)


def _agent_cmd(cli: str, model: "str | None",
               prompt: str) -> "tuple[list[str], str | None, str | None]":
    """エージェント CLI 1 回分の (argv, stdin テキスト, 最終応答ファイル) を組み立てる
    （実行はしない・決定的）。最終応答ファイルは codex のみ使う（stdout がイベントログのため）。"""
    if cli == "claude":
        # Claude Code ヘッドレス。プロンプトは stdin 渡し（ARG_MAX に当たらない）。
        cmd = ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
        return cmd, prompt, None
    if cli == "copilot":
        # GitHub Copilot CLI ヘッドレス。-s で応答本文のみ、--allow-all-tools は
        # 非対話モードの必須フラグ（--allow-all-paths はファイル読み書きの許可）。
        cmd = ["copilot", "-s", "--allow-all-tools", "--allow-all-paths", "--no-color"]
        if model:
            cmd += ["--model", model]
        return cmd + ["-p", prompt], None, None
    if cli == "codex":
        # OpenAI Codex CLI ヘッドレス（codex exec）。プロンプトは stdin 渡し（"-"）。
        # stdout には実行イベントログが混ざるため、最終応答は --output-last-message の
        # ファイルから読む。--skip-git-repo-check は git リポジトリ外でも動かすため。
        fd, out_file = tempfile.mkstemp(prefix="kiro-project-codex-", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", "--skip-git-repo-check",
               "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
               "--output-last-message", out_file]
        if model:
            cmd += ["--model", model]
        return cmd + ["-"], prompt, out_file
    cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"]
    if model:
        cmd += ["--model", model]
    return cmd + [prompt], None, None


def _run_kiro_cli(prompt: str, model: "str | None", purpose: str = "") -> str:
    """エージェント CLI（設定 agent_cli: kiro/claude/copilot/codex）を 1 回呼び出してテキスト応答を返す。
    このツールの LLM 呼び出し（分解・優先順位・裁定・ルーティング等）はすべてここを通る。
    purpose（AGENT_PURPOSES のいずれか）を渡すと、設定 agents: の処理毎上書き
    （agent_cli / model）が効く。model は 上書き ＞ 呼び出し値（通常グローバル model）。"""
    cli, model_ov = _agent_for(purpose)
    cmd, stdin_text, out_file = _agent_cmd(cli, model_ov or model, prompt)
    # 発生源で色を抑止（NO_COLOR/TERM=dumb）。残った ANSI は strip_ansi で除去する二段構え。
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text,
                              timeout=(_AGENT_TIMEOUT if _AGENT_TIMEOUT > 0 else None), env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"{cmd[0]} rc={proc.returncode}: {proc.stderr.strip()[:300]}")
        text = strip_ansi(proc.stdout).strip()
        if out_file:   # codex: 最終応答ファイルが取れればそれを正とする（stdout はイベントログ）
            with contextlib.suppress(OSError):
                with open(out_file, encoding="utf-8") as f:
                    text = f.read().strip() or text
        return text
    finally:
        if out_file:
            with contextlib.suppress(OSError):
                os.remove(out_file)


def rank_agent(ready: "list[Task]", model: "str | None", kiro_run=None) -> "list[Task] | None":
    kiro_run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="prioritize"))
    if len(ready) <= 1:
        return list(ready)     # 0/1 件は並べ替えの余地が無い＝LLM を呼ばない（コスト・レイテンシ削減）
    listing = "\n".join(
        f"- {t.id}: {t.title}（priority={t.priority}, source={t.source}）" for t in ready)
    prompt = ("あなたはバックログの優先順位付け役。次のタスク群を、重要度・緊急度・依存関係に加え、"
              "**外部で付与された priority（大きいほど高優先）も加味**して優先順位の高い順に並べ替え、"
              "**タスクID の JSON 配列だけ**を出力してください（説明文なし）。\n\nタスク:\n" + listing)
    try:
        order_ids = _extract_id_array(kiro_run(prompt, model))
    except Exception:  # noqa: BLE001
        return None
    if not order_ids:
        return None
    by_id = {t.id: t for t in ready}
    ordered = [by_id[i] for i in order_ids if i in by_id]
    seen = {t.id for t in ordered}
    ordered += [t for t in ready if t.id not in seen]
    return ordered


def _tail_matching(path: "Path | None", needle: str, limit: int) -> "list[str]":
    """ファイルから needle を含む行を末尾 limit 件返す（best-effort・無ければ空）。"""
    if not path or not path.exists():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if needle in ln]
    except OSError:
        return []
    return lines[-limit:]


def adjudication_context(cfg: "Config", task: Task,
                         journal_lines: int = 8, decision_chars: int = 1200) -> str:
    """裁定の判断材料を decisions/journal/task から決定的に集める（LLM 不要・有界）。
    『過去にどう試して何を人が判断したか』を門番へ渡し、的外れな requeue や再エスカレを減らす。"""
    parts: list[str] = []
    jl = _tail_matching(cfg.journal, task.id, journal_lines)
    if jl:
        parts.append("これまでのサイクル履歴(journal):\n" + "\n".join(jl))
    dp = decision_path(cfg, task.id)
    if dp.exists():
        try:
            txt = dp.read_text(encoding="utf-8").strip()
        except OSError:
            txt = ""
        if txt:
            if len(txt) > decision_chars:        # 直近の判断が重要なので末尾を残す
                txt = "…\n" + txt[-decision_chars:]
            parts.append("過去の決定記録(decisions):\n" + txt)
    fb = task.feedback()
    if fb:
        parts.append("適用済みの直近フィードバック: " + fb)
    note = next((v for k, v in task.extra if k == "note"), None)
    if note:
        parts.append("タスクのメモ(note): " + note)
    return "\n\n".join(parts)


def adjudicate_escalation(cfg: "Config", task: Task, reason: str,
                          kiro_run=None) -> "tuple[str, str]":
    """needs（人の判断）に落とす直前の kiro-cli 裁定ゲート。
    『ループ内で自律的に積み直して解けるか／人の判断が要るか』を判断させる。
    返り値: ("requeue", guidance) なら自律的に積み直す、("escalate", "") なら従来どおり人へ。
    判断不能・エラー・曖昧は **必ず escalate にフォールバック**（安全側＝人を飛ばさない）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="adjudicate"))
    ctx = adjudication_context(cfg, task)        # journal/decisions/feedback の文脈を渡す
    prompt = (
        "あなたは自律バックログ・ループの『人の判断を呼ぶ前の門番』です。次のタスクが検証(verify)に"
        "失敗し、通常なら人の判断待ち(needs)へ送られます。これを **ループ内で自律的に積み直して解決を試みる"
        "価値があるか** を判断してください。\n"
        "- requeue（積み直す）: 失敗が実装の不足・取り違え等で、明確な追加指示があれば次の試行で解けそうな場合。\n"
        "- escalate（人へ）: 要件が曖昧／意思決定や承認が要る／リスクが高い／同じ失敗の繰り返しで打開策が無い場合。\n"
        "**判断は厳しめに。少しでも人の意思決定が要るなら escalate。過去に同じ案件を積み直して解けていない"
        "なら escalate。**\n\n"
        f"タスクID: {task.id}\nタイトル: {task.title}\nverify: {task.verify}\n"
        f"これまでの試行回数(retries): {task.retries}\n失敗理由: {reason}\n\n"
        + (f"--- 参考文脈（既存の試行・判断の履歴）---\n{ctx}\n\n" if ctx else "")
        + '出力は次の JSON オブジェクトだけ（説明文なし）:\n'
        '{"decision": "requeue" | "escalate", "guidance": "requeue の場合のみ、次の試行への具体的な指示"}')
    try:
        obj = _extract_json_obj(run(prompt, cfg.model))
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は人へ
        return ("escalate", "")
    if not obj or obj.get("decision") != "requeue":
        return ("escalate", "")
    return ("requeue", str(obj.get("guidance", "")).strip())


# ---------------------------------------------------------------------------
# 投入時アセスメント（Spec Orchestrator の採点段）— c=複雑さ / r=リスク / a=曖昧さ（各1-3）
# ---------------------------------------------------------------------------
def _assess_heuristic(cfg: "Config", task: Task) -> dict:
    """エージェント不在・失敗・stub 時の決定的採点。材料はタスク定義と decisions/ の走査のみ。
    c: cohort（同種の繰り返し）は多対象＝3。r: 過去の回避判断（avoid）に類似＝3。
    a: 決定的 verify あり=1 / accept（自然言語）のみ=2 / どちらも無し=3。"""
    c = 3 if (task.get("cohort_items") or task.get("cohort")) else 1
    r = 3 if find_avoidance(cfg, task) else 1
    a = 1 if (task.verify or task.get("verify_template")) else (2 if task.get("accept") else 3)
    return {"c": c, "r": r, "a": a}


def _assess_prompt(task: Task) -> str:
    return (
        "あなたはタスクの事前アセスメント役です。以下のタスクを 3 軸で採点してください（各 1〜3 の整数）。\n"
        "- c=複雑さ: 関与するファイル・コンポーネント・手順の多さ（3=多岐にわたる）\n"
        "- r=リスク: 壊したときの影響の大きさ（認証・決済・データ移行・本番設定などは 3）\n"
        "- a=曖昧さ: 完了条件・やり方の不確かさ（verify が具体的なら 1）\n\n"
        f"タイトル: {task.title}\n"
        f"verify: {task.verify or '（未定義）'}\n"
        f"accept: {task.get('accept') or '（なし）'}\n"
        f"note: {task.get('note') or '（なし）'}\n\n"
        '出力は JSON オブジェクトのみ（説明文なし）: {"c": 1, "r": 1, "a": 1}')


def assess_task(cfg: "Config", task: Task, kiro_run=None) -> "str | None":
    """投入時アセスメント。採点は情報であり、それ自体は実行可否・done 条件を変えない
    （読むのは plan-review 票・リスクダイジェスト・spec ルーティング）。知能は委譲し、
    失敗・stub は決定的ヒューリスティックへフォールバック。1 タスク 1 回（既存はスキップ）。"""
    if task.get("assess"):
        return task.get("assess")
    scores = None
    if cfg.executor != "stub":
        run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="assess"))
        try:
            obj = _extract_json_obj(run(_assess_prompt(task), cfg.model)) or {}
            got = {k: int(obj[k]) for k in ("c", "r", "a") if k in obj}
            if len(got) == 3:
                scores = {k: min(3, max(1, v)) for k, v in got.items()}
        except Exception:  # noqa: BLE001  エージェント不在・タイムアウト・非 JSON はヒューリスティックへ
            scores = None
    if scores is None:
        scores = _assess_heuristic(cfg, task)
    val = f"c={scores['c']} r={scores['r']} a={scores['a']}"
    task.extra.append(("assess", val))
    return val


# ---------------------------------------------------------------------------
# spec ルーティング（Spec Driven の前段・opt-in `spec_track`）
#   採点が spec_threshold に達した（または policy `spec:` に一致する）タスク T に、spec 作成
#   タスク（specs/<T>/ の spec.md/design.md/tasks.md・review: human）を前置する。人が spec を
#   承認して done になったら、tasks.md（enqueue --json 互換）を実装タスク群へ展開し、T は
#   after: 実装タスク群 の総合検証として最後に走る。すべて既存プリミティブ（after DAG・
#   plan_review/delivery_review・enqueue）の組み合わせで、S0–S7 のゲートは無改造。
# ---------------------------------------------------------------------------
def specs_root(cfg: "Config") -> Path:
    """spec 成果物の置き場（<workdir>/specs）。act の指示（相対パス）と verify（workdir 実行）が
    同じ場所を指すよう workdir 基準にする。既定 workdir=root なので状態リポジトリに載り、
    git 同期で viewer からも読める。"""
    return cfg.workdir / "specs"


def _assess_max(task: Task) -> int:
    """`- assess: c=N r=N a=N` の最大値（未採点は 0）。spec ルーティングのしきい値判定に使う。"""
    vals = re.findall(r"[cra]=(\d)", task.get("assess", "") or "")
    return max((int(v) for v in vals), default=0)


def _spec_verify(cfg: "Config", tid: str) -> str:
    """spec 作成タスクの決定的 verify: 3 ファイルが非空で、tasks.md が JSON タスク分解を含む。
    workdir 相対（specs_root と同じ基準）。"""
    rel = f"specs/{tid}"
    return (f"test -s {rel}/spec.md -a -s {rel}/design.md -a -s {rel}/tasks.md"
            f" && grep -q '\"title\"' {rel}/tasks.md")


def _spec_instructions(cfg: "Config", task: Task) -> str:
    """spec 作成タスク（`- spec_for:` 持ち）の act 要求文に足す作成指示。実装はさせない。"""
    tid = task.get("spec_for", "")
    rel = f"specs/{tid}"
    return (
        f"これは実装前の Spec 作成タスクです。{task.get('note') or ''}\n"
        f"作業ディレクトリ直下の {rel}/ に次の 3 ファイルを作成すること（コードの実装はしない）:\n"
        f"- {rel}/spec.md   … 要求仕様（背景・要求・受け入れ観点）\n"
        f"- {rel}/design.md … 設計（方針・影響範囲・代替案と選定理由）\n"
        f"- {rel}/tasks.md  … 実装タスク分解。次の形式の JSON 配列を含む Markdown:\n"
        f'  [{{"title": "…", "verify": "終了コード0で合否が決まるシェルコマンド",'
        f' "after": ["先行タスクの title"]}}]\n'
        f"  verify は『履歴』でなく『望む最終状態/差分』を見ること。after は任意（配列内の先行タスク）。")


def route_spec_tasks(cfg: "Config", tasks: "list[Task]", policy: "Policy") -> "list[Task]":
    """spec ルーティング（S0）。決定はタスクの `- route:` に記録して再ルーティングしない
    （人の `- route: direct` が採点に常に勝つ＝policy/明示 > エージェント）。ルーティングは
    「タスクを足す」方向のみで、done 条件・予算には触れない。作成した spec タスクを返す。"""
    if not cfg.spec_track:
        return []
    created: "list[Task]" = []
    for t in list(tasks):
        if t.norm_status() not in ("proposed", "ready", "inbox"):
            continue
        if t.get("route") or t.get("spec_for") or t.get("spec"):   # 決定済み・spec 系タスクは対象外
            continue
        forced = any(t.matches(p) for p in policy.spec)
        if not forced and _assess_max(t) < cfg.spec_threshold:
            continue
        spec_dict = {"id": f"{t.id}-spec", "title": f"Spec 作成: {t.title}",
                     "verify": _spec_verify(cfg, t.id), "review": "human",
                     "spec_for": t.id, "route": "direct", "source": "spec",
                     "priority": t.priority + 1,
                     "note": f"対象タスク {t.id}: {t.title}"
                             + (f"（最終 verify: {t.verify}）" if t.verify else "")}
        if t.get("charter"):
            spec_dict["charter"] = t.get("charter")
        s = enqueue_task(cfg, spec_dict)
        deps = task_deps(t) + [s.id]
        t.set("route", "spec")
        t.set("spec_task", s.id)
        t.set("after", ", ".join(deps))
        persist_task(cfg, t)
        created.append(s)
        why = "policy spec 一致" if forced else \
            f"採点 {t.get('assess')} が spec_threshold({cfg.spec_threshold}) 以上"
        append_decision(cfg, t.id, "auto",
                        context=f"{t.id}（{t.title}）を spec ルートへ",
                        action="spec-route", reason=why,
                        affects=f"{s.id} を前置（承認後 tasks.md を実装タスクへ展開）")
        append_journal(cfg.journal, f"spec ルート: {t.id} に {s.id} を前置（{why}）")
    return created


def expand_spec_tasks(cfg: "Config", tasks: "list[Task]") -> "list[Task]":
    """spec 前段が done（archive へ・承認済み）になったタスクの tasks.md を実装タスク群へ展開する。
    tasks.md は enqueue --json 互換の JSON 配列。展開後、元タスクの after を実装タスク群へ
    付け替え＝元タスクは自らの verify を持つ総合検証として最後に走る。JSON が無ければ展開なし
    （元タスクが spec を文脈注入されて自力実装する・安全側）。展開数は max_spawn の傘の下。"""
    if not cfg.spec_track:
        return []
    by_id = {t.id: t for t in tasks}
    created_all: "list[Task]" = []
    for t in list(tasks):
        if t.get("route") != "spec" or t.get("spec_expanded"):
            continue
        sid = t.get("spec_task", "")
        if not sid or sid in by_id:                    # spec 前段が未決着（backlog に現存）
            continue
        arch = cfg.archive_dir() / f"{sid}.md"
        try:
            adone = (arch.exists() and
                     parse_task(arch.read_text(encoding="utf-8"), sid).norm_status() == "done")
        except OSError:
            adone = False
        if not adone:
            continue                                   # 却下等は展開しない（再審査は既存機構が担う）
        tmd = specs_root(cfg) / t.id / "tasks.md"
        try:
            items = _extract_json_array(tmd.read_text(encoding="utf-8")) if tmd.exists() else None
        except OSError:
            items = None
        specs = [it for it in (items or []) if isinstance(it, dict)
                 and str(it.get("title", "")).strip()][: max(0, cfg.max_spawn)]
        if not specs:
            t.set("spec_expanded", "none")             # 展開なし＝元タスクが spec 文脈で自力実装
            persist_task(cfg, t)
            append_journal(cfg.journal, f"spec 展開なし（tasks.md に有効な JSON 無し）: {t.id}")
            continue
        pairs: "list[tuple[dict, Task]]" = []
        title_to_id: "dict[str, str]" = {}
        for it in specs:
            sp = {"title": str(it["title"]).strip(),
                  "verify": _strip_code(str(it.get("verify", "") or "").strip()),
                  "source": "spec", "spec": t.id, "route": "direct"}
            for k in ("accept", "verify_template", "note", "priority"):
                if it.get(k) not in (None, "", []):
                    sp[k] = it[k]
            for k in ("charter", "workspace"):         # 成果の行き先・スコープは元タスクを引き継ぐ
                if t.get(k):
                    sp[k] = t.get(k)
            try:
                nt = enqueue_task(cfg, sp)
            except ValueError:
                continue
            pairs.append((it, nt))
            title_to_id[sp["title"]] = nt.id
        # 2 パス目: 配列内 after（先行タスクの title）を id へ決定的に解決（未知 title は落とす・循環は拒否）
        new_tasks = [nt for _, nt in pairs]
        for it, nt in pairs:
            deps = [title_to_id[w] for w in _coerce_titles(it.get("after"))
                    if w in title_to_id and title_to_id[w] != nt.id]
            if deps:
                nt.set("after", ", ".join(deps))
                if _after_introduces_cycle(new_tasks, nt):
                    nt.drop("after")
                persist_task(cfg, nt)
        impl_ids = [nt.id for nt in new_tasks]
        t.set("after", ", ".join(impl_ids))            # 元タスク＝総合検証として最後に走る
        t.set("spec_expanded", str(len(impl_ids)))
        persist_task(cfg, t)
        append_decision(cfg, t.id, "auto",
                        context=f"{t.id}（{t.title}）の spec 承認に伴う実装タスク展開",
                        action="spec-expand",
                        reason=f"specs/{t.id}/tasks.md から {len(impl_ids)} 件",
                        affects=f"{t.id} は総合検証（after: {', '.join(impl_ids)}）")
        append_journal(cfg.journal, f"spec 展開: {t.id} ← {len(impl_ids)} 件（{sid} 承認）")
        created_all.extend(new_tasks)
    return created_all


def spec_context(cfg: "Config", task: Task, limit: int = 1200) -> str:
    """act 要求文へ注入する spec 文脈（spec.md/design.md・有界）。対象は spec 展開で生まれた
    実装タスク（`- spec:`）と、展開後の総合検証タスク（route: spec）。spec 作成タスク自身は対象外。"""
    tid = task.get("spec") or ("" if task.get("spec_for")
                               else (task.id if task.get("route") == "spec" else ""))
    if not tid:
        return ""
    parts: "list[str]" = []
    for name in ("spec.md", "design.md"):
        p = specs_root(cfg) / tid / name
        try:
            txt = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            txt = ""
        if txt:
            parts.append(f"--- specs/{tid}/{name} ---\n{txt[:limit]}")
    return "\n".join(parts)


def apply_policy_order(ordered: "list[Task]", policy: Policy) -> "list[Task]":
    def hit(t, pats):
        return any(t.matches(p) for p in pats)
    pinned = [t for t in ordered if hit(t, policy.pin)]
    deferred = [t for t in ordered if not hit(t, policy.pin) and hit(t, policy.defer)]
    middle = [t for t in ordered if t not in pinned and t not in deferred]
    return pinned + middle + deferred


def by_priority_then_age(ready: "list[Task]") -> "list[Task]":
    """優先度降順、同値は最古優先（ready は mtime 昇順で渡される＝安定ソートで age が効く）。"""
    return sorted(ready, key=lambda t: -t.priority)


def prioritize(tasks, policy, planner, model=None, ranker=None) -> "list[Task]":
    """planner=none: priority＋古さ。planner=agent: エージェント委譲（priority も加味）。policy が最終上書き。"""
    ready = ready_after_deps(tasks)  # mtime 昇順（最古優先）。依存(after)未達は除外
    # 0/1 件は並べ替えの余地が無く順序が自明＝planner を問わず LLM 優先順位付けを呼ばない
    # （エージェント CLI 起動のコスト・レイテンシを丸ごと省く）。policy（pin/defer）は後段で必ず効く。
    if planner == "none" or len(ready) <= 1:
        base = by_priority_then_age(ready)
    else:  # agent（エージェント委譲の順位付け。失敗時は priority＋古さにフォールバック）
        rank = (ranker or rank_agent)(ready, model)
        base = rank if rank is not None else by_priority_then_age(ready)
    return apply_policy_order(base, policy)


# ---------------------------------------------------------------------------
# triage（inbox→ready 昇格・policy deny の適用）
# ---------------------------------------------------------------------------
def triage(tasks, policy, plan_review: bool = False) -> "list[tuple[Task, str]]":
    transitions = []
    for t in tasks:
        st = t.norm_status()
        if st == "inbox" and has_verify_plan(t):   # verify か、用意できる材料(accept/verify_template)があれば昇格
            # 実行前レビュー時は ready でなく proposed へ（人の承認で初めて実行可能になる）
            t.status = "proposed" if plan_review else "ready"
            st = t.status
        if st in CONSUMABLE and any(t.matches(p) for p in policy.deny):
            t.status = "blocked"
            transitions.append((t, "policy:deny（人の判断待ち）"))
    return transitions


# ---------------------------------------------------------------------------
# verify ゲート / act（kiro-flow 委譲）
# ---------------------------------------------------------------------------
def run_verify(cmd: str, workdir: Path, timeout: float, env: "dict | None" = None) -> "tuple[bool, str]":
    if not cmd.strip():
        return (False, "verify 未定義（自己申告では done にできない → 人の判断へ）")
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(workdir), timeout=timeout,
                              capture_output=True, text=True,
                              env={**os.environ, **env} if env else None)
    except subprocess.TimeoutExpired:
        return (False, f"verify タイムアウト（{timeout}s）")
    tail = (proc.stdout or "")[-400:] + (proc.stderr or "")[-400:]
    return (proc.returncode == 0, f"exit={proc.returncode} {tail.strip()}"[:500])


def run_verify_stable(cmd: str, workdir: Path, timeout: float,
                      confirm: int = 1, env: "dict | None" = None) -> "tuple[bool, bool, str]":
    """verify を最大 confirm 回まで実行し (ok, flaky, msg) を返す。confirm>1 で結果が PASS/FAIL を
    跨いだら flaky=True（不安定）。揺れる verify を NG 誤読して retry churn したり、flaky PASS を
    そのまま done にするのを防ぐ（一致したら確定、跨いだら人へ隔離）。"""
    ok, msg = run_verify(cmd, workdir, timeout, env)
    if confirm <= 1 or not cmd.strip():        # 既定(1)や verify 未定義は従来どおり1回
        return (ok, False, msg)
    for _ in range(confirm - 1):
        ok2, msg2 = run_verify(cmd, workdir, timeout, env)
        if ok2 != ok:                          # PASS/FAIL を跨いだ＝不安定（flake）
            return (ok, True, f"flaky: verify が不安定（{confirm} 回中で PASS/FAIL 混在）"
                              f" — 1回目:[{msg}] 別回:[{msg2}]"[:500])
    return (ok, False, msg)                    # 全回一致＝安定した結果


def run_verify_at_rev(cmd: str, workdir: Path, rev: str, timeout: float,
                      env: "dict | None" = None) -> "bool | None":
    """verify を workdir の rev（act 前 HEAD）のツリーで実行し PASS したか（True/False）を返す。
    detached worktree を temp に生やして実行し後始末する。git でない/worktree 作成失敗＝判定不能で None。
    red-green の『red（変更前は fail のはず）』を取るのに使う——base で PASS するなら変更を弁別していない。
    KIRO_BASE_REV は rev 自身に固定（差分基準 verify は base==HEAD で空差分＝正しく fail する）。"""
    if not cmd.strip() or not rev or not (workdir / ".git").exists():
        return None
    wt = tempfile.mkdtemp(prefix="kiro-redgreen-")
    try:
        add = subprocess.run(["git", "-C", str(workdir), "worktree", "add", "--detach", wt, rev],
                             capture_output=True, text=True, timeout=timeout)
        if add.returncode != 0:
            return None
        base_env = {**(env or {}), "KIRO_BASE_REV": rev}
        ok, _ = run_verify(cmd, Path(wt), timeout, base_env)
        return ok
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        subprocess.run(["git", "-C", str(workdir), "worktree", "remove", "--force", wt],
                       capture_output=True, timeout=30)
        shutil.rmtree(wt, ignore_errors=True)


def verify_undiscriminating(cfg: "Config", task: "Task", cwd: Path, is_temp_clone: bool,
                            git_base, env: "dict | None") -> bool:
    """合成 verify が『act 前でも PASS＝変更を弁別しない偽 done』か（red-green の red 側検査）。
    対象は verify_validate ポリシー（off/synth/all）と per-task 上書きに従う。temp clone（workspace
    タスク）は act 前ツリーが手元に無いので対象外（既存の no-progress ガードに委ねる）。判定不能は False。"""
    vv = str(dict(task.extra).get("verify_validate", "") or cfg.verify_validate).lower()
    if vv in ("off", "none", "false"):
        return False
    src = dict(task.extra).get("verify_source", "")
    if vv == "synth" and src not in ("synth", "template", "reused"):
        return False                                   # synth ポリシーは自動生成 verify のみ検証
    if is_temp_clone or not (cwd / ".git").exists():
        return False
    base_rev = git_base[0] if isinstance(git_base, (tuple, list)) and git_base else ""
    return run_verify_at_rev(task.verify, cwd, base_rev, cfg.verify_timeout, env) is True


def resolve_verify_cwd(cfg: "Config") -> Path:
    """verify/acceptance を実行する作業ディレクトリ。明示の `verify_cwd`（CLI/設定）があればそれを、
    無ければ従来どおり `workdir`。git-bus 等で workdir に成果が出ないとき、対象 repo のクローン先を指す。"""
    if cfg.verify_cwd:
        p = Path(cfg.verify_cwd)
        return p if p.is_absolute() else (cfg.workdir / p)
    return cfg.workdir


def _task_verify_cwd(cfg: "Config", task: "Task") -> "tuple[Path, str | None]":
    """このタスクの verify/回帰を実行する作業ディレクトリと、片付けが要る一時 clone のパス（無ければ None）を返す。
    優先順位: 明示 verify_cwd > タスクの `- workspace:` 該当 repo の一時 clone（target/base ブランチ）> workdir。
    workspace 指定タスクは worker が成果を該当 repo の作業ブランチへ push し、git-bus ルートの workdir には
    出ない。そこを検証先にすると「成果の無い場所」で誤判定するため、該当 repo を指定 branch で clone し
    その中で検証する。clone は worker の push 先を反映するため都度取り直す。clone 失敗・path 不在は
    RuntimeError（呼び出し側で NG 扱い・黙って workdir に倒さない）。

    cwd は常に **clone のルート**に取る。verify コマンドはリポジトリのルートからの相対（例
    `cd api && yarn test`）で書かれる規約で、プランナーの生成指示・owns 突き合わせ（_verify_paths）・
    kiro-flow のワークスペース（エージェントはリポジトリ直下で path 配下のみ編集）と一致する。
    `path`（モノレポのサブフォルダ）は編集範囲/owns 用であり verify の cwd ではない。ここで
    `clone/path` に潜ると `cd api` 等の相対指定が二重になって verify が壊れ、$KIRO_BASE_REV を
    取り直す `.git` 判定（呼び出し側）も外れる。"""
    if cfg.verify_cwd:                              # 明示指定は常に最優先（運用の上書き）
        return resolve_verify_cwd(cfg), None
    spec = _workspace_spec_for(cfg, task)
    if spec and spec.get("url"):
        tmp = tempfile.mkdtemp(prefix="kiro-verify-")
        dest = str(Path(tmp) / "repo")
        branch = spec.get("target") or spec.get("base") or ""   # worker の push 先＝target、無ければ base
        try:
            _clone_repo_shallow(spec["url"], branch, dest)
        except (OSError, RuntimeError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"workspace repo の clone 失敗（{spec['url']}@{branch or '既定'}）: {e}") from e
        root = Path(dest)
        sub = (spec.get("path") or "").strip().strip("/")       # path は編集範囲。誤設定検出のため在処だけ確認
        if sub and not (root / sub).is_dir():
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"workspace の path が clone 内に無い: {sub}"
                               f"（{spec['url']}@{branch or '既定'}）")
        append_journal(cfg.journal, f"verify: {task.id} を {spec['url']}@{branch or '既定'}"
                                    + (f"（path={sub}）" if sub else "") + " のクローン内で検証")
        return root, tmp
    return resolve_verify_cwd(cfg), None            # workspace 未指定は従来どおり workdir


# ---------------------------------------------------------------------------
# verify の用意（人が書く負担を減らす）。完了条件は決定的なシェルが正典だが、人が書くのは難しい。
#   - `- verify_template: <名前> :: <引数...>` … 決定的に展開（エージェント不要）。
#   - `- accept: <自然言語の完了条件>`         … エージェントが決定的 verify を合成（偽 done 防止規則を織込）。
# どちらも最終的に concrete な `verify`（終了コード0=PASS）になり、done は verify のみが根拠の不変条件を保つ。
# 合成/展開できなければ verify は空のまま＝従来どおり人へ（done 不能）。
# ---------------------------------------------------------------------------
def _sh_q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def expand_verify_template(spec: str) -> "str | None":
    """`<名前> :: <引数...>` を決定的なシェル verify に展開する（エージェント不要）。未知の名前は None。
    鉄則どおり「履歴でなく最終状態/差分」を見る形にする（diff-contains は $KIRO_BASE_REV を使う）。"""
    name, _, rest = (spec or "").partition("::")
    name = name.strip().lower()
    rest = rest.strip()
    args = [x.strip() for x in rest.split("::")] if rest else []
    if name in ("file-contains", "contains") and len(args) >= 2:
        return f"grep -qF -- {_sh_q(args[1])} {_sh_q(args[0])}"        # path に needle を含む
    if name in ("file-exists", "exists") and args:
        return f"test -e {_sh_q(args[0])}"
    if name in ("defines", "symbol") and len(args) >= 2:               # path に symbol を定義
        sym, path = args[0], args[1]
        pat = f"def +{sym}|function +{sym}|{sym} *=|class +{sym}"
        return f"grep -qE {_sh_q(pat)} {_sh_q(path)}"
    if name in ("diff-contains", "grep-diff") and args:               # act 後の差分に needle（履歴に騙されない）
        return f'git log "$KIRO_BASE_REV"..HEAD -p 2>/dev/null | grep -qF -- {_sh_q(args[0])}'
    if name in ("cmd-succeeds", "tests-pass", "cmd", "run",            # 残り全体をコマンドとして実行
                "test-passes", "builds", "exit-zero") and rest:       # test-passes/builds/exit-zero は意図を明示する別名
        return rest
    if name in ("endpoint-returns", "http-status") and len(args) >= 2:  # <url> が <status> を返す
        url, status = args[0], args[1]
        return (f'test "$(curl -s -o /dev/null -w \'%{{http_code}}\' -- {_sh_q(url)})"'
                f' = {_sh_q(status)}')
    return None


def detect_repo_context(workdir: "Path") -> str:
    """テスト/ビルド基盤を決定的に検出し、合成 verify のヒント文にする（grep 退化を防ぐ）。
    package.json scripts・pytest/pyproject・Makefile ターゲット・go/cargo 等を軽く走査（有界）。"""
    hints: list = []
    try:
        pj = workdir / "package.json"
        if pj.exists():
            data = json.loads(pj.read_text(encoding="utf-8"))
            scripts = list((data.get("scripts") or {}).keys())[:8]
            hints.append("package.json（npm/yarn）: scripts=" + (", ".join(scripts) or "なし"))
    except (OSError, ValueError):
        pass
    if (workdir / "pytest.ini").exists() or (workdir / "pyproject.toml").exists() \
            or (workdir / "tox.ini").exists() or (workdir / "tests").is_dir():
        hints.append("Python（pytest 等）: `pytest -q` が使えることが多い")
    mk = workdir / "Makefile"
    if mk.exists():
        try:
            targets = re.findall(r"^([a-zA-Z0-9_.-]+):", mk.read_text(encoding="utf-8"), re.M)[:10]
            hints.append("Makefile: targets=" + (", ".join(targets) or "なし"))
        except OSError:
            pass
    if (workdir / "go.mod").exists():
        hints.append("Go: `go test ./...` / `go build ./...`")
    if (workdir / "Cargo.toml").exists():
        hints.append("Rust: `cargo test` / `cargo build`")
    return "\n".join(f"- {h}" for h in hints)[:800]


def _synth_verify_prompt(title: str, accept: str, hint: str = "", repo_ctx: str = "",
                         retry_note: str = "") -> str:
    extra = ""
    if retry_note:
        extra += f"\n**前回の合成は不採用でした（{retry_note}）。今度は必ず改善すること。**\n"
    if repo_ctx:
        extra += ("\nこのリポジトリで検出したテスト/ビルド基盤（可能ならこれを使い、存在チェックの grep へ"
                  f"退化させない）:\n{repo_ctx}\n")
    if hint:
        extra += ("\n過去の類似タスクで人が示した『done の見方』（参考にしてよいが、望む最終状態/差分を"
                  f"検査する原則は保つ）:\n- {hint}\n")
    return (
        "次のタスクの『完了条件（自然言語）』を、**決定的なシェルコマンド**に変換してください。"
        "終了コード 0 を PASS とみなします。\n"
        "規則: ①「履歴」ではなく「望む最終状態 / 差分」を検査する"
        "（`git log|grep` で過去コミットに当てない）②差分を見るなら環境変数 `$KIRO_BASE_REV`"
        "（act 前の HEAD）を使い `git log \"$KIRO_BASE_REV\"..HEAD ...` の形にする"
        "③外部状態に依存せず再現可能にする。④単なる存在 grep や恒真式に退化させず、"
        "可能ならテスト/ビルドコマンドで実挙動を確かめる。\n"
        f"タスク: {title}\n完了条件: {accept}\n{extra}\n"
        "出力はコマンド 1 行のみ（説明・コードフェンス不要）。検証コマンドを書けない場合は空行を返す。")


# 全角の文/句読点。シェルコマンドにはまず現れず、自然言語（散文・拒否文）の強い指標。
_PROSE_PUNCT = "。、！？；：「」『』（）"

# 常に真＝何も検証しない恒真式。合成 verify がこれに退化すると done の唯一根拠が意味を失う。
_TAUTOLOGY_RE = re.compile(
    r"^(?:true|:|/bin/true"
    r"|test\s+1\s*=\s*1|test\s+-n\s+.\S*|\[\s+1\s*=\s*1\s+\]"
    r"|echo\b.*|printf\b.*|exit\s+0)$")


def _verify_is_degenerate(cmd: str) -> bool:
    """合成 verify が「常に PASS＝何も検証しない」恒真式に退化していないか（決定的スクリーン）。
    red-green（変更前 fail・変更後 pass）を実行で確かめられない enqueue 時点でも、明白な恒真式は弾く。
    複合（; && || | 含む）は個別判定が難しいので通し、単純トークンの恒真だけを弾く（false negative 寄り）。"""
    s = (cmd or "").strip().strip(";").strip()
    if not s:
        return True
    if any(op in s for op in ("&&", "||", "|", ";", "\n")):
        return False                              # 複合は退化と断定しない（誤棄却を避ける）
    return bool(_TAUTOLOGY_RE.match(s))


def _looks_like_shell_command(line: str) -> bool:
    """合成された 1 行が「決定的なシェルコマンド」か、エージェントの自然言語かを判定する。
    全角の文/句読点を含むものは散文とみなして弾き、残りは `sh -n`（構文解析のみ・非実行）で
    妥当性を確認する。疑わしきは False（→ verify 未定義のまま人の判断へ）。"""
    s = line.strip()
    if not s:
        return False
    if any(ch in s for ch in _PROSE_PUNCT):       # 全角の文/句読点 → 自然言語
        return False
    try:
        # sh -n は構文チェックのみで実行しない。不完全な if/未閉じクォート等の散文を弾く。
        chk = subprocess.run(["sh", "-n", "-c", s], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True          # 構文チェック不能な環境では句読点判定のみで通す（best-effort）
    return chk.returncode == 0


def _first_command_line(out: str) -> str:
    """合成出力の先頭の「意味あるコマンド行」を取り出す（コメント/コードフェンス/空行を飛ばす）。"""
    for line in (out or "").splitlines():
        line = _strip_code(line.strip())
        if line and not line.startswith("#"):
            return line
    return ""


def synth_verify(cfg: "Config", title: str, accept: str, kiro_run=None,
                 hint: str = "", repo_ctx: str = "", attempts: int = 2) -> str:
    """自然言語の完了条件 accept からエージェント（kiro-cli）が決定的 verify を合成する。
    失敗・不能・kiro-cli 不在は空文字（→ verify 未定義のまま人へ）。テストは kiro_run を注入する。
    hint（過去の類似 learn）・repo_ctx（検出したテスト/ビルド基盤）で grep 退化を抑える。
    **自己修復（多候補）**: 散文/シェル非妥当/恒真式に退化した候補は不採用とし、理由を添えて最大
    attempts 回まで再合成させる（1 回で諦めず、より良い候補を引き出す）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="verify"))
    retry_note = ""
    for _ in range(max(1, attempts)):
        try:
            out = run(_synth_verify_prompt(title, accept, hint, repo_ctx, retry_note), cfg.model)
        except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等は合成せず人へ
            return ""
        cand = _first_command_line(out)
        if not cand:
            retry_note = "空 or 散文だった"; continue
        # 自然言語（説明・拒否文）を shell=True に流すと ; | && ` > rm 等が誤実行されうるため弾く。
        if not _looks_like_shell_command(cand):
            retry_note = "シェルコマンドでなかった"; continue
        # 恒真式（true / echo … 等）は done の根拠にならない＝不採用。実挙動を確かめる候補を求める。
        if _verify_is_degenerate(cand):
            retry_note = "恒真式に退化していた。テスト/ビルド/差分/最終状態で実挙動を確かめよ"; continue
        return cand
    return ""


def ensure_verify(cfg: "Config", task: "Task", kiro_run=None) -> bool:
    """task に concrete な verify が無ければ `verify_template`（決定的）→ `accept`（合成）の順で用意する。
    用意できたら task.verify を埋め `verify_source` を記録して True を返す（呼び出し側が persist する）。"""
    if task.verify:
        return False
    ex = dict(task.extra)
    tmpl = ex.get("verify_template", "").strip()
    if tmpl:
        cmd = expand_verify_template(tmpl)
        if cmd:
            task.verify = cmd
            task.extra.append(("verify_source", "template"))
            return True
    accept = ex.get("accept", "").strip()
    if accept:
        # ① まず実績のある検証済み verify を再利用（毎回ゼロ合成しない。red-green が別途弁別を確かめる）。
        reused = find_learned_verify(cfg, task) if cfg.learn else None
        if reused:
            task.verify = reused
            task.extra.append(("verify_source", "reused"))
            return True
        # ② 無ければ合成。過去の類似 learn（done の見方）と検出したテスト/ビルド基盤を注入し grep 退化を防ぐ。
        matched = find_learned_resolution(cfg, task) if cfg.learn else None
        hint = matched[1] if matched else ""
        repo_ctx = detect_repo_context(resolve_verify_cwd(cfg))
        rm = repo_map_context(cfg, [task.get("workspace")] if task.get("workspace") else None,
                              limit=600, max_files=1)   # 理解の要約も合成の材料に（有界）
        if rm:
            repo_ctx = (repo_ctx + "\n" if repo_ctx else "") + rm
        pr = project_rules_context(cfg, limit=400)      # 恒常ルール（テスト実行方法等）も合成に効かせる
        if pr:
            repo_ctx = (repo_ctx + "\n" if repo_ctx else "") + pr
        cmd = synth_verify(cfg, task.title, accept, kiro_run, hint=hint, repo_ctx=repo_ctx)
        if cmd:
            task.verify = cmd
            task.extra.append(("verify_source", "synth"))
            return True
    return False


def has_verify_plan(task: "Task") -> bool:
    """concrete な verify か、それを用意する材料（accept / verify_template）を持つか。"""
    if task.verify:
        return True
    ex = dict(task.extra)
    return bool(ex.get("accept", "").strip() or ex.get("verify_template", "").strip())


def resolve_kiro_flow(explicit: "str | None") -> "list[str]":
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    found = shutil.which("kiro-flow")
    if found:
        return [found]
    local = Path(__file__).resolve().parent.parent / "kiro-flow" / "kiro-flow.py"
    return [sys.executable, str(local)]


def _charter_definition(ch: "Charter") -> str:
    parts = []
    if ch.goal:
        parts.append(f"目標: {ch.goal}")
    # 対象リポジトリ・リンクは「どこで・何に対して作業するか」の重要情報。goal 直後に置き、
    # ワーカー（gitlab イシュー等）へ確実に伝わるよう truncation で落ちにくい位置にする。
    # タスクは説明（desc）を見て関係する repo を選び、その base/target ブランチを踏まえて作業する。
    if ch.repo_specs:
        lines = ["対象リポジトリ（タスクは説明を見て関係するものを選び、その base/target ブランチで作業。"
                 "path 指定があればそのフォルダ配下のみ変更）:"]
        for r in ch.repo_specs:
            head = f"- {r['name']} = {r['url']}" if r["name"] else f"- {r['url']}"
            br = []
            if r.get("path"):
                br.append(f"path={r['path']}")
            if r["base"]:
                br.append(f"base={r['base']}")
            if r["target"]:
                br.append(f"target={r['target']}")
            if r.get("readonly"):
                br.append("参照のみ・push しない")
            elif r.get("owns"):
                br.append("書込先候補（owns: " + ", ".join(r["owns"][:3]) + "）")
            if br:
                head += "（" + ", ".join(br) + "）"
            lines.append(head)
            if r["desc"]:
                lines.append(f"    説明: {r['desc']}")
        parts.append("\n".join(lines))
    if ch.link_specs:
        lines = ["関連リンク（wiki/ドキュメント/横展開先など。踏まえること）:"]
        for l in ch.link_specs:
            lines.append(f"- {l['text']}" + (f" — {l['desc']}" if l["desc"] else ""))
        parts.append("\n".join(lines))
    if ch.constraints:
        parts.append("制約:\n" + "\n".join(f"- {c}" for c in ch.constraints))
    if ch.assumptions:
        parts.append("前提:\n" + "\n".join(f"- {a}" for a in ch.assumptions))
    if ch.deliverables:
        parts.append("成果物:\n" + "\n".join(f"- {d}" for d in ch.deliverables))
    return "\n".join(parts).strip()


def _charter_plan_signature(ch: "Charter") -> str:
    """charter の「backlog 分解に効く内容」の安定ハッシュ（目標/repos/リンク/制約/前提/成果物）。
    mtime ではなく *内容* ベースなので、state_git 同期やファイルコピーで mtime だけ変わっても
    誤検知せず、内容が実際に変わったときだけ変化する。これを project state に記録し、次回 run で
    charter が変わっていれば（＝署名が違えば）消化可能タスクがあっても backlog を再計画する。
    acceptance は done 判定に効くが分解入力ではないため署名には含めない（評価側で反映される）。"""
    return hashlib.sha256(_charter_definition(ch).encode("utf-8")).hexdigest()


def _charter_full_signature(ch: "Charter") -> str:
    """charter.md 全文（raw）の安定ハッシュ。acceptance も含めて「何か変わったか」を見る。
    承認済み（accepted）プロジェクトを再開すべきか（人が charter.md を更新したか）の判定専用。
    _charter_plan_signature は分解入力だけで acceptance を除くため、acceptance だけの更新では
    変わらず、この判定には使えない（accepted のまま何度も再収束してしまう）。"""
    return hashlib.sha256(ch.raw.encode("utf-8")).hexdigest()


def charter_context(cfg: "Config", max_chars: int = 1400, task: "Task | None" = None) -> str:
    """charter.md（プロジェクト定義＝目標/制約/前提/成果物）を act ワーカーへ渡す文脈に要約する。
    **`project` でも通常 `run` でも、charter.md が存在すれば全 act に注入**＝kiro-flow のワーカーが
    プロジェクトの北極星（目標・制約）を踏まえて働く。`## links` があればリンク先プロジェクトの定義も
    続けて取り込む（横展開）。charter 無し（通常運用）では空＝従来どおり。
    task を渡すと `charter:` タグの charter（複数 charter 運用）を選ぶ。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        return ""
    if ch is None:
        return ""
    block = _charter_definition(ch)
    if len(block) > max_chars:                  # 有界化（先頭＝目標/制約を優先して残す）
        block = block[:max_chars].rstrip() + " …"
    # 横展開: リンク先プロジェクトの定義を要約付与（有界・1 階層）
    for name, root in resolve_linked_projects(cfg, ch):
        lp = root / "charter.md"
        if not lp.exists():
            continue
        try:
            linked = parse_charter(lp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = _charter_definition(linked)
        if d:
            block += f"\n\n[リンク: {name}] の定義（横展開・踏まえること）:\n" + d[:600]
    return block


def decision_context(cfg: "Config", task: Task, max_chars: int = 1000) -> str:
    """このタスクの過去の判断記録（needs の判断結果・人の承認/差し戻し/learn）を act ワーカーへ渡す。
    **project/backlog を問わず**、`decisions/<id>.md` があれば注入する（末尾＝直近を優先して有界化）。"""
    dp = decision_path(cfg, task.id)
    if not dp.exists():
        return ""
    try:
        txt = dp.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not txt:
        return ""
    if len(txt) > max_chars:
        txt = "…\n" + txt[-max_chars:]
    return txt


def _scan_learn_lines(decisions_dir: Path, limit: int = 12) -> "list[str]":
    """decisions/ 配下の `- learn: <title> :: <guide>` 行を集める（再利用可能な人の判断）。"""
    out: list[str] = []
    if not decisions_dir.exists():
        return out
    for f in sorted(decisions_dir.glob("*.md")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                m = LEARN_RE.match(line.strip())
                if m:
                    out.append(f"{m.group('title').strip()} :: {m.group('guide').strip()}")
        except OSError:
            continue
    return out[-limit:]


def linked_learnings_context(cfg: "Config", max_chars: int = 800,
                             task: "Task | None" = None) -> str:
    """charter `## links` 先プロジェクトの判断（decisions の learn）を act ワーカーへ取り込む（横展開）。
    リンク先で人が下した再利用可能な判断を、別プロジェクトの作業にも効かせる（明示 opt-in・有界）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        return ""
    if ch is None or not ch.links:
        return ""
    lines: list[str] = []
    for name, root in resolve_linked_projects(cfg, ch):
        for ln in _scan_learn_lines(root / "decisions"):
            lines.append(f"[{name}] {ln}")
    if not lines:
        return ""
    block = "\n".join(f"- {x}" for x in lines)
    return block[:max_chars]


def build_request(task: Task, cfg: "Config | None" = None) -> str:
    base = (f"{task.title}\n\n"
            f"このタスクは完了条件を満たすまで反復し、満たしたら終了すること（loop-until-done）。\n"
            f"完了条件: 次のシェルコマンドが終了コード 0 で成功すること:\n"
            f"  {task.verify or '（verify 未定義）'}\n\nタスクID: {task.id}")
    fb = task.feedback()
    if fb:
        base += f"\n\n人からのフィードバック（必ず反映すること）:\n{fb}"
    if cfg is not None and task.get("spec_for"):
        # spec 作成タスク: 3 ファイルの作成指示（実装はさせない）を要求文に載せる
        base += "\n\n" + _spec_instructions(cfg, task)
    if cfg is not None:
        # spec 展開で生まれた実装タスク・総合検証タスクには spec/design を文脈注入（有界）
        sc = spec_context(cfg, task)
        if sc:
            base += "\n\n仕様（spec 前段の成果・必ず従うこと）:\n" + sc
        # 参照リポジトリは要求本文に畳まず、kiro-flow へ `--reference` で構造化伝搬する
        # （分解後の各ノード／gitlab イシューにも確実に届くように）。
        # 定義（charter）と判断結果（decisions）を、project でも通常 run でもワーカーへ渡す。
        cc = charter_context(cfg, task=task)
        if cc:
            base += ("\n\nプロジェクト定義（charter・常に踏まえること。成果物が目標/制約に反しないこと）:\n"
                     + cc)
        # プロジェクトルール（rules.md・人が書く＋効いた learn の自動昇格）。learn の recall が
        # 類似タスク限定なのに対し、これは全タスクへ常時注入される恒常ルール層。
        pr = project_rules_context(cfg)
        if pr:
            base += "\n\nプロジェクトルール（rules.md・全タスク共通。必ず従うこと）:\n" + pr
        # リポジトリ理解（context/*.md・生成は opt-in repo_map / 人の手書きも可）。workspace 指定
        # タスクはその repo 分だけ、無指定はプロジェクトの全ファイル（有界）を注入する。
        rm = repo_map_context(cfg, [task.get("workspace")] if task.get("workspace") else None)
        if rm:
            base += "\n\nリポジトリ理解（構造・規約・ビルド/テストコマンド）:\n" + rm
        dc = decision_context(cfg, task)
        if dc:
            base += ("\n\nこのタスクに関する過去の判断記録（needs の判断結果・必ず踏まえること）:\n" + dc)
        lc = linked_learnings_context(cfg, task=task)
        if lc:
            base += ("\n\nリンク先プロジェクトの判断（横展開・参考にすること）:\n" + lc)
        # 似た過去タスクの学び（gitlab 却下/承認・needs の learn）を **分解と実装の両方**に効かせる。
        # 要求本文に載るため flow-planner が分解時に、ワーカーが実装時に踏まえる（＝分解の再考にも届く）。
        if cfg.learn:
            matched = find_learned_resolution(cfg, task)
            if matched:
                base += ("\n\n類似タスクでの学び（分解・verify・実装で踏まえ、同種の手戻りを繰り返さないこと）:\n"
                         f"- {matched[1]}")
    return base


def decide_pace(cfg: "Config", cycle_elapsed: float) -> float:
    pace = cfg.pace
    if cfg.max_seconds and cfg.max_cycles:
        pace = max(pace, cfg.max_seconds / cfg.max_cycles)
    return max(0.0, pace - cycle_elapsed)


def decide_location(task: Task, policy: Policy, cfg: "Config") -> str:
    """act の実行モードを local / daemon / remote に決める（kiro-flow の起動方法を統合）。

      local  : kiro-flow run（単発・自己完結・daemon 不要）
      daemon : ローカルバスの daemon に submit して結果を待つ（warm worker 再利用）
      remote : 共有 git バス（別マシンの daemon）へ submit＝真のオフロード
    `--location auto`（既定）: offload 一致かつ git-bus → remote / ローカル daemon 稼働 → daemon / それ以外 local。
    明示指定（local/daemon/remote）はそれを優先（remote は git-bus 必須、無ければ local）。"""
    if task.get("spec_for"):
        # spec 作成タスク（§5.10）: 成果物 specs/<id>/ はプロジェクトの workdir に要る。
        # daemon/remote だと別プロセス・別マシンに生成されローカルの verify が通らないため、
        # location 設定に依らず常にローカル単発 run で実行する（executor の差し替えは
        # build_kiro_flow_cmd が行う。local 固定はその前提でもある）。
        return "local"
    loc = cfg.location
    if loc == "auto":
        if cfg.git_bus and any(task.matches(p) for p in policy.offload):
            return "remote"
        if daemon_running(cfg, use_git=False):
            return "daemon"
        return "local"
    if loc == "remote" and not cfg.git_bus:
        return "local"
    return loc


def _kf_base(cfg: "Config", use_git: bool) -> "list[str]":
    base = resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]
    if use_git and cfg.git_bus:
        base += ["--git", cfg.git_bus, "--git-branch", cfg.git_branch]
        if cfg.git_subdir:
            base += ["--git-subdir", cfg.git_subdir]
    return base


def _is_reference_repo(spec: dict) -> bool:
    """owns: が無い repo は参照リポジトリ（read-only）。書込先ワークスペースの候補にしない。"""
    return not spec.get("owns")


def _split_tokens(raw) -> "list[str]":
    return [_strip_code(t) for t in re.split(r"[,\s]+", str(raw or "").strip()) if _strip_code(t)]


def _raw_url_spec(tok: str) -> "dict | None":
    """charter に無い素の URL トークンを最小の workspace spec にする（owns 無し＝参照ではなく明示指定用）。"""
    if "://" in tok or "@" in tok or tok.endswith(".git"):
        return {"name": "", "url": tok, "desc": "", "base": "", "target": "", "path": "",
                "readonly": False, "owns": []}
    return None


def route_target(task: Task, policy: "Policy") -> str:
    """policy の `route: <パターン> -> <repo名>` を順に評価し、最初に一致した repo 名を返す（無ければ ""）。"""
    for rule in policy.route:
        for sep in ("->", "=>", "→"):
            if sep in rule:
                pattern, name = rule.split(sep, 1)
                if task.matches(pattern.strip()):
                    return _strip_code(name.strip())
                break
    return ""


def _glob_prefix(g: str) -> str:
    """グロブの先頭の非ワイルドカード部分（`apps/api/**` → `apps/api/`）。"""
    m = re.search(r"[*?\[]", g)
    return g[:m.start()] if m else g


def _owns_matches(owns: "list[str]", tok: str) -> bool:
    """パストークン tok が owns グロブのどれかに該当するか（fnmatch ＋ 先頭プレフィックス一致）。"""
    for g in owns:
        if fnmatch.fnmatch(tok, g) or fnmatch.fnmatch(tok, g.rstrip("/") + "/*"):
            return True
        pre = _glob_prefix(g).rstrip("/")
        if pre and (tok == pre or tok.startswith(pre + "/")):
            return True
    return False


def _verify_paths(verify: str) -> "list[str]":
    """verify シェルコマンドから「操作するパス」らしきトークンを抽出する（owns 突き合わせ用）。
    シェル区切りで割り、引用符/先頭 ./ を外し、`/` か拡張子を含むパスらしいものだけ残す。"""
    out: "list[str]" = []
    for t in re.split(r"[\s;|&()<>=]+", verify or ""):
        t = t.strip().strip("'\"`").lstrip("./")
        if t and ("/" in t or re.search(r"\.\w+$", t)):
            out.append(t)
    return out


def _infer_workspace_from_paths(workspaces: "list[dict]", paths: "list[str]") -> "dict | None":
    """パス群を各ワークスペースの owns: と突き合わせ、所有する1つを返す。曖昧（複数一致）/不一致なら
    候補が1つだけのときはそれ、そうでなければ None。"""
    if not paths:
        return workspaces[0] if len(workspaces) == 1 else None
    hits = [s for s in workspaces if any(_owns_matches(s.get("owns", []), p) for p in paths)]
    if len(hits) == 1:
        return hits[0]
    return workspaces[0] if (not hits and len(workspaces) == 1) else None


def _owns_infer(task: Task, workspaces: "list[dict]") -> "dict | None":
    """タスクが触る予定パス（`- paths:` ヒント。無ければ verify コマンドから抽出）を charter の owns:
    グロブと突き合わせ、所有するワークスペースを推定する。曖昧（複数一致）なら推定しない。"""
    paths = _split_tokens(task.get("paths")) or _verify_paths(task.verify)
    if not paths:
        return None
    hits = [s for s in workspaces if any(_owns_matches(s.get("owns", []), p) for p in paths)]
    return hits[0] if len(hits) == 1 else None


def _route_agent_prompt(task: Task, workspaces: "list[dict]") -> str:
    lines = ["次のタスクをコミットすべき書込先リポジトリ（ワークスペース）を1つだけ選んでください。",
             f"タスク: {task.title}", f"verify: {task.verify or '（未定義）'}", "", "候補リポジトリ:"]
    for s in workspaces:
        owns = "・".join(s.get("owns", []))
        lines.append(f"- {s.get('name') or s['url']}"
                     + (f"（担当: {owns}）" if owns else "")
                     + (f": {s['desc']}" if s.get("desc") else ""))
    lines.append('\n出力は JSON のみ: {"workspace": "<repo名>"}（判断できなければ {"workspace": ""}）')
    return "\n".join(lines)


def route_agent(cfg: "Config", task: Task, workspaces: "list[dict]",
                kiro_run=None) -> str:
    """曖昧なタスクの書込先を LLM に1つ選ばせる（決定論で決まらなかったときのみ）。失敗時は ""。"""
    kiro_run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="route"))
    try:
        out = kiro_run(_route_agent_prompt(task, workspaces), cfg.model)
        data = _extract_json_obj(out)
        return _strip_code(str((data or {}).get("workspace") or "").strip())
    except Exception:  # noqa: BLE001 — 推定失敗は「決まらない」に倒す
        return ""


def resolve_workspace(cfg: "Config", task: Task, policy: "Policy") -> "tuple[dict | None, str]":
    """タスク → ちょうど1つの書込先ワークスペース spec を決める。解決順（上が優先）:
      1. 明示 `- workspace:`  2. policy `route:`  3. charter owns: 推定
      4. auto-route エージェント（route_planner=kiro）  5. 既定（default_workspace / 候補が1つ）
    返り値 (spec or None, routed_by)。None は書込先なし＝読み取り専用 run（調査タスク等）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        ch = None
    specs = registry_specs(cfg, ch)               # repos ファイル単独（charter 無し）でも解決できる
    smap = repo_spec_map(specs)
    workspaces = [s for s in specs if not _is_reference_repo(s)]

    explicit = _strip_code(str(task.get("workspace") or "").strip())
    if explicit:                                  # 1. 人/過去ルーティングの明示指定（最優先）
        sp = smap.get(explicit) or _raw_url_spec(explicit)
        if sp:
            return sp, "explicit"
    name = route_target(task, policy)             # 2. route: パターンルール（決定論）
    if name and smap.get(name) and not _is_reference_repo(smap[name]):
        return smap[name], "rule"
    sp = _owns_infer(task, workspaces)            # 3. charter owns: パス推定（決定論）
    if sp:
        return sp, "owns"
    if cfg.route_planner == "agent" and workspaces:  # 4. auto-route エージェント（曖昧時のみ）
        nm = route_agent(cfg, task, workspaces)
        if nm and smap.get(nm) and not _is_reference_repo(smap[nm]):
            return smap[nm], "agent"
    if cfg.default_workspace and smap.get(cfg.default_workspace):  # 5a. 既定ワークスペース
        return smap[cfg.default_workspace], "default"
    if len(workspaces) == 1:                       # 5b. 書込先候補が1つだけ → それ
        return workspaces[0], "sole"
    return None, "none"


def resolve_and_persist_workspace(cfg: "Config", task: Task, policy: "Policy") -> "dict | None":
    """タスクを書込先ワークスペースへルーティングし、決定を md（`- workspace:`/`- routed_by:`）へ
    書き戻して安定・監査可能にする（毎サイクル LLM を呼ばない）。返り値は解決した spec か None。"""
    spec, routed_by = resolve_workspace(cfg, task, policy)
    if spec and routed_by != "explicit":          # 明示指定はそのまま（上書きしない）
        task.set("workspace", spec.get("name") or spec["url"])
        task.set("routed_by", routed_by)
        persist_task(cfg, task)
    return spec


def task_branch_name(cfg: "Config", task: "Task") -> str:
    """タスク単位ターゲットブランチ名（kp/<task-id>）。全試行（リトライ含む）の成果を集約する。"""
    return f"{getattr(cfg, 'task_branch_prefix', 'kp/') or 'kp/'}{task.id}"


def _workspace_token(spec: dict) -> str:
    """workspace spec を kiro-flow の `--workspace` 値（JSON）にする。url/path/base/target/desc/branch を伝搬。
    worker（clone・作業ブランチ）と gitlab の起票先解決の双方で使われる。"""
    meta = {k: spec[k] for k in ("path", "base", "target", "desc", "branch") if spec.get(k)}
    if meta.get("desc") and len(meta["desc"]) > 300:
        meta["desc"] = meta["desc"][:300]         # argv 肥大を防ぐ（説明は有界に）
    return json.dumps({"url": spec["url"], **meta}, ensure_ascii=False, separators=(",", ":"))


def _workspace_spec_for(cfg: "Config", task: Task) -> "dict | None":
    """既に解決・永続化済みの `- workspace:`（_act_batch で確定）を charter spec へ。
    未解決なら None（読み取り専用 run）。ルーティングはここでは行わない（決定は1度だけ）。"""
    name = _strip_code(str(task.get("workspace") or "").strip())
    if not name:
        return None
    try:
        smap = repo_spec_map(registry_specs(cfg, charter_for_task(cfg, task)))
    except (OSError, ValueError):
        smap = {}
    spec = smap.get(name) or _raw_url_spec(name)
    if spec and getattr(cfg, "task_branch", False):
        # タスク単位ターゲットブランチ: kiro-flow は run 毎の kf/<run-id> の代わりにこのブランチへ
        # push する（リトライも同一ブランチに積み増し、レビュー・MR の対象を 1 本に集約する）
        spec = {**spec, "branch": task_branch_name(cfg, task)}
    return spec


def _workspace_cmd_args(cfg: "Config", task: Task) -> "list[str]":
    """kiro-flow へ渡す `--workspace`（唯一の書込先）。書込先が無ければ空＝読み取り専用 run。"""
    spec = _workspace_spec_for(cfg, task)
    return ["--workspace", _workspace_token(spec)] if spec else []


def _reference_token(spec: dict) -> str:
    """参照リポジトリ spec を kiro-flow の `--reference` 値（JSON）にする。url/path/base/desc を伝搬。"""
    meta = {k: spec[k] for k in ("path", "base", "desc") if spec.get(k)}
    if meta.get("desc") and len(meta["desc"]) > 300:
        meta["desc"] = meta["desc"][:300]
    return json.dumps({"url": spec["url"], **meta}, ensure_ascii=False, separators=(",", ":"))


def _reference_cmd_args(cfg: "Config", task: Task) -> "list[str]":
    """kiro-flow へ渡す `--reference` 列（参照リポジトリ＝読むだけ。executor が描画する）。"""
    args: "list[str]" = []
    for spec in task_reference_specs(cfg, task):
        args += ["--reference", _reference_token(spec)]
    return args


def task_reference_specs(cfg: "Config", task: Task) -> "list[dict]":
    """このタスクが参照する（書き込まない）リポジトリの spec 列。charter の owns: 無しエントリ全部に、
    タスクの `- refs:`（および `- repos:` に挙げた参照先）で明示したものを足す。書込先 `- workspace:`
    に解決された url は除く（書込先は参照に含めない）。要求本文へ記述として埋め込む（clone はしない）。"""
    try:
        ch = charter_for_task(cfg, task)
    except (OSError, ValueError):
        ch = None
    specs = registry_specs(cfg, ch)
    smap = repo_spec_map(specs)
    ws = _workspace_spec_for(cfg, task)
    ws_url = ws["url"] if ws else None
    out: "list[dict]" = []
    seen: "set[str]" = set()
    refs = [s for s in specs if _is_reference_repo(s)]
    for tok in _split_tokens(task.get("refs")) + _split_tokens(task.get("repos")):
        sp = smap.get(tok) or _raw_url_spec(tok)
        if sp:
            refs.append(sp)
    for s in refs:
        url = s.get("url")
        if url and url not in seen and url != ws_url:   # 書込先は参照に含めない
            seen.add(url)
            out.append(s)
    return out


def build_kiro_flow_cmd(task: Task, cfg: "Config", use_git: bool = False) -> "list[str]":
    """kiro-flow run（都度起動）のコマンド。planner/executor を制御できる（submit では不可）。
    書込先は _act_batch で確定・永続化済みの `- workspace:` を読む（再ルーティングしない）。"""
    executor = cfg.executor
    if task.get("spec_for") and executor_delegates(cfg):
        # spec 作成タスクは委譲しない（§5.10）: gitlab 等の委譲先では specs/<id>/ がローカルに
        # 生成されず verify が成立しない。組み込み agent でローカル完結させる
        # （decide_location が spec タスクを local 固定しているため、この差し替えが必ず効く）。
        executor = "agent"
    cmd = (_kf_base(cfg, use_git) + _workspace_cmd_args(cfg, task)
           + _reference_cmd_args(cfg, task) + [
        "run", build_request(task, cfg), "--planner", cfg.flow_planner,
        "--executor", executor, "--max-iterations", str(cfg.max_iterations)])
    # 委譲 executor（gitlab）の却下は kiro-flow 内部で再委譲せず即失敗させ、kiro-project の
    # 通常リトライ（人コメント注入つき）に委ねる。複数イシューの濫造を防ぐ。
    if executor not in ("agent", "stub"):
        cmd += ["--max-retries", "0"]
    return cmd


def daemon_lock_path(cfg: "Config", use_git: bool) -> Path:
    """kiro-flow daemon の singleton ロックパス（kiro-flow と同一規則）。

    外部起動の daemon を取りこぼさないため、kiro-flow と完全に同じ導出をする:
      - ロック置き場は設定 `lock_dir`（無ければ tempdir 配下）
      - local キーは realpath で canonical 化（symlink/相対パスのズレを吸収）"""
    if use_git and cfg.git_bus:
        key = f"git::{cfg.git_bus}@{cfg.git_branch}/{cfg.git_subdir or ''}"
    else:
        key = "local::" + os.path.realpath(str(cfg.bus))
    h = hashlib.sha1(key.encode()).hexdigest()
    base = cfg.lock_dir or str(Path(tempfile.gettempdir()) / "kiro-flow-locks")
    return Path(base) / f"daemon-{h}.lock"


def _pid_alive(pid: int) -> bool:
    """pid が生存しているか（POSIX）。0/負や不在は False。別ユーザのプロセスは生存扱い。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # 別ユーザの生存プロセス（送れないだけ）
    except OSError:
        return False
    return True


def _lock_pid(p: Path) -> int:
    """ロックファイル先頭行の pid を読む（kiro-flow daemon が記録）。読めなければ 0。"""
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return 0
    try:
        return int(lines[0]) if lines else 0
    except ValueError:
        return 0


def _flock_held(p: Path) -> "bool | None":
    """flock の保持状況。True=保持中 / False=未保持 / None=判定不能（fcntl 無し・非対応FS 等）。"""
    if fcntl is None:
        return None
    try:
        f = open(p, "r+")
    except OSError:
        return None
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False           # 取得できた = 誰も保持していない
    except BlockingIOError:
        return True            # 保持されている = daemon 稼働中
    except OSError:
        return None            # flock 非対応FS 等 → pid で判定へ
    finally:
        f.close()


def daemon_running(cfg: "Config", use_git: bool = False) -> bool:
    """対象バスの kiro-flow daemon が稼働中かを判定する。
    flock を第一の根拠とし、判定不能（fcntl 無し / 異種FS）なら daemon が記録した
    pid の生存で補完する。これで外部起動・Windows・NFS 上の daemon も発見できる。"""
    p = daemon_lock_path(cfg, use_git)
    if not p.exists():
        return False
    held = _flock_held(p)
    if held is not None:
        return held
    return _pid_alive(_lock_pid(p))


def _act_run(task: Task, cfg: "Config", use_git: bool = False) -> "tuple[bool, str]":
    """kiro-flow run で都度起動（同期実行）。daemon 不要。"""
    cmd = build_kiro_flow_cmd(task, cfg, use_git)
    try:
        # act_timeout=0（以下）はタイムアウト無効＝完了まで待つ（gitlab 等の長時間委譲向け）。
        proc = subprocess.run(cmd, cwd=str(cfg.workdir),
                              timeout=(cfg.act_timeout if cfg.act_timeout > 0 else None),
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return (False, f"kiro-flow run タイムアウト（{cfg.act_timeout}s）")
    except FileNotFoundError as e:
        return (False, f"kiro-flow を起動できません: {e}")
    return (proc.returncode == 0, (proc.stdout or "")[-300:].strip())


def _load_task_file(cfg: "Config", tid: str) -> "Task | None":
    """backlog/<id>.md をディスクから読み直す（無い/読めないなら None）。"""
    p = cfg.backlog / f"{tid}.md"
    try:
        return parse_task(p.read_text(encoding="utf-8"), tid) if p.exists() else None
    except OSError:
        return None


def _task_file_revised(cfg: "Config", task: Task) -> bool:
    """実行中の revise（軌道修正）が入ったか＝backlog ファイルに `revised` マーカーがあるか。
    act の結果待ちループから毎ポーリング呼ばれるため、小さなファイル読みだけで判定する。"""
    fresh = _load_task_file(cfg, task.id)
    return fresh is not None and bool(fresh.get("revised"))


def _adopt_task(task: Task, fresh: Task) -> None:
    """in-memory の Task をディスクの内容（fresh）へ合わせる（人の revise/直接編集の採用）。"""
    task.title, task.status, task.source = fresh.title, fresh.status, fresh.source
    task.priority, task.verify, task.retries = fresh.priority, fresh.verify, fresh.retries
    task.extra = list(fresh.extra)


def _requeue_revised(cfg: "Config", task: Task, fresh: Task, cycle: int) -> None:
    """実行中に人が revise したタスクを、結果を確定させずに修正内容で積み直す。
    verify も done もしない（方向の変わった成果を判定しても意味を持たないため）。"""
    fresh.drop("revised")
    fresh.status = "ready"
    _adopt_task(task, fresh)
    persist_task(cfg, task)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の revise により積み直し"
                                "（この試行の結果は確定しない）")


def _submit_req_id(task: Task, cfg: "Config") -> str:
    """リブート跨ぎで同じ act 試行へ再接続するための決定的 req_id。

    （backlog パス, task.id, retries）で一意にする——PC のシャットダウン等で submit の
    待機ごと消えても、再起動後の同じ試行は同じ req_id を再 submit するため、kiro-flow 側の
    既存 run（daemon が孤児を自動再開する）に合流して結果を受け取れる＝二重実行しない。
    リトライ（retries+1）は新しい試行＝新しい run。backlog パスの hash は共有バスに
    複数プロジェクトが乗るときの衝突を防ぐ。人の revise（rev 世代）も新しい試行＝
    新しい run にする（軌道修正後の act が修正前の古い run に合流しないように）。"""
    return _req_id_for(task, cfg, task.retries)


def _req_id_for(task: Task, cfg: "Config", retries: int) -> str:
    """指定 retries 世代の決定的 req_id（_submit_req_id の一般化）。"""
    h = hashlib.sha1(str(cfg.backlog.resolve()).encode()).hexdigest()[:8]
    tid = re.sub(r"[^\w.-]+", "_", str(task.id))[:60]
    rev = str(task.get("rev", "") or "").strip()
    return f"req-{h}-{tid}-r{retries}" + (f"-v{rev}" if rev else "")


def _prev_req_id(task: Task, cfg: "Config") -> "str | None":
    """直前の試行（retries-1・同 rev）の run-id。retries==0（初回）なら None。
    kiro-flow の --inherit-from に渡し、タイムアウト/失敗した先行 run から確定済みノードを
    引き継ぎつつ先行 run を掃除させる。"""
    return _req_id_for(task, cfg, task.retries - 1) if task.retries > 0 else None


class _Pending:
    """act の第3の結果＝『実行層 daemon へ非ブロッキング submit 済み・まだ終端していない』。
    run_loop はこれを受けたらタスクを offloaded にして settle をスキップし、次パスでポーリングする。"""
    __slots__ = ("run_id",)

    def __init__(self, run_id: str):
        self.run_id = run_id


def _flow_result_once(cfg: "Config", use_git: bool, run_id: str) -> "tuple[bool, bool, str]":
    """kiro-flow result を1回だけ読む（待たない）。(terminal, ok, msg) を返す。
    terminal=run が done/failed に達したか、ok=failed でないか。取得不能は (False,...) で継続待ち扱い。"""
    base = _kf_base(cfg, use_git)
    try:
        res = subprocess.run(base + ["result", "--run-id", run_id, "--json"],
                             cwd=str(cfg.workdir), timeout=60, capture_output=True, text=True)
        data = json.loads(res.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError, ValueError):
        return (False, False, "")
    if not data.get("done"):
        return (False, False, "")
    if data.get("status") == "failed":
        return (True, False, f"daemon run {run_id} failed")
    return (True, True, f"daemon run {run_id} done")


def _act_offload(task: Task, cfg: "Config", use_git: bool) -> "tuple":
    """非ブロッキング委譲: run が無ければ submit し、結果を1回だけ確認する。終端なら (ok, msg)、
    未終端なら (_Pending(run_id), msg) を返す（待たない）。専用 daemon が run を保持するので、
    gitlab の長期委譲でもループをブロックせず次のタスクへ進める（結果は次パスで回収する）。"""
    base = _kf_base(cfg, use_git) + _workspace_cmd_args(cfg, task) + _reference_cmd_args(cfg, task)
    run_id = _submit_req_id(task, cfg)
    term, ok, msg = _flow_result_once(cfg, use_git, run_id)
    if not term:                                  # 未作成/実行中: 未作成なら submit（作成済みは冪等 no-op）
        prev = _prev_req_id(task, cfg)
        inherit = ["--inherit-from", prev] if prev else []
        try:
            sub = subprocess.run(base + ["--run-id", run_id, "submit", build_request(task, cfg)]
                                 + inherit, cwd=str(cfg.workdir),
                                 timeout=60, capture_output=True, text=True)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return (False, f"submit 失敗: {e}")
        if sub.returncode != 0:
            return (False, f"submit rc={sub.returncode}: {sub.stderr.strip()[:200]}")
        term, ok, msg = _flow_result_once(cfg, use_git, run_id)   # submit 直後に一応もう一度
        if not term:
            return (_Pending(run_id), f"daemon run {run_id} 実行中（offload・非ブロッキング）")
    return (ok, msg)


def _act_submit(task: Task, cfg: "Config", use_git: bool) -> "tuple[bool, str]":
    """daemon があるとき: submit して、その run が終端に達するまで待つ（verify は待機後）。
    req_id は決定的（_submit_req_id）——リブート後の再実行は既存 run に合流する。"""
    base = _kf_base(cfg, use_git) + _workspace_cmd_args(cfg, task) + _reference_cmd_args(cfg, task)
    prev = _prev_req_id(task, cfg)   # リトライ: 先行 run から引き継ぎ＆掃除させる
    inherit = ["--inherit-from", prev] if prev else []
    try:
        sub = subprocess.run(base + ["--run-id", _submit_req_id(task, cfg),
                                     "submit", build_request(task, cfg)] + inherit,
                             cwd=str(cfg.workdir),
                             timeout=60, capture_output=True, text=True)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return (False, f"submit 失敗: {e}")
    if sub.returncode != 0:
        return (False, f"submit rc={sub.returncode}: {sub.stderr.strip()[:200]}")
    out = (sub.stdout or "").strip().splitlines()
    run_id = out[0].strip() if out else ""
    if not run_id:
        return (False, "run-id を取得できません")
    # act_timeout=0（以下）はタイムアウト無効＝終端に達するまで待つ。gitlab 等の長時間委譲
    # （人のレビュー往復で数日かかりうる）で、待ち切れずに retry を空増やしする事故を防ぐ。
    deadline = (time.time() + cfg.act_timeout) if cfg.act_timeout > 0 else None
    while deadline is None or time.time() < deadline:
        try:
            res = subprocess.run(base + ["result", "--run-id", run_id, "--json"],
                                cwd=str(cfg.workdir), timeout=60, capture_output=True, text=True)
            data = json.loads(res.stdout)
            if data.get("done"):
                # done=True は終端（done/failed の両方）を意味する。failed は act 失敗として
                # 扱い（verify=NG 相当で後段が retry/エスカレーション）、success と取り違えない。
                # orchestrator がクラッシュして daemon が failed に確定した場合もここで即検知でき、
                # act_timeout までの永久待機を避けられる。
                if data.get("status") == "failed":
                    return (False, f"daemon run {run_id} failed")
                return (True, f"daemon run {run_id} done")
        except Exception:  # noqa: BLE001 — 取得失敗は次ポーリングで再試行
            pass
        if _task_file_revised(cfg, task):
            # 実行中に人が revise（軌道修正）→ 結果待ちを打ち切り、settle 側の積み直しへ。
            # daemon 側の run は完走しうるが結果は確定させない。次の試行は rev 世代で
            # 新しい req_id になるため、この古い run に合流することもない。
            return (False, f"daemon run {run_id} の結果待ちを中断（人の revise を検知）")
        time.sleep(2.0)
    return (False, f"daemon run {run_id} タイムアウト")


def act_via_kiro_flow(task: Task, cfg: "Config", location: str = "local") -> "tuple[bool, str]":
    """location（local/daemon/remote）に応じて kiro-flow へ委譲する。

      local  → run（単発）
      daemon → ローカル daemon に submit＋結果待ち（daemon が無ければ run にフォールバック）
      remote → git バスの remote daemon に submit＋結果待ち（オフロード。フォールバックしない）
    """
    async_ok = bool(getattr(cfg, "act_async", False))
    if location == "remote":
        return _act_offload(task, cfg, True) if async_ok else _act_submit(task, cfg, use_git=True)
    if location == "daemon":
        if daemon_running(cfg, use_git=False):
            # 非ブロッキング（act_async）: submit して待たず次へ。専用 daemon が run を保持し、
            # 結果は次パスのポーリングで回収する（gitlab 等の長期委譲でループを塞がない）。
            return _act_offload(task, cfg, False) if async_ok else _act_submit(task, cfg, use_git=False)
        return _act_run(task, cfg, use_git=False)  # daemon 不在 → run（同期・待つ）
    return _act_run(task, cfg, use_git=False)


# ---------------------------------------------------------------------------
# 委譲 executor（gitlab 等）のやり直し連携。
#   gitlab executor は「関連 MR が全マージ＝承認 / 一つでも未マージクローズ＝却下」を判定し、
#   却下時は人コメント（無ければ自動判断）を `[gitlab-reject]` 付きで失敗にする。kiro-flow run は
#   failed で非 0 終了し、kiro-project は verify=NG 相当として通常リトライする。その際、却下時の
#   人コメントを次 act の feedback に注入して活かす。
# ---------------------------------------------------------------------------
_REJECT_MARK = "[gitlab-reject]"


def executor_delegates(cfg: "Config") -> bool:
    """この executor が外部（人）へ委譲し、却下→やり直しのコメント連携を要するか。
    組み込み agent/stub はローカル完結＝対象外。"""
    return cfg.executor not in ("agent", "stub")


def read_reject_guidance(cfg: "Config", use_git: bool) -> str:
    """直近 run のノード結果から却下のやり直し指示（人コメント）を取り出す。
    `kiro-flow result --json` を読むだけ（決定的）。まず構造化 data
    （decision=rejected の guidance。gitlab executor が却下例外に載せる）を見て、
    無ければ従来どおり output の `[gitlab-reject]` マーカーから取り出す（後方互換）。
    見つからなければ空（＝自動判断）。"""
    if not executor_delegates(cfg):
        return ""
    cmd = _kf_base(cfg, use_git) + ["result", "--json"]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=60,
                              capture_output=True, text=True)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return ""
    for n in data.get("final_nodes", []):
        d = (n or {}).get("data")
        if isinstance(d, dict) and d.get("decision") == "rejected":
            g = str(d.get("guidance") or "").strip()
            if g:
                return g[:1500]
    for n in data.get("final_nodes", []):
        out = str((n or {}).get("output", ""))
        i = out.find(_REJECT_MARK)
        if i >= 0:
            return out[i + len(_REJECT_MARK):].strip()[:1500]
    return ""


def read_result_notes(cfg: "Config", use_git: bool) -> "list[dict]":
    """直近 run のノード結果 data.notes（gitlab executor が載せる**人コメント**の構造化列）を集める。
    承認/却下いずれの決着でも、人/エージェント判別済みの人コメントだけが載っている（判別は executor 側）。
    重複排除は note_id で行う。kiro-flow result --json を読むだけ（決定的）。"""
    if not executor_delegates(cfg):
        return []
    cmd = _kf_base(cfg, use_git) + ["result", "--json"]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.workdir), timeout=60,
                              capture_output=True, text=True)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return []
    seen, out = set(), []
    for n in data.get("final_nodes", []):
        for note in ((n or {}).get("data") or {}).get("notes", []) if isinstance((n or {}).get("data"), dict) else []:
            if not isinstance(note, dict):
                continue
            nid = note.get("note_id")
            key = nid if nid is not None else str(note.get("body", ""))[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(note)
    return out


def verify_lib_path(cfg: "Config") -> Path:
    """検証済み verify（procedural memory）の格納先。DR を汚さない専用ファイル。"""
    return cfg.decisions / ".verifylib.md"


def save_validated_verify(cfg: "Config", task: "Task") -> None:
    """done 確定した**自動生成 verify**（synth/template/reused）を、タイトル付きで再利用ライブラリへ保存する。
    人が書いた verify は元から良質＝ライブラリ経由を要さない。同一 (title, cmd) は重複保存しない。"""
    if not cfg.learn_capture or not task.verify:
        return
    src = dict(task.extra).get("verify_source", "")
    if src not in ("synth", "template", "reused"):
        return
    line = f"- verifycmd: {task.title.replace(chr(10), ' ')} :: {task.verify.replace(chr(10), ' ')}\n"
    p = verify_lib_path(cfg)
    if p.exists() and line in p.read_text(encoding="utf-8"):
        return
    cfg.decisions.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line)


_VERIFYCMD_RE = re.compile(r"^- verifycmd:\s*(?P<title>.+?)\s*::\s*(?P<guide>.+)$")


def find_learned_verify(cfg: "Config", task: "Task") -> "str | None":
    """検証済み verify ライブラリから、タイトルが十分似た過去の verify コマンドを返す（決定的・Jaccard）。
    毎回ゼロから合成せず、実績のある検査を再利用する（red-green が別途、変更を弁別するか実行で確かめる）。"""
    p = verify_lib_path(cfg)
    if not p.exists():
        return None
    m = _best_learn_match(task, cfg.learn_threshold, [p], label=lambda f: f.stem,
                          pattern=_VERIFYCMD_RE)
    return m[1] if m else None


def capture_approve_learn(cfg: "Config", task: "Task", location: str) -> None:
    """承認決着（done）時、gitlab result の人コメント notes（正例）を横断 learn 化する。
    従来 done では人コメントを還元せず承認時の良い指摘を捨てていた。判別済みの人コメントだけが
    notes に載る（判別は executor 側 _human_notes）。learn_capture off や委譲でない場合は何もしない。"""
    if not (cfg.learn_capture and executor_delegates(cfg)):
        return
    bodies = [str(n.get("body") or "").strip()
              for n in read_result_notes(cfg, location == "remote")]
    guidance = "\n".join(b for b in bodies if b)[:1500]
    if not guidance:
        return
    append_decision(cfg, task.id, "gitlab",
                    context=f"{task.id}（{task.title}）が gitlab で承認",
                    action="gitlab-approve", reason=guidance[:300],
                    affects=f"{task.id} → done",
                    learn=distill_learn(cfg, task.title, guidance))


def _distill_prompt(title: str, guidance: str) -> str:
    return (
        "次は、あるタスクに対して**人間が残したフィードバック/指摘**です。これを、"
        "**類似タスクにも再利用できる一般化した学習ルール**に蒸留してください。\n"
        "規則: ①タスク固有の固有名詞（イシュー番号・特定ファイル名等）は種別・パターンへ引き上げる "
        "②『どういう種類のタスクで/何に気をつけるべきか』を一文で ③一過性の相談・雑談は蒸留対象外"
        "（その場合は空行のみ返す）。\n"
        f"タスク: {title}\nフィードバック: {guidance}\n\n"
        "出力は `<一般化した条件> :: <再利用可能な指針>` の 1 行のみ（説明・コードフェンス不要）。")


def distill_learn(cfg: "Config", title: str, guidance: str, kiro_run=None) -> "tuple[str, str]":
    """人コメント（guidance）を `(条件, 指針)` の一般化ルールへ蒸留する（ltm-use の consolidate 相当）。
    kiro-cli 委譲。失敗・不能・一過性判定は **生 verbatim フォールバック**（劣化しても現状より前進）。
    返り値は append_decision(learn=) にそのまま渡せる (title, guide)。"""
    verbatim = (title, guidance.replace("\n", " ⏎ ").strip()[:400])
    if not cfg.distill_learn:                       # 蒸留 off＝従来どおり生の指摘を learn 化
        return verbatim
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="distill"))
    try:
        out = run(_distill_prompt(title, guidance), cfg.model)
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等
        return verbatim
    for line in (out or "").splitlines():
        line = _strip_code(line.strip())
        if not line or line.startswith("#"):
            continue
        if "::" in line:
            cond, _, guide = line.partition("::")
            cond, guide = cond.strip(), guide.strip()
            if cond and guide:
                return (cond, guide)
        return verbatim                             # 蒸留形式でない最初の行＝失敗扱い
    return verbatim                                 # 空出力（一過性判定含む）＝生で残す


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
# このツールがスキルリポジトリ内に置かれているサブディレクトリ（自動アップデートの参照先）。
# 自動アップデートは update_repo のこのパス以下だけを temp 領域へ sparse-checkout して
# install.sh を実行する（doctor と同じ流儀で、操作は決定的・無関係ファイルは取得しない）。
TOOL_SUBDIR = "tools/kiro-project"
# スキルリポジトリ（git URL/パス）の既定。空なら install.py が生成する skill-registry.json から
# 自動解決する（repositories.origin.url → install_dir）。設定ファイルの update_repo で明示も可。
DEFAULT_UPDATE_REPO = ""
# skill-registry.json を探すエージェントホーム（install.py の AGENT_DIRS に対応）。
_AGENT_HOME_DIRS = (".kiro", ".claude", ".copilot", ".codex")

# 自己更新の再起動先 cwd（main で起動時の cwd を捕捉。「動いていたカレントディレクトリ」へ戻す）。
_START_CWD: "str | None" = None


@dataclass
class Config:
    backlog: Path      # ディレクトリ（案件毎ファイル）
    policy: Path       # ファイル
    decisions: Path    # ディレクトリ（案件毎）
    journal: Path      # ファイル
    needs: Path        # ディレクトリ（案件毎）
    workdir: Path
    bus: Path
    git_bus: "str | None" = None
    git_branch: str = "main"
    git_subdir: "str | None" = None
    # 状態の git 保存・共有（state_git）: ワーク内容（プロジェクトルートの状態）を共有 git リポジトリへ
    # 双方向同期し、リモートの kiro-projects-viewer と結果/指示を往復する。fetch/push は
    # state_git_interval で律速。ルート自体が git クローンなら管理クローンを介さず直接コミット・push
    # する（direct モード。state_git 未設定でも有効）。
    state_git: "str | None" = None        # 共有リポジトリ（URL/パス）。None で無効（direct モードを除く）
    state_git_branch: str = "main"        # 同期先ブランチ
    state_git_subdir: str = "kiro-project"  # リポジトリ内の保存先サブディレクトリ（多重コミッタとの名前空間分離）
    state_git_interval: float = 300.0     # fetch/push の最短間隔（秒）。0 で毎同期（リモート負荷は増える）
    # 実行層 kiro-flow daemon をこのプロジェクト用に kiro-project が起動・監視する（opt-in）。
    manage_flow_daemon: bool = False
    # daemon に --config で渡す共有 kiro-flow.yaml（任意。未指定は kiro-flow の既定発見に委ねる）。
    # kiro-flow の設定値（executor / state_git_subdir / gitlab.* / defer_waits 等）は個別に CLI 注入
    # せず、この設定ファイルに集約して kiro-flow に読ませる（kiro-project 側に kiro-flow 設定を増やさない）。
    # 例外は「バスをどのリポジトリへ鏡写しするか」の routing（--state-git 等）のみで、これは
    # kiro-project の役割なので CLI 注入し続ける。
    flow_config: "str | None" = None
    flow_max_workers: int = 4          # kiro-flow daemon の worker 上限
    status_interval: float = 0.0          # watch アイドル中に status.json の生存信号を更新する間隔（秒）。
                                           # 既定 0=無効（idle 中は追加コミットを一切生まない）。>0 でこの間隔
                                           # ごとに 1 回だけ書き直し、state_git の commit-if-diff に乗る
    lock_dir: "str | None" = None   # kiro-flow daemon ロックの置き場（外部 daemon 発見のため kiro-flow と一致させる）
    kiro_flow: "str | None" = None
    planner: str = "agent"         # 優先順位付け戦略: agent（エージェント委譲）/ none（priority＋古さ）
    flow_planner: str = "flow-planner"  # kiro-flow run に渡す planner
    # ルーティング: タスク → ちょうど1つの書込先ワークスペースを決める自動判断。agent=曖昧時に
    # エージェント委譲で推定（charter owns: と route: の決定論を先に適用）/ none=決定論のみ（推定しない）。
    route_planner: str = "agent"
    default_workspace: str = ""    # route で決まらないタスクの既定ワークスペース（charter の name/url）。空で無効
    location: str = "auto"         # act の実行モード: auto / local / daemon / remote
    executor: str = "agent"
    model: "str | None" = None
    agent_cli: str = "kiro"        # LLM 実行に使うエージェント CLI: kiro / claude / copilot / codex
    agent_timeout: float = 300.0   # エージェント CLI 1 呼び出しのタイムアウト秒（0 以下で無効）
    # バックログ分解の粒度: coarse（ストーリー相当・既定）/ fine（単機能）/ finest（1ファイル/1関数）
    granularity: str = "coarse"
    max_iterations: int = 3
    max_cycles: int = 20
    max_seconds: float = 0.0
    max_tokens: int = 0            # 予算: 消費トークン上限（0=無制限）。act 出力の @cost を計上
    max_cost: float = 0.0          # 予算: 金額(USD)上限（0=無制限）
    max_retries: int = 2
    pace: float = 0.0
    verify_timeout: float = 120.0
    verify_confirm: int = 1         # verify を最大この回数まで再実行し PASS/FAIL が跨いだら flake として人へ隔離（1=従来）
    verify_cwd: "str | None" = None  # verify/acceptance を実行する作業ディレクトリ（既定 workdir）。git-bus 等で
                                     # workdir に成果が無いとき、対象 repo のクローン先を指す。未指定かつ charter に
                                     # 単一 repo があれば acceptance はその repo を一時 clone して実行する。
    require_progress: bool = False  # verify=PASS でも act が baseline 以降に変更を生んでなければ done せず人へ（履歴一致 verify の偽 done 対策）
    auto_level: bool = False         # 実績連動の自動昇格（track 毎に手戻り率で level を上げ下げ）。既定 off
    auto_level_max: str = "assisted" # 自動昇格の ceiling。既定 assisted（unattended への自動到達は明示時のみ）
    level_promote_after: int = 5     # 昇格に要する連続 clean 完了数
    level_window: int = 10           # 手戻り率の評価窓（直近 N 件の完了）
    level_rework_max: float = 0.0    # 昇格を許す最大 rework_rate（既定 0＝手戻りゼロ）
    act_timeout: float = 1800.0
    # 非ブロッキング委譲: daemon/remote への submit で結果を待たず次のタスクへ進み、offloaded にして
    # 次パスでポーリングして回収する。gitlab 等の長期委譲でループを塞がない（専用 daemon が run を保持）。
    act_async: bool = False
    notify_cmd: "str | None" = None
    actor: str = "user"
    archive: "Path | None" = None   # done の退避先ディレクトリ（既定 archive/）
    do_archive: bool = True         # done を archive/ へ退避（False なら削除）
    learn: bool = True              # DR 学習: 過去の人の判断から類似案件を自動解決
    learn_capture: bool = True      # 人の判断（approve 理由・hold 理由・gitlab 却下コメント）から learn/avoid を蓄積
    distill_learn: bool = True      # 人コメントを一般化ルールへ蒸留してから learn 化（off で生の指摘をそのまま残す）
    verify_validate: str = "synth"  # red-green 検証: 合成 verify が act 前でも PASS（=変更を弁別しない偽 done）を弾く。
                                    # off=無効 / synth=自動生成(synth/template)のみ / all=常時。per-task `- verify_validate: none` で除外
    reject_recur: int = 2           # 同種の gitlab 却下がこの回数に達したら、silent 積み直しをやめ「系の再考」で人へ（0/負で無効）
    intake_recall: bool = True      # 投入/triage 時に過去の hold 判断（avoid）と照合し類似は inbox（人へ）へ寄せる
    learn_threshold: float = 0.5    # タイトル類似度（Jaccard）のしきい値
    auto_adjudicate: bool = True    # needs に落とす前に kiro-cli が積み直し可否を裁定（既定 on）
    adjudicate_max: int = 1         # 1タスクあたりの自律裁定の上限回数（有限停止のため）
    max_spawn: int = 20             # 1 run で生成できる派生タスク数の上限（0 で生成無効。暴走防止）
    regression_cmd: "str | None" = None  # done 確定前に走らせるグローバル回帰検査（巻き込み事故の検知）
    regression_revert: bool = False      # 回帰時に作業ツリーの未コミット変更を巻き戻す（既定 off）
    intake_cmd: "str | None" = None      # 外部の決定的ゲート/検出器から修復タスクを汲み上げる取り込みコマンド
    intake_interval: float = 600.0       # intake_cmd の実行間隔（秒）。0 以下なら毎回（パス開始/idle poll 毎）
    ltm: bool = False               # ltm-use 長期記憶への昇格＋横断 recall（既定 off: home へ書くため明示）
    ltm_home: "Path | None" = None  # ltm-use ストアのルート（既定 KIRO_LTM_HOME→~/.claude）
    promote_threshold: int = 2      # learn ルールがこの回数以上効いたら昇格
    rot: bool = False               # rot 検知（古い/重複/実行不能を triage で掃除）
    rot_age_days: float = 14.0      # stale とみなす経過日数
    cleanup: bool = True            # run 後に kiro-flow バスの一時状態を掃除
    bus_keep_runs: int = 20         # 掃除しても残す直近 run 数（viewer のフロータブが読む一次ソース）
    delivery: "Path | None" = None  # 納品一覧（受領書）DELIVERY.md
    inbox: "Path | None" = None     # 取り込み待ちのドロップ口（外部ソースがここへファイルを置く）
    debounce: float = 3.0           # watch 中、最終保存からこの秒数は feedback 取込を待つ
    watch: bool = False     # 終了条件後もプロセスを残し backlog を監視
    poll: float = 5.0       # watch のポーリング間隔（秒）
    concurrency: int = 1    # 1サイクルで daemon/remote へ並行 submit する独立タスク数（1=逐次）
    level: str = "unattended"  # 自律度: report(実行せず計画報告) / assisted(実行するが done は人が承認) / unattended(現行)
    # 実行前レビュー（plan review・既定 on）: 新規タスクは proposed で入り、人の承認で ready になる。
    plan_review: bool = True
    # 投入時アセスメント（既定 on）: 新規タスクを c=複雑さ / r=リスク / a=曖昧さ（各1-3）で採点し
    # `- assess:` に記録する。採点は情報であり実行可否を変えない（読むのは plan-review 票・
    # リスクダイジェスト・spec ルーティング）。知能は委譲・stub/失敗時は決定的ヒューリスティック。
    assess: bool = True
    # spec ルーティング（既定 off）: 採点 max(c,r,a) が spec_threshold に達したタスクに spec 前段
    # タスク（specs/<id>/ の spec.md/design.md/tasks.md 作成・人の承認で実装タスクへ展開）を前置する。
    spec_track: bool = False
    spec_threshold: int = 3
    # リポジトリ理解の成果物化（既定 off）: plan の直前に charter の書込先 repo ごとに
    # context/<repo名>.md を生成（HEAD sha キャッシュ・変化時のみ再生成）。読み出しは常時
    # （人が手書きした context/*.md も plan / act / verify 合成へ有界注入される）。
    repo_map: bool = False
    # プロジェクトルールの自動昇格（既定 on）: 効果が再現した learn（auto-resolve が
    # promote_threshold 回以上）を rules.md（全タスク常時注入層）へ決定的に追記する。
    # rules.md の読み出し・注入自体は設定に依らず常時（人が書けば必ず効く）。
    rules_capture: bool = True
    # 処理毎のエージェント上書き（設定ファイル専用・CLI フラグ無し）。キーは AGENT_PURPOSES
    # （plan/review/prioritize/route/adjudicate/verify/distill/assess/repo_map/doctor）、
    # 値は {agent_cli, model}。未指定の処理はグローバル agent_cli / model を使う。
    agents: dict = field(default_factory=dict)
    # タスク単位ターゲットブランチ（既定 on）: 成果を kp/<task-id> に集約する（リトライも同一ブランチ）。
    task_branch: bool = True
    task_branch_prefix: str = "kp/"
    # 成果物レビュー（既定 on）: verify PASS 後、level に依らず常に review（検収待ち）へ。
    # 人の承認で done 確定（GitLab 設定があれば MR を自動マージ規則で決着）。false で従来の自動 done。
    delivery_review: bool = True
    throttle: float = 0.0   # ソフト予算比率(0=off)。max_tokens/max_cost のこの割合で run を打ち切り watch は report 降格
    runlog: "Path | None" = None    # 構造化 run-log（JSONL・run 毎に1行追記）。既定 <root>/run-log.jsonl
    registry: "list" = field(default_factory=list)  # 共有レジストリ（別ホスト発見用。NFS/同期/git バス）
    dry_run: bool = False
    once: bool = False
    project_name: str = ""               # プロジェクト名（ルートのディレクトリ名。milestone id の一次ソース）
    # プロジェクト層（charter 駆動の plan→execute→evaluate ループ）。`project` サブコマンドでのみ使う。
    charter: "Path | None" = None        # 人が書く目標/制約/前提/成果物/acceptance（既定 <root>/charter.md）
    review_project: bool = False         # evaluate で敵対的レビューを上乗せ（opt-in・知能は委譲）
    max_project_cycles: int = 5          # 改善サイクルの上限（有限停止）
    max_project_cost: float = 0.0        # プロジェクト累計コスト上限(USD・0=無制限)
    project_stall: int = 2               # acceptance PASS 数が増えない連続回数の上限→人へ
    with_flow: bool = False              # doctor: 実行層 kiro-flow doctor も連携実行し findings を統合（CLI 既定 on）
    # 自動アップデート（既定 on）。更新元は skill-registry.json から自動解決。watch のアイドル時に
    # git ls-remote で main の先頭を確認し、適用済みと違えば temp 領域へ sparse-checkout
    # （tools/kiro-project/ だけ）→ install.sh 実行 → graceful 再起動する。起動直後にも 1 回実施。
    update_enabled: bool = True          # 自動アップデートの ON/OFF（false で完全無効・既定 on）
    update_check_interval: float = 21600.0  # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    update_repo: "str | None" = None     # スキルリポジトリ（git URL/パス）。空/None なら registry から自動解決
    update_branch: str = "main"          # 追従するブランチ
    update_subdir: str = TOOL_SUBDIR     # リポジトリ内のこのツールのサブディレクトリ
    update_installer: str = "install.sh"  # サブディレクトリ内で実行するインストーラ

    def archive_dir(self) -> Path:
        return self.archive or (self.backlog.parent / "archive")

    def cohorts_dir(self) -> Path:
        return self.backlog.parent / "cohorts"

    def __post_init__(self):
        if self.delivery is None:
            self.delivery = self.backlog.parent / "DELIVERY.md"
        if self.runlog is None:
            self.runlog = self.backlog.parent / "run-log.jsonl"
        if self.charter is None:
            self.charter = self.backlog.parent / "charter.md"


def ensure_dirs(cfg: Config) -> None:
    for d in (cfg.backlog, cfg.needs, cfg.decisions):
        d.mkdir(parents=True, exist_ok=True)
    if cfg.inbox:                       # 外部ソースが投入先を見つけられるよう作っておく
        cfg.inbox.mkdir(parents=True, exist_ok=True)
    commands_dir(cfg).mkdir(parents=True, exist_ok=True)  # 指示ドロップ口も同様に作っておく
    cfg.journal.parent.mkdir(parents=True, exist_ok=True)


def extract_delivery_ref(act_msg: str, cfg: Config,
                         baseline: "tuple[str, frozenset] | None" = None) -> str:
    """成果物の参照を得る。act 出力の PR URL / commit SHA を優先。
    baseline（act 前スナップショット）が渡されたら **baseline 以降の新規コミット/未コミット変更のみ**を
    成果物とみなし、変化が無ければ `(変更なし)` を返す（既存コミットを成果物と偽らない＝偽 done の可視化）。
    baseline=None のときは従来どおり `git log -1`（後方互換）。"""
    m = re.search(r"https?://\S+/(?:pull|merge_requests)/\d+", act_msg or "")
    if m:
        return m.group(0)
    m = re.search(r"\b[0-9a-f]{7,40}\b", act_msg or "")
    if m:
        return f"commit {m.group(0)}"
    if baseline is not None:
        head0, _ = baseline
        head1 = _git_out(cfg.workdir, "rev-parse", "HEAD").strip()
        if head1 and head1 != head0:                      # baseline 以降の新規コミット
            line = _git_out(cfg.workdir, "log", "-1", "--format=%h %s").strip()
            return f"git: {line}" if line else f"commit {head1[:8]}"
        if meaningful_changes(cfg, baseline):             # 未コミットの作業ツリー変更（kiro 状態は除外）
            return "git: 未コミットの変更あり"
        return "(変更なし)"                               # ← 既存コミットを成果物として報告しない
    try:
        r = subprocess.run(["git", "-C", str(cfg.workdir), "log", "-1", "--format=%h %s"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return f"git: {r.stdout.strip()}"
    except Exception:  # noqa: BLE001
        pass
    return "(参照なし)"


def _current_branch(cfg: "Config") -> str:
    """作業ツリーの現在ブランチ（git でなければ空）。成果物の所在をブランチ単位で示すのに使う。"""
    if not (cfg.workdir / ".git").exists():
        return ""
    return _git_out(cfg.workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()


def delivery_evidence(cfg: "Config", act_msg: str, git_base, location: str = "local",
                      verify: "str | None" = None, vmsg: str = "", ok: "bool | None" = None,
                      max_files: int = 12) -> str:
    """人が「成果物がどこにあり・何が差分で・検証はどうだったか」を判断できる材料を作る。
    needs（判断待ち）と DELIVERY/archive（受領）双方の説明欄に使う。git でなければ ref/差分は空。"""
    ref = extract_delivery_ref(act_msg, cfg, git_base)
    branch = _current_branch(cfg)
    changed = sorted(meaningful_changes(cfg, git_base)) if git_base is not None else []
    where = str(cfg.workdir)
    if location == "remote" and cfg.git_bus:
        where += f"（git-bus: {cfg.git_bus}@{cfg.git_branch}）"
    lines = [f"- 成果物: {ref}",
             f"- 所在: {where}" + (f" / ブランチ {branch}" if branch else ""),
             f"- 実行先: {location}"]
    if changed:
        shown = changed[:max_files]
        lines.append(f"- 差分: {len(changed)} ファイル")
        lines += [f"    - {p}" for p in shown]
        if len(changed) > len(shown):
            lines.append(f"    - …他 {len(changed) - len(shown)} 件")
    elif git_base is not None:
        lines.append("- 差分: baseline 以降の変更なし")
    if verify is not None:
        res = "PASS" if ok else ("FAIL" if ok is not None else "?")
        vm = (vmsg or "").replace("\n", " ").strip()[:200]
        lines.append(f"- 検証: `{verify}` → {res}" + (f"（{vm}）" if vm else ""))
    return "\n".join(lines)


_COST_RE = re.compile(r"@cost\b(?P<rest>.*)")


def parse_cost(act_msg: str) -> "tuple[int, float]":
    """act 出力からコストを計上する。エージェントが `@cost tokens=1234 usd=0.05` 形式の行を吐けば
    それを合算（1タスクで複数回呼ぶこともあるので加算）。マーカが無ければ (0, 0.0)。決定的・LLM 不要。"""
    tokens, usd = 0, 0.0
    for line in (act_msg or "").splitlines():
        m = _COST_RE.search(line)
        if not m:
            continue
        rest = m.group("rest")
        tm = re.search(r"tokens?\s*[=:]\s*([\d_]+)", rest)
        um = re.search(r"(?:usd|cost)\s*[=:]\s*([\d.]+)", rest)
        if tm:
            tokens += int(tm.group(1).replace("_", ""))
        if um:
            usd += float(um.group(1))
    return tokens, usd


def append_delivery(cfg: Config, task: Task, ref: str, ts: str, branch: str = "") -> None:
    """納品一覧（受領書）DELIVERY.md に1行追記する。成果参照はブランチも併記して所在を明確にする。"""
    path = cfg.delivery
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() else (
        "# 納品一覧（受領書）\n\n| id | タイトル | 検収 | 成果参照 | 完了 |\n|---|---|---|---|---|\n")
    title = task.title.replace("|", "\\|")
    # 実成果物があるときだけブランチを併記する（"(変更なし)"/"(参照なし)" 等のセンチネルには付けない）
    show_branch = branch and not ref.startswith("(")
    cell = (f"{ref} @ {branch}" if show_branch else ref).replace("|", "\\|").replace("\n", " ")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{header}| {task.id} | {title} | PASS | {cell} | {ts} |\n")


def archive_task(cfg: Config, task: Task, vmsg: str, ref: str, ts: str, evidence: str = "") -> None:
    """done タスクを archive/<id>.md へ退避し、検収用の『納品書』を付す（backlog と1:1）。
    evidence（成果物の所在・差分・検証）を載せ、後から「どこに何が入ったか」を辿れるようにする。"""
    cfg.archive_dir().mkdir(parents=True, exist_ok=True)
    task.extra.append(("archived", ts))
    body = serialize_task(task) + (
        f"\n## 納品書\n"
        f"- 完了 : {ts}\n"
        f"- verify: `{task.verify}` → PASS（{vmsg}）\n"
        f"- 成果 : {ref}\n"
    )
    if evidence:
        body += f"\n## 判断材料（成果物の所在・差分・検証）\n{evidence}\n"
    _archive_write(cfg, task.id, body)
    delete_task_file(cfg, task)


def _archive_write(cfg: "Config", tid: str, body: str) -> None:
    """archive/<id>.md へ書く。既存（過去の同 id の退避）があれば上書きせず -2, -3… で退避する
    （明示 id の再投入や複数 charter の同名タスクで過去の記録を失わない）。"""
    adir = cfg.archive_dir()
    adir.mkdir(parents=True, exist_ok=True)
    dest = adir / f"{tid}.md"
    n = 2
    while dest.exists():
        dest = adir / f"{tid}-{n}.md"
        n += 1
    dest.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# 正準ループ（run）
# ---------------------------------------------------------------------------
def summarize(tasks: "list[Task]") -> "dict[str, int]":
    c = {s: 0 for s in VALID_STATUS}
    for t in tasks:
        c[t.norm_status()] = c.get(t.norm_status(), 0) + 1
    return c


# journal のローテーション閾値（バイト。0 以下で無効）とアーカイブ保持世代数（0 以下で無制限）。
# build_config が設定 journal_max_bytes / journal_keep をここへ確定する（_AGENT_CLI と同じ流儀）。
_JOURNAL_MAX_BYTES: int = 262144
_JOURNAL_KEEP: int = 20


def _journal_lock_path(path: Path) -> str:
    h = hashlib.sha1(str(path).encode()).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), f"kiro-project-journal-{h}.lock")


def rotate_journal(path: Path, max_bytes: "int | None" = None,
                   keep: "int | None" = None) -> "Path | None":
    """journal が閾値を超えていたら journal-archive/ へ退避し、新しい journal を始める。
    退避名はタイムスタンプ＋ホスト名で一意（複数ホストの direct 同期でも rename が衝突しない・
    退避ファイルは以後不変＝マージ衝突源にならない）。保持世代を超えた古いアーカイブは削除する。
    ローテーションしたら退避先を返す（しなければ None）。呼び出し側でロックを取ること。"""
    mx = _JOURNAL_MAX_BYTES if max_bytes is None else max_bytes
    if mx <= 0:
        return None
    try:
        if not path.is_file() or path.stat().st_size < mx:
            return None
    except OSError:
        return None
    arch_dir = path.parent / "journal-archive"
    try:
        arch_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        host = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname())[:24] or "host"
        dest = arch_dir / f"{path.stem}-{ts}-{host}{path.suffix}"
        n = 1
        while dest.exists():
            dest = arch_dir / f"{path.stem}-{ts}-{host}.{n}{path.suffix}"
            n += 1
        path.replace(dest)                 # 同一ファイルシステム内の原子的 rename
    except OSError:
        return None
    keep_n = _JOURNAL_KEEP if keep is None else keep
    if keep_n > 0:
        try:
            arch = sorted(p for p in arch_dir.iterdir()
                          if p.is_file() and p.name.startswith(path.stem + "-"))
            for old in arch[:-keep_n]:
                old.unlink()
        except OSError:
            pass
    return dest


def append_journal(path: Path, line: str) -> None:
    ts = _now_ts()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 多重プロセス（daemon・外部 CLI・別 watch）の追記とローテーションをホスト内で直列化する
    with _file_lock(_journal_lock_path(path)):
        rotated = rotate_journal(path)
        with path.open("a", encoding="utf-8") as f:
            if rotated is not None:
                f.write(f"- {ts} journal をローテーション（→ journal-archive/{rotated.name}）\n")
            f.write(f"- {ts} {line}\n")


def append_runlog(path: "Path | None", record: dict) -> None:
    """構造化 run-log（JSONL）に1行追記。run 毎の機械可読な観測ログ（journal は人間可読、これは集計用）。"""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _block(cfg, task, reason, reasons, evidence: str = ""):
    task.status = "blocked"
    reasons[task.id] = reason
    persist_task(cfg, task)
    write_needs_file(cfg, task, reason, evidence=evidence)
    release_claim(cfg, task)              # blocked は doing でなくなる＝実行権（claim）を解放（人手 hold 含む）


def _revert_workdir(cfg) -> None:
    """回帰時の best-effort 巻き戻し: 追跡ファイルを HEAD に戻し未追跡を消す。
    **コミット済み/ push 済みの変更は対象外**（未コミットの作業ツリー変更のみ）。"""
    if not (cfg.workdir / ".git").exists():
        return
    for cmd in (["git", "-C", str(cfg.workdir), "checkout", "--", "."],
                ["git", "-C", str(cfg.workdir), "clean", "-fd"]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def _escalate(cfg, task, reason, reasons, cycle, evidence: str = ""):
    """ループ内で人の判断(needs)へ回す直前のフック。auto_adjudicate が有効なら、人へ送る前に
    kiro-cli へ『自律的に積み直して解けるか』を諮り、可能なら needs を作らず ready に戻して回し続ける。
    verify を持たないタスク（acceptance 未定義）は対象外＝必ず人へ。adjudicate_max で有限回に制限。"""
    if cfg.auto_adjudicate and not cfg.dry_run and task.verify:
        done_n = int(task.get("adjudicated", "0") or "0")
        if done_n < cfg.adjudicate_max:
            decision, guide = adjudicate_escalation(cfg, task, reason)
            if decision == "requeue":
                task.drop("feedback", "adjudicated")
                if guide:
                    task.extra.append(("feedback", guide.replace("\n", " ⏎ ")))
                task.extra.append(("adjudicated", str(done_n + 1)))
                task.status = "ready"
                persist_task(cfg, task)
                append_decision(cfg, task.id, "auto",
                                context=f"{task.id}（{task.title}）を人の判断前に自律裁定",
                                action="auto-adjudicate",
                                reason=(f"kiro-cli: requeue — {guide[:120]}" if guide
                                        else "kiro-cli: requeue"),
                                affects=f"{task.id} → ready")
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} 自律裁定で積み直し"
                                            f"（人の判断を回避 {done_n + 1}/{cfg.adjudicate_max}）")
                return
    _block(cfg, task, reason, reasons, evidence=evidence)


# ---------------------------------------------------------------------------
# 並列消費（§11）— kiro-flow の worker 並列へ寄せる。
#   prioritize が返す order は依存(after)解決済み＝互いに独立。daemon/remote へ submit する
#   タスクは実行が daemon 側の隔離ワーカで走るので、最大 concurrency 個まで並行 submit して
#   一括で待つ。verify と done/archive/decisions/派生など「ローカル状態の変更」は逐次のまま
#   （workdir/決定記録の競合を避け、不変条件をそのまま守る）。local act は逐次（並列化しない）。
# ---------------------------------------------------------------------------
def _submit_bound(location: str, cfg: "Config") -> bool:
    """その location が daemon/remote への submit（=隔離ワーカ実行）になるか。local 実行なら False。"""
    if location == "remote":
        return True
    if location == "daemon":
        return daemon_running(cfg, use_git=False)
    return False


def _select_batch(order: "list[Task]", cfg: "Config", policy, remaining: int) -> "list[Task]":
    """先頭から、並行 submit 可能（daemon/remote）なタスクを最大 width 個まとめる。
    先頭が local 実行なら従来どおり1件だけ（逐次）。残サイクル予算 remaining も超えない。"""
    width = cfg.concurrency if (cfg.concurrency > 1 and not cfg.once) else 1
    width = max(1, min(width, remaining))
    first_loc = decide_location(order[0], policy, cfg)
    if width == 1 or not _submit_bound(first_loc, cfg):
        return [order[0]]
    batch = []
    for t in order:
        if len(batch) >= width:
            break
        if not _submit_bound(decide_location(t, policy, cfg), cfg):
            break                      # local 実行が混ざったらそこで切る（逐次に落とす）
        batch.append(t)
    return batch or [order[0]]


# --- 原子的クレーム: 同一 backlog を複数 worker/インスタンスが回しても二重実行しないための claim。---
#   <root>/claims/<id>.lock を O_CREAT|O_EXCL で作れた者だけが実行権を持つ。owner 失踪時のため TTL で奪取可。
def _claims_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "claims"


def _claim_ttl(cfg: "Config") -> float:
    # act_timeout=0（無制限待ち）なら claim も期限なし＝長時間委譲中に他インスタンスへ
    # 奪われて二重実行するのを防ぐ（owner が生きている限り握り続ける）。
    if cfg.act_timeout <= 0:
        return float("inf")
    return cfg.act_timeout + cfg.verify_timeout + 60.0   # act+verify を十分に上回る猶予（失踪検知用）


def claim_task(cfg: "Config", task: "Task") -> bool:
    """task の実行権を原子的に取得できれば True。既に新鮮なクレームがあれば False（他者が実行中）。"""
    d = _claims_dir(cfg)
    p = d / f"{task.id}.lock"
    rec = json.dumps({"host": socket.gethostname(), "pid": os.getpid(),
                      "ts": time.time(), "id": task.id}).encode("utf-8")
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if time.time() - float(old.get("ts", 0) or 0) <= _claim_ttl(cfg):
            return False                      # 新鮮なクレーム＝他者が実行中
        try:                                  # stale（owner 失踪）＝奪取を試みる
            p.unlink()
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except (FileExistsError, OSError):
            return False                      # 競合で他者が先取り
    except OSError:
        return True                           # claim 不能な環境（FS 制約等）は従来どおり通す
    try:
        os.write(fd, rec)
    finally:
        os.close(fd)
    # クレーム後の再検証: 別インスタンスが既に消化（archive/削除）や状態変更をしていないか。
    # （ロック取得は「同時実行」を防ぐが、こちらの in-memory ビューが古い場合に二重実行を防ぐ）
    live = _load_task_file(cfg, task.id)
    # offloaded（非ブロッキング委譲・結果待ち）は reap が doing へ確定させる正当な遷移なので claim を許す。
    if live is None or (live.norm_status() not in CONSUMABLE and live.norm_status() != "offloaded"):
        release_claim(cfg, task)              # 既に done/review/blocked 等 → 実行しない
        return False
    # 実行直前のディスク内容を採用する（in-memory がパス開始時点で止まっていても、
    # 人の revise・直接編集をこの試行に反映し、doing 永続化で上書き消失させない）。
    live.drop("revised")                      # これから走る試行は最新内容を含む＝マーカー消化
    _adopt_task(task, live)
    return True


def release_claim(cfg: "Config", task: "Task") -> None:
    """実行権を解放する（done/review/blocked/積み直しのいずれでも、doing でなくなったら呼ぶ）。"""
    try:
        (_claims_dir(cfg) / f"{task.id}.lock").unlink()
    except OSError:
        pass


def _act_batch(batch: "list[Task]", cfg: "Config", act, policy) -> "dict[str, tuple[str, str]]":
    """batch のうち**クレームできたタスクだけ** doing にして act（2件以上は ThreadPool で並行）。
    返り値のキーはクレーム成功＝実際に実行したタスクのみ（取れなかったものは含めない）。"""
    claimed = [t for t in batch if claim_task(cfg, t)]   # 二重実行防止: 取れた者だけ進む
    for t in claimed:
        t.status = "doing"
        resolve_and_persist_workspace(cfg, t, policy)    # タスク→1つの書込先へルーティング（決定を md へ永続化）
        persist_task(cfg, t)
    locs = {t.id: decide_location(t, policy, cfg) for t in claimed}
    if cfg.dry_run:
        return {t.id: (locs[t.id], None, "(dry-run)") for t in claimed}
    if not claimed:
        return {}

    def _one(t):
        # act は (bool|_Pending, msg)。_Pending は「非ブロッキング submit 済み・未終端」＝offload。
        status, msg = act(t, cfg, locs[t.id])
        return (locs[t.id], status if isinstance(status, _Pending) else None, msg)

    if len(claimed) == 1:
        return {claimed[0].id: _one(claimed[0])}
    results: "dict[str, tuple]" = {}
    with ThreadPoolExecutor(max_workers=len(claimed)) as ex:
        futs = {ex.submit(_one, t): t for t in claimed}
        for fut, t in futs.items():
            try:
                results[t.id] = fut.result()
            except Exception as e:     # noqa: BLE001 — act 失敗は verify=NG 相当として後段で扱う
                results[t.id] = (locs[t.id], None, f"act 失敗: {e}")
    return results



# ---------------------------------------------------------------------------
# タスク MR（成果物レビュー）— kp/<task-id> → target の MR を作り、承認で自動決着する。
#   GitLab REST v4 を stdlib で直叩きする最小クライアント（kiro-flow executors/gitlab.py の
#   トークン解決・URL 解釈・承認規則の縮小版）。GitLab に到達できなければすべて無害にスキップし、
#   従来どおり「記録のみ」で動く（done の確定は MR に依存させるが、未設定なら従来のまま）。
# ---------------------------------------------------------------------------
_GL_TOKEN_ENVS = ("GITLAB_TOKEN", "GL_TOKEN")
_GL_RC_FILES = ("~/.bashrc", "~/.bash_profile", "~/.profile", "~/.zshrc")


def _gl_token() -> str:
    for k in _GL_TOKEN_ENVS:
        v = os.environ.get(k, "").strip()
        if v:
            return v
    pat = re.compile(r"^\s*(?:export\s+)?(?:GITLAB_TOKEN|GL_TOKEN)=[\"\']?([^\"\'\s]+)")
    for rc in _GL_RC_FILES:
        try:
            for line in Path(rc).expanduser().read_text(encoding="utf-8",
                                                        errors="ignore").splitlines():
                m = pat.match(line)
                if m:
                    return m.group(1)
        except OSError:
            continue
    return ""


def _gl_parse_repo(url: str) -> "tuple[str, str] | None":
    """リポジトリ URL → (host, project_path)。https 形と ssh 形（git@host:group/repo.git）を解釈。"""
    u = (url or "").strip()
    m = re.match(r"^https?://([^/]+)/(.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", u)
    if m:
        return m.group(1), m.group(2)
    return None


def _gl_api(host: str, token: str, method: str, path: str,
            data: "dict | None" = None, params: "dict | None" = None):
    import urllib.error
    import urllib.parse
    import urllib.request
    url = f"https://{host}/api/v4{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"PRIVATE-TOKEN": token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitLab API {method} {path} 失敗: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitLab API {method} {path} へ接続できません: {e.reason}")


def _gl_quote(project: str) -> str:
    import urllib.parse
    return urllib.parse.quote(project, safe="")


def _task_mr_coords(task: "Task") -> "tuple[str, str, str] | None":
    """タスクに記録済みの MR 座標 (host, project, iid)。無ければ None。"""
    iid = str(task.get("mr_iid") or "").strip()
    pj = str(task.get("mr_project") or "")
    if not iid or "|" not in pj:
        return None
    host, proj = pj.split("|", 1)
    return host, proj, iid


def ensure_task_mr(cfg: "Config", task: "Task") -> str:
    """review 到達時に kp/<task-id> → target の MR を用意する（冪等）。
    GitLab 未設定・非 GitLab リポジトリ・API 失敗は ""（記録のみで続行＝done の確定は従来どおり）。"""
    if not getattr(cfg, "task_branch", False):
        return ""
    if task.get("mr_url"):
        return str(task.get("mr_url"))
    spec = _workspace_spec_for(cfg, task)
    if not spec or not spec.get("url"):
        return ""
    parsed = _gl_parse_repo(spec["url"])
    token = _gl_token()
    if not parsed or not token:
        return ""
    host, proj = parsed
    source = task_branch_name(cfg, task)
    target = spec.get("target") or spec.get("base") or "main"
    try:
        ep = _gl_quote(proj)
        found = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests",
                        params={"source_branch": source, "state": "opened"})
        mr = found[0] if isinstance(found, list) and found else None
        if mr is None:
            mr = _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests",
                         data={"source_branch": source, "target_branch": target,
                               "title": f"[kiro-project] {task.id}: {task.title[:80]}",
                               "description": f"kiro-project タスク {task.id} の成果物"
                                              f"（ブランチ {source}。承認でクリーンなら自動マージ）",
                               "remove_source_branch": True})
        task.drop("mr_url", "mr_iid", "mr_project")
        task.extra += [("mr_url", str(mr.get("web_url") or "")),
                       ("mr_iid", str(mr.get("iid") or "")),
                       ("mr_project", f"{host}|{proj}")]
        append_journal(cfg.journal, f"タスク MR 用意: {task.id} → {mr.get('web_url', '')}")
        return str(mr.get("web_url") or "")
    except RuntimeError as e:
        append_journal(cfg.journal, f"タスク MR の用意に失敗（記録のみで続行）: {task.id}: {e}")
        return ""


def finalize_task_mr(cfg: "Config", task: "Task") -> "tuple[bool, str]":
    """approve（検収承認）時にタスク MR を Stage 2（gitlab executor）と同一規則で自動決着する:
    クリーン（コンフリクト無し・未解決ディスカッション無し）→ マージ（ソースブランチ削除）、
    差分なし → クローズ、未クリーン → 差し戻しコメントを付けて (False, 理由)（done にしない）。
    MR 無し・GitLab 未設定は (True, "")＝従来どおり done 確定のみ。"""
    coords = _task_mr_coords(task)
    if coords is None:
        return True, ""
    token = _gl_token()
    if not token:
        return True, "GitLab トークン無し（MR は手動で決着してください）"
    host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        mr = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests/{iid}")
        state = str(mr.get("state") or "")
        if state in ("merged", "closed"):
            return True, f"MR は決着済み（{state}）"
        problems = []
        discussions = _gl_api(host, token, "GET",
                              f"/projects/{ep}/merge_requests/{iid}/discussions",
                              params={"per_page": 100})
        unresolved = sum(1 for d in (discussions if isinstance(discussions, list) else [])
                         if any(n.get("resolvable") and not n.get("resolved")
                                for n in (d.get("notes") or [])))
        changes = _gl_api(host, token, "GET", f"/projects/{ep}/merge_requests/{iid}/changes")
        no_diff = isinstance(changes.get("changes"), list) and not changes["changes"]
        conflicts = bool(mr.get("has_conflicts")) or \
            str(mr.get("merge_status") or "") == "cannot_be_merged"
        if unresolved:
            problems.append(f"未解決のレビューコメントが {unresolved} 件")
        if conflicts and not no_diff:
            problems.append(f"コンフリクト（merge_status={mr.get('merge_status')}）")
        if problems:
            why = "; ".join(problems)
            _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                    data={"body": f"kiro-project: # 差し戻し（自動チェック）\n- {why}\n"
                                  "解消後に再度 approve してください。"})
            return False, why
        if no_diff:                              # 差分なし＝マージするものが無い → クローズで決着
            _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                    data={"state_event": "close"})
            return True, "差分なし MR＝クローズで決着"
        _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}/merge",
                data={"should_remove_source_branch": True})
        return True, "MR を自動マージ"
    except RuntimeError as e:
        return False, f"MR の決着に失敗（解消/再試行してください）: {e}"


def close_task_mr(cfg: "Config", task: "Task", reason: str) -> None:
    """却下（reject）時: タスク MR をクローズしソースブランチを削除する（best-effort・
    gitlab-review-viewer の却下と同じ規則）。GitLab 未設定なら何もしない。"""
    coords = _task_mr_coords(task)
    token = _gl_token()
    if coords is None or not token:
        return
    host, proj, iid = coords
    ep = _gl_quote(proj)
    try:
        _gl_api(host, token, "POST", f"/projects/{ep}/merge_requests/{iid}/notes",
                data={"body": f"kiro-project: タスク {task.id} は却下されました（{reason}）。"})
        _gl_api(host, token, "PUT", f"/projects/{ep}/merge_requests/{iid}",
                data={"state_event": "close"})
        branch = task_branch_name(cfg, task)
        _gl_api(host, token, "DELETE",
                f"/projects/{ep}/repository/branches/{_gl_quote(branch)}")
    except RuntimeError as e:
        append_journal(cfg.journal, f"却下 MR の後始末に失敗（無視）: {task.id}: {e}")


def risk_digest(cfg: "Config", task: "Task", changed: "set[str]", protect_hits: list,
                dtok: int = 0, dusd: float = 0.0) -> "tuple[str, str]":
    """承認（review）前のリスクダイジェスト。決定的な材料だけで組み立てる（LLM 不使用・
    gitlab-gatekeeper の「人が 1 枚で決める判断パケット」の薄い移植）。返り値 (level, markdown)。
    level は high > med > low: protect 接触・avoid 類似＝high、リトライ経験・大きな差分・
    自動合成 verify・採点 r=3＝med、どれも無ければ low。承認フロー自体は変えない（情報が増えるだけ）。"""
    lines: "list[str]" = []
    high = med = False
    if protect_hits:
        paths = ", ".join(p for p, _ in protect_hits)
        lines.append(f"- 保護パス接触: {paths[:200]}")
        high = True
    avoided = find_avoidance(cfg, task)
    if avoided:
        src, why = avoided
        lines.append(f"- 過去の回避判断（avoid）に類似: {src} — {why[:160]}")
        high = True
    if task.retries:
        lines.append(f"- リトライ: {task.retries} 回（NG 積み直しを経た成果）")
        med = True
    if changed:
        sample = ", ".join(sorted(changed)[:5])
        more = f" 他 {len(changed) - 5} 件" if len(changed) > 5 else ""
        lines.append(f"- 変更ファイル: {len(changed)} 件（{sample}{more}）")
        if len(changed) >= 10:
            med = True
    vsrc = task.get("verify_source", "")
    if vsrc.startswith("synth"):
        lines.append(f"- verify は自動合成（{vsrc}）。合否基準そのものの妥当性も確認")
        med = True
    assess = task.get("assess", "")
    if assess:
        lines.append(f"- 投入時採点: {assess}（c=複雑さ r=リスク a=曖昧さ・各1-3）")
        m = re.search(r"\br=(\d)", assess)
        if m and int(m.group(1)) >= 3:
            med = True
    if cfg.regression_cmd:
        lines.append(f"- 回帰ゲート: PASS（`{cfg.regression_cmd}`）")
    if dtok or dusd:
        lines.append(f"- コスト: tokens={dtok} usd={dusd:.4f}")
    level = "high" if high else ("med" if med else "low")
    label = {"high": "高", "med": "中", "low": "低"}[level]
    header = f"- 総合: {label}（protect/avoid=高、リトライ・大差分・合成 verify=中）"
    return level, "\n".join([header] + lines)


def _settle_review(cfg, task, act_msg, git_base, branch, ev, vmsg, protect_hits, assisted,
                   policy, reasons, cycle, risk: "tuple[str, str] | None" = None):
    """verify は通ったが承認ゲート対象（review/gate/protect/assisted）→ done せず人の承認(review)へ。
    所在（ref/ブランチ）を gate_* に保持し、approve 時の受領書へ引き継ぐ。"""
    ts = _now_ts()
    ref = extract_delivery_ref(act_msg, cfg, git_base)
    task.status = "review"
    task.drop("gate_ref", "gate_vmsg", "gate_ts", "gate_protect")
    task.set("gate_ref", ref)
    task.set("gate_ts", ts)
    task.set("gate_branch", branch)             # approve 時の受領書に所在（ブランチ）を引き継ぐ
    task.set("gate_vmsg", vmsg.replace("\n", " ")[:200])
    if protect_hits:
        paths = ", ".join(p for p, _ in protect_hits)
        task.set("gate_protect", paths[:200])
        gate_why = f"保護パス変更（protect）: {paths[:160]} — approve で done 確定"
    elif assisted and not needs_human_review(task, policy):
        gate_why = "assisted レベル（done は人が承認）。approve で done 確定、" \
                   "フィードバック記入で差し戻し（再実行）"
    else:
        gate_why = "承認ゲート対象（review/policy.gate）。approve で done 確定、" \
                   "フィードバック記入で差し戻し（再実行）"
    disp = (f"（保護パス: {paths[:80]}）" if protect_hits
            else "（assisted）" if assisted else "（承認ゲート）")
    reasons[task.id] = ("検収待ち（verify=PASS・保護パス変更。approve で done 確定）"
                        if protect_hits else "検収待ち（verify=PASS。approve で done 確定）")
    # 成果物レビューの MR: タスクブランチ（kp/<id>）→ target の MR を用意し（冪等・GitLab 設定時のみ）、
    # 承認（approve）時に Stage 2 と同じ規則（クリーンなら自動マージ）で決着させる
    mr_url = ensure_task_mr(cfg, task)
    if mr_url:
        ev = (ev + "\n" if ev else "") + f"- MR: {mr_url}（承認時にクリーンなら自動マージ）"
        if not ref:
            task.set("gate_ref", mr_url)
    persist_task(cfg, task)
    write_needs_file(cfg, task, f"verify=PASS だが {gate_why}", review=True, evidence=ev,
                     risk=risk)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 検収待ち{disp} — {ref}")


def _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle, autonomy_cache):
    """verify=PASS かつゲート対象外 → 無人 auto-done（受領書＋archive）。集計 delta を返す。"""
    task.status = "done"
    autonomy_record(cfg, task, clean=True, cache=autonomy_cache)        # 無人 auto-done＝clean 実績
    ts = _now_ts()
    ref = extract_delivery_ref(act_msg, cfg, git_base)   # 成果参照（baseline 以降の新規のみ）
    if dtok or dusd:                                  # コストを納品書に残し stats で集計可能に
        task.extra.append(("cost", f"tokens={dtok} usd={dusd:.4f}"))
    append_delivery(cfg, task, ref, ts, branch=branch)   # 受領書一覧に追記（所在ブランチ併記）
    if cfg.do_archive:
        archive_task(cfg, task, vmsg, ref, ts, evidence=ev)  # backlog → archive/（納品書＋判断材料）
        done_disp = "DONE → archive（納品書）"
    else:
        delete_task_file(cfg, task)
        done_disp = "DONE 削除"
    clear_needs_file(cfg, task.id)
    append_journal(cfg.journal, f"cycle {cycle}: {task.id} {done_disp} — {ref}")
    return {"archived": 1 if cfg.do_archive else 0, "followups": parse_followups(task, act_msg)}


def _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location="local"):
    """verify=NG → 上限内なら積み直し / 学習で自動解決 / 上限超で人へエスカレーション。
    委譲 executor（gitlab）の却下なら、人コメント（やり直し指示）を次 act の feedback に注入する。"""
    task.retries += 1
    if not task.verify:
        _escalate(cfg, task, "verify 未定義", reasons, cycle, evidence=ev)
        if task.norm_status() == "blocked":
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（verify 未定義）")
    elif task.retries > cfg.max_retries:
        learned = find_learned_resolution(cfg, task) if cfg.learn else None
        if learned and not task.get("autolearned"):
            src, guide = learned
            task.drop("feedback", "autolearned")
            task.extra += [("feedback", guide.replace("\n", " ⏎ ")), ("autolearned", "1")]
            task.status = "ready"
            persist_task(cfg, task)
            append_decision(cfg, task.id, "auto",
                            context=f"{task.id}（{task.title}）を学習で自動解決",
                            action="auto-resolve", reason=f"learned from {src}: {guide[:120]}",
                            affects=f"{task.id} → ready")
            append_journal(cfg.journal, f"cycle {cycle}: {task.id} 学習で自動解決"
                                        f"（{src} に倣う・通知を抑制）")
        else:
            _escalate(cfg, task, f"繰り返し NG（retries={task.retries}）: {vmsg}", reasons, cycle,
                      evidence=ev)
            if task.norm_status() == "blocked":
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（繰り返し NG）")
    else:
        task.status = "ready"
        # 委譲 executor の却下: 人コメント（やり直し指示）を feedback に載せて次 act で活かす。
        # コメントが無ければ空＝注入なし（ワーカーが自動で原因判断してやり直す）。
        if executor_delegates(cfg):
            guidance = read_reject_guidance(cfg, location == "remote")
            if guidance:
                task.drop("feedback")
                task.extra.append(("feedback", guidance.replace("\n", " ⏎ ")))
                append_journal(cfg.journal,
                               f"cycle {cycle}: {task.id} 却下コメントを次 act に注入")
                # cohort メンバ/pilot の却下なら、同 cohort の未完了メンバへ指摘を波及（兄弟に同じ轍を踏ませない）。
                cohort_reflux(cfg, task, guidance)
                # 同一タスクの再試行に注入するだけでなく、**横断学習ストアにも蒸留して残す**。
                # これで似たタスク（find_learned_resolution）・別プロジェクト（links）・ltm へ還元される。
                # 対象は人と判別済みの gitlab 人コメント（判別は executor 側 _human_notes）。
                if cfg.learn_capture:
                    append_decision(cfg, task.id, "gitlab",
                                    context=f"{task.id}（{task.title}）が gitlab で却下",
                                    action="gitlab-reject", reason=guidance[:300],
                                    affects=f"{task.id} → ready（次 act に反映）",
                                    learn=distill_learn(cfg, task.title, guidance))
                    # 系の反復検知（昇格ラダー）: 同種の gitlab 却下が閾値に達したら、silent 積み直しを
                    # やめて「分解/verify/policy の見直し」を人へ提案する（＝系の再考へ格上げ）。
                    if cfg.reject_recur > 0 and \
                            count_gitlab_reject_recur(cfg, task) + 1 >= cfg.reject_recur:
                        _escalate(cfg, task,
                                  f"系の再考: 同種タスクの gitlab 却下が反復（≥{cfg.reject_recur} 件）。"
                                  "個別のやり直しでなく、タスク分解・verify・policy の見直しを検討してください。"
                                  f" 直近の指摘: {guidance[:200]}", reasons, cycle, evidence=ev)
                        append_journal(cfg.journal,
                                       f"cycle {cycle}: {task.id} → 人の判断（系の再考・却下反復）")
                        return
        persist_task(cfg, task)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} NG 積み直し "
                                    f"({task.retries}/{cfg.max_retries}) — {vmsg}")


def _settle_task(cfg: "Config", task: "Task", location: str, act_msg: str, cycle: int,
                 dtok: int, dusd: float, git_base, verify_env, policy: "Policy",
                 autonomy_cache: dict, reasons: dict) -> dict:
    """act 済みタスクを検証ゲート（verify→回帰→保護→進捗→flake）に通し、done/review/retry/escalate を
    確定する。副作用（persist/journal/needs/decision/delivery/archive）は内部で行い、run_loop が集計に使う
    deltas（archived・followups）を返す。run_loop の per-task 本体を 1 か所に切り出したもの（挙動は不変）。"""
    # act 中に人が revise（軌道修正）していたら、この試行の結果は確定せず修正内容で積み直す。
    # verify より先に判定する（方向の変わった成果に PASS/FAIL を付けない・verify コストも省く）。
    fresh = _load_task_file(cfg, task.id)
    if fresh is not None and fresh.get("revised"):
        _requeue_revised(cfg, task, fresh, cycle)
        return {"archived": 0, "followups": []}
    if location != "local":
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} を {location} で実行"
                       + (f"（{cfg.git_bus}）" if location == "remote" else ""))

    # 人が「成果物の所在（リポジトリ/ブランチ/コミット）・差分・検証」を見て判断できる材料。
    # needs（判断待ち）と DELIVERY/archive（受領）双方に載せる。
    branch = _current_branch(cfg)
    regressed = False
    vtmp = None
    try:
        # workspace 指定タスクは git-bus ルート（workdir）でなく該当 repo のクローン内（指定 branch・
        # クローンのルート）で検証する。verify はリポジトリ直下からの相対で書かれる規約なので path
        # 配下には潜らない。明示 verify_cwd はそれを優先。
        vcwd, vtmp = _task_verify_cwd(cfg, task)
        venv = verify_env
        if vtmp and (vcwd / ".git").exists():          # 一時 clone は差分基準を clone の HEAD に取り直す
            head = _git_out(vcwd, "rev-parse", "HEAD").strip()
            venv = {"KIRO_BASE_REV": head} if head else None
        ok, flaky, vmsg = run_verify_stable(task.verify, vcwd, cfg.verify_timeout,
                                            cfg.verify_confirm, venv)
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok)
        if ok and not flaky and cfg.regression_cmd:    # done 確定前のグローバル回帰ゲート（巻き込み事故）
            rok, rmsg = run_verify(cfg.regression_cmd, vcwd, cfg.verify_timeout, venv)
            if not rok:
                regressed = True
                if cfg.regression_revert:
                    _revert_workdir(cfg)
                _block(cfg, task, f"回帰検知: グローバル検査 `{cfg.regression_cmd}` 失敗 — {rmsg}", reasons,
                       evidence=ev)
                autonomy_record(cfg, task, clean=False, cache=autonomy_cache)   # 手戻り（track 信頼を下げる）
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（回帰検知）"
                               + ("・revert 済" if cfg.regression_revert else ""))
    except RuntimeError as e:      # workspace clone 失敗等は黙って workdir に倒さず NG（成果の無い場所で誤判定しない）
        ok, flaky, vmsg = False, False, str(e)[:500]
        ev = delivery_evidence(cfg, act_msg, git_base, location,
                               verify=task.verify, vmsg=vmsg, ok=ok)
    finally:
        if vtmp:
            shutil.rmtree(vtmp, ignore_errors=True)
            _prune_caches(_provisioned_urls)   # 共有 cache の worktree 登録を回収（本体は残す）

    changed: set = set()
    protect_hits: list = []
    if ok and not flaky and not regressed:
        changed = meaningful_changes(cfg, git_base)    # act が生んだ成果差分（kiro 状態ファイルは除外）
        if policy.protect:                             # act が保護パスを触ったか（safety denylist）
            protect_hits = sorted({(p, m) for p in changed
                                   if (m := path_protected(p, policy.protect))})
    # no-progress: verify=PASS でも変更ゼロ＝履歴一致 verify による偽 done の疑い（opt-in）
    _expect = task.get("expect", "")
    require_prog = ((cfg.require_progress or _expect == "changes") and _expect != "none"
                    and (cfg.workdir / ".git").exists())
    no_progress = (ok and not flaky and not regressed and require_prog and not changed)
    # red-green: 合成 verify が act 前ツリーでも PASS＝この変更を弁別していない（偽 done）。
    # no-progress（変更ゼロ）の上位互換で、変更があっても verify がそれを追えていないケースを弾く。
    undiscriminating = (ok and not flaky and not regressed and not no_progress
                        and verify_undiscriminating(cfg, task, cfg.workdir,
                                                     vtmp is not None, git_base, verify_env))
    # 実効自律レベル（明示 - level: > track 自動昇格 > グローバル）。report は選択時に除外済み
    assisted = resolve_level(task, cfg, autonomy_cache) == "assisted"

    if flaky:
        # verify が不安定（flake）→ 自動修正せず人へ隔離（NG churn / flaky PASS の done を防ぐ）
        task.set("flake", "1")
        _block(cfg, task, f"flake 検知（verify 不安定・自動修正せず隔離）: {vmsg}", reasons, evidence=ev)
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（flake 検知・quarantine）")
    elif regressed:
        pass                                  # 既に blocked 化済み。done/review にしない
    elif no_progress:
        # verify=PASS だが act が何も変更していない＝履歴一致 verify 等による偽 done の疑い → 人へ
        task.set("noprogress", "1")
        _block(cfg, task, "no-progress: verify=PASS だが baseline 以降の変更が無い"
               "（履歴一致 verify による偽 done の疑い。verify を差分基準で見直すか expect: none を付与）",
               reasons, evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)       # 偽 done 疑い＝手戻り
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（no-progress・偽 done 疑い）")
    elif undiscriminating:
        # verify=PASS だが act 前のツリーでも PASS＝この変更を弁別していない（恒真式/既存状態/履歴一致）→ 人へ
        task.set("undiscriminating", "1")
        _block(cfg, task, "red-green: verify が act 前のツリーでも PASS＝この変更を弁別していない"
               "（偽 done の疑い。verify を望む最終状態/差分の assert に見直す。除外は - verify_validate: none）",
               reasons, evidence=ev)
        autonomy_record(cfg, task, clean=False, cache=autonomy_cache)        # 偽 done 疑い＝手戻り
        append_journal(cfg.journal, f"cycle {cycle}: {task.id} → 人の判断（red-green・変更を弁別しない verify）")
    elif ok and (getattr(cfg, "delivery_review", False)
                 or needs_human_review(task, policy) or protect_hits or assisted):
        # delivery_review（既定 on）: verify PASS 後は level に依らず常に人の検収（review）へ
        _settle_review(cfg, task, act_msg, git_base, branch, ev, vmsg, protect_hits, assisted,
                       policy, reasons, cycle,
                       risk=risk_digest(cfg, task, changed, protect_hits, dtok, dusd))
    elif ok:
        capture_approve_learn(cfg, task, location)   # 承認時の人コメント（正例）を横断 learn 化
        save_validated_verify(cfg, task)             # 通った自動生成 verify を再利用ライブラリへ
        return _settle_done(cfg, task, act_msg, git_base, branch, ev, vmsg, dtok, dusd, cycle,
                            autonomy_cache)
    else:
        _settle_failure(cfg, task, vmsg, cycle, ev, reasons, location)
    return {"archived": 0, "followups": []}


def _run_setup(cfg: "Config") -> tuple:
    """run_loop の前処理: inbox 取り込み → 読み込み → 人のフィードバック解除 → triage/rot で
    ready/blocked を確定 → verify を用意する。(tasks, policy, reasons, ingested, inboxed, pre_blocked)。"""
    ensure_dirs(cfg)
    ingest_commands(cfg)          # 人の指示（approve/hold/pin/defer/revise のファイルドロップ）を先に適用
    inboxed = run_intake(cfg) + ingest_inbox(cfg)     # 取り込みコマンド＋外部ドロップ(inbox/)を backlog へ
    tasks = load_tasks(cfg.backlog)
    recover_revised(cfg, tasks)   # 実行側が settle できなかった revise 予約の回収（クラッシュ自己回復）
    policy = load_policy(cfg.policy)
    reasons: dict[str, str] = {}
    ingested = ingest_feedback(cfg, tasks)           # 人のフィードバックでブロック解除
    pre_blocked = {t.id for t in tasks if t.norm_status() in ("blocked", "review", "proposed")}
    transitions = list(triage(tasks, policy, cfg.plan_review))   # inbox→ready/proposed 昇格・deny→blocked
    if cfg.rot:                                       # rot 検知（古い/重複/実行不能を掃除）
        transitions += [(t, f"rot: {why}") for t, why in detect_rot(cfg, tasks)]
    for t, why in transitions:
        if t.norm_status() != "blocked":
            t.status = "blocked"
        reasons[t.id] = why
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)
    for t in tasks:                                   # accept/verify_template から concrete な verify を用意
        if t.norm_status() in CONSUMABLE and not t.verify and ensure_verify(cfg, t):
            persist_task(cfg, t)
            append_journal(cfg.journal, f"verify 用意: {t.id} ← {t.get('verify_source')}")
    if cfg.assess:                                    # 投入時アセスメント（1 タスク 1 回・実行可否は不変）
        for t in tasks:
            if t.norm_status() in ("proposed", "ready", "inbox") and not t.get("assess"):
                assess_task(cfg, t)
                persist_task(cfg, t)
    tasks += route_spec_tasks(cfg, tasks, policy)     # spec ルーティング（opt-in・spec 前段を前置）
    tasks += expand_spec_tasks(cfg, tasks)            # 承認済み spec の tasks.md を実装タスクへ展開
    ensure_plan_review_needs(cfg, tasks)              # proposed に needs（実行前レビュー票）を用意
    return tasks, policy, reasons, ingested, inboxed, pre_blocked


def _budget_reason(cfg: "Config", cycle: int, start: float,
                   tokens_used: int, cost_used: float) -> "str | None":
    """予算ゲート: サイクル/実時間/トークン/コスト/ソフト(throttle) の上限到達なら停止理由を返す。"""
    if cycle >= cfg.max_cycles:
        return REASON_BUDGET
    if cfg.max_seconds and (time.time() - start) >= cfg.max_seconds:
        return REASON_BUDGET
    if cfg.max_tokens and tokens_used >= cfg.max_tokens:
        return REASON_COST
    if cfg.max_cost and cost_used >= cfg.max_cost:
        return REASON_COST
    if cfg.throttle > 0 and (                 # ソフト予算: ハード上限の手前で緩やかに打ち切る
        (cfg.max_tokens and tokens_used >= cfg.throttle * cfg.max_tokens)
        or (cfg.max_cost and cost_used >= cfg.throttle * cfg.max_cost)):
        return REASON_THROTTLE
    return None


# ---------------------------------------------------------------------------
# 状態の git 保存・共有（state_git）
# ---------------------------------------------------------------------------
# ワークの内容（<root> コンテナ配下の状態ファイル＝backlog/needs/decisions/journal/…）を共有 git
# リポジトリへ保存し、リモートの kiro-projects-viewer と「結果を見せる／指示を受け取る」を往復する。
#   ・リモート負荷を抑える: 専用の管理クローン（subdir だけの sparse・blob:none）を 1 本再利用し、
#     fetch/push は state_git_interval（既定 300 秒）で律速。push は「共有すべきローカルコミットが
#     あるとき」だけ行い、idle 中は間隔ごとの pull 1 本に収まる。
#   ・多重コミッタ前提: 同一リポジトリには他プログラム（viewer 側の git-file-sync・kiro-flow の
#     git バス・別ホストの kiro-project 等）もコミットする。ステージは自 subdir のみ
#     （`add -A -- <subdir>`）、push 競合は pull --rebase → 再 push の指数バックオフで吸収し、
#     force push は決してしない（他者のコミットを壊さない）。
#   ・双方向: 機械の状態は外へ、人の指示（commands/ ドロップ・inbox/ 投入・needs の記入・
#     policy/charter の編集）は中へ。前回同期スナップショット（manifest）基準の 3-way で
#     「どちらが変えたか」を判定し、同時変更だけを「人の入力パスはリモート優先・機械状態は
#     ローカル優先」の決定的規則で裁定する。
STATE_GIT_MARKER = "kiro-project.stateclone"   # 自前管理クローンの目印（git config）
_STATE_LOCK_STALE_SEC = 30.0                    # これ以上古い .git ロックは残骸とみなし自己回復
_STATE_GIT_RETRIES = 4                          # ロック起因の git 失敗の再試行回数
_STATE_PUSH_RETRIES = 5                         # push 競合の再試行回数（2,4,8,16s バックオフ）
# コンテナ相対パスの同期除外。一時/ホスト局所の状態は共有しない:
#   bus/          … kiro-flow の一時バス（run 後に掃除される・肥大しうる）
#   flow-archive/ … viewer が bus から写し取る run のスナップショット（bus の派生・肥大しうる）
#   claims/       … 原子的クレーム（ホスト内の実行権。同期遅延越しでは排他の意味を持たない）
#   "." 始まりのセグメント … .state-git（クローン自身）や .git などの管理領域
_STATE_EXCLUDE_DIRS = {"bus", "flow-archive", "claims"}
# 同時変更（ローカル・リモートの両方が base から変えた）の裁定。人の入力はリモート優先で
# 取りこぼさず、機械状態（backlog/journal/decisions/…）は実行側＝ローカルを正とする。
# repos.{json,yaml,yml} も人が書くレジストリ（charter ## repos の互換入力・手書きが正）なので
# charter.md / policy.md と同じくリモート優先に含める。ただし _meta.generated_from 付きの
# 自動生成 repos.json は、リモート優先で取り込んでも次の run の export_repo_registry が
# charter から再生成するため charter が正のまま保たれる（手書き＝_meta 無しだけが残る）。
_STATE_REMOTE_WINS_DIRS = {"commands", "inbox", "needs"}
# rules.md（プロジェクトルール）も人が書くのが正なのでリモート優先。システムの昇格追記
# （promote_rules）は実行側ローカルで起きるが、同時変更時は人の編集を取りこぼさない側に倒す
# （昇格は冪等なので次パスで再追記される）。
_STATE_REMOTE_WINS_FILES = {"policy.md", "charter.md", "rules.md",
                            "repos.json", "repos.yaml", "repos.yml"}


class StateGit:
    """プロジェクト状態 ⇔ 共有 git リポジトリの双方向同期（kiro-flow GitBus と同じ管理クローン流儀）。
    プロジェクトルート自体が git 作業ツリーでない場合のフォールバック（git のルートなら DirectStateGit）。

    真実は常にファイル側（ローカルはプロジェクトルート・リモートは共有リポジトリ）にあり、このクラスは
    「前回同期時点のスナップショット（manifest）」を基準に差分の発生源を判定して橋渡しするだけ。
    クローンや manifest を失っても、次の同期が裁定規則で決定的に再収束させる。"""

    def __init__(self, container: Path, remote: str, branch: str = "main",
                 subdir: str = "kiro-project", interval: float = 300.0,
                 clone_dir: "Path | None" = None):
        self.container = Path(container)
        self.remote = remote
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")
        self.interval = max(0.0, interval)
        self.clone = Path(clone_dir) if clone_dir else (self.container / ".state-git")
        self._ready = False
        self._last_remote = 0.0     # 最後にリモートへ触れた時刻（fetch/push の間隔律速）
        self._last_attempt = 0.0    # クローン準備の失敗も間隔律速（不通のリモートを連打しない）

    # --- git 低レベル（GitBus と同じ護り: ceiling / C ロケール / ロック残骸の自己回復） ---
    def _env(self) -> dict:
        env = dict(os.environ)
        parent = os.path.dirname(os.path.realpath(self.clone)) or "/"
        ceil = env.get("GIT_CEILING_DIRECTORIES")
        env["GIT_CEILING_DIRECTORIES"] = parent + (os.pathsep + ceil if ceil else "")
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "0"
        env["LC_ALL"] = "C"              # ロック競合の検知は英語メッセージの文字列マッチに頼る
        env["GIT_EDITOR"] = "true"       # rebase --continue がエディタを開かないように
        return env

    _STALE_LOCKS = ("index.lock", "HEAD.lock", "config.lock", "shallow.lock", "packed-refs.lock")

    def _remove_stale_locks(self) -> int:
        removed = 0
        gitdir = self.clone / ".git"
        now = time.time()
        for name in self._STALE_LOCKS:
            p = gitdir / name
            try:
                if p.is_file() and now - p.stat().st_mtime >= _STATE_LOCK_STALE_SEC:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def _is_lock_error(p) -> bool:
        err = p.stderr or ""
        return ".lock" in err and ("File exists" in err or "another git process" in err.lower())

    def _git(self, *args: str, check: bool = False):
        p = None
        for i in range(_STATE_GIT_RETRIES):
            p = subprocess.run(["git", "-C", str(self.clone), *args],
                               capture_output=True, text=True, env=self._env())
            if p.returncode == 0 or not self._is_lock_error(p):
                break
            if self._remove_stale_locks() == 0 and i < _STATE_GIT_RETRIES - 1:
                time.sleep(2 ** i)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失敗: {(p.stderr or '').strip()[:300]}")
        return p

    # --- クローンの用意（自前管理クローンのみ再利用。他人の作業ツリーは決して触らない） ---
    def _is_managed(self) -> bool:
        if not (self.clone / ".git").is_dir():
            return False
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if not top or os.path.realpath(top) != os.path.realpath(str(self.clone)):
            return False
        origin = self._git("remote", "get-url", "origin").stdout.strip()
        same_origin = origin == self.remote or (
            bool(origin) and os.path.realpath(origin) == os.path.realpath(self.remote))
        return same_origin and self._git("config", "--get", STATE_GIT_MARKER).stdout.strip() == "1"

    def _recover(self) -> None:
        """前プロセスの異常終了が残したロック残骸・中断 rebase を自己回復する。"""
        self._remove_stale_locks()
        gitdir = self.clone / ".git"
        if any((gitdir / d).is_dir() for d in ("rebase-merge", "rebase-apply")):
            self._git("rebase", "--abort")
            for d in ("rebase-merge", "rebase-apply"):
                shutil.rmtree(gitdir / d, ignore_errors=True)

    def _setup_worktree(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "kiro-project@local")
            self._git("config", "user.name", "kiro-project")
        if self.subdir:                  # 自分の名前空間だけを作業ツリーに展開（他者のパスを引かない）
            self._git("sparse-checkout", "init", "--cone")
            self._git("sparse-checkout", "set", self.subdir)
        if self._git("checkout", self.branch).returncode != 0:
            self._git("checkout", "-B", self.branch, check=True)   # 空リポジトリ初回など

    def _ensure_clone(self) -> None:
        if self._is_managed():
            self._recover()
            self._setup_worktree()
            return
        if self.clone.is_dir() and any(self.clone.iterdir()):
            raise RuntimeError(
                f"state_git のクローン先 {self.clone} が管理外の非空ディレクトリです"
                "（作業ツリーを壊さないため中断。空のパスを指定してください）")
        self.clone.parent.mkdir(parents=True, exist_ok=True)
        # blob:none で履歴の実体を引かない（非対応サーバはフィルタ無しへフォールバック）
        for extra in (["--filter=blob:none"], []):
            r = subprocess.run(["git", "clone", "--no-checkout", *extra, self.remote,
                                str(self.clone)], capture_output=True, text=True)
            if r.returncode == 0:
                break
            shutil.rmtree(self.clone, ignore_errors=True)
        if r.returncode != 0:
            raise RuntimeError(f"state_git クローン失敗: {(r.stderr or '').strip()[:300]}")
        self._git("config", STATE_GIT_MARKER, "1")
        self._setup_worktree()

    # --- 3-way 同期（manifest = 前回同期時点の path→sha256 スナップショット） ---
    @property
    def _manifest_path(self) -> Path:
        return self.clone / ".git" / "kiro-project-state.json"

    def _load_manifest(self) -> dict:
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, manifest: dict) -> None:
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._manifest_path)

    @staticmethod
    def _excluded(rel: Path) -> bool:
        parts = rel.parts
        return any(s.startswith(".") for s in parts) or any(
            s in _STATE_EXCLUDE_DIRS for s in parts[:-1])

    @staticmethod
    def _remote_wins(rel: str) -> bool:
        parts = Path(rel).parts
        return any(s in _STATE_REMOTE_WINS_DIRS for s in parts[:-1]) or (
            parts and parts[-1] in _STATE_REMOTE_WINS_FILES)

    @staticmethod
    def _scan(root: Path) -> "dict[str, str]":
        """root 配下の同期対象ファイルを {相対パス: sha256} で返す（除外規則は両側で同一）。"""
        out: dict[str, str] = {}
        if not root.is_dir():
            return out
        for base, dirs, files in os.walk(root):
            rel_base = Path(base).relative_to(root)
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d not in _STATE_EXCLUDE_DIRS]
            for name in files:
                rel = rel_base / name
                if StateGit._excluded(rel):
                    continue
                p = Path(base) / name
                if p.is_symlink() or not p.is_file():
                    continue
                try:
                    out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError:
                    pass
        return out

    def _remote_root(self) -> Path:
        return self.clone / self.subdir if self.subdir else self.clone

    def _three_way(self) -> "tuple[int, int]":
        """manifest 基準の 3-way でローカル⇔クローンを橋渡しする。(imported, exported) を返す。"""
        base = self._load_manifest()
        lroot, rroot = self.container, self._remote_root()
        local, remote = self._scan(lroot), self._scan(rroot)
        manifest: dict[str, str] = {}
        imported = exported = 0
        for rel in sorted(set(base) | set(local) | set(remote)):
            lh, rh, bh = local.get(rel), remote.get(rel), base.get(rel)
            if lh == rh:                      # 一致（双方無し含む）→ そのまま
                if lh is not None:
                    manifest[rel] = lh
                continue
            if rh == bh:                      # ローカルだけが変えた（or 消した）→ export
                take_local = True
            elif lh == bh:                    # リモートだけが変えた → import
                take_local = False
            else:                             # 同時変更 → 決定的裁定
                take_local = not self._remote_wins(rel)
            src, dst, h = (lroot, rroot, lh) if take_local else (rroot, lroot, rh)
            try:
                if h is None:                 # 片側の削除を伝播
                    (dst / rel).unlink(missing_ok=True)
                else:
                    d = dst / rel
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src / rel, d)
                    manifest[rel] = h
                imported, exported = (imported, exported + 1) if take_local \
                    else (imported + 1, exported)
            except OSError:
                if bh is not None:            # 反映できなかった分は次回また差分として現れるように
                    manifest[rel] = bh
        self._save_manifest(manifest)
        return imported, exported

    # --- push（多重コミッタ吸収: rebase 再試行・コンフリクトは裁定規則で決着・force しない） ---
    def _resolve_rebase(self) -> None:
        """pull --rebase が同一ファイルの同時変更で止まったら、パス種別の裁定で決着して続行する。
        rebase 中は --ours=リモート（upstream）/ --theirs=ローカルのコミット側。"""
        gitdir = self.clone / ".git"
        for _ in range(50):                   # 有限（1 コミットずつしか進まない）
            if not any((gitdir / d).is_dir() for d in ("rebase-merge", "rebase-apply")):
                return
            conflicted = [ln for ln in self._git(
                "diff", "--name-only", "--diff-filter=U").stdout.splitlines() if ln.strip()]
            for path in conflicted:
                rel = path[len(self.subdir) + 1:] if self.subdir and \
                    path.startswith(self.subdir + "/") else path
                side = "--ours" if self._remote_wins(rel) else "--theirs"
                if self._git("checkout", side, "--", path).returncode != 0:
                    self._git("rm", "-q", "--", path)   # add/delete 衝突: 消えた側に合わせる
                self._git("add", "--", path)
            if self._git("rebase", "--continue").returncode != 0 and \
                    self._git("rebase", "--skip").returncode != 0:
                self._git("rebase", "--abort")          # 進められない → 次回の 3-way で再収束
                return

    def _ahead(self) -> int:
        r = self._git("rev-list", "--count", f"origin/{self.branch}..HEAD")
        if r.returncode == 0:
            try:
                return int(r.stdout.strip() or 0)
            except ValueError:
                return 0
        # リモートにブランチが無い（初回）→ ローカルにコミットがあれば push が必要
        return 1 if self._git("rev-parse", "-q", "--verify", "HEAD").returncode == 0 else 0

    def _push(self) -> None:
        for i in range(_STATE_PUSH_RETRIES):
            if self._git("push", "-u", "origin", self.branch).returncode == 0:
                self._last_remote = time.time()
                return
            self._git("pull", "--rebase", "origin", self.branch)   # 競合 → 取り込んで再試行
            self._resolve_rebase()
            self._last_remote = time.time()
            if i < _STATE_PUSH_RETRIES - 1:
                time.sleep(2 ** i if i < 4 else 16)
        raise RuntimeError(f"state_git push が {self.branch} へ反映できませんでした")

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作は interval で律速し、
        force=True は「push すべきものがあれば間隔を待たず押し出す」（run 直後の結果共有用）。"""
        now = time.time()
        if not self._ready:
            if not force and self.interval > 0 and now - self._last_attempt < self.interval:
                return (0, 0)                 # 不通のリモートへの再クローン連打を防ぐ
            self._last_attempt = now
            self._ensure_clone()
            self._ready = True
            self._last_remote = 0.0           # 初回は必ず pull する（停止中の指示を取りこぼさない）
        with _file_lock(str(self.clone) + ".lock"):   # 同一ホストの多重プロセスを直列化
            due = self.interval <= 0 or (now - self._last_remote) >= self.interval
            if due:                           # 取り込み方向の fetch は間隔でのみ（負荷を一定に保つ）
                self._git("pull", "--rebase", "origin", self.branch)
                self._resolve_rebase()
                self._last_remote = now
            imported, exported = self._three_way()
            pathspec = self.subdir or "."
            self._git("add", "-A", "--", pathspec)               # 自分の名前空間だけをステージ
            # 空コミットを試みない: unborn ブランチでの失敗 commit は index を汚し以後の pull を壊す
            if self._git("status", "--porcelain", "--", pathspec).stdout.strip():
                # 未 push の連続 state sync は --amend で 1 コミットに束ねる（DirectStateGit と同じ）
                amend = ["--amend"] if (self._ahead() > 0 and self._git(
                    "log", "-1", "--format=%s").stdout.strip().startswith(
                        "kiro-project: state sync")) else []
                self._git("commit", "-q", *amend, "-m",
                          f"kiro-project: state sync {datetime.now().isoformat(timespec='seconds')}")
            if (due or force) and self._ahead() > 0:
                self._push()
        return imported, exported


class DirectStateGit:
    """プロジェクトルート自体が git 作業ツリー（トップレベル）のときの直接同期（direct モード）。

    管理クローン（StateGit）を介さず、ルートのリポジトリのブランチへ state コミットを積む。
    ただし **ルートのチェックアウト（index・作業ツリー・stash）には触れない**:

    - export: コミットは detached worktree（専用 index）で組み立て、ルートのブランチは
      update-ref の CAS（compare-and-swap）で進める。人が同時にコミットしていたら
      今回の export は見送る（次パスで再試行）＝ index.lock 競合・人のステージの
      巻き込み・コミットの衝突が起きない。
    - import: fetch → ff-only を優先し、分岐時のみ rebase する。--autostash は使わない
      （未コミット変更と衝突するなら取り込みを見送る＝人の作業を stash で壊さない）。
    - push: HEAD:branch。reject は fetch + 上記 integrate の再試行で合流（force push しない）。

    同期対象・除外規則は StateGit と同一（bus/ claims/ とドット始まりは同期しない）。
    リモート（origin）が無ければコミットのみ行う。"""

    def __init__(self, root: Path, interval: float = 300.0):
        self.root = Path(root)
        self.interval = max(0.0, interval)
        self._last_remote = 0.0

    def _env(self) -> dict:
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["GIT_EDITOR"] = "true"
        return env

    def _git(self, *args: str):
        return subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, text=True, env=self._env())

    def _branch(self) -> str:
        # symbolic-ref は unborn ブランチ（空リポジトリの clone 直後）でも現在ブランチ名を返す
        name = self._git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        return name or self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"

    def _has_remote(self) -> bool:
        return bool(self._git("remote", "get-url", "origin").stdout.strip())

    def _ensure_identity(self) -> None:
        if not self._git("config", "user.email").stdout.strip():
            self._git("config", "user.email", "kiro-project@local")
            self._git("config", "user.name", "kiro-project")

    _MERGE_ATTRS = ("journal.md merge=union",)

    def _ensure_merge_attrs(self) -> None:
        """journal.md（追記専用ログ）の EOF 追記同士が _integrate の rebase/merge で
        衝突しないよう、リポジトリローカルの .git/info/attributes に union マージを
        宣言する（冪等）。versioned な .gitattributes はユーザーの領分なので触れない。"""
        gp = self._git("rev-parse", "--git-path", "info/attributes").stdout.strip()
        if not gp:
            return
        attrs = Path(gp) if os.path.isabs(gp) else (self.root / gp)
        try:
            cur = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
            missing = [ln for ln in self._MERGE_ATTRS if ln not in cur.splitlines()]
            if not missing:
                return
            attrs.parent.mkdir(parents=True, exist_ok=True)
            with attrs.open("a", encoding="utf-8") as f:
                if cur and not cur.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(missing) + "\n")
        except OSError:
            pass

    def _changed_targets(self) -> "list[str]":
        """ルート配下の未コミット変更のうち同期対象（StateGit と同じ除外規則）の相対パス。"""
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        out: list[str] = []
        for line in self._git("status", "--porcelain", "--", ".").stdout.splitlines():
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if not path:
                continue
            try:                          # porcelain はリポジトリトップ相対 → ルート相対へ
                rel = (Path(top) / path).resolve().relative_to(self.root.resolve())
            except ValueError:
                continue
            # 未追跡ディレクトリは `bus/` のようにディレクトリ 1 行で出るため、
            # 全要素を除外規則（ドット始まり・bus/claims）にかける（StateGit と同じ集合）。
            parts = rel.parts
            if any(s.startswith(".") for s in parts) or any(
                    s in _STATE_EXCLUDE_DIRS for s in parts):
                continue
            out.append(str(rel))
        return out

    def _amendable(self, remote: bool) -> bool:
        """HEAD が「未 push の state sync コミット」なら True（--amend で 1 つに束ねる）。
        push 済み履歴は書き換えず、人・他コミッタのコミットが HEAD のときは通常コミットで積む。"""
        head = self._git("log", "-1", "--format=%s").stdout.strip()
        if not head.startswith("kiro-project: state sync"):
            return False
        if not remote:
            return True
        r = self._git("rev-list", "--count", f"origin/{self._branch()}..HEAD")
        if r.returncode != 0:
            return True                       # リモートに追跡ブランチが無い＝未 push
        try:
            return int(r.stdout.strip() or 0) > 0
        except ValueError:
            return False

    def _commit_msg(self) -> str:
        return f"kiro-project: state sync {datetime.now().isoformat(timespec='seconds')}"

    def _cas_branch(self, branch: str, new: str, old: str) -> bool:
        """ルートのブランチを CAS（update-ref <new> <old>）で進める。old 不一致
        （人の並行コミット）なら失敗＝今回の export は見送り。作業ツリーには触れない。"""
        zero = "0" * 40
        r = self._git("update-ref", f"refs/heads/{branch}", new, old or zero)
        return r.returncode == 0

    def _refresh_index(self, targets: "list[str]") -> None:
        """CAS で進めた新 HEAD に、対象パスの index エントリだけを追随させる
        （作業ツリー内容＝コミット内容なので status が clean に戻る）。他パスのステージは触らない。
        未追跡ディレクトリは porcelain が 1 行（`dir/`）で返すため、ここでファイルへ展開する。"""
        existing: list[str] = []
        gone: list[str] = []
        for t in targets:
            p = self.root / t
            if p.is_dir():
                for base, _dirs, files in os.walk(p):
                    for name in files:
                        existing.append(str((Path(base) / name).relative_to(self.root)))
            elif p.exists():
                existing.append(t)
            else:
                gone.append(t)
        if existing:
            self._git("update-index", "--add", "--", *existing)
        if gone:
            self._git("update-index", "--remove", "--", *gone)

    def _initial_commit(self, targets: "list[str]", branch: str) -> "str | None":
        """unborn ブランチ（コミット 0 件）への最初の state コミット。worktree は作れないため
        一時 index（GIT_INDEX_FILE）で組み立てる。ルートの index には触れない。"""
        fd, tmpidx = tempfile.mkstemp(prefix="kiro-project-state-idx-")
        os.close(fd)
        os.remove(tmpidx)                    # git が新規作成する
        env = {**self._env(), "GIT_INDEX_FILE": tmpidx}
        try:
            existing = [t for t in targets if (self.root / t).exists()]
            if not existing:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "add", "--", *existing],
                               capture_output=True, text=True, env=env)
            if r.returncode != 0:
                return None
            tree = subprocess.run(["git", "-C", str(self.root), "write-tree"],
                                  capture_output=True, text=True, env=env).stdout.strip()
            if not tree:
                return None
            r = subprocess.run(["git", "-C", str(self.root), "commit-tree", tree,
                                "-m", self._commit_msg()],
                               capture_output=True, text=True, env=env)
            new = r.stdout.strip()
            if r.returncode != 0 or not new:
                return None
            if not self._cas_branch(branch, new, ""):
                return None
            self._refresh_index(targets)
            return new
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmpidx)

    def _worktree_commit(self, targets: "list[str]", branch: str,
                         amend: bool) -> "str | None":
        """state コミットを detached worktree（専用 index）で組み立てる。
        ルートの index・作業ツリーに触れず、ブランチ更新は CAS（_cas_branch）のみ。
        amend=True なら HEAD（未 push の state sync コミット）へ束ねる（--amend）。
        返り値は新コミット SHA（差分なし・競合検知・失敗は None）。"""
        old = self._git("rev-parse", "HEAD").stdout.strip()
        if not old:
            return None
        wt = tempfile.mkdtemp(prefix="kiro-project-state-wt-")
        os.rmdir(wt)                         # worktree add は空でも既存ディレクトリを嫌う
        try:
            if self._git("worktree", "add", "--detach", "--force", wt, old).returncode != 0:
                return None

            def _wgit(*args: str):
                return subprocess.run(["git", "-C", wt, *args],
                                      capture_output=True, text=True, env=self._env())

            for rel in targets:              # 現在のルートの内容を worktree へ写す（削除も反映）
                src = self.root / rel
                dst = Path(wt) / rel
                if src.is_dir():             # 未追跡ディレクトリ（porcelain は dir/ 1 行で返す）
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                elif dst.exists():
                    _wgit("rm", "-rq", "--ignore-unmatch", "--", rel)
            _wgit("add", "-A", "--", *targets)
            if _wgit("diff", "--cached", "--quiet").returncode == 0:
                return None                  # 差分なし
            # 連続する state sync は未 push の間 --amend で 1 コミットに束ねる
            # （同期のたびに 1 行差分のコミットが積もり、履歴を埋め尽くすのを防ぐ）
            args = (["commit", "-q", "--amend"] if amend else ["commit", "-q"])
            if _wgit(*args, "-m", self._commit_msg()).returncode != 0:
                return None
            new = _wgit("rev-parse", "HEAD").stdout.strip()
            if not new or not self._cas_branch(branch, new, old):
                return None                  # 人の並行コミットに競り負け → 次パスで再試行
            self._refresh_index(targets)
            return new
        finally:
            self._git("worktree", "remove", "--force", wt)
            shutil.rmtree(wt, ignore_errors=True)
            self._git("worktree", "prune")

    def _integrate(self, branch: str) -> int:
        """origin/<branch> をローカルへ取り込む。ff-only を優先し、分岐時のみ rebase。
        --autostash は使わない: 未コミット変更と衝突するときは取り込みを見送る（壊さない）。
        取り込んだファイル数を返す（見送り・コンフリクト abort は 0）。"""
        if self._git("rev-parse", "-q", "--verify",
                     f"refs/remotes/origin/{branch}").returncode != 0:
            return 0
        before = self._git("rev-parse", "HEAD").stdout.strip()
        if not before:
            return 0
        r = self._git("rev-list", "--count", f"HEAD..origin/{branch}")
        try:
            behind = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
        except ValueError:
            behind = 0
        if behind == 0:
            return 0
        r = self._git("rev-list", "--count", f"origin/{branch}..HEAD")
        try:
            local_only = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
        except ValueError:
            local_only = 0
        if local_only == 0:
            ok = self._git("merge", "--ff-only", f"origin/{branch}").returncode == 0
        else:
            ok = self._git("rebase", f"origin/{branch}").returncode == 0
            if not ok:
                self._git("rebase", "--abort")
        if not ok:
            return 0
        after = self._git("rev-parse", "HEAD").stdout.strip()
        if before == after:
            return 0
        diff = self._git("diff", "--name-only", before, after, "--", ".").stdout
        return len([ln for ln in diff.splitlines() if ln.strip()])

    def sync(self, force: bool = False) -> "tuple[int, int]":
        """双方向同期を 1 回行い (imported, exported) を返す。リモート操作は interval で律速し、
        force=True は「push すべきものがあれば間隔を待たず押し出す」（run 直後の結果共有用）。"""
        now = time.time()
        lock = os.path.join(tempfile.gettempdir(),
                            f"kiro-project-sync-{hashlib.sha1(str(self.root).encode()).hexdigest()[:12]}.lock")
        with _file_lock(lock):            # 同一ホストの多重プロセスを直列化
            self._ensure_identity()
            self._ensure_merge_attrs()    # journal の追記同士を union で無衝突マージ
            remote = self._has_remote()
            branch = self._branch()
            due = self.interval <= 0 or (now - self._last_remote) >= self.interval
            imported = 0
            if remote and due:
                self._git("fetch", "-q", "origin", branch)   # 取り込みは _integrate が行う
                self._last_remote = now
            targets = self._changed_targets()
            exported = 0
            if targets:
                if self._git("rev-parse", "-q", "--verify", "HEAD").returncode != 0:
                    new = self._initial_commit(targets, branch)
                else:
                    new = self._worktree_commit(targets, branch,
                                                amend=self._amendable(remote))
                if new:
                    exported = len(targets)
            if remote and (due or force):
                imported += self._integrate(branch)
                r = self._git("rev-list", "--count", f"origin/{branch}..HEAD")
                if r.returncode == 0:
                    ahead = (r.stdout.strip() or "0") != "0"
                else:                     # リモートにブランチが無い（初回）→ コミットがあれば push
                    ahead = self._git("rev-parse", "-q", "--verify", "HEAD").returncode == 0
                if ahead:
                    for i in range(_STATE_PUSH_RETRIES):
                        if self._git("push", "-u", "origin", f"HEAD:{branch}").returncode == 0:
                            self._last_remote = time.time()
                            break
                        self._git("fetch", "-q", "origin", branch)
                        imported += self._integrate(branch)
                        if i < _STATE_PUSH_RETRIES - 1:
                            time.sleep(2 ** i if i < 4 else 16)
                    else:
                        raise RuntimeError(f"state_git push が {branch} へ反映できませんでした")
        return imported, exported


# プロジェクトルート単位で同期器を再利用する（watch 常駐で毎パス作り直さない）
_STATE_GITS: "dict[tuple, object]" = {}


def _git_toplevel(root: Path) -> bool:
    """root 自体が git 作業ツリーのトップレベルか（direct モードの発動条件）。
    リポジトリ内の深いサブディレクトリでは発動させない（無関係リポジトリへの自動コミットを防ぐ）。"""
    if not (root / ".git").exists():
        return False
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(str(root))


def state_git_status_line(cfg: "Config") -> str:
    """起動時に「state_git が有効か・何を鏡写しするか」を一行で示す（silent な設定ミスの切り分け用）。
    注意: これはプロジェクト状態（backlog/needs/…）の鏡写し。kiro-flow のバス（フロータブの run 表示）
    は別途 kiro-flow 側の state_git が担う（本ツールはバスを同期しない）。"""
    root = cfg.backlog.parent
    if _git_toplevel(root):
        return (f"state-git: direct モード → {root} 自体の git リポジトリへ直接コミット/push "
                f"interval={cfg.state_git_interval}s")
    if not getattr(cfg, "state_git", None):
        return "state-git: 無効（未設定・ルートも git リポジトリでない）"
    return (f"state-git: 有効 → {cfg.state_git} subdir={cfg.state_git_subdir} "
            f"interval={cfg.state_git_interval}s（プロジェクト状態を鏡写し。kiro-flow のバスは "
            f"kiro-flow 側 state_git が別途担当）")


def state_git_for(cfg: "Config") -> "StateGit | DirectStateGit | None":
    root = cfg.backlog.parent
    # direct モード（既定）: ルート自体が git クローンなら、そのリポジトリへ直接コミット・push する。
    if _git_toplevel(root):
        key = ("direct", str(root))
        if key not in _STATE_GITS:
            _STATE_GITS[key] = DirectStateGit(root, cfg.state_git_interval)
        return _STATE_GITS[key]
    # フォールバック: ルートが git でないときは管理クローン（.state-git）で共有リポジトリへ鏡写しする。
    if not getattr(cfg, "state_git", None):
        return None
    key = (str(root), cfg.state_git, cfg.state_git_branch, cfg.state_git_subdir)
    if key not in _STATE_GITS:
        _STATE_GITS[key] = StateGit(root, cfg.state_git, cfg.state_git_branch,
                                    cfg.state_git_subdir, cfg.state_git_interval,
                                    clone_dir=root / ".state-git")
    return _STATE_GITS[key]


# 実行層 kiro-flow を「プロジェクト単位で kiro-project が起動・監視」する。
# kiro-flow は project の概念を持たず素の単一バス daemon のまま。プロジェクトとリポジトリの対応
# （＝どのバスがどこへ鏡写しするか）は制御層 kiro-project が握り、daemon 起動時に CLI で注入する:
#   kiro-flow --bus <project>/bus --state-git <repo> --state-git-subdir kiro-flow ... daemon ...
# 起動はバスロックで冪等（既に稼働なら二重起動しない）。kiro-project 停止時も detached で残すため、
# in-flight run（gitlab 長期委譲・夜間停止からの孤児再開）は daemon 側でそのまま継続する。
FLOW_STATE_SUBDIR = "kiro-flow"   # プロジェクト固有リポジトリ内の kiro-flow 名前空間（viewer は <clone>/kiro-flow）


def project_flow_remote(cfg: "Config") -> "tuple[str, str, float] | None":
    """このプロジェクトの kiro-flow バスを鏡写しすべき (remote, branch, interval)。無ければ None。
    プロジェクト状態と同じ共有リポジトリの kiro-flow 名前空間へ向ける: direct モードなら
    ルートの origin（現在ブランチ）、state_git 設定ならそのリポジトリ。"""
    root = cfg.backlog.parent
    if _git_toplevel(root):
        r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        remote = r.stdout.strip() if r.returncode == 0 else ""
        if not remote:
            return None
        b = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        branch = b.stdout.strip() or "main"
        return remote, branch, cfg.state_git_interval
    if getattr(cfg, "state_git", None):
        return cfg.state_git, cfg.state_git_branch, cfg.state_git_interval
    return None


def flow_daemon_cmd(cfg: "Config", budget: int) -> "list[str]":
    """このプロジェクトの kiro-flow daemon 起動コマンド。CLI で注入するのは kiro-project の役割である
    per-project routing（どのバスをどのリポジトリへ鏡写しするか＝`--state-git` remote/branch/interval）
    と、バス・executor・予算・ロック置き場だけ。state_git サブディレクトリを含む kiro-flow の設定値は
    個別注入せず flow_config（--config）に集約して kiro-flow に読ませる（未指定なら kiro-flow の既定
    ＝subdir は "kiro-flow"）。これで kiro-project 側に kiro-flow 設定を増やさずに済む。"""
    base = resolve_kiro_flow(cfg.kiro_flow) + ["--bus", str(cfg.bus)]
    rf = project_flow_remote(cfg)
    if rf is not None:
        remote, branch, interval = rf
        base += ["--state-git", remote, "--state-git-branch", branch,
                 "--state-git-interval", str(interval)]
    fc = getattr(cfg, "flow_config", None)
    if fc:
        base += ["--config", os.path.abspath(os.path.expanduser(str(fc)))]
    if cfg.lock_dir:
        base += ["--lock-dir", str(cfg.lock_dir)]   # kiro-flow と同じロック置き場（検知の一致）
    base += ["daemon", "--max-workers", str(max(1, int(budget))), "--executor", cfg.executor]
    return base


def ensure_flow_daemon(cfg: "Config", budget: int) -> bool:
    """このプロジェクトの kiro-flow daemon を（無ければ）detached で起動する。起動したら True。
    manage_flow_daemon が off・per-project 対象でない・既に稼働中、のときは何もしない（冪等）。
    kiro-project 停止後も残す（start_new_session）＝in-flight run を跨いで維持する。"""
    if not getattr(cfg, "manage_flow_daemon", False):
        return False
    if project_flow_remote(cfg) is None:        # 鏡写しの落とし先なし → 対象外
        return False
    if daemon_running(cfg, use_git=False):      # 既にこのバスの daemon が稼働（ロック保持）→ 冪等スキップ
        return False
    cmd = flow_daemon_cmd(cfg, budget)
    try:
        cfg.bus.mkdir(parents=True, exist_ok=True)
        logp = cfg.backlog.parent / "flow-daemon.log"
        try:
            logf = open(logp, "a", encoding="utf-8")
        except OSError:
            logf = subprocess.DEVNULL
        try:
            subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=str(cfg.workdir))
        finally:
            if hasattr(logf, "close"):
                logf.close()
        append_journal(cfg.journal,
                       f"kiro-flow daemon 起動: bus={cfg.bus} max_workers={max(1, int(budget))}")
        return True
    except OSError as e:
        append_journal(cfg.journal, f"kiro-flow daemon 起動失敗（続行）: {e}")
        return False


def status_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "status.json"


def pause_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "paused.json"


def is_paused(cfg: "Config") -> bool:
    return pause_path(cfg).exists()


class _StopRequested(Exception):
    """commands/ の {"command": "stop"} による graceful 停止の内部シグナル。
    KeyboardInterrupt と同じ finally 経路（レジストリ後始末）を通して 0 終了する。"""


def _status_fresh_after_sec(cfg: "Config") -> float:
    """リモート viewer が『稼働中』と信じてよい経過秒数の目安。state_git/status の同期間隔
    から書き手（自分の設定を知っている側）が計算し、viewer 側は単純比較だけで済むようにする。"""
    intervals = [i for i in (cfg.state_git_interval, cfg.status_interval) if i and i > 0]
    return max([2.0 * i for i in intervals] + [120.0])


def write_status(cfg: "Config") -> None:
    """status.json（生存信号）を書く。state_git 越しにリモートの kiro-projects-viewer が
    『daemon が今も生きているか』を判定するための最小スナップショット（watch/level の
    現在値＋更新時刻のみ）。backlog/needs/decisions/run-log 等の実データはここで重複を
    持たない（既に state_git で同期されるため）。実パス完了時に呼べば、そのパスが触った
    他ファイルの変更と同じコミットに相乗りする＝これ単体で追加の push を生まない。"""
    rec = {
        "host": socket.gethostname(), "watch": cfg.watch, "level": cfg.level,
        "paused": is_paused(cfg),
        "updated_iso": _now_ts(), "fresh_after_sec": _status_fresh_after_sec(cfg),
    }
    try:
        p = status_path(cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def maybe_heartbeat_status(cfg: "Config") -> None:
    """watch アイドル中の任意の生存信号更新（`--status-interval`。既定 0＝無効）。
    無効時は status.json に一切触れない＝state_git の commit-if-diff で追加コミットを
    作らない（idle の git 負荷は今日と同じゼロ）。有効時も前回書き込みから
    status_interval 秒経つまでは触らず、書き込み頻度を利用者の指定した間隔に抑える。"""
    if cfg.status_interval <= 0:
        return
    try:
        age = time.time() - status_path(cfg).stat().st_mtime
    except OSError:
        age = float("inf")     # 未作成 → 書く
    if age >= cfg.status_interval:
        write_status(cfg)


def state_sync(cfg: "Config", force: bool = False) -> None:
    """状態の git 同期（best-effort）。ネットワーク断・リポジトリ不通でもループは殺さず
    journal に残して続行する（done の確定や消化は state_git に一切依存しない）。"""
    sg = state_git_for(cfg)
    if sg is None:
        return
    try:
        imported, exported = sg.sync(force=force)
        # journal へ残すのは取り込み（リモートの指示の反映）だけ。export を記録すると
        # その行自体が同期対象（journal.md）の新しい差分になり、「export=1」の空同期と
        # コミットが恒久に続くフィードバックループになる（export の履歴は git 側が持つ）。
        if imported:
            append_journal(cfg.journal, f"state-git 同期: import={imported} export={exported}")
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"state-git 同期失敗（続行）: {e}")


def _mark_offloaded(cfg: "Config", task: "Task", location: str, run_id: str) -> None:
    """タスクを『非ブロッキング委譲・結果待ち』に退避する（run_loop が settle をスキップ）。"""
    task.status = "offloaded"
    task.set("flow_run", run_id)
    task.set("flow_loc", location)
    persist_task(cfg, task)


def _reap_offloaded(cfg: "Config", tasks: "list[Task]", policy: "Policy",
                    autonomy_cache: dict, reasons: dict, cycle0: int,
                    spawn_budget: int) -> dict:
    """offloaded タスク（非ブロッキング委譲・結果待ち）を1回ずつポーリングし、終端した run だけ
    settle する（未終端はそのまま次パスへ）。専用 daemon が run を保持するので、ここでは待たない。
    deltas（settled/archived/spawned/tokens/cost）を返す。"""
    settled = archived = spawned = tokens = 0
    cost = 0.0
    for task in [t for t in tasks if t.norm_status() == "offloaded"]:
        run_id = str(task.get("flow_run", "") or "")
        loc = str(task.get("flow_loc", "daemon") or "daemon")
        if not run_id:
            continue
        term, ok, msg = _flow_result_once(cfg, loc == "remote", run_id)
        if not term:
            continue                       # まだ実行中 → 次パスで再確認（ブロックしない）
        if not claim_task(cfg, task):      # 実行権を取ってから確定（他インスタンスと競合しない）
            continue
        gb = git_change_baseline(cfg.workdir)   # 完了時点の基準（remote/daemon 委譲は local 差分なし）
        venv = {"KIRO_BASE_REV": gb[0]} if gb[0] else None
        task.drop("flow_run", "flow_loc")
        task.status = "doing"
        persist_task(cfg, task)
        dtok, dusd = parse_cost(msg)
        tokens += dtok
        cost += dusd
        res = _settle_task(cfg, task, loc, msg, cycle0 + settled + 1, dtok, dusd, gb, venv,
                           policy, autonomy_cache, reasons)
        archived += res["archived"]
        if res["followups"] and spawned < spawn_budget:
            new = spawn_followups(cfg, task, res["followups"], tasks, spawn_budget - spawned)
            spawned += len(new)
        release_claim(cfg, task)
        settled += 1
    return {"settled": settled, "archived": archived, "spawned": spawned,
            "tokens": tokens, "cost": cost}


def run_loop(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep) -> dict:
    state_sync(cfg)                    # 状態 git: リモートの指示（commands/inbox/needs 記入）を先に取り込む
    tasks, policy, reasons, ingested, inboxed, pre_blocked = _run_setup(cfg)
    append_journal(cfg.journal, f"=== kiro-project 開始 tasks={len(tasks)} "
                                f"ingested={len(ingested)} planner={cfg.planner} "
                                f"executor={cfg.executor} dry_run={cfg.dry_run} ===")
    append_journal(cfg.journal, state_git_status_line(cfg))
    start = time.time()
    cycle = 0
    archived = 0
    spawned_total = 0
    tokens_used = 0
    cost_used = 0.0
    reason = REASON_DRAINED

    unavailable: set[str] = set()             # この run でクレームできなかった（他者処理中の）タスク
    plan: list[str] = []
    plan_seen: set[str] = set()               # 計画に載せた report タスク（重複追記の防止）
    autonomy_cache: dict = {}                  # track→自動昇格レコードの読みキャッシュ

    while True:                                # report タスクは actionable から除外し有限停止で収束
        budget_stop_reason = _budget_reason(cfg, cycle, start, tokens_used, cost_used)
        if budget_stop_reason:
            reason = budget_stop_reason
            break

        # 人の指示（commands/ ドロップ・needs 記入）はパス途中でも取り込む＝フィードバック即応。
        # この時点で act 中のタスクは無く（バッチは同期で settle 済み）、変更は都度 persist
        # されているため、ファイル（＝真実）から再読しても安全。バックログが長くても、
        # 人の revise（依存 after・優先度・内容の修正）が次のサイクルからすぐ効く。
        if cycle:
            state_sync(cfg)                    # リモートの指示も間隔律速の範囲で取り込む
            if _has_pending_input(cfg):
                ingest_commands(cfg)
                tasks = load_tasks(cfg.backlog)
                recover_revised(cfg, tasks)
                policy = load_policy(cfg.policy)
                ingested += ingest_feedback(cfg, tasks)

        # 非ブロッキング委譲（act_async）の回収: offloaded タスクの run を1回ずつポーリングし、
        # 終端したものだけ settle する（待たない）。専用 daemon が run を保持するので、gitlab の
        # 長期委譲でもループを塞がず、完了したものから順に消化できる。
        reaped = _reap_offloaded(cfg, tasks, policy, autonomy_cache, reasons, cycle,
                                 cfg.max_spawn - spawned_total)
        if reaped["settled"]:
            cycle += reaped["settled"]
            archived += reaped["archived"]
            spawned_total += reaped["spawned"]
            tokens_used += reaped["tokens"]
            cost_used += reaped["cost"]
            tasks = load_tasks(cfg.backlog)    # settle が状態を変えたので再読

        order_all = [t for t in prioritize(tasks, policy, cfg.planner, cfg.model, ranker)
                     if t.id not in unavailable]  # 他 worker/インスタンスがクレーム済みは除外
        levels = {t.id: resolve_level(t, cfg, autonomy_cache) for t in order_all}
        for t in order_all:                       # report タスクは実行せず「計画」に載せて保留（塩漬け）
            if levels[t.id] == "report" and t.id not in plan_seen:
                plan_seen.add(t.id)
                plan.append(t.id)
                append_journal(cfg.journal, f"report: {t.id} — {t.title}（level=report・実行せず保留）")
        order = [t for t in order_all if levels[t.id] != "report"]
        if not order:                             # 実行可能ゼロ＝消化完了（全 report ならグローバルに応じ report）
            reason = "report" if cfg.level == "report" else REASON_DRAINED
            break

        # 並列消費: 依存解決済み（=互いに独立）な先頭群を daemon/remote へ並行 submit。
        # verify 以降のローカル状態変更は逐次のまま（competition を避け不変条件を保つ）。
        batch = _select_batch(order, cfg, policy, cfg.max_cycles - cycle)
        git_base = git_change_baseline(cfg.workdir)   # act 前スナップショット（保護パス/進捗判定/成果参照）
        verify_env = {"KIRO_BASE_REV": git_base[0]} if git_base[0] else None  # verify に差分基準を渡す
        act_results = _act_batch(batch, cfg, act, policy)   # クレームできたものだけ実行
        if not act_results:                      # 全て他者がクレーム済み → 次パスへ（この run では触らない）
            unavailable.update(t.id for t in batch)
            continue

        stop = None
        for task in batch:
            if task.id not in act_results:        # クレームできなかった分はこの run では飛ばす
                unavailable.add(task.id)
                continue
            location, pend, act_msg = act_results[task.id]
            if pend is not None:                  # 非ブロッキング委譲（offload）: 待たず offloaded に退避
                _mark_offloaded(cfg, task, location, pend.run_id)
                release_claim(cfg, task)          # 実行権は解放（次パスでポーリングして終端したら settle）
                append_journal(cfg.journal, f"{task.id} を offload（run={pend.run_id}）→ 結果待ち")
                unavailable.add(task.id)          # この run ではもう触らない（再選択しない）
                continue
            cycle += 1
            cycle_start = time.time()
            dtok, dusd = parse_cost(act_msg)             # このサイクルのコストを計上（予算ゲート用）
            tokens_used += dtok
            cost_used += dusd
            if dtok or dusd:
                append_journal(cfg.journal, f"cycle {cycle}: {task.id} cost tokens={dtok} usd={dusd:.4f}"
                                            f"（累計 tokens={tokens_used} usd={cost_used:.4f}）")
            res = _settle_task(cfg, task, location, act_msg, cycle, dtok, dusd, git_base,
                               verify_env, policy, autonomy_cache, reasons)
            archived += res["archived"]
            if res["followups"] and spawned_total < cfg.max_spawn:   # done から派生タスク（backlog 自走）
                new = spawn_followups(cfg, task, res["followups"], tasks, cfg.max_spawn - spawned_total)
                spawned_total += len(new)
                if new:
                    append_journal(cfg.journal,
                                   f"cycle {cycle}: {task.id} から派生生成 {[t.id for t in new]}")

            release_claim(cfg, task)          # doing でなくなったので実行権を解放
            if cfg.once:
                stop = "once"
                break
            delay = decide_pace(cfg, time.time() - cycle_start)
            if delay > 0:
                sleeper(delay)
        if stop:
            reason = stop
            break

    counts = summarize(tasks)
    newly_blocked = {t.id for t in tasks
                     if t.norm_status() in ("blocked", "review")} - pre_blocked
    budget_stop = reason in (REASON_BUDGET, REASON_COST)
    notified = notify(cfg, tasks, reasons, newly_blocked, budget_stop)
    promote_rules(cfg)                                     # 効いた学習を rules.md（常時注入層）へ昇格
    promoted = promote_learnings(cfg) if cfg.ltm else []   # 効いた学習を ltm-use へ昇格（横断・opt-in）
    _cleanup_bus(cfg)             # 不要な一時ファイル（kiro-flow バスの run 状態）を掃除
    append_journal(cfg.journal, f"=== kiro-project 停止 reason={reason} cycles={cycle} "
                                f"done={counts['done']} blocked={counts['blocked']} "
                                f"notified={notified} promoted={len(promoted)} ===")
    append_runlog(cfg.runlog, {                    # 構造化 run-log（機械可読・運用判断の土台）
        "ts": datetime.now().isoformat(timespec="seconds"), "reason": reason,
        "level": cfg.level, "cycles": cycle, "done": counts["done"],
        "blocked": counts["blocked"], "review": counts.get("review", 0),
        "archived": archived, "escalations": len(newly_blocked),
        "spawned": spawned_total, "inboxed": len(inboxed),
        "tokens": tokens_used, "cost": round(cost_used, 4),
        "duration_s": round(time.time() - start, 2)})
    write_status(cfg)             # 生存信号（このパスが触った他ファイルの変更と同じコミットに相乗り）
    state_sync(cfg, force=True)   # 状態 git: このパスの結果（done/needs/journal）を共有側へ押し出す
    return {"reason": reason, "cycles": cycle, "counts": counts, "tasks": tasks,
            "reasons": reasons, "newly_blocked": newly_blocked, "notified": notified,
            "ingested": ingested, "archived": archived, "promoted": promoted,
            "spawned": spawned_total, "tokens": tokens_used, "cost": cost_used,
            "inboxed": inboxed, "level": cfg.level, "plan": plan}


def _cleanup_bus(cfg: Config) -> None:
    """local run 後に不要となる kiro-flow バスの一時状態を掃除する。
    daemon 稼働中や git バス（remote）は作業中のため触らない。また state_git でバスを
    リモート viewer へ鏡写ししている構成では、ここで runs/ を消すと『フロータブに見せたい
    run 状態』を破壊し、削除が次の同期でリモートへ伝播してしまうため触らない
    （kiro-flow 側の state_git がバスの寿命を管理する＝gc に委ねる）。

    runs/<id>/ は viewer のフロータブが読む一次ソースなので、直近 bus_keep_runs 件は残す。
    かつては act のたびに runs/ を丸ごと消していたため、run は完了しているのに viewer が
    その最終状態（全ノード done）を観測する前にディレクトリごと消え、最後に撮れた
    スナップショット（最終ノードが実行中）のままフローが固まって見えていた。掃除は
    「古い run を捨てる」ためのものであって「いま終わった run を人の目から隠す」ためのものではない。"""
    if (not cfg.cleanup or cfg.git_bus or cfg.state_git
            or daemon_running(cfg, use_git=False)):
        return
    shutil.rmtree(cfg.bus / "inbox", ignore_errors=True)   # local run では使わない submit キュー
    runs = cfg.bus / "runs"
    if not runs.is_dir():
        return
    keep = max(0, int(cfg.bus_keep_runs))
    try:
        dirs = sorted((d for d in runs.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    except OSError:
        return
    for d in dirs[keep:]:                                  # 新しい順に keep 件を残して捨てる
        shutil.rmtree(d, ignore_errors=True)


def exit_code_for(result: dict) -> int:
    counts = result["counts"]
    if counts["blocked"] > 0 or counts.get("review", 0) > 0 \
            or counts.get("proposed", 0) > 0:   # 人の対応待ち（判断 / 検収承認 / 実行前レビュー）
        return 1
    if result["reason"] in (REASON_DRAINED, "report"):         # 正常停止（消化完了 or 計画報告）
        return 0
    return 2


# ---------------------------------------------------------------------------
# watch（終了条件後もプロセス常駐。エージェントは待機しない＝idle 中は起動しない）
# ---------------------------------------------------------------------------
def has_work(cfg: Config) -> bool:
    """次パスを起こすべき仕事があるか（新規/実行待ちタスク or フィードバック）。安価な FS 走査のみ。

    起床の条件は「そのパスで実際に処理できる仕事があるか」でなければならない。commands/ を
    ingest_commands と同じ述語（_read_command）で見るのはそのため: 取り込めない指示で起こすと、
    何も処理しないまま charter を再評価するパスが生まれ、承認済みマイルストーンが復活する。"""
    for t in load_tasks(cfg.backlog):
        # offloaded は「機械が委譲実行中・結果待ち」＝次パスでポーリングして回収するため起こす
        if t.norm_status() in CONSUMABLE or t.norm_status() in ("inbox", "offloaded"):
            return True
    if cfg.inbox and cfg.inbox.exists() and any(cfg.inbox.glob("*")):
        return True               # 外部ドロップ(inbox/)が来たら起こす
    cdir = commands_dir(cfg)
    # ingest_commands と同じ条件（読めること）。読めない書きかけでは起こさない＝起きたパスは
    # 必ずその指示を処理できる（起床と取り込みの食い違いを作らない）。
    if cdir.exists() and any(_read_command(f)[0] is not None for f in cdir.glob("*.json")):
        return True               # 人の指示ドロップ(commands/)が来たら起こす
    if replan_request_path(cfg).exists():
        return True               # バックログ再分解の要求が来たら起こす（次パスで plan を強制）
    if cfg.needs.exists():
        for nf in cfg.needs.glob("*.md"):
            # ingest_feedback と同じ条件（確定 [x]・静穏化済み）。本文の有無だけで起こすと、
            # 書きかけのまま毎パス起床して何も取り込まない空振りを繰り返す。
            if feedback_submitted(nf) and settled(cfg, nf):
                return True
    return False


def _has_pending_input(cfg: Config) -> bool:
    """パス途中に取り込むべき人の入力があるか（commands/ ドロップ or needs の確定記入）。
    安価な FS 走査のみ（has_work の入力側サブセット。タスクの有無は見ない）。"""
    cdir = commands_dir(cfg)
    if cdir.exists() and any(cdir.glob("*.json")):
        return True
    if cfg.needs.exists():
        for nf in cfg.needs.glob("*.md"):
            try:
                if feedback_submitted(nf):
                    return True
            except OSError:
                continue
    return False


def run_watch(cfg: Config, act=act_via_kiro_flow, ranker=None, sleeper=time.sleep,
              max_passes=None, heartbeat=None) -> dict:
    passes = 0
    last: dict = {}
    while True:
        if is_paused(cfg):           # pause 中はパスを起こさない（resume/stop の指示待ち）
            append_journal(cfg.journal, "=== watch: 一時停止中（resume/stop 待ち。エージェント非起動）===")
            write_status(cfg)        # paused をリモート viewer へ知らせる
        else:
            last = run_loop(cfg, act, ranker, sleeper)
            passes += 1
            if heartbeat:
                heartbeat()          # 各パスで生存信号を更新（共有レジストリ越しのリモート発見用）
            c = last["counts"]
            print(f"[watch] pass {passes}: reason={last['reason']} "
                  f"done={c['done']} blocked={c['blocked']}", flush=True)
            if last["reason"] == REASON_THROTTLE and cfg.level != "report":
                cfg.level = "report"  # ソフト予算超過 → 以降は report 降格（spend を止め監視は継続）
                print("[watch] throttle: ソフト予算超過につき report レベルへ降格（act 停止）", flush=True)
                append_journal(cfg.journal, "=== watch: throttle 降格（report・act 停止）===")
                write_status(cfg)    # 直近パスの生存信号は降格前の level だったため上書きしておく
            if max_passes is not None and passes >= max_passes:
                return last
            append_journal(cfg.journal, "=== watch: 監視中（新規タスク/フィードバック待ち。"
                                        "エージェントは待機しない）===")
        while is_paused(cfg) or not has_work(cfg):   # idle/pause: kiro-cli/flow は一切起動しない
            sleeper(cfg.poll)
            if heartbeat:
                heartbeat()          # idle 中も heartbeat を保ち、リモートから生存が見えるようにする
            if not is_paused(cfg):
                run_intake(cfg)      # 外部ゲートからの汲み上げ（間隔律速。積まれれば has_work が起こす）
            maybe_heartbeat_status(cfg)  # --status-interval のときだけ idle 中も生存信号を更新（既定は無効＝無干渉）
            state_sync(cfg)          # 状態 git: リモートの指示を取り込む（間隔律速。届けば has_work が起こす）
            if is_paused(cfg):
                ingest_commands(cfg)  # pause 中も resume/stop（と他の指示）は受け付ける
            if maybe_self_update(cfg):   # アイドル時のみ自己更新を確認・取り込み（取り込めたら再起動）
                raise _RestartRequested()


# ---------------------------------------------------------------------------
# 人の操作コマンド（いずれも案件毎の決定記録を残す）
# ---------------------------------------------------------------------------
def cmd_approve(cfg: Config, tid: str, reason: str) -> int:
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        # プロジェクト milestone の承認（収束候補 → done 確定）。backlog タスクではない。
        # 複数 charter 運用では project.json の charters マップから該当 charter を探す。
        data = load_project_state(cfg)
        candidates = [(None, data)] if data.get("id") else []
        for cname, st in (data.get("charters") or {}).items():
            candidates.append((cname, st))
        for cname, st in candidates:
            if st.get("id") != tid:
                continue
            status = str(st.get("status") or "")
            if status == REASON_PROJECT_CONVERGED:
                ch = _load_named_charter(cfg, cname)
                finalize_project(cfg, st, reason, charter=ch, charter_name=cname)
                print(f"プロジェクト done（承認・最終納品書）: {tid}")
                return 0
            if status == REASON_PROJECT_ACCEPTED:
                # 二度押し・取り込み遅延による再送は冪等に成功扱いする（.err に退避させない）
                print(f"プロジェクト {tid} は承認済み（accepted）です（何もしません）。")
                return 0
            # milestone の id には一致したが収束候補ではない＝実行中/ブロック等の古いカードからの
            # 承認。従来の「タスクが見つかりません」では原因が分からなかったため状態を明示する。
            print(f"エラー: プロジェクト {tid} は収束候補（converged）ではありません"
                  f"（現在: {status or '未実行'}）。実行中の再評価か needs のタスク対応を待って"
                  f"から承認してください。", file=sys.stderr)
            return 2
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    # 人手の承認はタスクを consumable/doing から確定遷移させる。worker のクラッシュや
    # review/blocked 滞留で残った古い claim ロック（claims/<id>.lock）を先に掃除しておく。
    # release_claim は冪等（無ければ no-op）なので、新鮮なロックが無い通常ケースでも無害。
    release_claim(cfg, t)
    if t.norm_status() == "proposed":
        # 実行前レビューの承認（実行を許可）。done 確定ではない
        _plan_approve(cfg, t, reason)
        print(f"plan-review: {tid} を承認しました（→ {t.status}）。")
        return 0
    if t.norm_status() == "review":
        # 成果物レビューの承認: タスク MR があれば Stage 2 と同一規則で自動決着（クリーンなら
        # マージ・未クリーンなら差し戻しコメントを付けて review のまま）。MR 無しは従来どおり
        mr_ok, mr_msg = finalize_task_mr(cfg, t)
        if not mr_ok:
            write_needs_file(cfg, t, f"承認されたが MR が未クリーン: {mr_msg}", review=True,
                             evidence=f"- MR: {t.get('mr_url', '')}")
            print(f"{tid}: MR が未クリーンのため done にできません（{mr_msg}）。"
                  f"解消後に再度 approve してください。", file=sys.stderr)
            return 1
        if mr_msg:
            print(f"{tid}: {mr_msg}")
        # 検収ゲートの承認 = done 確定（verify は実行済み。保持した成果参照で納品書を書く）
        ex = dict(t.extra)
        ref = ex.get("gate_ref", "")
        ts = ex.get("gate_ts") or _now_ts()
        vmsg = ex.get("gate_vmsg", "")
        gate_branch = ex.get("gate_branch", "")
        t.status = "done"
        autonomy_record(cfg, t, clean=True)          # 検収承認＝手戻りなし。track の信頼を上げる
        t.drop("gate_ref", "gate_ts", "gate_vmsg", "gate_branch")
        # review 時に保持した所在（ref/ブランチ）を受領書へ引き継ぐ（どこに成果物があるかを残す）
        gate_ev = (f"- 成果物: {ref}\n- 所在: {cfg.workdir}"
                   + (f" / ブランチ {gate_branch}" if gate_branch else "")) if ref else ""
        append_delivery(cfg, t, ref, ts, branch=gate_branch)
        disp = "done（承認・納品書）"
        if cfg.do_archive:
            archive_task(cfg, t, vmsg or f"承認: {reason}", ref, ts, evidence=gate_ev)
        else:
            delete_task_file(cfg, t)
            disp = "done（承認・削除）"
        clear_needs_file(cfg, tid)
        dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を検収承認",
                             action="approve-done", reason=reason, affects=f"{tid} → done",
                             # 承認理由を learn 化して類似案件の判断材料に残す（approve-and-fix と対称）
                             learn=(t.title, reason) if reason and cfg.learn_capture else None)
        print(f"{dr}: {tid} を承認し {disp} 確定しました。")
        # cohort の pilot 承認なら、固めた定義から残りのタスクを生成して ready にする
        if t.get("cohort_role") == "pilot":
            members = materialize_cohort_rest(cfg, t, feedback=reason)
            if members:
                print(f"cohort {t.get('cohort')}: 残り {len(members)} 件を生成しました "
                      f"（{', '.join(m.id for m in members[:6])}{' …' if len(members) > 6 else ''}）。")
        return 0
    t.status = "ready"
    persist_task(cfg, t)
    clear_needs_file(cfg, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を人の判断から復帰",
                         action="approve-and-fix", reason=reason, affects=f"{tid} → ready",
                         learn=(t.title, reason))
    print(f"{dr}: {tid} を ready に積み直しました。")
    return 0


def cmd_hold(cfg: Config, tid: str, reason: str) -> int:
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    append_policy(cfg.policy, "deny", tid)
    _block(cfg, t, f"hold（人が保留）: {reason}", {})
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} を保留（denylist 化）",
                         action="hold(deny)", reason=reason,
                         affects=f"{tid} → blocked, policy.deny += {tid}",
                         # hold 理由を avoid 化＝『この種は自動実行させない』予防知識として蓄積
                         # （policy.deny はこの id 限定だが、avoid は類似の新規タスクも投入時に捕まえる）
                         avoid=(t.title, reason) if reason and cfg.learn_capture else None)
    print(f"{dr}: {tid} を hold（policy.deny 追加）しました。")
    return 0


def cmd_reprioritize(cfg: Config, tid: str, kind: str, reason: str) -> int:
    append_policy(cfg.policy, kind, tid)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid} の優先度を変更",
                         action=f"reprioritize({kind})", reason=reason,
                         affects=f"policy.{kind} += {tid}")
    print(f"{dr}: {tid} を {kind}（policy.{kind} 追加）しました。")
    return 0


def cmd_replan(cfg: Config, reason: str, charter_name: str = "") -> int:
    """charter からのバックログ再分解を要求する（エラー回復用の一発の口）。
    次の project パスで plan を強制し、charter を分解し直して backlog の差分を投入する。
    冪等照合は「done 以外」（現行処理中のバックログ＋却下済み）と行う＝処理中タスクとの二重投入や
    却下済みの復活はさせず、done と類似のタスクだけやり直しとして再作成を許可する（エラー回復の口）。
    複数 charter 運用では charter_name でその charter だけを対象にできる（未指定はどの charter でも消化）。
    charter が無い（backlog ループ）プロジェクトでは再分解の対象が無いためエラー。"""
    charter = _load_named_charter(cfg, charter_name or None)
    if charter is None:
        print(f"エラー: charter がありません（再分解の対象なし）: {cfg.charter}", file=sys.stderr)
        return 2
    pid = _project_id(cfg, charter) + (f"-{charter_name}" if _is_multi_charter(cfg, charter_name) else "")
    write_replan_request(cfg, reason, charter=charter_name)
    dr = append_decision(cfg, pid, cfg.actor,
                         context=f"{charter.name}: charter からのバックログ再分解を要求",
                         action="replan", reason=reason,
                         affects="次パスで charter を再分解（現行処理中のバックログと重複するものは"
                                 "投入しない。done と同種のやり直しは再作成する）")
    print(f"{dr}: charter からのバックログ再分解を要求しました（次パスで反映）。")
    return 0


# ---------------------------------------------------------------------------
# revise（人の即時フィードバック）— ループがブロックする前に、人が気づいた時点で
#   タスクの内容（title/verify/accept/依存 after/優先度 等）を修正し、自由記述の
#   feedback を次の act に必ず届ける口。needs（ループ起点・受動）の対になる能動ルート。
#   実行中（doing・新鮮なクレームあり）のタスクは `revised` マーカーを付けて予約し、
#   現在の試行の結果は確定させず（done にせず）修正内容で積み直す＝早い軌道修正。
# ---------------------------------------------------------------------------
REVISE_FIELDS = ("title", "priority", "verify", "accept", "after",
                 "note", "level", "track")
_CLEAR_VALUES = ("", "-", "none")      # フィールド削除の明示値（revise の置換規約）


def _claim_fresh(cfg: "Config", tid: str) -> bool:
    """claims/<id>.lock が新鮮（= 誰かが実行中）か。stale/欠損は False（実行者不在）。"""
    p = _claims_dir(cfg) / f"{tid}.lock"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (time.time() - float(rec.get("ts", 0) or 0)) <= _claim_ttl(cfg)


def _after_introduces_cycle(tasks: "list[Task]", start: "Task") -> bool:
    """start の after 依存を辿って start 自身へ戻るか（DAG を壊す循環の検知）。"""
    by_id = {t.id: t for t in tasks}
    seen: set = set()
    stack = list(task_deps(start))
    while stack:
        d = stack.pop()
        if d == start.id:
            return True
        if d in seen:
            continue
        seen.add(d)
        nxt = by_id.get(d)
        if nxt is not None:
            stack.extend(task_deps(nxt))
    return False


def _apply_revise_fields(t: Task, tasks: "list[Task]", fields: dict) -> "list[str]":
    """revise のフィールド編集を Task へ適用し、変更内容の一覧を返す。
    規約: 値が None のキーは触らない。''/'-'/'none' は削除（置換の明示規約）。
    ValueError = 人へ返す入力エラー（level 不正・after 循環/自己依存）。"""
    changes: list[str] = []
    for key in REVISE_FIELDS:
        if key not in fields or fields[key] is None:
            continue
        val = str(fields[key]).strip()
        if key == "title":
            if val and val != t.title:
                changes.append(f"title: {t.title} → {val}")
                t.title = val
        elif key == "priority":
            try:
                pv = int(val)
            except ValueError:
                raise ValueError(f"priority は整数で指定してください: {val!r}")
            if pv != t.priority:
                changes.append(f"priority: {t.priority} → {pv}")
                t.priority = pv
        elif key == "verify":
            v = _strip_code(val)
            if v.lower() in _CLEAR_VALUES:
                v = ""
            if v != t.verify:
                changes.append(f"verify: {v or '（削除）'}")
                t.verify = v
        else:                                   # extra フィールド（after/accept/note/level/track）
            if val.lower() in _CLEAR_VALUES:
                if t.get(key) is not None:
                    t.drop(key)
                    changes.append(f"{key}: （削除）")
                continue
            if key == "level" and val not in LEVELS:
                raise ValueError(f"level は {'/'.join(LEVELS)} のいずれかです: {val!r}")
            if val != t.get(key, ""):
                t.set(key, val)
                changes.append(f"{key}: {val}")
            if key == "after":
                deps = task_deps(t)
                if t.id in deps:
                    raise ValueError(f"after に自分自身は指定できません: {t.id}")
                if _after_introduces_cycle(tasks, t):
                    raise ValueError(f"after が循環します（DAG を壊すため拒否）: {val}")
    return changes


def recover_revised(cfg: "Config", tasks: "list[Task]") -> "list[str]":
    """実行側が settle できなかった `revised` マーカーの回収（クラッシュ後の自己回復）。
    doing かつ実行者不在（stale claim）は修正内容のまま ready に積み直す。
    実行中（新鮮なクレーム）は settle 側の積み直しに任せて触らない。
    それ以外に残ったマーカーは、内容が既にファイルへ反映済みのため落とすだけでよい。"""
    out: list[str] = []
    for t in tasks:
        if not t.get("revised"):
            continue
        st = t.norm_status()
        if st == "doing" and _claim_fresh(cfg, t.id):
            continue
        t.drop("revised")
        if st == "doing":
            release_claim(cfg, t)
            t.status = "ready"
            append_journal(cfg.journal, f"revise 回収: {t.id} を ready に積み直し（実行者不在）")
        persist_task(cfg, t)
        out.append(t.id)
    return out


def _print_impact_note(tasks: "list[Task]", tid: str) -> None:
    """revise/reject 時に、影響を受ける依存先（after 逆辺・推移）を人へ提示する。"""
    downs = dependents_of(tasks, tid)
    if downs:
        print(f"影響範囲（{tid} に依存するタスク・推移）: "
              + ", ".join(f"{t.id}[{t.norm_status()}]" for t in downs))


def cmd_revise(cfg: Config, tid: str, fields: dict, feedback: str, reason: str) -> int:
    """バックログのタスクを人が即時修正する（内容・依存・優先度＋feedback 注入。決定記録）。

      ready/inbox/draft  : 即時にファイルへ反映（次の選択・実行から効く）
      blocked/review     : 反映して ready に積み直す（needs 記入＋[x] と同じ復帰。needs は消す）
      doing（実行中）    : 反映して `revised` マーカーを付ける。実行側は現在の試行の結果を
                           確定せず（verify も done もしない）修正内容で積み直す
    done の確定には一切触れない（「done は verify のみが根拠」の不変条件を保つ）。"""
    tasks = load_tasks(cfg.backlog)
    t = next((x for x in tasks if x.id == tid), None)
    if t is None:
        print(f"エラー: タスクが見つかりません: {tid}", file=sys.stderr)
        return 2
    try:
        changes = _apply_revise_fields(t, tasks, fields or {})
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    fb = str(feedback or "").strip()
    if fb:
        t.drop("feedback")
        t.extra.append(("feedback", fb.replace("\n", " ⏎ ")))
        changes.append("feedback 注入")
    if not changes:
        print("エラー: 変更がありません（フィールドか --feedback を指定してください）", file=sys.stderr)
        return 2

    status = t.norm_status()
    doing = status == "doing" and _claim_fresh(cfg, tid)
    # rev は act 試行の世代番号（req_id に載る）。実行中の古い run に次の試行が
    # 合流しないよう、revise のたびに上げて新しい run を強制する。
    t.set("rev", int(str(t.get("rev", "0") or "0")) + 1)
    disp = ""
    if doing:
        t.set("revised", _now_ts())     # 実行側が settle 時に検知して積み直す（結果は確定しない）
        disp = "実行中のため現在の試行は確定せず、修正内容で積み直されます"
    elif status in ("blocked", "review", "doing"):   # doing でも実行者不在（stale claim）はここ
        release_claim(cfg, t)            # 残骸クレームの掃除（無ければ no-op）
        clear_needs_file(cfg, tid)
        if status == "review":
            autonomy_record(cfg, t, clean=False)     # 検収からの修正＝差し戻し（手戻り）
        t.status = "ready"
        disp = "ready に積み直しました"
    persist_task(cfg, t)
    affects = "; ".join(changes)
    dr = append_decision(cfg, tid, cfg.actor, context=f"{tid}（{t.title}）を人が修正（revise）",
                         action="revise", reason=reason or fb[:200] or "revise",
                         affects=(affects[:200] + (f"; {tid} → ready" if disp and not doing else "")),
                         learn=(t.title, fb) if fb else None)
    append_journal(cfg.journal, f"revise: {tid} — {affects}"
                   + ("（実行中→積み直し予約）" if doing else ""))
    print(f"{dr}: {tid} を修正しました（{affects}）。" + (disp and f"{disp}。"))
    _print_impact_note(tasks, tid)     # 依存先（after 逆辺・推移）を提示＝変更の影響範囲を人が辿れる
    return 0


# ---------------------------------------------------------------------------
# 指示のファイル取り込み（commands/<name>.json）
# ---------------------------------------------------------------------------
# CLI を実行できない環境（ビュアーが Windows・本体が WSL 内で稼働、など）から、
# approve / hold / reprioritize と同じ人の指示をファイルだけで渡すための口。
# inbox/（タスク投入）・needs/（フィードバック）と同じ push 型の入力契約で、
# watch がこの口を監視して起こす。実行は CLI と同一の関数へ委譲する
# （ロジックの二重実装はしない＝効果・決定記録 DR も CLI と同一）。

COMMAND_ACTIONS = ("approve", "hold", "pin", "defer", "revise", "reject")


def commands_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "commands"


def _reject_command(cfg: "Config", f: Path, why: str) -> None:
    """処理できない指示ファイルは .err に退避して journal に残す（無限再試行を防ぐ）。"""
    append_journal(cfg.journal, f"commands 取り込み失敗: {f.name}: {why}")
    try:
        f.rename(f.with_name(f.name + ".err"))
    except OSError:
        try:
            f.unlink()
        except OSError:
            pass


def _read_command(f: Path) -> "tuple[dict | None, str]":
    """指示ファイルを読む。(rec, why) を返す。rec が None なら why が理由（未完・不正）。

    「取り込めるか」の唯一の判定点。has_work（起床するか）と ingest_commands（処理するか）が
    同じ述語を共有するために切り出してある。両者が食い違うと、起床したのに取り込めないパスが
    生まれ、そのパスが charter を再評価して承認済みマイルストーンを書き直してしまう。"""
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"JSON 解析失敗: {e}"
    if not isinstance(rec, dict):
        return None, "オブジェクトではない"
    return rec, ""


def ingest_commands(cfg: "Config") -> "list[str]":
    """commands/*.json（{"command": "approve|hold|pin|defer|revise|replan|pause|resume|stop",
    "id": ..., "reason": ...}）を読み、CLI と同一のロジック（cmd_approve / cmd_hold /
    cmd_reprioritize / cmd_revise / cmd_replan）を実行する。
    revise は加えて title/priority/verify/accept/after/note/level/track/feedback キーを受ける。
    replan / pause / resume / stop はプロジェクト単位（id 不要）: replan は charter からの
    バックログ再分解を次パスに要求、pause/resume は watch の消化を一時停止/再開（監視は継続）、
    stop はプロセスの graceful 停止（リモート viewer が git 越しに操作する口）。
    処理できたらファイルを消す。実行した指示（"action:tid"）の一覧を返す。

    読める指示は watch 中でも即座に取り込む。debounce は「読めなかったファイル」だけの再試行
    猶予として使う（書きかけを .err へ飛ばして指示を失わないため）。読める指示まで debounce で
    先送りすると、has_work が起こしたパスで承認が取り込まれず、そのパスが charter を再評価して
    マイルストーンを書き直す＝承認したのに要対応が復活する。"""
    cdir = commands_dir(cfg)
    done: "list[str]" = []
    if not cdir.exists():
        return done
    for f in sorted(cdir.glob("*.json")):
        rec, why = _read_command(f)
        if rec is None:
            # 書きかけ（アトミックに置かれなかった指示）かもしれない。watch 中は debounce 秒だけ
            # 猶予を与えて次パスで読み直す。猶予後もダメなら .err へ退避する（再試行ループにしない）。
            if cfg.watch and cfg.debounce > 0 and (time.time() - f.stat().st_mtime) < cfg.debounce:
                continue
            _reject_command(cfg, f, why)
            continue
        action = str(rec.get("command", "")).strip()
        tid = str(rec.get("id", "")).strip()
        reason = str(rec.get("reason", "") or "").strip() or "commands/ からの指示"
        if action == "replan":
            # プロジェクト単位（id 不要）: charter からのバックログ再分解を要求する
            # （複数 charter 運用は "charter" キーで対象を絞れる）
            rc = cmd_replan(cfg, reason, str(rec.get("charter", "") or "").strip())
            if rc == 0:
                try:
                    f.unlink()
                except OSError:
                    pass
                append_journal(cfg.journal, f"commands 取り込み: replan（{f.name}）")
                done.append("replan:project")
            else:
                _reject_command(cfg, f, f"replan が失敗 (exit {rc})")
            continue
        if action in ("pause", "resume", "stop"):
            # プロジェクト単位のライフサイクル指示（id 不要）。リモート viewer の停止/回復の口。
            try:
                f.unlink()
            except OSError:
                pass
            append_journal(cfg.journal, f"commands 取り込み: {action}（{f.name}・理由: {reason}）")
            done.append(f"{action}:project")
            if action == "pause":
                try:
                    pause_path(cfg).write_text(json.dumps(
                        {"reason": reason, "paused_iso": _now_ts()},
                        ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
                write_status(cfg)
            elif action == "resume":
                try:
                    pause_path(cfg).unlink()
                except OSError:
                    pass
                write_status(cfg)
            else:                        # stop: graceful 停止（レジストリ後始末は cmd_run の finally）
                state_sync(cfg, force=True)   # 停止前に journal/status の変更を押し出す（best-effort）
                raise _StopRequested()
            continue
        if action not in COMMAND_ACTIONS or not tid:
            _reject_command(cfg, f, f"未知の指示: command={action!r} id={tid!r}")
            continue
        if action == "approve":
            rc = cmd_approve(cfg, tid, reason)
        elif action == "reject":
            rc = cmd_reject(cfg, tid, reason)
        elif action == "hold":
            rc = cmd_hold(cfg, tid, reason)
        elif action == "revise":
            fields = {k: rec[k] for k in REVISE_FIELDS if k in rec}
            rc = cmd_revise(cfg, tid, fields, str(rec.get("feedback", "") or ""), reason)
        else:
            rc = cmd_reprioritize(cfg, tid, action, reason)
        if rc == 0:
            try:
                f.unlink()
            except OSError:
                pass
            append_journal(cfg.journal, f"commands 取り込み: {action} {tid}（{f.name}）")
            done.append(f"{action}:{tid}")
        else:
            _reject_command(cfg, f, f"{action} {tid} が失敗 (exit {rc})")
    return done


def cmd_needs(cfg: Config) -> int:
    tasks = load_tasks(cfg.backlog)
    blocked, intake, review, proposed = human_worklist(tasks)
    print(render_digest(blocked, intake, {}, budget_stop=False, review=review, proposed=proposed))
    if blocked or review or proposed:
        print(f"（各案件の詳細・フィードバック欄: {cfg.needs}/<id>.md）")
    return 1 if (blocked or review or proposed) else 0


def _decision_action_tally(decisions_dir: Path) -> "dict[str, int]":
    """decisions/*.md の `- action  : X` を数える（ループ計測の素）。"""
    tally: dict[str, int] = {}
    if not decisions_dir.exists():
        return tally
    pat = re.compile(r"^- action\s*:\s*(?P<a>.+)$")
    for f in decisions_dir.glob("*.md"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = pat.match(line.strip())
            if m:
                a = m.group("a").strip()
                tally[a] = tally.get(a, 0) + 1
    return tally


def compute_stats(cfg: Config) -> dict:
    """archive・decisions・DELIVERY・backlog から決定的にループの KPI を集計する。"""
    tasks = load_tasks(cfg.backlog)
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t.norm_status()] = by_status.get(t.norm_status(), 0) + 1
    arch_dir = cfg.archive_dir()
    archived = sorted(arch_dir.glob("*.md")) if arch_dir.exists() else []
    arch_tasks = [parse_task(p.read_text(encoding="utf-8"), p.stem) for p in archived]
    deliv_rows = 0
    dp = Path(cfg.delivery) if cfg.delivery else None
    if dp and dp.exists():
        for line in dp.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("|") and not s.startswith("| id") and "---" not in s:
                deliv_rows += 1
    actions = _decision_action_tally(cfg.decisions)
    auto = actions.get("auto-resolve", 0) + actions.get("auto-adjudicate", 0)
    human = (actions.get("approve-done", 0) + actions.get("approve-and-fix", 0)
             + actions.get("hold(deny)", 0) + actions.get("feedback-resume", 0))
    routed = auto + human
    done = len(archived)
    pending_human = (by_status.get("blocked", 0) + by_status.get("review", 0)
                     + by_status.get("proposed", 0))
    tok_total, usd_total = 0, 0.0                         # 納品書の `- cost: tokens=.. usd=..` を集計
    for t in arch_tasks:
        dt, du = parse_cost("@cost " + t.get("cost", ""))
        tok_total += dt
        usd_total += du
    return {
        "backlog_pending": len(tasks),
        "by_status": by_status,
        "pending_human": pending_human,                 # blocked + review（要対応）
        "done_archived": done,
        "delivery_rows": deliv_rows,
        "decisions_total": sum(actions.values()),
        "actions": actions,
        "auto_resolved": auto,                           # auto-resolve + auto-adjudicate
        "human_actions": human,
        "automation_rate": (auto / routed) if routed else None,  # 機械で捌けた割合
        "retries_pending_sum": sum(t.retries for t in tasks),
        "retries_archived_sum": sum(t.retries for t in arch_tasks),
        "first_pass_done": sum(1 for t in arch_tasks if t.retries == 0),  # 一発 done
        "tokens_archived": tok_total,                     # archive 済みタスクの累計コスト
        "cost_archived": round(usd_total, 4),
    }


# ---------------------------------------------------------------------------
# audit（Loop Readiness セルフ監査）— Loop Engineering の Loop Design Checklist /
#   Quick Red Flags を決定的に採点する。L0–L3 のレベルと 0–100 スコア・赤旗・提案を出し、
#   「いまどの自律度で無人運用してよいか」を機械判定する。stdlib のみ・エージェント不要。
# ---------------------------------------------------------------------------
def compute_audit(cfg: Config) -> dict:
    """backlog/policy/config/state を走査して Loop Readiness を採点する（決定的）。"""
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    protect = list(getattr(policy, "protect", []) or [])
    ready = consumable_tasks(tasks)
    # accept / verify_template を持つタスクは実行時に concrete な verify が用意されるので「verify 無し」に数えない
    # （detect_rot / run_loop S0 と整合させる）。
    ready_no_verify = [t.id for t in ready if not has_verify_plan(t)]
    has_cost_budget = bool(cfg.max_tokens) or bool(cfg.max_cost)
    near_cap = [t.id for t in ready if cfg.max_retries and t.retries >= cfg.max_retries]
    state_ok = cfg.decisions.exists() or cfg.journal.exists()
    handoff_ok = cfg.needs.exists()
    rot_hits = detect_rot(cfg, tasks) if cfg.rot else []   # rot on のときだけ走査

    # checks: id, label, ok, weight, min_level, severity, detail
    checks = [
        ("verify_coverage", "ready タスクは全て verify を持つ（鉄則）",
         not ready_no_verify, 25, 1, "critical",
         (f"verify 無し ready: {ready_no_verify[:8]}" if ready_no_verify else "OK")),
        ("verifier_independent", "verifier は実装者と別（決定的 verify＝rubber-stamp 不能）",
         True, 5, 1, "info", "verify は終了コードで判定（構造的に独立）"),
        ("finite_stop", "有限停止（max_cycles が有限）",
         cfg.max_cycles > 0, 10, 1, "critical",
         f"max_cycles={cfg.max_cycles} max_seconds={cfg.max_seconds}"),
        ("state_observability", "状態/観測（decisions・journal）",
         state_ok, 10, 1, "warn", "decisions/journal あり" if state_ok else "未作成"),
        ("attempt_cap", "リトライ上限→escalate（無限 fix ループ防止）",
         cfg.max_retries >= 0, 10, 2, "warn", f"max_retries={cfg.max_retries}"),
        ("human_handoff", "人へのエスカレーション先（needs/）",
         handoff_ok, 10, 2, "warn", "needs/ あり" if handoff_ok else "needs/ 未作成"),
        ("cost_budget", "コスト予算（max_tokens か max_cost）",
         has_cost_budget, 10, 3, "warn",
         f"tokens={cfg.max_tokens} usd={cfg.max_cost}" if has_cost_budget else "未設定（無人運用は要設定）"),
        ("safety_denylist", "パス保護デニーリスト（policy protect:）",
         bool(protect), 15, 3, "warn",
         f"protect={protect[:6]}" if protect else "未設定（.env/secrets/auth 等を守れていない）"),
        ("prune_state", "状態の掃除（--rot で古い/重複/実行不能を検知）",
         bool(cfg.rot), 5, 3, "info", "rot on" if cfg.rot else "rot off"),
    ]
    score = round(100 * sum(w for _, _, ok, w, *_ in checks if ok)
                  / sum(w for _, _, _, w, *_ in checks))

    # level: 各レベルの必須 check が全て ok か（下から積み上げ）
    def _lvl_ok(n):
        return all(ok for _id, _lbl, ok, _w, ml, sev, _d in checks if ml <= n and sev != "info")
    level = 0
    for n in (1, 2, 3):
        if _lvl_ok(n):
            level = n
        else:
            break

    red_flags = []
    if ready_no_verify:
        red_flags.append(("critical", f"verify を持たない ready タスク {len(ready_no_verify)} 件"
                                      "（拾われても escalate＝人手に逆流）"))
    if cfg.watch and not has_cost_budget:
        red_flags.append(("warn", "無人運用(watch)なのにコスト予算(max_tokens/max_cost)が未設定"))
    if cfg.watch and not protect:
        red_flags.append(("warn", "無人運用(watch)なのに保護パス(protect)が未設定"
                                  "（act が .env/secrets/auth を書き換え得る）"))
    if rot_hits:
        red_flags.append(("warn", f"rot（古い/重複/実行不能）{len(rot_hits)} 件を検知"))
    if near_cap:
        red_flags.append(("warn", f"リトライ上限間際のタスク {near_cap[:6]}（収束していない可能性）"))
    # L3 はクリティカル赤旗があれば認めない
    if level >= 3 and any(sev == "critical" for sev, _ in red_flags):
        level = 2

    suggestions = []
    for _id, lbl, ok, _w, ml, sev, _d in checks:
        if not ok and sev != "info":
            if _id == "cost_budget":
                suggestions.append("max_cost か max_tokens を設定（config か --max-cost/--max-tokens）")
            elif _id == "safety_denylist":
                suggestions.append("policy.md に protect: を追加（.env / **/secrets/** / auth/** など）")
            elif _id == "verify_coverage":
                suggestions.append("verify 無しの ready タスクに検証コマンドを与えるか inbox へ戻す")
            elif _id == "prune_state":
                suggestions.append("--rot を有効化して古い/重複タスクを掃除")
            else:
                suggestions.append(f"未達: {lbl}")

    return {
        "level": level, "level_label": f"L{level}", "score": score,
        "checks": [{"id": i, "label": l, "ok": ok, "min_level": ml,
                    "severity": sev, "detail": d}
                   for i, l, ok, _w, ml, sev, d in checks],
        "red_flags": [{"severity": s, "message": m} for s, m in red_flags],
        "suggestions": suggestions,
        "summary": {"ready": len(ready), "ready_no_verify": len(ready_no_verify),
                    "pending_human": sum(1 for t in tasks
                                         if t.norm_status() in ("blocked", "review")),
                    "watch": cfg.watch, "level": cfg.level},
    }


_LEVEL_MEANING = {0: "Draft（意図のみ）", 1: "Report（報告のみ・自動実行なし相当）",
                  2: "Assisted（検証つき小修正）", 3: "Unattended（無人運用可・人ゲート前提）"}


def cmd_audit(cfg: Config, as_json: bool = False, strict: bool = False) -> int:
    """Loop Readiness を採点して L0–L3・スコア・赤旗・提案を出す。--strict で CI ゲート化。"""
    a = compute_audit(cfg)
    if as_json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print("=== kiro-project audit（Loop Readiness）===")
        print(f"レベル : {a['level_label']} — {_LEVEL_MEANING[a['level']]}")
        print(f"スコア : {a['score']}/100")
        print("チェック:")
        for c in a["checks"]:
            mark = "✓" if c["ok"] else ("✗" if c["severity"] == "critical" else "−")
            print(f"  [{mark}] L{c['min_level']} {c['label']} … {c['detail']}")
        if a["red_flags"]:
            print("赤旗:")
            for r in a["red_flags"]:
                print(f"  ⚠ [{r['severity']}] {r['message']}")
        if a["suggestions"]:
            print("提案:")
            for s in a["suggestions"]:
                print(f"  → {s}")
    has_critical = any(r["severity"] == "critical" for r in a["red_flags"])
    if strict and (a["score"] < 40 or has_critical):
        return 2
    return 0


# ---------------------------------------------------------------------------
# doctor（稼働診断）— ログ/状態/環境から稼働状況を kiro-cli に診断させ、原因を
#   env（ユーザー環境固有）/ config（設定）/ program（プログラム上の不具合）へ分類する。
#   env・config は（--fix で）決定的に修正し、program は gitlab-idd スキルでイシュー起票する
#   （スキルが無ければ起票文面を出力するだけ）。知能（診断・分類・起票文面）は kiro-cli へ委譲し、
#   収集・修正・起票の駆動は本体が決定的に行う（§1 不変条件: 知能は委譲・操作は決定的）。
# ---------------------------------------------------------------------------
_DOCTOR_CATEGORIES = ("env", "config", "program")
_DOCTOR_SEVERITIES = ("critical", "warn", "info")
_DOCTOR_DEFAULT_PROTECT = ["**/.env", "**/secrets/**", "auth/**", "payments/**", "**/migrations/**"]


def _tail_text(path: "Path | None", n_lines: int = 40, n_chars: int = 2000) -> str:
    """ファイル末尾を有界に読む（無ければ空）。診断文脈の注入用。"""
    if not path or not Path(path).exists():
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n_lines:])[-n_chars:]


def doctor_env_findings(cfg: "Config", which=shutil.which) -> "list[dict]":
    """環境/設定の決定的チェック（LLM 不要）。fix_action を持つものは --fix で修正できる。"""
    findings: list[dict] = []
    needs_cli = cfg.planner == "agent" or cfg.executor == "agent" or cfg.auto_adjudicate
    agent_bin = _AGENT_CLI_BINARIES.get(cfg.agent_cli, cfg.agent_cli)
    if needs_cli and not which(agent_bin):
        findings.append({
            "category": "env", "severity": "critical",
            "title": f"{agent_bin} が PATH に見つからない",
            "evidence": (f"planner={cfg.planner} executor={cfg.executor} "
                         f"auto_adjudicate={cfg.auto_adjudicate} agent_cli={cfg.agent_cli} は "
                         f"{agent_bin} を要求する"),
            "fix": f"{agent_bin} をインストールして PATH を通す（暫定回避は --planner none / --executor stub）"})
    if cfg.executor != "stub" and not (cfg.kiro_flow or which("kiro-flow")):
        findings.append({
            "category": "env", "severity": "warn",
            "title": "kiro-flow が見つからない（PATH / --kiro-flow / 同梱のいずれにも無い）",
            "evidence": f"act(local run) の委譲先 kiro-flow を解決できない（executor={cfg.executor}）",
            "fix": "kiro-flow を PATH に置くか --kiro-flow で実体を指定する"})
    if not which("git"):
        findings.append({
            "category": "env", "severity": "warn", "title": "git が見つからない",
            "evidence": "成果参照・$KIRO_BASE_REV 差分 verify・回帰巻き戻しに git を使う",
            "fix": "git をインストールして PATH を通す"})
    elif not (cfg.workdir / ".git").exists():
        findings.append({
            "category": "env", "severity": "info", "title": "workdir が git リポジトリでない",
            "evidence": f"workdir={cfg.workdir} に .git が無い",
            "fix": "成果物リポジトリ上で実行するか、charter の repos に owns: を付けて route で書込先を割り当てる"})
    missing = [str(d) for d in (cfg.backlog, cfg.needs, cfg.decisions) if not d.exists()]
    if missing:
        findings.append({
            "category": "config", "severity": "warn", "title": "必須ディレクトリが未作成",
            "evidence": "未作成: " + ", ".join(missing),
            "fix": "backlog / needs / decisions を作成する", "fix_action": "create-dirs"})
    return findings


def doctor_audit_findings(cfg: "Config") -> "list[dict]":
    """compute_audit の未達チェックを config カテゴリの finding に変換（決定的）。"""
    a = compute_audit(cfg)
    out: list[dict] = []
    for c in a["checks"]:
        if c["ok"] or c["severity"] == "info":
            continue
        f = {"category": "config",
             "severity": "critical" if c["severity"] == "critical" else "warn",
             "title": f"監査未達: {c['label']}", "evidence": c["detail"], "fix": ""}
        if c["id"] == "safety_denylist":
            f["fix"] = "policy.md に protect: を追加（.env / **/secrets/** / auth/** など）"
            f["fix_action"] = "policy-protect"
        elif c["id"] == "verify_coverage":
            f["fix"] = "verify 無しの ready タスクに検証コマンドを与えるか inbox へ戻す"
        elif c["id"] == "cost_budget":
            f["fix"] = "max_cost か max_tokens を設定（config か --max-cost/--max-tokens）"
        elif c["id"] == "finite_stop":
            f["fix"] = "max_cycles を正の値にする（有限停止の鉄則）"
        elif c["id"] in ("state_observability", "human_handoff"):
            f["fix"] = "decisions / journal / needs を作成する（run か doctor --fix で自動作成）"
            f["fix_action"] = "create-dirs"
        else:
            f["fix"] = f"未達を解消する: {c['label']}"
        out.append(f)
    return out


def doctor_flow_bus_coverage_findings(cfg: "Config") -> "list[dict]":
    """このプロジェクトのバスに稼働中の kiro-flow daemon がいるかを確認し、不在を warn にする
    （未担当だと run が local 実行に落ち、夜間停止からの自動再開・gitlab 長期委譲の継続が効かない）。
    manage_flow_daemon が on なら kiro-project が自動起動するので通常は満たされ、
    起動失敗や off での起動忘れのときに気づける。鏡写しの落とし先があるバスだけ確認する。"""
    if project_flow_remote(cfg) is None:        # 鏡写しする対象のバスだけ見る
        return []
    managed = bool(getattr(cfg, "manage_flow_daemon", False))
    if daemon_running(cfg, use_git=False):
        return []
    fix = ("manage_flow_daemon: true を設定（kiro-project が自動起動）"
           if not managed else
           f"起動失敗の可能性。手動確認: kiro-flow --bus {cfg.bus} "
           f"--state-git <repo> daemon（subdir は kiro-flow.yaml の state_git_subdir。既定 "
           f"{FLOW_STATE_SUBDIR}）")
    return [{
        "category": "config", "severity": "warn",
        "title": "kiro-flow daemon 不在",
        "evidence": f"{cfg.bus} を担当する kiro-flow daemon が見つかりません"
                    "（run が local 実行に落ち、夜間停止からの自動再開・gitlab 長期委譲の継続が効きません）",
        "fix": fix,
    }]


def collect_doctor_signals(cfg: "Config") -> dict:
    """ログ/状態から診断材料を決定的に集める（kiro-cli へ渡す・有界）。"""
    tasks = load_tasks(cfg.backlog)
    blocked = [{"id": t.id, "title": t.title, "status": t.norm_status(),
                "retries": t.retries}
               for t in tasks if t.norm_status() in ("blocked", "review")][:20]
    recs: list[dict] = []
    if cfg.runlog and cfg.runlog.exists():
        for line in cfg.runlog.read_text(encoding="utf-8").splitlines()[-20:]:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    needs: list[str] = []
    if cfg.needs.exists():
        for p in sorted(cfg.needs.glob("*.md"))[:20]:
            head = next((ln[2:].strip() for ln in
                         p.read_text(encoding="utf-8", errors="replace").splitlines()
                         if ln.startswith("# ")), p.stem)
            needs.append(head)
    a = compute_audit(cfg)
    return {
        "stats": compute_stats(cfg),
        "audit": {"level": a["level"], "score": a["score"], "red_flags": a["red_flags"]},
        "runlog_tail": recs,
        "journal_tail": _tail_text(cfg.journal),
        "needs": needs,
        "blocked": blocked,
    }


def _doctor_prompt(signals: dict, deterministic: "list[dict]") -> str:
    sig = json.dumps(signals, ensure_ascii=False, indent=2)[:6000]
    det = json.dumps(deterministic, ensure_ascii=False, indent=2)[:2000]
    return (
        "あなたは自律バックログ・ループ（kiro-project）の稼働診断医です。以下のログ・状態・"
        "決定的チェック結果から、稼働の問題を洗い出し、それぞれを次の3カテゴリに分類してください。\n"
        "- env     : ユーザー環境固有（依存コマンド不在・権限・PATH・ネットワーク等）。修正可能。\n"
        "- config  : 設定の問題（予算未設定・保護パス未設定・verify 欠落・矛盾した設定等）。修正可能。\n"
        "- program : kiro-project 自体（や委譲先ツール）のプログラム上の不具合・想定外の例外・"
        "ロジックの欠陥。コード修正が必要でイシュー起票の対象。\n"
        "**判断は保守的に。** env/config で説明できるものを安易に program にしない。program は"
        "『正しい環境・正しい設定でも再現する不具合』に限る。\n\n"
        f"=== 決定的チェック（既出の所見・重複可）===\n{det}\n\n"
        f"=== 稼働シグナル（stats / audit / run-log / journal / needs / blocked）===\n{sig}\n\n"
        "出力は次の形の JSON 配列だけ（説明文なし。問題が無ければ [] ）:\n"
        '[{"category":"env|config|program","severity":"critical|warn|info",'
        '"title":"簡潔な要約","evidence":"根拠（どのログ/状態か）",'
        '"fix":"env/config は具体的な修正手順 / program は不具合の説明と再現条件"}]')


def _parse_doctor_findings(text: str) -> "list[dict] | None":
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arr, list):
        return None
    out: list[dict] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category", "")).strip().lower()
        if cat not in _DOCTOR_CATEGORIES:
            continue
        sev = str(it.get("severity", "warn")).strip().lower()
        out.append({
            "category": cat,
            "severity": sev if sev in _DOCTOR_SEVERITIES else "warn",
            "title": str(it.get("title", "")).strip()[:200],
            "evidence": str(it.get("evidence", "")).strip()[:600],
            "fix": str(it.get("fix", "")).strip()[:600],
            "source": "agent"})
    return out


def diagnose_with_agent(cfg: "Config", signals: dict, deterministic: "list[dict]",
                        kiro_run=None) -> "list[dict] | None":
    """kiro-cli に稼働を診断させ、分類済み finding の配列を得る。
    kiro-cli 不在・エラー・解析不能は None（＝決定的所見のみで続行）。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="doctor"))
    try:
        out = run(_doctor_prompt(signals, deterministic), cfg.model)
    except Exception:  # noqa: BLE001  kiro-cli 不在・タイムアウト等
        return None
    return _parse_doctor_findings(out)


def _dedupe_findings(findings: "list[dict]") -> "list[dict]":
    """(category, 正規化 title) で重複を畳む。決定的チェックを優先して残す。"""
    seen: dict = {}
    for f in findings:
        key = (f["category"], re.sub(r"\s+", " ", f.get("title", "").lower()).strip())
        if key not in seen:
            seen[key] = f
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(seen.values(),
                  key=lambda f: (_DOCTOR_CATEGORIES.index(f["category"]),
                                 order.get(f["severity"], 1)))


def find_skill(name: str, home: "str | None" = None) -> "Path | None":
    """名前付きスキルのディレクトリを探す（無ければ None）。検索順: $KIRO_SKILLS_HOME →
    cwd から上方向の .github/skills → ~/.claude/skills → ~/.github/skills。"""
    cands: list[Path] = []
    env = home or os.environ.get("KIRO_SKILLS_HOME")
    if env:
        cands.append(Path(env).expanduser() / name)
    cur = Path.cwd().resolve()
    for base in [cur, *cur.parents]:
        cands.append(base / ".github" / "skills" / name)
    cands.append(Path("~/.claude/skills").expanduser() / name)
    cands.append(Path("~/.github/skills").expanduser() / name)
    for c in cands:
        if c.is_dir():
            return c
    return None


def _ensure_policy_protect(cfg: "Config") -> str:
    """policy.md に protect: が一つも無ければ既定の保護デニーリストを追記する（決定的・冪等）。"""
    if load_policy(cfg.policy).protect:
        return ""
    cfg.policy.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if (cfg.policy.exists() and cfg.policy.stat().st_size > 0) else ""
    with cfg.policy.open("a", encoding="utf-8") as f:
        f.write(prefix + "# doctor: 既定の保護パス（無人運用の最低ライン）\n")
        for g in _DOCTOR_DEFAULT_PROTECT:
            f.write(f"protect: {g}\n")
    return ", ".join(_DOCTOR_DEFAULT_PROTECT)


def apply_doctor_fix(cfg: "Config", finding: dict) -> str:
    """env/config の finding を決定的に修正する。既知の fix_action のみ適用し、結果文を返す
    （未対応なら空文字＝提案の表示のみ）。"""
    act = finding.get("fix_action")
    if act == "create-dirs":
        ensure_dirs(cfg)
        return "backlog / needs / decisions を作成しました"
    if act == "policy-protect":
        added = _ensure_policy_protect(cfg)
        return f"policy.md に protect: を追加しました（{added}）" if added else ""
    return ""


def file_issues_via_gitlab_idd(cfg: "Config", program: "list[dict]", skill_dir: Path,
                               kiro_run=None) -> bool:
    """program カテゴリの不具合を gitlab-idd スキルのリクエスター役でイシュー起票させる
    （kiro-cli へ委譲）。成功で True、kiro-cli 不在・失敗で False。"""
    run = kiro_run or (lambda p, m: _run_kiro_cli(p, m, purpose="doctor"))
    items = "\n".join(
        f"{i}. {f['title']}\n   - 根拠: {f.get('evidence', '')}\n   - 詳細: {f.get('fix', '')}"
        for i, f in enumerate(program, 1))
    prompt = (
        "あなたは gitlab-idd スキルのリクエスター役です。kiro-project の稼働診断で見つかった"
        "『プログラム上の不具合』について、gitlab-idd スキルの手順に従い GitLab イシューを起票して"
        f"ください（スキル: {skill_dir}）。各不具合ごとに目的・再現条件・『## 受け入れ条件』を含む"
        "1 イシューを作成し、既に同一不具合のイシューがあれば重複起票しないこと。\n\n"
        f"=== 不具合一覧 ===\n{items}")
    try:
        run(prompt, cfg.model)
        return True
    except Exception:  # noqa: BLE001  kiro-cli 不在・失敗 → 起票せず（呼び出し側で出力）
        return False


def collect_flow_findings(cfg: "Config", fix: bool, runner=None) -> "list[dict]":
    """連携: 実行層 `kiro-flow doctor --json` を同じバスに対して実行し findings を取り込む。
    kiro-project の診断に kiro-flow（内側＝act の実体）の稼働所見を統合する。`--fix` のときは
    kiro-flow 側にも `--fix` を委譲し、kiro-flow が自分の env/config 修正と program 起票を行う
    （本体は kiro-flow 由来の finding を再修正・再起票しない＝二重作業を避ける）。
    cfg.with_flow が off・kiro-flow 不在・タイムアウト・解析不能は空で無害にスキップ。"""
    if not cfg.with_flow:
        return []
    cmd = _kf_base(cfg, bool(cfg.git_bus)) + ["doctor", "--json"]
    if fix:
        cmd.append("--fix")
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=600))
    try:
        proc = run(cmd)
        data = json.loads(getattr(proc, "stdout", "") or "")
    except Exception:  # noqa: BLE001  kiro-flow 不在・タイムアウト・JSON 解析失敗
        return []
    out: list[dict] = []
    for f in (data.get("findings", []) if isinstance(data, dict) else []):
        if not isinstance(f, dict) or f.get("category") not in _DOCTOR_CATEGORIES:
            continue
        g = dict(f)
        g["source"] = "kiro-flow"
        out.append(g)
    return out


def cmd_doctor(cfg: "Config", fix: bool = False, as_json: bool = False,
               kiro_run=None, skill_finder=find_skill, flow_finder=collect_flow_findings) -> int:
    """稼働を診断し env/config を（--fix で）修正、program は gitlab-idd で起票する。
    実行層 kiro-flow の doctor も連携実行し findings を統合する（cfg.with_flow 時）。
    終了コード: 0=健康 / 1=未解決の所見あり / 2=未解決の critical あり。"""
    # 決定的所見は ensure_dirs より前に集める（create-dirs 所見を消さないため）
    deterministic = (doctor_env_findings(cfg) + doctor_audit_findings(cfg)
                     + doctor_flow_bus_coverage_findings(cfg))
    for f in deterministic:
        f["source"] = "check"
    signals = collect_doctor_signals(cfg)
    agent = diagnose_with_agent(cfg, signals, deterministic, kiro_run=kiro_run)
    flow = flow_finder(cfg, fix) if cfg.with_flow else []   # 実行層 kiro-flow の所見を連携取得
    findings = _dedupe_findings(deterministic + (agent or []) + flow)

    applied: list[tuple] = []
    if fix:
        for f in findings:
            # kiro-flow 由来は kiro-flow 側で既に処理済み（再修正しない）
            if f["category"] in ("env", "config") and f.get("source") != "kiro-flow":
                msg = apply_doctor_fix(cfg, f)
                if msg:
                    f["resolved"] = msg
                    applied.append((f, msg))
        # 適用後に決定的チェックを取り直し、もう再現しない所見は『修正により解消』として畳む
        # （例: create-dirs は複数の監査未達を一度に解消する）。
        still = {(g["category"], re.sub(r"\s+", " ", g.get("title", "").lower()).strip())
                 for g in doctor_env_findings(cfg) + doctor_audit_findings(cfg)}
        for f in findings:
            if f.get("source") == "check" and not f.get("resolved"):
                key = (f["category"], re.sub(r"\s+", " ", f.get("title", "").lower()).strip())
                if key not in still:
                    f["resolved"] = "修正により解消"

    # program は本体由来のみ本体が起票（kiro-flow 由来は kiro-flow が起票済み）
    program = [f for f in findings
               if f["category"] == "program" and f.get("source") != "kiro-flow"]
    skill_dir = skill_finder("gitlab-idd")
    filed = False
    if fix and program:
        if skill_dir:
            filed = file_issues_via_gitlab_idd(cfg, program, skill_dir, kiro_run=kiro_run)
            if filed:
                for f in program:
                    f["resolved"] = f"gitlab-idd で起票（{skill_dir.name}）"

    if applied or filed:
        append_journal(cfg.journal,
                        f"doctor: env/config 修正 {len(applied)} 件 / "
                        f"program 起票 {'有' if filed else '無'}（program {len(program)} 件）")

    unresolved = [f for f in findings if not f.get("resolved")]
    has_critical = any(f["severity"] == "critical" for f in unresolved)

    if as_json:
        print(json.dumps({
            "agent_used": agent is not None,
            "skill_available": bool(skill_dir),
            "with_flow": cfg.with_flow,
            "flow_findings": len(flow),
            "fix": fix,
            "findings": findings,
            "applied": len(applied),
            "issues_filed": filed,
            "unresolved": len(unresolved),
        }, ensure_ascii=False, indent=2))
        return 2 if has_critical else (1 if unresolved else 0)

    print("=== kiro-project doctor（稼働診断）===")
    flow_note = f"  / kiro-flow 連携 {len(flow)} 件" if cfg.with_flow else ""
    print(f"診断: {'kiro-cli' if agent is not None else '決定的チェックのみ（kiro-cli 不在/解析不能）'}"
          f"  / 所見 {len(findings)} 件{flow_note}")
    if not findings:
        print("問題は見つかりませんでした（healthy）。")
        return 0
    label = {"env": "環境", "config": "設定", "program": "プログラム"}
    mark = {"critical": "✗", "warn": "−", "info": "·"}
    for cat in _DOCTOR_CATEGORIES:
        group = [f for f in findings if f["category"] == cat]
        if not group:
            continue
        print(f"\n[{label[cat]}] {len(group)} 件")
        for f in group:
            src = " [flow]" if f.get("source") == "kiro-flow" else ""
            print(f"  {mark.get(f['severity'], '−')} {f['title']}{src}")
            if f.get("evidence"):
                print(f"      根拠: {f['evidence']}")
            if f.get("fix"):
                print(f"      対処: {f['fix']}")
            if f.get("resolved"):
                print(f"      ✓ {f['resolved']}")
    print()
    if fix:
        print(f"修正: env/config {len(applied)} 件を適用。")
        if program:
            if skill_dir and filed:
                print(f"起票: program {len(program)} 件を gitlab-idd で起票しました。")
            elif skill_dir and not filed:
                print(f"起票: gitlab-idd への委譲に失敗（kiro-cli 不在等）。上記 program "
                      f"{len(program)} 件は未起票です。")
            else:
                print(f"起票: gitlab-idd スキルが見つからないため、program {len(program)} 件は"
                      f"出力のみ（イシュー未起票）。")
    else:
        print("（--fix で env/config の修正と program のイシュー起票を実行します）")
    return 2 if has_critical else 1


def cmd_runlog(cfg: Config, as_json: bool = False, tail: int = 10) -> int:
    """構造化 run-log（run-log.jsonl）の末尾を表示。運用判断（slow down/pause/kill）の土台。"""
    recs: list[dict] = []
    p = cfg.runlog
    if p and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    recs = recs[-tail:] if tail and tail > 0 else recs
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print("run-log がありません（run すると run-log.jsonl に1行ずつ記録されます）。")
        return 0
    print(f"=== run-log（最新 {len(recs)} 件）===")
    for r in recs:
        print(f"{r.get('ts', '?')}  reason={str(r.get('reason')):8s} "
              f"done={r.get('done', 0)} esc={r.get('escalations', 0)} "
              f"tokens={r.get('tokens', 0)} usd={r.get('cost', 0)} {r.get('duration_s', 0)}s")
    return 0


def cmd_stats(cfg: Config, as_json: bool = False) -> int:
    """ループの計測値を出す（スループット・自動化率・retry・人対応待ち）。回路調整の土台。"""
    s = compute_stats(cfg)
    if as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    rate = s["automation_rate"]
    rate_disp = f"{rate*100:.0f}%" if rate is not None else "—"
    fp = s["first_pass_done"]
    fp_disp = f"{fp}/{s['done_archived']}" if s["done_archived"] else "—"
    print("=== kiro-project stats ===")
    print(f"完了(archive)   : {s['done_archived']}（一発 done {fp_disp}）")
    print(f"納品(DELIVERY)  : {s['delivery_rows']}")
    print(f"未消化 backlog  : {s['backlog_pending']}  {s['by_status']}")
    print(f"人の対応待ち    : {s['pending_human']}（blocked + review）")
    print(f"自動解決/人対応 : {s['auto_resolved']} / {s['human_actions']}  → 自動化率 {rate_disp}")
    print(f"retry 累計      : pending {s['retries_pending_sum']} / archived {s['retries_archived_sum']}")
    print(f"コスト(archive) : tokens {s['tokens_archived']} / usd {s['cost_archived']}")
    print(f"決定記録        : {s['decisions_total']} 件  {s['actions']}")
    return 0


def cmd_rot(cfg: Config, fix: bool) -> int:
    tasks = load_tasks(cfg.backlog)
    rot = detect_rot(cfg, tasks)
    if not rot:
        print("rot は見つかりませんでした。")
        return 0
    print(f"rot を {len(rot)} 件検出:")
    for t, reason in rot:
        print(f"  {t.id}: {t.title} — {reason}")
        if fix:
            _block(cfg, t, f"rot: {reason}", {})
    if fix:
        print("→ いずれも人の判断（blocked）へ回しました。")
    return 1


def cmd_promote(cfg: Config) -> int:
    """効いた学習（decisions/ の learn）を ltm-use 長期記憶へ昇格（エージェント不要）。
    プロジェクト内の常時注入層（rules.md）への昇格も同時に行う。"""
    cfg.ltm = True   # promote は明示操作なので ltm を有効化
    rules = promote_rules(cfg)
    if rules:
        print(f"rules.md へ昇格: {', '.join(rules)}")
    mem_dir = ltm_memories_dir(cfg)
    promoted = promote_learnings(cfg)
    print(f"昇格先: {mem_dir}")
    if not promoted:
        hits = count_learn_hits(cfg)
        print(f"昇格対象なし（threshold={cfg.promote_threshold}・既存hits={hits or '無'}）。")
        return 0
    print(f"{len(promoted)} 件を昇格:")
    for src, memid in promoted:
        print(f"  decisions/{src} → {memid}")
    return 0


def cmd_enqueue(cfg: Config, args) -> int:
    """汎用の取り込み口。CLI フラグ・stdin/JSON から検証済み backlog タスクを作る。
    外部ソース（webhook/メール/issue 抽出）は薄いアダプタでここへ流し込む。"""
    ensure_dirs(cfg)
    if getattr(args, "json", False):
        try:
            raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
            data = json.loads(raw)
        except (OSError, ValueError) as e:
            print(f"enqueue 失敗: JSON 読込エラー: {e}", file=sys.stderr)
            return 2
        specs = data if isinstance(data, list) else [data]
    else:
        specs = [{"id": args.id, "title": args.title, "verify": args.verify,
                  "priority": args.priority, "source": args.source, "status": args.status,
                  "after": args.after, "review": args.review, "note": args.note,
                  "accept": args.accept, "verify_template": args.verify_template,
                  "repos": _coerce_repos(getattr(args, "repos", None)),
                  "cohort_items": _coerce_repos(getattr(args, "cohort_items", None))}]
    created = []
    for sp in specs:
        if not isinstance(sp, dict):
            print(f"enqueue 失敗: オブジェクトでない要素: {sp!r}", file=sys.stderr)
            return 2
        try:
            created.append(enqueue_task(cfg, sp))
        except ValueError as e:
            print(f"enqueue 失敗: {e}", file=sys.stderr)
            return 2
    for t in created:
        recalled = apply_intake_recall(cfg, t)   # 過去の hold に類似すれば実行前に人の判断へ
        if recalled:
            warn = f"  ⚠ 過去の hold に類似 → 人の判断へ（needs）: {recalled}"
        elif t.verify:
            warn = ""
        elif t.get("accept"):
            warn = "  （accept から実行時に verify を合成）"
        elif t.get("verify_template"):
            warn = "  ⚠ verify_template が未知 → inbox"
        else:
            warn = "  ⚠ verify 未定義 → inbox（人の triage へ）"
        print(f"enqueued {t.id} [{t.norm_status()}] {t.title}{warn}")
    return 0


def cmd_triage(cfg: Config) -> int:
    ensure_dirs(cfg)
    tasks = load_tasks(cfg.backlog)
    policy = load_policy(cfg.policy)
    for t in tasks:                              # 予防リコール: 過去 hold に類似する ready は実行前に人へ
        apply_intake_recall(cfg, t)              # 一致すれば blocked＋needs（_block が persist 済み）
    for t, why in triage(tasks, policy, cfg.plan_review):
        write_needs_file(cfg, t, why)
        persist_task(cfg, t)
    for t in tasks:
        persist_task(cfg, t)
    ensure_plan_review_needs(cfg, tasks)         # proposed に needs（実行前レビュー票）を用意
    order = prioritize(tasks, policy, cfg.planner, cfg.model)
    print("優先順位（消化対象）:")
    for i, t in enumerate(order, 1):
        print(f"  {i}. {t.id}: {t.title}")
    return 0


def _run_single(cfg: Config) -> int:
    """1 プロジェクトの単発実行（charter があれば目標駆動・無ければ backlog ループ）。要約を表示する。
    複数 charter（charters/）は全バージョンを順に 1 パスずつ回す。
    マスター憲章のみ（バージョン未作成）は分解せず backlog ループ＝タスク消化と指示の取り込みだけ行う。"""
    names = charter_names(cfg)
    if names:                                        # charter 駆動（plan→execute→evaluate）
        worst = 0
        for name in names:
            worst = max(worst, cmd_project(cfg, charter_name=name))
        reconcile_milestones(cfg)                    # milestone を status へ整合（唯一の調整点）
        return worst
    if _has_master_charter(cfg):
        print("[project] マスター憲章のみ（計画バージョン未作成）— 分解は行わず backlog を消化します。"
              "charters/<名前>.md にやるべきことを書くと計画が始まります。")
    result = run_loop(cfg)
    counts = result["counts"]
    if result.get("level") == "report":              # report: 消化せず計画だけ提示
        plan = result.get("plan", [])
        print(f"\n=== kiro-project report（level=report・実行なし）===")
        print(f"実行待ち {len(plan)} 件（この順で回す予定）:")
        for i, tid in enumerate(plan, 1):
            print(f"  {i}. {tid}")
        print(f"人の対応待ち: blocked={counts['blocked']} review={counts.get('review', 0)}")
        return exit_code_for(result)
    print(f"\n=== kiro-project 完了（project={cfg.project_name}）===")
    print(f"停止理由 : {result['reason']}（level={result.get('level')}）")
    print(f"サイクル : {result['cycles']}")
    print(f"done={counts['done']} blocked={counts['blocked']} ready={counts['ready']} "
          f"inbox={counts['inbox']} archived={result.get('archived', 0)} "
          f"ingested={len(result.get('ingested', []))} "
          f"promoted={len(result.get('promoted', []))}")
    return exit_code_for(result)


def cmd_run(cfg: Config) -> int:
    # 起動時に死んだインスタンスのゴミレコードを掃除する。前回の異常終了（kill -9 / クラッシュ /
    # マシン再起動）では finally が走らず *.json が残るため、自分を register する前に一掃して
    # instances の発見ノイズと start の偽の重複検出を防ぐ（prune は自ホストの死レコードを即削除）。
    list_instances(prune=True, extra=cfg.registry)
    ensure_dirs(cfg)
    reg = register_instance(cfg, cfg.registry)   # ローカル＋共有レジストリへ登録（リモート発見）
    hb = lambda: refresh_instance(reg)
    # watch はタスク実行中（エージェント CLI・kiro-flow run）に数分〜数十分ブロックする。
    # パス境界の hb だけでは心拍が TTL 切れし、外からは停止したように見えるため、実行中も
    # 打ち続ける別スレッドを立てる（単発 run は即終わるので不要）。
    hb_stop = _start_heartbeat_thread(cfg, reg) if cfg.watch else None
    try:
        # （再）起動直後は駆動より先にリモート状態を取り込む（停止中に viewer が push した
        # charter 更新/指示/フィードバックを、初回パスが古いローカル状態で読まないように）。
        state_sync(cfg)
        ensure_flow_daemon(cfg, cfg.flow_max_workers)   # 実行層 daemon の確保（opt-in・冪等）
        if cfg.watch:
            _install_sigterm()                   # stop の SIGTERM を KeyboardInterrupt 化（graceful 停止）
            # マスター憲章のみ（バージョン未作成）も project_watch へ: バージョン
            # （charters/<name>.md）が置かれた瞬間に charter 駆動へ入れる（run_watch は
            # charter の追加を監視しないため、ここで振り分けを間違えると気づけない）。
            if charter_names(cfg) or _has_master_charter(cfg):
                project_watch(cfg, heartbeat=hb)  # 目標を満たすまで回り続ける常駐（全 charter）
            else:
                run_watch(cfg, heartbeat=hb)      # backlog 監視の常駐
            return 0
        return _run_single(cfg)
    except (KeyboardInterrupt, _StopRequested):
        # stop(SIGTERM / commands の stop) / Ctrl-C: graceful 停止。finally でレジストリを掃除し 0 終了。
        if cfg.watch:
            print("\n=== kiro-project 停止（stop/SIGTERM/Ctrl-C 受信）===")
        return 0
    except _RestartRequested:
        # 自己更新を適用済み。finally でレジストリを掃除してから新しい本体へ exec する。
        print("\n=== kiro-project 自己更新を適用。graceful 再起動します ===")
    finally:
        if hb_stop is not None:
            hb_stop.set()          # レジストリを消す前に心拍を止める（無駄打ちを避ける）
        for p in reg:
            try:
                p.unlink()
            except OSError:
                pass
    # _RestartRequested 経由でここに到達（return 済みの正常/停止系は通らない）。後始末後に再起動。
    restart_self(_START_CWD)
    return 0


def _install_sigterm() -> None:
    """stop からの SIGTERM を KeyboardInterrupt 化して finally で後始末させる（watch 常駐用）。"""
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except (ValueError, OSError):  # メインスレッド以外/未対応では無視
        pass


# ---------------------------------------------------------------------------
# プロジェクト層（charter 駆動の plan→execute→evaluate ループ）
#   設計: docs/designs/kiro-project-design.md §6（プロジェクト層）
#   backlog の上に「目標→分解→消化→評価→改善」のもう一段を載せる。内側の正準ループ（run_loop）は
#   無改造で呼ぶ。done は acceptance(=verify) 全 PASS のみが根拠。知能（分解・敵対的レビュー）は
#   エージェントへ委譲し、本体は決定的なファイル操作（charter 解釈・enqueue・acceptance 実行・収束計算）
#   のみを担う。`project` を呼ばない限り従来挙動は完全不変。
# ---------------------------------------------------------------------------
REASON_PROJECT_CONVERGED = "converged"        # acceptance 全 PASS・改善ゼロ → milestone gate で人へ
REASON_PROJECT_ACCEPTED = "accepted"          # 人が milestone を承認（プロジェクト done）
REASON_PROJECT_BUDGET = "project-budget"      # 改善サイクル/内側予算の上限
REASON_PROJECT_COST = "project-cost"          # プロジェクト累計コスト上限
REASON_PROJECT_STALL = "no-progress"          # acceptance PASS 数が増えず人へ
REASON_PROJECT_BLOCKED = "blocked"            # 内側ループが人へエスカレーション
REASON_PROJECT_NO_ACCEPTANCE = "no-acceptance"  # acceptance 未定義（done 判定不能）→ 人へ（完了条件を足す）

# milestone として「人待ち」にできる status（承認できる converged と、対応が要る no-acceptance/
# blocked/stall/budget/cost）。これ以外（accepted/running）や、もう無いバージョンの milestone
# ファイルは reconcile_milestones が消す＝milestone は status の純粋な投影になり復活しない。
MILESTONE_STATUSES = frozenset({
    REASON_PROJECT_CONVERGED, REASON_PROJECT_STALL, REASON_PROJECT_BUDGET,
    REASON_PROJECT_COST, REASON_PROJECT_BLOCKED, REASON_PROJECT_NO_ACCEPTANCE,
})


@dataclass
class Charter:
    name: str = "project"
    goal: str = ""
    constraints: "list[str]" = field(default_factory=list)
    assumptions: "list[str]" = field(default_factory=list)
    deliverables: "list[str]" = field(default_factory=list)
    acceptance: "list[str]" = field(default_factory=list)   # 受入 verify（シェルコマンド）
    links: "list[str]" = field(default_factory=list)        # 横展開/参考リンク（見出し文字列）
    repos: "list[str]" = field(default_factory=list)        # 対象リポジトリ見出し（`name = url`。後方互換）
    repo_specs: "list[dict]" = field(default_factory=list)  # 構造化 repos: {name,url,desc,base,target}
    link_specs: "list[dict]" = field(default_factory=list)  # 構造化 links: {text,desc}（wiki/doc 等も可）
    # マスター憲章（`## master` セクション付き charter.md）: プロジェクト全体の普遍的な前提・制約。
    # それ自体はバックログへ分解されず、計画バージョン（charters/<name>.md）へ継承される。
    master: bool = False
    raw: str = ""


_CHARTER_NAME_RE = re.compile(r"^#\s+(?:Charter|憲章)\s*[:：]?\s*(?P<name>.+?)\s*$", re.M)
_CHARTER_SECTION_RE = re.compile(r"^##\s+(?P<key>[A-Za-z]+)\b")
# acceptance 行を自然言語として明示する接頭辞（`accept: …` / `受入: …`）。タスクの `accept:` と同じ流儀。
_ACCEPT_PREFIX_RE = re.compile(r"^(?:accept|受入|受入条件|自然文|自然言語)\s*[:：]\s*(?P<text>.+)$", re.I)


def _charter_bullets(lines: "list[str]") -> "list[str]":
    """`- ...` 行の中身を抽出（コードフェンス/バッククォートは剥がす）。空行・コメントは無視。"""
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        if s.startswith(("- ", "* ", "+ ")):
            out.append(_strip_code(s[2:].strip()))
        elif s.startswith(("-", "*", "+")) and len(s) > 1 and not s[1].isspace():
            out.append(_strip_code(s[1:].strip()))
    return [x for x in out if x]


def _bullet_text(s: str) -> str:
    """箇条書きマーカー（- * +）を剥がした本文（コード除去）。マーカー以外は空。"""
    if s.startswith(("- ", "* ", "+ ")):
        return _strip_code(s[2:].strip())
    if s[:1] in "-*+" and len(s) > 1 and not s[1].isspace():
        return _strip_code(s[1:].strip())
    return ""


def _charter_entries(lines: "list[str]") -> "list[dict]":
    """セクションを構造化エントリに分解する。最小インデントの箇条書きを「見出し」とし、
    より深くインデントした `- key: value`（`：` も可）をその見出しの属性（attrs）にする。
    旧来のフラットな箇条書き（サブ箇条なし）は、各行が属性なしの見出しになる（後方互換）。"""
    entries: "list[dict]" = []
    head_indent: "int | None" = None
    for line in lines:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        if s[:1] not in "-*+":
            continue
        body = _bullet_text(s)
        if not body:
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if head_indent is None or indent <= head_indent:
            head_indent = indent
            entries.append({"head": body, "attrs": {}})
        elif entries:                      # サブ箇条書き = 直近見出しの属性
            for sep in (":", "："):
                if sep in body:
                    k, v = body.split(sep, 1)
                    entries[-1]["attrs"][k.strip().lower()] = v.strip()
                    break
    return entries


# 構造化 charter の属性キー別名（日本語表記も受ける）
_REPO_KEY_ALIASES = {
    "desc": ("desc", "description", "説明", "内容", "内容物", "役割", "role"),
    "base": ("base", "base_branch", "ベース", "ベースブランチ"),
    "target": ("target", "target_branch", "ターゲット", "ターゲットブランチ"),
    "path": ("path", "dir", "folder", "subdir", "subpath",
             "パス", "ディレクトリ", "フォルダ", "サブディレクトリ"),
    "readonly": ("readonly", "read_only", "read-only", "ref", "reference",
                 "参照のみ", "参照", "読み取り専用", "読取専用"),
    "owns": ("owns", "own", "owned", "owns_paths", "paths",
             "所有", "担当", "管轄", "担当パス"),
    # 分類グロブ（repos スキーマの拡張キー。本体は使わず repos.json への書き出しで引き継ぐ）
    "docs": ("docs", "doc", "ドキュメント", "文書"),
    "tests": ("tests", "test", "テスト"),
    "code": ("code", "コード", "実装"),
}

# readonly フラグの真値表記（値なし＝キーだけ書いた場合も True 扱い）
_REPO_TRUTHY = {"", "true", "yes", "y", "1", "on", "参照のみ", "参照",
                "readonly", "read-only", "読み取り専用", "読取専用"}


def _entry_readonly(attrs: dict) -> bool:
    """repos エントリの参照のみ（readonly）フラグを判定する。`- readonly: true` /『- 参照のみ:』など。"""
    for alias in _REPO_KEY_ALIASES["readonly"]:
        if alias in attrs:
            return str(attrs[alias]).strip().lower() in _REPO_TRUTHY
    return False


def _entry_attr(attrs: dict, key: str) -> str:
    for alias in _REPO_KEY_ALIASES.get(key, (key,)):
        v = attrs.get(alias)
        if v:
            return str(v).strip()
    return ""


def _repo_spec_from_entry(e: dict) -> dict:
    """repos エントリ（見出し `name = url` ＋ 属性 desc/base/target/path/owns）を構造化する。
    target 省略時は base と同じ（同一ブランチで作業）。path はモノレポ内の作業フォルダ（任意）で、
    同一 URL を役割別に複数エントリへ分けるときの識別子になる。owns は担当パス（グロブ）の列で、
    ルーティング（タスク→書込先）の根拠になる。**owns 未指定＝参照リポジトリ**（書込先にしない）。"""
    name, url = _repo_token_parts(e["head"])
    desc = _entry_attr(e["attrs"], "desc")
    base = _entry_attr(e["attrs"], "base")
    target = _entry_attr(e["attrs"], "target") or base
    path = _entry_attr(e["attrs"], "path").strip("/")
    owns = [g for g in re.split(r"[,\s]+", _entry_attr(e["attrs"], "owns")) if g]
    # owns があれば書込先候補。owns 未指定は参照リポジトリ（readonly 明示と同義に倒す）。
    readonly = _entry_readonly(e["attrs"]) or not owns
    spec = {"name": name, "url": url, "desc": desc, "base": base,
            "target": target, "path": path, "readonly": readonly, "owns": owns}
    for k in ("docs", "tests", "code"):   # 分類グロブは repos スキーマへ損失なく引き継ぐ（本体は不使用）
        globs = [g for g in re.split(r"[,\s]+", _entry_attr(e["attrs"], k)) if g]
        if globs:
            spec[k] = globs
    return spec


def validate_charter(ch: "Charter") -> "list[str]":
    """構造化 repos の必須項目（desc・base）と、同一 URL を役割分割する際の規約を検証し、
    問題点の説明リストを返す（空＝OK）。
    『説明は原則必須・base は必須・target は省略可（既定 base）』に加え、**同じ URL を複数エントリで
    使う場合は path（作業フォルダ）か base/target（ブランチ）のいずれかで区別する**こと
    （どのフォルダ／どのブランチの役割かを曖昧にしない）。path も branch も全て一致するエントリだけを
    曖昧な重複として弾く。"""
    problems: list[str] = []
    by_url: "dict[str, list[dict]]" = {}
    for r in ch.repo_specs:
        label = r["name"] or r["url"] or "(無名 repo)"
        if not r["desc"]:
            problems.append(f"repo '{label}': 説明（desc）が必須です（内容物・関与範囲を 1 行で）")
        if not r["base"]:
            problems.append(f"repo '{label}': base ブランチが必須です（例 `- base: main`）")
        if r["url"]:
            by_url.setdefault(r["url"], []).append(r)
    for url, group in by_url.items():
        if len(group) < 2:
            continue                         # 単独エントリは path/branch 任意（後方互換）
        # 同一 URL のエントリは path（作業フォルダ）か base/target（ブランチ）で区別する。
        # どちらかが違えば別エントリとして成立し、3 つとも一致するものだけを曖昧な重複として弾く。
        seen: "dict[tuple, str]" = {}
        for r in group:
            label = r["name"] or url
            key = (r["path"], r["base"], r["target"])
            if key in seen:
                where = f"path '{r['path']}'" if r["path"] else "path 無し"
                problems.append(
                    f"repo '{label}': 同一 URL のエントリが {where}・base '{r['base']}'・"
                    f"target '{r['target']}' まで一致して '{seen[key]}' と重複しています"
                    "（path＝作業フォルダ か base/target＝ブランチ のいずれかで区別してください）")
            else:
                seen[key] = label
    return problems



def parse_charter(text: str) -> Charter:
    """charter.md を構造化する。`# Charter: <name>` と `## goal/constraints/assumptions/
    deliverables/acceptance` を読む。acceptance は受入 verify（1 行 1 コマンド）。決定的・LLM 不要。"""
    ch = Charter(raw=text)
    m = _CHARTER_NAME_RE.search(text)
    if m:
        ch.name = m.group("name").strip() or "project"
    sections: dict[str, list[str]] = {}
    cur: "str | None" = None
    for line in text.splitlines():
        sm = _CHARTER_SECTION_RE.match(line)
        if sm:
            cur = sm.group("key").lower()
            sections.setdefault(cur, [])
        elif cur is not None:
            sections[cur].append(line)
    ch.goal = "\n".join(l for l in sections.get("goal", []) if l.strip()).strip()
    ch.constraints = _charter_bullets(sections.get("constraints", []))
    ch.assumptions = _charter_bullets(sections.get("assumptions", []))
    ch.deliverables = _charter_bullets(sections.get("deliverables", []))
    ch.acceptance = _charter_bullets(sections.get("acceptance", []))
    # links: 構造化（見出し＋任意の desc）。wiki/doc/横展開先など何でも置ける。
    link_entries = _charter_entries(sections.get("links", []))
    ch.links = [e["head"] for e in link_entries]
    ch.link_specs = [{"text": e["head"], "desc": _entry_attr(e["attrs"], "desc")}
                     for e in link_entries]
    # repos: 構造化（見出し `name = url` ＋ desc/base/target）。後方互換で ch.repos は見出し列を維持。
    repo_entries = (_charter_entries(sections.get("repos", []))
                    or _charter_entries(sections.get("repositories", [])))
    ch.repos = [e["head"] for e in repo_entries]
    ch.repo_specs = [_repo_spec_from_entry(e) for e in repo_entries]
    # `## master` セクションの存在＝マスター宣言（中身は説明コメントで良い・パースしない）
    ch.master = "master" in sections
    return ch


def _repo_token_parts(token: str) -> "tuple[str, str]":
    """charter の repos 行 `name = url` または `url` を (name, url) に分解する。
    name 省略時は URL の末尾（.git 除く）を name とする。"""
    if "=" in token:
        name, url = token.split("=", 1)
        name, url = name.strip(), url.strip()
    else:
        name, url = "", token.strip()
    if not name:
        base = url.rstrip("/").split("/")[-1]
        name = base[:-4] if base.endswith(".git") else base
    return name, url


def charter_repo_map(ch: "Charter | None") -> "dict[str, str]":
    """charter の repos を {name: url} と {url: url} の両引きできる辞書にする。"""
    out: "dict[str, str]" = {}
    for token in (ch.repos if ch else []):
        name, url = _repo_token_parts(token)
        if url:
            out[name] = url
            out[url] = url
    return out


def repo_spec_map(specs: "list[dict]") -> "dict[str, dict]":
    """構造化 repos を {name: spec} と {url: spec} で両引きできる辞書にする。
    name はエントリ一意（モノレポの役割別フォルダも name で区別）。url は先勝ち（同一 URL の
    複数エントリは name で参照させる）。"""
    out: "dict[str, dict]" = {}
    for spec in specs:
        if not spec.get("url"):
            continue
        if spec.get("name"):
            out[spec["name"]] = spec
        out.setdefault(spec["url"], spec)
    return out


def charter_repo_spec_map(ch: "Charter | None") -> "dict[str, dict]":
    return repo_spec_map(ch.repo_specs if ch else [])


# ---------------------------------------------------------------------------
# repos レジストリ（schemas/repos.schema.json）— リポジトリ定義の独立スキーマ
#   <project>/repos.{yaml,yml,json} があればそれがレジストリの正。charter の ## repos は
#   互換入力（アダプタ）として残るが、内部的には同じ構造（repo_specs）へ正規化して引き回す。
#   両方あるときは repos ファイルが勝つ。repos ファイル単独では charter モード（目標駆動）は
#   発動しない（発動条件は charter.md の存在のまま）。
# ---------------------------------------------------------------------------
REPOS_FILE_NAMES = ("repos.yaml", "repos.yml", "repos.json")


def _read_structured(path: Path):
    """YAML（PyYAML 任意）/ JSON を読む（他ツールと同じフォールバック規約）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            pass
    return json.loads(text)


def _registry_entry(name: str, e: dict) -> dict:
    """repos スキーマの 1 エントリを内部の repo_spec 形へ正規化する（charter パースと同じ形）。"""
    owns = e.get("owns") or []
    if isinstance(owns, str):
        owns = [g for g in re.split(r"[,\s]+", owns) if g]
    base = str(e.get("base", "") or "")
    spec = {"name": str(name), "url": str(e.get("url", "") or ""),
            "desc": str(e.get("desc", "") or ""), "base": base,
            "target": str(e.get("target", "") or "") or base,
            "path": str(e.get("path", "") or "").strip("/"),
            "readonly": bool(e.get("readonly")) or not owns,
            "owns": [str(g) for g in owns]}
    for k in ("docs", "tests", "code"):   # 分類グロブ（repos スキーマの拡張キー）も引き回す
        v = e.get(k) or []
        if isinstance(v, str):
            v = [g for g in re.split(r"[,\s]+", v) if g]
        if v:
            spec[k] = [str(g) for g in v]
    return spec


def repo_registry_path(cfg: "Config") -> "Path | None":
    base = cfg.backlog.parent
    for name in REPOS_FILE_NAMES:
        p = base / name
        if p.is_file():
            return p
    return None


def _specs_from_registry(data) -> "list[dict]":
    specs: "list[dict]" = []
    if isinstance(data, dict):
        for name, e in data.items():
            if str(name).startswith("_"):            # "_" 接頭辞キーはメタデータ予約（_meta 等）
                continue
            if isinstance(e, dict):
                specs.append(_registry_entry(str(name), e))
    elif isinstance(data, list):                     # [{name: ..., url: ...}, ...] も許容
        for e in data:
            if isinstance(e, dict) and e.get("name"):
                specs.append(_registry_entry(str(e["name"]), e))
    return specs


def _registry_generated(data) -> bool:
    """repos ファイルが「charter からの自動生成物」か（_meta.generated_from マーカー）。"""
    return (isinstance(data, dict) and isinstance(data.get("_meta"), dict)
            and bool(data["_meta"].get("generated_from")))


def load_repo_registry(cfg: "Config") -> "list[dict] | None":
    """<project>/repos.{yaml,yml,json} を読んで repo_specs 形にする。無ければ None。
    壊れたファイルは警告して None（黙って空レジストリにせず charter へフォールバック）。"""
    p = repo_registry_path(cfg)
    if p is None:
        return None
    try:
        data = _read_structured(p)
    except (OSError, ValueError) as e:
        print(f"[kiro-project] repos レジストリを解釈できません: {p}: {e}", file=sys.stderr)
        return None
    return _specs_from_registry(data)


def export_repo_registry(cfg: "Config", specs: "list[dict]",
                         path: "Path | None" = None) -> None:
    """charter の ## repos を共通スキーマ（schemas/repos.schema.json）の repos.json へ書き出す。
    codd-gate 等の外部ツールへ**レジストリファイルとして渡す**ための派生物（codd-gate は charter を
    読まない）。_meta.generated_from を刻み、charter 変更のたび同期する（**正は charter のまま**。
    手で管理したくなったら _meta を消す＝以後は手書きが正で本体は上書きしない）。
    内容が同じなら書かない（ルーティング解決のたびに呼ばれるため）。"""
    path = path or (cfg.backlog.parent / "repos.json")
    entries: "dict[str, dict]" = {}
    for s in specs:
        e = {k: s[k] for k in ("url", "desc", "base", "target", "path") if s.get(k)}
        for k in ("owns", "docs", "tests", "code"):
            if s.get(k):
                e[k] = list(s[k])
        if s.get("readonly") and not s.get("owns"):
            e["readonly"] = True
        entries[s.get("name") or s.get("url") or f"repo{len(entries) + 1}"] = e
    payload = {"_meta": {"generated_from": "charter.md ## repos",
                         "note": "kiro-project が自動生成（正は charter）。手で管理するなら _meta を消す"},
               **entries}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def registry_specs(cfg: "Config", ch: "Charter | None") -> "list[dict]":
    """実効レジストリ: charter があればその repo_specs（load_charter が repos ファイルで
    上書き済み）、無ければ repos ファイル単独でも読める（ルーティング/参照用）。"""
    if ch is not None:
        return ch.repo_specs
    return load_repo_registry(cfg) or []


def _apply_repo_registry(cfg: "Config", ch: "Charter", allow_export: bool = True) -> "Charter":
    """repos レジストリ（手書きが正・自動生成物は charter に追従）を charter へ適用する。
    allow_export=False は charters/ 複数運用時: 各 charter の ## repos はその charter のルーティングに
    のみ効かせ、repos.json の自動生成（単一 charter が正の前提）は行わない。"""
    rp = repo_registry_path(cfg)
    if rp is not None:
        try:
            data = _read_structured(rp)
        except (OSError, ValueError) as e:
            print(f"[kiro-project] repos レジストリを解釈できません: {rp}: {e}", file=sys.stderr)
            return ch                                # 壊れた手書きは上書きせず charter のまま
        if _registry_generated(data):                # 自動生成物 → 正は charter・毎回同期
            if allow_export:
                if ch.repo_specs:
                    export_repo_registry(cfg, ch.repo_specs, rp)
                else:
                    rp.unlink(missing_ok=True)       # charter から repos が消えたら生成物も消す
            return ch
        specs = _specs_from_registry(data)
        if specs:                                    # 手書きレジストリが正・## repos は互換入力
            ch.repo_specs = specs
            ch.repos = [f"{s['name']} = {s['url']}" if s.get("name") else s["url"]
                        for s in specs if s.get("url")]
        return ch
    if allow_export and ch.repo_specs:               # レジストリ無し → charter から生成して外部ツールへ渡す
        export_repo_registry(cfg, ch.repo_specs)
    return ch


def load_charter(cfg: "Config") -> "Charter | None":
    p = cfg.charter
    if not p or not p.exists():
        return None
    ch = parse_charter(p.read_text(encoding="utf-8"))
    return _apply_repo_registry(cfg, ch)


# ---------------------------------------------------------------------------
# 複数 charter（charters/<name>.md）— 1 プロジェクトで複数バージョンの開発を並行管理する。
#   charters/ があれば各ファイルが 1 バージョン（stem が charter 名）で、全 charter を
#   ラウンドロビンで plan→execute→evaluate する。無ければ従来の charter.md（"default"）。
#   タスクには `charter: <name>` タグが付き、plan の冪等照合・drained 判定・acceptance
#   評価・milestone/state は charter 単位に閉じる（execute の run_loop は backlog 共有）。
# ---------------------------------------------------------------------------
def charters_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "charters"


# マスター宣言の軽量検知（`## master` セクション行）。charter_names は watch ループで高頻度に
# 呼ばれるため、フルパース（repos レジストリ適用・書き出し）を避けてテキストだけ見る。
_CHARTER_MASTER_RE = re.compile(r"(?m)^##\s+master\b", re.I)


def _has_master_charter(cfg: "Config") -> bool:
    """ルート charter.md がマスター憲章（`## master` 付き＝分解しない普遍の前提）か。"""
    if not (cfg.charter and cfg.charter.exists()):
        return False
    try:
        return bool(_CHARTER_MASTER_RE.search(cfg.charter.read_text(encoding="utf-8")))
    except OSError:
        return False


def charter_names(cfg: "Config") -> "list[str]":
    """駆動対象の charter 名一覧。charters/*.md（名前順）＞ 単一 charter.md（"default"）＞ 空。
    マスター憲章（`## master` 付き charter.md）は分解対象にしない: バージョンが無ければ空を返し、
    バージョン（charters/<name>.md）が置かれるとそれだけが駆動される（マスターは継承元）。"""
    d = charters_dir(cfg)
    if d.is_dir():
        names = sorted(f.stem for f in d.glob("*.md") if f.is_file())
        if names:
            return names
    if not (cfg.charter and cfg.charter.exists()):
        return []
    return [] if _has_master_charter(cfg) else ["default"]


def _merge_master_charter(cfg: "Config", ch: "Charter") -> "Charter":
    """マスター憲章（ルート charter.md・`## master` 付き）を計画バージョンへ継承する。
    バージョン側が空のフィールド（goal/deliverables/acceptance）はマスターで補い、
    制約・前提・links・repos はマスター∪バージョンで合成する。raw は両方を連結し、
    マスターの編集も再計画判定（plan signature）と accepted 再開判定（full signature）に効かせる。"""
    if not _has_master_charter(cfg):
        return ch
    base = load_charter(cfg)
    if base is None or not base.master:
        return ch
    if not ch.name or ch.name == "project":
        ch.name = base.name
    ch.goal = ch.goal or base.goal
    ch.deliverables = ch.deliverables or list(base.deliverables)
    ch.acceptance = ch.acceptance or list(base.acceptance)
    ch.constraints = base.constraints + [c for c in ch.constraints if c not in base.constraints]
    ch.assumptions = base.assumptions + [a for a in ch.assumptions if a not in base.assumptions]
    seen_links = {s.get("text") for s in ch.link_specs}
    for s in base.link_specs:
        if s.get("text") not in seen_links:
            ch.link_specs.append(dict(s))
            ch.links.append(s.get("text") or "")
    seen_repos = {(s.get("name"), s.get("url")) for s in ch.repo_specs}
    for i, s in enumerate(base.repo_specs):
        if (s.get("name"), s.get("url")) not in seen_repos:
            ch.repo_specs.append(dict(s))
            if i < len(base.repos):
                ch.repos.append(base.repos[i])
    ch.raw = base.raw + "\n\n" + ch.raw
    return ch


def _version_target_overrides(parsed: "Charter") -> "list[tuple[str, str, str, str]]":
    """バージョン charter の ## repos が明示する『base と異なる target』を抽出する。
    バージョン毎のターゲットブランチ（例 v1→release/1.x, v2→release/2.x）を、共有レジストリ
    （repos.json）を使っていても効かせるための材料。返り値は (name, url, path, target) の列。
    target が未指定、または target==base（＝作業ブランチと同じで既定）のエントリは含めない
    ＝共有レジストリの target を尊重する（後方互換。明示的にリリース先を分けた版だけ拾う）。"""
    out: "list[tuple[str, str, str, str]]" = []
    for s in parsed.repo_specs:
        t = str(s.get("target") or "").strip()
        b = str(s.get("base") or "").strip()
        if t and t != b:
            out.append((str(s.get("name") or ""), str(s.get("url") or ""),
                        str(s.get("path") or ""), t))
    return out


def _apply_version_target_overrides(
        ch: "Charter", overrides: "list[tuple[str, str, str, str]]") -> None:
    """共有レジストリ適用でバージョン charter の target が失われても、バージョンが明示した
    『base と異なる target』を実効 spec に復元する（バージョン毎のリリース先ブランチ）。
    マッチは name 一致、または (url, path) 一致（モノレポの役割別を取り違えないよう path も見る）。
    レジストリの url/path/owns/base 等（＝リポジトリ同一性・ルーティング根拠）は一切触らず、
    MR の宛先である target だけを差し替える＝ルーティングやクローン先には影響しない。"""
    if not overrides:
        return
    for spec in ch.repo_specs:
        sn = str(spec.get("name") or "")
        su = str(spec.get("url") or "")
        sp = str(spec.get("path") or "")
        for on, ou, op, t in overrides:
            if (on and on == sn) or (ou and ou == su and op == sp):
                spec["target"] = t
                break


def _load_named_charter(cfg: "Config", name: "str | None") -> "Charter | None":
    """charter 名 → Charter。charters/<name>.md があればそれ（複数運用・repos 自動生成なし。
    マスター憲章があれば継承合成する）、無ければ従来の charter.md へフォールバック（"default" や未指定）。"""
    if name:
        f = charters_dir(cfg) / f"{name}.md"
        if f.is_file():
            try:
                parsed = parse_charter(f.read_text(encoding="utf-8"))
                # 共有レジストリが ## repos を上書きする前に、バージョン毎 target を控えておく。
                overrides = _version_target_overrides(parsed)
                ch = _apply_repo_registry(cfg, parsed, allow_export=False)
            except (OSError, ValueError):
                return None
            _apply_version_target_overrides(ch, overrides)
            return _merge_master_charter(cfg, ch)
    return load_charter(cfg)


def load_charters(cfg: "Config") -> "list[tuple[str, Charter]]":
    """全 charter を (name, Charter) で返す（charters/ 無しは [("default", charter.md)]）。"""
    out: "list[tuple[str, Charter]]" = []
    for name in charter_names(cfg):
        ch = _load_named_charter(cfg, name)
        if ch is not None:
            out.append((name, ch))
    return out


def _is_multi_charter(cfg: "Config", name: "str | None") -> bool:
    """この charter 名が charters/ 複数運用のものか（milestone id の接尾辞・state のキー化に使う）。"""
    return bool(name) and (charters_dir(cfg) / f"{name}.md").is_file()


def task_charter_name(task: "Task") -> str:
    """タスクが属する charter 名（`- charter:` タグ。無ければ "default" 扱い）。"""
    return (task.get("charter") or "").strip() or "default"


def charter_for_task(cfg: "Config", task: "Task | None" = None) -> "Charter | None":
    """タスクの `charter:` タグから該当 charter を選ぶ（無ければ先頭/従来の charter.md）。
    ルーティング・文脈注入など「タスク単位で charter を引く」箇所の共通入口。"""
    name = (task.get("charter") or "").strip() if task is not None else ""
    if name:
        ch = _load_named_charter(cfg, name)
        if ch is not None:
            return ch
    chs = load_charters(cfg)
    return chs[0][1] if chs else None


def project_state_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / "project.json"


def load_project_state(cfg: "Config") -> dict:
    p = project_state_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def save_project_state(cfg: "Config", state: dict) -> None:
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    project_state_path(cfg).parent.mkdir(parents=True, exist_ok=True)
    project_state_path(cfg).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                       encoding="utf-8")


def load_charter_state(cfg: "Config", name: "str | None" = None) -> dict:
    """charter 単位の収束状態。複数運用（name あり）は project.json の {"charters": {name: …}}、
    単一運用（name 無し）は従来どおりトップレベル（後方互換）。"""
    data = load_project_state(cfg)
    if name:
        return dict((data.get("charters") or {}).get(name) or {})
    return data


def save_charter_state(cfg: "Config", state: dict, name: "str | None" = None) -> None:
    if not name:
        save_project_state(cfg, state)
        return
    data = load_project_state(cfg)
    data.setdefault("charters", {})[name] = state
    save_project_state(cfg, data)


# ---------------------------------------------------------------------------
# バックログ再分解の要求（人が charter から backlog を作り直したいときの一発の口）。
#   通常の再分解は「消化可能タスクが無い」か「charter が変わった」ときに自動で走るが、
#   タスクの取りこぼし・誤削除・plan 失敗などのエラー回復では charter が無変更のまま
#   backlog を作り直したい。project.json とは別のマーカーファイルにすることで、
#   cmd_project の通常の state 保存に上書きされず一発分だけ確実に効く（冪等・one-shot）。
#   再分解の冪等照合は「done 以外」（現行処理中のバックログ＋却下済み）と行う: 処理中タスクの
#   二重投入や却下済み（人の明示判断）の復活はさせず、done と類似のタスクだけやり直しとして
#   再作成を許す（通常 plan と違い、過去の完了実績が回復のための再分解を弾かないようにする）。
# ---------------------------------------------------------------------------
def replan_request_path(cfg: "Config") -> Path:
    return cfg.backlog.parent / ".replan.request"


def write_replan_request(cfg: "Config", reason: str, charter: str = "") -> None:
    """次パスの再分解要求マーカーを置く（人の明示アクション。冪等＝上書き）。
    charter を渡すとその charter だけを再計画対象にする（複数 charter 運用）。"""
    p = replan_request_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason or "", "actor": getattr(cfg, "actor", "") or "",
               "ts": datetime.now().isoformat(timespec="seconds")}
    if charter:
        payload["charter"] = charter
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def consume_replan_request(cfg: "Config", charter_name: "str | None" = None) -> "dict | None":
    """再分解要求マーカーがあれば読み取り、消して payload を返す（無ければ None）。one-shot。
    charter_name を渡したとき、要求が**別の charter 宛**ならマーカーを残して None を返す
    （その charter のパスで消化される）。charter 指定の無い要求はどの charter でも消化する。"""
    p = replan_request_path(cfg)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"reason": ""}
    target = str(payload.get("charter") or "").strip()
    if charter_name is not None and target and target != charter_name:
        return None                              # 別 charter 宛 → 残す
    try:
        p.unlink()
    except OSError:
        pass
    return payload


def _project_id(cfg: "Config", charter: "Charter") -> str:
    """milestone/state の id。プロジェクト名（ルートのディレクトリ名）を一次採用し、
    未設定なら charter 名から導出。Config を直接構築するテスト等（project_name 未設定）では
    従来どおり charter 名スラグになる（後方互換）。"""
    return getattr(cfg, "project_name", "") or _slug_id(charter.name) or "project"


def resolve_linked_projects(cfg: "Config", charter: "Charter") -> "list[tuple[str, Path]]":
    """charter の `## links` を他プロジェクト root へ解決する（横展開）。パスとして解決する:
    絶対パス、現プロジェクト root からの相対、または兄弟ディレクトリ名（root の親からの相対）。
    存在するものだけ返す（1 階層・自己/重複は無視）。"""
    out: list[tuple[str, Path]] = []
    proj_root = cfg.backlog.parent
    seen = {proj_root.resolve()}
    for link in charter.links:
        link = link.strip()
        if not link:
            continue
        p = Path(link).expanduser()
        cands = [p] if p.is_absolute() else [proj_root / link, proj_root.parent / link]
        for cand in cands:
            cand = cand.resolve()
            if cand.exists() and cand.is_dir() and cand not in seen:
                seen.add(cand)
                out.append((link, cand))
                break
    return out


def _existing_titles(cfg: "Config", charter: "str | None" = None,
                     active_only: bool = False) -> "list[str]":
    """重複投入の冪等照合に使う既存タイトル（backlog＋archive）。charter を渡すと
    その charter のタスク（タグ一致・タグ無しも含む）に照合を閉じる（複数 charter 運用で
    別バージョンの同名タスクを誤って弾かない）。
    active_only は照合を「done 以外」に絞る: 現行処理中の backlog に加え、archive の rejected
    （人の明示的な却下）だけは残す。再分解（replan）のエラー回復で、過去に done した同種タスクの
    再作成（やり直し）は許しつつ、却下済みタスクの復活は防ぐための口。"""
    def _match(t: "Task") -> bool:
        if not charter:
            return True
        tag = (t.get("charter") or "").strip()
        return tag in ("", charter)

    tasks = load_tasks(cfg.backlog)
    if active_only:
        titles = [t.title for t in tasks
                  if _match(t) and t.norm_status() != "done"]
    else:
        titles = [t.title for t in tasks if _match(t)]
    adir = cfg.archive_dir()
    if adir.exists():
        for p in adir.glob("*.md"):
            try:
                t = parse_task(p.read_text(encoding="utf-8"), p.stem)
            except (OSError, ValueError):
                continue
            if _match(t) and (not active_only or t.norm_status() == "rejected"):
                titles.append(t.title)
    return [t for t in titles if t]


def _is_duplicate(title: str, verify: str, existing: "list[str]", threshold: float) -> bool:
    """タイトルが既存と十分類似（Jaccard ≥ threshold）なら重複とみなす（plan/evaluate の冪等性）。"""
    return any(_title_overlap(title, e) >= threshold for e in existing)


def _extract_json_array(text: str) -> "list | None":
    """エージェント出力から最初の JSON 配列を取り出す（寛容パース）。"""
    depth, start = 0, -1
    for i, c in enumerate(text or ""):
        if c == "[":
            if depth == 0:
                start = i
            depth += 1
        elif c == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    v = json.loads(text[start:i + 1])
                    if isinstance(v, list):
                        return v
                except ValueError:
                    start = -1
    return None


def _coerce_repos(v) -> "list[str]":
    """エージェント出力の repos（list/str/None）を name/url の文字列リストへ正規化する。"""
    if not v:
        return []
    if isinstance(v, str):
        return [x for x in (s.strip() for s in re.split(r"[,\s]+", v)) if x]
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _coerce_titles(v) -> "list[str]":
    """after（先行タスクの title）の list/str/None を title 文字列リストへ正規化する。
    title は空白を含むため、文字列はカンマでのみ区切る（_coerce_repos の空白区切りは使えない）。"""
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).split(",") if s.strip()]


def build_charter_request(charter: "Charter") -> str:
    """charter を分解要求の文章に組み立てる（plan フェーズで kiro-flow/エージェントへ渡す）。"""
    parts = [f"プロジェクト目標: {charter.goal}"]
    if charter.constraints:
        parts.append("制約:\n" + "\n".join(f"- {c}" for c in charter.constraints))
    if charter.assumptions:
        parts.append("前提:\n" + "\n".join(f"- {a}" for a in charter.assumptions))
    if charter.deliverables:
        parts.append("成果物:\n" + "\n".join(f"- {d}" for d in charter.deliverables))
    if charter.acceptance:
        parts.append("受入条件(満たすべき検証):\n" + "\n".join(f"- {a}" for a in charter.acceptance))
    if charter.repo_specs:
        # 名前・フォルダ(path)・役割(desc) を提示し、プランナーが「役割に合うエントリ」を選べるようにする。
        # 同一 URL でも path/役割が違えば別エントリ＝別タスクに割り当てられる（モノレポの役割分割）。
        rlines = ["利用可能なリポジトリ（中身を読む/ push する必要があるタスクにのみ、その name で割当）:"]
        for r in charter.repo_specs:
            label = r["name"] or r["url"]
            line = f"- {label} = {r['url']}"
            tags = []
            if r.get("path"):
                tags.append(f"フォルダ {r['path']}")
            if r.get("readonly"):
                tags.append("参照のみ")
            if tags:
                line += "（" + "・".join(tags) + "）"
            if r["desc"]:
                line += f" — {r['desc']}"
            rlines.append(line)
        parts.append("\n".join(rlines))
    return "\n\n".join(parts)


def _charter_owns_note(charter: "Charter") -> str:
    """プランナーへ「どの repo がどのパスを担当（owns）するか」を伝える。書込先（workspace）選定の根拠。"""
    ws = [s for s in charter.repo_specs if s.get("owns")]
    refs = [s for s in charter.repo_specs if s.get("url") and not s.get("owns")]
    lines = []
    if ws:
        lines.append("書込先候補（owns＝担当パス。verify が操作するパスの owns を持つ repo を workspace にする）:")
        lines += [f"- {s.get('name') or s['url']}: owns {', '.join(s['owns'])}"
                  + (f" — {s['desc']}" if s.get("desc") else "") for s in ws]
    if refs:
        lines.append("参照リポジトリ（読むだけ。書込先にはしない）:")
        lines += [f"- {s.get('name') or s['url']}" + (f": {s['desc']}" if s.get("desc") else "")
                  for s in refs]
    return "\n".join(lines)


# バックログ分解の粒度指示（設定 granularity: coarse/fine/finest・既定 coarse）。
# 一般的なプロダクトバックログの書き方に合わせ、既定はユーザーストーリー相当
# （INVEST: 独立・価値・見積り可能・小さすぎない・検証可能）。kiro-flow の同名設定と
# 語彙を揃えている（あちらは実行時 DAG の分解、こちらは backlog の分解に効く）。
PLAN_GRANULARITY_DIRECTIVES = {
    "coarse": "各タスクはユーザーストーリー相当の**意味のある成果のかたまり**にすること"
              "（独立に着手でき、単体で価値ある成果になり、独立に検証できる = INVEST）。"
              "1 ファイル・1 関数・単一の実装手順のレベルまでは刻まない。目安はプロジェクト"
              "全体で 3〜10 件。関連する小さな変更は 1 タスクにまとめ、verify はそのタスクの"
              "受入を代表する検証に絞ること。",
    "fine": "各タスクは単機能・単モジュール程度の変更セットにすること（目安 8〜20 件）。",
    "finest": "各タスクは機械的に検証できる最小単位（1 ファイル/1 関数/1 観点）まで"
              "原子的に分解すること。",
}


def plan_granularity_directive(level: "str | None") -> str:
    """プランナーへ渡す分解粒度の指示文。未知値は既定（coarse）に倒す。"""
    return PLAN_GRANULARITY_DIRECTIVES.get(
        (level or "coarse").lower(), PLAN_GRANULARITY_DIRECTIVES["coarse"])


# ---------------------------------------------------------------------------
# リポジトリ理解の成果物化（repo-map・opt-in `repo_map`）
#   charter の書込先 repo ごとに context/<repo名>.md（構造・主要モジュール・ビルド/テスト
#   コマンド・規約）をエージェントに生成させ、HEAD sha を署名にキャッシュする。
#   生成だけが opt-in で、**読み出しは常時**（人が手書きした context/*.md も同じ口で
#   plan / act / verify 合成に注入される）。生成失敗は空のまま＝従来動作。
# ---------------------------------------------------------------------------
def context_dir(cfg: "Config") -> Path:
    return cfg.backlog.parent / "context"


_REPO_MAP_HEAD_RE = re.compile(r"^<!--\s*head:\s*(\S+)\s*-->")


def _repo_head_sha(url: str, branch: str = "") -> "str | None":
    """repo の先頭コミット SHA（branch 指定はそのブランチ・無指定は HEAD）。取得不能は None。"""
    if branch:
        return remote_branch_sha(url, branch)
    try:
        r = subprocess.run(["git", "ls-remote", url, "HEAD"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").split()
    return out[0] if r.returncode == 0 and out else None


def _repo_map_generate(cfg: "Config", spec: dict) -> str:
    """repo を一時 worktree に用意してエージェントに理解を要約させる（有界・失敗は空）。"""
    tmp = tempfile.mkdtemp(prefix="kiro-repomap-")
    dest = str(Path(tmp) / "repo")
    try:
        _clone_repo_shallow(spec["url"], spec.get("base") or "", dest)
        prompt = (
            f"ローカルのリポジトリ {dest} を調査し、次を Markdown で 2000 字以内に要約してください。\n"
            "- 構造（主要ディレクトリと役割）\n- 主要モジュールと責務\n"
            "- ビルド・テスト・リンタの実行コマンド\n- 命名・実装の規約（読み取れる範囲で）\n"
            "出力は要約本文のみ（前置き・後書きなし）。")
        return _run_kiro_cli(prompt, cfg.model, purpose="repo_map").strip()[:4000]
    except Exception:  # noqa: BLE001  clone 失敗・エージェント不在・タイムアウトは生成なし
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_repo_maps(cfg: "Config", charter: "Charter | None") -> None:
    """charter の書込先 repo ごとに context/<repo名>.md を用意する（plan の直前に呼ぶ）。
    HEAD sha が前回生成時と同じなら再生成しない（sha 不明でファイルが既にあれば温存＝
    無限再生成を避ける）。stub executor では生成しない（plan_via_stub と同じ扱い）。"""
    if not (cfg.repo_map and charter) or cfg.executor == "stub":
        return
    for spec in charter.repo_specs:
        if not spec.get("url") or spec.get("readonly"):
            continue
        name = _slug_id(spec.get("name") or spec["url"]) or "repo"
        path = context_dir(cfg) / f"{name}.md"
        sha = _repo_head_sha(spec["url"], spec.get("base") or spec.get("target") or "")
        if path.exists():
            try:
                m = _REPO_MAP_HEAD_RE.match(path.read_text(encoding="utf-8"))
            except OSError:
                m = None
            recorded = m.group(1) if m else ""
            if not sha or sha == recorded:
                continue                            # 変化なし（or 判定不能）は再生成しない
        body = _repo_map_generate(cfg, spec)
        if not body:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<!-- head: {sha or 'unknown'} -->\n"
                        f"# リポジトリ理解: {spec.get('name') or spec['url']}\n\n{body}\n",
                        encoding="utf-8")
        append_journal(cfg.journal, f"repo-map 生成: context/{name}.md（{spec['url']}）")


def repo_map_context(cfg: "Config", names: "list[str] | None" = None,
                     limit: int = 1500, max_files: int = 3) -> str:
    """context/*.md（リポジトリ理解・人の手書きも可）を有界に読み出す。names 指定はその repo
    のみ、None は全ファイル（先頭 max_files 件）。repo_map off でも既存ファイルは読む。"""
    cdir = context_dir(cfg)
    if not cdir.exists():
        return ""
    files = sorted(cdir.glob("*.md"))
    if names:
        wanted = {_slug_id(n) for n in names if n}
        files = [f for f in files if f.stem in wanted]
    parts: "list[str]" = []
    for f in files[:max_files]:
        try:
            txt = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        txt = _REPO_MAP_HEAD_RE.sub("", txt).strip()
        if txt:
            parts.append(txt[:limit])
    return "\n\n".join(parts)


def _plan_decompose_prompt(charter: "Charter", granularity: "str | None" = None,
                           context: str = "") -> str:
    return (
        "あなたはプロジェクトを実行可能なタスクに分解するプランナーです。以下の憲章を、"
        "それぞれ独立に検証できるタスクへ分解してください。"
        + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + (f"\n\n参考文脈（プロジェクトルール・リポジトリ理解。分解の粒度と verify の精度に使う）:\n{context}"
           if context else "")
        + "\n\n出力は JSON 配列のみ。各要素は {\"title\": str, \"verify\": str} で、verify は"
        " 終了コード0をPASSとみなすシェルコマンド（『履歴』でなく『望む最終状態/差分』を見ること）。"
        " タスク間に順序依存があれば **\"after\": [\"先行タスクの title\"]**（配列内の先行タスク・"
        "任意）を付けること（依存グラフとして実行順と並列性の判断に使われる。循環は不可）。"
        " 各タスクには **\"workspace\": \"name\"（唯一の書込先・必須）** を付ける。workspace は"
        " **verify が操作するパスの owns を持つリポジトリ**にすること。読むだけの他リポジトリは"
        " \"refs\": [\"name\", ...] に入れる（書込先にはしない）。"
        " 同じ手順を多数の対象に繰り返すタスクは 1 件ずつ列挙せず、"
        " {\"title\": \"…{item}…\", \"verify\": \"…{item}…\", \"cohort_items\": [\"対象1\", \"対象2\", …]} の"
        " 1 件にまとめること（{item} に各対象が差し込まれ、先頭を pilot として人が指示を固めてから残りが生成される）。"
        " 検証コマンドを書けない曖昧なタスクは含めないでください。")


def assign_plan_workspace(charter: "Charter", spec: dict) -> dict:
    """plan で生成した spec に**書込先 workspace を必ず明示**し、参照を refs に振り分ける。
    workspace = verify が操作するパスの owns を持つリポジトリ（プランナーが付けた workspace が
    owns を持つ書込先候補ならそれを尊重）。それ以外の charter repo・プランナーが挙げた repo は
    すべて参照（refs）として扱う。書込先が決まらなければ何も設定しない（route 層が後段で解決）。"""
    smap = charter_repo_spec_map(charter)
    workspaces = [s for s in charter.repo_specs if s.get("owns")]
    ws = None
    hint = _strip_code(str(spec.get("workspace") or ""))
    if hint and smap.get(hint) and smap[hint].get("owns"):     # プランナー指定（owns 持ち）を尊重
        ws = smap[hint]
    if ws is None:                                             # verify が操作するパスの owns で決定論的に確定
        paths = _split_tokens(spec.get("paths")) or _verify_paths(str(spec.get("verify") or ""))
        ws = _infer_workspace_from_paths(workspaces, paths)
    # 参照: 書込先以外の charter repo すべて＋プランナーが挙げた repos/refs（書込先 url は除く）
    ref_names: "list[str]" = []
    seen: "set[str]" = set()
    cand = list(charter.repo_specs)
    for tok in _coerce_repos(spec.get("refs")) + _coerce_repos(spec.get("repos")):
        sp = smap.get(tok) or _raw_url_spec(tok)
        if sp:
            cand.append(sp)
    for s in cand:
        url = s.get("url")
        if not url or (ws and url == ws["url"]) or url in seen:
            continue
        seen.add(url)
        ref_names.append(s.get("name") or url)
    spec.pop("repos", None)                                   # repos は廃止: workspace/refs へ置換
    if ws is not None:
        spec["workspace"] = ws.get("name") or ws["url"]
    if ref_names:
        spec["refs"] = ",".join(ref_names)
    return spec


def plan_via_agent(cfg: "Config", charter: "Charter") -> "list[dict]":
    """charter をエージェント（kiro-flow/kiro-cli）に分解させ、[{title, verify}, ...] を得る。
    知能は委譲し、取り込み（enqueue）は本体が決定的に行う。失敗時は空（plan を諦め人へ）。
    各タスクには書込先 workspace を必ず明示する（verify が操作するパスの owns を持つ repo）。"""
    ctx = "\n\n".join(x for x in (project_rules_context(cfg), repo_map_context(cfg)) if x)
    try:
        out = _run_kiro_cli(_plan_decompose_prompt(charter, cfg.granularity,
                                                   context=ctx), cfg.model, purpose="plan")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"project plan: 分解に失敗（{e}）")
        return []
    arr = _extract_json_array(out) or []
    specs = []
    for item in arr:
        if isinstance(item, dict) and str(item.get("title", "")).strip():
            sp = {"title": str(item["title"]).strip(),
                  "verify": _strip_code(str(item.get("verify", "") or "").strip()),
                  "workspace": _strip_code(str(item.get("workspace") or "").strip()),
                  "refs": _coerce_repos(item.get("refs")) or _coerce_repos(item.get("repos")),
                  "cohort_items": _coerce_repos(item.get("cohort_items")),
                  # 依存（先行タスクの title）。enqueue 後に id へ決定的に解決される（after_titles）
                  "after_titles": _coerce_titles(item.get("after")),
                  "source": "charter"}
            specs.append(assign_plan_workspace(charter, sp))
    return specs


def plan_via_stub(cfg: "Config", charter: "Charter") -> "list[dict]":
    """plan_via_agent の決定的代替（executor: stub 時のデフォルト planner）。エージェントを
    一切呼ばず、charter.acceptance（呼び出し時点で解決済み前提）をそっくり初期タスクにする。
    verify は人が charter に書いた受入条件そのもの。acceptance が無ければ空（呼び出し元の
    no-acceptance ゲートで人へ回る）。

    stub は goal の文章を読めないため、起票源は acceptance しかない。かつては acceptance を
    その場で実行して未達の項目だけを起票していたが、それだと初回から PASS する acceptance
    （`echo ok` 等）では起票がゼロになり、backlog が空のまま converged して「バージョンを足しても
    バックログが現れない」ことになっていた。plan は未達判定の場ではない（それは evaluate の役目）
    ので、ここでは初回未達とみなして全項目を起票する。二周目以降は _enqueue_specs が backlog と
    archive のタイトルで冪等に弾くため、同じ受入条件が積み直されることはない。"""
    if not charter.acceptance:
        return []
    return _acceptance_specs(list(charter.acceptance))


def review_via_stub(cfg: "Config", charter: "Charter") -> "list[dict]":
    """review_via_agent の決定的代替（executor: stub 時のデフォルト reviewer）。敵対的レビューは
    判断を要する性質上、決定的な代用を作らず常に所見なしを返す（--review-project は既定 opt-in
    off のため、stub 環境では何もしない＝acceptance PASS をそのまま信頼する）。"""
    return []


def _review_prompt(charter: "Charter", granularity: "str | None" = None) -> str:
    return (
        "あなたは成果物を批判的にレビューする敵対的レビュアです。以下の憲章の目標・成果物に対し、"
        "現状の成果物がまだ満たせていない点（短絡的達成・抜け漏れ・品質不足）を洗い出してください。"
        "改善タスクの粒度: " + plan_granularity_directive(granularity) + "\n\n"
        + build_charter_request(charter)
        + "\n\n" + _charter_owns_note(charter)
        + "\n\n出力は JSON 配列のみ。各要素は {\"title\": str, \"verify\": str,"
        " \"workspace\": \"name\"（唯一の書込先・必須。verify が操作するパスの owns を持つ repo）,"
        " \"refs\": [\"name\", ...]（読むだけの参照）}（改善タスクと検証）。"
        " 問題が無ければ空配列 [] を返してください。")


def review_via_agent(cfg: "Config", charter: "Charter") -> "list[dict]":
    """敵対的レビュー（opt-in）。成果物 vs 目標の不足を改善タスク [{title, verify}] として返す。
    plan と同様、各タスクに書込先 workspace を必ず明示する。"""
    try:
        out = _run_kiro_cli(_review_prompt(charter, cfg.granularity), cfg.model, purpose="review")
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        append_journal(cfg.journal, f"project review: レビューに失敗（{e}）")
        return []
    arr = _extract_json_array(out) or []
    specs = []
    for i in arr:
        if isinstance(i, dict) and str(i.get("title", "")).strip():
            sp = {"title": str(i["title"]).strip(),
                  "verify": _strip_code(str(i.get("verify", "") or "").strip()),
                  "workspace": _strip_code(str(i.get("workspace") or "").strip()),
                  "refs": _coerce_repos(i.get("refs")) or _coerce_repos(i.get("repos")),
                  "source": "review"}
            specs.append(assign_plan_workspace(charter, sp))
    return specs


def _enqueue_specs(cfg: "Config", specs: "list[dict]", existing: "list[str]",
                   threshold: float, charter: "str | None" = None,
                   active_only: bool = False) -> "list[Task]":
    """spec 群を冪等に backlog へ投入（既存と類似は飛ばす）。verify 無しは enqueue_task が inbox にする。

    冪等照合は「呼び出し時点のスナップショット ∪ 投入直前に読み直した現物」で行う。plan/review は
    エージェント委譲で数分かかるため、スナップショットだけだと、その間に投入されたタスク
    （別インスタンス・前パスの残り・state_git 同期で届いた分・リセット後に書き戻された残骸）が
    照合に無く、類似バックログを二重投入してしまう。
    active_only は読み直しも「done 以外」に絞る（replan のやり直し経路。スナップショット側の
    絞り込みと揃えないと、ここで done の archive タイトルが混ざり再作成が弾かれてしまう）。"""
    merged = list(existing) + _existing_titles(cfg, charter, active_only=active_only)
    created: list[Task] = []
    afters: "dict[str, list[str]]" = {}   # 新規タスク id → 先行タスクの title 群（後段で id へ解決）
    for sp in specs:
        title = str(sp.get("title", "") or "").strip()
        verify = str(sp.get("verify", "") or "").strip()
        if not title or _is_duplicate(title, verify, merged, threshold):
            continue
        wants = _coerce_titles(sp.pop("after_titles", None))  # 生 title を task に書かない（id が正）
        try:
            t = enqueue_task(cfg, sp)
            created.append(t)
            if wants:
                afters[t.id] = wants
            merged.append(title)
            existing.append(title)   # 呼び出し側スナップショットにも反映（同一パス内の連続呼び出し用）
        except ValueError:
            continue
    if afters:
        _resolve_after_titles(cfg, created, afters)
    return created


def _resolve_after_titles(cfg: "Config", created: "list[Task]",
                          afters: "dict[str, list[str]]") -> None:
    """plan が出した after（先行タスクの title）を id へ決定的に解決して persist する。
    照合は「今回作成分」を優先し、次に現役 backlog のタイトル完全一致。未知 title は落とし、
    循環を作る after はそのタスクの分ごと捨てる（DAG の健全性が優先・落とした事実は journal へ）。"""
    by_title = {t.title: t.id for t in load_tasks(cfg.backlog)}
    by_title.update({t.title: t.id for t in created})
    by_id_created = {t.id: t for t in created}
    # 循環判定のグラフは「backlog の現物 ＋ 今回作成分は同一インスタンス」（解決の途中経過を共有）
    all_tasks = [by_id_created.get(x.id, x) for x in load_tasks(cfg.backlog)]
    for t in created:
        deps: "list[str]" = []
        for w in afters.get(t.id) or []:
            tid = by_title.get(w)
            if tid and tid != t.id and tid not in deps:
                deps.append(tid)
        if not deps:
            continue
        prev = task_deps(t)
        t.set("after", ", ".join(dict.fromkeys(prev + deps)))
        if _after_introduces_cycle(all_tasks, t):
            if prev:
                t.set("after", ", ".join(prev))
            else:
                t.drop("after")
            append_journal(cfg.journal, f"plan の after を循環のため破棄: {t.id}")
        persist_task(cfg, t)


def _charter_single_repo(charter: "Charter") -> "dict | None":
    """charter が「成果を push する対象 repo」を 1 つだけ持つならその spec を返す（複数/0 は None）。
    参照のみ（readonly）repo は成果の出る先ではないので除外する。"""
    work = [r for r in charter.repo_specs if r.get("url") and not r.get("readonly")]
    return work[0] if len(work) == 1 else None


# --------------------------------------------------------------------------
# 共有 git キャッシュ + worktree（docs/designs/git-worktree-cache-pattern.md）
#   検証（verify/acceptance）のたびに対象 repo を浅 clone する代わりに、ホスト共有の bare ミラー
#   （--mirror --filter=blob:none）を 1 本持ち、最新化（fetch）後に detached worktree を temp へ生やす。
#   kiro-flow とミラー root を共有する（KIRO_GIT_CACHE_DIR / 既定 $TMPDIR/kiro-git-cache）。
#   不変条件: INV-1 鮮度（毎 fetch→fetch 後 SHA）/ INV-2 直列化・自己修復・gc.auto=0 /
#   INV-3 失敗時は従来の浅 clone へフォールバック。
# --------------------------------------------------------------------------
CLONE_RETRIES = 5
_CACHE_CORRUPT = ("not a git repository", "bad object", "corrupt", "broken link",
                  "unable to read", "object directory", "fatal: bad")
_provisioned_urls: "set[str]" = set()


@contextlib.contextmanager
def _file_lock(path: str):
    """fcntl があれば排他ロック。無ければ no-op（ベストエフォート）。"""
    if fcntl is None:
        yield
        return
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def cache_root() -> str:
    """ホスト共有 git キャッシュの root（kiro-flow と同じ既定・同じ環境変数で共有する）。"""
    return os.environ.get("KIRO_GIT_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "kiro-git-cache")


def _cache_path_for(url: str) -> str:
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    return os.path.join(cache_root(), f"{h}.git")


@contextlib.contextmanager
def _cache_lock(url: str):
    """URL 単位のホスト内ロック（INV-2: cache の全変更を直列化。kiro-flow と同一パス）。"""
    root = cache_root()
    os.makedirs(root, exist_ok=True)
    h = hashlib.sha1(url.strip().encode()).hexdigest()
    with _file_lock(os.path.join(root, f"{h}.lock")):
        yield


def _git_cache(cache: str, *args: str, timeout: float = 600):
    return subprocess.run(["git", "-C", cache, *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_cache_valid(cache: str) -> bool:
    if not os.path.isdir(cache):
        return False
    try:
        return _git_cache(cache, "rev-parse", "--git-dir", timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mirror_clone(url: str, cache: str) -> bool:
    """url を blob:none の bare ミラーとして cache に作る。partial 非対応サーバには filter 無しで再試行。"""
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    attempts = [["git", "clone", "--mirror", "--filter=blob:none", url, cache],
                ["git", "clone", "--mirror", url, cache]]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            _git_cache(cache, "config", "gc.auto", "0")
            # --mirror が付ける remote.origin.mirror=true を無効化（refspec 付き push 拒否を防ぐ）。
            _git_cache(cache, "config", "remote.origin.mirror", "false")
            return True
        shutil.rmtree(cache, ignore_errors=True)
    return False


def ensure_cache(url: str) -> "str | None":
    """URL の共有 bare ミラーを用意（無ければ作成・壊れていれば再作成）。fetch はしない。要 _cache_lock。"""
    cache = _cache_path_for(url)
    if _is_cache_valid(cache):
        return cache
    for i in range(CLONE_RETRIES):
        if _mirror_clone(url, cache):
            return cache
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return None


def _cache_fetch(cache: str) -> bool:
    """INV-1: 全 heads を増分 fetch（リトライ付き）。破損系は False（再ミラー誘発）。"""
    for i in range(CLONE_RETRIES):
        try:
            r = _git_cache(cache, "fetch", "--prune", "--no-tags", "origin",
                           "+refs/heads/*:refs/heads/*")
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return True
        if r is not None and any(s in (r.stderr or "").lower() for s in _CACHE_CORRUPT):
            return False
        if i < CLONE_RETRIES - 1:
            time.sleep(2 ** i if i < 4 else 16)
    return False


def _resolve_sha(cache: str, refs: "list[str]") -> str:
    for ref in refs:
        cand = f"refs/heads/{ref}" if ref else "HEAD"
        try:
            r = _git_cache(cache, "rev-parse", "--verify", "--quiet",
                           f"{cand}^{{commit}}", timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def provision_worktree(url: str, refs: "list[str]", dest: str) -> "str | None":
    """INV-1/2 を満たして dest に detached worktree を用意（要 _cache_lock）。失敗時 None。"""
    cache = ensure_cache(url)
    if not cache:
        return None
    if not _cache_fetch(cache):
        shutil.rmtree(cache, ignore_errors=True)
        cache = ensure_cache(url)
        if not cache or not _cache_fetch(cache):
            return None
    sha = _resolve_sha(cache, refs)
    if not sha:
        return None
    dest = os.path.abspath(dest)   # `git -C <cache> worktree add` は相対パスを cache 基準で解くため絶対化
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    for _ in range(2):
        try:
            r = _git_cache(cache, "worktree", "add", "--detach", "--force",
                           dest, sha, timeout=300)
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return dest
        _git_cache(cache, "worktree", "prune", timeout=60)
        shutil.rmtree(dest, ignore_errors=True)
    return None


def _prune_caches(urls) -> None:
    for url in list(urls):
        try:
            with _cache_lock(url):
                cache = _cache_path_for(url)
                if os.path.isdir(cache):
                    _git_cache(cache, "worktree", "prune", timeout=60)
        except Exception:  # noqa: BLE001
            pass


def _clone_repo_shallow(url: str, branch: str, dest: str, timeout: float = 300) -> None:
    """検証用に dest へ対象 repo を用意する。まず共有 cache から detached worktree を生やし（最新化済み・
    INV-1）、失敗時は従来どおり branch（空なら既定）を浅 clone する（INV-3）。最終的に失敗なら RuntimeError。

    branch を明示した場合は **その branch が無ければ既定へ無言フォールバックしない**（refs に "" を
    足さない）。target が消えている等は「成果の無い場所での偽判定」を避けるため NG にする必要があり、
    元の `git clone --depth 1 --branch <target>`（無ければ失敗）と同じ厳密さを保つ。"""
    refs = [branch] if branch else [""]
    try:
        with _cache_lock(url):
            wt = provision_worktree(url, refs, dest)
        if wt:
            _provisioned_urls.add(url)
            return
    except Exception:  # noqa: BLE001 — cache 系の想定外失敗は黙って浅 clone へフォールバック
        pass
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, dest]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(str(e)) from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:300] or "git clone 失敗")


def _acceptance_cwd(cfg: "Config", charter: "Charter") -> "tuple[Path, str | None]":
    """acceptance を実行する作業ディレクトリと、片付けが要る一時 clone のパス（無ければ None）を返す。
    優先順位: 明示 verify_cwd > 単一対象 repo の一時 clone（target ブランチ＝worker の push 先）> workdir。
    git-bus 等で workdir に成果が出ないケースに対応する。"""
    if cfg.verify_cwd:
        return resolve_verify_cwd(cfg), None
    spec = _charter_single_repo(charter)
    if spec:
        tmp = tempfile.mkdtemp(prefix="kiro-accept-")
        dest = str(Path(tmp) / "repo")
        branch = spec.get("target") or spec.get("base") or ""
        try:
            _clone_repo_shallow(spec["url"], branch, dest)
        except (OSError, RuntimeError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(f"対象 repo の clone 失敗（{spec['url']}@{branch or '既定'}）: {e}") from e
        append_journal(cfg.journal, f"project acceptance: {spec['url']}@{branch or '既定'}"
                                    " を clone して検証")
        return Path(dest), tmp
    return cfg.workdir, None


def evaluate_acceptance(cfg: "Config", charter: "Charter") -> "tuple[int, int, list]":
    """charter の acceptance（受入 verify）を実行し (passed, total, [(cmd, ok, msg)]) を返す。
    プロジェクト done の唯一の根拠＝全 PASS。実行先は 明示 verify_cwd > 単一 repo の一時 clone > workdir。
    clone は worker の push 先（target ブランチ）を反映するため毎評価で取り直す。clone 失敗は全 NG 扱い
    （workdir へ黙ってフォールバックすると成果の無い場所で誤判定するため）。"""
    try:
        wd, tmp = _acceptance_cwd(cfg, charter)
    except RuntimeError as e:
        append_journal(cfg.journal, f"project acceptance: {e} → 全 NG 扱い")
        return 0, len(charter.acceptance), [(c, False, str(e)[:500]) for c in charter.acceptance]
    try:
        env = None
        if (wd / ".git").exists():
            head = _git_out(wd, "rev-parse", "HEAD").strip()
            if head:
                env = {"KIRO_BASE_REV": head}
        results = []
        for cmd in charter.acceptance:
            ok, _flaky, msg = run_verify_stable(cmd, wd, cfg.verify_timeout,
                                                cfg.verify_confirm, env)
            results.append((cmd, ok, msg))
        passed = sum(1 for _, ok, _ in results if ok)
        return passed, len(results), results
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
            _prune_caches(_provisioned_urls)   # 共有 cache の worktree 登録を回収（本体は残す）


def _acceptance_kind(line: str) -> "tuple[str, str]":
    """acceptance 1 行を (kind, text) に分類する。kind は 'command'（決定的シェル・そのまま実行）
    か 'accept'（自然言語・要合成）。明示の `accept:` 接頭辞、または『シェルに見えない散文』
    （全角句読点を含む等）を自然言語とみなす。散文をそのまま shell に流して誤実行するのを防ぐため、
    判定不明な行は command でなく accept（合成 → 失敗時は人へ）に倒す。"""
    s = line.strip()
    m = _ACCEPT_PREFIX_RE.match(s)
    if m:
        return "accept", m.group("text").strip()
    if _looks_like_shell_command(s):
        return "command", s
    return "accept", s


def resolve_charter_acceptance(cfg: "Config", charter: "Charter", state: "dict | None" = None,
                               kiro_run=None) -> "tuple[list[str], list[str]]":
    """charter.acceptance の各行を実行可能なシェルコマンドへ解決し (resolved, unresolved) を返す。
    決定的コマンドはそのまま、自然言語（`accept:` 接頭辞 or 散文）はエージェントが決定的 verify を合成する
    （タスクの synth_verify を流用＝偽 done 防止規則を織込）。合成結果は state['acceptance_synth'] に
    原文キーでキャッシュし、サイクル/再実行をまたいで done 基準（acceptance）を安定させる（毎回の再合成と
    非決定的なブレを防ぐ）。合成できない自然言語は unresolved に積み、呼び出し側が done 判定不能として人へ回す。"""
    cache = dict((state or {}).get("acceptance_synth") or {})
    resolved: "list[str]" = []
    unresolved: "list[str]" = []
    for line in charter.acceptance:
        kind, text = _acceptance_kind(line)
        if kind == "command":
            resolved.append(text)
            continue
        cmd = cache.get(text)
        if not cmd:
            cmd = synth_verify(cfg, charter.name or "project", text, kiro_run)
            if cmd:
                cache[text] = cmd
        if cmd:
            resolved.append(cmd)
        else:
            unresolved.append(text)
    if state is not None:
        state["acceptance_synth"] = cache
    return resolved, unresolved


def _acceptance_specs(cmds: "list[str]") -> "list[dict]":
    """acceptance コマンドを、それ自体を verify とするタスク spec にする（決定的・的が外れない）。
    verify は charter に書かれた受入条件そのもの＝人が入力した条件で done を判定する。"""
    return [{"title": f"受入条件を満たす: {cmd}"[:120], "verify": cmd, "source": "acceptance"}
            for cmd in cmds]


def _failing_acceptance_specs(results: "list") -> "list[dict]":
    """未達 acceptance を、それ自体を verify とする改善タスク spec にする（決定的・的が外れない）。"""
    return _acceptance_specs([cmd for cmd, ok, _ in results if not ok])


def write_milestone(cfg: "Config", charter: "Charter", reason: str, summary: str,
                    pid: "str | None" = None, version: str = "") -> None:
    """収束候補/要対応を milestone として needs/<pid>.md に出す（検収ゲートのプロジェクト版）。
    複数 charter 運用では pid が `<project>-<charter名>` になり charter 別に分かれる。
    version（バージョン名）を渡すと見出しに使う: charter の `# Charter:` 宣言名は前バージョンの
    コピー等でプロジェクト名のまま食い違うことがあるため、バージョンの識別はファイル名（version）を
    正とする（viewer の表示も同じ規則）。"""
    pid = pid or _project_id(cfg, charter)
    # 見出し: 複数バージョン運用はバージョン名、単一運用は charter の宣言名。
    # 宣言名がバージョン名と別に意味を持つ場合だけ併記する（「v2」ではなく「v2（保守）」等）。
    heading = charter.name
    if version:
        heading = version if charter.name in ("", "project", version, cfg.project_name) \
            else f"{version}（{charter.name}）"
    cfg.needs.mkdir(parents=True, exist_ok=True)
    labels = {
        REASON_PROJECT_CONVERGED: "収束候補（acceptance 全 PASS・改善ゼロ）",
        REASON_PROJECT_STALL: "停滞（acceptance PASS 数が増えない→人へ）",
        REASON_PROJECT_BUDGET: "サイクル予算到達（人の判断待ち）",
        REASON_PROJECT_COST: "コスト予算到達（人の判断待ち）",
        REASON_PROJECT_BLOCKED: "内側ループが人へエスカレーション",
        REASON_PROJECT_NO_ACCEPTANCE: "acceptance 未定義（done 判定不能→人へ）",
    }
    hint = (
        f"<!-- 完了として受領するなら `kiro-project approve {pid} --reason ...`（プロジェクト done）。\n"
        f"     次フェーズへ続けるなら charter.md の goal/acceptance を更新して再実行。\n"
        f"     方向修正なら下に方針を書いて [x]（または policy.md を編集）。 -->\n")
    body = (
        f"{_madr_frontmatter(pid, 'milestone')}"
        f"# マイルストーン: {heading}\n\n"
        f"## Context and Problem Statement\n\n"
        f"- なぜ: {labels.get(reason, reason)}\n"
        f"- 状態: {reason}\n"
        f"- 概況: {summary}\n\n"
        f"## goal\n{charter.goal}\n\n"
        f"{DECISION_MARKER}\n\n"
        f"<!-- 人の決定の記入欄（MADR の Decision Outcome）。方針・指示をここに書く。 -->\n"
        f"- [ ] 確定（このボックスを [x] にして保存すると取り込みます）\n\n"
        f"{hint}")
    (cfg.needs / f"{pid}.md").write_text(body, encoding="utf-8")


_NEEDS_KIND_RE = re.compile(r"(?m)^kind:\s*(\S+)")


def _needs_kind(path: Path) -> str:
    """needs/<id>.md の frontmatter kind を読む（milestone / plan-review / review / blocked）。"""
    try:
        head = path.read_text(encoding="utf-8")[:400]
    except OSError:
        return ""
    m = _NEEDS_KIND_RE.search(head)
    return m.group(1) if m else ""


def reconcile_milestones(cfg: "Config") -> None:
    """milestone ファイル（needs/<pid>.md, kind=milestone）を project.json の status に一致させる
    唯一の調整点（GC）。この関数だけが「milestone を残すか消すか」を決める＝milestone は status の
    純粋な投影になり、署名比較の綻び・承認失敗・バージョン削除・旧トップレベル残存などで
    milestone が『復活』しても、毎パス確実に status に合わせて掃除される（根本対策）。

    残すのは MILESTONE_STATUSES（converged/no-acceptance/blocked/stall/budget/cost）の、いま存在する
    バージョンの milestone だけ。承認済み（accepted）・もう無いバージョン・旧トップレベル
    （<project>.md、バージョン運用時）の milestone は消す。タスク級の needs（plan-review/review/
    blocked タスク）は kind で除外して触らない。"""
    if not cfg.needs.exists():
        return
    data = load_project_state(cfg)
    names = charter_names(cfg)
    # 有効な pid → status。単一 charter は top-level、バージョン運用は各 version の state だけ
    # （トップレベル state は無効）、マスターのみ（names 空）は有効ゼロ＝全 milestone を消す。
    valid: "dict[str, str]" = {}
    if names == ["default"]:
        if data.get("id"):
            valid[str(data["id"])] = str(data.get("status") or "")
    else:
        charters = data.get("charters") or {}
        for name in names:
            st = charters.get(name) or {}
            if st.get("id"):
                valid[str(st["id"])] = str(st.get("status") or "")
    for nf in sorted(cfg.needs.glob("*.md")):
        if _needs_kind(nf) != "milestone":
            continue
        status = valid.get(nf.stem)
        if status is None or status not in MILESTONE_STATUSES:
            try:
                nf.unlink()
            except OSError:
                pass


def finalize_project(cfg: "Config", state: dict, reason: str,
                     charter: "Charter | None" = None,
                     charter_name: "str | None" = None) -> None:
    """プロジェクト（charter）を done 確定する。最終納品書を残し state を accepted に。
    charter を渡すと accepted_charter_sig を記録し、次回 run は cmd_project 冒頭のガードで
    charter.md が変わるまで再実行しない（accepted 直後に再収束して milestone が復活するのを防ぐ）。"""
    pid = state.get("id", "project")
    name = state.get("name", pid)
    total = int(state.get("acceptance_total", 0))
    ts = _now_ts()
    summary = f"acceptance {total}/{total} PASS"
    final = Task(id=pid, title=f"[project] {name}", status="done",
                 source="project", verify=f"acceptance×{total}")
    append_delivery(cfg, final, summary, ts)
    append_decision(cfg, pid, "user", context=f"プロジェクト『{name}』を完了として受領",
                    action="project-accept", reason=reason, affects=summary)
    clear_needs_file(cfg, pid)
    state["status"] = REASON_PROJECT_ACCEPTED
    if charter is not None:
        state["accepted_charter_sig"] = _charter_full_signature(charter)
    save_charter_state(cfg, state, charter_name)


def project_exit_code(reason: str) -> int:
    if reason == REASON_PROJECT_ACCEPTED:
        return 0
    if reason in (REASON_PROJECT_BUDGET, REASON_PROJECT_COST):
        return 2
    return 1   # converged / no-progress / blocked / no-acceptance は人の対応待ち


def _project_evaluate(cfg: "Config", charter: "Charter", pid: str, state: dict,
                      cycle: int, cost_used: float, review_fn,
                      charter_tag: str = "") -> "tuple[str | None, str]":
    """③ evaluate: acceptance 評価 → 未達/レビュー所見を改善タスク化 → 収束/コスト/停滞を判定する。
    停止すべきなら停止理由を、続行なら None を返す（last_summary も返す）。state(history/best/stall) を更新。
    charter_tag（複数 charter 運用）を渡すと改善タスクにタグを付け、冪等照合もその charter に閉じる。"""
    passed, total, results = evaluate_acceptance(cfg, charter)
    state["history"] = list(state.get("history", [])) + [passed]
    # best（過去最高 PASS 数）は停滞判定の基準であると同時に、viewer の「n / m 達成」の表示元。
    # 停滞判定より先にここで更新する: 下の収束 return より後ろで更新していたため、一発で全 PASS
    # して収束したプロジェクトは best が 0 のまま残り、完了しているのに「0 / 1 達成」と出ていた。
    # 停滞判定は更新前の値（prev_best）と比べる＝従来の意味を変えない。
    prev_best = int(state.get("best", 0))
    state["best"] = max(prev_best, passed)
    existing = _existing_titles(cfg, charter_tag or None)

    def _tag(specs: "list[dict]") -> "list[dict]":
        if charter_tag:
            for sp in specs:
                sp.setdefault("charter", charter_tag)
        return specs

    improved: list[Task] = []
    if passed < total:                        # 未達 acceptance を、それ自体を verify とする改善タスクへ
        improved += _enqueue_specs(cfg, _tag(_failing_acceptance_specs(results)),
                                   existing, cfg.learn_threshold, charter=charter_tag or None)
    findings: list[dict] = []
    if cfg.review_project and passed == total:  # 短絡的達成を疑い敵対的レビュー（opt-in）
        findings = _tag(review_fn(charter))
        improved += _enqueue_specs(cfg, findings, existing, cfg.learn_threshold,
                                   charter=charter_tag or None)
    last_summary = (f"cycle {cycle}: acceptance {passed}/{total} PASS, "
                    f"改善 {len(improved)} 件, cost={cost_used:.4f}")
    append_decision(cfg, pid, "auto",
                    context=f"cycle {cycle}: acceptance {passed}/{total} PASS",
                    action="project-evaluate",
                    reason=("収束候補" if passed == total and not improved else "改善継続"),
                    affects=f"改善 {len(improved)} 件 / findings {len(findings)}")
    append_journal(cfg.journal, "project " + last_summary)
    if passed == total and not improved:      # 収束: acceptance 全 PASS かつ改善ゼロ
        return REASON_PROJECT_CONVERGED, last_summary
    if cfg.max_project_cost and cost_used >= cfg.max_project_cost:
        return REASON_PROJECT_COST, last_summary
    if passed > prev_best:                    # 停滞: PASS 数が過去最高を更新しないなら人へ（自動チャーン止め）
        state["stall"] = 0
    else:
        state["stall"] = int(state.get("stall", 0)) + 1
    if state["stall"] >= cfg.project_stall:
        return REASON_PROJECT_STALL, last_summary
    return None, last_summary


def cmd_project(cfg: "Config", planner=None, reviewer=None, runner=run_loop, heartbeat=None,
                kiro_run=None, charter_name: "str | None" = None) -> int:
    """charter 駆動の plan→execute→evaluate ループ（1 charter の 1 パス。`run` が charter 検出時に呼ぶ）。
    charter_name（charters/<name>.md）を渡すとその charter だけを回す（複数 charter 運用）。
    planner/reviewer/runner/kiro_run は テストのため注入可能（既定はエージェント委譲＋正準ループ）。"""
    ensure_dirs(cfg)
    charter = _load_named_charter(cfg, charter_name)
    multi = _is_multi_charter(cfg, charter_name)
    if charter is None:
        print(f"エラー: charter が見つかりません: {cfg.charter}", file=sys.stderr)
        print("  ヒント: 目標/制約/前提/成果物/acceptance を charter.md に書いてください。",
              file=sys.stderr)
        return 2
    # 人の指示（commands/ ドロップ）を入口で消化する。従来は execute（run_loop）内でしか
    # 取り込まれず、下の accepted ガードで早期 return するパスでは承認/却下/replan の指示ファイルが
    # 何パスも放置され、watch が空振り起床を繰り返した末に、状態が変わってから取り込まれて
    # exit 2（.err 退避）になる実害があった（viewer の承認ボタンが「押しても何も起きない」の一因）。
    ingest_commands(cfg)
    # 人からの再分解要求（エラー回復）を入口で消費する（one-shot）。ここで消しておくことで、
    # acceptance 未定義など早期 return するパスでもマーカーが残らず、has_work の空振り起床が
    # 続かない（要求は消化済み＝下の plan ゲートで charter 無変更でも一発だけ plan を強制する）。
    # 直前の ingest_commands が replan 指示をマーカー化した場合も、この場で拾って同一パスで反映する。
    replan_req = consume_replan_request(cfg, charter_name if multi else None)
    problems = validate_charter(charter)
    if problems:
        print(f"エラー: charter の repos 定義が不正です（{cfg.charter}）:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("  ヒント: 各 repo に `- desc:`（説明・必須）と `- base:`（ベースブランチ・必須）を、"
              "必要なら `- target:`（既定 base）を付けてください。", file=sys.stderr)
        return 2
    pid = _project_id(cfg, charter) + (f"-{charter_name}" if multi else "")
    # バージョン運用（multi）では、旧・単一 charter 時代のトップレベル milestone
    # （needs/<project>.md、バージョン接尾辞なし）は不要。単一 charter で一度 run した後に
    # charters/ を足す（バージョン運用へ移行する）と、この古い milestone が残って「マイルストーン」が
    # 二重に見える（<project>.md と <project>-<version>.md）。バージョン運用のパスに入ったら常に
    # 掃除する（accepted ガードや no-acceptance で早期 return するパスより前で行う＝取り残さない）。
    if multi:
        base_pid = _project_id(cfg, charter)
        if base_pid != pid:
            clear_needs_file(cfg, base_pid)
    # 収束状態は milestone ファイルより先に決める（viewer と milestone GC はこの status を正とする）。
    # 早期 return するパス（no-acceptance / 合成不能）でも status を project.json に必ず残すことで、
    # viewer が「承認できるのか（converged）／完了条件を足すべきか（no-acceptance）」を判別でき、
    # reconcile_milestones が milestone ファイルを status に追従させられる。
    state = load_charter_state(cfg, charter_name if multi else None)
    if state.get("id") != pid:
        state = {"id": pid, "name": charter.name, "history": [], "best": 0, "stall": 0}
    if not charter.acceptance:
        # acceptance（受入 verify）が無いと done を判定できない＝必ず人へ（鉄則の保全）。
        # status=no-acceptance を保存してから milestone を出す（承認ではなく「完了条件を追加」を促す）。
        state.update({"id": pid, "name": charter.name, "status": REASON_PROJECT_NO_ACCEPTANCE})
        save_charter_state(cfg, state, charter_name if multi else None)
        write_milestone(cfg, charter, REASON_PROJECT_NO_ACCEPTANCE,
                        "acceptance 未定義のため done 判定不能", pid=pid,
                        version=charter_name if multi else "")
        print(f"[project] {charter.name}: acceptance 未定義 → 人へ（needs/{pid}.md）")
        return project_exit_code(REASON_PROJECT_NO_ACCEPTANCE)

    # executor: stub はローカル完結（エージェント不使用）が既定の意味＝charter 駆動の
    # 分解・レビューもここで揃える（さもないと --planner none / --executor stub を設定しても
    # plan_via_agent/review_via_agent が黙ってエージェントを呼んでしまう）。
    stub_mode = cfg.executor == "stub"
    plan_fn = planner or ((lambda ch: plan_via_stub(cfg, ch)) if stub_mode
                          else (lambda ch: plan_via_agent(cfg, ch)))
    review_fn = reviewer or ((lambda ch: review_via_stub(cfg, ch)) if stub_mode
                             else (lambda ch: review_via_agent(cfg, ch)))
    # このパス開始時点で「人が承認済み・charter も承認時から無変更」だったか。
    # 下のガードの早期 return に加え、replan（差分ゼロ）等でガードを抜けて再評価した場合にも、
    # 新しい仕事が何も無ければ末尾で accepted を維持する（converged へ降格して承認済み
    # マイルストーンを復活させない）ための基準値。
    was_accepted = (state.get("status") == REASON_PROJECT_ACCEPTED
                    and state.get("accepted_charter_sig") == _charter_full_signature(charter))
    # 承認済み（accepted）かつ charter.md が承認時から無変更なら何もしない（毎 run 再収束して
    # 承認済みプロジェクトの milestone が復活する不具合の防止）。charter.md を編集すると
    # 署名が変わり、ここを抜けて通常どおり再評価される（「続行: charter.md を更新して再実行」の
    # 案内どおりの挙動になる）。replan_req（人が明示的に要求したエラー回復の再分解）がある場合は
    # 素通りしない＝ここで早期 return すると直前で consume 済みの要求が握り潰され、
    # 「再分解を押しても何も起きない」になるため、accepted でも明示要求は必ず一度は処理する。
    if replan_req is None and was_accepted:
        print(f"[project] {charter.name}: 承認済み（charter.md に変更なし）→ 何もしません。"
              f"続けるなら charter.md を編集してください。")
        return project_exit_code(REASON_PROJECT_ACCEPTED)
    # acceptance を実行可能なコマンドへ解決（自然言語は決定的 verify へ合成し、結果を state にキャッシュ）。
    # 合成できない自然言語が残れば done 判定不能＝人へ（acceptance を書けないプロジェクトは人へ回す鉄則）。
    resolved, unresolved = resolve_charter_acceptance(cfg, charter, state, kiro_run)
    if unresolved:
        state["status"] = REASON_PROJECT_NO_ACCEPTANCE          # viewer/GC が status を正に読める
        save_charter_state(cfg, state, charter_name if multi else None)   # 合成済みキャッシュも残す
        summary = ("自然言語の acceptance を決定的 verify に合成できません（done 判定不能）: "
                   + " / ".join(unresolved))
        write_milestone(cfg, charter, REASON_PROJECT_NO_ACCEPTANCE, summary, pid=pid,
                        version=charter_name if multi else "")
        print(f"[project] {charter.name}: acceptance を合成できず → 人へ（needs/{pid}.md）")
        for u in unresolved:
            print(f"  - 未合成: {u}", file=sys.stderr)
        return project_exit_code(REASON_PROJECT_NO_ACCEPTANCE)
    charter.acceptance = resolved             # 以降の評価は合成済みの決定的コマンドで行う
    # charter 変更の検知（内容署名）: backlog 分解に効く内容が前回計画時と変わっていれば、消化可能
    # タスクが残っていても再計画して差分を投入する（viewer 等で charter を編集しても backlog が
    # 変わらない問題への対処）。署名が未記録（初回/既存プロジェクト）はベースラインを張るだけで
    # 再計画は誘発しない（次回以降の編集から検知できる）。
    plan_sig = _charter_plan_signature(charter)
    charter_changed = bool(state.get("planned_charter_sig")) and state["planned_charter_sig"] != plan_sig
    state["planned_charter_sig"] = plan_sig
    # "status" はここでは触らない（旧実装は "running" に即上書きしていた）。② execute
    # （runner=run_loop）が内部で ingest_commands を呼び、その場で approve/hold 等の人の指示を
    # 処理する。ここで status を "running" にしてしまうと、直前サイクルの "converged" を
    # ingest_commands が読めなくなり、watch 中は「承認してもプロジェクト milestone の approve が
    # 常に exit 2 で失敗し、次サイクルでまた収束候補として復活し続ける」実害があった
    # （cmd_approve は status == converged の milestone しか受け付けないため）。
    # 最終的な状態は下の evaluate ループの結果で必ず上書きされるので、ここで省いても状態は失われない。
    state.update({"id": pid, "name": charter.name, "acceptance_total": len(charter.acceptance)})
    save_charter_state(cfg, state, charter_name if multi else None)

    # 前パスが残した milestone（needs/<pid>.md）はこのパスで再評価するため先に掃除する。
    # 残したままだと execute（run 実行）中も「要対応: マイルストーン」カードが出続け、
    # 収束前の承認（cmd_approve は converged しか受けない＝exit 2）を人に押させてしまう。
    # まだ必要なら末尾の write_milestone が最新内容で書き直す。
    clear_needs_file(cfg, pid)

    append_journal(cfg.journal, f"=== project 開始 {charter.name} "
                                f"acceptance={len(charter.acceptance)} ===")
    cost_used = float(state.get("cost", 0.0))
    cycle = 0
    reason = REASON_PROJECT_CONVERGED
    last_summary = ""
    did_work = False              # このパスで新規タスク投入 or 消化があったか（accepted 維持の判定）

    while True:
        cycle += 1
        if heartbeat:
            heartbeat()                  # 長い改善ループ中も生存信号を更新（リモート発見の鮮度）
        if cycle > cfg.max_project_cycles:
            reason = REASON_PROJECT_BUDGET
            break

        # ① plan — 消化可能タスクが無いとき、または charter が前回計画時から変わったときに目標から
        #   backlog を起こす（変更が無ければ毎サイクルの再分解は避ける）。再計画は既存/archive タイトルで
        #   冪等に重複排除されるため、既存タスクを二重投入せず「charter の差分が生む新規タスク」だけ入る。
        #   再分解要求（replan＝エラー回復のやり直し）だけは照合を「done 以外」に絞る:
        #   done と類似でも再作成を許可する（過去に完了した同種タスクが再分解を丸ごと弾き、
        #   「再分解を押しても何も起きない」になるのを防ぐ）。処理中タスクとの二重投入と
        #   却下済み（人の明示判断）の復活はさせない。
        replan_retry = replan_req is not None
        existing = _existing_titles(cfg, charter_name if multi else None,
                                    active_only=replan_retry)
        has_consumable = any(
            t.consumable() and (not multi or task_charter_name(t) == charter_name)
            for t in load_tasks(cfg.backlog))
        if not has_consumable or charter_changed or replan_retry:
            ensure_repo_maps(cfg, charter)   # リポジトリ理解の成果物化（opt-in・sha キャッシュ）
            specs = plan_fn(charter)
            if multi:
                for sp in specs:                 # この charter のタスクとしてタグ付け（スコープの正）
                    if isinstance(sp, dict):
                        sp.setdefault("charter", charter_name)
            planned = _enqueue_specs(cfg, specs, existing, cfg.learn_threshold,
                                     charter=charter_name if multi else None,
                                     active_only=replan_retry)
            trig = ("再分解要求（エラー回復）" if replan_retry
                    else "charter 変更検知" if charter_changed else f"plan cycle {cycle}")
            if planned:
                did_work = True
                append_journal(cfg.journal,
                               f"project cycle {cycle}: {trig} で {len(planned)} 件投入 "
                               f"{[t.id for t in planned]}")
            elif replan_retry:
                # 再分解しても差分ゼロ（すべて現行処理中の backlog と重複）＝やり直し対象なし。要求は消化済み。
                append_journal(cfg.journal,
                               f"project cycle {cycle}: {trig} → 新規なし（現行バックログと重複）")
            charter_changed = False   # 変更由来の再計画は 1 回だけ（以降のサイクルで再分解しない）
            replan_req = None         # 再分解要求も 1 回だけ消化する（one-shot）

        # ② execute — 既存の正準ループを無改造で回す（drained まで）
        result = runner(cfg)
        cost_used += float(result.get("cost", 0.0))
        counts = result["counts"]
        if counts.get("done", 0) > 0:
            did_work = True
        if result["reason"] in (REASON_BUDGET, REASON_COST, REASON_THROTTLE):
            reason = REASON_PROJECT_BUDGET if result["reason"] != REASON_COST else REASON_PROJECT_COST
            break
        if counts.get("blocked", 0) > 0 or counts.get("review", 0) > 0 \
                or counts.get("proposed", 0) > 0:
            reason = REASON_PROJECT_BLOCKED      # 内側が人へ → プロジェクトも人待ちで止める
            break
        # execute 中（runner=run_loop 内の ingest_commands）に人がこの milestone を承認して
        # いるかもしれない。承認済みなら evaluate で acceptance を再収束させて上書きしない
        # （accepted を尊重する。さもないと承認直後の同一サイクルで milestone が復活する）。
        # accepted_charter_sig を「今評価している charter」と突き合わせるのは、charter.md が
        # 変わった直後の run（冒頭ガードは通過済み）で、まだ古い accepted が残っているだけの
        # ケースと区別するため（そのケースは新規承認ではないので短絡しない＝通常どおり再評価する）。
        mid_state = load_charter_state(cfg, charter_name if multi else None)
        if (mid_state.get("status") == REASON_PROJECT_ACCEPTED
                and mid_state.get("accepted_charter_sig") == _charter_full_signature(charter)):
            state.update(mid_state)
            reason = REASON_PROJECT_ACCEPTED
            break

        # ③ evaluate — acceptance 評価・改善起票・収束/コスト/停滞判定（停止理由 or None）
        stop_reason, last_summary = _project_evaluate(cfg, charter, pid, state, cycle,
                                                      cost_used, review_fn,
                                                      charter_tag=charter_name if multi else "")
        if stop_reason:
            reason = stop_reason
            break

    if reason == REASON_PROJECT_CONVERGED and was_accepted and not did_work:
        # 承認済み（accepted・charter 無変更）のプロジェクトを再評価しただけ（新規タスクなし・
        # 消化なし）で再収束した場合は accepted を維持する。降格を許すと、差分ゼロの再分解や
        # 再評価のたびに accepted → converged へ戻り、承認済みのマイルストーンが復活して
        # 人に同じ承認を何度も求めてしまう。新しい仕事が実際にあった場合（did_work）は
        # 新規成果の検収ゲートとして通常どおり converged（milestone）へ進む。
        reason = REASON_PROJECT_ACCEPTED
        append_journal(cfg.journal, "project: 承認済み（差分ゼロの再評価）→ accepted を維持")
    state["cost"] = round(cost_used, 4)
    state["cycles"] = int(state.get("cycles", 0)) + cycle
    state["status"] = reason
    save_charter_state(cfg, state, charter_name if multi else None)

    if reason in (REASON_PROJECT_CONVERGED, REASON_PROJECT_STALL,
                  REASON_PROJECT_BUDGET, REASON_PROJECT_COST, REASON_PROJECT_BLOCKED):
        write_milestone(cfg, charter, reason, last_summary or "（評価前に停止）", pid=pid,
                        version=charter_name if multi else "")
    append_journal(cfg.journal, f"=== project 停止 reason={reason} cycles={cycle} "
                                f"cost={cost_used:.4f} ===")
    print(f"\n=== kiro-project run（charter 駆動: {charter.name}）===")
    print(f"停止理由 : {reason}")
    print(f"概況     : {last_summary or '（評価前に停止）'}")
    if reason == REASON_PROJECT_CONVERGED:
        print(f"→ 収束候補。受領: kiro-project approve {pid} --reason ...  "
              f"／ 続行: charter.md を更新して run を再実行")
    elif reason != REASON_PROJECT_ACCEPTED:
        print(f"→ 人の対応待ち: needs/{pid}.md を確認")
    return project_exit_code(reason)


def _charter_mtimes(cfg: "Config") -> "dict[str, float]":
    """charter ファイル群（charters/*.md と charter.md）の mtime（更新検知用）。"""
    out: "dict[str, float]" = {}
    d = charters_dir(cfg)
    if d.is_dir():
        for f in d.glob("*.md"):
            try:
                out[f.name] = f.stat().st_mtime
            except OSError:
                pass
    if cfg.charter and cfg.charter.exists():
        try:
            out["charter.md"] = cfg.charter.stat().st_mtime
        except OSError:
            pass
    return out


def project_watch(cfg: "Config", planner=None, reviewer=None, runner=run_loop,
                  sleeper=time.sleep, max_passes=None, heartbeat=None) -> int:
    """`run --watch`（charter あり）: 1 パスごとに plan→execute→evaluate を回し、人待ちで止まったら
    charter 更新/フィードバックを poll で拾って再開する（idle 中はエージェント非起動）。"""
    passes = 0
    code = 0
    # （再）起動直後は plan より先にリモート状態を取り込む。自己更新の graceful 再起動を挟むと、
    # 停止中に viewer が push した charter 更新/フィードバックが未取り込みのまま cmd_project の
    # 初回 plan が走り、古い charter で計画してしまう（cmd_project→run_loop の入口同期は plan の後）。
    state_sync(cfg)
    while True:
        if is_paused(cfg):           # pause 中は plan/execute/evaluate を起こさない
            append_journal(cfg.journal, "=== project watch: 一時停止中（resume/stop 待ち）===")
            write_status(cfg)
            code = 0
        else:
            names = charter_names(cfg)
            if not names:
                if not _has_master_charter(cfg):
                    return code
                # マスター憲章のみ（バージョン未作成）: 分解はしない。実 backlog タスクや人の指示が
                # あるときだけ消化し、無ければ何もしない＝アイドルのまま（リセット直後など、やる
                # ことが無いのに run_loop が回って run-log/journal を無駄に増やさない）。バージョン
                # （charters/<名前>.md）が置かれれば次パスで charter 駆動へ入る。
                if has_work(cfg):
                    runner(cfg)
                passes += 1
                if heartbeat:
                    heartbeat()
                if max_passes is not None and passes >= max_passes:
                    return code
            for name in names:       # 全 charter（バージョン）をラウンドロビンで 1 パスずつ
                code = cmd_project(cfg, planner, reviewer, runner, heartbeat=heartbeat,
                                   charter_name=name)
                passes += 1
                if heartbeat:
                    heartbeat()
                if max_passes is not None and passes >= max_passes:
                    return code
        names = charter_names(cfg)
        if not names and not _has_master_charter(cfg):
            return code
        # このパスの版処理を終えたら milestone を status へ整合する（唯一の調整点）。
        # accepted・削除済みバージョン・旧トップレベルの milestone を掃除し、承認できない
        # no-acceptance 等はそのまま残す＝「要対応マイルストーンが何度も復活」を止める根本対策。
        reconcile_milestones(cfg)
        pids = {}
        for name in names:           # charter 別の milestone id（フィードバック検知に使う）
            ch = _load_named_charter(cfg, name)
            if ch is not None:
                pids[name] = _project_id(cfg, ch) + (
                    f"-{name}" if _is_multi_charter(cfg, name) else "")
        mtimes0 = _charter_mtimes(cfg)
        append_journal(cfg.journal, "=== project watch: 監視中（charter 更新/フィードバック待ち）===")
        while True:                  # idle: charter が変わるか、人のフィードバックが来たら再開
            sleeper(cfg.poll)
            if heartbeat:
                heartbeat()
            state_sync(cfg)          # 状態 git: リモートの charter 更新/フィードバックを取り込む（間隔律速）
            if is_paused(cfg):
                ingest_commands(cfg)  # pause 中も resume/stop（と他の指示）は受け付ける
                if maybe_self_update(cfg):
                    raise _RestartRequested()
                continue             # pause 中は再開条件を評価しない
            resumed = False
            for pid in pids.values():
                # milestone のフィードバックは [x] を待たず本文だけで再開する（方針を書けば動く）。
                # ただし書きかけを消さないよう静穏化は待つ: settled を挟まないと、人が needs を
                # 編集して保存した瞬間にファイルごと消えてしまう。
                nf = needs_path(cfg, pid)
                if nf.exists() and settled(cfg, nf) and read_feedback(nf):
                    clear_needs_file(cfg, pid)
                    resumed = True
            if resumed or _charter_mtimes(cfg) != mtimes0 or has_work(cfg):
                break
            if maybe_self_update(cfg):   # アイドル時のみ自己更新（取り込めたら再起動）
                raise _RestartRequested()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 設定ファイル（kiro-flow と同じ流儀: YAML 任意 / JSON フォールバック）
#   優先順位 CLI > 設定ファイル > 組み込み既定。環境ごとに決まる値をファイルに、
#   その場限りの上書きだけ CLI で渡す。PyYAML 無し環境は JSON（同じキー）で。
# ---------------------------------------------------------------------------
try:
    import yaml  # type: ignore

    def _load_config_file(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
except ImportError:  # PyYAML 無し → JSON のみ
    yaml = None  # type: ignore

    def _load_config_file(path: str) -> dict:  # type: ignore[misc]
        if path.lower().endswith((".yaml", ".yml")):
            print("[kiro-project] ERROR: YAML 設定には PyYAML が必要です（pip install pyyaml）。"
                  "JSON 設定（kiro-project.json・同じキー）なら不要です。", file=sys.stderr)
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


DEFAULT_CONFIG_NAMES = ["kiro-project.yaml", "kiro-project.yml", "kiro-project.json"]

# 設定ファイルで上書きできるキー（snake_case）と組み込み既定。
# CLI 引数の default は None にし、resolve_config で「設定ファイル→ここ」の順に埋める。
# 真偽フラグ（--watch / --ltm / --no-archive 等）と個別パス上書きは CLI 専用。
CONFIG_DEFAULTS = {
    "root": ".",
    "workdir": ".",
    "executor": "agent",
    "planner": "agent",
    "flow_planner": "flow-planner",
    "route_planner": "agent",
    "default_workspace": "",
    "location": "auto",
    "model": None,
    # LLM 実行に使うエージェント CLI: kiro（kiro-cli chat）/ claude（Claude Code `claude -p`）/
    # copilot（GitHub Copilot CLI `copilot -p`）/ codex（OpenAI Codex CLI `codex exec`）。
    # kiro-project 自身の LLM 呼び出し
    # （分解・優先順位・裁定・ルーティング）に効く。実行層 kiro-flow の CLI は
    # kiro-flow 側の設定（flow_config / kiro-flow.yaml の agent_cli）で揃える。
    "agent_cli": "kiro",
    "agent_timeout": 300.0,   # エージェント CLI 1 呼び出しのタイムアウト秒（0 以下で無効）
    # バックログ分解の粒度: coarse（ストーリー相当・既定）/ fine（単機能）/ finest（1ファイル/1関数）。
    # kiro-flow の同名設定と語彙を揃えている（あちらは実行時 DAG、こちらは backlog の分解に効く）。
    "granularity": "coarse",
    "poll": 5.0,
    "concurrency": 1,
    "level": "unattended",
    "throttle": 0.0,
    "debounce": 3.0,
    "pace": 0.0,
    "max_cycles": 20,
    "max_seconds": 0.0,
    "max_tokens": 0,
    "max_cost": 0.0,
    "max_retries": 2,
    "max_iterations": 3,
    "verify_timeout": 120.0,
    "verify_confirm": 1,
    "verify_cwd": None,
    "act_timeout": 1800.0,
    "act_async": False,   # 非ブロッキング委譲（daemon/remote へ submit して待たず offloaded で回収）
    # kiro-flow バスの置き場（絶対パスで明示すると外部 daemon を検知できる）。None なら <root>/bus。
    # 設定ファイルの bus: をここに載せておかないと resolve_config が読まず黙って既定バスに落ちる
    # （daemon 非検知の原因になる）。CLI --bus と同義。
    "bus": None,
    "git_bus": None,
    "git_branch": "main",
    "git_subdir": None,
    "state_git": None,                  # 状態の git 保存・共有（プロジェクト状態を双方向同期。None で無効）
    "state_git_branch": "main",
    "state_git_subdir": "kiro-project",   # リポジトリ内の保存先サブディレクトリ（多重コミッタとの分離）
    "state_git_interval": 300.0,        # fetch/push の最短間隔（秒）。0 で毎同期
    # journal のローテーション: 閾値を超えたら journal-archive/ へ退避して新しい journal を始める。
    # 追記専用ファイルの肥大と、direct 同期での EOF 追記マージ衝突の温床を抑える。
    "journal_max_bytes": 262144,        # 閾値バイト（既定 256KB）。0 以下でローテーション無効
    "journal_keep": 20,                 # journal-archive/ の保持世代数。0 以下で無制限
    # 実行層 kiro-flow daemon をこのプロジェクト用に kiro-project が起動・監視する（opt-in）。
    "manage_flow_daemon": False,
    "flow_config": None,        # daemon に --config で渡す共有 kiro-flow.yaml（任意。kiro-flow の設定はここに集約）
    "flow_max_workers": 4,      # kiro-flow daemon の worker 上限
    "status_interval": 0.0,             # watch アイドル中の status.json 生存信号更新間隔（秒）。既定 0=無効
    "lock_dir": None,   # kiro-flow daemon ロックの置き場（外部 daemon 発見のため kiro-flow と一致させる）
    "kiro_flow": None,
    "notify_cmd": None,
    "actor": os.environ.get("USER", "user"),
    "learn_capture": True,      # approve/hold 理由・gitlab 却下コメントから learn/avoid を自動抽出（三値 --learn-capture/--no-...）
    "distill_learn": True,      # 人コメントを一般化ルールへ蒸留してから learn 化（三値 --distill-learn/--no-distill-learn）
    "verify_validate": "synth", # red-green 検証（off/synth/all）。合成 verify が変更を弁別するか実行で確かめる
    "reject_recur": 2,          # 同種 gitlab 却下がこの回数で「系の再考」へ格上げ（0/負で無効）
    "intake_recall": True,      # 投入/triage 時の予防リコール（過去 hold に類似→inbox）（三値フラグ）
    "learn_threshold": 0.5,
    "promote_threshold": 2,
    "ltm_home": None,
    "rot_age_days": 14.0,
    "auto_adjudicate": True,    # 真偽だが --auto-adjudicate/--no-... の三値で config 上書き可（既定 on）
    "adjudicate_max": 1,
    "max_spawn": 20,            # 1 run の派生タスク生成上限（0 で無効）
    "regression_cmd": None,     # done 確定前のグローバル回帰検査コマンド（巻き込み事故の検知）
    "regression_revert": False,
    "intake_cmd": None,         # 外部ゲート/検出器から修復タスクを汲み上げるコマンド（例: codd-gate tasks --debt）
    "intake_interval": 600.0,   # intake の実行間隔（秒）。0 以下で毎パス/毎 poll
    "auto_level_max": "assisted",   # 自動昇格の ceiling（unattended への自動到達は明示時のみ）
    "level_promote_after": 5,       # 昇格に要する連続 clean 数
    "level_window": 10,             # 手戻り率の評価窓（直近 N 件）
    "level_rework_max": 0.0,        # 昇格を許す最大 rework_rate
    "max_project_cycles": 5,        # project: 改善サイクルの上限（有限停止）
    "max_project_cost": 0.0,        # project: 累計コスト上限(USD・0=無制限)
    "project_stall": 2,             # project: acceptance PASS 数が増えない連続回数→人へ
    # 自動アップデート（既定 on）。watch のアイドル時に更新を取り込む。更新元は skill-registry.json から自動解決
    "update_enabled": True,              # 自動アップデートの ON/OFF（false で完全無効）
    "update_check_interval": 21600.0,    # 更新チェック間隔（秒）。既定 6 時間。0 以下で自動チェック無効
    "update_repo": DEFAULT_UPDATE_REPO,  # スキルリポジトリ（git URL/パス）。空なら skill-registry.json から自動解決
    "update_branch": "main",             # 追従するブランチ
    "update_subdir": TOOL_SUBDIR,        # リポジトリ内のこのツールのサブディレクトリ
    "update_installer": "install.sh",    # サブディレクトリ内で実行するインストーラ
    # 実行前レビュー（plan review）: 新規タスクは proposed で入り、人の承認（approve）で実行可能になる。
    # false で従来の自動投入（verify ありは即 ready）へ戻す。
    "plan_review": True,
    # 投入時アセスメント: 新規タスクを c=複雑さ/r=リスク/a=曖昧さ（各1-3）で採点し `- assess:` に記録。
    # 採点は情報のみ（plan-review 票・リスクダイジェスト・spec ルーティングが読む）。
    "assess": True,
    # spec ルーティング: 採点 max(c,r,a) >= spec_threshold のタスクに spec 前段タスクを前置
    # （specs/<id>/ の spec/design/tasks を人が承認してから実装へ）。opt-in。
    "spec_track": False,
    "spec_threshold": 3,
    # リポジトリ理解の成果物化: plan 前に context/<repo名>.md を生成（sha キャッシュ）。読み出しは常時。
    "repo_map": False,
    # 効いた learn を rules.md（全タスク常時注入のプロジェクトルール）へ自動昇格。読み出しは常時。
    "rules_capture": True,
    # 処理毎のエージェント上書き（yaml 専用）。キーは plan/review/prioritize/route/adjudicate/
    # verify/distill/assess/repo_map/doctor、値は {agent_cli, model}。
    "agents": {},
    # タスク単位ターゲットブランチ: 成果物を kp/<task-id> に集約（kiro-flow の workspace branch へ注入。
    # リトライ（r0/r1…）も同一ブランチに積み増す）。false で従来の run 毎 kf/<run-id>。
    "task_branch": True,
    "task_branch_prefix": "kp/",
    # 成果物レビュー: verify PASS 後、常に review（検収待ち）→ 人の承認で done 確定。
    # review 到達時に GitLab 設定（GITLAB_TOKEN/GL_TOKEN）があれば kp/<task-id> → target の MR を
    # 自動作成し、承認時にクリーン（コンフリクト無し・未解決ディスカッション無し）なら自動マージする。
    # false で従来の unattended 自動 done。
    "delivery_review": True,
    # 真偽フラグ（CLI > 設定ファイル > 既定）。CLI 未指定（None）なら設定ファイル→この既定で確定
    "watch": False, "once": False, "dry_run": False, "rot": False, "ltm": False,
    "require_progress": False, "auto_level": False, "review_project": False,
    "do_archive": True, "learn": True, "cleanup": True,   # do_archive: --archive はパス用なので別名
    "bus_keep_runs": 20,  # 掃除しても残す直近 run 数（viewer のフロータブが読む一次ソース）
    "with_flow": True,   # doctor: 実行層 kiro-flow doctor も連携実行（CLI 既定 on・直接 Config は off）
}


def _find_config(explicit):
    """設定ファイルの探索: 1) --config 明示 2) ./（ルート直下）3) ./.kiro/ 4) ~/.kiro/。
    ルート直下を最優先にするのは 1 root = 1 プロジェクト構成で kiro-project.yaml が
    プロジェクトのマニフェスト（viewer の自動発見マーカー）を兼ねるため。"""
    if explicit:
        p = os.path.expanduser(explicit)
        if not os.path.isfile(p):
            print(f"[kiro-project] 設定ファイルが見つかりません: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for base in (os.getcwd(),
                 os.path.join(os.getcwd(), ".kiro"),
                 os.path.join(os.path.expanduser("~"), ".kiro")):
        for name in DEFAULT_CONFIG_NAMES:
            cand = os.path.join(base, name)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_config(args):
    """CLI 未指定（None）の設定値だけを 設定ファイル→組み込み既定 で埋める（CLI > config > 既定）。"""
    path = _find_config(getattr(args, "config", None))
    cfg = _load_config_file(path) if path else {}
    args._config_path = path
    for key, dflt in CONFIG_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, cfg.get(key, dflt))
    return args


def build_config(args) -> Config:
    # プロジェクトルート = --root（cwd 相対、既定 . = cwd）が唯一のアンカー。charter.md /
    # repos.json / backlog/ 等はすべてこの直下（1 プロジェクト = 1 ディレクトリ = 1 プロセス）で、
    # 相対パスの上書きもすべて root 基準で解決する（viewer の bus 解決とも一致する）。
    root = Path(str(args.root or ".")).expanduser()
    root = (root if root.is_absolute() else (Path.cwd() / root)).resolve()
    # act / verify の作業ディレクトリ。相対値は root 基準（既定 . = root）。
    wd = Path(str(getattr(args, "workdir", None) or ".")).expanduser()
    workdir = (wd if wd.is_absolute() else (root / wd)).resolve()

    def under(name, sub):
        """個別指定があればそれ（相対は root 基準）を、無ければプロジェクトルート配下に集約。"""
        v = getattr(args, name, None)
        if v:
            p = Path(v)
            return p if p.is_absolute() else (root / p)
        return root / sub

    # エージェント CLI（分解・優先順位・裁定等の free 関数が参照）をここで確定する。
    global _AGENT_CLI, _AGENT_TIMEOUT, _AGENT_OVERRIDES
    _AGENT_CLI = str(getattr(args, "agent_cli", "kiro") or "kiro").lower()
    _AGENT_TIMEOUT = float(getattr(args, "agent_timeout", 300.0) or 0.0)
    _AGENT_OVERRIDES = _normalize_agent_overrides(getattr(args, "agents", None))
    # journal ローテーション（append_journal が参照する free 関数向け設定）も同時に確定する。
    global _JOURNAL_MAX_BYTES, _JOURNAL_KEEP
    try:
        _JOURNAL_MAX_BYTES = int(getattr(args, "journal_max_bytes", 262144) or 0)
    except (TypeError, ValueError):
        _JOURNAL_MAX_BYTES = 262144
    try:
        _JOURNAL_KEEP = int(getattr(args, "journal_keep", 20) or 0)
    except (TypeError, ValueError):
        _JOURNAL_KEEP = 20

    return Config(
        backlog=under("backlog", "backlog"),
        policy=under("policy", "policy.md"),
        decisions=under("decisions", "decisions"),
        journal=under("journal", "journal.md"),
        needs=under("needs", "needs"),
        workdir=workdir,
        bus=under("bus", "bus"),
        git_bus=args.git_bus, git_branch=args.git_branch, git_subdir=args.git_subdir,
        state_git=getattr(args, "state_git", None) or None,
        state_git_branch=str(getattr(args, "state_git_branch", "main") or "main"),
        state_git_subdir=str(getattr(args, "state_git_subdir", "kiro-project") or "").strip("/"),
        state_git_interval=max(0.0, float(getattr(args, "state_git_interval", 300.0) or 0.0)),
        manage_flow_daemon=bool(getattr(args, "manage_flow_daemon", False)),
        flow_config=getattr(args, "flow_config", None) or None,
        flow_max_workers=max(1, int(getattr(args, "flow_max_workers", 4) or 4)),
        status_interval=max(0.0, float(getattr(args, "status_interval", 0.0) or 0.0)),
        lock_dir=getattr(args, "lock_dir", None),
        kiro_flow=args.kiro_flow, planner=args.planner, flow_planner=args.flow_planner,
        route_planner=str(getattr(args, "route_planner", "agent") or "agent"),
        default_workspace=str(getattr(args, "default_workspace", "") or ""),
        location=args.location, executor=args.executor,
        model=args.model,
        agent_cli=_AGENT_CLI, agent_timeout=_AGENT_TIMEOUT,
        granularity=str(getattr(args, "granularity", "coarse") or "coarse").lower(),
        max_iterations=args.max_iterations,
        max_cycles=args.max_cycles, max_seconds=args.max_seconds,
        max_tokens=getattr(args, "max_tokens", 0) or 0,
        max_cost=getattr(args, "max_cost", 0.0) or 0.0,
        max_retries=args.max_retries, pace=args.pace, verify_timeout=args.verify_timeout,
        verify_confirm=max(1, int(getattr(args, "verify_confirm", 1) or 1)),
        verify_cwd=getattr(args, "verify_cwd", None),
        act_timeout=args.act_timeout, act_async=bool(getattr(args, "act_async", False)),
        notify_cmd=args.notify_cmd, actor=args.actor,
        archive=under("archive", "archive"), do_archive=bool(getattr(args, "do_archive", True)),
        learn=bool(getattr(args, "learn", True)),
        learn_capture=bool(getattr(args, "learn_capture", True)),
        distill_learn=bool(getattr(args, "distill_learn", True)),
        verify_validate=str(getattr(args, "verify_validate", "synth") or "synth"),
        reject_recur=int(getattr(args, "reject_recur", 2) or 0),
        intake_recall=bool(getattr(args, "intake_recall", True)),
        learn_threshold=args.learn_threshold,
        auto_adjudicate=bool(getattr(args, "auto_adjudicate", True)),
        adjudicate_max=getattr(args, "adjudicate_max", 1),
        max_spawn=getattr(args, "max_spawn", 20),
        regression_cmd=getattr(args, "regression_cmd", None),
        regression_revert=bool(getattr(args, "regression_revert", False)),
        intake_cmd=getattr(args, "intake_cmd", None),
        intake_interval=float(getattr(args, "intake_interval", 600.0) or 0.0),
        require_progress=bool(getattr(args, "require_progress", False)),
        auto_level=bool(getattr(args, "auto_level", False)),
        auto_level_max=str(getattr(args, "auto_level_max", "assisted") or "assisted"),
        level_promote_after=max(1, int(getattr(args, "level_promote_after", 5) or 5)),
        level_window=max(1, int(getattr(args, "level_window", 10) or 10)),
        level_rework_max=max(0.0, float(getattr(args, "level_rework_max", 0.0) or 0.0)),
        ltm=bool(getattr(args, "ltm", False)), ltm_home=resolve_ltm_home(getattr(args, "ltm_home", None)),
        promote_threshold=getattr(args, "promote_threshold", 2),
        rot=bool(getattr(args, "rot", False)), rot_age_days=args.rot_age_days,
        cleanup=bool(getattr(args, "cleanup", True)),
        bus_keep_runs=max(0, int(getattr(args, "bus_keep_runs", 20) or 0)),
        delivery=under("delivery", "DELIVERY.md"), inbox=under("inbox", "inbox"),
        runlog=under("runlog", "run-log.jsonl"),
        throttle=max(0.0, float(getattr(args, "throttle", 0.0) or 0.0)),
        debounce=args.debounce,
        watch=bool(getattr(args, "watch", False)), poll=getattr(args, "poll", 5.0),
        concurrency=max(1, int(getattr(args, "concurrency", 1) or 1)),
        level=getattr(args, "level", None) or "unattended",
        plan_review=bool(getattr(args, "plan_review", True)),
        assess=bool(getattr(args, "assess", True)),
        spec_track=bool(getattr(args, "spec_track", False)),
        spec_threshold=min(3, max(1, int(getattr(args, "spec_threshold", 3) or 3))),
        repo_map=bool(getattr(args, "repo_map", False)),
        rules_capture=bool(getattr(args, "rules_capture", True)),
        agents=_AGENT_OVERRIDES,
        task_branch=bool(getattr(args, "task_branch", True)),
        task_branch_prefix=str(getattr(args, "task_branch_prefix", "kp/") or "kp/"),
        delivery_review=bool(getattr(args, "delivery_review", True)),
        registry=_split_registry(getattr(args, "registry", None)),
        dry_run=bool(getattr(args, "dry_run", False)), once=bool(getattr(args, "once", False)),
        project_name=root.name,
        charter=under("charter", "charter.md"),
        review_project=bool(getattr(args, "review_project", False)),
        max_project_cycles=max(1, int(getattr(args, "max_project_cycles", 5) or 5)),
        max_project_cost=max(0.0, float(getattr(args, "max_project_cost", 0.0) or 0.0)),
        project_stall=max(1, int(getattr(args, "project_stall", 2) or 2)),
        with_flow=bool(getattr(args, "with_flow", False)),
        update_enabled=bool(getattr(args, "update_enabled", True)),
        update_check_interval=max(0.0, float(getattr(args, "update_check_interval", 0.0) or 0.0)),
        update_repo=getattr(args, "update_repo", None) or None,
        update_branch=str(getattr(args, "update_branch", "main") or "main"),
        update_subdir=str(getattr(args, "update_subdir", TOOL_SUBDIR) or TOOL_SUBDIR),
        update_installer=str(getattr(args, "update_installer", "install.sh") or "install.sh"),
    )


def _add_common(sp):
    # 設定ファイルで上書き可能なキー（CONFIG_DEFAULTS）は default=None にし、resolve_config で確定する
    # （CLI > 設定ファイル > 組み込み既定）。個別パス上書きと真偽フラグは CLI 専用。
    sp.add_argument("--config", default=None,
                    help="設定ファイル（未指定なら ./ → ./.kiro → ~/.kiro の kiro-project.{yaml,yml,json}）")
    sp.add_argument("--root", default=None,
                    help="プロジェクトルート（cwd 相対、既定 . = cwd）。charter.md / backlog/ 等はこの直下。"
                         "相対パスの上書きはすべてこの root 基準で解決される")
    sp.add_argument("--backlog", default=None, help="バックログディレクトリ（既定 <root>/backlog）")
    sp.add_argument("--policy", default=None, help="（既定 <root>/policy.md）")
    sp.add_argument("--decisions", default=None, help="決定記録ディレクトリ（既定 <root>/decisions）")
    sp.add_argument("--journal", default=None, help="（既定 <root>/journal.md）")
    sp.add_argument("--needs", default=None, help="要対応ディレクトリ（既定 <root>/needs）")
    sp.add_argument("--archive", default=None, help="done の退避先（既定 <root>/archive）")
    sp.add_argument("--delivery", default=None, help="納品一覧（既定 <root>/DELIVERY.md）")
    sp.add_argument("--inbox", default=None, help="取り込み待ちのドロップ口（既定 <project>/inbox）")
    sp.add_argument("--debounce", type=float, default=None,
                    help="watch 中、最終保存からこの秒数は feedback 取込を待つ（誤発火防止。既定 3）")
    sp.add_argument("--workdir", default=None,
                    help="act / verify の作業ディレクトリ（root 相対、既定 . = root）")
    sp.add_argument("--bus", default=None, help="kiro-flow バス（root 相対、既定 <root>/bus）")
    sp.add_argument("--agent-cli", dest="agent_cli", default=None, choices=["kiro", "claude", "copilot", "codex"],
                    help="LLM 実行に使うエージェント CLI（設定 agent_cli と同義）。kiro=kiro-cli chat（既定）/ "
                         "claude=Claude Code ヘッドレス（claude -p）/ copilot=GitHub Copilot CLI（copilot -p）/ "
                         "codex=OpenAI Codex CLI（codex exec）")
    sp.add_argument("--granularity", default=None, choices=["coarse", "fine", "finest"],
                    help="バックログ分解の粒度（設定 granularity と同義）。coarse=ストーリー相当（既定）/ "
                         "fine=単機能 / finest=1ファイル/1関数の最小単位")
    sp.add_argument("--git-bus", default=None, help="分散移譲先の共有 git リポジトリ")
    sp.add_argument("--git-branch", default=None)
    sp.add_argument("--git-subdir", default=None)
    sp.add_argument("--state-git", default=None,
                    help="ワーク内容（プロジェクトルートの状態）を保存・共有する git リポジトリ（URL/パス）。"
                         "リモートの kiro-projects-viewer と結果/指示を双方向で往復する。"
                         "ルート自体が git クローンなら不要（direct モードで直接コミット・push する）")
    sp.add_argument("--state-git-branch", default=None, help="state_git の同期先ブランチ（既定 main）")
    sp.add_argument("--state-git-subdir", default=None,
                    help="state_git リポジトリ内の保存先サブディレクトリ（既定 kiro-project）。"
                         "同一リポジトリへ他プログラムもコミットする前提の名前空間分離")
    sp.add_argument("--state-git-interval", type=float, default=None,
                    help="state_git の fetch/push の最短間隔（秒。既定 300）。リモートサーバへの"
                         "負荷を一定に保つ律速。0 で毎同期")
    sp.add_argument("--status-interval", type=float, default=None,
                    help="watch アイドル中に status.json（生存信号。リモート viewer の稼働判定に使う）を"
                         "更新する間隔（秒。既定 0＝無効）。0 のままなら idle 中に status.json は触らず、"
                         "state_git への追加コミットは生まない（実パスの完了時にのみ書く＝相乗り）。"
                         ">0 にすると idle でもこの間隔で 1 回だけ書き直し、その分だけ state_git の"
                         "コミットが増える（負荷とリモートでの生存判定の鮮度のトレードオフ）")
    sp.add_argument("--lock-dir", dest="lock_dir", default=None,
                    help="kiro-flow daemon ロックの置き場（設定ファイル lock_dir と同義）。"
                         "外部起動の daemon を発見するため kiro-flow 側と一致させる")
    sp.add_argument("--kiro-flow", default=None)
    sp.add_argument("--planner", default=None, choices=["agent", "none"],
                    help="優先順位付け: agent=エージェント委譲（priority 加味）/ none=priority＋古さ（既定 agent）")
    sp.add_argument("--flow-planner", default=None,
                    choices=["flow-planner", "agent", "stub"], help="kiro-flow run に渡す planner（既定 flow-planner）")
    sp.add_argument("--location", default=None,
                    choices=["auto", "local", "daemon", "remote"], help="act の実行モード（既定 auto）")
    sp.add_argument("--executor", default=None,
                    help="act の実体（kiro-flow run へ委譲）。組み込み agent / stub、または kiro-flow の "
                         "executor プラグイン名（例 gitlab）/ .py パスを指定できる（既定 agent）")
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-iterations", type=int, default=None)
    sp.add_argument("--max-cycles", type=int, default=None, help="予算: サイクル数（既定 20）")
    sp.add_argument("--max-seconds", type=float, default=None, help="予算: 実時間（0=無制限）")
    sp.add_argument("--max-tokens", type=int, default=None,
                    help="予算: 消費トークン上限（0=無制限。act 出力の @cost を計上）")
    sp.add_argument("--max-cost", type=float, default=None,
                    help="予算: 金額(USD)上限（0=無制限。act 出力の @cost usd= を計上）")
    sp.add_argument("--max-retries", type=int, default=None)
    sp.add_argument("--pace", type=float, default=None, help="1サイクルの下限間隔（秒）。レーン減速")
    sp.add_argument("--verify-timeout", type=float, default=None)
    sp.add_argument("--verify-confirm", type=int, default=None,
                    help="verify をこの回数まで再実行し PASS/FAIL が跨いだら flake として人へ隔離（既定 1）。"
                         "揺れる verify の NG churn / flaky PASS の done を防ぐ（コストは回数分）")
    sp.add_argument("--verify-cwd", default=None,
                    help="verify/acceptance を実行する作業ディレクトリ（既定 workdir）。git-bus 等で workdir に"
                         "成果が無いとき、対象 repo のクローン先を指す。未指定でも charter に単一 repo があれば"
                         "acceptance はその repo を一時 clone して実行する")
    sp.add_argument("--act-timeout", type=float, default=None)
    sp.add_argument("--act-async", dest="act_async", action="store_true", default=None,
                    help="非ブロッキング委譲: daemon/remote へ submit して待たず offloaded にし、"
                         "次パスでポーリングして回収する（gitlab 等の長期委譲でループを塞がない）")
    sp.add_argument("--notify-cmd", default=None, help="要対応ダイジェストを渡す通知コマンド")
    sp.add_argument("--actor", default=None)
    sp.add_argument("--learn", action=argparse.BooleanOptionalAction, default=None,
                    help="DR 学習（過去の人の判断から類似案件を自動解決）。--no-learn で無効化（既定 on）")
    sp.add_argument("--learn-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="人の判断（approve 理由→learn / hold 理由→avoid / gitlab 却下コメント→learn）を"
                         "自動抽出して蓄積。--no-learn-capture で無効化（既定 on）")
    sp.add_argument("--distill-learn", action=argparse.BooleanOptionalAction, default=None,
                    help="人コメントを一般化ルールへ蒸留してから learn 化。--no-distill-learn で"
                         "生の指摘をそのまま残す（既定 on・蒸留失敗時も生でフォールバック）")
    sp.add_argument("--verify-validate", choices=["off", "synth", "all"], default=None,
                    help="red-green 検証: 合成 verify が act 前でも PASS（=変更を弁別しない偽 done）を弾く。"
                         "off/synth（自動生成のみ・既定）/all。per-task `- verify_validate: none` で除外")
    sp.add_argument("--reject-recur", type=int, default=None,
                    help="同種の gitlab 却下がこの回数に達したら silent 積み直しをやめ『系の再考』で人へ"
                         "（分解/verify/policy の見直し。0/負で無効・既定 2）")
    sp.add_argument("--intake-recall", action=argparse.BooleanOptionalAction, default=None,
                    help="投入/triage 時に過去の hold（avoid）と照合し、類似は ready へ落とさず inbox（人へ）"
                         "寄せる予防リコール。--no-intake-recall で無効化（既定 on）")
    sp.add_argument("--learn-threshold", type=float, default=None,
                    help="DR 学習・予防リコールのタイトル類似度しきい値（0〜1。既定 0.5）")
    # 自律裁定: needs に落とす前に kiro-cli が積み直し可否を判断（三値: 未指定→設定ファイル/既定 on）
    sp.add_argument("--auto-adjudicate", dest="auto_adjudicate", action="store_true", default=None,
                    help="人の判断(needs)へ送る前に kiro-cli が『自律的に積み直すか人へ回すか』を裁定（既定 on）")
    sp.add_argument("--no-auto-adjudicate", dest="auto_adjudicate", action="store_false",
                    default=None, help="自律裁定を無効化して常に人へ回す（明示 off）")
    sp.add_argument("--adjudicate-max", type=int, default=None,
                    help="1タスクあたりの自律裁定の上限回数（有限停止のため。既定 1）")
    sp.add_argument("--max-spawn", type=int, default=None,
                    help="1 run で生成できる派生タスク（followup）数の上限（0 で無効。既定 20）")
    sp.add_argument("--regression-cmd", default=None,
                    help="done 確定前に走らせるグローバル回帰検査（失敗で done にせず人へ。巻き込み事故の検知）")
    sp.add_argument("--regression-revert", action=argparse.BooleanOptionalAction, default=None,
                    help="回帰検知時に作業ツリーの未コミット変更を巻き戻す（best-effort・既定 off）")
    sp.add_argument("--intake-cmd", default=None,
                    help="外部の決定的ゲート/検出器から修復タスクを汲み上げるコマンド（stdout の "
                         "enqueue --json 形式を冪等取り込み。例: codd-gate tasks --debt。"
                         "単発・有界なコマンドであること＝常駐はこちらが持つ）")
    sp.add_argument("--intake-interval", type=float, default=None,
                    help="intake の実行間隔（秒。既定 600。0 以下で毎パス/毎 poll）")
    sp.add_argument("--ltm", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習を ltm-use 長期記憶へ昇格＋プロジェクト横断 recall（既定 off）")
    sp.add_argument("--ltm-home", default=None,
                    help="ltm-use ストアのルート（既定 KIRO_LTM_HOME → ~/.claude）")
    sp.add_argument("--promote-threshold", type=int, default=None,
                    help="learn ルールがこの回数以上効いたら昇格（既定 2）")
    sp.add_argument("--rot-age-days", type=float, default=None,
                    help="rot の stale 判定（経過日数。既定 14）")
    sp.add_argument("--plan-review", action=argparse.BooleanOptionalAction, default=None,
                    help="実行前レビュー: 新規タスクを proposed で入れ、人の承認で実行可能にする"
                         "（--no-plan-review で従来の自動投入。既定 on）")
    sp.add_argument("--assess", action=argparse.BooleanOptionalAction, default=None,
                    help="投入時アセスメント: 新規タスクを c=複雑さ/r=リスク/a=曖昧さ（各1-3）で採点し"
                         " `- assess:` に記録（表示のみ・実行可否は不変。既定 on）")
    sp.add_argument("--spec-track", action=argparse.BooleanOptionalAction, default=None,
                    help="spec ルーティング: 採点が --spec-threshold に達したタスクに spec 前段タスク"
                         "（specs/<id>/ の spec/design/tasks 作成→人の承認→実装タスク展開）を前置（既定 off）")
    sp.add_argument("--spec-threshold", type=int, default=None,
                    help="spec ルートに乗せる採点しきい値（max(c,r,a) がこの値以上。1-3・既定 3）")
    sp.add_argument("--repo-map", action=argparse.BooleanOptionalAction, default=None,
                    help="リポジトリ理解の成果物化: plan 前に書込先 repo ごとに context/<repo名>.md を"
                         "生成（HEAD sha キャッシュ・変化時のみ再生成）。読み出しは常時（既定 off）")
    sp.add_argument("--rules-capture", action=argparse.BooleanOptionalAction, default=None,
                    help="効いた学習（learn の auto-resolve が閾値回数以上）を rules.md（プロジェクト"
                         "ルール・全タスク常時注入）へ自動昇格（既定 on。rules.md の注入自体は常時）")
    sp.add_argument("--task-branch", action=argparse.BooleanOptionalAction, default=None,
                    help="タスク単位ターゲットブランチ（kp/<task-id> に成果を集約。既定 on）")
    sp.add_argument("--delivery-review", action=argparse.BooleanOptionalAction, default=None,
                    help="成果物レビュー: verify PASS 後、常に検収待ち（review）にして人の承認で done"
                         "（--no-delivery-review で従来の自動 done。既定 on）")


# ---------------------------------------------------------------------------
# 自動アップデート — スキルリポジトリ（main）の更新を取り込み graceful 再起動する
# ---------------------------------------------------------------------------
# doctor と同じ流儀（知能は委譲・操作は決定的）で、本体は「決定的な取り込み」だけを行う:
#   1. git ls-remote でスキルリポジトリ main の最新コミットを得る
#   2. 適用済み SHA（state ファイル）と違えば「更新あり」
#   3. アイドル時に temp 領域へ sparse-checkout（このツールの tools/kiro-project/ だけ）
#   4. install.sh を実行して ~/.local/bin の本体を更新
#   5. 動いていた cwd のまま os.execv で新しい本体へ graceful 再起動
# update_repo 未設定 or update_check_interval<=0 のときは完全に無効（既定 off）。
class _RestartRequested(Exception):
    """自己更新の適用後に graceful 再起動を要求する内部シグナル。
    watch 常駐の finally（レジストリ後始末）を必ず通してから exec するため例外で伝播する。"""


# 更新チェックの最終実行時刻（プロセス内 1 watcher 前提のモジュール状態）。
_UPDATE_LAST_CHECK = {"t": 0.0}


def _update_state_path() -> Path:
    base = os.environ.get("KIRO_STATE_HOME") or os.path.expanduser("~/.kiro")
    return Path(base) / "kiro-project.update.json"


def read_update_state() -> dict:
    try:
        return json.loads(_update_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_update_state(state: dict) -> None:
    p = _update_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def remote_branch_sha(repo: str, branch: str, runner=None) -> "str | None":
    """git ls-remote でリモート branch の先頭コミット SHA を得る（取得不能なら None）。"""
    if not repo:
        return None
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=60))
    try:
        r = run(["git", "ls-remote", repo, f"refs/heads/{branch}"])
    except Exception:  # noqa: BLE001  git 不在・ネットワーク不通・タイムアウト
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    lines = (getattr(r, "stdout", "") or "").strip().splitlines()
    if not lines:
        return None
    sha = lines[0].split()[0].strip()
    return sha if len(sha) >= 7 else None


def find_skill_registry(home: "str | None" = None) -> "str | None":
    """install.py が生成する skill-registry.json を探す（無ければ None）。
    $KIRO_SKILL_REGISTRY（ファイル or ディレクトリ）が指定されていれば**それを権威として使い**
    （フォールバックしない）、未指定なら各エージェントホーム（~/.kiro / ~/.claude 等）を探す。"""
    env = home or os.environ.get("KIRO_SKILL_REGISTRY")
    if env:
        p = os.path.expanduser(env)
        cand = os.path.join(p, "skill-registry.json") if os.path.isdir(p) else p
        return cand if os.path.isfile(cand) else None
    for d in _AGENT_HOME_DIRS:
        c = os.path.join(os.path.expanduser("~"), d, "skill-registry.json")
        if os.path.isfile(c):
            return c
    return None


def registry_update_source(registry: "str | None" = None) -> "tuple[str | None, str | None]":
    """skill-registry.json からスキルリポジトリの (url, branch) を解決する（無ければ (None, None)）。
    repositories の origin（無ければ priority 昇順の先頭）を採り、url が無ければ install_dir
    （インストール元のローカルクローン＝『自動更新の参照元』）にフォールバックする。"""
    path = registry or find_skill_registry()
    if not path or not os.path.isfile(path):
        return (None, None)
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return (None, None)
    repos = reg.get("repositories") or []
    chosen = next((r for r in repos if r.get("name") == "origin"), None)
    if chosen is None and repos:
        chosen = sorted(repos, key=lambda r: r.get("priority", 99))[0]
    if chosen and chosen.get("url"):
        return (chosen["url"], chosen.get("branch") or "main")
    idir = reg.get("install_dir")               # フォールバック: ローカルクローンを直接 clone 元に
    if idir and os.path.isdir(idir):
        return (idir, (chosen.get("branch") if chosen else None) or "main")
    return (None, None)


def resolve_update_target(cfg: "Config") -> "tuple[str, str]":
    """更新元リポジトリと branch を確定する。優先順位 設定の update_repo > skill-registry.json > 無効。
    update_repo 未指定（自動）のときは registry の branch を採用（設定 update_branch が既定 main のまま時）。"""
    repo = cfg.update_repo or ""
    branch = cfg.update_branch or "main"
    if not repo:
        rurl, rbranch = registry_update_source()
        if rurl:
            repo = rurl
            if rbranch and branch == "main":     # 設定で branch を変えていなければ registry を採用
                branch = rbranch
    return repo, branch


def check_update(cfg: "Config", runner=None) -> dict:
    """更新の有無を判定する（取り込みはしない）。戻り値の dict:
      {enabled, repo, branch, remote_sha, applied_sha, available, baseline}
    repo は設定 update_repo か skill-registry.json から解決する。
    初回（applied_sha 未記録）は現在の本体を最新とみなし remote_sha をベースライン記録して
    available=False を返す（無用な初回更新ループを避ける）。"""
    repo, branch = resolve_update_target(cfg)
    info = {"enabled": bool(repo), "repo": repo, "branch": branch, "remote_sha": None,
            "applied_sha": None, "available": False, "baseline": False}
    if not repo:
        return info
    state = read_update_state()
    info["applied_sha"] = state.get("applied_sha")
    remote = remote_branch_sha(repo, branch, runner=runner)
    info["remote_sha"] = remote
    if not remote:
        return info
    if not info["applied_sha"]:
        state["applied_sha"] = remote
        state["baseline_at"] = _now_ts()
        write_update_state(state)
        info["applied_sha"] = remote
        info["baseline"] = True
        return info
    info["available"] = (remote != info["applied_sha"])
    return info


def sparse_checkout_tool(repo: str, branch: str, subdir: str, dest: str, runner=None) -> str:
    """repo の branch から subdir 以下だけを dest へ sparse-checkout し dest/subdir のパスを返す。
    無関係ファイルを取得しないため --no-checkout + blob フィルタ + sparse-checkout を使う。"""
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    r = run(["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none",
             "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:   # blob フィルタ非対応サーバ向けフォールバック
        r = run(["git", "clone", "--no-checkout", "--depth", "1", "--branch", branch, repo, dest])
    if getattr(r, "returncode", 1) != 0:
        raise RuntimeError(f"git clone 失敗: {(getattr(r, 'stderr', '') or '').strip()[:300]}")

    def g(cmd):
        return run(["git", "-C", dest] + cmd)
    g(["sparse-checkout", "init", "--cone"])
    g(["sparse-checkout", "set", subdir])
    co = g(["checkout", branch])
    if getattr(co, "returncode", 1) != 0:
        raise RuntimeError(f"git checkout 失敗: {(getattr(co, 'stderr', '') or '').strip()[:300]}")
    tool_dir = os.path.join(dest, subdir)
    if not os.path.isdir(tool_dir):
        raise RuntimeError(f"sparse-checkout 後に {subdir} が見つかりません（リポジトリ構成を確認）")
    return tool_dir


def run_installer(tool_dir: str, installer: str = "install.sh", runner=None) -> "tuple[bool, str]":
    """tool_dir 内の installer を実行して本体を更新する。(成功, 末尾出力) を返す。"""
    path = os.path.join(tool_dir, installer)
    if not os.path.isfile(path):
        return False, f"インストーラが見つかりません: {path}"
    run = runner or (lambda c, **k: subprocess.run(c, capture_output=True, text=True,
                                                   timeout=600, **k))
    try:
        r = run(["bash", path], cwd=tool_dir)
    except Exception as e:  # noqa: BLE001
        return False, f"インストーラ実行に失敗: {e}"
    out = ((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")).strip()
    return getattr(r, "returncode", 1) == 0, out[-2000:]


def _tree_digest(root: str) -> str:
    """ツールディレクトリの内容ダイジェスト（.git を除く、相対パス＋内容の sha256）。
    「リポジトリの HEAD は進んだが本体（update_subdir）は変わっていない」を判定する
    （コミット SHA の比較では判別できない）。"""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, root).encode("utf-8"))
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
            except OSError:
                continue
    return h.hexdigest()


def apply_update(cfg: "Config", info: dict, runner=None) -> bool:
    """temp 領域へ sparse-checkout → install.sh → 適用済み SHA を記録。成功で True。
    temp は必ず後始末する。失敗時は state を変えない（次回再試行）。
    subdir の内容が前回適用時と同一なら installer を実行せずベースラインだけ進めて False:
    direct state-git 構成では自分の state sync push が update_repo の新コミットになるため、
    SHA 比較だけだと「自分の push → 更新検出 → 再起動 → また push」の自己増殖ループになる。"""
    subdir = cfg.update_subdir or TOOL_SUBDIR
    installer = cfg.update_installer or "install.sh"
    tmp = tempfile.mkdtemp(prefix="kiro-project-update-")
    dest = os.path.join(tmp, "repo")
    try:
        tool_dir = sparse_checkout_tool(info["repo"], info["branch"], subdir, dest, runner=runner)
        digest = _tree_digest(tool_dir)
        state = read_update_state()
        if digest == state.get("applied_digest"):
            state["applied_sha"] = info["remote_sha"]
            state["skipped_at"] = _now_ts()
            write_update_state(state)
            print(f"[update] 本体（{subdir}）に変更なし——適用をスキップし "
                  f"ベースラインを {info['remote_sha'][:8]} へ進めました。", flush=True)
            return False
        ok, out = run_installer(tool_dir, installer, runner=runner)
        if not ok:
            print(f"[update] install.sh 失敗（更新を見送り）: {out[-300:]}", flush=True)
            append_journal(cfg.journal, "=== update: install.sh 失敗（更新を見送り・次回再試行）===")
            return False
        state = read_update_state()
        state["applied_sha"] = info["remote_sha"]
        state["applied_digest"] = digest
        state["applied_at"] = _now_ts()
        write_update_state(state)
        print(f"[update] 更新を適用しました（{info['remote_sha'][:8]}）。", flush=True)
        append_journal(cfg.journal, f"=== update: 更新を適用（{info['remote_sha'][:8]}）===")
        return True
    except Exception as e:  # noqa: BLE001  clone/checkout/installer の失敗は次回再試行
        print(f"[update] 更新の取り込みに失敗（次回再試行）: {e}", flush=True)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart_self(cwd: "str | None" = None) -> None:
    """更新後の本体へ os.execv で graceful 再起動する。動いていた cwd を保ったまま起動し直す。"""
    if cwd and os.path.isdir(cwd):
        try:
            os.chdir(cwd)
        except OSError:
            pass
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, _self_script()] + sys.argv[1:])


def maybe_self_update(cfg: "Config", runner=None) -> bool:
    """watch のアイドル時に定期的に呼ぶ自己更新チェック。更新を適用したら True
    （呼び出し側は _RestartRequested を投げて finally 後始末の後に restart_self する）。
    update_enabled=false / update_check_interval<=0 で無効。間隔は前回からの経過で律速する。"""
    if not cfg.update_enabled:
        return False
    interval = float(cfg.update_check_interval or 0)
    if interval <= 0:
        return False
    now = time.time()
    # 前回チェック時刻は state ファイルにも持続化して参照する。自己更新は restart_self の
    # 新プロセスになりメモリの時刻がリセットされるため、メモリだけだと再起動直後に即時
    # 再チェック→再適用→再起動…の自己増殖ループになる。
    try:
        persisted = float(read_update_state().get("last_check_at") or 0.0)
    except (TypeError, ValueError):
        persisted = 0.0
    if now - max(_UPDATE_LAST_CHECK["t"], persisted) < interval:
        return False
    _UPDATE_LAST_CHECK["t"] = now
    state = read_update_state()
    state["last_check_at"] = now
    write_update_state(state)
    info = check_update(cfg, runner=runner)
    if not info.get("available"):
        return False
    print(f"[update] スキルリポジトリ {info['branch']} に更新を検出: "
          f"{(info['applied_sha'] or '')[:8]} → {(info['remote_sha'] or '')[:8]}", flush=True)
    return apply_update(cfg, info, runner=runner)


def cmd_update(cfg: "Config", now: bool = False, check: bool = False) -> int:
    """手動アップデート: 更新の有無を確認し、--now で取り込んで再起動する。
    終了コード: 0=最新/ベースライン記録/更新あり表示 / 1=取り込み失敗 / 2=未設定・取得不能。"""
    info = check_update(cfg)
    if not info["enabled"]:
        print("[kiro-project] update: update_repo が未設定です（設定ファイルで指定してください）。",
              file=sys.stderr)
        return 2
    if info["remote_sha"] is None:
        print(f"[kiro-project] update: リモート {info['repo']}@{info['branch']} を取得できませんでした。",
              file=sys.stderr)
        return 2
    if info.get("baseline"):
        print(f"[kiro-project] update: ベースラインを記録しました（{info['remote_sha'][:8]}）。"
              "以降この地点からの更新を検出します。")
        return 0
    if not info["available"]:
        print(f"[kiro-project] update: 最新です（{info['applied_sha'][:8]}）。")
        return 0
    print(f"[kiro-project] update: 更新があります "
          f"{info['applied_sha'][:8]} → {info['remote_sha'][:8]}")
    if check or not now:
        print("  取り込むには `kiro-project update --now` を実行してください。")
        return 0
    if apply_update(cfg, info):
        print("  install.sh を実行して更新しました。再起動します。")
        restart_self(_START_CWD or os.getcwd())   # 戻らない
    if read_update_state().get("applied_sha") == info.get("remote_sha"):
        print("  本体（update_subdir）に変更が無かったため適用をスキップし、ベースラインだけ進めました。")
        return 0
    print("  更新の取り込みに失敗しました（ログを確認してください）。", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    global _START_CWD
    _START_CWD = os.getcwd()   # 自己更新の graceful 再起動で「動いていた cwd」へ戻すために捕捉
    if argv is None:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="kiro-project",
        description="backlog/ を優先順位付け・検証・収束させる制御層（Loop Engineering MVP）。"
                    "サブコマンドを省略すると常駐監視（run --watch）で起動し backlog 投入を待ち続ける")
    sub = p.add_subparsers(dest="cmd", required=False)

    run = sub.add_parser("run", help="正準ループ（優先順位付け→実行→検証→積み直し→収束）。"
                                     "<project>/charter.md があれば自動で目標駆動（plan→execute→evaluate）")
    _add_common(run)
    run.add_argument("--watch", action=argparse.BooleanOptionalAction, default=None,
                     help="終了条件後もプロセスを残し backlog を監視（エージェントは待機しない）")
    run.add_argument("--poll", type=float, default=None, help="watch のポーリング間隔（秒。既定 5）")
    run.add_argument("--level", default=None, choices=["report", "assisted", "unattended"],
                     help="自律度の段階導入（既定 unattended）。report=実行せず計画報告のみ／"
                          "assisted=実行するが done は人が承認（全件 review）／unattended=現行（自動 done）。"
                          "タスク毎に `- level:` で上書き可、`- track:` 群は --auto-level で実績連動昇格")
    run.add_argument("--auto-level", action=argparse.BooleanOptionalAction, default=None,
                     help="実績連動の自動昇格（opt-in）。`- track:` 群の手戻り率が低ければ level を自動で上げ、"
                          "手戻りで下げる。ceiling は --auto-level-max（既定 assisted）")
    run.add_argument("--auto-level-max", default=None, choices=["report", "assisted", "unattended"],
                     help="自動昇格の上限（既定 assisted）。unattended にすると完全無人化への自動到達を解禁")
    run.add_argument("--throttle", type=float, default=None,
                     help="ソフト予算比率(0=off)。max_tokens/max_cost のこの割合(例 0.8)で run を打ち切り、"
                          "watch は以降 report へ降格（act 停止）。ハード上限の手前で緩やかに止める")
    run.add_argument("--concurrency", type=int, default=None,
                     help="1サイクルで daemon/remote へ並行 submit する独立タスク数（既定 1=逐次。"
                          "kiro-flow の worker 並列に委ねる。local 実行は逐次のまま）")
    run.add_argument("--registry", action="append", default=None,
                     help="共有レジストリへも自分を登録（別ホスト発見。os.pathsep 区切り可・"
                          "環境変数 KIRO_PROJECTS_REGISTRY でも指定可）")
    run.add_argument("--no-archive", dest="do_archive", action="store_const", const=False,
                     default=None, help="done を archive/ へ退避せず削除（既定は退避。config: do_archive）")
    run.add_argument("--rot", action=argparse.BooleanOptionalAction, default=None,
                     help="triage で rot（古い/重複/実行不能）を検知し人の判断へ回す")
    run.add_argument("--require-progress", action=argparse.BooleanOptionalAction, default=None,
                     help="verify=PASS でも act が baseline 以降に変更を生んでなければ done せず人へ"
                          "（履歴一致 verify の偽 done 対策。タスク毎に - expect: changes / none で上書き）")
    run.add_argument("--cleanup", action=argparse.BooleanOptionalAction, default=None,
                     help="run 後に kiro-flow バスの一時状態を掃除（--no-cleanup で残す。既定 on）")
    run.add_argument("--bus-keep-runs", type=int, default=None,
                     help="掃除しても残す直近 run 数（viewer のフローが読む。既定 20・0 で全消し）")
    run.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None,
                     help="act を飛ばし verify のみ")
    run.add_argument("--once", action=argparse.BooleanOptionalAction, default=None,
                     help="1 タスクだけ処理して終了")
    # charter 駆動（目標から回す）。<project>/charter.md があれば run が自動で plan→execute→evaluate に入る
    run.add_argument("--charter", default=None,
                     help="プロジェクト憲章ファイル（既定 <project>/charter.md。あれば run が目標駆動になる）")
    run.add_argument("--review-project", action=argparse.BooleanOptionalAction, default=None,
                     help="charter 駆動時、evaluate で敵対的レビューを上乗せ（全 PASS でも短絡的達成を疑う・opt-in）")
    run.add_argument("--max-project-cycles", type=int, default=None,
                     help="charter 駆動時の改善サイクル上限（有限停止・既定 5）")
    run.add_argument("--max-project-cost", type=float, default=None,
                     help="charter 駆動時のプロジェクト累計コスト上限(USD・0=無制限)")
    run.add_argument("--project-stall", type=int, default=None,
                     help="charter 駆動時、acceptance PASS 数が増えない連続回数の上限→人へ（既定 2）")

    for name, helptext in [("triage", "優先順位付けのみ（inbox→ready 昇格・policy 適用）"),
                           ("needs", "人の判断待ち（blocked / need_intake）を表示"),
                           ("promote", "効いた学習を ltm-use 長期記憶へ昇格（エージェント不要）")]:
        _add_common(sub.add_parser(name, help=helptext))
    rot = sub.add_parser("rot", help="rot（古い/重複/実行不能）を検出して報告（--fix で blocked 化）")
    _add_common(rot); rot.add_argument("--fix", action="store_true", help="検出した rot を人の判断へ回す")

    st = sub.add_parser("stats", help="ループの計測値（スループット・自動化率・retry・人対応待ち）")
    _add_common(st); st.add_argument("--json", action="store_true", help="JSON で出力")

    au = sub.add_parser("audit", help="Loop Readiness を採点（L0–L3・スコア・赤旗・提案）")
    _add_common(au); au.add_argument("--json", action="store_true", help="JSON で出力")
    au.add_argument("--strict", action="store_true",
                    help="スコア<40 か critical 赤旗があれば exit 2（CI ゲート用）")

    rl = sub.add_parser("runlog", help="構造化 run-log（run-log.jsonl）の末尾を表示")
    _add_common(rl); rl.add_argument("--json", action="store_true", help="JSON で出力")
    rl.add_argument("--tail", type=int, default=10, help="表示する直近の件数（既定 10・0 で全件）")

    dr = sub.add_parser("doctor", help="ログ/状態/環境から稼働を診断（kiro-cli）。env/config は "
                                       "--fix で修正・program は gitlab-idd でイシュー起票")
    _add_common(dr); dr.add_argument("--json", action="store_true", help="JSON で出力")
    dr.add_argument("--fix", action="store_true",
                    help="env/config の問題を修正し、program の不具合を gitlab-idd で起票"
                         "（スキルが無ければ出力のみ。既定は診断のみ）")
    dr.add_argument("--with-flow", dest="with_flow", action="store_true", default=None,
                    help="実行層 kiro-flow の doctor も連携実行して所見を統合（既定 on）")
    dr.add_argument("--no-flow", dest="with_flow", action="store_false",
                    help="kiro-flow との連携を無効化し本体のみ診断する")

    up = sub.add_parser("update",
                        help="スキルリポジトリ(main)の更新を確認。--now で temp に sparse-checkout "
                             "して install.sh を実行し再起動する")
    _add_common(up)
    up.add_argument("--now", action="store_true",
                    help="更新があれば即座に install.sh を実行して再起動する")
    up.add_argument("--check", action="store_true", help="更新の有無だけを表示（取り込まない）")

    enq = sub.add_parser("enqueue", help="汎用の取り込み口（CLI/stdin/JSON から backlog タスクを作る）")
    _add_common(enq)
    enq.add_argument("--title", default=None, help="タスクのタイトル（必須・--json 時は不要）")
    enq.add_argument("--verify", default=None, help="done 確定の verify コマンド（書ければこれが最良）")
    enq.add_argument("--accept", default=None,
                     help="完了条件を自然言語で（verify が書けない人向け。実行時にエージェントが決定的 verify を合成）")
    enq.add_argument("--verify-template", default=None,
                     help="決定的テンプレで verify を生成（例 'file-contains :: path :: 文字列'。エージェント不要）")
    enq.add_argument("--priority", type=int, default=0, help="優先度（大きいほど高優先・既定 0）")
    enq.add_argument("--source", default=None, help="出所（既定 enqueue）")
    enq.add_argument("--status", default=None, help="status を明示（既定: verify 有→ready / 無→inbox）")
    enq.add_argument("--after", default=None, help="依存タスク ID（カンマ区切り。DAG）")
    enq.add_argument("--repos", default=None,
                     help="このタスクが clone して作業する成果物リポジトリ（charter の name か URL・"
                          "カンマ区切りで複数可）。worker が temp 領域へ clone してから作業し作業後に消す")
    enq.add_argument("--cohort-items", dest="cohort_items", default=None,
                     help="同様手順の繰り返しタスクの対象一覧（カンマ区切り）。先頭を pilot として"
                          "先行実行し review:human で指示を固め、承認後に残りを生成する。"
                          "title/verify 中の {item} に各対象を差し込む")
    enq.add_argument("--review", default=None, help="検収ゲート（human で done 前に承認）")
    enq.add_argument("--note", default=None, help="メモ（保持される）")
    enq.add_argument("--id", default=None, help="タスク ID を明示（既定はタイトルから自動生成）")
    enq.add_argument("--json", action="store_true", help="stdin か --file の JSON（オブジェクト/配列）で投入")
    enq.add_argument("--file", default=None, help="--json の入力ファイル（既定 stdin）")

    ap = sub.add_parser("approve", help="判断待ちを修正承認して積み直し（決定記録）")
    _add_common(ap); ap.add_argument("id"); ap.add_argument("--reason", required=True)
    hd = sub.add_parser("hold", help="policy に deny 追加し保留（決定記録）")
    _add_common(hd); hd.add_argument("id"); hd.add_argument("--reason", required=True)
    rp = sub.add_parser("reprioritize", help="policy に pin/defer 追加（決定記録）")
    _add_common(rp); rp.add_argument("id")
    g = rp.add_mutually_exclusive_group(required=True)
    g.add_argument("--pin", action="store_true"); g.add_argument("--defer", action="store_true")
    rp.add_argument("--reason", required=True)

    rv = sub.add_parser("revise",
                        help="タスクを人が即時修正（内容・依存 after・優先度＋feedback 注入。"
                             "実行中なら現在の試行を確定せず修正内容で積み直す。決定記録）")
    _add_common(rv); rv.add_argument("id")
    # dest は rv_ プレフィックスで分離する（level 等は CONFIG_DEFAULTS のキーでもあり、
    # 素の dest だと resolve_config が設定既定値を注入して「指定していない編集」になるため）
    rv.add_argument("--title", dest="rv_title", default=None, help="タイトルを置換")
    rv.add_argument("--priority", dest="rv_priority", type=int, default=None,
                    help="優先度を置換（整数・大ほど高）")
    rv.add_argument("--verify", dest="rv_verify", default=None,
                    help="verify コマンドを置換（'' / none で削除）")
    rv.add_argument("--accept", dest="rv_accept", default=None,
                    help="自然言語の完了条件を置換（'' / none で削除）")
    rv.add_argument("--after", dest="rv_after", default=None,
                    help="依存タスク ID を置換（カンマ区切り。'' / none で解除。循環は拒否）")
    rv.add_argument("--note", dest="rv_note", default=None, help="メモを置換（'' / none で削除）")
    rv.add_argument("--level", dest="rv_level", default=None,
                    help="自律度を置換（report/assisted/unattended）")
    rv.add_argument("--track", dest="rv_track", default=None, help="track を置換（'' / none で削除）")
    rv.add_argument("--feedback", dest="rv_feedback", default=None,
                    help="次の act に必ず反映させる指示（例: e2e はローカルでなく実サーバに配備して実施）")
    rv.add_argument("--reason", default=None, help="決定記録に残す理由（省略時は feedback を流用）")

    rj = sub.add_parser("reject",
                        help="タスクを却下（廃止して archive へ退避。依存先を再審査に戻し、"
                             "charter があれば再計画を要求。決定記録・avoid 記録）")
    _add_common(rj); rj.add_argument("id"); rj.add_argument("--reason", required=True)

    imp = sub.add_parser("impact",
                         help="タスクの依存関係（前提／依存先・推移）を一覧表示（変更・却下の影響範囲）")
    _add_common(imp); imp.add_argument("id")
    imp.add_argument("--json", action="store_true", help="JSON で出力")

    rpl = sub.add_parser("replan",
                         help="charter からバックログを再分解（エラー回復。done/既存と類似は投入しない）")
    _add_common(rpl)
    rpl.add_argument("--reason", default=None, help="決定記録に残す理由")
    rpl.add_argument("--charter", default=None,
                     help="対象 charter 名（charters/ 複数運用時。未指定は全 charter で消化可能）")

    _reg_help = ("共有レジストリ（os.pathsep 区切り可）。NFS/同期フォルダ/git バスのチェックアウト等を"
                 "指すと別ホストを相互発見。環境変数 KIRO_PROJECT_REGISTRY でも指定可")
    inst = sub.add_parser("instances",
                          help="稼働中の kiro-project（監視中プロジェクトルート）を一覧（外部操作者の発見口）")
    inst.add_argument("--json", action="store_true", help="JSON で出力（スキル等が機械処理する用）")
    inst.add_argument("--registry", action="append", default=None, help=_reg_help)

    sta = sub.add_parser("start",
                         help="run --watch を切り離して常駐起動（detached。重複は --force）")
    sta.add_argument("--root", default=None, help="プロジェクトルート（既定 . = cwd）")
    sta.add_argument("--config", default=None, help="子プロセスへ渡す設定ファイル")
    sta.add_argument("--force", action="store_true", help="同じプロジェクトを既に監視中でも起動する")
    sta.add_argument("--registry", action="append", default=None, help=_reg_help)
    sto = sub.add_parser("stop", help="稼働インスタンスを停止（SIGTERM→必要なら SIGKILL・登録掃除）")
    sto.add_argument("--root", default=None, help="停止対象のプロジェクトルート（既定 . = cwd）")
    sto.add_argument("--config", default=None,
                     help="root の解決に使う設定ファイル（--root 未指定時。start と同じ探索既定）")
    sto.add_argument("--pid", type=int, default=None, help="停止対象の PID（instances で確認）")
    sto.add_argument("--all", action="store_true", help="稼働中インスタンスを全停止")
    sto.add_argument("--registry", action="append", default=None, help=_reg_help)
    res = sub.add_parser("restart", help="同じプロジェクトの監視を停止してから起動し直す")
    res.add_argument("--root", default=None, help="プロジェクトルート（既定 . = cwd）")
    res.add_argument("--config", default=None, help="子プロセスへ渡す設定ファイル")
    res.add_argument("--registry", action="append", default=None, help=_reg_help)

    # サブコマンドを省略して呼ばれたら「常駐監視（run --watch）」を既定にする。
    # PC 起動時に立ち上げっぱなしにして cwd のプロジェクトを面倒見る daemon 用途を一級にするため。
    _subcommands = {"run", "triage", "needs", "promote", "rot", "stats", "audit",
                    "runlog", "doctor", "update", "enqueue", "approve", "hold", "reprioritize",
                    "revise", "reject", "impact", "replan", "instances", "start", "stop", "restart"}
    if not argv or (argv[0] not in _subcommands and argv[0] not in ("-h", "--help")):
        argv = ["run", "--watch", *argv]

    args = p.parse_args(argv)

    # instances / start / stop / restart は共通設定（backlog 等）を必要としない操作コマンド。
    if args.cmd == "instances":
        return cmd_instances(args.json, extra=_split_registry(getattr(args, "registry", None)))
    if args.cmd == "start":
        return cmd_start(args.root, args.config, args.force,
                         extra=_split_registry(getattr(args, "registry", None)))
    if args.cmd == "stop":
        return cmd_stop(args.root, args.pid, args.all,
                        extra=_split_registry(getattr(args, "registry", None)),
                        config=getattr(args, "config", None))
    if args.cmd == "restart":
        return cmd_restart(args.root, args.config,
                           extra=_split_registry(getattr(args, "registry", None)))

    resolve_config(args)      # CLI 未指定値を 設定ファイル → 組み込み既定 で確定
    cfg = build_config(args)

    if args.cmd in ("triage", "needs", "rot") and not cfg.backlog.exists():
        print(f"エラー: バックログディレクトリがありません: {cfg.backlog}", file=sys.stderr)
        return 2

    return {
        "run": lambda: cmd_run(cfg),
        "triage": lambda: cmd_triage(cfg),
        "needs": lambda: cmd_needs(cfg),
        "enqueue": lambda: cmd_enqueue(cfg, args),
        "stats": lambda: cmd_stats(cfg, getattr(args, "json", False)),
        "audit": lambda: cmd_audit(cfg, getattr(args, "json", False),
                                   getattr(args, "strict", False)),
        "runlog": lambda: cmd_runlog(cfg, getattr(args, "json", False),
                                     getattr(args, "tail", 10)),
        "doctor": lambda: cmd_doctor(cfg, getattr(args, "fix", False),
                                     getattr(args, "json", False)),
        "update": lambda: cmd_update(cfg, getattr(args, "now", False),
                                     getattr(args, "check", False)),
        "promote": lambda: cmd_promote(cfg),
        "rot": lambda: cmd_rot(cfg, getattr(args, "fix", False)),
        "approve": lambda: cmd_approve(cfg, args.id, args.reason),
        "reject": lambda: cmd_reject(cfg, args.id, args.reason),
        "impact": lambda: cmd_impact(cfg, args.id, getattr(args, "json", False)),
        "hold": lambda: cmd_hold(cfg, args.id, args.reason),
        "reprioritize": lambda: cmd_reprioritize(
            cfg, args.id, "pin" if args.pin else "defer", args.reason),
        "revise": lambda: cmd_revise(
            cfg, args.id, {k: getattr(args, f"rv_{k}") for k in REVISE_FIELDS},
            args.rv_feedback or "", args.reason or ""),
        "replan": lambda: cmd_replan(cfg, args.reason or "charter からのバックログ再分解",
                                     getattr(args, "charter", None) or ""),
    }[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
