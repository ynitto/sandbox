# verify gate 結果（t1〜t4 突合せ）

## 判定

**verify=fail**

理由:
1. `docs/designs/README.md` が未作成で、完了条件コマンドが `EXIT:1`。
2. t1〜t4 間で二重計上が 5 件あり、README 執筆前にカテゴリを一意化する必要がある。

---

## (a) 実ファイル一覧（t4）と t1〜t4 設計書集合の差分

- 基準実ファイル（`/Users/nitto/Workspace/sandbox/docs/designs/*.md`）: **23件**
- t1〜t4 の列挙ファイル（重複除く）: **23件**

### 漏れ（どのカテゴリにも属さないファイル）

- なし（`actual - union = ∅`）

### 実在しないファイル名

- なし（`union - actual = ∅`）

### 二重計上（要一本化）

| ファイル名 | 重複元 |
|---|---|
| `codd-gate-design.md` | t1, t2 |
| `agent-tools-rename-design.md` | t1, t4 |
| `gitea-gitlab-sync-design.md` | t3, t4 |
| `ltm-use-v4-design.md` | t3, t4 |
| `selfhost-forge-comparison.md` | t3, t4 |

---

## (b) kiro-loop-* / agent-loop-* 重複 4 組の扱い確認

4 組とも **両方のファイル名が列挙済み**（t2 で両系統を明示）。

1. `kiro-loop-adaptive-interval-design.md` / `agent-loop-adaptive-interval-design.md`
2. `kiro-loop-agent-messaging-design.md` / `agent-loop-agent-messaging-design.md`
3. `kiro-loop-event-hook-design.md` / `agent-loop-event-hook-design.md`
4. `kiro-loop-gitlab-webhook-design.md` / `agent-loop-gitlab-webhook-design.md`

---

## (c) カテゴリ体系（重複解消後の確定割当）と読む順序

### 確定カテゴリ割当（23件を一意割当）

#### 主要 4 設計（README 冒頭固定）
- `agent-project-design.md`
- `agent-flow-design.md`
- `codd-gate-design.md`
- `agent-tools-rename-design.md`

#### ループ拡張（kiro/agent 並記）
- `kiro-loop-adaptive-interval-design.md`
- `agent-loop-adaptive-interval-design.md`
- `kiro-loop-agent-messaging-design.md`
- `agent-loop-agent-messaging-design.md`
- `kiro-loop-event-hook-design.md`
- `agent-loop-event-hook-design.md`
- `kiro-loop-gitlab-webhook-design.md`
- `agent-loop-gitlab-webhook-design.md`

#### 実装・運用設計（外部連携/インフラ/実行基盤）
- `agent-cli-plugin-design.md`
- `agent-flow-retry-inheritance-design.md`
- `git-gitlab-circuit-breaker-pattern.md`
- `git-worktree-cache-pattern.md`
- `gitlab-agent-sns-design.md`
- `node-federation-design.md`
- `plan-a-local-gitlab-design.md`

#### 歴史的・比較検討
- `ltm-use-v4-design.md`
- `ltm-use-v5-brain-design.md`
- `selfhost-forge-comparison.md`
- `gitea-gitlab-sync-design.md`

### 読む順序（README 掲載順）

1. 主要 4 設計（上記4件）
2. ループ拡張（kiro/agent の4組をペアで並記）
3. 実装・運用設計
4. 歴史的・比較検討

---

## 是正指示（不足ファイル名を明示）

1. **不足ファイルを作成**: `docs/designs/README.md`
2. README 冒頭に主要 4 設計への導線を必ず記載:
   - `agent-project-design.md`
   - `agent-flow-design.md`
   - `codd-gate-design.md`
   - `agent-tools-rename-design.md`
3. 二重計上 5 件は上記「確定カテゴリ割当」に合わせて README 上で一意化すること。
