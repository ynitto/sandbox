#!/usr/bin/env python3
"""
validate_skill.py — gitlab-idd スキル静的検証スクリプト

SKILL.md・リファレンス・スクリプト・テンプレートの整合性を検証する。
"""

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
SKILL_MD      = SKILL_DIR / "SKILL.md"
API_MD        = SKILL_DIR / "references" / "gitlab-api.md"
WORKER_MD     = SKILL_DIR / "references" / "worker-role.md"
REQ_POST_MD   = SKILL_DIR / "references" / "requester-post.md"
REQ_REV_MD    = SKILL_DIR / "references" / "requester-review.md"
DAEMON_MD     = SKILL_DIR / "references" / "polling-daemon.md"
TMPL_WORKER   = SKILL_DIR / "templates" / "worker-prompt.md"
TMPL_WSL      = SKILL_DIR / "templates" / "worker-prompt-wsl-kiro.md"
GL_PY         = SKILL_DIR / "scripts" / "gl.py"
DAEMON_PY     = SKILL_DIR / "scripts" / "gl_poll_daemon.py"
SETUP_PY      = SKILL_DIR / "scripts" / "gl_poll_setup.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


skill   = read(SKILL_MD)
api     = read(API_MD)
worker  = read(WORKER_MD)
req_p   = read(REQ_POST_MD)
req_r   = read(REQ_REV_MD)
daemon  = read(DAEMON_MD)
tw      = read(TMPL_WORKER)
twsl    = read(TMPL_WSL)
gl      = read(GL_PY)
dpy     = read(DAEMON_PY)
spy     = read(SETUP_PY)

results: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    results.append((label, cond, detail))


# ───────────────────────────────────────────────
# 1. ファイル存在
# ───────────────────────────────────────────────
check("ファイル: SKILL.md",                  SKILL_MD.exists())
check("ファイル: references/gitlab-api.md",  API_MD.exists())
check("ファイル: references/worker-role.md", WORKER_MD.exists())
check("ファイル: references/requester-post.md",   REQ_POST_MD.exists())
check("ファイル: references/requester-review.md", REQ_REV_MD.exists())
check("ファイル: references/polling-daemon.md",   DAEMON_MD.exists())
check("ファイル: templates/worker-prompt.md",     TMPL_WORKER.exists())
check("ファイル: templates/worker-prompt-wsl-kiro.md", TMPL_WSL.exists())
check("ファイル: scripts/gl.py",             GL_PY.exists())
check("ファイル: scripts/gl_poll_daemon.py", DAEMON_PY.exists())
check("ファイル: scripts/gl_poll_setup.py",  SETUP_PY.exists())

# ───────────────────────────────────────────────
# 2. SKILL.md フロントマター
# ───────────────────────────────────────────────
check("メタデータ: name フィールド",        "name: gitlab-idd" in skill)
check("メタデータ: description フィールド", "description:" in skill)
check("メタデータ: version フィールド",     re.search(r"version:\s+\d+\.\d+\.\d+", skill) is not None)
check("メタデータ: tier フィールド",        "tier:" in skill)
check("メタデータ: category フィールド",    "category:" in skill)
check("メタデータ: tags フィールド",        "tags:" in skill)

# ───────────────────────────────────────────────
# 3. トリガーフレーズ（発動条件）
# ───────────────────────────────────────────────
check("トリガー: イシューを立てて",               "イシューを立てて" in skill)
check("トリガー: イシューを拾って / 担当タスク",   "イシューを拾って" in skill)
check("トリガー: イシューをレビューして",          "イシューをレビューして" in skill)
check("トリガー: ポーリングデーモンをインストール", "ポーリングデーモンをインストール" in skill)
check("トリガー: descriptionにもトリガー記載",
      any(kw in skill[:200] for kw in ["イシューを立てて", "イシューを拾って", "ポーリング"]),
      "description フィールドにトリガーフレーズがあること")

# ───────────────────────────────────────────────
# 4. ロール定義
# ───────────────────────────────────────────────
check("ロール: リクエスター(投稿)の定義",    "リクエスター" in skill and "投稿" in skill)
check("ロール: リクエスター(レビュー)の定義","レビュー" in skill and "クローズ" in skill)
check("ロール: ワーカーの定義",              "ワーカー" in skill)
check("ロール: ポーリングデーモン管理の定義","ポーリングデーモン" in skill)
check("ロール: ロール選択ガイドのテーブル",  "ロール選択ガイド" in skill or "役割" in skill)

# ───────────────────────────────────────────────
# 5. イシューラベル規約
# ───────────────────────────────────────────────
REQUIRED_LABELS = [
    "status:open", "status:in-progress", "status:review-ready",
    "status:needs-rework", "status:done", "assignee:any"
]
for lbl in REQUIRED_LABELS:
    check(f"ラベル規約: {lbl}", lbl in skill, f"{lbl} が SKILL.md に未定義")

# ───────────────────────────────────────────────
# 6. ブランチ命名規則
# ───────────────────────────────────────────────
check("ブランチ命名: feature/issue-{id} パターン", "feature/issue-" in skill)
check("ブランチ命名: SKILL.md と worker-role.md で一致",
      "feature/issue-" in worker)
check("ブランチ命名: SKILL.md と gl.py で一致",
      "feature/issue-" in gl)

# ───────────────────────────────────────────────
# 7. 前提条件
# ───────────────────────────────────────────────
check("前提条件: Python 3.11+",           "Python 3.11" in skill or "python 3.11" in skill.lower())
check("前提条件: GITLAB_TOKEN 環境変数",  "GITLAB_TOKEN" in skill)
check("前提条件: git remote get-url",     "git remote" in skill)
check("前提条件: エージェント CLI 一覧",  "claude" in skill and "codex" in skill)

# ───────────────────────────────────────────────
# 8. 行動指針（ガードレール）
# ───────────────────────────────────────────────
check("ガードレール: LLMポーリング禁止",          "LLM" in skill and "ポーリング禁止" in skill or "sleep" in skill.lower())
check("ガードレール: 受け入れ条件必須",            "受け入れ条件" in skill)
check("ガードレール: 並列評価の指定",              "並列" in skill)
check("ガードレール: self-defer 遵守",            "self-defer" in skill or "猶予" in skill)
check("ガードレール: デーモンインストール前ユーザー確認必須", "ユーザー確認" in skill and "インストール" in skill)
check("ガードレール: エージェント CLI 確認必須",   "エージェント CLI" in skill and "確認" in skill)

# ───────────────────────────────────────────────
# 9. エラーハンドリング表
# ───────────────────────────────────────────────
ERROR_CASES = [
    "GITLAB_TOKEN",         # トークン未設定
    "git remote origin",    # remote なし
    "競合",                  # イシュー競合取得
    "ブランチ競合",           # ブランチ競合
    "0 件",                  # イシュー 0 件
]
for case in ERROR_CASES:
    check(f"エラーハンドリング: {case}", case in skill, f"{case} がエラーハンドリング表に未記載")

# ───────────────────────────────────────────────
# 10. Permissions セクション
# ───────────────────────────────────────────────
check("Permissions: Allowed セクション",  "Allowed" in skill)
check("Permissions: Denied セクション",   "Denied" in skill)
check("Permissions: force push 禁止",     "force push" in skill)
check("Permissions: イシュー削除禁止",    "イシューの削除" in skill)
check("Permissions: LLMポーリングループ禁止", "ポーリングループ" in skill)

# ───────────────────────────────────────────────
# 11. リファレンス参照リンク
# ───────────────────────────────────────────────
check("参照リンク: requester-post.md",   "references/requester-post.md" in skill)
check("参照リンク: requester-review.md", "references/requester-review.md" in skill)
check("参照リンク: worker-role.md",      "references/worker-role.md" in skill)
check("参照リンク: gitlab-api.md",       "references/gitlab-api.md" in skill)
check("参照リンク: polling-daemon.md",   "references/polling-daemon.md" in skill)

# ───────────────────────────────────────────────
# 12. gl.py コマンドカバレッジ
# ───────────────────────────────────────────────
GL_COMMANDS = [
    "project-info", "current-user", "list-issues", "get-issue",
    "create-issue", "update-issue", "add-comment", "get-comments",
    "list-mrs", "create-mr", "merge-mr", "make-branch-name", "check-defer",
]
for cmd in GL_COMMANDS:
    in_api  = cmd in api
    in_gl   = cmd in gl
    check(f"コマンドカバレッジ[gl.py→api.md]: {cmd}", in_api and in_gl,
          f"api.md:{in_api}, gl.py:{in_gl}")

# ───────────────────────────────────────────────
# 13. ワーカーフロー（references/worker-role.md）
# ───────────────────────────────────────────────
check("ワーカー: Phase 1 環境確認",              "Phase 1" in worker)
check("ワーカー: Phase 2 イシュー取得",          "Phase 2" in worker)
check("ワーカー: Phase 3 イシュー着手",          "Phase 3" in worker)
check("ワーカー: Phase 4 タスク実行",            "Phase 4" in worker)
check("ワーカー: Phase 5 成果物提出",            "Phase 5" in worker)
check("ワーカー: self-defer チェック手順",       "check-defer" in worker)
check("ワーカー: 競合防止（assign後に確認）",    "ASSIGNED" in worker or "assignee" in worker.lower())
check("ワーカー: 並列レビュー（3観点）",
      "機能" in worker and "セキュリティ" in worker and "アーキテクチャ" in worker)
check("ワーカー: 並列評価ループ最大回数",         "最大 5 回" in worker or "最大5回" in worker)
check("ワーカー: MR ドラフト作成",              "draft" in worker.lower() or "ドラフト" in worker)
check("ワーカー: status:review-ready への更新",  "status:review-ready" in worker)
check("ワーカー: 実装はサブエージェント委譲",
      "サブエージェント" in worker and ("委譲" in worker or "自分で実装してはならない" in worker))

# ───────────────────────────────────────────────
# 14. リクエスター投稿フロー（references/requester-post.md）
# ───────────────────────────────────────────────
check("リクエスター投稿: 受け入れ条件セクション必須",      "受け入れ条件" in req_p)
check("リクエスター投稿: create-issue コマンド",          "create-issue" in req_p)
check("リクエスター投稿: ラベル status:open,assignee:any", "status:open,assignee:any" in req_p)
check("リクエスター投稿: 完了報告テンプレート",           "URL" in req_p and "イシュー" in req_p)

# ───────────────────────────────────────────────
# 15. リクエスターレビューフロー（references/requester-review.md）
# ───────────────────────────────────────────────
check("リクエスターレビュー: status:review-ready 取得",   "status:review-ready" in req_r)
check("リクエスターレビュー: 並列評価（3観点）",
      "機能" in req_r and "セキュリティ" in req_r and "アーキテクチャ" in req_r)
check("リクエスターレビュー: 条件充足→マージ手順",        "merge-mr" in req_r)
check("リクエスターレビュー: 条件不足→リオープン手順",    "reopen" in req_r)
check("リクエスターレビュー: 評価はサブエージェント委譲",
      "サブエージェント" in req_r and ("委譲" in req_r or "委ねる" in req_r or "自分で評価してはならない" in req_r))
check("リクエスターレビュー: 自分発行イシューのみ",       "自分が発行" in req_r or "自分が作成" in req_r)

# ───────────────────────────────────────────────
# 16. ポーリングデーモン（references/polling-daemon.md）
# ───────────────────────────────────────────────
check("デーモン: インストール手順",                        "インストール" in daemon)
check("デーモン: --dry-run vs mock_cli の違い表",         "dry-run" in daemon and "mock_cli" in daemon)
check("デーモン: 3OS対応（macOS/Linux/Windows）",
      "macOS" in daemon and "Linux" in daemon and "Windows" in daemon)
check("デーモン: SessionStart フック",                    "SessionStart" in daemon)
check("デーモン: エージェント CLI 優先順位",
      "claude" in daemon and "codex" in daemon and "kiro" in daemon and "amazonq" in daemon)
check("デーモン: config.json フィールド仕様",             "poll_interval_seconds" in daemon)
check("デーモン: トラブルシューティング表",               "トラブルシューティング" in daemon)
check("デーモン: 設定ディレクトリパス（OS別）",
      "~/.config/gitlab-idd" in daemon and "Library/Application Support" in daemon)

# ───────────────────────────────────────────────
# 17. テンプレート変数整合性
# ───────────────────────────────────────────────
def tmpl_vars(text: str) -> set[str]:
    return set(re.findall(r"\$\{(\w+)\}", text))

DEFINED_VARS = {
    "issue_id", "issue_title", "issue_url", "issue_body", "issue_labels",
    "host", "project", "project_name", "local_path",
    "branch_name", "remote_url", "clone_dir",
}
for tmpl_path, tmpl_text, name in [
    (TMPL_WORKER, tw,   "worker-prompt.md"),
    (TMPL_WSL,    twsl, "worker-prompt-wsl-kiro.md"),
]:
    used = tmpl_vars(tmpl_text)
    undefined = used - DEFINED_VARS
    check(f"テンプレート変数: {name} に未定義変数なし",
          len(undefined) == 0, f"未定義: {sorted(undefined)}")

# ───────────────────────────────────────────────
# 18. check-defer の bash 出力値整合性
# ───────────────────────────────────────────────
# Python bool → print() → "True"/"False"
# bash比較は "True" で行う必要がある
bash_defer_refs = re.findall(r'check-defer.*?= "(\w+)"', api + worker, re.DOTALL)
all_capital = all(v in ("True", "False") for v in bash_defer_refs)
check("check-defer 出力整合性: bash比較が \"True\" (大文字)",
      all_capital and len(bash_defer_refs) > 0,
      f"発見した比較値: {bash_defer_refs}")

# コメント整合性: # → false (小文字) は誤記
comment_lowercase = re.search(r"check-defer.*?→ false", api)
check("check-defer コメント: → True/False (大文字) で記述",
      comment_lowercase is None,
      '`gitlab-api.md` に `# → false`（小文字）の誤記コメントあり')

# ───────────────────────────────────────────────
# 19. スクリプト整合性（gl.py ↔ gl_poll_daemon.py）
# ───────────────────────────────────────────────
check("スクリプト: gl.py が stdlib のみ",
      "import urllib" in gl and "import requests" not in gl)
check("スクリプト: gl_poll_daemon.py が stdlib のみ",
      "import urllib" in dpy and "import requests" not in dpy)
check("スクリプト: gl_poll_setup.py が stdlib のみ",
      "import subprocess" in spy and "import requests" not in spy)
check("スクリプト: config.json アトミック書き込み (tmp→replace)",
      ".json.tmp" in dpy and ".replace(" in dpy)
check("スクリプト: config.json パーミッション 0600",
      "0o600" in dpy and "0o600" in spy)
check("スクリプト: save_config dry_run 対応",
      "dry_run" in spy)
check("スクリプト: デーモンインストール前ユーザー確認要求コメント",
      "ユーザー" in spy and ("同意" in spy or "確認" in spy))

# ───────────────────────────────────────────────
# 20. SKILL.md ↔ リファレンスの相互整合性
# ───────────────────────────────────────────────
check("整合性: SKILL.md のワーカーフロー(5step)がrefs引用",
      "Phase" in skill or "ステップ" in skill or "フロー" in skill)
check("整合性: SKILL.md のself-deferがrefs引用",
      "DEFER_MINUTES" in skill or "SELF_DEFER" in skill or "猶予" in skill)
check("整合性: api.md のコマンドがworker-role.mdで使用",
      "make-branch-name" in worker and "update-issue" in worker)
check("整合性: api.md のコマンドがrequester-review.mdで使用",
      "merge-mr" in req_r and "add-comment" in req_r)

# ───────────────────────────────────────────────
# 結果出力
# ───────────────────────────────────────────────
PASS_COLOR = "\033[32mPASS\033[0m"
FAIL_COLOR = "\033[31mFAIL\033[0m"
WARN_COLOR = "\033[33mWARN\033[0m"

passed  = sum(1 for _, ok, _ in results if ok)
failed  = sum(1 for _, ok, _ in results if not ok)
total   = len(results)

print("\ngitlab-idd スキル検証結果")
print("=" * 65)

prev_group = ""
for label, ok, detail in results:
    group = label.split(":")[0]
    if group != prev_group:
        prev_group = group
        print()
    mark   = "✓" if ok else "✗"
    status = PASS_COLOR if ok else FAIL_COLOR
    print(f"  {mark} [{status}] {label}")
    if not ok and detail:
        print(f"          ↳ {detail}")

print()
print("=" * 65)
print(f"結果: {passed}/{total} PASS  ({failed} FAIL)")
print()

if failed > 0:
    print("FAILした項目を修正してください。")
    sys.exit(1)
else:
    print("すべての検証が通過しました。")
    sys.exit(0)
