# CHANGELOG

All notable changes to this project are documented in this file.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — versions use [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### kiro-autonomous

#### Added
- `doctor` サブコマンド。ログ/状態/環境から稼働を診断し、原因を **env（ユーザー環境固有）/
  config（設定）/ program（プログラム上の不具合）** に分類する。収集・修正・起票の駆動は決定的に、
  診断と分類は kiro-cli へ委譲（kiro-cli 不在時は決定的チェックのみで続行）。`--fix` で env/config を
  修正（`create-dirs` / policy への保護デニーリスト追記）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する。**スキルが見つからなければ出力のみ**。終了コード `0`=健康/`1`=所見あり/
  `2`=未解決の critical。既定（`--fix` 無し）は無害な診断のみ。
- `doctor` の **実行層 kiro-flow との連携**（`--with-flow`・既定 on／`--no-flow` で本体のみ）。
  同じバスに対して `kiro-flow doctor --json` を呼び、実行層の所見を `[flow]` 印で統合する。`--fix` 時は
  kiro-flow 側にも委譲し、kiro-flow が自分の env/config 修正と program 起票を担う（二重作業を避ける）。

### kiro-flow

#### Added
- `doctor` サブコマンド。run 状態/イベント/環境から稼働を診断し、原因を **env / config / program** に
  分類する。収集・修正・起票の駆動は決定的に、診断と分類は kiro-cli へ委譲（不在時は決定的チェックのみ）。
  `--fix` で env/config を修正（`ensure-bus`＝バス作成）し、program の不具合は `gitlab-idd` スキルで
  GitLab イシューを起票する（スキルが無ければ出力のみ）。`--json` の findings は kiro-autonomous の doctor と
  同一スキーマで、単独でも kiro-autonomous からの連携呼び出しでも使える。終了コード `0`/`1`/`2`。
- executor（ワーカーバス）のプラグイン化。kiro-loop の hooks（event_hook）と同じ流儀で、
  `--executor` に組み込み名（`kiro`/`stub`）に加えてプラグイン名（例 `gitlab`）や `.py` パスを
  指定できる。プラグインは標準ライブラリのみの単一ファイルで `execute(kind, goal, dep_results,
  model, art_dir, dep_arts)` を公開し、本体が `importlib` で動的ロードする（mtime キャッシュ付き）。
  検索順は スクリプト同階層 `executors/` → リポジトリ `tools/kiro-flow/executors/` →
  `~/.kiro/kiro-flow/executors/`（インストーラ配置）→ 設定 `executor_dir`。プラグイン固有設定は
  同名のトップレベル設定ブロックを JSON 化し環境変数 `KIRO_FLOW_EXECUTOR_CONFIG` で渡す。
  `install.sh` は同梱プラグインを `~/.kiro/kiro-flow/executors/` へコピーする。
- gitlab ワーカーバス（opt-in・`executors/gitlab.py` プラグイン）。`--executor gitlab` /
  設定 `executor: gitlab` を選ぶと、各ワーカータスクを gitlab-idd スキルの `gl.py` で GitLab
  イシュー化して委譲し、リモートのワーカーが実装・レビュアーが承認した結果を `get-issue` で
  ポーリングする。`status:approved`（または `status:done` / クローズ）に達したらそのタスクを
  完了とみなす。ポーリング間隔・タイムアウト・付与ラベルは設定 `gitlab:` ブロックで調整可。
  既定の executor は `kiro` のままで、明示選択時のみ有効になる。
- 作業後に sparse-checkout クローンを自動削除（既定 ON）。各コマンド終了時に
  ノード専用クローンを丸ごと掃除しクローンの溜まり込みを防ぐ。`--keep-clone` /
  設定 `cleanup_clone: false` で従来どおり残して再利用も可能。
- 中間成果物のファイル参照プロトコル。`output`/`data` に乗らない大きな成果物は
  決定的なディレクトリ `runs/<run-id>/artifacts/<node-id>/` に書き出し、後続タスクは
  依存ノードの同じパスを読んで発見できる。ワーカーは生成した成果物を result に記録し、
  `result` コマンドでも一覧できる。

#### Fixed
- judge/評価役のサーキットブレーカー。同一系統の作り直し（verify=fail の再生成・
  失敗タスクの retry）が `--max-retries`（設定 `max_retries`, 既定 3）に達したら
  打ち切る。達成不可能な完了条件に対し無限に再タスクを積み続ける暴走を防ぐ
  （`--max-iterations` と二重ガード）。
- 依存タスクの成果物が大きいとき、kiro-cli へ渡すプロンプトが OS のコマンドライン長
  制限（ARG_MAX）に達して起動失敗する不具合を修正。一定サイズを超えるプロンプトは
  一時ファイルへ退避し参照渡しに切り替える（設定 `argv_limit` / `--argv-limit` で調整、既定 100000）。

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
