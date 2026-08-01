# agent-flow base 同期と統合 receipt 設計

## 1. 結論

長寿命の作業ブランチを最新 target へ統合する責務を、LLM ワーカーではなく `agent-flow` の
workspace 制御へ置く。書込 workspace を持ち `branch` と `target` が異なる run には、planner の
出力と無関係な system node `base-sync` を先頭へ 1 件だけ挿入する。

- target が作業ブランチの祖先なら何もしない。
- target が進んでいて競合しなければ、`agent-flow` が通常 merge を commit・push する。
- 競合すれば、専用 worktree の merge 状態と競合ファイルをワーカーへ渡す。ワーカーはファイルだけを
  編集し、commit / push / rebase は引き続き禁止する。
- unmerged entry、conflict marker、target の祖先性を制御層が検査し、すべて満たすまで node を
  `done` にしない。
- verification receipt は成果 revision に加えて検証時の target revision を固定する。承認時に target
  が進んでいれば receipt を古いものとして扱い、統合・再検証なしに `done` にしない。

履歴を書き換える rebase は使わない。通常 merge なら force push が不要で、複数 commit にまたがる
競合も一度に解消できる。利用者の「rebase して」という表現は「最新 target と統合可能にする」という
目的として扱う。

## 2. 問題

現行の worker prompt は、専用 worktree 内でも `commit / push / checkout / branch / rebase / stash` を
禁止している。一方 `ensure_workspace_clone()` は既存の作業ブランチを base より優先して checkout し、
`finalize_workspace()` が行う rebase は同じ作業ブランチへの並行 push の統合だけである。target の更新を
作業ブランチへ取り込む担当が存在しない。

この状態で planner が「最新 main を取得して rebase」という work node を作ると、ワーカーは規約に従い
何も変更しない。それでも executor が例外を送出しなければ node は `done` になり、後続のブランチ単体
テストが PASS する。`resume-run` は done node を温存するため、同じ誤判定を繰り返す。

さらに verification receipt は `result_rev` だけを固定しており、検証後に target が進んだことを表せない。
承認時の統合処理は競合なら `done` を拒否するが、競合の発見が遅く、解消を実行する正規経路もない。

## 3. 不変条件

1. Git の履歴操作は `agent-flow` が所有し、LLM ワーカーへ許可しない。
2. `base-sync` は planner が省略・完了宣言できない system node とする。
3. 書込 workspace の検証 PASS は、`result_rev` と `target_rev` の組に対してのみ有効とする。
4. target 更新後の古い receipt で自動 merge または `done` を確定しない。
5. `resume-run` は一過性失敗の再開に残し、target 更新を伴う差し戻しには既存の `revise` を使って新 run
   を作る。
6. GitLab MR の有無にかかわらず同じ安全条件を適用する。
7. 読み取り専用 workspace、target の無い run、branch と target が同じ runには base 同期を要求しない。

## 4. 実行フロー

### 4.1 system node の挿入

workspace spec に `url`、`branch`、`target` があり、`branch != target` の場合、orchestrator は初回計画の
直後に `base-sync` を挿入する。元の root node すべてへ `base-sync` の依存を追加する。planner の JSON
には system node を生成させない。

再計画で追加される node は既に完了した `base-sync` の成果ブランチから作業する。新しい run は必ず
新しい `base-sync` を持つ。これにより、古い run の done node を温存する `resume-run` と target 更新を
混ぜない。

### 4.2 base-sync worker

`kind=base-sync` は通常の work node と別処理にする。

1. origin から作業ブランチと target を fetch する。
2. target が作業ブランチの祖先なら `done` を記録し、LLM を呼ばない。
3. 専用 worktree で `git merge --no-commit --no-ff origin/<target>` を実行する。
4. 競合がなければ制御層が merge commit を作り、作業ブランチへ push する。
5. 競合があれば `git diff --name-only --diff-filter=U` の有界な一覧と解決方針をワーカーへ渡す。
6. ワーカーは競合ファイルを編集する。Git コマンドは実行しない。
7. 制御層が次を検査する。
   - `git diff --name-only --diff-filter=U` が空
   - `git diff --check` が成功
   - 変更対象に conflict marker がない
   - merge commit 後、target revision が HEAD の祖先
8. 検査成功時だけ commit・push と node `done` を記録する。失敗時は node `failed` と構造化情報を残す。

構造化結果は次を最小形とする。

```json
{
  "ok": false,
  "error_class": "integration",
  "target": "main",
  "target_rev": "0123abcd",
  "conflict_files": ["package.json", "src/project.js"]
}
```

通常 work node についても executor の `data.ok == false` は `failed` として扱う。既存 executor が
構造化 data を返さない場合の動作は変えず、flow-worker prompt に未完了時の `{"ok": false}` 出力を
追加する。偽 done の一般的な逃げ道を閉じつつ、既存プラグインを一括破壊しない。

### 4.3 verification plan / receipt

書込 workspace の verification plan version 2 に、target を追加する。

```json
{
  "version": 2,
  "integration": {"target": "main"}
}
```

receipt version 2 は検証時の target revision と統合判定を返す。

```json
{
  "version": 2,
  "result_rev": "成果commit",
  "integration": {
    "target": "main",
    "target_rev": "検証時のorigin/main",
    "verdict": "pass",
    "conflict_files": []
  }
}
```

verifier は target を fetch し、`target_rev` が `result_rev` の祖先である場合だけ integration を PASS に
する。不一致なら通常の成果修正 node ではなく、新しい `base-sync` node を注入してから verification を
やり直す。plan version 1 と読み取り専用 run は従来どおり扱い、version 2 の integration が欠落した
receipt は fail-close とする。

`agent-project` は既存の plan digest、result revision、command、criterion に加えて integration を検算し、
検収待ちへ進める際に `gate_target` と `gate_target_rev` を保存する。

### 4.4 承認時の鮮度確認

`approve_review_done()` は成果を統合する直前に target を fetch する。

- 現在の target revision が `gate_target_rev` と一致する: 既存の MR / 一時 worktree 統合へ進む。
- 異なる: merge を試さず review を維持し、「target 更新により再統合・再検証が必要」とする。

競合がないからといって、未検証の新しい target をその場で merge して `done` にしない。既存の
`finalize_task_delivery()` は最終的な統合と push を担当し続け、鮮度確認だけをその前段へ足す。

## 5. agent-dashboard

needs の構造化情報が `error_class=integration`、または承認時に target の更新を検出した場合、通常の
「失敗した工程だけ再実行」を表示しない。代わりに次を表示する。

- 状態: 「最新 main との統合が必要」
- 検証時 target revision と現在 revision
- 競合ファイル一覧
- 操作: 「最新 main を取り込み、競合解消して再検証」

操作は新しい command を増やさず、既存の `revise` に固定 feedback を渡す。`cmd_revise()` は review / blocked
から ready へ戻し `rev` を進めて新 run を強制するため、target 更新の用途に合う。`resume-run` は表示しない。

固定 feedback:

> 最新 target を作業ブランチへ統合し、競合を解消したうえで全検証を再実行する。

## 6. エラー処理

| 事象 | 状態 | 次の処理 |
|---|---|---|
| fetch 失敗 | `failed`, `error_class=transient` | 既存 auto-heal / resume-run |
| clean merge | `base-sync=done` | 後続 node を実行 |
| 競合解消成功 | `base-sync=done` | 後続 node と verification |
| 競合未解消 | `base-sync=failed`, `error_class=integration` | 同 node の再作業または人へ |
| verification 中に target 更新 | integration fail | 新 `base-sync` →再検証 |
| review 中に target 更新 | review 維持 | dashboard の revise 操作で新 run |
| approve 時に競合 | review 維持 | 現行どおり done 拒否 |

## 7. テスト

### agent-flow

1. 古い作業ブランチ＋競合なし: LLM を呼ばず target を merge・pushする。
2. 古い作業ブランチ＋競合あり: conflict worktreeを渡し、解消後だけ pushする。
3. unmerged entry または conflict marker 残存: `base-sync` を failed にする。
4. `data.ok=false`: work node を done にしない。
5. system node が全 root node の依存になる。
6. target が既に祖先: no-op、余分な commit を作らない。
7. verification 後に target 更新: integration failからbase-syncを再注入する。

### agent-project

1. version 2 receipt の target / target_rev / verdict を検算する。
2. integration 欠落・target不一致・古い target revisionを採用しない。
3. review 到達時に `gate_target_rev` を保存する。
4. approve 前に target が進んだ場合、reviewを維持しmerge/doneを実行しない。
5. target不変の場合、既存のGitLab MRおよびforge無し統合を維持する。

### agent-dashboard

1. integration conflictではresume-runを選ばない。
2. 競合ファイルとrevision差を表示する。
3. 専用操作が既存revise commandを投函する。
4. 通常のtransient失敗は従来どおりresume-runを使う。

### 回帰シナリオ

`dashboard-163827` と同じ履歴をfixture化する。baseから長く分岐した作業ブランチ、後から進んだmain、
複数ファイルのcontent conflict、ブランチ単体テストPASSを再現し、integrationが解消されるまでreviewへ
進まないことを確認する。

## 8. 実装計画

### Phase 1: workspace base-sync

- `tools/agent-flow/agent_flow/orchestrate.py`
  - system node挿入とroot dependency付与。
- `tools/agent-flow/agent_flow/workspace.py`
  - target fetch、祖先判定、merge準備、競合検査、merge commit/push。
- `tools/agent-flow/agent_flow/work.py`
  - `kind=base-sync` の決定的処理と `data.ok=false` のstatus反映。
- `.github/skills/flow-worker/scripts/prompt.py`
  - 未完了時の構造化result契約。Git禁止規則は維持。
- `tools/agent-flow/tests/test_daemon.py` / `test_run.py`
  - clean/conflict/no-op/偽doneの最小回帰テスト。

### Phase 2: integration receipt

- `schemas/verification-plan.schema.json`
- `schemas/verification-receipt.schema.json`
- `tools/agent-tools/agentcore/agentcore/verifycontract.py`
  - version 2 integration契約と検算。
- `tools/agent-flow/agent_flow/verifyplan.py`
  - target revision取得、integration判定、base-sync再注入。
- `tools/agent-project/agent_project/verify.py` / `mr.py`
  - receipt検算、gate target保存、approve時鮮度確認。
- 対応する既存testファイルへversion 1互換とversion 2 fail-closeのケースを追加。

### Phase 3: dashboard導線

- `tools/agent-project/agent_project/needs.py`
  - integration failureの構造化表示材料。
- `tools/agent-dashboard/src/renderer/sections/needs.js`
  - integration conflictの表示とrerun plan分岐。
- `tools/agent-dashboard/src/features/agent-project/main/actions.js`
  - 新commandは追加せず既存reviseを使用。
- `tools/agent-dashboard/test/needs-diagnosis.test.js`ほか既存needs actionテスト
  - integrationとtransientの操作分岐。

### Phase 4: 総合確認

- agent-flow、agent-project、agent-dashboardの対象テストを実行する。
- `dashboard-163827` fixtureで偽PASSが再現しないことを確認する。
- version 1 receipt、読み取り専用run、GitLab MR、forge無し統合の回帰を確認する。

## 9. 採用しない案

### 最終 mergeability gate だけを追加する

偽PASSは防げるが、競合を解消する担当不在は残る。人が同じ指示を繰り返す問題を解決しない。

### LLMワーカーへrebaseを許可する

共有ブランチの履歴書換え、force push、並行workerとの競合をLLMへ委ねることになる。現在のworkspace
所有境界を壊すため採用しない。

### feedback文から「競合」を推測する

自然言語ヒューリスティックは表記ゆれと誤発火を生む。dashboardは構造化failureを読み、既存reviseを
明示的に呼ぶ。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-01 |
| 決定者 | nitto |
| 採用案 | agent-flow制御層によるbase-sync system node＋target revision固定receipt |
| 却下案 | 最終gateのみ（解消経路がない）、LLMへのrebase許可（履歴操作の安全境界を壊す） |
| 主な理由 | Git操作の所有者が最新target統合も担当し、統合後のrevision組に対する検証だけを採用できるため |
| トレードオフ | runごとにfetch・祖先判定が増え、receipt version 2の段階移行が必要 |
| 再評価条件 | base-syncの競合解消率が低い、merge commit増加が運用上問題になる、またはforge側のmerge trainへ統一するとき |
