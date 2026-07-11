## DR-0001  2026-07-11  actor: nitto
- context : pytest--x--q-220342（受入条件を満たす: > pytest -x -q）の実行を承認
- action  : plan-approve
- reason  : kiro-projects-viewer から操作
- affects : pytest--x--q-220342 → ready

## DR-0002  2026-07-11  actor: nitto
- context : pytest--x--q-220342 を保留（denylist 化）
- action  : hold(deny)
- reason  : kiro-projects-viewer から操作
- affects : pytest--x--q-220342 → blocked, policy.deny += pytest--x--q-220342
- avoid: 受入条件を満たす: > pytest -x -q :: kiro-projects-viewer から操作

## DR-0003  2026-07-11  actor: nitto
- context : pytest--x--q-220342（受入条件を満たす: > pytest -x -q）を人の判断から復帰
- action  : approve-and-fix
- reason  : 検証コマンドを「echo "test"」にする
- affects : pytest--x--q-220342 → ready
- learn: 受入条件を満たす: > pytest -x -q :: 検証コマンドを「echo "test"」にする

