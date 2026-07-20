## DR-0001  2026-07-18  actor: nitto
- context : dashboard-163827（dashboard で一貫性ゲートの状態把握と有効化を支援する）の実行を承認
- action  : plan-approve
- reason  : agent-dashboard から操作
- affects : dashboard-163827 → ready

## DR-0002  2026-07-20  actor: nitto
- context : dashboard-163827 を run req-ef1f92c3-dashboard-163827-r0 の続きから再開
- action  : resume-run
- reason  : 要対応画面から再実行（失敗した工程だけやり直し）
- affects : dashboard-163827 → ready (last_run=req-ef1f92c3-dashboard-163827-r0)

