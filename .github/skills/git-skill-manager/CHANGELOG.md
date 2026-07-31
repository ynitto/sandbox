# Changelog

## 1.1.0 — 2026-04-03

- fix(git-skill-manager): SKILL.md を450行未満に削減してWARNを解消 (`e24a89e`)
- feat(git-skill-manager): CLIエントリポイントを追加してコマンド検索問題を解消 (`b6753ec`)
- fix(git-skill-manager): Windows PowerShell でマルチバイト文字を含む JSON が原因で不安定になる問題を修正 (`0adb96f`)
- fix: use ensure_ascii=True when writing skill-registry.json (`1afc37c`)
- skill-recruiter を skill-creator v3.0.0 に統合 (`f2ac018`)
- 統合スキルへの参照を更新 (`e16ebec`)
- skill-creator統合に伴う他スキルの参照を一斉更新 (`af68987`)
- docs: git-skill-manager の発動フレーズを他スキルと重複しないものに整理 (`9f12e88`)
- docs: git-skill-manager description の発動フレーズを追加 (`8823467`)
- docs: git-skill-manager description を見直し (`e5635fa`)
- fix: skill-evaluator 警告2件を解消 (`1013e13`)
- enhance: スキル群の4項目改善を実装 (`229b655`)

## 1.0.1 — 2026-03-15

- Update _instructions_home function to return the instructions directory path (`1c88ed5`)
- Refactor instruction file copying logic and update print statements for clarity (`6f19e3c`)
- Add sync_instructions script and integrate automatic instruction synchronization (`0f47e01`)
- docs: Windows パス読み替え注記を整理（自動解決済み箇所を削除） (`0d0a1d5`)
- refactor: copilot ハードコードを廃止し __file__ ベースのパス解決に統一 (`fac1e7c`)
- feat: マルチエージェント対応（copilot/claude/codex/kiro） (`84c45f2`)
- カスタムエージェント機能を削除 (`813618b`)
- Merge pull request #122 from ynitto/claude/enhance-task-quality-takt-tiWch (`e8cd6bf`)
- feat(teams-poster): タイトルと @channel/@team メンション機能を追加 (`57aa3c3`)
- feat(engineer-mentor-agent): エージェントを6点強化 (`f0ba5c3`)
- docs: push-branch を push のオプション操作として統合 (`5f8a1f1`)
- Merge pull request #103 from ynitto/copilot/check-skill-selector-logic (`7391340`)
