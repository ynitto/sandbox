# Delegation feature

エンジン間の委譲を、**エンジン非依存の共通封筒**（[delegation 契約](../../../../../schemas/delegation.schema.json)）で
扱う内部機能。利用者向けの独立画面は持たない — 依頼・参加・受け入れはミッション画面、判断は要対応、
進捗は実行画面から操作する。

設計: [`docs/plans/2026-07-19-delegation-contract-design.md`](../../../../../docs/plans/2026-07-19-delegation-contract-design.md)。
骨格は [`docs/designs/agent-dashboard-design.md`](../../../../../docs/designs/agent-dashboard-design.md) §5。

## 方針

**バスも claim プロトコルも統一しない。** 共通なのは封筒（何を誰にどんな条件で頼むか）だけで、
アダプタが各エンジンのネイティブ形式へ変換して投函する。

| 宛先 | 変換先 | アダプタ |
|---|---|---|
| agent-amigos | ホームの `commands/*.json` ドロップ | `main/amigos-adapter.js` |
| agent-flow | バスの `inbox/` ドロップ | `main/flow-adapter.js` |
| 委譲公示板（agent-board） | 板リポジトリの `delegations/` | `main/board-adapter.js` |

IPC は `delegation:list` / `post` / `accept` / `reject` / `award` / `cancel`。
どれも**契約ファイルの投函か読み取り**で、エンジンのプロセスには触れない。
