"""SKILL.md の構造・整合性テスト。

以下を検証する:
1. フロントマター — 必須フィールドの存在・型・値の妥当性
2. 参照リンク — references/*.md へのリンクが全て実在するファイルを指す
3. スクリプト参照 — 「scripts/xxx.py」の記載が全て実在するスクリプトを指す
4. 操作一覧 — 「操作一覧」テーブルの全操作がセクション見出しとして定義済み
5. リファレンスファイル — 参照先の .md が空でないこと
"""
import os
import re
import sys

import pytest

# SKILL.md のパスを解決
SKILL_MD = os.path.join(os.path.dirname(__file__), "..", "SKILL.md")
REFERENCES_DIR = os.path.join(os.path.dirname(__file__), "..", "references")
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")

# -------------------------------------------------------------------
# ヘルパー: SKILL.md を一度だけ読む
# -------------------------------------------------------------------

@pytest.fixture(scope="module")
def skill_md_content():
    with open(SKILL_MD, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def frontmatter(skill_md_content):
    """フロントマターブロック（--- ... ---）を dict として返す。"""
    match = re.match(r"^---\s*\n(.*?)\n---", skill_md_content, re.DOTALL)
    assert match, "SKILL.md にフロントマターが見つかりません"
    return _parse_simple_yaml(match.group(1))


def _parse_simple_yaml(text: str) -> dict:
    """フロントマター用の簡易 YAML パーサー（ネスト1段まで対応）。"""
    result: dict = {}
    current_key = None
    list_key = None

    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue

        # トップレベルキー
        top_match = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if top_match:
            list_key = None
            current_key = top_match.group(1)
            val = top_match.group(2).strip()
            if val:
                result[current_key] = val.strip("\"'")
            else:
                result[current_key] = {}
            continue

        # ネストしたキー（2スペースインデント）
        nested_match = re.match(r"^  (\w[\w-]*):\s*(.*)", line)
        if nested_match and isinstance(result.get(current_key), dict):
            key2 = nested_match.group(1)
            val2 = nested_match.group(2).strip().strip("\"'")
            result[current_key][key2] = val2
            list_key = None
            continue

        # リスト要素
        list_match = re.match(r"^    - (.*)", line)
        if list_match and isinstance(result.get(current_key), dict):
            # ネストしたリスト（例: metadata.tags）
            last_nested = list(result[current_key].keys())
            if last_nested:
                lk = last_nested[-1]
                if not isinstance(result[current_key][lk], list):
                    result[current_key][lk] = []
                result[current_key][lk].append(list_match.group(1).strip())

    return result


# -------------------------------------------------------------------
# 1. フロントマター検証
# -------------------------------------------------------------------

class TestFrontmatter:
    def test_name_exists(self, frontmatter):
        assert "name" in frontmatter, "name フィールドが必要です"
        assert frontmatter["name"].strip()

    def test_name_matches_directory(self, frontmatter):
        """スキル名がディレクトリ名と一致すること。"""
        dir_name = os.path.basename(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        assert frontmatter["name"] == dir_name, (
            f"name '{frontmatter['name']}' がディレクトリ名 '{dir_name}' と一致しません"
        )

    def test_description_exists(self, frontmatter):
        assert "description" in frontmatter, "description フィールドが必要です"
        assert len(frontmatter["description"]) > 10, "description が短すぎます"

    def test_metadata_exists(self, frontmatter):
        assert "metadata" in frontmatter, "metadata ブロックが必要です"
        assert isinstance(frontmatter["metadata"], dict)

    def test_metadata_version_exists(self, frontmatter):
        meta = frontmatter["metadata"]
        assert "version" in meta, "metadata.version が必要です"

    def test_metadata_version_semver(self, frontmatter):
        version = frontmatter["metadata"]["version"]
        assert re.match(r"^\d+\.\d+\.\d+$", version), (
            f"metadata.version '{version}' は X.Y.Z 形式である必要があります"
        )

    def test_metadata_tier_exists(self, frontmatter):
        meta = frontmatter["metadata"]
        assert "tier" in meta, "metadata.tier が必要です"

    def test_metadata_tier_valid(self, frontmatter):
        tier = frontmatter["metadata"].get("tier", "")
        valid_tiers = {"core", "standard", "experimental", "workspace"}
        assert tier in valid_tiers, (
            f"metadata.tier '{tier}' は {valid_tiers} のいずれかである必要があります"
        )

    def test_metadata_category_exists(self, frontmatter):
        meta = frontmatter["metadata"]
        assert "category" in meta, "metadata.category が必要です"

    def test_metadata_tags_is_list(self, frontmatter):
        meta = frontmatter["metadata"]
        if "tags" in meta:
            assert isinstance(meta["tags"], list), "metadata.tags はリストである必要があります"
            assert len(meta["tags"]) > 0, "metadata.tags が空です"


# -------------------------------------------------------------------
# 2. 参照リンク検証（references/*.md）
# -------------------------------------------------------------------

def _extract_reference_links(content: str) -> list[str]:
    """SKILL.md 内の `[text](references/xxx.md)` をすべて抽出する。"""
    return re.findall(r"\[.*?\]\((references/[^)]+)\)", content)


class TestReferenceLinks:
    def test_no_broken_reference_links(self, skill_md_content):
        links = _extract_reference_links(skill_md_content)
        assert links, "references/ へのリンクが1件も見つかりません"

        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        broken = []
        for link in links:
            target = os.path.join(skill_dir, link)
            if not os.path.isfile(target):
                broken.append(link)

        assert not broken, f"存在しない参照リンクがあります:\n" + "\n".join(f"  - {b}" for b in broken)

    def test_reference_links_are_unique(self, skill_md_content):
        """同一ファイルへのリンクは3回以上では過剰。
        1ファイルが複数操作をカバーする場合（feedback-loop.md など）は2回まで許容。
        """
        links = _extract_reference_links(skill_md_content)
        over_linked = [l for l in set(links) if links.count(l) > 2]
        assert not over_linked, (
            f"同じ参照リンクが3回以上使われています: {over_linked}"
        )

    def test_all_reference_files_are_linked(self, skill_md_content):
        """references/ 配下の全ファイルが SKILL.md からリンクされていること。"""
        linked = set(_extract_reference_links(skill_md_content))
        existing = set(
            f"references/{f}"
            for f in os.listdir(REFERENCES_DIR)
            if f.endswith(".md")
        )
        unlinked = existing - linked
        assert not unlinked, (
            f"references/ 内にリンクされていないファイルがあります:\n"
            + "\n".join(f"  - {f}" for f in sorted(unlinked))
        )


# -------------------------------------------------------------------
# 3. スクリプト参照検証（scripts/xxx.py）
# -------------------------------------------------------------------

def _extract_script_refs(content: str) -> list[str]:
    """SKILL.md 内の `scripts/xxx.py` 参照をすべて抽出する。"""
    return re.findall(r"scripts/([\w]+\.py)", content)


class TestScriptReferences:
    def test_all_referenced_scripts_exist(self, skill_md_content):
        refs = _extract_script_refs(skill_md_content)
        assert refs, "scripts/*.py への参照が1件も見つかりません"

        missing = []
        for script in set(refs):
            if not os.path.isfile(os.path.join(SCRIPTS_DIR, script)):
                missing.append(script)

        assert not missing, (
            f"存在しないスクリプトが参照されています:\n"
            + "\n".join(f"  - scripts/{s}" for s in sorted(missing))
        )


# -------------------------------------------------------------------
# 4. 操作一覧 ↔ セクション見出し の整合性
# -------------------------------------------------------------------

def _extract_operations_from_table(content: str) -> list[str]:
    """「操作一覧」テーブルから操作名（**xxx** の xxx 部分）を抽出する。"""
    ops = re.findall(r"\|\s*\*\*([^*|]+)\*\*\s*\|", content)
    return [op.strip() for op in ops]


def _extract_h2_sections(content: str) -> list[str]:
    """SKILL.md の ## 見出しを抽出する。"""
    return re.findall(r"^## (.+)$", content, re.MULTILINE)


class TestOperationCoverage:
    # 操作一覧に存在するが独立セクションを持たない操作（グループ化されている）
    GROUPED_OPS = {
        "repo list",       # repo add セクションで一括説明
        "repo remove",     # 同上
        "unpin",           # pin セクションで説明
        "unlock",          # lock セクションで説明
        "profile use",     # profile セクションで一括説明
        "profile create",  # 同上
        "profile list",    # 同上
        "profile delete",  # 同上
        "rollback",        # snapshot セクションで説明
        "search --refresh",# search セクションで説明
        "metrics-detail",  # metrics セクションで説明
        "metrics-co",      # 同上
        "metrics-collect", # 同上
        "deps-graph",      # deps セクションで説明
    }

    def test_operations_have_sections_or_are_grouped(self, skill_md_content):
        ops = _extract_operations_from_table(skill_md_content)
        assert ops, "操作一覧テーブルから操作が抽出できません"

        sections = _extract_h2_sections(skill_md_content)
        # セクション見出しを正規化（/ を含む複合見出しを分割、大文字を小文字に）
        section_names: set[str] = set()
        for s in sections:
            for part in re.split(r"\s*/\s*", s):
                section_names.add(part.strip().lower())

        uncovered = []
        for op in ops:
            op_lower = op.lower()
            if op_lower in self.GROUPED_OPS:
                continue
            # 操作名がいずれかのセクション見出しに部分一致するか確認
            if not any(op_lower in sn or sn.startswith(op_lower.split()[0]) for sn in section_names):
                uncovered.append(op)

        assert not uncovered, (
            f"以下の操作に対応するセクションが見つかりません:\n"
            + "\n".join(f"  - {op}" for op in uncovered)
        )

    def test_operation_table_is_not_empty(self, skill_md_content):
        ops = _extract_operations_from_table(skill_md_content)
        assert len(ops) >= 10, f"操作一覧が少なすぎます（{len(ops)}件）"


# -------------------------------------------------------------------
# 5. リファレンスファイルの内容検証
# -------------------------------------------------------------------

class TestReferenceFileContents:
    @pytest.mark.parametrize("ref_file", [
        f for f in os.listdir(REFERENCES_DIR) if f.endswith(".md")
    ])
    def test_reference_file_not_empty(self, ref_file):
        path = os.path.join(REFERENCES_DIR, ref_file)
        content = open(path, encoding="utf-8").read().strip()
        assert content, f"references/{ref_file} が空です"

    @pytest.mark.parametrize("ref_file", [
        f for f in os.listdir(REFERENCES_DIR) if f.endswith(".md")
    ])
    def test_reference_file_has_heading(self, ref_file):
        path = os.path.join(REFERENCES_DIR, ref_file)
        content = open(path, encoding="utf-8").read()
        assert re.search(r"^#+ .+", content, re.MULTILINE), (
            f"references/{ref_file} に見出し（# ...）がありません"
        )


# -------------------------------------------------------------------
# 6. SKILL.md 全体の構造検証
# -------------------------------------------------------------------

class TestSkillMdStructure:
    REQUIRED_SECTIONS = [
        "操作一覧",
        "pull",
        "push",
        "feedback",
        "deps",
        "snapshot",
    ]

    def test_required_sections_present(self, skill_md_content):
        sections = _extract_h2_sections(skill_md_content)
        section_set = {s.lower() for s in sections}
        missing = [
            s for s in self.REQUIRED_SECTIONS
            if not any(s.lower() in sec for sec in section_set)
        ]
        assert not missing, f"必須セクションがありません: {missing}"

    def test_has_h1_title(self, skill_md_content):
        assert re.search(r"^# .+", skill_md_content, re.MULTILINE), \
            "H1 タイトルがありません"

    def test_frontmatter_is_first(self, skill_md_content):
        assert skill_md_content.startswith("---"), \
            "SKILL.md はフロントマター（---）で始まる必要があります"

    def test_no_dead_anchor_links(self, skill_md_content):
        """同一ファイル内のアンカーリンク（#xxx）が存在しないこと（外部参照のみ）。"""
        # 他ファイルへのリンクが主体であり、内部アンカーリンクは不要
        internal_anchors = re.findall(r"\[.*?\]\(#[^)]+\)", skill_md_content)
        assert not internal_anchors, (
            f"内部アンカーリンクは使用しないでください: {internal_anchors}"
        )
