## DR-0001  2026-07-12  actor: nitto
- context : kiro-project-kiro-flow-8-171537（kiro-project / kiro-flow のユニットテストを拡充し、実装コードのカバレッジを 85% 以上へ引き上げる）の実行を承認
- action  : plan-approve
- reason  : kiro-projects-viewer から操作
- affects : kiro-project-kiro-flow-8-171537 → ready

## DR-0002  2026-07-13  actor: nitto
- context : kiro-project-kiro-flow-8-171537（kiro-project / kiro-flow のユニットテストを拡充し、実装コードのカバレッジを 85% 以上へ引き上げる）を人が修正（revise）
- action  : revise
- reason  : 分割によりカバレッジ対象ファイルが消滅（kiro-project.py → kiro_project/ パッケージ）。併せて計測が元から壊れている事実を feedback で申し送る
- affects : verify: python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q --cov=kiro_project --cov=tools/kiro-flow/kiro-flow.py --cov-fail-under=85; feedback 注入
- learn: kiro-project / kiro-flow のユニットテストを拡充し、実装コードのカバレッジを 85% 以上へ引き上げる :: kiro-project.py は分割され 16 行の薄いエントリポイントになった。実体は tools/kiro-project/kiro_project/ パッケージ（20+ モジュール・11,625 行）。カバレッジ対象をそちらへ変更した。  なお現状カバレッジは 1% としか出ない。原因はテストが importlib.util.spec_from_file_location でモジュールを読み込んでいるため coverage が追跡できないこと（kiro-flow.py でも "Module was never imported" 警告が出る＝分割前から計測が壊れていた）。85% を達成するには、まずテストの読み込みを通常の import（sys.path 追加 + import kiro_project）へ直して計測が効くようにすること。それをせずにテストだけ足しても数値は動かない。

