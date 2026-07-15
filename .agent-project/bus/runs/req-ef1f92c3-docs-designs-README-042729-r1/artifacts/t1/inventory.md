# docs/designs 棚卸し — 主要4設計の実在・配置・役割・相互関係

対象リポジトリ: `/Users/nitto/Workspace/sandbox`（charter が指す実装リポジトリ）。
本 worktree `sandbox-agent-state/.agent-project` には `docs/`（`docs/designs` を含む）が
存在しないため、実装リポジトリ側を `ls` / `grep` で参照読み専用で確認した（書き込みなし）。

## docs/designs ディレクトリの有無

- 本 worktree（`.agent-project`）: **存在しない**（`docs/` 自体が無い）。
- 実装リポジトリ `/Users/nitto/Workspace/sandbox`: **存在する**。`docs/designs/` 配下に
  ファイル25件（`README.md` を含む）。

## 主要4設計の実在・配置

| ファイル | 配置 | 行数 |
|---|---|---|
| `agent-project-design.md` | `docs/designs/agent-project-design.md` | 1016行 |
| `agent-flow-design.md` | `docs/designs/agent-flow-design.md` | 866行 |
| `codd-gate-design.md` | `docs/designs/codd-gate-design.md` | 301行 |
| `agent-tools-rename-design.md` | `docs/designs/agent-tools-rename-design.md` | 85行 |

4ファイルとも実在を確認済み（推測なし、`ls -la` と各ファイル冒頭の読み取りで確認）。

## 各設計の役割

- **`agent-project-design.md`**（最終更新 2026-07-14）: 単一プロジェクトのバックログを自律的に
  優先順位付け・実行・検証・収束させる制御層 `agent-project` の唯一の設計正典（統合版）。
  3層2ループ構成（project 上位ループ／run 正準ループ／agent-flow 実行層）を地図として示す。
  旧 `kiro-project` から改称移行済み、旧設計は削除済み。
- **`agent-flow-design.md`**（作成 2026-06-13／改称クローン日 2026-07-14）: git 共有バス
  （ローカル dir／共有 git repo）上でタスクグラフを動的生成し複数ワーカーへ分散実行する
  Dynamic Workflow 基盤 `agent-flow` の設計書。orchestrator/worker のオンデマンド起動・
  LLM 実行の切替（kiro-cli既定／Claude Code／stub）を規定する。旧 `kiro-flow` から改称移行済み。
- **`codd-gate-design.md`**（最終更新 2026-07-02）: ドキュメント・コード・テストの一貫性を
  「受け入れ前ゲート」と「負債棚卸し→タスク化」で維持する決定的ツール `codd-gate` の唯一の
  設計正典。**agent-project には依存しない独立ツール**（依存は python3 と git のみ）。
- **`agent-tools-rename-design.md`**（作成 2026-07-14）: 旧 `kiro-*` 系統
  （kiro-project/kiro-flow/kiro-projects-viewer/kiro-loop）を `agent-*`
  （agent-project/agent-flow/agent-dashboard/agent-loop）へクローン移行・改称する方針と
  新旧名称対応表を定めた設計書。agent-project/agent-flow/agent-dashboard の移行は完了、
  `kiro-loop → agent-loop` の移行のみ未了。

## 相互関係（クロス参照の実測）

- `agent-project-design.md` ⇄ `agent-flow-design.md`: 相互に参照し合う。agent-project が
  実行を agent-flow へ委譲する関係（`agent-` 接頭辞は「実行を agent-flow へ委譲すること」を
  表すと agent-project-design.md 本文に明記）。
- `codd-gate-design.md` → `agent-project-design.md`: codd-gate 側から参照。ただし依存の向きは
  「codd-gate は agent-project を読まない・双方が相手の実装を知らない」設計で、結合点は
  `schemas/` の共通データ契約（repos/task）のみと codd-gate-design.md 本文に明記。
  agent-project-design.md からも codd-gate-design.md を参照（相互リンクだが依存は非対称）。
- `agent-tools-rename-design.md` は他5設計（`agent-loop-*` 系4件、`agent-dashboard-feature-split-design.md`、
  `agent-cli-plugin-design.md`）から改称方針の根拠として広く参照されている。主要4設計の中では
  唯一「他の主要3設計を直接参照しない」（対象は kiro-* 系・agent-loop 系の移行方針が主）。

## 範囲外で見つけた事実（本タスクの範囲外・報告のみ）

- **`docs/designs/README.md` は既に実装リポジトリ側に存在し、主要4設計すべてへのリンクを
  含んでいる**（`## まず読むもの — 主要4設計` 節に4ファイルとも掲載済み、確認済み）。
  本 run の完了条件（`test -f docs/designs/README.md && grep -q ...` の4件）は、実装リポジトリ
  `/Users/nitto/Workspace/sandbox` を対象にすれば**既に満たされている**可能性が高い。ただし
  本 worktree（`.agent-project`）には `docs/designs` 自体が存在せず、同じコマンドをこの
  worktree で実行すると失敗する。README.md の作成・配置・置き場所（どちらのリポジトリ/
  worktree を正とするか）は本タスク（t1: 棚卸し）の範囲外であり、synth 系タスクの責務と判断
  し、ここでは作成・変更を行っていない。
- `agent-dashboard`（フロントエンド）の設計書は `docs/designs/` 配下に単体で存在しない。
  `docs/plans/2026-07-14-agent-dashboard-*-design.md` 等に分散しているが、`docs/designs/`
  内には制御面分離の正典 `agent-dashboard-feature-split-design.md` がある。README 導線を作る
  後続タスクはこの点を踏まえる必要がある（前回 r0 の棚卸しと同じ所見、今回も再確認済み）。
- `agent-flow-retry-inheritance-design.md` は agent-flow の派生・詳細設計（リトライ継承）で
  あり、主要4設計本体ではなく衛星ドキュメントと判断し表から除外した。
- `docs/designs/` にはこの他 agent-loop系・kiro-loop系・ltm-use系・gitlab連携系など多数の
  設計書があるが、charter が定めるエンジン（agent-project/agent-flow）＋フロントエンド
  （agent-dashboard）＋ゲート（codd-gate）＋改称方針（agent-tools-rename）の範囲外のため対象外。

## 検証

- 4ファイルの実在: `ls -la /Users/nitto/Workspace/sandbox/docs/designs/` で確認（実行結果に
  25エントリ、対象4ファイルすべて含まれることを目視確認）。
- 各ファイルの役割・由来: 各ファイル冒頭（ヘッダ・概要節、`sed -n '1,25p'`）を実際に読んで
  記述。推測での要約はしていない。
- 相互関係: `grep -rl "<ファイル名>" docs/designs/` で被参照ファイルを実測し記載。
- 本 worktree に `docs/designs` が無いことは `ls docs/` の失敗（`No such file or directory`）
  で確認済み。

## 採用した前提

- 「主要4設計」の範囲は、この run の完了条件が名指す4ファイル
  （agent-project-design.md / agent-flow-design.md / codd-gate-design.md /
  agent-tools-rename-design.md）とした。
- 実在確認・内容読み取りの対象は charter が指す実装リポジトリ `/Users/nitto/Workspace/sandbox`
  とした（本 worktree には該当ディレクトリが無いため）。読み取り専用でアクセスし、書き込みは
  行っていない。
- 本タスク（t1）の担当は棚卸し（実在・配置・役割・相互関係の確認と報告）のみとし、
  `docs/designs/README.md` の作成・編集は行っていない（既出の通り、実装リポジトリ側には
  既に存在することを確認済みだが、その扱いの判断は本タスクの範囲外）。
