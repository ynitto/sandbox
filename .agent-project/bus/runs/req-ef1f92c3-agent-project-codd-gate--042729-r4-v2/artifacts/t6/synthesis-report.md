# t6: 統合 — codd-gate 3結線の agent-project.yaml への反映と完了条件の成立確認

## 判定
**完了条件は現時点で成立済み（exit=0）。ファイルへの新規書き込みは不要だった。**

```
$ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
exit=0
```

## (a) 成果サマリー

t2/t3/t4 が個別に検証した検出・regression・intake の3結線は、この状態 worktree の正典設定
（root 直下 bare `agent-project.yaml`。DR-0005 で人が明示選択し、agent-project の設定探索順序でも
`.agent/agent-project.yaml` より優先される）に**既に一貫して反映済み**であることを確認した。

- `agent-project.yaml` と `.agent/agent-project.yaml` は byte-identical（`diff` で無差分）。
- 両ファイルとも `regression_cmd` / `intake_cmd` に `codd-gate verify --base` / `codd-gate tasks --debt`
  を含み、`codd_gate_wiring.regression_wired` / `intake_wired`（参照リポジトリ、読み取りのみで実行確認）
  はいずれも `True`。
- 完了条件コマンドは、このタスク自身の verify が実際に評価される cwd（`cfg.workdir` = この worktree
  直下、workspace 未指定のため）で exit=0。

**このタスクの本質は「ファイルへの書き込み」ではなく「書き込み済みの設定へ、検証が正しい経路で
辿り着くことの確認」だった。** 設定値自体は t1 系列の作業で以前から正しく、ブロッカーは
`backlog/agent-project-codd-gate--042729.md` の `- workspace: src` / `- routed_by: explicit-alias` が
verify の実行 cwd を「存在しない `ap/agent-project-codd-gate--042729` ブランチの一時 clone」へ
誤誘導していたことにあった（t9/t12 が構造的ブロッカーとして特定 → t10 が同フィールドを除去して是正
→ t11 が実 cwd での exit=0 を再現確認）。今回そのフィールドは既に除去済みであることを直接ファイルを
読んで再確認した。

## (b) t5（敵対的事前チェック）の fail 差し戻しへの対応

t5 は t3・t4 の記述根拠に誤りがあるとして fail 判定した。独立に再検算し、以下のとおり切り分けた。

| 指摘 | t5 の指摘内容 | 再検算結果 | 完了条件への影響 |
|---|---|---|---|
| t3 | 参照リポジトリ `main` の `.agent/agent-project.yaml` も整合していると読める記述 | **誤り確認**。参照repo（`https://github.com/ynitto/sandbox`, read-only）の `regression_cmd` は `codd-gate verify --debt --sync --repos repos.json --max-broken ...` であり `--base` を含まない。t3 はこの区別を曖昧にした | **なし**。完了条件 grep が対象とするのはこの状態worktree自身の bare `agent-project.yaml`（DR-0005 で確定）であり、参照repoの内容は無関係 |
| t4 | `workspace: src` を根拠に verify 失敗を説明 | **時点のズレ**。t4 執筆時点では backlog に `workspace: src` が存在し記述は正しかったが、その後 t10 が同フィールドを除去したため現状とは不整合になった | **なし**。現在の backlog には `workspace` フィールド自体が存在せず（本タスクで直接確認済み）、verify は正しく `cfg.workdir` で実行される |

両者とも記述の甘さ・時点差はあるが、**現在の実ファイル状態・完了条件の成否には影響しない**と判断し、
t3/t4 への差し戻し再実行は行わなかった（現状態の直接検証で十分に代替できたため）。

## (c) 検証内容

1. `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`（cwd=この worktree）→ exit=0（上記）
2. `agent-project.yaml` と `.agent/agent-project.yaml` の内容一致（`diff` no output）
3. `backlog/agent-project-codd-gate--042729.md` に `workspace` / `routed_by` フィールドが存在しないこと、`verify` フィールドが bare `agent-project.yaml` を指すこと（DR-0005 と一致）を直接確認
4. `codd_gate_wiring.regression_wired` / `intake_wired`（参照リポジトリのコードを読み取り専用で import・実行）に現在の `regression_cmd` / `intake_cmd` 文字列を通し、両方 `True` を実測
5. 参照リポジトリ（`/Users/nitto/Workspace/sandbox`, read-only）は無変更（`git status` clean）

## (d) 前提・範囲外

- コード・参照リポジトリへの変更は行っていない（read-only制約を遵守）。
- 変更が必要な状態ではなかったため、`agent-project.yaml` 系ファイルへの書き込みは行っていない。
- 未コミットの状態（`backlog/*.md` 3件の変更、`.agent/agent-project.yaml` の新規stage、`bus/runs/...-r3-v2/` 配下の大量削除）は本タスク以前から存在する orchestrator 側の状態同期の一部であり、`[[kiro-state-single-writer]]` の不変条件（書き手を増やさない）に従いコミットは行っていない。
- 範囲外の既知課題（未修正・報告のみ）: `backlog/docs-designs-README-042729.md` / `backlog/verify-codd-gate-042729.md` にも同種の `workspace` 誤ルーティングが残っている可能性（t10 が報告済み）。`.agent/agent-project.yaml`（新規stage済み）は探索順序上 bare 版に劣後する死蔵ファイルだが、削除要否は本タスクの範囲外。

## 完了条件との突き合わせ

`grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` は、この統合作業の
最終確認として直接再実行し **exit=0** を確認済み。

```json
{"constraints": [
  "agent-project.yaml の正典パスは DR-0005（decisions/配下）で bare `agent-project.yaml`（root直下、.agent/ プレフィックスなし）に確定している。`.agent/agent-project.yaml` は別インスタンス用または死蔵ファイルであり、正典判定・完了条件のgrep対象と混同しないこと。",
  "backlog タスクに `- workspace: <alias>` を付けると、その task.verify は cfg.workdir ではなく該当repoの一時shallow clone（かつ既定で task_branch=true によりそのタスク専用ブランチ）で実行される。push先を持たない・状態worktree内で完結する設定/ドキュメント変更タスクには workspace を付けてはならない（存在しないブランチへのclone失敗でretryを消費し続ける）。",
  "参照リポジトリ（読み取り専用の外部ソース）とこの状態worktree自身の control-plane設定は別物であり、'origin/main側の整合性' のような主張をするときは、完了条件が実際にどちらのファイルを対象にしているか（このケースではDR-0005によりworktree自身のbareファイル）を先に確定してから評価すること。"
]}
```
