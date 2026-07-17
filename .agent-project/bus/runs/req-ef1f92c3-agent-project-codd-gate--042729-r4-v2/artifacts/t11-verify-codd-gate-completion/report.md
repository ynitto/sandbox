# t11: t10 修正後の完了条件・実行cwd再現検証

## (a) 成果サマリー

t10 が backlog の `- workspace: src` / `- routed_by: explicit-alias` を除去したことで、`task.verify`
の実行 cwd は `cfg.workdir`（= 状態 worktree `.agent-project/` 直下、今回の作業ディレクトリそのもの）
に一致する。この実際の cwd で完了条件コマンドをそのまま再実行し、exit=0・一致行を確認した。
併せて、grep 対象の `agent-project.yaml`（root 直下、bare パス）が検証専用に用意された囮ではなく、
実運用でも実際にロードされる正典設定であることをコード・git履歴の両面から確認した。

## (b) 検証内容と結果

### 1. 完了条件コマンドの再実行（実際の検証cwd）

- cwd: `/Users/nitto/Workspace/sandbox-agent-state/.agent-project`（= `cfg.workdir`。t10 の是正により
  `task.verify` もここで実行される）
- 実行コマンド: `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml`
- 一致行:
  ```
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
  ```
- 終了コード: **0**

### 2. 対象 `agent-project.yaml` が正典／実運用参照であることの確認（囮でないことの確認）

| 確認観点 | 方法 | 結果 |
|---|---|---|
| 人の決定との整合 | `decisions/agent-project-codd-gate--042729.md` の DR-0005 を確認 | 2026-07-16、actor: nitto が「要対応画面で検証コマンドを変更」し、`verify: grep ... agent-project.yaml`（bare パス、`.agent/` プレフィックスなし）を明示的に指定済み。今回の grep 対象と完全一致 |
| 実プロセスの設定探索順序 | `tools/agent-project/agent-project.yaml.example`（sandbox、読み取りのみ）冒頭の検索順序コメントを確認 | `1. --config 明示 → 2. カレントディレクトリ直下の agent-project.yaml（プロジェクトマニフェスト。agent-dashboard の自動発見マーカーも兼ねる）→ 3. ./.agent/agent-project.yaml → 4. ~/.agent/...`。root 直下 bare ファイルは `.agent/agent-project.yaml` より**優先順位が高い**ため、両方存在する現状では bare 側が実際にロードされる |
| git 追跡状態 | `git rev-parse --show-toplevel` でリポジトリ実体は `/Users/nitto/Workspace/sandbox-agent-state`、`git show HEAD:.agent-project/agent-project.yaml` と作業ツリーを diff | 差分なし＝**HEAD に既にコミット済みで内容も一致**（今回の検証のために新規作成・改変したものではない） |
| 対比: `.agent/agent-project.yaml`（入れ子インスタンス用） | `git status --short` | `A  .agent-project/.agent/agent-project.yaml`（新規ステージ済み＝今回の一連の作業で追加されたファイル）。中身は bare 版と同一だが、こちらは優先順位が低い別インスタンス用設定であり、grep 対象ではない |

以上により、grep 対象の `agent-project.yaml` は (i) 人が DR-0005 で明示選択したパス、(ii) 実プロセスの
設定探索順序で最優先されるパス、(iii) 既に git 履歴にコミット済みで今回改変していないファイル、の
3点で「検証専用の囮ファイル」ではなく正典／実運用参照設定であると確認した。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「実際の検証cwd」は t10 が特定した `_task_verify_cwd`（workspace 未指定時は `cfg.workdir`）の実装
  に従い、今回のタスク自身の作業ディレクトリ（`.agent-project/` 直下）と同一とみなした。t10 の変更後
  であるため、この前提は t10 の報告内容と整合する。
- 「囮でないこと」の確認基準は、(i) 人の決定記録との整合、(ii) 実プロセスのファイル探索順序上の優先度、
  (iii) git 追跡・コミット履歴の3点とした。これはタスク文の「正典または実運用で参照される設定」という
  要求を満たす具体的な検証観点として妥当と判断した。

**未解決事項**
- なし。完了条件は exit=0 で満たされている。

**範囲外で見つけた問題（報告のみ・修正せず）**
- t10 が報告済みの通り、同種の `- workspace: src` 誤ルーティングが `backlog/docs-designs-README-042729.md`
  と `backlog/verify-codd-gate-042729.md` に残存している（本タスクの範囲外のため未修正）。
- `.agent-project/.agent/agent-project.yaml`（入れ子インスタンス用、新規ステージ済み）は bare 版と内容が
  完全一致しており、探索順序上は使われない死蔵ファイルになっている。誤って将来編集された場合に
  「反映されない設定変更」として混乱を招くリスクがあるが、削除の要否は本タスクの範囲外のため評価役の
  判断に委ねる。

## 完了条件との突き合わせ

指定コマンド `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` を、
t10 是正後に実際に `task.verify` が実行される cwd（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project`）
でそのまま実行し、**終了コード0・一致行を実測確認**した。対象ファイルが正典／実運用参照設定であること
も併せて確認済み。issues なし。

```json
{"constraints": [
  "root 直下の bare `agent-project.yaml` は agent-project の設定探索順序（--config 明示 > カレントディレクトリ直下 > ./.agent/ 配下 > ~/.agent/ 配下）で `.agent/agent-project.yaml` より優先される。両方が存在するリポジトリでは bare 側が実際にロードされる設定であり、`.agent/` 配下は別インスタンス用または死蔵ファイルの可能性があるため、正典判定には両方の存在有無と優先順位を必ず確認すること。",
  "verify 対象ファイルが「囮でないか」を判定するときは、(1) decisions/ 配下の人の明示決定（DR）との一致、(2) 実プロセスの設定ロード順序上の優先度、(3) git 追跡状態（HEAD との diff・コミット履歴の有無）の3点を確認する。新規作成・未コミットのファイルで完了条件のgrepだけが通る状態は「見せかけの成功」を疑う根拠になる。"
]}
```
