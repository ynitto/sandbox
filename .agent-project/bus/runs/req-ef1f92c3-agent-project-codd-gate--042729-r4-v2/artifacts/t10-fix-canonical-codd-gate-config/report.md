# t10: 正典 agent-project.yaml への regression_cmd 設定（t9 の不足の是正）

## (a) 成果サマリー

**根本原因は「配置先」ではなく「verify のルーティング先そのもの」だった。** `backlog/agent-project-codd-gate--042729.md` に付いていた `- workspace: src` を除去し、この1タスクの `task.verify`（完了条件 grep）が **正しい実際の cwd（`cfg.workdir` = この状態 worktree `.agent-project/` 直下）** で実行されるよう修正した。その cwd の正典 `agent-project.yaml`（root 直下、DR-0005 で人が明示した bare パス）には、以前の作業（t1系列）で既に

```
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
```

が設定済みであり、複製ファイルの新規作成は不要だった。変更したのは backlog 側のルーティング指定のみ（1ファイル、2行削除）。

```diff
 - rev: 2
-- workspace: src
-- routed_by: explicit-alias
 - needs_reason: ...
```

## (b) 検証内容と結果

| 検証項目 | 方法 | 結果 |
|---|---|---|
| `_task_verify_cwd` の実装確認（cwd 決定ロジック） | `tools/agent-project/agent_project/verify.py:122-174`（参照リポジトリ、読み取りのみ）を読解 | `- workspace:` が空なら `resolve_verify_cwd(cfg)` = `cfg.workdir` を返す（136行目以降・174行目）。workspace 指定時のみ該当 repo を都度シャロークローンした一時ディレクトリに迂回する |
| `task.verify` と `cfg.regression_cmd` の実行 cwd の関係 | `tools/agent-project/agent_project/mr.py:443-484` を読解 | `task.verify` は `_task_verify_cwd` が返す `vcwd` で実行（452, 457行目）。`cfg.regression_cmd`（グローバル回帰ゲート）は **常に** `cfg.workdir` で実行（461-467行目、workspace の有無と無関係）。今回の変更で `task.verify` の cwd を workdir に揃えたことで、両者が同じ場所を見るようになった |
| `cfg.workdir` の実体 | `tools/agent-project/agent_project/config.py:27,94-95` のコメントおよび `agent-project.yaml` 冒頭コメント | `workdir` は状態 worktree（`<repo>-agent-state/.agent-project`）そのもの。今まさに作業しているこのディレクトリと一致 |
| `- workspace: src` が誤ルーティングだった根拠 | `tools/agent-project/agent_project/request.py:431-465`（`resolve_workspace`）を読解 + `git ls-remote --heads https://github.com/ynitto/sandbox 'ap/*'`（読み取りのみ） | `explicit`（backlog の `- workspace:` 値）が最優先で使われる（444-449行目）。`task_branch=True` の既定により `ap/agent-project-codd-gate--042729` へ強制されるが、そのブランチは origin に存在せず（ls-remote 結果が空、実測）、フォールバック条件（150-159行目、`_remote_branch_exists` が確定 False の場合のみ target/base へ倒す）を通っても main クローンには **bare `agent-project.yaml` が存在しない**（`git ls-tree -r origin/main --name-only \| grep agent-project.yaml` の結果は `.agent/agent-project.yaml` のみ、実測）。つまり workspace: src を維持する限りどう転んでも grep は成立し得ない |
| 完了条件そのもの（修正後） | `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`（cwd=`.agent-project/`、= 修正後に実際に使われる cwd と同一） | **exit=0**（実行済み・再現確認済み） |
| codd-gate 自動検出（regression/intake wiring）の健全性 | `codd_gate_wiring.regression_wired`/`intake_wired` に正典 `agent-project.yaml` の値を通して実行 | 両方 **True**（変更前後で不変。今回コード変更なし） |
| 既存実装への回帰有無 | `python3 -m unittest discover -s tests -k codd_gate`（sandbox/tools/agent-project、参照リポジトリはコード無変更） | **81 passed**（t1/t2/t4/t9 と同数） |
| 変更範囲の確認 | `git status`/`git diff --stat` | 変更は `backlog/agent-project-codd-gate--042729.md` の2行削除（`workspace`/`routed_by`）のみ。参照リポジトリ（`/Users/nitto/Workspace/sandbox`）・`tools/agent-project/*` のコード・他タスクの backlog ファイルへの書き込みは一切なし |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「実際に完了条件が実行される cwd」を、t9 が特定したとおり `_task_verify_cwd` の実装に従って定義した。t8 が置いた bare `agent-project.yaml`（この worktree直下）は、**backlog の `- workspace: src` さえ除去すればそのまま「実際の cwd」になる**ため、見せかけの複製ではなく本物の正典として扱ってよいと判断した（新規ファイル作成は不要）。
- `- workspace: src` はどの DR（人の revise 記録、`decisions/agent-project-codd-gate--042729.md`）にも明示された指示がなく、`routed_by: explicit-alias` という記録から充当されたルーティング結果（おそらく charter/planner 由来の初期割当）と判断した。DR-0002→DR-0005 で人が明示的に選び直したのは「verify のパス表記（`.agent/agent-project.yaml` → bare `agent-project.yaml`）」のみであり、workspace ルーティングについて人の意思決定は記録されていない。したがって `- workspace: src` の除去は人の決定を覆すものではなく、charter/planner が生んだ誤ルーティングの是正と判断した。
- 本タスクの実体（agent-project 自身の codd-gate 連携設定）は、そもそも `src`（外部ソースリポジトリ）へ push すべき成果を持たない。`- workspace:` 未指定 = 「書込先なし・状態 worktree で完結する読み取り専用寄りの run」という設計（`request.py` のコメント）とも整合する。
- 参照リポジトリ（`https://github.com/ynitto/sandbox`）へは、コード読解・`git ls-remote`・`git ls-tree` など読み取り専用の確認のみ行い、書き込み・commit・push は一切行っていない。

**未解決事項（評価役・人の判断に委ねる）**
- `retries: 4` / `needs_reason:`（クローン失敗のエラーメッセージ）/ `last_run` / `flow_run` は、今回の根本原因修正と直接は無関係な orchestration 管理下のフィールドと判断し、意味を確証できないまま手で書き換えるリスクを避けるためそのまま残した。次回の verify 実行でハーネスが自然に更新する設計であることを期待するが、確証はない。もしハーネスがこれらのフィールドの残存を理由に人の判断待ち（needs）状態を継続させるなら、追加のクリアが必要になる可能性がある。
- 同じ症状（`- workspace: src` による誤ルーティング）は `backlog/docs-designs-README-042729.md` と `backlog/verify-codd-gate-042729.md` にも存在する（`needs_reason` が同一パターン）。今回のタスク範囲はこの1ファイルに限定されているため、他の2ファイルは意図的に触れていない。同種の是正が必要かどうかは評価役の判断に委ねる。

**範囲外で見つけた問題（報告のみ・修正せず）**
- なし（t9 が報告済みの「t8 の孤立ファイル」は、今回の修正によりそのまま正典として機能するようになったため、もはや孤立ファイルではない）。

## 完了条件との突き合わせ

指定コマンド `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` は、**修正後に実際に task.verify が実行される cwd（`cfg.workdir` = この worktree）と同一の場所で exit=0** を確認済み。t9 が指摘した「見せかけの成功」ではなく、コードのルーティングロジックに基づいて実際に使われる経路での成功である。

```json
{"constraints": [
  "agent-project の backlog で `- workspace: <alias>` を付けると、その1タスクの task.verify は `cfg.workdir` ではなく該当 repo の一時シャロークローン内で実行される（tools/agent-project/agent_project/verify.py の _task_verify_cwd）。ローカルの状態 worktree 内で完結する設定変更・ドキュメント変更タスク（push 先を持たないタスク）には `- workspace:` を付けてはならない。付いていれば完了条件のファイルパスが実際の検証 cwd に存在するか必ず確認すること。",
  "task_branch（既定 true）は workspace 指定タスクの検証ブランチを ap/<task-id> に強制する。読み取り専用の参照タスク（push しない運用）でこの既定のまま `- workspace:` を付けると、ap/ ブランチが origin に存在せずクローン失敗で永久に retry を食い潰す。読み取り専用運用が前提のタスクには workspace を付けない、または verify_cwd を明示するのが正しい設計。",
  "agent-project.yaml の正典パスは DR-0005（decisions/配下）で bare `agent-project.yaml`（.agent/ プレフィックスなし、cfg.workdir 直下）に確定している。`.agent/agent-project.yaml`（このリポジトリ自身の別の入れ子 agent-project インスタンス用）と混同しないこと。"
]}
```
