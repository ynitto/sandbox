#!/usr/bin/env python3
"""
agent-mentor スキル静的検証スクリプト

テストシナリオ(test-scenarios.md)の期待動作が
スキル定義ファイルに実装されているかを検証する。
"""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"
DESIGN_MD = SKILL_DIR / "references" / "agent-design.md"
TEMPLATES_MD = SKILL_DIR / "references" / "subagent-templates.md"
SCENARIOS_MD = SKILL_DIR / "references" / "test-scenarios.md"


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


agent = read(SKILL_MD)
design = read(DESIGN_MD)
templates = read(TEMPLATES_MD)
scenarios = read(SCENARIOS_MD)

results: list[tuple[str, bool, str]] = []  # (label, passed, detail)


def check(label: str, condition: bool, detail: str = "") -> None:
    results.append((label, condition, detail))


# ──────────────────────────────────────────────
# ファイル存在確認
# ──────────────────────────────────────────────
check("ファイル: SKILL.md", SKILL_MD.exists(), str(SKILL_MD))
check("ファイル: agent-design.md", DESIGN_MD.exists(), str(DESIGN_MD))
check("ファイル: subagent-templates.md", TEMPLATES_MD.exists(), str(TEMPLATES_MD))
check("ファイル: test-scenarios.md", SCENARIOS_MD.exists(), str(SCENARIOS_MD))

# ──────────────────────────────────────────────
# 4フェーズ構造
# ──────────────────────────────────────────────
check("Phase 1: 壁打ち（Clarify）の定義", "Phase 1: 壁打ち" in agent)
check("Phase 2: スキル選定＆実行計画の定義", "Phase 2: スキル選定" in agent)
check("Phase 3: 実行の定義", "Phase 3: 実行" in agent)
check("Phase 4: レビュー＆フィードバックの定義", "Phase 4: レビュー" in agent)

# ──────────────────────────────────────────────
# ガードレール
# ──────────────────────────────────────────────
check("ガードレール: 最大7問", "最大 **7問**" in agent or "最大7問" in agent or "最大 7 問" in agent)
check("ガードレール: レビュー再実行最大2回", "最大 2 回" in agent or "最大2回" in agent)
check("ガードレール: 修正リトライ最大2回", "修正リトライ" in agent)

# ──────────────────────────────────────────────
# 委譲ルール
# ──────────────────────────────────────────────
check("委譲: skill-selector をサブエージェントで起動", "skill-selector" in agent and "runSubagent" in agent)
check("委譲: Phase 3 は自己実装禁止", "自分で実装" in agent or "自身が実装しない" in agent or "自分で実行してはいけない" in agent)
check("委譲: Phase 4 レビューはサブエージェント", "並列サブエージェント" in agent)

# ──────────────────────────────────────────────
# scrum-master エスカレーション（シナリオ 5）
# ──────────────────────────────────────────────
check("エスカレーション: 独立成果物3つ以上で案内", "3つ以上" in agent or "3 つ以上" in agent)
check("エスカレーション: 複数スプリント条件", "複数スプリント" in agent)

# ──────────────────────────────────────────────
# エラーリカバリー（シナリオ 6・7）
# ──────────────────────────────────────────────
check("エラーリカバリー: スキル失敗時の選択肢提示", "リトライ" in agent and "中断" in agent)
check("エラーリカバリー: スキルが見つからない場合の対処", "skill-recruiter" in agent or "skill-creator" in agent)

# ──────────────────────────────────────────────
# レビュー戦略（シナリオ 1・2・3・4）
# ──────────────────────────────────────────────
check("レビュー戦略: ソースコード→code-reviewer", "code-reviewer" in design)
check("レビュー戦略: ドキュメント→document-reviewer", "document-reviewer" in design)
check("レビュー戦略: アーキテクチャ→architecture-reviewer", "architecture-reviewer" in design)
check("レビュー戦略: セキュリティ→security-reviewer", "security-reviewer" in design)

# ──────────────────────────────────────────────
# サブエージェントテンプレート
# ──────────────────────────────────────────────
check("テンプレート: skill-selector 呼び出し時", "skill-selector 呼び出し時" in templates)
check("テンプレート: スキル実行時", "スキル実行時" in templates)
check("テンプレート: brainstorming スキル実行時", "brainstorming スキル実行時" in templates)
check("テンプレート: レビュー並列実行時", "レビュー並列実行時" in templates)
check("テンプレート: 修正リトライ時", "修正リトライ時" in templates)
check("テンプレート: フィードバック保存時", "フィードバック保存時" in templates)
check("テンプレート: git-skill-manager フィードバック記録", "git-skill-manager" in templates and "スキルフィードバック記録時" in templates)

# ──────────────────────────────────────────────
# 改善1: クイック壁打ちモード（シナリオ 10）
# ──────────────────────────────────────────────
check("[改善1] クイックモード: 最大3問の制限", "最大 **3問**" in agent or "最大3問" in agent or "最大 3 問" in agent)
check("[改善1] クイックモード: 適用条件の定義（ゴール明示）", "ゴール（何をしたいか）が明示" in agent)
check("[改善1] クイックモード: 適用条件の定義（対象特定）", "対象ファイル・機能・範囲が特定できる" in agent)

# ──────────────────────────────────────────────
# 改善2: タスク種別の自動推定（シナリオ 10・11）
# ──────────────────────────────────────────────
check("[改善2] タスク種別: 自動推定の定義", "タスク種別の自動推定" in agent)
check("[改善2] タスク種別: 推定結果のフォーマット", "理解:" in agent and "概要:" in agent)
check("[改善2] タスク種別: バグ修正を推定対象に含む", "バグ修正" in agent)
check("[改善2] タスク種別: ユーザー訂正の受け付け", "タスク定義の「対象」に反映" in agent or "修正なく" in agent)

# ──────────────────────────────────────────────
# 改善3: git-skill-manager テンプレート（フィードバック記録）
# ──────────────────────────────────────────────
check("[改善3] git-skill-manager: SKILL.md 参照指示", "git-skill-manager/SKILL.md" in templates)
check("[改善3] git-skill-manager: 評価フィールドの定義", "評価: [良かった / 改善の余地あり / 問題があった]" in templates)
check("[改善3] git-skill-manager: 結果フォーマット指定", "記録先:" in templates)

# ──────────────────────────────────────────────
# 改善4: Phase間の状態スナップショット（シナリオ 12）
# ──────────────────────────────────────────────
check("[改善4] スナップショット: Phase 2 完了後に保存", "状態スナップショット" in agent)
check("[改善4] スナップショット: session-snapshot カテゴリ", "session-snapshot" in agent)
check("[改善4] スナップショット: recall_memory で復元可能", "recall_memory" in agent and "復元" in agent)

# ──────────────────────────────────────────────
# 改善5: フォローアップ提案（シナリオ 13）
# ──────────────────────────────────────────────
check("[改善5] フォローアップ: Phase 4 に Step 4 追加", "Step 4" in agent and "フォローアップ" in agent)
check("[改善5] フォローアップ: 1〜3件提案", "1〜3件" in agent)
check("[改善5] フォローアップ: 任意対応であることを明示", "任意" in agent)
check("[改善5] フォローアップ: scrum-master-agent への案内", "scrum-master-agent" in agent and "フォローアップ" in agent)

# ──────────────────────────────────────────────
# 改善6: Warning 時の自動判定ルール（シナリオ 14）
# ──────────────────────────────────────────────
check("[改善6] Warning判定: 3件以上で修正推奨確認", "3件以上" in agent)
check("[改善6] Warning判定: 1〜2件は任意対応", "1〜2件" in agent)
check("[改善6] Warning判定: セキュリティ・パフォーマンスは件数に関わらず確認", "セキュリティ・パフォーマンス" in agent)
check("[改善6] Warning判定: design.md にも判定ルール記載", "3件以上" in design and "セキュリティ・パフォーマンス" in design)

# ──────────────────────────────────────────────
# テストシナリオカバレッジ
# ──────────────────────────────────────────────
for i in range(1, 15):
    check(f"テストシナリオ {i:02d}: 定義済み", f"シナリオ {i}:" in scenarios)

# ──────────────────────────────────────────────
# 結果出力
# ──────────────────────────────────────────────
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total = len(results)

print(f"\nagent-mentor スキル検証結果")
print("=" * 60)

current_group = ""
for label, ok, detail in results:
    group = label.split(":")[0]
    if group != current_group:
        current_group = group
        print()
    status = PASS if ok else FAIL
    mark = "✓" if ok else "✗"
    print(f"  {mark} [{status}] {label}")
    if not ok and detail:
        print(f"          → {detail}")

print()
print("=" * 60)
print(f"結果: {passed}/{total} PASS  ({failed} FAIL)")
print()

if failed > 0:
    print("FAILした項目を確認してください。")
    sys.exit(1)
else:
    print("すべての検証が通過しました。")
    sys.exit(0)
