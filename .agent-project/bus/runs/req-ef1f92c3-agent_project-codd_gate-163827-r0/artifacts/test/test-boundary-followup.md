# test: `_codd_gate_wiring_module` mock テストの新境界追随 / regression gate 検証

## 結論
担当タスクの完了条件は **先行コミットで既に充足済み**。ワークスペース（`tools/agent-project`）への追加編集は不要（調査＋回帰確認に徹し、何も書き換えていない）。

- `_codd_gate_wiring_module` を mock するテスト（`TestCoddGateAutoWiring`）→ **新境界へ追随済み**。
- `TestLoopEngineering::test_regression_gate_blocks_on_failure` / `::test_regression_gate_passes` → **新境界で PASS**。

## 経緯（なぜ編集不要か）
`doc` タスクの予測時点では `tests/test_agent_project.py` の `TestCoddGateAutoWiring`（main の 3894/3923/3933 行）が旧名 `km._codd_gate_wiring_module` を mock しており、doctor.py の改名（`_codd_gate_wiring_module` → `_wiring_module`）で 6 件が AttributeError となる想定だった。

しかし `doc` の**後に**コミットされた `cfg`（`b694cb9`）が、configfile の自動配線を「改名」ではなく**丸ごと除去**する設計判断を採り、同コミットでテスト側も書き換え済み：
- クラス `TestCoddGateAutoWiring` → `TestCoddGateNoAutoWiring` に改題。
- 旧 mock（`mock.patch.object(km, "_codd_gate_wiring_module")` ×3）を**削除**し、新境界の回帰ガードへ置換：
  - `test_configfile_has_no_codd_gate_auto_wiring_hook`（`_apply_codd_gate_auto_wiring` の**非存在**を固定）
  - `test_repos_json_present_does_not_auto_wire` / `test_no_repos_json_leaves_commands_unset` / `test_explicit_commands_pass_through_unchanged`（build_config が probe/補完しない＝差し込み点のみ）

これは全体意図「本体は無改造・差し込み点のみ」に整合する。configfile 側には配線が存在しなくなったため、mock を「改名後モジュールへ付け替える」対象が消え、正しい新境界テストは「自動配線が無いこと」の固定になる。配線ロジック自体は sibling `codd_gate_wiring`（`test_codd_gate_wiring.py`）へ外出しされ、doctor は `_wiring_module()`（`importlib` 遅延解決）経由の `doctor_wiring_findings` で finding 提示する。

**doctor 側の孤児 mock は無い**ことを base で確認：main の `tests/` 内で `_codd_gate_wiring_module` を参照するのは上記 3 行（すべて configfile 側 `TestCoddGateAutoWiring`）のみ。`doctor_codd_gate_findings` を mock/参照するテストは存在しなかったため、doctor の改名で付け替えるべきテストは無い。

`TestLoopEngineering` の回帰ゲート 2 件は `_codd_gate_wiring_module` を mock しておらず、`cfg_for(..., regression_cmd="false"/"true")` で汎用の regression_cmd を直接与える構造。改名の影響を受けず新境界で素通しに PASS する。

## 検証内容と結果（cwd=`tools/agent-project`, Python 3.9.6 / pytest 8.4.2）
- 名指しの完了条件 6 件 — **6 passed**：
  - `TestLoopEngineering::test_regression_gate_blocks_on_failure` PASS
  - `TestLoopEngineering::test_regression_gate_passes` PASS
  - `TestCoddGateNoAutoWiring`（4 件）PASS
- 新境界の裏付け（sibling 外出し先）`test_codd_gate_wiring.py` + `test_codd_gate_regression.py` + `test_codd_gate_detect.py` — **62 passed**。
- 目標クラス＋ doctor 群（`-k "TestLoopEngineering or TestCoddGateNoAutoWiring or TestDoctor or Doctor"`）— **33 passed**。
- 旧名の残存 grep（`tests/` 配下 `_codd_gate_wiring_module` / `doctor_codd_gate_findings`）— **0 stale references**。
- フルスイート（`python3 -m pytest -q`）— **796 passed, 3 failed**。

## 失敗 3 件は本境界と無関係の pre-existing failure（触れていない）
- `TestDaemonRouting::test_kf_base_passes_flow_config` — daemon への flow_config 受け渡し。
- `TestProjectLayer::test_version_inherits_master_charter` — charter 制約の和集合継承。
- `TestJournalRotation::test_rotation_archives_and_starts_fresh` — 同一秒タイムスタンプのアーカイブ名（`.1`..`.19`）を文字列ソートするため順序が崩れ、`line 3` 等の行照合が落ちる（決定性欠如）。

いずれも `cfg`/`mdl` 報告と同一の既知失敗であり、`regression_cmd`/`intake_cmd`/codd_gate 配線・改名境界に触れない。diff（`git diff main..HEAD` の該当領域）は本境界にのみ及び、当該 3 テストは未変更。

## 採用した前提・未解決・範囲外
- **採用前提**: 完了条件を「(1) `_codd_gate_wiring_module` を mock するテストが新境界に追随している (2) 回帰ゲート 2 件が新境界で通る」と解釈。`cfg` が正規の担当として configfile 側テストを既に移行済みのため、最小変更原則で再編集せず検証に徹した。configfile は「配線なし＝差し込み点のみ」が新境界であり、mock 付け替え先（改名後モジュール）は configfile 側には存在しない、という `cfg` の設計判断を正とした。
- **範囲外で見つけた問題（直さず報告）**:
  - `@followup: TestJournalRotation` — アーカイブ名を `.1`..`.19` のゼロ埋め連番にし lexicographic ソートを安定化（`cfg` の followup と同一）。
  - `@followup: doctor `_wiring_module` と model 側 sibling ローダは同型。共有ヘルパ `_sibling_module(name)` への DRY 統合余地（`doc` の followup と同一）。合わせて `doctor_wiring_findings` の finding 内容を直接検証する doctor 側ユニットテスト新設余地あり（現状は sibling `test_codd_gate_wiring.py` が配線検出を、doctor 群テストが統合経路を担保）。
- **未解決事項**: なし。
