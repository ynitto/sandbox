# t12: fix-and-verify canonical codd-gate — 結果

## 完了できず（構造的ブロッカーを独立に再確認。t10は無変更のままタイムアウト）

t9の構造的ブロッカーを本タスクで独立に再現・再確認した。追加の是正は行っていない。理由は以下の通り。

## 成果・サマリー

- **t10の状況**: `results/t10-fix-canonical-codd-gate-config.json` は `status: failed`, `output: "実行エラー: claude タイムアウト（900s 超過）"`。`artifacts/t10-fix-canonical-codd-gate-config/` は空、worker-2 のイベントログにも `claimed` → `result(failed)` の2行のみで、ファイル変更・調査の痕跡はゼロ。**補完すべき「未完成部分」は存在しない（差分ゼロからの再検証）**。
- **このworktreeの正典設定は既に正しい**: `.agent-project/.agent/agent-project.yaml`（git index に新規 staged 済み、コミット未済）は
  ```
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
  intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
  ```
  を含み、`--repo-dir src=.` まで正しく設定済み。t8が作った `.agent-project/agent-project.yaml`（root, untracked）はこのファイルの byte-identical な複製で、t9の指摘通り「見せかけの複製」であり本物ではない。
- **エンジン側の結線は健全（回帰なし）**: `tools/agent-project` のユニットテストを再実行し、codd-gate 関連32件（`test_codd_gate_detect.py` + `test_codd_gate_routing.py`）全通過、全体759件中758通過。唯一の失敗 `TestDaemonRouting.test_kf_base_passes_flow_config` は macOS の `/var` と `/private/var` のシンボリックリンク差によるテスト自体の環境依存の既存不良で、codd-gate結線・本タスクの変更とは無関係（範囲外につき不変更）。
- **それでも完了条件の grep は exit=0 にならない**: 要求された検証cwd（backlog `workspace: src` が解決する、`ynitto/sandbox` の一時 shallow clone）には、このworktreeの正典設定はそもそも到達しない。二重の構造的理由による（t9の再現に加え、本タスクでも直接再現済み・下記「検証内容」参照）。

## 検証内容と結果（本タスクで実施した再現。すべて読み取り専用の確認操作）

1. **ブランチ未存在**（`_task_verify_cwd` は `task_branch` 既定 true により解決ブランチ `ap/agent-project-codd-gate--042729` を使う）
   ```
   $ git ls-remote --heads origin ap/agent-project-codd-gate--042729   # 該当なし
   $ git clone --depth 1 --branch ap/agent-project-codd-gate--042729 https://github.com/ynitto/sandbox.git <tmp>
   fatal: Remote branch ap/agent-project-codd-gate--042729 not found in upstream origin
   exit=128
   ```
2. **main へフォールバックしても対象ファイルが存在しない**
   ```
   $ git clone --depth 1 --branch main https://github.com/ynitto/sandbox.git <tmp>   # exit=0
   $ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' <tmp>/agent-project.yaml
   <tmp>/agent-project.yaml: No such file or directory
   exit=2
   ```
   クローン直下には `agent-project.yaml` は無く、`.agent/agent-project.yaml` のみ存在（ローカル恒久チェックアウト `/Users/nitto/Workspace/sandbox` でも同一構成。なお同チェックアウトの `.agent/agent-project.yaml` にはローカル未コミット編集があるが、origin未pushのため verify には反映されない — journal.md 13/26行目の既知警告と整合）。
3. **t10成果確認**: 上記の通り、ファイル差分・コミット・ブランチ作成のいずれも見当たらない。

## 採用した前提・未解決事項・範囲外で見つけた問題

- **前提**: 「agent-project が実際に読み込む正典設定」は、本タスクの完了条件grepが評価される実cwd（backlog `workspace: src` が解決する一時clone）内の `agent-project.yaml` を指すと解釈した（t9/t10の記述と整合）。`.agent-project/.agent/agent-project.yaml` はこのworktree内で見た場合の正典だが、その verify cwd からは不可視であり、書き込んでも完了条件には効かない。
- **未解決**: 完了条件のgrepをexit=0で記録することはできなかった（実測はexit=128またはexit=2）。是正には次のいずれかの、本タスクの裁量を超える設計判断が必要:
  - (a) charterの `src`（`参照のみ・push しない`）を書込み許可に変更し、workerが実際に `ap/agent-project-codd-gate--042729` へpushできるようにする（＝reference repoへの変更を許可する charter 改定）。
  - (b) backlogの `verify`/`workspace` 定義を見直し、完了条件が到達可能な正典設定（`.agent-project` 側、既に正しい）を指すようにする。
  - (c) `task_branch` の解決先（存在しないブランチへフォールバックせず main 等へ倒す）をエンジン側で見直す — ただしこれは本タスク単体のスコープを超えるグローバル挙動変更であり、他タスクへの影響評価が要る。
  いずれも「参照リポジトリは読み取り専用・commit/push しないこと」という本タスクの明示の制約と charter の記載に反するため、実施しなかった。
- **範囲外で見つけた問題**: なし。t9の指摘を独立に再確認したのみで、無関係な修正・リファクタリングは行っていない。
