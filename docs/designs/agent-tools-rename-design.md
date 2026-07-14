# agent-* ツール改称（クローン方針）設計書

> 作成日: 2026-07-14
> 関連: `tools/agent-project/`, `tools/agent-flow/`, `tools/agent-loop/`, `tools/agent-dashboard/`,
> `docs/designs/agent-*-design.md`,
> `.github/skills/agent-project/`, `.github/skills/agent-flow/`, `.github/skills/agent-loop-messaging/`

---

## 1. 目的

今後ツールを agent CLI 横断で発展させるため、既存の kiro 接頭辞系統を**置換せずクローン**し、
次の名称へ改称した系統を正として育てる。

| 旧（残置） | 新（クローン） | 役割 |
|-----------|----------------|------|
| `kiro-project` | `agent-project` | 単一プロジェクトの自律バックログ制御層 |
| `kiro-flow` | `agent-flow` | 分散 Dynamic Workflow 実行層 |
| `kiro-projects-viewer` | `agent-dashboard` | 複数プロジェクトの可視化・操作 GUI |
| `kiro-loop` | `agent-loop` | tmux 上のエージェント CLI 定期駆動ループ |

旧系統は後方互換・参照用としてリポジトリ内に残す。新機能・設計更新は新系統へ寄せる。

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
| 設定探索ホーム | `.kiro/` / `~/.kiro/` | `.agent/` / `~/.agent/`（skills/agents は `.kiro` も継続探索） |
| 状態ディレクトリ | `.kiro-project` | `.agent-project` |
| ホーム env | `KIRO_PROJECT_HOME` 等 | `AGENT_PROJECT_HOME` 等（`KIRO_AGENTS_DIR` / `KIRO_STATE_HOME` は共有のため維持） |
| daemon ロック | `kiro-flow-locks` | `agent-flow-locks` |
| 作業ブランチ接頭辞 | `kp/` / `kf/` | `ap/` / `af/` |
| 状態ブランチ | `kiro-state` | `agent-state` |
| Electron 製品名 | Kiro Projects Viewer | Agent Dashboard |
| 設定キー / IPC | `config.kiro` / `kiro:*` | `config.projects` / `dashboard:*` |
| スキル | `.github/skills/kiro-*` | `.github/skills/agent-*` |

**維持するもの**（製品・共有インフラ）:

- `kiro-cli`（エージェント CLI 実装の一種）
- （旧系統として残置する）`kiro-loop` / `kiro-flow` / `kiro-projects-viewer`
- `kiro-project` は `agent-project` への移行完了後に削除する
- `$KIRO_AGENTS_DIR` / `$KIRO_STATE_HOME`（複数ツール共有）
- `~/.kiro/agents`・`~/.kiro/skills`（共有定義の探索先として併用）

## 4. 設計書の扱い

| 旧設計書 | 新設計書 |
|----------|----------|
| `kiro-project-design.md` | `agent-project-design.md` |
| `kiro-flow-design.md` | `agent-flow-design.md` |
| `kiro-flow-retry-inheritance-design.md` | `agent-flow-retry-inheritance-design.md` |
| `docs/plans/*kiro-projects-viewer*` | `docs/plans/*agent-dashboard*` |
| `kiro-loop-*-design.md` / `DESIGN.md` | `agent-loop-*-design.md` 等 |

旧設計書は削除しない。新設計書ヘッダに由来（クローン元）を明記する。

## 5. インストール

```bash
bash tools/agent-flow/install.sh
bash tools/agent-project/install.sh
bash tools/agent-loop/install.sh
# GUI
cd tools/agent-dashboard && npm start
```

旧 `~/.local/bin/kiro-*` と新 `~/.local/bin/agent-*` は併存可能（別バイナリ名）。

## 6. 非目標（この改称ではやらないこと）

- 旧ツール・旧設計書の削除
- 稼働中プロジェクト状態（`.kiro-project`）の自動移行
- `kiro-cli` の改称（エージェント CLI 製品名は維持）
