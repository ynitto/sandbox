# t1: codd-gate 自動検出・呼び出し方確定（3ブランチ共通起点）

## (a) 成果 / サマリー

codd-gate の実行パス・サブコマンド・フラグ綴りを実バイナリへの実プローブで確定した。完了条件のコマンドは**既に exit 0** で成立している（変更不要）。

**実行パス解決**
- 解決結果: `/Users/nitto/.local/bin/codd-gate`（PATH 上に存在。`shutil.which("codd-gate")` で解決）
- バージョン: `1.0.0`（`codd-gate --version`）
- 解決ロジックは `tools/agent-project/codd_gate_detect.py`（`resolve_codd_gate`: explicit → PATH → 同梱パスの順）に実装済みで、実バイナリに対して動作確認済み:
  ```
  resolved binary: ['/Users/nitto/.local/bin/codd-gate']
  version: (1, 0, 0)
  capabilities: {'verify': True, 'tasks': True, 'debt': True}
  ```

**サブコマンド**（`codd-gate --help`）
```
{scan, impact, verify, tasks, check}
```

**verify サブコマンド**（差分ゲート／負債ラチェット兼用。exit 0/1）
- `--base BASE`（差分の基準 rev。既定 `$KIRO_BASE_REV`。`--debt` 時は不要）
- `--repos FILE`
- `--debt`（差分でなく全体負債をしきい値と突合）
- `--strict` / `--strict-cross`
- `--max-broken` / `--max-undocumented` / `--max-untested`
- 他: `--config` `--repo-dir` `--sync` `--map` `--json` `--repo`

**tasks サブコマンド**（所見を共通 task スキーマの修復タスクとして出力）
- `--debt`（全体負債からタスク化。既定は差分から）
- `--base BASE` / `--repos FILE` / `--priority` / `--max` / `--cohort` / `--inbox`
- 他: `--config` `--repo-dir` `--sync` `--map` `--json` `--repo`

**確定した正しい呼び出し形**（`tools/agent-project/README.md` の正典と一致・`.agent/agent-project.yaml` の既存記述とも一致）
```yaml
regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos <root>/repos.json'
intake_cmd: 'codd-gate tasks --debt --repos <root>/repos.json'
```

## (b) 検証内容と結果

1. **完了条件コマンドの実行**（cwd: `/Users/nitto/Workspace/sandbox-agent-state/.agent-project`）:
   ```
   $ grep -E '^[[:space:]]*regression_cmd:.*codd-gate verify --base' .agent/agent-project.yaml
   regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV" --repos .agent-project/repos.json'
   exit=0
   ```
   → **成立済み**。`.agent/agent-project.yaml:30` に既に正しい綴り（`codd-gate verify --base`）で記述されている。`intake_cmd:`（`.agent/agent-project.yaml:31`）も `codd-gate tasks --debt --repos ...` で整合。

2. **フラグ綴りの実プローブ照合**: `codd-gate verify --help` / `codd-gate tasks --help` の実出力と `.agent/agent-project.yaml` の記述を突合し、`--base` `--repos` `--debt` の綴り・意味とも一致を確認（本報告の (a) 節に転記）。

3. **自動検出コードの動作確認**: `tools/agent-project/codd_gate_detect.py` の `resolve_codd_gate` / `get_version` / `detect_capabilities` を実バイナリに対して実行し、`verify`/`tasks`/`debt` すべて `True` を確認（このモジュールは既存実装。本タスクでは新規実装せず、実バイナリとの整合を裏取りした）。

4. **README 正典との突合**: `tools/agent-project/README.md`（230–244行）に記載の正しい `regression_cmd`/`intake_cmd` の形と、`.agent/agent-project.yaml` の現状記述が完全一致することを確認。

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- 「実行パス」は `resolve_codd_gate` が返す **argv prefix**（`[binary_path]`。PATH 解決時は Python インタプリタ経由でない単一実行ファイル）とした。同梱パス（`tools/codd-gate/codd-gate.py`）は今回未使用（PATH 解決で足りたため）。
- 完了条件が `.agent/agent-project.yaml` への `grep` である以上、「確定」は同ファイルへの**恒久的な記述**を指すと解釈した。同ファイルは README 曰く「人専有ファイル」で agent-project 本体は自動では書き換えないが、既に正しい内容が書かれているため追加の書き込みは行っていない。

**未解決事項 / 範囲外で見つけた問題**
- `<root>/repos.json`（`.agent-project/repos.json` ＝ この worktree の親ディレクトリを基準にした相対パス）は**まだ実在しない**。`regression_cmd`/`intake_cmd` の**実行そのもの**（`codd-gate verify` を実際に走らせて exit 0/1 を得る）は repos.json の生成が前提になるため、後続タスク（regression/intake の結線・end-to-end 検証）の対象として残る。本タスクは呼び出し方の確定までがスコープと判断し、repos.json 生成には着手していない。
- `.agent/agent-project.yaml` の該当2行（`regression_cmd`/`intake_cmd`）は `git status` 上 **untracked**（`.agent/agent-project.yaml` 自体が新規ファイル扱い）。既存の内容が今回のタスクで新規に書いたものか、先行run（r1/r2）の成果が引き継がれたものかは本タスクの範囲では判別できなかった。コミットは kiro-flow 側の責務のため未実施。
