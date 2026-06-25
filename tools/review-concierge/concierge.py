#!/usr/bin/env python3
"""
review-concierge — マージ判断を「1枚の Obsidian ノート」に集約する独立監視デーモン。

設計思想:
  - gitlab-idd には一切手を入れない。gl.py のヘルパーを import 再利用するだけ。
  - LLM セッション内ポーリングはしない。本体は非 LLM の Python プロセスで、
    安い API ポーリングで `status:review-ready` の差分を見張り、
    新着が出た時だけ重い処理（＝任意の review_command／LLM）を起動する。
  - レビューの成果物はコードリポジトリではなく Obsidian Vault に置く（肥大化回避）。
  - マージは「人間が Obsidian で明示承認した時だけ」実行する（責任は人間に固定）。

サブコマンド:
  scan       review-ready イシューを 1 回走査し、新着/更新分のノートを生成
  watch      scan を一定間隔でループ（非 LLM ポーリング・デーモン）
  writeback  Vault 内ノートの decision: を読み、GitLab へ書き戻す
             （approve→ラベル更新＋コメント＋[任意]マージ / reject→コメント＋リオープン）
  queue      Dataview キュー（Review/Queue.md）を再生成
  selftest   ネットワーク無しでノート生成・トリアージ・受け入れ条件抽出を検証

使い方:
  python3 concierge.py scan      --config review-concierge.yaml
  python3 concierge.py watch     --config review-concierge.yaml
  python3 concierge.py writeback --config review-concierge.yaml
  python3 concierge.py selftest

環境変数:
  GITLAB_TOKEN   GitLab パーソナルアクセストークン（gl.py と共有）
  GL_SCRIPTS_DIR gl.py のあるディレクトリ（省略時は自動探索）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# gl.py ヘルパーの読み込み（gitlab-idd は無改修・import のみ）
# ---------------------------------------------------------------------------


def _find_gl_scripts_dir() -> Path | None:
    """gl.py / config_loader.py のあるディレクトリを探す。"""
    env = os.environ.get("GL_SCRIPTS_DIR")
    if env and (Path(env) / "gl.py").is_file():
        return Path(env)
    here = Path(__file__).resolve()
    # tools/review-concierge/concierge.py から見たリポジトリルートを推定して探索。
    for base in [here.parent, *here.parents]:
        cand = base / ".github" / "skills" / "gitlab-idd" / "scripts"
        if (cand / "gl.py").is_file():
            return cand
    # フォールバック: PATH 的に近い場所を総当り
    for base in here.parents:
        for cand in base.rglob("gitlab-idd/scripts/gl.py"):
            return cand.parent
    return None


_GL_DIR = _find_gl_scripts_dir()
if _GL_DIR:
    sys.path.insert(0, str(_GL_DIR))

try:  # gl.py が見つかればオンライン機能が使える。無くても selftest は動く。
    import gl  # type: ignore

    _HAVE_GL = True
except Exception:  # pragma: no cover - 環境依存
    gl = None  # type: ignore
    _HAVE_GL = False


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULTS = {
    "vault_path": "~/Documents/Obsidian/MyVault",
    "inbox_dir": "Review/Inbox",          # 判断ノートの出力先（vault 相対）
    "archive_dir": "Review/Archive",      # 決定済みノートの退避先
    "queue_file": "Review/Queue.md",      # Dataview キュー
    "ready_label": "status:review-ready",  # 監視対象ラベル
    "approved_label": "status:approved",   # 承認時に付け替えるラベル
    "rework_label": "status:rework",       # 差し戻し時に付けるラベル
    "branch_prefix": "feature/issue-",     # MR を引き当てるソースブランチ接頭辞
    "connection_label": "default",         # connections.yaml のラベル
    "poll_interval_sec": 120,              # watch のポーリング間隔（非 LLM）
    "merge_on_approve": True,              # 承認ノート確定時に実際にマージするか
    "squash_on_merge": True,
    "remove_source_branch": True,
    # review_command: 生成した raw バンドル(JSON)を stdin で受け取り、
    #   キュレーション済みノート本文を stdout に返す任意コマンド（例: kiro-cli ラッパ）。
    #   未設定なら決定論的スキャフォールド（受け入れ条件/リスク/自動チェック）のみ書き出す。
    "review_command": "",
    # リスク・トリアージのパス正規表現（先頭一致順に評価）。
    "risk_rules": [
        {"level": "high", "pattern": r"(auth|login|password|secret|token|crypto|payment|billing|migrat|schema|infra|deploy|Dockerfile|\.tf$|security)"},
        {"level": "low", "pattern": r"(test|spec|__tests__|fixtures?|docs?/|\.md$|README|CHANGELOG|\.lock$)"},
    ],
}

RISK_EMOJI = {"high": "🔴", "medium": "🟡", "low": "⚪"}
RISK_RANK = {"high": 3, "medium": 2, "low": 1}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULTS)
    search = []
    if path:
        search.append(Path(path))
    search += [
        Path.cwd() / "review-concierge.yaml",
        Path.cwd() / "review-concierge.yml",
        Path.home() / "review-concierge.yaml",
    ]
    for p in search:
        if p and p.is_file():
            loaded = _read_yaml_or_json(p)
            if loaded:
                cfg.update(loaded)
            break
    cfg["vault_path"] = str(Path(os.path.expanduser(cfg["vault_path"])))
    return cfg


def _read_yaml_or_json(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# GitLab アクセス（gl.py 再利用）
# ---------------------------------------------------------------------------


class GitLab:
    """gl.py のヘルパーを薄くラップした read/write クライアント。"""

    def __init__(self, label: str = "default"):
        if not _HAVE_GL:
            raise RuntimeError(
                "gl.py が見つかりません。GL_SCRIPTS_DIR を設定するか "
                "gitlab-idd と同じリポジトリ内で実行してください。"
            )
        self.host, self.project = gl.get_project_info(label)
        self.token = gl.get_token(label)
        self.ep = gl.encode_project(self.project)

    def _api(self, method, path, data=None, params=None):
        return gl.api(self.host, self.token, method, path, data=data, params=params)

    def _api_list(self, path, params=None):
        return gl.api_list(self.host, self.token, path, params=params)

    def list_ready_issues(self, label: str) -> list[dict]:
        return self._api_list(
            f"/projects/{self.ep}/issues",
            params={"state": "opened", "labels": label},
        )

    def get_issue(self, iid) -> dict:
        return self._api("GET", f"/projects/{self.ep}/issues/{iid}")

    def get_issue_comments(self, iid) -> list[dict]:
        return self._api_list(f"/projects/{self.ep}/issues/{iid}/notes")

    def find_mr_for_issue(self, iid, branch_prefix: str) -> dict | None:
        mrs = self._api_list(
            f"/projects/{self.ep}/merge_requests",
            params={"state": "opened", "source_branch": f"{branch_prefix}{iid}"},
        )
        if mrs:
            return mrs[0]
        # 接頭辞 + iid- で前方一致も試す（feature/issue-42-slug 形式）
        mrs = self._api_list(
            f"/projects/{self.ep}/merge_requests", params={"state": "opened"}
        )
        for mr in mrs:
            sb = mr.get("source_branch", "")
            if sb.startswith(f"{branch_prefix}{iid}-") or sb == f"{branch_prefix}{iid}":
                return mr
        return None

    def get_mr_changes(self, mr_iid) -> dict:
        return self._api(
            "GET", f"/projects/{self.ep}/merge_requests/{mr_iid}/changes"
        )

    def get_mr_pipeline(self, mr_iid) -> dict:
        pipes = self._api(
            "GET",
            f"/projects/{self.ep}/merge_requests/{mr_iid}/pipelines",
            params={"per_page": 1},
        )
        return pipes[0] if pipes else {"status": "none"}

    def add_issue_comment(self, iid, body):
        return self._api(
            "POST", f"/projects/{self.ep}/issues/{iid}/notes", data={"body": body}
        )

    def add_mr_comment(self, mr_iid, body):
        return self._api(
            "POST",
            f"/projects/{self.ep}/merge_requests/{mr_iid}/notes",
            data={"body": body},
        )

    def set_issue_labels(self, iid, add=None, remove=None):
        issue = self.get_issue(iid)
        labels = set(issue.get("labels") or [])
        labels |= set(add or [])
        labels -= set(remove or [])
        return self._api(
            "PUT",
            f"/projects/{self.ep}/issues/{iid}",
            data={"labels": ",".join(sorted(labels))},
        )

    def reopen_issue(self, iid):
        return self._api(
            "PUT", f"/projects/{self.ep}/issues/{iid}", data={"state_event": "reopen"}
        )

    def merge_mr(self, mr_iid, squash=True, remove_source_branch=True):
        data = {}
        if squash:
            data["squash"] = True
        if remove_source_branch:
            data["should_remove_source_branch"] = True
        return self._api(
            "PUT", f"/projects/{self.ep}/merge_requests/{mr_iid}/merge", data=data
        )


# ---------------------------------------------------------------------------
# 決定論的パケット組み立て（LLM 不要の部分）
# ---------------------------------------------------------------------------

_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")


def extract_acceptance(description: str) -> list[dict]:
    """イシュー本文のチェックリストを受け入れ条件として抽出する。"""
    items = []
    for line in (description or "").splitlines():
        m = _CHECKBOX_RE.match(line)
        if m:
            items.append({"done": m.group(1).lower() == "x", "text": m.group(2)})
    return items


def classify_risk(path: str, rules: list[dict]) -> str:
    for rule in rules:
        if re.search(rule["pattern"], path or "", re.IGNORECASE):
            return rule["level"]
    return "medium"


def summarize_diff(changes: dict, rules: list[dict]) -> dict:
    """MR の changes からファイル別リスクと増減行数を集計する。"""
    files = []
    add_total = del_total = 0
    for ch in changes.get("changes", []) or []:
        path = ch.get("new_path") or ch.get("old_path") or "?"
        diff = ch.get("diff", "") or ""
        adds = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        dels = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        add_total += adds
        del_total += dels
        files.append(
            {
                "path": path,
                "risk": classify_risk(path, rules),
                "adds": adds,
                "dels": dels,
                "diff": diff,
            }
        )
    files.sort(key=lambda f: (-RISK_RANK[f["risk"]], f["path"]))
    overall = "low"
    if any(f["risk"] == "high" for f in files):
        overall = "high"
    elif any(f["risk"] == "medium" for f in files):
        overall = "medium"
    return {
        "files": files,
        "adds": add_total,
        "dels": del_total,
        "overall_risk": overall,
        "n_files": len(files),
    }


def build_bundle(issue: dict, comments: list[dict], mr: dict | None,
                 diff_summary: dict, pipeline: dict, cfg: dict) -> dict:
    """ノート生成に必要な生データを 1 つの辞書にまとめる。"""
    accept = extract_acceptance(issue.get("description", ""))
    return {
        "issue": {
            "iid": issue.get("iid"),
            "title": issue.get("title"),
            "description": issue.get("description", ""),
            "web_url": issue.get("web_url"),
            "labels": issue.get("labels", []),
            "author": (issue.get("author") or {}).get("username"),
            "updated_at": issue.get("updated_at"),
        },
        "comments": [
            {"author": (c.get("author") or {}).get("username"), "body": c.get("body", "")}
            for c in comments
            if not c.get("system")
        ],
        "mr": None
        if not mr
        else {
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "web_url": mr.get("web_url"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
        },
        "diff": diff_summary,
        "pipeline": {"status": pipeline.get("status"), "web_url": pipeline.get("web_url")},
        "acceptance": accept,
        "config": {"ready_label": cfg["ready_label"]},
    }


# ---------------------------------------------------------------------------
# Obsidian ノート描画
# ---------------------------------------------------------------------------


def _fm_value(v) -> str:
    if isinstance(v, str) and (":" in v or v == ""):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_note(bundle: dict, ai_section: str | None, cfg: dict) -> str:
    issue = bundle["issue"]
    mr = bundle["mr"]
    diff = bundle["diff"]
    accept = bundle["acceptance"]
    met = sum(1 for a in accept if a["done"])
    risk = diff["overall_risk"] if mr else "medium"

    fm = {
        "issue_id": issue["iid"],
        "title": issue["title"],
        "mr": (mr["iid"] if mr else ""),
        "risk": risk,
        "acceptance_total": len(accept),
        "acceptance_met": met,
        "decision": "pending",
        "confirmed_by": "",
        "url": issue.get("web_url") or "",
        "mr_url": (mr.get("web_url") if mr else "") or "",
        "generated_by": "review-concierge",
    }
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_fm_value(v)}")
    lines.append("---\n")

    # ヘッダ callout
    scale = f"{diff['n_files']} files / +{diff['adds']} -{diff['dels']}" if mr else "MR 未検出"
    rel = []
    rel.append(f"[[issue-{issue['iid']}]]（元イシュー）")
    if issue.get("web_url"):
        rel.append(f"[Issue]({issue['web_url']})")
    if mr:
        rel.append(f"[MR !{mr['iid']}]({mr.get('web_url')})")
    lines.append(f"> [!abstract] 🧭 マージ判断パケット — #{issue['iid']} {issue['title']}")
    lines.append(f"> **規模** {scale} ・ **リスク** {RISK_EMOJI.get(risk,'🟡')}{risk} ・ **受入** {met}/{len(accept)}")
    lines.append(f"> 関連: " + " ・ ".join(rel))
    lines.append("")

    if ai_section:
        # review_command が返したキュレーション済み本文をそのまま差し込む
        lines.append(ai_section.rstrip())
        lines.append("")
    else:
        lines.append("> [!warning]+ 🤖 AI レビュー未実行")
        lines.append("> `review_command` 未設定。下の決定論パケットのみ。SKILL に従いレビューエージェントで埋めてください。")
        lines.append("")

    # 受け入れ条件トレーサビリティ（畳み）
    lines.append("> [!check]- ✅ 受け入れ条件トレーサビリティ")
    if accept:
        for a in accept:
            mark = "x" if a["done"] else " "
            lines.append(f"> - [{mark}] {a['text']}")
    else:
        lines.append("> （イシュー本文にチェックリスト形式の受け入れ条件が見つかりませんでした）")
    lines.append("")

    # リスク段階開示（🔴必読は展開／🟡⚪は畳み）
    if mr and diff["files"]:
        highs = [f for f in diff["files"] if f["risk"] == "high"]
        rest = [f for f in diff["files"] if f["risk"] != "high"]
        if highs:
            lines.append("> [!danger]+ 🔴 必ず確認すべき差分")
            for f in highs:
                lines.append(f"> - `{f['path']}` (+{f['adds']} -{f['dels']})")
            lines.append("")
        if rest:
            lines.append("> [!note]- 🟡⚪ 流し読みでよい差分")
            for f in rest:
                lines.append(f"> - {RISK_EMOJI[f['risk']]} `{f['path']}` (+{f['adds']} -{f['dels']})")
            lines.append("")

    # 自動チェック
    pipe = bundle["pipeline"]["status"] or "none"
    pipe_mark = {"success": "✅", "failed": "❌", "running": "⏳", "none": "—"}.get(pipe, pipe)
    lines.append("> [!success] 自動チェック")
    lines.append(f"> CI {pipe_mark} ({pipe}) ・ files {diff['n_files']} ・ +{diff['adds']} -{diff['dels']}")
    lines.append("")

    # 判断セクション（人間がここを編集 → writeback が拾う）
    lines.append("## 判断")
    lines.append("frontmatter の `decision` を **approve** か **reject** に変更し、")
    lines.append("`confirmed_by` にあなたの名前を記入して保存してください（writeback が GitLab へ反映）。")
    lines.append("")
    lines.append("- ✅ approve → ラベル更新＋「マージお願いします」コメント"
                 + ("＋自動マージ" if cfg.get("merge_on_approve") else "") )
    lines.append("- ♻️ reject → 下に理由を書く → リオープン＋ラベル `" + cfg["rework_label"] + "`")
    lines.append("")
    lines.append("### 差し戻し理由（reject 時に記入）")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# frontmatter パース（write_back_result.py と同等の素朴実装）
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v and v[0] in "\"'" and v[-1:] == v[0]:
            v = v[1:-1]
        fm[k.strip()] = v
    return fm


def extract_section(text: str, header: str) -> str:
    """`### header` 以降〜次の見出しまでの本文を返す。"""
    lines = text.splitlines()
    out, capture = [], False
    for ln in lines:
        if ln.strip().startswith("### ") and header in ln:
            capture = True
            continue
        if capture and ln.startswith("#"):
            break
        if capture:
            out.append(ln)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# review_command 連携
# ---------------------------------------------------------------------------


def run_review_command(command: str, bundle: dict) -> str | None:
    if not command:
        return None
    import subprocess

    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps(bundle, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
        sys.stderr.write(f"[review_command] rc={proc.returncode} {proc.stderr[:400]}\n")
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[review_command] error: {e}\n")
    return None


# ---------------------------------------------------------------------------
# 状態管理（dedup）
# ---------------------------------------------------------------------------


def _state_path(cfg: dict) -> Path:
    return Path(cfg["vault_path"]) / cfg["inbox_dir"] / ".concierge-state.json"


def load_state(cfg: dict) -> dict:
    p = _state_path(cfg)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(cfg: dict, state: dict):
    p = _state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------


def cmd_scan(cfg: dict, gl_client: "GitLab | None" = None) -> int:
    gl_client = gl_client or GitLab(cfg["connection_label"])
    state = load_state(cfg)
    inbox = Path(cfg["vault_path"]) / cfg["inbox_dir"]
    inbox.mkdir(parents=True, exist_ok=True)

    issues = gl_client.list_ready_issues(cfg["ready_label"])
    generated = 0
    for issue in issues:
        iid = issue["iid"]
        key = str(iid)
        sig = issue.get("updated_at")
        note_path = inbox / f"issue-{iid}.md"
        # 既存ノートが pending 以外（人間が判断中/済み）なら上書きしない
        if note_path.is_file():
            fm = parse_frontmatter(note_path.read_text(encoding="utf-8"))
            if fm.get("decision", "pending") not in ("", "pending"):
                continue
        if state.get(key, {}).get("updated_at") == sig and note_path.is_file():
            continue  # 変化なし

        full = gl_client.get_issue(iid)
        comments = gl_client.get_issue_comments(iid)
        mr = gl_client.find_mr_for_issue(iid, cfg["branch_prefix"])
        diff_summary = {"files": [], "adds": 0, "dels": 0, "overall_risk": "medium", "n_files": 0}
        pipeline = {"status": "none"}
        if mr:
            changes = gl_client.get_mr_changes(mr["iid"])
            diff_summary = summarize_diff(changes, cfg["risk_rules"])
            pipeline = gl_client.get_mr_pipeline(mr["iid"])

        bundle = build_bundle(full, comments, mr, diff_summary, pipeline, cfg)
        ai_section = run_review_command(cfg.get("review_command", ""), bundle)
        note_path.write_text(render_note(bundle, ai_section, cfg), encoding="utf-8")
        state[key] = {"updated_at": sig, "mr": (mr["iid"] if mr else None)}
        generated += 1
        print(f"[scan] note written: {note_path}")

    save_state(cfg, state)
    regen_queue(cfg)
    print(f"[scan] {generated} note(s) generated from {len(issues)} ready issue(s).")
    return 0


def cmd_watch(cfg: dict) -> int:
    interval = int(cfg.get("poll_interval_sec", 120))
    print(f"[watch] polling every {interval}s for label '{cfg['ready_label']}' (Ctrl-C to stop)")
    gl_client = GitLab(cfg["connection_label"])
    while True:
        try:
            cmd_scan(cfg, gl_client)
            cmd_writeback(cfg, gl_client)
        except KeyboardInterrupt:
            print("\n[watch] stopped.")
            return 0
        except Exception as e:  # デーモンは落とさない
            sys.stderr.write(f"[watch] error: {e}\n")
        time.sleep(interval)


def cmd_writeback(cfg: dict, gl_client: "GitLab | None" = None) -> int:
    gl_client = gl_client or GitLab(cfg["connection_label"])
    inbox = Path(cfg["vault_path"]) / cfg["inbox_dir"]
    archive = Path(cfg["vault_path"]) / cfg["archive_dir"]
    if not inbox.is_dir():
        return 0
    processed = 0
    for note in sorted(inbox.glob("issue-*.md")):
        text = note.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        decision = (fm.get("decision") or "pending").lower()
        if decision not in ("approve", "reject"):
            continue
        iid = fm.get("issue_id")
        mr_iid = fm.get("mr") or None
        who = fm.get("confirmed_by") or "unknown"
        if not iid:
            continue

        if decision == "approve":
            gl_client.set_issue_labels(
                iid, add=[cfg["approved_label"]], remove=[cfg["ready_label"]]
            )
            comment = (
                f"✅ レビュアー（{who}）が Obsidian 上で承認しました。"
                f"\n\n— review-concierge"
            )
            gl_client.add_issue_comment(iid, comment)
            merged = False
            if cfg.get("merge_on_approve") and mr_iid:
                gl_client.add_mr_comment(
                    mr_iid, f"✅ 人間レビュアー（{who}）の明示承認によりマージします。"
                )
                gl_client.merge_mr(
                    mr_iid,
                    squash=cfg.get("squash_on_merge", True),
                    remove_source_branch=cfg.get("remove_source_branch", True),
                )
                merged = True
            elif mr_iid:
                gl_client.add_mr_comment(
                    mr_iid, f"@{fm.get('author','')} レビュアー（{who}）が承認しました。マージをお願いします 🙏"
                )
            print(f"[writeback] #{iid} approved by {who}" + (" + merged" if merged else ""))
        else:  # reject
            reason = extract_section(text, "差し戻し理由") or "（理由未記入）"
            gl_client.set_issue_labels(
                iid, add=[cfg["rework_label"]], remove=[cfg["ready_label"], cfg["approved_label"]]
            )
            gl_client.reopen_issue(iid)
            body = (
                f"♻️ レビュアー（{who}）が差し戻しました。\n\n**理由 / 修正要望:**\n\n{reason}"
                f"\n\n— review-concierge"
            )
            gl_client.add_issue_comment(iid, body)
            if mr_iid:
                gl_client.add_mr_comment(mr_iid, body)
            print(f"[writeback] #{iid} rejected by {who}")

        # ノートを Archive へ退避（frontmatter を done に）
        archive.mkdir(parents=True, exist_ok=True)
        done_text = _set_fm_value(text, "decision", f"{decision}d")
        (archive / note.name).write_text(done_text, encoding="utf-8")
        note.unlink()
        # state からも除去して再生成を防ぐ
        state = load_state(cfg)
        state.pop(str(iid), None)
        save_state(cfg, state)
        processed += 1

    if processed:
        regen_queue(cfg)
    return 0


def _set_fm_value(text: str, key: str, value: str) -> str:
    def repl(m):
        body = m.group(1)
        new_lines = []
        found = False
        for line in body.splitlines():
            if line.startswith(f"{key}:"):
                new_lines.append(f"{key}: {value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}: {value}")
        return "---\n" + "\n".join(new_lines) + "\n---\n"

    if _FM_RE.match(text):
        return _FM_RE.sub(repl, text, count=1)
    return text


def regen_queue(cfg: dict) -> int:
    """Review/Queue.md に Dataview の判断キューを書き出す。"""
    queue = Path(cfg["vault_path"]) / cfg["queue_file"]
    queue.parent.mkdir(parents=True, exist_ok=True)
    inbox_rel = cfg["inbox_dir"]
    content = f"""---
generated_by: review-concierge
---

# 🧭 マージ判断キュー

人間が判断すべきパケットの一覧（リスク降順・受入の少ない順）。各行をクリックして 1 枚ノートを開く。

```dataview
TABLE risk AS リスク, confidence AS 信頼度, (acceptance_met + "/" + acceptance_total) AS 受入, decision AS 判断
FROM "{inbox_rel}"
WHERE generated_by = "review-concierge" AND (decision = "pending" OR decision = "")
SORT risk DESC, acceptance_met ASC
```

## 🔴 高リスク（要精読）
```dataview
LIST FROM "{inbox_rel}" WHERE risk = "high" AND (decision = "pending" OR decision = "")
```

## 🟢 軽量承認候補（低リスク・受入充足）
```dataview
LIST FROM "{inbox_rel}" WHERE risk = "low" AND acceptance_met = acceptance_total AND (decision = "pending" OR decision = "")
```
"""
    queue.write_text(content, encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# selftest（ネットワーク不要）
# ---------------------------------------------------------------------------


def cmd_selftest(cfg: dict) -> int:
    import tempfile

    print("[selftest] start (no network)")
    sample_issue = {
        "iid": 42,
        "title": "二要素認証(TOTP)の追加",
        "web_url": "https://gitlab.example.com/x/issues/42",
        "labels": ["status:review-ready"],
        "author": {"username": "alice"},
        "updated_at": "2026-06-25T00:00:00Z",
        "description": (
            "## 受け入れ条件\n"
            "- [x] TOTP 検証が通る\n"
            "- [x] 失敗3回でロック\n"
            "- [ ] リカバリコード\n"
        ),
    }
    sample_comments = [
        {"author": {"username": "bob"}, "body": "LGTM だが TZ に注意", "system": False},
        {"author": {"username": "gitlab"}, "body": "changed the description", "system": True},
    ]
    sample_mr = {
        "iid": 88,
        "title": "二要素認証(TOTP)の追加",
        "web_url": "https://gitlab.example.com/x/mr/88",
        "source_branch": "feature/issue-42-totp",
        "target_branch": "main",
    }
    sample_changes = {
        "changes": [
            {"new_path": "auth/totp.py", "diff": "@@\n+secret = gen()\n+def verify():\n-old\n"},
            {"new_path": "tests/test_totp.py", "diff": "@@\n+def test_valid():\n+    pass\n"},
            {"new_path": "app/view.py", "diff": "@@\n+render()\n-legacy\n"},
        ]
    }

    # 1) 受け入れ条件抽出
    accept = extract_acceptance(sample_issue["description"])
    assert len(accept) == 3 and sum(a["done"] for a in accept) == 2, accept
    print("  ok: acceptance extraction (3 items, 2 done)")

    # 2) リスク・トリアージ
    diff = summarize_diff(sample_changes, cfg["risk_rules"])
    risks = {f["path"]: f["risk"] for f in diff["files"]}
    assert risks["auth/totp.py"] == "high", risks
    assert risks["tests/test_totp.py"] == "low", risks
    assert risks["app/view.py"] == "medium", risks
    assert diff["files"][0]["risk"] == "high", "high risk must sort first"
    assert diff["overall_risk"] == "high"
    print(f"  ok: risk triage {risks}, overall={diff['overall_risk']}")

    # 3) ノート描画
    bundle = build_bundle(sample_issue, sample_comments, sample_mr, diff, {"status": "success"}, cfg)
    assert len(bundle["comments"]) == 1, "system note must be filtered"
    note = render_note(bundle, None, cfg)
    for needle in ["decision: pending", "🔴 必ず確認すべき差分", "auth/totp.py",
                   "受け入れ条件トレーサビリティ", "[[issue-42]]", "## 判断"]:
        assert needle in note, f"missing in note: {needle}"
    print("  ok: note rendering contains all required sections")

    # 4) frontmatter ラウンドトリップ＋ decision 更新
    fm = parse_frontmatter(note)
    assert fm["issue_id"] == "42" and fm["decision"] == "pending", fm
    updated = _set_fm_value(note, "decision", "approve")
    assert parse_frontmatter(updated)["decision"] == "approve"
    print("  ok: frontmatter parse + decision update")

    # 5) reject 理由抽出
    rej = note.replace("### 差し戻し理由（reject 時に記入）\n",
                       "### 差し戻し理由（reject 時に記入）\nシークレットが平文保存\n")
    assert "平文保存" in extract_section(rej, "差し戻し理由")
    print("  ok: reject reason extraction")

    # 6) 実ファイル書き出し（一時 Vault）＋ queue 生成
    with tempfile.TemporaryDirectory() as td:
        tcfg = dict(cfg)
        tcfg["vault_path"] = td
        (Path(td) / cfg["inbox_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(td) / cfg["inbox_dir"] / "issue-42.md").write_text(note, encoding="utf-8")
        regen_queue(tcfg)
        assert (Path(td) / cfg["queue_file"]).is_file()
        print("  ok: note + Dataview queue written to temp vault")

    print("[selftest] ALL PASS ✅")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="review-concierge — マージ判断を Obsidian の 1 枚ノートに集約する独立監視デーモン")
    parser.add_argument("command",
                        choices=["scan", "watch", "writeback", "queue", "selftest"])
    parser.add_argument("--config", help="設定ファイル(review-concierge.yaml)のパス")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if args.command == "selftest":
        return cmd_selftest(cfg)
    if args.command == "scan":
        return cmd_scan(cfg)
    if args.command == "watch":
        return cmd_watch(cfg)
    if args.command == "writeback":
        return cmd_writeback(cfg)
    if args.command == "queue":
        return regen_queue(cfg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
