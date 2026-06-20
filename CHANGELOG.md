# CHANGELOG

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — versions use [Semantic Versioning](https://semver.org/).

---

## [v1.0.0] — 2026-06-20

Initial release. 188 tests passing (kiro-flow + kiro-autonomous).

### kiro-autonomous

#### Added
- 並列消費 — kiro-flow の worker 並列へ寄せる（§11）
- 共有レジストリ越しの別ホスト発見（§11-7）
- 汎用の取り込み口 enqueue / inbox（§11-5）
- 常駐ライフサイクル start / stop / restart（§11-4）
- 自律裁定の判断材料を拡充（§11-3）
- 真偽フラグを設定ファイル対応（§11-1）
- コスト予算（トークン/金額の上限と per-task 計上）（§11-2）
- Loop Engineering 中核4機能（計測・自己生成・依存・回帰ゲート）
- 検収ゲート — verify=PASS でも人の承認を要する review 状態
- 自律裁定フック（needs 直前で kiro-cli が積み直し可否を判断）
- 設定ファイル対応（YAML 任意 / JSON フォールバック）＋サンプル
- 稼働インスタンスのレジストリ追加＋スキルを WSL/Windows 対応に
- サブコマンド省略時を `run --watch`（常駐監視）の既定に
- ltm-use への学習昇格（プロジェクト横断・エージェント不要）
- 編集完了の明示検知と成果物の納品書
- ファイルを `.kiro-autonomous/` に集約・一時バスを自動クリーンアップ
- DR 学習と rot 検知

#### Changed
- `auto_adjudicate` の既定を on に変更

### kiro-flow

#### Added
- flow-planner をデフォルト planner に変更し `~/.kiro/skills` のフォールバック追加
- flow-planner スキル — kiro-flow orchestrator 向け 3 フェーズパイプライン
- タスクタイムアウト機構（kiro-cli 呼び出しの無限ハング防止）
- 最終結果プレゼンテーションとコマンドアップデート
- 一時ファイルの自動クリーンアップ

---

[v1.0.0]: https://github.com/ynitto/sandbox/releases/tag/v1.0.0
