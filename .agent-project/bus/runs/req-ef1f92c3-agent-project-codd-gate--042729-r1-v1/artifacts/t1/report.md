# t1 — codd-gate 自動検出ロジックの実装

編集対象はいずれもメイン worktree（`/Users/nitto/Workspace/sandbox`、branch `main`）。

## (a) 成果

既存の `tools/agent-project/codd_gate_detect.py`（a1: 実在・バージョン・schemas 互換・能力の
生の実測値）と `codd_gate_status.py`（a4: no-op 縮退の合流点 `build_status`）は、いずれも
「実測そのものを呼び出す配線」を意図的に含めていなかった（各 docstring の除外節に明記）。
`codd_gate_routing.py`（b2）の除外節も同様に「`cfg.regression_cmd`/`cfg.intake_cmd` への自動配線
（b3/c1/e1）」を別タスクの責務として明記しており、t3・t4 の報告（`artifacts/t3/report.md`・
`artifacts/t4/report.md`）も「部品は実在するが自動配線は未接続、現状の有効化は手書き設定のみ」
と確認していた。t1 はこの欠落——検出結果を実際に評価し、「codd-gate が使える状態か」「regression/
intake の結線が既にあるか」を判定する層——を埋める。

1. **新規モジュール `tools/agent-project/codd_gate_wiring.py`**（stdlib のみ、既存の
   codd_gate_detect/codd_gate_status/codd_gate_routing の3モジュールにのみ依存する sibling
   module。他の codd_gate_* と同じ設計規約に従う）。
   - `regression_wired(regression_cmd)` / `intake_wired(intake_cmd)` — 手書き文字列が既に
     codd-gate を指しているか（結線の有無）を正規表現で判定する純粋関数。
   - `recommend_regression_cmd(repos_path, vcwd=None)` / `recommend_intake_cmd(...)` — 未結線時に
     `cfg.regression_cmd`/`cfg.intake_cmd` へそのまま注入できる推奨コマンド文字列を組み立てる
     （`codd_gate_routing.resolve_repos_arg` を再利用。`$KIRO_BASE_REV` はシェル変数参照のまま
     埋め込み、`codd_gate_base.py` が担う実行時解決に委ねる）。
   - `judge_wiring(status, regression_cmd, intake_cmd, capabilities=None, repos_path=None, vcwd=None)`
     — 実測済みの `CoddGateStatus`/capabilities を受け取り `WiringJudgment`（usable/fully_wired/
     actionable と推奨コマンド）を組み立てる純粋関数（I/O なし）。
   - `detect_wiring(regression_cmd=None, intake_cmd=None, repos_path=None, ...)` — a1 の実測4関数
     （`resolve_codd_gate`→`get_version`→`check_repos_schema_compat`→`detect_capabilities`）を
     短絡順で呼び出し、`build_status` へ合流させたうえで `judge_wiring` へつなぐ「a2」相当の配線。
     `which=`/`run=` の依存性注入に対応（test_codd_gate_detect.py と同じテスト容易性）。
   - `doctor_findings(judgment)` — `WiringJudgment` を doctor.py の finding 形式
     （category/severity/title/evidence/fix）へ変換。未検出・非互換は `status.findings` をそのまま
     使い、usable だが未結線のときだけ severity=info の推奨 finding を追加、完全結線済みなら
     空リスト。
2. **`tools/agent-project/agent_project/doctor.py` への結線**（2箇所、計 +34/-1 行）。
   - `_codd_gate_wiring_module()` — `model.py` の `_codd_gate_debt_module()` と同一パターンの
     遅延 import（sys.path 解決含む）。sibling module が無い環境では None を返し、呼び出し側は
     no-op 縮退する。
   - `doctor_codd_gate_findings(cfg, which=shutil.which, run=subprocess.run)` —
     `repo_registry_path(cfg)`（charter.py fragment。同一共有名前空間への実行時前方参照。
     `agent_project/__init__.py` の exec 合成方式が保証する安全性は既存コードと同じ）で
     repos.json の場所を解決し、`codd_gate_wiring.detect_wiring` へ渡して doctor finding を得る。
   - `cmd_doctor` の `deterministic` 所見リスト（1箇所目のみ。`doctor_flow_bus_coverage_findings`
     と同じ理由で `--fix` 後の再チェックリストには含めない——どちらも `fix_action` を持たず
     `apply_doctor_fix` で自動修正できない「推奨のみ」の所見のため）に
     `+ doctor_codd_gate_findings(cfg)` を追加。`agent-project doctor` を実行すると codd-gate の
     検出結果と結線の有無が finding として表示されるようになる（severity=info。任意機能なので
     `doctor` の終了コードは変えない設計は critical/warn を出す既存所見と同じ枠組みに従う）。
3. **テスト `tools/agent-project/tests/test_codd_gate_wiring.py`**（新規19件。依存性注入で
   subprocess を起動しない決定的テスト。結線判定・推奨コマンド組み立て・`judge_wiring` の
   純粋関数としての5分岐・`detect_wiring` の実測配線3ケース・`doctor_findings` の3ケースを
   カバー）。

## (b) 検証内容と結果

- 完了条件ゲート: `cd /Users/nitto/Workspace/sandbox && grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml` → **exit 0**（既存の手書き設定のまま。
  今回の変更はこの行に触れていない——`.agent/agent-project.yaml` への書き込みは regression 結線
  タスクの責務であり、範囲外）。
- 新規テスト: `python3 -m unittest discover -s tools/agent-project/tests -p "test_codd_gate_wiring.py"`
  → **19/19 pass**。
- 全体テスト: `python3 -m unittest discover -s tools/agent-project/tests` → **725件中724 pass**。
  唯一の失敗 `test_kf_base_passes_flow_config` は `/var` vs `/private/var`（macOS の tmp シンボリック
  リンク解決差）による既存の環境依存 flake で、本タスクの変更と無関係（t3 の報告と同じ既知事象。
  変更前の baseline でも同一テストのみ失敗することを確認済み）。
- `doctor.py` への結線は実環境（このリポジトリには `tools/codd-gate/codd-gate.py` が同梱されて
  おり `--version` が実際に `1.0.0` を返す）で `cmd_doctor` を含む既存テストスイート全体を再実行し、
  新規 finding の混入によるテスト破壊が無いことを確認した（`repo_registry_path` が None を返す
  既存テストの Config では `repos_path is None` により推奨コマンドが出ず finding 数は変わらない
  設計だが、念のため実行時間・pass 数の両方で裏取りした）。
- `python3 -m py_compile` で `codd_gate_wiring.py`・`doctor.py`・テストファイルの構文健全性を確認。

## (c) 前提・未解決事項・範囲外の所見

**採用した前提**:
- t1 の完了条件（グレップ対象の yaml 行）は着手前から満たされていたため、実質的なスコープは
  タスク定義の goal（「codd-gate の存在・schemas 共通データ契約を検出し、regression/intake
  結線の有無を判定する」検出ロジックの実装）と解釈した。
- 検出結果を実際に `.agent/agent-project.yaml`（`cfg.regression_cmd`/`cfg.intake_cmd`）へ書き込む
  永続化、および `mr.py`/`model.py` の実行時フックを `CoddGateStatus.command()` ベースの動的組み立て
  へ置き換える大改修は、並行タスク（regression/intake 結線タスク）の責務・この run の他ワーカーの
  同時編集ファイルと重なるため、意図的に対象外とした（`codd_gate_wiring.py` は推奨コマンド文字列を
  「返すだけ」で、どこにも書き込まない）。t2/t3/t6 が `wiring.detect_wiring(...)` を呼んで
  `recommended_regression_cmd`/`recommended_intake_cmd` をそのまま使える形にしてある。
- `judge_wiring` の capability ゲート（`capabilities.get("verify"/"debt", True)`）は「未実測なら
  楽観的に True」を既定にした。呼び出し元が capabilities を渡さずに直接使う簡易呼び出し（テスト等）
  で過剰に厳しくならないための設計判断。`detect_wiring` 経由（実運用の入口）では常に実測値が渡る
  ため既定値は使われない。

**未解決事項・範囲外で見つけた問題（未修正・報告のみ）**:
- t4 の報告どおり `install.sh` の zipapp 生成が `codd_gate_*.py`（sibling module 一式。今回追加した
  `codd_gate_wiring.py` も含む）を同梱していない。配布バイナリでは `_codd_gate_wiring_module()` が
  import 失敗して None を返し、`doctor_codd_gate_findings` は no-op（空リスト）へ縮退する
  ——doctor 自体は壊れないが、配布環境では codd-gate 連携の finding が一切出ない。t3 と同じ理由
  （共有ファイルの同時編集衝突回避）で今回も手を付けていない。横断的な課題として1箇所（install.sh）
  で解消するのが筋。
- `judge_wiring`/`detect_wiring` は `.agent-project/repos.json` が**存在しない**場合、schema 判定を
  「不明（内定的に OK 扱い）」に倒す設計にした——repos.json は charter からの自動生成物であり
  未生成自体は異常ではないため。ただし repos.json が存在するのに schema 不適合なケースでは
  `status.usable=False` となり、推奨コマンドが一切出ない（regression/intake 側は「未結線のまま
  何も推奨されない」に留まり、エラー通知はしない）。人が気づく経路は `agent-project doctor` の
  finding（critical severity で state される）のみなので、無人運用で doctor を定期実行しない構成
  では埋もれる可能性がある——運用上の懸念として記す（今回のスコープでは doctor 経路の追加で
  可視化した以上の対応はしていない）。
