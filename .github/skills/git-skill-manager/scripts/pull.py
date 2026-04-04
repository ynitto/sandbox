#!/usr/bin/env python3
"""pull 操作: リポジトリからスキルを取得してインストールする。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime

from registry import (
    load_registry, save_registry, _cache_dir, _skill_home,
    _version_tuple, _read_frontmatter_version,
)
from repo import clone_or_fetch, update_remote_index
from delta_tracker import check_sync_protection


def _auto_save_snapshot() -> str | None:
    """pull 前に自動スナップショットを保存する。失敗しても pull は続行する。"""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from snapshot import save_snapshot
        return save_snapshot(label="pull前自動保存")
    except Exception as e:
        print(f"   ⚠️  スナップショット保存をスキップしました: {e}")
        return None


def _merge_copilot_instructions(parts: list[str]) -> str:
    """複数の copilot-instructions.md を H2 セクション単位でマージする。

    同じ見出しのセクションは内容を重複排除しながら結合し、
    異なる見出しのセクションはすべて取り込む。
    """
    SEP_RE = re.compile(r'\n[ \t]*[-]{3,}[ \t]*$', re.MULTILINE)

    def parse(text: str) -> tuple[str, list[tuple[str, str]]]:
        """(preamble, [(heading, body), ...]) を返す。"""
        preamble_lines: list[str] = []
        sections: list[tuple[str, str]] = []
        current_heading: str | None = None
        current_body: list[str] = []

        for line in text.split("\n"):
            if line.startswith("## "):
                if current_heading is not None:
                    body = SEP_RE.sub("", "\n".join(current_body)).strip()
                    sections.append((current_heading, body))
                else:
                    preamble_lines = list(current_body)
                current_heading = line[3:].strip()
                current_body = []
            else:
                current_body.append(line)

        if current_heading is not None:
            body = SEP_RE.sub("", "\n".join(current_body)).strip()
            sections.append((current_heading, body))

        return "\n".join(preamble_lines).strip(), sections

    preamble = ""
    seen: dict[str, str] = {}  # heading -> merged body
    order: list[str] = []

    for part in parts:
        p, sections = parse(part)
        if not preamble and p:
            preamble = p
        for heading, body in sections:
            if heading not in seen:
                seen[heading] = body
                order.append(heading)
            elif body and body not in seen[heading]:
                seen[heading] = seen[heading] + "\n\n" + body

    section_chunks = []
    for heading in order:
        body = seen[heading]
        section_chunks.append(f"## {heading}\n\n{body}" if body else f"## {heading}")

    joined_sections = "\n\n-----\n\n".join(section_chunks)

    if preamble and joined_sections:
        return preamble + "\n\n" + joined_sections + "\n"
    if joined_sections:
        return joined_sections + "\n"
    return preamble + "\n" if preamble else ""


def pull_skills(
    repo_name: str | None = None,
    skill_name: str | None = None,
    interactive: bool = True,
) -> None:
    """
    repo_name=None → 全リポジトリから取得
    skill_name=None → リポジトリ内の全スキルを取得
    interactive=True → ユーザー直接呼び出し（競合時に確認）
    interactive=False → サブエージェント経由（自動解決）
    """
    cache_dir = _cache_dir()
    skill_home = _skill_home()
    reg = load_registry()
    repos = reg["repositories"]
    if repo_name:
        repos = [r for r in repos if r["name"] == repo_name]
        if not repos:
            print(f"❌ リポジトリ '{repo_name}' が見つかりません")
            return

    os.makedirs(skill_home, exist_ok=True)

    # pull 前にスナップショットを自動保存（ロールバック用）
    snap_id = _auto_save_snapshot()

    # 全リポジトリからスキル候補を収集
    candidates: dict[str, list[dict]] = {}

    for repo in repos:
        repo_cache = clone_or_fetch(repo)
        update_remote_index(reg, repo["name"], repo_cache, repo["skill_root"])

        root = os.path.join(repo_cache, repo["skill_root"])
        if not os.path.isdir(root):
            continue

        for entry in os.listdir(root):
            skill_md = os.path.join(root, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            if skill_name and entry != skill_name:
                continue

            with open(skill_md, encoding="utf-8") as f:
                content = f.read()
            desc = ""
            tier = ""
            deprecated_by = ""
            fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if fm_match:
                in_metadata = False
                for line in fm_match.group(1).splitlines():
                    if line.startswith("description:"):
                        desc = line[len("description:"):].strip()
                    if line.startswith("metadata:"):
                        in_metadata = True
                        continue
                    if in_metadata:
                        if line and not line[0].isspace():
                            in_metadata = False
                        else:
                            stripped = line.lstrip()
                            if stripped.startswith("tier:"):
                                tier = stripped[len("tier:"):].strip().strip("\"'")
                            elif stripped.startswith("deprecated_by:"):
                                deprecated_by = stripped[len("deprecated_by:"):].strip().strip("\"'")

            result = subprocess.run(
                ["git", "log", "-1", "--format=%aI", "--",
                 os.path.join(repo["skill_root"], entry).replace("\\", "/")],
                cwd=repo_cache, capture_output=True, text=True, encoding="utf-8",
            )
            commit_date = result.stdout.strip() or "1970-01-01T00:00:00+00:00"

            commit_hash = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_cache, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()

            candidates.setdefault(entry, []).append({
                "repo_name": repo["name"],
                "repo_priority": repo.get("priority", 100),
                "source_path": os.path.join(repo["skill_root"], entry),
                "full_path": os.path.join(root, entry),
                "commit_date": commit_date,
                "commit_hash": commit_hash,
                "description": desc[:80],
                "tier": tier,
                "deprecated_by": deprecated_by,
            })

    # ---- 競合解決 ----
    installed = []
    conflicts = []
    removed_deprecated: list[dict] = []
    pruned: list[str] = []
    skipped_pinned: list[str] = []

    for sname, sources in candidates.items():
        winner = sources[0]

        if len(sources) > 1:
            if interactive:
                print(f"\n⚠️ 競合: '{sname}' が複数リポジトリに存在します")
                for i, s in enumerate(sources, 1):
                    short_desc = s["description"] or "(説明なし)"
                    ver = _read_frontmatter_version(s["full_path"])
                    ver_label = f"  v{ver}" if ver else ""
                    print(f"   {i}. {s['repo_name']:20s}  ({s['commit_date'][:10]}){ver_label}  {short_desc}")
                print()
                print("CONFLICT_CHOICE:")
                print(f"  どちらの '{sname}' をインストールしますか？ (1-{len(sources)}, デフォルト: 1)")
                try:
                    raw = input("  > ").strip()
                    choice = int(raw) if raw else 1
                    if 1 <= choice <= len(sources):
                        winner = sources[choice - 1]
                    else:
                        sources.sort(key=lambda s: s["repo_priority"])
                        winner = sources[0]
                        print(f"   ℹ️ 範囲外の番号です。priority の高い '{winner['repo_name']}' を使用します")
                except (ValueError, EOFError):
                    sources.sort(key=lambda s: s["repo_priority"])
                    winner = sources[0]
                    print(f"   ℹ️ 自動選択: priority の高い '{winner['repo_name']}' を採用します")
            else:
                sources.sort(key=lambda s: s["repo_priority"])
                winner = sources[0]
                print(f"   ℹ️ 競合 '{sname}': priority の高い '{winner['repo_name']}' を自動採用します")

            conflicts.append({
                "skill": sname,
                "adopted": winner["repo_name"],
                "rejected": [s["repo_name"] for s in sources if s != winner],
            })

        # ---- deprecated スキルをアンインストール ----
        if winner.get("tier") == "deprecated":
            dest = os.path.join(skill_home, sname)
            dep_by = winner.get("deprecated_by", "")
            if os.path.isdir(dest):
                shutil.rmtree(dest)
                removed_deprecated.append({"name": sname, "deprecated_by": dep_by})
                successor = f" → {dep_by}" if dep_by else ""
                print(f"   🗑️  {sname}: deprecated{successor} → ユーザーホームから削除しました")
            else:
                successor = f" → {dep_by}" if dep_by else ""
                print(f"   ℹ️  {sname}: deprecated{successor}（未インストール、スキップ）")
            continue

        # ---- pinned_commit 対応 ----
        existing_skill = next(
            (s for s in reg.get("installed_skills", []) if s["name"] == sname),
            None,
        )
        pinned = existing_skill.get("pinned_commit") if existing_skill else None

        if pinned:
            repo_cache = os.path.join(cache_dir, winner["repo_name"])
            try:
                subprocess.run(
                    ["git", "fetch", "--depth", "1", "origin", pinned],
                    cwd=repo_cache, check=True,
                    capture_output=True, text=True, encoding="utf-8",
                )
                subprocess.run(
                    ["git", "checkout", pinned],
                    cwd=repo_cache, check=True,
                    capture_output=True, text=True, encoding="utf-8",
                )
                winner["full_path"] = os.path.join(repo_cache, winner["source_path"])
                winner["commit_hash"] = pinned[:7]
                print(f"   📌 {sname}: pinned commit {pinned[:7]} を使用")
            except subprocess.CalledProcessError:
                print(f"   ⚠️ {sname}: pinned commit {pinned[:7]} の取得に失敗。最新版を使用します")
                pinned = None

        # ---- ローカル変更保護チェック ----
        if existing_skill and check_sync_protection(existing_skill, reg):
            print(f"   🛡️  {sname}: ローカル変更あり → pull をスキップ（protect_local_modified=true）")
            print(f"         解除する場合: python delta_tracker.py --skill {sname}  # 差分なしなら自動解除")
            continue

        # ---- バージョン比較（version_ahead の判定） ----
        local_ver = existing_skill.get("version") if existing_skill else None
        central_ver = _read_frontmatter_version(winner["full_path"])
        version_ahead = _version_tuple(local_ver) > _version_tuple(central_ver)
        if version_ahead:
            print(f"   ⚠️  {sname}: ローカル v{local_ver} が中央 v{central_ver or '?'} より新しい → pull で上書きします")

        dest = os.path.join(skill_home, sname)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(winner["full_path"], dest)

        enabled = existing_skill.get("enabled", True) if existing_skill else True
        version = _read_frontmatter_version(dest)

        installed.append({
            "name": sname,
            "source_repo": winner["repo_name"],
            "source_path": winner["source_path"],
            "commit_hash": winner["commit_hash"],
            "installed_at": datetime.now().isoformat(),
            "enabled": enabled,
            "pinned_commit": pinned,
            "version": version,
            "central_version": central_ver,
            "version_ahead": version_ahead,
        })

    # レジストリ更新
    existing = {s["name"]: s for s in reg.get("installed_skills", [])}
    for s in installed:
        old = existing.get(s["name"], {})
        # v3フィールドを引き継ぐ
        s["feedback_history"] = old.get("feedback_history", [])
        s["pending_refinement"] = old.get("pending_refinement", False)
        # v5フィールドを設定する（pull後はソース追跡情報を更新、統計は引き継ぐ）
        # s["version"], s["central_version"], s["version_ahead"] は installed.append() 時に設定済み
        s["lineage"] = {
            "origin_repo": s["source_repo"],
            "origin_commit": s["commit_hash"],
            "origin_version": s.get("central_version"),
            "local_modified": False,
            "diverged_at": None,
            "local_changes_summary": "",
        }
        s["metrics"] = old.get("metrics", {
            "total_executions": 0,
            "ok_rate": None,
            "last_executed_at": None,
            "central_ok_rate": None,
        })
        existing[s["name"]] = s
    # deprecated により削除されたスキルをレジストリからも除去
    for r in removed_deprecated:
        existing.pop(r["name"], None)
    reg["installed_skills"] = list(existing.values())
    save_registry(reg)

    # ---- リモートから削除されたスキルをクリーンアップ ----
    # skill_name 指定時（特定スキルのみpull）はプルーニング対象外
    if skill_name is None:
        pulled_repo_names = {r["name"] for r in repos}
        current_installed = {s["name"]: s for s in reg.get("installed_skills", [])}
        for sname, skill in current_installed.items():
            if sname in candidates:
                continue  # リモートにまだ存在する
            src_repo = skill.get("source_repo", "local")
            if src_repo == "local":
                continue  # ローカル昇格スキルは削除しない
            if src_repo not in pulled_repo_names:
                continue  # 今回のpull対象外リポジトリのスキルは触らない
            if skill.get("pinned_commit"):
                skipped_pinned.append(sname)
                print(f"   ⚠️  {sname}: リモートから削除済みですが pin されているためスキップ")
                continue
            # リモートから完全に削除されたスキル → ユーザーホームから削除
            dest = os.path.join(skill_home, sname)
            if os.path.isdir(dest):
                shutil.rmtree(dest)
                print(f"   🗑️  {sname}: リモートから削除済み → ユーザーホームから削除しました")
            pruned.append(sname)

        if pruned:
            reg["installed_skills"] = [
                s for s in reg["installed_skills"] if s["name"] not in pruned
            ]

        # 登録リポジトリに存在しない remote_index エントリを除去
        registered_repo_names = {r["name"] for r in reg.get("repositories", [])}
        stale_repos = [
            k for k in reg.get("remote_index", {})
            if k not in registered_repo_names
        ]
        if stale_repos:
            for k in stale_repos:
                del reg["remote_index"][k]
            print(f"   🧹 remote_index: 未登録リポジトリのエントリを削除しました ({', '.join(stale_repos)})")

        if pruned or stale_repos:
            save_registry(reg)

    # copilot-instructions.md のコピー
    copilot_instruction_parts: list[str] = []
    for repo in repos:
        repo_cache = os.path.join(_cache_dir(), repo["name"])
        src = os.path.join(repo_cache, ".github", "copilot-instructions.md")
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as f:
                copilot_instruction_parts.append(f.read().rstrip())

    if copilot_instruction_parts:
        copilot_dir = os.path.dirname(_skill_home())
        os.makedirs(copilot_dir, exist_ok=True)
        dest = os.path.join(copilot_dir, "copilot-instructions.md")
        merged = _merge_copilot_instructions(copilot_instruction_parts)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(merged)
        print(f"   📋 copilot-instructions.md → {dest}")

    # 結果レポート
    print(f"\n📦 pull 完了")
    print(f"   新規/更新: {len(installed)} 件")
    if removed_deprecated:
        print(f"   非推奨削除: {len(removed_deprecated)} 件")
        for r in removed_deprecated:
            successor = f" → {r['deprecated_by']}" if r.get("deprecated_by") else ""
            print(f"   🗑️  {r['name']} (deprecated{successor})")
    if pruned:
        print(f"   リモート削除除去: {len(pruned)} 件")
        for name in pruned:
            print(f"   🗑️  {name} (リモートから削除済み)")
    if skipped_pinned:
        print(f"   ⚠️  リモート削除済み・pin のためスキップ: {', '.join(skipped_pinned)}")
    if conflicts:
        print(f"   競合解決:  {len(conflicts)} 件")
        for c in conflicts:
            print(f"     {c['skill']}: {c['adopted']} を採用（{', '.join(c['rejected'])} を不採用）")
    for s in installed:
        pin_mark = f" 📌{s['pinned_commit'][:7]}" if s.get("pinned_commit") else ""
        status = "✅" if s["enabled"] else "⏸️"
        print(f"   {status} {s['name']} ← {s['source_repo']} ({s['commit_hash']}){pin_mark}")
    if snap_id and installed:
        print(f"\n   💡 問題があれば元に戻せます:")
        print(f"      python snapshot.py restore --latest")


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="リポジトリからスキルを取得・インストールする",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python pull.py                          # 全リポジトリからすべてのスキルをpull
  python pull.py --skill my-skill         # 特定スキルのみpull
  python pull.py --repo team-skills       # 特定リポジトリからpull
  python pull.py --no-interactive         # 非対話モード（競合はpriority自動解決）
""",
    )
    parser.add_argument("--skill", default=None, metavar="SKILL_NAME",
                        help="取得するスキル名（省略時は全スキル）")
    parser.add_argument("--repo", default=None, metavar="REPO_NAME",
                        help="取得元リポジトリ名（省略時は全リポジトリ）")
    parser.add_argument("--no-interactive", action="store_true",
                        help="非対話モード: 競合はpriority自動解決")
    args = parser.parse_args()

    pull_skills(
        skill_name=args.skill,
        repo_name=args.repo,
        interactive=not args.no_interactive,
    )


if __name__ == "__main__":
    main()
