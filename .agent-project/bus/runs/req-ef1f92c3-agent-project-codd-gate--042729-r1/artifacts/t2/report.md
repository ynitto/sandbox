# t2: codd_gate_status.py 実装 — 候補

**切り口**: 新規実装ではなく「既存実装の正当性を git オブジェクトから直接検証する」アプローチ。
作業 worktree（`sandbox-agent-state/.agent-project`）は sparse-checkout で `.agent-project/` のみに限定されており
`tools/` 配下は物理的に存在しないため、ファイル編集ではなく `git show` によるコード抽出と隔離サンドボックスでの
実行検証を行った。共有チェックアウトへの書き込みは一切行っていない。

## (a) 成果 / サマリー

`tools/agent-project/codd_gate_status.py` は現在のブランチ `agent-state`（HEAD `a74c3a62`）に
**前ラウンド（r0）から実装済み**であり、t2 のゴール文（PYTHONPATH assert 一発）をそのまま満たす。
このタスクで新たに書いたコードはない — 既存実装がゴールを満たすことを確認し、その根拠を報告する。

- `detect_status()`（`codd_gate_status.py`）は `codd_gate_detect.resolve_codd_gate()` を呼び、
  結果を `try/except Exception` で包んで例外を外に漏らさない → 未検出環境でも `usable=False` を返す no-op 縮退。
- `resolve_codd_gate()`（`codd_gate_detect.py`）の解決順は `explicit → PATH（shutil.which）→ 同梱パス
  （`tools/codd-gate/codd-gate.py`）`。見つからなければ `None`。
- `CoddGateStatus.command(*args)` は `usable` のときのみ `[*binary, *args]` を返し、そうでなければ `None`
  （呼び出し側の分岐を `if status.command(...):` の1行に落とせる設計、t1 契約と整合）。

## (b) 検証内容と結果

worktree に `tools/` が存在しないため、`git show HEAD:tools/agent-project/{codd_gate_status.py,codd_gate_detect.py}`
で2ファイルを一時ディレクトリ（リポジトリ外）へ抽出し、ゴール文記載の assert コマンドをそのまま実行した。

```
$ PYTHONPATH=$TMPDIR python3 -c 'from codd_gate_status import detect_status; s=detect_status(); \
  assert s.usable and s.command("verify", "--base", "HEAD"); print("OK", s.binary, s.command("verify","--base","HEAD"))'
OK ['/Users/nitto/.local/bin/codd-gate'] ['/Users/nitto/.local/bin/codd-gate', 'verify', '--base', 'HEAD']
exit=0
```

この環境には `codd-gate` が PATH（`/Users/nitto/.local/bin/codd-gate`）にインストール済みのため、
tier(1) の PATH 検出だけで `usable=True` に到達する。tier(2)（同梱パス）・未検出時の `usable=False` no-op 縮退は、
コード読解（`detect_status` の `try/except`、`resolve_codd_gate` の `which(...) is None` 分岐）と、
t1 報告にある既存テスト（`test_codd_gate_detect.py` / `test_codd_gate_routing.py`、29件 PASS）で担保されている。
本タスクの worktree では pytest 実行環境（`tools/` 配下）にアクセスできないため re-run はしていない
（t1 も同じ理由でこの2ファイルには触れていない）。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**前提**: t2 のゴール文が定める唯一の機械的完了条件は `PYTHONPATH=tools/agent-project python3 -c '...assert...'`
の exit 0 のみであり、`.agent/agent-project.yaml` への `regression_cmd`/`intake_cmd` 追記（t3 担当）とは独立に
評価できる。t1 契約・t3/t4 のタスク定義もこの切り分けと一致している。

**表現上の齟齬（未解決・範囲外・評価役判断待ち）**:
ゴール文は "(1) PATH (2) リポジトリ内スクリプト (3) スキル配置ディレクトリ" と3段のフォールバックを謳うが、
実装（および前ラウンドで書かれたそのテスト・docstring）は "explicit → PATH → 同梱パス（`tools/codd-gate/codd-gate.py`）"
の2段構成で、「リポジトリ内スクリプト」と「スキル配置ディレクトリ」を単一の「同梱パス」に統合する設計判断を
既に下している。実際 `~/.claude/skills/codd-gate/` には `SKILL.md` のみでスクリプトが存在せず
（codd-gate は pip/local-bin 型 CLI として配布されている）、独立した3段目を実装しても本環境では到達不能。
この統合はコード・テスト・ドキュメントの3点で一貫しており、機械的完了条件も満たすため、
このラウンドでは追加実装をせず**現状維持を推奨**する。厳密な3段化が必要と判断されるなら、
別タスクとして「スキル配置ディレクトリの実在パス規約」を先に定義すべき（このタスクの権限・情報だけでは
規約を捏造できない）。

**未実施**: `.agent/agent-project.yaml` の `regression_cmd`/`intake_cmd` 追記、テストの実 pytest 実行
（いずれも t3/t4 の担当、worktree のスコープ外）。
