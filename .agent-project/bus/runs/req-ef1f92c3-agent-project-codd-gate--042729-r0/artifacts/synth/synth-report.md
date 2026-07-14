# synth 統合報告 — codd-gate 自動検出と regression/intake 結線

判定: **統合完了・適用先の再指定が必要**（コード側に不整合は無かった。gate の fail はワークスペース取り違えが原因）

## 1. 結論（先に要旨）

t2/t3/t4 の3成果物を突き合わせた結果、**import パス・コマンド字面・yaml インデントのいずれにも実際の不整合は無かった**。gate が fail と判定したのは、完了条件コマンドを `/Users/nitto/Workspace/sandbox-agent-state`（`agent-state` ブランチ、`.agent-project` のみの sparse checkout）で実行したためで、このワークツリーには `.agent/agent-project.yaml` も `tools/agent-project/` も**構造的に存在し得ない**（対象パスは `main` ブランチ側のツリーにあり、`agent-state` ブランチのツリーにそもそも含まれていない）。

統合パッチ（`codd-gate-integration.patch`）を現行 `main` HEAD（`f71dda19`）から作った使い捨て worktree に実際に `git apply` し、完了条件4コマンドを実行して **全て exit 0** を確認済み（§3）。

## 2. 統合した内容

| 成果物 | 採用可否 | 内容 |
|---|---|---|
| t2 | 採用（無差分） | `codd_gate_status.py` は無変更で仕様を満たすことを検証のみ。コード変更なし。 |
| t3 | 採用（無修正で統合） | `.agent/agent-project.yaml` + `_head.py` + `mr.py` + `model.py` の4ファイル、+42/-2行。 |
| t4 | 採用（無修正で統合） | `test_codd_gate_detect.py` に1テスト追加（+8行）。`test_codd_gate_routing.py` は無変更のため統合パッチに含めず。 |

t3 と t4 は編集対象ファイルが重複しないため、単純結合（`cat`）で1本のパッチに統合した（`codd-gate-integration.patch`、5ファイル分）。

## 3. 整合性チェック（タスクで名指しされた3観点）

いずれも独立検算し、**修正不要**と判断した根拠を記す。

### 3.1 import パス
- t3 は `_head.py` に `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` を追加し `from codd_gate_status import detect_status` / `from codd_gate_base import resolve_base_rev` をトップレベル import。
- `main` 側の実ファイルを読んで確認: `_head.py` は既に `import sys` / `from pathlib import Path` を保持済み（新規 import 不要）。`Path(__file__).resolve().parent.parent` は `_head.py`（`agent_project/` 直下）から2階層上＝`tools/agent-project/` — `codd_gate_status.py` の実配置と一致。
- `agent_project/__init__.py` の `_FRAGMENTS` 順序を確認: `"_head"` が先頭、`"model"` は2番目、`"mr"` は15番目。`_head` の exec で共有 globals に注入された `detect_status`/`resolve_base_rev` は、後続の `model`/`mr` フラグメントから素の名前で参照できる（衝突なし、順序も安全）。t1 が「未確認」として申し送った懸念点はこれで解消。

### 3.2 コマンド字面
- yaml: `regression_cmd: codd-gate verify --base "$KIRO_BASE_REV"` / `intake_cmd: codd-gate tasks --debt`。
- 判定ロジック: `mr.py` の `_codd_gate_regression_ready`/`_codd_gate_regression_env` は `"codd-gate" in cmd`、`model.py` は `"codd-gate" in cfg.intake_cmd` — いずれも yaml の実値に "codd-gate" 部分文字列が含まれることを前提にしており、実際に含まれる。既存の非 codd-gate コマンド例（`make -s smoke` 等）を誤検出しないことも確認済み（t3 報告 (b) の 11 件 pass）。

### 3.3 yaml インデント
- t3 追加分（`regression_cmd:`/`intake_cmd:`）はトップレベルキー（インデント0）。`main` の実ファイルを直接読み、`root:`/`agent_cli:`/`model:` 等の既存トップレベルキーと同じ位置に揃っていることを確認。完了条件の grep パターン（`^[[:space:]]*regression_cmd:...`）はインデント0個以上を許容するため、この点は元々どちらでも通るが、`Config` のフィールドとしてはトップレベルが正しい配置であり、t3 案をそのまま採用した。

## 4. 不整合の原因分析（gate 指摘への回答）

gate report（`artifacts/gate/verify-report.md`）は次の4コマンドがすべて exit≠0 だったと報告している。原因を実地で特定した:

- 作業ディレクトリ `/Users/nitto/Workspace/sandbox-agent-state` は `agent-state` ブランチの **sparse checkout**（`git sparse-checkout list` → `.agent-project` のみ）。
- `git ls-tree -r --name-only HEAD`（`agent-state` ブランチ）で確認すると `tools/agent-project/**` はブランチのツリー自体には存在するが、`.agent/agent-project.yaml` は **ブランチのツリーにすら存在しない**（`git show HEAD:.agent/agent-project.yaml` → `fatal: path does not exist`）。sparse-checkout の展開有無に関わらず、そもそも対象パスの実体は `main` ブランチ側のツリーにしかない。
- t2/t3/t4 は全員この点を認識しており（t1 メモ §1 で明記済み）、共有チェックアウト `/Users/nitto/Workspace/sandbox`（`main`、無関係な大規模差分あり）を汚さないよう `git_worktree.py provision --ref main` の使い捨て worktree でのみ検証していた。**gate だけが `agent-state` 側のワークツリーで完了条件を再実行しており、この run のタスク説明にある「実行対象ワークスペース」の解釈が t2/t3/t4 と food gate とで食い違っていた**。

したがって、コード自体に手戻りは無く、**次段（loop/適用タスク）が統合パッチをどこに当てて再検証するか**が唯一の残作業になる。

## 5. 独立検算（今回 synth で実施）

使い捨て worktree（`git worktree add --detach <tmp> main` → 検証後 `git worktree remove --force` で破棄。共有チェックアウト・sparse worktree のいずれも無変更）を用意し、`main` 現行 HEAD（`f71dda19`）に対して:

1. `git apply --check` → 成功（後述 §6 の2行ドリフトはヒットせず適用可能と確認）
2. `git apply codd-gate-integration.patch` → 成功
3. 完了条件4コマンドを実行 — **全て exit 0**:
   - `grep regression_cmd:.*codd-gate verify --base` → マッチ
   - `grep intake_cmd:.*codd-gate tasks` → マッチ
   - `PYTHONPATH=tools/agent-project python3 -c 'detect_status()...'` → `usable=True, command=[...codd-gate, verify, --base, HEAD]`
   - `pytest test_codd_gate_detect.py test_codd_gate_routing.py` → **30 passed**
4. 追加で `pytest test_agent_project.py -k "Intake or regression"` → **11 passed**（t3 報告と一致、regression 未検出）

## 6. main の drift について（軽微・ブロッキングではない）

t3/t4 が worktree を取得した時点から、`main` の `.agent/agent-project.yaml` に **無関係な2行の用語リネーム**（`kiro-flow` → `agent-flow`、コメント内のみ）が追加でコミットされていた（`flow_planner`/`route_planner` 近傍のコメント行、17行目・25行目）。統合パッチの hunk（31-33行目付近）とは離れているため `git apply` は3-way ではなく通常の context マッチでそのまま成功した（§5 で実証済み）。実害なし、報告のみ。

## 7. 次段（loop/適用タスク）への申し送り事項

1. **適用先**: `main` ブランチから新規に取得した専用 worktree（`git_worktree.py provision --ref main` 等、shared checkout `/Users/nitto/Workspace/sandbox` を直接汚さない）に `codd-gate-integration.patch` を適用し、コミットする。
2. **再検証**: 完了条件4コマンドは、パッチ適用後の **同一ワークスペース**（`main` 系統。`agent-state` の sparse checkout ではない）で実行すること。
3. **範囲外（t1/t3/t4 が既に明記済み、今回も未着手のまま）**:
   - `codd_gate_detect.py`/`codd_gate_status.py`/`codd_gate_base.py` の docstring 行番号の陳腐化（パッケージ分割前の行番号を参照）
   - `run_intake` の `subprocess.run(shell=True, ...)` を `codd_gate_status.command()` 由来の argv・`codd_gate_debt.parse_debt_output()` 経由のパースに置き換える拡張
   - `--repos`/`--repo-dir`（`codd_gate_routing.build_routing_args`）の自動付与
   - `agent_project/doctor.py` への codd-gate 検出状態の合流（`agent-project doctor` からの可視化）
   - `TestDaemonRouting.test_kf_base_passes_flow_config` の flaky failure（macOS `/tmp` vs `/private/tmp` symlink 差分、本件の変更ファイルとは無関係と確認済み）

## 8. 成果物

- `codd-gate-integration.patch` — t3（4ファイル）+ t4（1ファイル）を統合した単一の git 差分（5ファイル、+50/-2行）。`main` HEAD `f71dda19` に対して `git apply` 可能であることを実地検証済み。
