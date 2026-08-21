# Delegation feature

エンジン間の委譲を、**エンジン非依存の共通封筒**（[delegation 契約](../../../../../schemas/delegation.schema.json)）で
扱う内部機能。利用者向けの独立画面は持たない — 依頼・参加・受け入れはミッション画面、判断は要対応、
進捗は実行画面から操作する。

設計: [`docs/plans/2026-07-19-delegation-contract-design.md`](../../../../../docs/plans/2026-07-19-delegation-contract-design.md)。
骨格は [`docs/designs/agent-dashboard-design.md`](../../../../../docs/designs/agent-dashboard-design.md) §4。

## 方針

**バスも claim プロトコルも統一しない。** 共通なのは封筒（何を誰にどんな条件で頼むか）だけで、
アダプタが各エンジンのネイティブ形式へ変換して投函する。

| 宛先 | 変換先 | アダプタ |
|---|---|---|
| agent-amigos | ホームの `commands/*.json` ドロップ | `main/amigos-adapter.js` |
| agent-flow | バスの `inbox/` ドロップ | `main/flow-adapter.js` |
| 委譲公示板（agent-board） | 板リポジトリの `delegations/` | `main/board-adapter.js` |

IPC は `delegation:list` / `nodes` / `nodeCommand` / `post` / `accept` / `reject` / `award` / `cancel`。
どれも**契約ファイルの投函か読み取り**で、エンジンのプロセスには触れない。

## 板（agent-board）への書き込みは常駐体が行う（S8-2 / S8-3）

`cancel` / `award` / 手動入札（`board-bid`）は、板へ直接書かずに
**この PC の常駐体（`agent-project serve`）へ指示を投函**する
（[`agent-node-command`](../../../../../schemas/agent-node-command.schema.json)・
置き場は `$AGENT_COMMANDS_DIR`、既定は**実行エンジンのホーム**配下 `.agents/commands/`）。
理由は 2 つ:

1. **`git+` 板では直接書き込みが誰にも届かない。** アダプタは板の作業ディレクトリへ
   ファイルを置くだけで push しない——ローカル dir の板でしか成立しておらず、
   押しても効かないボタンだった。
2. **claim 規則を UI に複製しない。** 入札は lease と `(ts, who)` タイブレークを持つ
   プロトコルで、2 つ目の実装を作れば必ずずれる（二重落札）。

`post`（公示）だけは今も直接書き込みのまま。dashboard に手動 post の UI が無いので
触っていない——`git+` 板で使い始めるときに `board-award` と同じ経路へ寄せる。

### 投函先と `board:` の決め方（P0-2）

指示が届くための条件は 2 つあり、どちらも**実行エンジンが書く `engine/status.json` が
唯一の根拠**になる（画面が host.yaml を自前で読み解くと、宣言の解釈が 2 実装になる）:

| | 何を使うか | 外すとどうなるか |
|---|---|---|
| 置き場 | `engine.agentsHome(cfg)`（Windows なら `wslpath` で WSL 側 `$HOME/.agents`）配下の `commands/` | 投函先と取り込み先が別ファイルシステムになり、押しても何も起きない（`.err` すら出ない） |
| `board:` | `status.json` の `board.location`（板の**所在**）。設定の板フォルダは `board.workdir` と突き合わせて所在へ翻訳する | 常駐体の「宣言と違う板は取り込まない」検査に必ず引っかかり、全指示が `.err` へ落ちる |

翻訳できない板（この端末の実行エンジンが参加していない板）は、投函せずその場で断る——
届かない指示をファイルとして残し、後から `.err` で失敗を知るより短い。
`delegation.nodeCommandsDir` を明示すれば置き場は上書きできる（設定画面の
「この端末への指示の受け渡し先」）。**旧ホーム `~/.agent` へのフォールバックは持たない**
——実行エンジン側に無いので、書けるのに誰も読まない場所が増えるだけになる。
