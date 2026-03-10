#!/usr/bin/env python3
"""スキル依存関係の解析・検証・グラフ出力。

depends_on  : 必須依存（前提スキルが欠如すると失敗リスクあり）
recommends  : 推奨依存（なくても動くが組み合わせると効果が高い）

依存関係の定義場所（優先順）:
  1. <skill>/meta.yaml の depends_on / recommends キー
  2. SKILL.md フロントマターの metadata.depends_on / metadata.recommends（後方互換）

操作:
  deps check [skill_name]   -- インストール状況を検証
  deps graph [skill_name]   -- Mermaid 形式で依存グラフを出力
"""
from __future__ import annotations

import os
import re
import subprocess

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

from registry import load_registry, _skill_home


# ---------------------------------------------------------------------------
# 依存関係解析
# ---------------------------------------------------------------------------

def _parse_dep_list(raw: list) -> list[dict]:
    """YAML リストから depends_on / recommends のエントリを正規化する。"""
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append({"name": str(item.get("name", "")), "reason": str(item.get("reason", ""))})
    return result


def _read_deps_from_meta_yaml(skill_path: str) -> dict | None:
    """meta.yaml から depends_on / recommends を読み取る。

    ファイルが存在しない場合は None を返す。
    """
    meta_path = os.path.join(skill_path, "meta.yaml")
    if not os.path.isfile(meta_path):
        return None

    with open(meta_path, encoding="utf-8") as f:
        raw = f.read()

    if _yaml:
        data = _yaml.safe_load(raw) or {}
    else:
        # yaml 未インストール時の簡易パース（キーのみ抽出、ネスト非対応）
        data = {}
        for line in raw.splitlines():
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                data[key.strip()] = val.strip()

    return {
        "depends_on": _parse_dep_list(data.get("depends_on") or []),
        "recommends": _parse_dep_list(data.get("recommends") or []),
    }


def _read_deps_from_frontmatter(skill_path: str) -> dict:
    """SKILL.md フロントマターから depends_on / recommends を読み取る（後方互換）。"""
    skill_md = os.path.join(skill_path, "SKILL.md")
    if not os.path.isfile(skill_md):
        return {"depends_on": [], "recommends": []}

    with open(skill_md, encoding="utf-8") as f:
        content = f.read()

    fm = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm:
        return {"depends_on": [], "recommends": []}

    result: dict = {"depends_on": [], "recommends": []}
    lines = fm.group(1).splitlines()
    in_metadata = False
    current_section: str | None = None
    current_item: dict | None = None

    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            continue

        if line.startswith("metadata:"):
            in_metadata = True
            current_section = None
            current_item = None
            continue

        if in_metadata:
            indent = len(line) - len(stripped)

            # metadata ブロック終了（インデントなしの別キー）
            if indent == 0:
                in_metadata = False
                current_section = None
                current_item = None
                continue

            # depends_on: / recommends: の開始
            m = re.match(r"^[ \t]+(depends_on|recommends):\s*$", line)
            if m:
                current_section = m.group(1)
                current_item = None
                continue

            # リスト項目 "  - name: ..."
            if current_section and stripped.startswith("-"):
                item_rest = stripped[1:].strip()
                if item_rest.startswith("name:"):
                    name_val = item_rest.split(":", 1)[1].strip().strip("\"'")
                    current_item = {"name": name_val, "reason": ""}
                    result[current_section].append(current_item)
                continue

            # reason フィールド（リスト項目の属性）
            if current_item and re.match(r"^[ \t]+reason:", line):
                reason_val = line.split("reason:", 1)[1].strip().strip("\"'")
                current_item["reason"] = reason_val
                continue

            # 別の metadata キー → セクション終了
            if not stripped.startswith("-") and ":" in stripped:
                key = stripped.split(":")[0]
                if key in ("version", "tags", "author"):
                    current_section = None
                    current_item = None

    return result


def _read_deps(skill_path: str) -> dict:
    """meta.yaml を優先し、なければ SKILL.md フロントマターから依存関係を読み取る。

    Returns:
        {
            'depends_on': [{'name': str, 'reason': str}, ...],
            'recommends': [{'name': str, 'reason': str}, ...],
        }
    """
    meta = _read_deps_from_meta_yaml(skill_path)
    if meta is not None:
        return meta
    return _read_deps_from_frontmatter(skill_path)


def _all_skill_paths() -> dict[str, str]:
    """インストール済み + ワークスペース配下のスキルパスをまとめて返す。

    ワークスペーススキルが優先される。
    Returns: {skill_name: path}
    """
    paths: dict[str, str] = {}

    # インストール済み（~/.copilot/skills/）
    home_dir = _skill_home()
    if os.path.isdir(home_dir):
        for entry in os.listdir(home_dir):
            p = os.path.join(home_dir, entry)
            if os.path.isfile(os.path.join(p, "SKILL.md")):
                paths[entry] = p

    # ワークスペース配下（.github/skills/）優先で上書き
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            ws_skills = os.path.join(result.stdout.strip(), ".github", "skills")
            if os.path.isdir(ws_skills):
                for entry in os.listdir(ws_skills):
                    p = os.path.join(ws_skills, entry)
                    if os.path.isfile(os.path.join(p, "SKILL.md")):
                        paths[entry] = p
    except FileNotFoundError:
        pass

    return paths


# ---------------------------------------------------------------------------
# deps check
# ---------------------------------------------------------------------------

def check_deps(skill_name: str | None = None) -> int:
    """depends_on の充足状況を検証する。

    Returns: 1以上の欠如があれば 1、すべて充足なら 0。
    """
    all_paths = _all_skill_paths()
    installed = set(all_paths.keys())

    targets: list[str]
    if skill_name:
        if skill_name not in all_paths:
            print(f"❌ スキル '{skill_name}' が見つかりません")
            return 1
        targets = [skill_name]
    else:
        targets = sorted(all_paths.keys())

    issues_found = False

    for name in targets:
        deps = _read_deps(all_paths[name])
        required = deps["depends_on"]
        optional = deps["recommends"]

        if not required and not optional:
            continue

        print(f"📦 {name}")

        for dep in required:
            dep_name = dep["name"]
            ok = dep_name in installed
            icon = "✅" if ok else "❌"
            status = "" if ok else "  ← 未インストール"
            print(f"   {icon} [必須] {dep_name}{status}")
            if dep.get("reason"):
                print(f"         理由: {dep['reason']}")
            if not ok:
                issues_found = True

        for rec in optional:
            rec_name = rec["name"]
            ok = rec_name in installed
            icon = "✅" if ok else "⚠️ "
            status = "" if ok else "  ← 未インストール（推奨）"
            print(f"   {icon} [推奨] {rec_name}{status}")
            if rec.get("reason"):
                print(f"         理由: {rec['reason']}")

        print()

    if not issues_found and skill_name is None:
        print("✅ すべての必須依存関係が充足されています")

    return 1 if issues_found else 0


# ---------------------------------------------------------------------------
# deps graph
# ---------------------------------------------------------------------------

def show_graph(skill_name: str | None = None) -> None:
    """インストール済みスキルの依存グラフを Mermaid flowchart で出力する。

    skill_name を指定した場合は、そのスキルの直接依存のみ表示する。
    """
    all_paths = _all_skill_paths()

    if skill_name:
        if skill_name not in all_paths:
            print(f"❌ スキル '{skill_name}' が見つかりません")
            return
        targets = [skill_name]
    else:
        targets = sorted(all_paths.keys())

    # エッジ収集
    edges_required: list[tuple[str, str, str]] = []   # (from, to, reason)
    edges_recommends: list[tuple[str, str, str]] = []

    for name in targets:
        deps = _read_deps(all_paths[name])
        for dep in deps["depends_on"]:
            edges_required.append((name, dep["name"], dep.get("reason", "")))
        for rec in deps["recommends"]:
            edges_recommends.append((name, rec["name"], rec.get("reason", "")))

    if not edges_required and not edges_recommends:
        print("（依存関係の定義がありません）")
        return

    # ノード収集
    nodes: set[str] = set()
    for a, b, _ in edges_required + edges_recommends:
        nodes.add(a)
        nodes.add(b)

    # 未インストールノードを特定（破線表示用）
    installed = set(all_paths.keys())

    print("```mermaid")
    print("flowchart TD")
    print()

    # ノードスタイル
    for node in sorted(nodes):
        safe = node.replace("-", "_")
        if node not in installed:
            print(f'    {safe}["{node} ⚠️"]:::missing')
        else:
            print(f'    {safe}["{node}"]')

    print()

    # エッジ（必須: 実線 -->）
    print("    %% 必須依存 depends_on")
    for frm, to, reason in edges_required:
        f_safe = frm.replace("-", "_")
        t_safe = to.replace("-", "_")
        label = f"|{reason[:30]}|" if reason else ""
        print(f"    {f_safe} -->{label} {t_safe}")

    if edges_recommends:
        print()
        print("    %% 推奨依存 recommends")
        for frm, to, reason in edges_recommends:
            f_safe = frm.replace("-", "_")
            t_safe = to.replace("-", "_")
            label = f"|{reason[:30]}|" if reason else ""
            print(f"    {f_safe} -..->{label} {t_safe}")

    print()
    print("    classDef missing fill:#fee,stroke:#c33,stroke-dasharray:4")
    print("```")
    print()
    print("凡例: 実線 `-->` = depends_on（必須）、破線 `-..->` = recommends（推奨）")
    print("      ⚠️ = 未インストール")
