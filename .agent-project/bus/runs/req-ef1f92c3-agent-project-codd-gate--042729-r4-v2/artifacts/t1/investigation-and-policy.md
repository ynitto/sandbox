# t1: 現状調査と方針決定 — agent-project × codd-gate 連携

## (a) 成果サマリー

**結論: 3結線（自動検出／regression／intake）はコード側もこの run の設定側も既に実装・結線済み。
残る唯一のギャップは「完了条件grepが参照するパスが、実ファイルの場所と1階層ズレている」という
メタ情報側の不整合であり、コード実装課題ではない。**

### 1. agent-project.yaml の構造
- 本 run の control-plane（`/Users/nitto/Workspace/sandbox-agent-state/.agent-project`。cwd=root）では
  実体は `.agent/agent-project.yaml`（README.md 693行が明記する探索順 `--config` → `./.agent/` → `~/.agent/`
  に合致）。`root: .agent-project` はこのファイル自身の1行目にある設定で、**このリポジトリ内の
  agent-project.py プロセスの root がこの `.agent-project` ディレクトリである**ことを表す（1つ上の
  `sandbox` 本体とは別の kiro-project インスタンス）。
- 参照専用の `src`（`https://github.com/ynitto/sandbox`, base=main）側にも同名の
  `.agent/agent-project.yaml` があるが、これは **sandbox 自身が持つ別の kiro-project インスタンス**
  （`sandbox/.agent-project/` 配下に独自の backlog/needs/decisions がある）の設定であり、本タスクが
  直接編集する対象ではない（読み取り専用）。
- 現物（control-plane側, 4行目以降抜粋）:
  ```
  regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos repos.json --repo-dir src=.'
  intake_cmd: 'codd-gate tasks --debt --repos repos.json --repo-dir src=.'
  ```
  → 完了条件の正規表現 `regression_cmd:.*codd-gate verify --base` に**既に一致している**。

### 2. codd-gate CLI の検出方法（存在・パス解決）
`sandbox` 本体 `tools/agent-project/codd_gate_detect.py` の `resolve_codd_gate()` が実装済み・
テスト済み（`tests/test_codd_gate_detect.py` 含め計81件 pass 確認）。
- 解決順: `explicit引数` → `shutil.which("codd-gate")`（PATH） → 同梱パス
  `tools/codd-gate/codd-gate.py`。`resolve_agent_flow` と同型の解決連鎖。
- 見つからなければ `None` を返すのみで例外化しない（codd-gate は任意機能。無くても
  agent-project は動く）→ 前提「人による操作を最小限にする」を満たす no-op 縮退設計。
- `get_version` / `check_repos_schema_compat` / `detect_capabilities` が実バイナリへの
  probe（`--version`, `--help`）で version・schemas 互換・利用可能サブコマンド
  （verify/tasks/--debt）を判定し、すべて「不明」は「使わない」側に丸める設計
  （`codd_gate_status.py` の `build_status` が合流点）。

### 3. regression_cmd / intake の既存結線状態
**「結線ロジック」自体は `configfile.py` に実装済みで、`build_config()` から自動的に呼ばれている
（`configfile.py:376` → `_apply_codd_gate_auto_wiring(cfg)`, 定義は同ファイル201-229行目）。**
- `regression_cmd`/`intake_cmd` が**両方とも未設定のときだけ**、`codd_gate_wiring.detect_wiring()`
  の推奨値でメモリ上の `cfg` を埋める（冪等・no-op縮退）。
- **`.agent/agent-project.yaml` そのものへは意図的に一切書き込まない**
  （`configfile.py:204-207`のdocstring: 同ファイルは `state.py` の `_HUMAN_OWNED_STATE_FILES`
  ＝「機械が絶対に書かない」設定ファイルであり、書き込むと状態worktreeの鏡合わせの不変条件を
  壊すため）。これは過去に確認済みの不変条件（kiro-state 同期は単一書き手のみ）と整合する設計判断。
- 実行時の消費側もすでに配線済み:
  - `mr.py:437-448` — done確定前に `cfg.regression_cmd` を実行し、失敗ならタスクを
    `巻き込み事故`としてブロックする（実際に本 run 中で1度発火し、`repos.json` パス解決の
    設定ミスを検知した実績あり＝下記「範囲外の既発見事項」参照）。
  - `model.py:493` `run_intake()` — `cfg.intake_cmd` を subprocess 実行し、stdout の
    JSON を Task spec としてbacklogへ enqueue する。
  - `doctor.py:309` `doctor_codd_gate_findings()` — 未結線・未検出時に info/warn finding を
    生成し、agent-dashboard へ表示できる形にする（charter制約「agent-dashboardをフロントエンド
    として使用する」に合致：ロジックはengine側、表示のみdashboard側）。
- `sandbox/tools/agent-project/tests/test_codd_gate_*.py` 一式（wiring/detect/routing/regression/debt）
  81件すべて pass。**コード実装は完了しており、新規実装の必要はない。**
- 参考: agent-dashboard 側にも対応する後続タスク
  `agent-dashboard-codd-gat-042729`（`after: agent-project-codd-gate--042729`）が
  `sandbox/.agent-project/backlog/` に既に用意されており、本タスク完了を待って
  「連携の有効状態を確認できるUI」を追加する計画になっている（engine/frontend分離の charter
  制約と整合）。

### 4. charter制約との整合性
- 「agent-project/agent-flowをエンジンとして使用する」→ 検出・結線・実行系ロジックはすべて
  `tools/agent-project/*.py`（エンジン側）に閉じている。dashboard側にロジックはない。
- 「agent-dashboardをフロントエンドとして使用する」→ dashboard側タスクは doctor findings の
  表示のみを担う設計（上記）。矛盾なし。
- 「人による操作や確認を最小限にする」→ 自動検出・自動wiring（メモリ上）・no-op縮退により、
  codd-gate 未導入環境でも壊れない。矛盾なし。

## (b) 検証内容と結果

| 検証項目 | 方法 | 結果 |
|---|---|---|
| codd-gate wiring 一式のユニットテスト | `pytest tests/test_codd_gate_wiring.py tests/test_codd_gate_detect.py tests/test_codd_gate_routing.py tests/test_codd_gate_regression.py tests/test_codd_gate_debt.py` (sandbox/tools/agent-project) | **81 passed** |
| 自動wiringの呼び出し配線 | `configfile.py` を grep・目視 | `build_config()` (376行目) → `_apply_codd_gate_auto_wiring` 呼び出し確認 |
| regression/intake の実行時消費 | `mr.py`/`model.py` を grep・目視 | `cfg.regression_cmd`(mr.py:437), `cfg.intake_cmd`(model.py:493, run_intake) とも消費箇所を確認 |
| 完了条件grepの現況（この run の control-plane cwd で） | `grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' agent-project.yaml` を cwd=`.agent-project` root で実行 | **exit=2（ファイルなし）**。実ファイルは `.agent/agent-project.yaml` にあり、そちらに対して同じgrepを打つと **exit=0 で一致**（内容は既に正しい）。 |
| repos.json の実在確認 | `ls repos.json` | 存在（`src` エントリを持つ。以前 needs_reason に記録された「repos レジストリが見つかりません」失敗は、control-plane側の regression_cmd が既に `--repos repos.json --repo-dir src=.` に修正済みのため現状では再現しない） |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「agent-project.yaml」という完了条件grep中のパスは、README・コードの探索順規約
  （`./.agent/` → `~/.agent/`）およびこの run の全 `.agent/agent-project.yaml` の実態に照らし、
  **正規の設定ファイルパスは `.agent/agent-project.yaml` である**とみなした
  （bare `agent-project.yaml` を新設する運用は本プロジェクトのどこにも文書化されていない）。
- 本タスク（t1・kind: work）の責務は「調査と方針決定」であり、`.agent/agent-project.yaml` や
  backlogの `- verify:` フィールドへの書き込みは t3/t6（generate/synthesize）の責務と判断し、
  t1からは一切のファイル変更を行っていない（範囲を守る）。

**方針決定（t2/t3/t4への申し送り）**
1. **t2（自動検出）**: 新規実装は不要。`codd_gate_detect.resolve_codd_gate` が既に
   explicit→PATH→同梱パスの解決連鎖とno-op縮退を実装・テスト済み。t2は「既存実装のこの run
   環境での動作確認（`codd-gate --version` が通るか等）」に限定してよい。
2. **t3（regression結線）/ t4（intake結線）**: control-plane の `.agent/agent-project.yaml` は
   既に `regression_cmd`/`intake_cmd` とも正しい形（`codd_gate_routing.resolve_repos_arg`/
   `resolve_repo_dir_arg` の規約通り、cwd相対の `repos.json` と `--repo-dir src=.`）で設定済み。
   t3/t4は「既存値の再実装」ではなく「値が `codd_gate_wiring.regression_wired`/`intake_wired`
   の判定基準・実行時契約と齟齬なく整合しているかの確認」に限定してよい。
3. **t6（統合）で対応すべき本質的な残作業**: 完了条件grepの対象パス不一致の解消。
   本 run の意思決定記録（DR-0002/0003 は `.agent/agent-project.yaml` を対象とする正しい verify
   文言）に対し、**DR-0005（人による「要対応画面」からの revise）でパスから `.agent/` が
   誤って落ちた**とみられる（DR-0002原文と比較すると `.agent/` の有無だけが差分）。
   実ファイルの配置を変える（bareファイルを新設）のではなく、**backlogの `- verify:` 文言を
   `.agent/agent-project.yaml` に戻す方向を推奨**する（README/コード双方が `.agent/` 配下を
   人専有の正規置き場と明記しているため、bare配置は未文書の shadow file を作ることになり
   望ましくない）。ただしbacklogの `- verify:` 修正は人の意思決定記録を書き換える操作であり、
   t1の範囲外と判断しここでは変更していない。

**未解決事項 / 範囲外で見つけた問題（評価役の判断に委ねる）**
- 上記の「DR-0005でパス prefixが落ちた」疑いは状況証拠（DR間のdiff）からの推測であり、
  本人に確認したわけではない。もし意図的（例: 将来 bare 配置へ規約変更する意図）だった場合は
  本方針と逆の対応（configの物理配置を root 直下へ揃える）が必要になる。
- `sandbox` 本体（メイン worktree、参照専用）側の `.agent/agent-project.yaml` は
  `regression_cmd: '...--repos .agent-project/repos.json'` のままで、`--repo-dir` 指定が無く
  cwd解決規約（`codd_gate_routing.resolve_repo_dir_arg` が前提とする `--repo-dir <name>=.`）とも
  ずれている。ただしこれは参照専用リポジトリ（push禁止）内の別インスタンス設定であり、
  本タスクの変更対象外のため報告のみに留める。
