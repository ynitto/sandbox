# agent-* ツール改称（クローン方針）設計書

> 作成日: 2026-07-14
> 関連: `tools/agent-project/`, `tools/agent-flow/`, `tools/agent-loop/`, `tools/agent-dashboard/`,
> `docs/designs/agent-*-design.md`,
> `.github/skills/agent-project/`, `.github/skills/agent-flow/`, `.github/skills/agent-loop-messaging/`

---

## 1. 目的

今後ツールを agent CLI 横断で発展させるため、既存の kiro 接頭辞系統をクローンして移行し、
次の名称へ改称した系統を正として育てる。移行完了後の旧系統は削除する。

| 旧 | 新（移行先） | 役割 |
|-----------|----------------|------|
| `kiro-project` | `agent-project` | 単一プロジェクトの自律バックログ制御層 |
| `kiro-flow` | `agent-flow` | 分散 Dynamic Workflow 実行層 |
| `kiro-projects-viewer` | `agent-dashboard` | 複数プロジェクトの可視化・操作 GUI |
| `kiro-loop` | `agent-loop` | tmux 上のエージェント CLI 定期駆動ループ |

新機能・設計更新は新系統へ寄せ、移行確認後に旧実装・旧設計・旧計画を削除する。

## 2. クローン方針（置換しない理由）

- 既存運用（設定パス・ロック・状態ブランチ・インストーラ）を壊さない。
- 新旧を並べて比較・段階移行できる。
- 設計書もクローンし、それぞれが自系統の正典を持つ。
- **モジュール分解は改称後のみ行う**（旧 `kiro-*` は分解しない。新 `agent-*` 側で断片パッケージ化する）。

## 3. 名称対応表（プログラム内）

| 種別 | 旧 | 新 |
|------|----|----|
| ツールディレクトリ | `tools/kiro-*` | `tools/agent-*` / `tools/agent-dashboard` |
| Python パッケージ | （旧は単一/既存のまま） | `agent_project` / `agent_flow` / `agent_loop` |
| CLI / エントリ | `kiro-*.py` | `agent-*.py` |
| 設定ファイル名 | `kiro-*.yaml` | `agent-*.yaml` |
| 設定探索ホーム | `.kiro/` / `~/.kiro/` | `.agents/` / `~/.agents/`（skills/agents は `.kiro` も継続探索） |
| 状態ディレクトリ | `.kiro-project` | `.agent-project` |
| ホーム env | `KIRO_PROJECT_HOME` 等 | `AGENT_PROJECT_HOME` 等（`KIRO_AGENTS_DIR` / `KIRO_STATE_HOME` は共有のため維持） |
| daemon ロック | `kiro-flow-locks` | `agent-flow-locks` |
| 作業ブランチ接頭辞 | `kp/` / `kf/` | `ap/` / `af/` |
| 状態ブランチ | `kiro-state` | `agent-state`（※その後 S1 で状態ブランチ／worktree 方式自体が廃止され、この改称対応は無効化された。状態は状態専用リポジトリの通常 clone〈DirectStateGit〉に一本化し、`state_branch` 等は起動時に fail-fast する廃止キーになっている） |
| Electron 製品名 | Kiro Projects Viewer | Agent Dashboard |
| 設定キー / IPC | `config.kiro` / `kiro:*` | `config.projects` / `dashboard:*` |
| スキル | `.github/skills/kiro-*` | `.github/skills/agent-*` |
| 共有ライブラリ | （なし） | `tools/agent-tools/agentcore`（transport / protocol / vocab / agentcli ほか。3 エンジンの zipapp へ同梱） |

**維持するもの**（製品・共有インフラ）:

- `kiro-cli`（エージェント CLI 実装の一種）
- `kiro-project` / `kiro-flow` / `kiro-projects-viewer` は移行完了後に削除済み
- `$KIRO_AGENTS_DIR` / `$KIRO_STATE_HOME`（複数ツール共有）
- `~/.kiro/agents`・`~/.kiro/skills`（共有定義の探索先として併用）

## 4. 設計書の扱い

| 旧設計書 | 新設計書 |
|----------|----------|
| `kiro-project-design.md` | `agent-project-design.md` |
| `kiro-flow-design.md` | `agent-flow-design.md` |
| `docs/plans/*kiro-projects-viewer*` | `docs/plans/*agent-dashboard*` |
| `kiro-loop-*-design.md` / `DESIGN.md` | `agent-loop-*-design.md` 等 |

旧設計書・旧計画は移行完了後に削除し、新設計書ヘッダには由来を履歴として残す。

> 2026-08-06: ループ拡張の設計書 8 件（`kiro-loop-{event-hook,agent-messaging,gitlab-webhook,adaptive-interval}-design.md` とその agent-loop クローン 4 件）は [`agent-loop-design.md`](./agent-loop-design.md) へ統合し削除した。
>
> 2026-08-08: ツール実装 `tools/kiro-loop/` の残置方針を撤回し、退役（§6）へ切り替えた。以後 `agent-loop` を唯一の正系統としてメンテナンスする。手順は [`2026-08-08-agent-tools-resource-efficiency-plan.md`](../plans/2026-08-08-agent-tools-resource-efficiency-plan.md) の F13。

## 5. インストール

3 エンジン（agent-project / agent-flow / agent-amigos）は統合インストーラ 1 本でまとめて入る。
共有ライブラリ agentcore と環境チェックもここに集約されている（各エンジンの `install.sh` は
`tools/agent-tools/install.sh --only <engine>` へ委譲するシムとして残る）。

```bash
bash tools/agent-tools/install.sh          # 3 エンジン + agentcore 一括
bash tools/agent-loop/install.sh
# GUI
cd tools/agent-dashboard && npm start
```

移行中は旧 `~/.local/bin/kiro-*` と新 `~/.local/bin/agent-*` を併存できるが、移行完了後は旧CLIを削除する。

## 6. `kiro-loop` の退役（2026-08-08 方針転換）

クローン移行の当初方針は「旧系統は残置」だったが、これを撤回する。`tools/kiro-loop/`
（4231 行の単一ファイル）と `tools/agent-loop/`（エントリ + パッケージ 23 モジュール）は
同じ仕様の 2 実装で、C7 が禁じる状態そのものである。ループ系へ機能を足すたび 2 回実装するか、
片方だけ育てて差を広げるかしかない。機能差は kiro-loop 側の 2 点のみで、agent-loop が
上位互換である。

退役の範囲・移行手順・語彙と GUI の寄せは
[`2026-08-08-agent-tools-resource-efficiency-plan.md`](../plans/2026-08-08-agent-tools-resource-efficiency-plan.md)
の F13 が正典。本書は「旧系統を残さない」という方針だけを固定する。

## 7. 非目標（この改称ではやらないこと）

- 稼働中プロジェクト状態（`.kiro-project`）の自動移行
- `kiro-cli` の改称（エージェント CLI 製品名は維持）
