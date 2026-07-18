## codd-gate-163827: codd-gate 連携の目標境界を設計書に固定する
- status: done
- source: charter
- priority: 0
- verify: `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md`
- retries: 0
- workspace: agent-project
- refs: skills
- why: 『パッケージは汎用フックのみ・codd_gate_* は sibling 任意部品』を実装前に文書で合意しないと、整理の完了判定と dashboard の見せ方がぶれるため。
- out_of_scope: agent_project / dashboard の実装変更やテスト改修
- hints: ドキュメントは slop-police スキルで整える。正典は docs/designs/codd-gate-design.md §4（差し込み点 E1–E3）と §4.1（自動検出レイヤ）。受入の `! git grep ... _apply_codd_gate|_codd_gate|import codd_gate` を設計上の完了条件として明記し、永続化は `codd_gate_regression.py`・有効化は yaml/CLI のみ、と境界を書く。tools/agent-project/README.md の一貫性ゲート節も同じ境界に揃える。
- charter: v1
- assess: c=2 r=1 a=1
- last_run: req-ef1f92c3-codd-gate-163827-r0
- needs_reason: 回帰検知: グローバル検査 `codd-gate verify --base "$KIRO_BASE_REV" --repos ./repos.json` 失敗 — exit=2 失敗した工程: `codd-gate verify --base 350d6121de099dc880cb7b0e138271d57451aa6e --repos ./repos.json` [codd-gate] エラー: スキャン可能な repo がありません（--repo-dir <name>=<dir> か --sync を指定）
- archived: 2026-07-18 21:29:56

## 納品書
- 完了 : 2026-07-18 21:29:56
- verify: `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md` → PASS（承認: 検証失敗を確認・受容して完了）
- 成果 : 
