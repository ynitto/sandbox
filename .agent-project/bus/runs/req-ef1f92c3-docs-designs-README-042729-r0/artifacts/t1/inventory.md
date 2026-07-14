# docs/designs 棚卸し — エンジン中核カテゴリ

対象リポジトリ: `/Users/nitto/Workspace/sandbox`（charter が指す実装リポジトリ。本 worktree
`sandbox-agent-state` 上には `docs/designs` が存在しないため、`ls` で実在確認したうえで参照読みした）

## 対象ファイル一覧

| ファイル名 | 要旨（1〜2文） | 対象読者 | 現行/歴史的 |
|---|---|---|---|
| `agent-project-design.md` | 単一プロジェクトのバックログを自律的に優先順位付け・実行・検証・収束させる制御層 agent-project の唯一の設計正典（統合版）。3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を地図として示す。 | agent-project の実装者・charter/backlog を書く運用者 | 現行（旧 `kiro-project` から改称移行済み、旧設計は削除済み） |
| `agent-flow-design.md` | git 共有バス（ローカル dir／共有 git repo）上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤 agent-flow の設計書。orchestrator/worker のオンデマンド起動・LLM 実行の切替（kiro-cli既定／Claude Code／stub）を規定する。 | agent-flow の実装者・分散実行環境の運用者 | 現行（旧 `kiro-flow` から改称移行済み、旧設計は削除済み） |
| `codd-gate-design.md` | ドキュメント・コード・テストの一貫性を「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツール codd-gate の唯一の設計正典。agent-project には依存しない独立ツールで、結合点は `schemas/` の共通データ契約のみと明記される。 | codd-gate の実装者・agent-project との連携設計を確認する開発者 | 現行 |
| `agent-tools-rename-design.md` | 旧 `kiro-*` 系統（kiro-project/kiro-flow/kiro-projects-viewer/kiro-loop）を `agent-*`（agent-project/agent-flow/agent-dashboard/agent-loop）へクローン移行・改称する方針と新旧名称対応表を定めた設計書。移行完了後に旧系統を削除する計画を含む。 | 移行作業を行う実装者・名称/パス規約を確認する開発者 | 現行（agent-project/agent-flow/agent-dashboard は移行完了、`kiro-loop` は未移行のまま残置） |

## 補足（範囲外で見つけた事実）

- **agent-dashboard（フロントエンド）の設計書は `docs/designs/` 配下に存在しない。** charter は
  agent-dashboard をフロントエンドと定めているが、実体の UI 設計ドキュメントは
  `docs/plans/2026-07-14-agent-dashboard-*-design.md`（overview-first-ui / detail-tabs-ui /
  doctor / verify-revise）および `docs/plans/2026-07-15-agent-dashboard-sidebar-ai-consultation-design.md`
  に分散している。`docs/designs/agent-tools-rename-design.md` が改称方針として
  agent-dashboard に言及するのみで、`docs/designs` 単体の主要設計ファイルは無い。
  README の導線を作る後続タスクはこの点を踏まえ、`docs/plans` 側へのリンクも検討する必要がある。
- `agent-flow-retry-inheritance-design.md`（`docs/designs/` に実在）は agent-flow の派生・詳細設計
  （リトライ継承の挙動）であり、本棚卸しでは「エンジン中核の主要設計」本体ではなく衛星ドキュメントと
  判断して表から除外した。必要なら README の agent-flow 項目からのリンク候補になる。
- `docs/designs/` にはこの他 agent-loop 系・kiro-loop 系・ltm-use 系・gitlab 連携系など多数の
  設計書があるが、charter が定めるエンジン（agent-project/agent-flow）＋フロントエンド
  （agent-dashboard）の範囲外のため対象外とした。

## 検証

- 完了条件として与えられた `test -f docs/designs/README.md && grep ...` は本タスク（t1: 棚卸し表の
  作成）ではなく、この run 全体（docs/designs/README.md 自体の作成）に対する検証コマンドと判断した。
  本タスクの担当は上記表の作成のみであり、README.md の作成・配置は後続タスク（docs/synth 系）の
  責務とみなし、ここでは作成していない。
- 対象ファイルの実在は `ls -la /Users/nitto/Workspace/sandbox/docs/designs/` で確認済み（推測でファイル名を
  書いていない）。4ファイルとも存在を確認：`agent-project-design.md` / `agent-flow-design.md` /
  `codd-gate-design.md` / `agent-tools-rename-design.md`。
- 各ファイルの要旨・対象読者は各ファイル冒頭（ヘッダ・概要節）を実際に読んで記述した。

## 採用した前提

- 本 worktree（`sandbox-agent-state`）には `docs/designs` が存在しないため、charter が指す実装
  リポジトリ `/Users/nitto/Workspace/sandbox`（同一 git リポジトリの `main` worktree）の
  `docs/designs/` を参照読み専用で確認した。書き込みは行っていない。
- 「エンジン中核」の範囲は charter の `constraints`（agent-project/agent-flow をエンジン、
  agent-dashboard をフロントエンドとして使用）と、この run の完了条件が名指す4ファイルを基準に、
  agent-project-design.md / agent-flow-design.md / codd-gate-design.md / agent-tools-rename-design.md
  の4件とした。
