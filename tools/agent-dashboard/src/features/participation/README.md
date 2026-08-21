# Participation feature

「この端末で、いま募集中の仕事を引き受ける」ための小さな操作面。実行の主体は常駐体
（`agent-project serve`）だが、常駐体を置いていない PC からでも人が明示的に手を挙げられるようにする。

設計: [`docs/plans/2026-07-20-agent-dashboard-participation-ui-design.md`](../../../../../docs/plans/2026-07-20-agent-dashboard-participation-ui-design.md)。
骨格は [`docs/designs/agent-dashboard-design.md`](../../../../../docs/designs/agent-dashboard-design.md) §4。

## 唯一のプロセス起動経路

dashboard は原則としてエンジンを起動しない（設計 §2.1）。**この feature だけが例外**で、
`participation:flowJoin` は人がボタンを押したときに agent-flow のワーカーを 1 つだけ detached で立てる。
節度を守るための決まり:

- **人の明示操作でだけ起動する。** ポーリングや自動判断からは起動しない。
- **全体設定の lifecycle を尊重する。** `agent-control` の `workloads.flow.lifecycle` が
  `pause` / `stop` なら、理由を添えて起動を断る（管理面の停止指示を GUI が迂回しない）。
- **起動前に到達性を確かめる。** WSL 経由なら `command -v agent-flow` で存在を確認し、
  無ければ「WSL に agent-flow が入っていない」と具体的に言う（黙って失敗しない）。
- **ノード名は端末由来**（`dashboard-<hostname>-<rand>`）。バス上で誰が参加したか追える。

amigos 側の参加（ロールの引き受け）はプロセスを起こさず、ホームの `commands/` へ claim を投函するだけ。
