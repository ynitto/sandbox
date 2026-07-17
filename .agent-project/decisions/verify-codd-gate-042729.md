## DR-0001  2026-07-15  actor: nitto
- context : verify-codd-gate-042729（verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : verify-codd-gate-042729 → ready

## DR-0002  2026-07-16  actor: nitto
- context : verify-codd-gate-042729（verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる）に人のフィードバック
- action  : feedback-resume
- reason  : チェックで承認
- affects : verify-codd-gate-042729 → ready

## DR-0003  2026-07-17  actor: nitto
- context : verify-codd-gate-042729（verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる）に人のフィードバック
- action  : feedback-resume
- reason  : チェックで承認
- affects : verify-codd-gate-042729 → ready

## DR-0004  2026-07-18  actor: nitto
- context : verify-codd-gate-042729（verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる）に人のフィードバック
- action  : feedback-resume
- reason  : チェックで承認
- affects : verify-codd-gate-042729 → ready

## DR-0005  2026-07-18  actor: nitto
- context : verify-codd-gate-042729 の優先度を変更
- action  : reprioritize(pin)
- reason  : agent-dashboard から操作
- affects : policy.pin += verify-codd-gate-042729

## DR-0006  2026-07-18  actor: nitto
- context : verify-codd-gate-042729 の優先度を変更
- action  : reprioritize(pin)
- reason  : agent-dashboard から操作
- affects : policy.pin += verify-codd-gate-042729

## DR-0007  2026-07-18  actor: nitto
- context : verify-codd-gate-042729（verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる）を人の判断から復帰
- action  : approve-and-fix
- reason  : 検証失敗を確認・受容して完了
- affects : verify-codd-gate-042729 → ready
- learn: verify合成が日本語「検証コマンド:」ラベル付き出力からcodd-gateコマンド行を抽出できる :: 検証失敗を確認・受容して完了

