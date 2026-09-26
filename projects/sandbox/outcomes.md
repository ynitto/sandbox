# 成果・教訓

## agent_project-codd_gate-163827: agent_project を codd_gate 非依存の汎用フックへ整理する

- 検証: `PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestIntake.test_run_intake_enqueues_and_dedups_by_id TestLoopEngineering.test_regression_gate_blocks_on_failure TestLoopEngineering.test_regression_gate_passes && ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' -- tools/agent-project/agent_project`
- 背景: 設計の『本体は無改造・差し込み点のみ』をコードで真にし、受入の grep 条件と intake/regression 回帰テストを同時に満たすため。
- 対象外: dashboard UI・設計書の文章だけの推敲・codd-gate 本体（tools/codd-gate）の仕様変更
- 補足: 除去/改名対象: configfile._apply_codd_gate_auto_wiring、doctor._codd_gate_wiring_module / doctor_codd_gate_findings、model._codd_gate_debt_module と `import codd_gate_*`。intake は schemas/task 相当の汎用 JSON パース＋ id 冪等のまま維持。自動配線は sibling（codd_gate_wiring / codd_gate_regression）か設定明示に寄せ、パッケージ内に codd_gate 名を残さない。TestCoddGateAutoWiring など `_codd_gate_wiring_module` を mock するテストも新境界へ追随。完了後は agent-reviewer で境界レビュー。
- charter: v1
- assess: c=2 r=2 a=1
- last_run: req-48d24769-agent_project-codd_gate-163827-r0
- archived: 2026-07-26 09:04:10

#### 納品書
- 完了 : 2026-07-26 09:04:10
- 検証: `PYTHONPATH=tools/agent-project python3 tools/agent-project/tests/test_agent_project.py TestIntake.test_run_intake_enqueues_and_dedups_by_id TestLoopEngineering.test_regression_gate_blocks_on_failure TestLoopEngineering.test_regression_gate_passes && ! git grep -n -E '(^|[[:space:]])(import|from)[[:space:]]+codd_gate|_apply_codd_gate|_codd_gate' -- tools/agent-project/agent_project` → PASS（exit=0 --- 通知（要対応）--- # 要対応（agent-project）  ## 判断待ち（blocked） - T1: x     なぜ: 回帰検知: グローバル検査 `external-regression-hook` 失敗 — hook failed     対応: needs/T1.md に方針を書く、または `approve T1` / `hold T1`  ... ----）
- 成果 : commit 48d24769

#### 判断材料（成果物の所在・差分・検証）
- 成果物: commit 48d24769
- 所在: /Users/nitto/Workspace/sandbox-project / ブランチ main

出典: archive/agent_project-codd_gate-163827.md

---

## codd-gate-163827: codd-gate 連携の目標境界を設計書に固定する

- 検証: `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md`
- refs: skills
- 背景: 『パッケージは汎用フックのみ・codd_gate_* は sibling 任意部品』を実装前に文書で合意しないと、整理の完了判定と dashboard の見せ方がぶれるため。
- 対象外: agent_project / dashboard の実装変更やテスト改修
- 補足: ドキュメントは slop-police スキルで整える。正典は docs/designs/codd-gate-design.md §4（差し込み点 E1–E3）と §4.1（自動検出レイヤ）。受入の `! git grep ... _apply_codd_gate|_codd_gate|import codd_gate` を設計上の完了条件として明記し、永続化は `codd_gate_regression.py`・有効化は yaml/CLI のみ、と境界を書く。tools/agent-project/README.md の一貫性ゲート節も同じ境界に揃える。
- charter: v1
- assess: c=2 r=1 a=1
- feedback: 成果物ブランチをrebaseして
- last_run: req-48d24769-codd-gate-163827-r1
- archived: 2026-07-24 06:09:09

#### 納品書
- 完了 : 2026-07-24 06:09:09
- 検証: `grep -nE 'agent_project.*(import|結合|依存).*(しない|外|禁止)|パッケージ.*(codd_gate|sibling)|有効化は設定' tools/agent-project/README.md && grep -nE 'regression_cmd|intake_cmd|codd_gate_\*\.py|自動検出' tools/agent-project/README.md && test -f docs/designs/codd-gate-design.md && grep -nE 'agent_project パッケージ|_apply_codd_gate|sibling|汎用フック' docs/designs/codd-gate-design.md` → PASS（exit=0 _auto_wiring`）はパッケージ内に 367:しか現れず、sibling の `codd_gate_*.py` や tests には出ない（それらは `resolve_codd_gate` などを正当に持つ）。 377:パスを `tools/agent-project` 全体へ広げると、sibling の `resolve_codd_gate` や tests の `impo）
- 成果 : commit 48d24769

#### 判断材料（成果物の所在・差分・検証）
- 成果物: commit 48d24769
- 所在: /Users/nitto/Workspace/sandbox-project / ブランチ main

#### run ブリーフ（この試行群で確定した制約・教訓。learn 射影済み）
- 成果物ブランチをrebaseして

出典: archive/codd-gate-163827.md

---

## dashboard-163827: dashboard で一貫性ゲートの状態把握と有効化を支援する

- 検証: `echo "done"`
- refs: agent-project
- 背景: パッケージ内マジック配線を外した後も、人が regression/intake の有無とゲート失敗の意味を画面から判断・対処できるようにするため。
- 対象外: agent-project 本体のフック実装・done 不変条件を破る UI からの状態書換
- 補足: 概要またはプロジェクト情報に regression_cmd/intake_cmd（設定の有無）を可視化し、未結線時は README と同じ有効化導線（設定編集／sibling CLI）を示す。needs の codd-gate / 回帰失敗要約（needs-diagnosis）の可読性を落とさない。公式契約（needs/inbox/commands）以外へ書かない。実装後は agent-reviewer で UX レビュー。
- charter: v1
- assess: c=2 r=1 a=1
- rev: 2
- edited: human
- needs_reason: 繰り返し NG（retries=18）: agent-flow run タイムアウト（1800.0s）
- last_run: req-48d24769-dashboard-163827-r15-v2
- verification: {"pass": 2, "fail": 0, "unverifiable": 0, "report": "verifications/dashboard-163827/016a4bde9bf90b57d6cdc35571fcb17674079ab9.md", "receipt": true, "plan_digest": "sha256:146cdf038e5db43cfbc5b1b47abe53a3d7b205e1e7093a22b6ee54c96f1304bc"}
- needs_dr: DR-0018
- archived: 2026-08-02 04:49:21

#### 納品書
- 完了 : 2026-08-02 04:49:21
- 検証: `echo "done"` → PASS（基準 2 件中 2 件 pass（agent-flow runner の receipt を検算して採用））
- 成果 : commit 48d24769

#### 判断材料（成果物の所在・差分・検証）
- 成果物: commit 48d24769
- 所在: /Users/nitto/Workspace/sandbox-project / ブランチ main

#### run ブリーフ（この試行群で確定した制約・教訓。learn 射影済み）
- コンフリクトを解消して
- 現状のmainブランチが大幅に変わっているためrebaseして再度作業する。
- マージ先の main とコンフリクトしているため最新をpullして解消して
- コンフリクトを解消する
- 最新 main を統合し、6ファイルの競合を解消して全検証をやり直す。main由来のMarkdown末尾空白は競合として扱わない。
- 競合解決済み commit 59ccf49e を起点に、旧 run の done ノードを継承せず新規計画で全検証する。work の terminal ok:false は失敗として扱う。

出典: archive/dashboard-163827.md

---

## document-msbqisx2-5: 共有禁止項目（redaction）を契約テスト化する

- 受入基準: プライバシー用 fixture を用いた redaction テストが追加されている
- 受入基準: テストにより token/ホームパス/生プロンプトが共有ファイルに現れないことが自動検出される
- 受入基準: redaction 失敗時に CI が失敗するようになっている
- 背景: Phase0 の必須安全網。共有前検査を自動化し漏出リスクを阻止する。
- 内容: token / ホームディレクトリ / 生プロンプト / 生の資格情報が状態リポジトリや brief/decisions へ出ないことを fixture で固定する redaction テストを追加する。
- charter: v1
- assess: c=2 r=3 a=2
- last_run: req-48d24769-document-msbqisx2-5-r0
- needs_reason: verify 未定義（工程は完了しています。完了条件が無いため自動では done にできません。成果を確認し、問題なければ approve してください）
- needs_dr: DR-0001
- archived: 2026-08-04 06:00:18

#### 納品書
- 完了 : 2026-08-04 06:00:18
- 検証: `` → PASS（承認: 成果を確認して完了を承認）
- 成果 :

出典: archive/document-msbqisx2-5.md
