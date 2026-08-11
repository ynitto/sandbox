# agent-loop CLI ターン完了hook 実装計画

> 作成日: 2026-08-12  
> 前提設計: [`2026-08-12-agent-loop-turn-completion-hooks-design.md`](2026-08-12-agent-loop-turn-completion-hooks-design.md)  
> 方針: 各stepを独立して検証し、常に画面監視へ戻せる状態を保つ

## 1. agent定義契約

### 変更対象

- `schemas/agent-cli.schema.json`
- `agents/README.md`
- `agents/kiro.json`
- `agents/claude.json`
- `agents/codex.json`
- `agents/copilot.json`
- `agents/opencode.json`
- `tools/agent-loop/agent_loop/cliprofile.py`
- `tools/agent-loop/test/test_cli_profile.py`

### 作業

1. `interactive.turn_completion`を既知string enumとしてschemaへ追加する
2. `CliProfile`へreadonlyな`turn_completion`を追加する
3. legacy Kiroは内部既定`kiro`、他はagent定義値、headlessは常に空として解決する
4. 対応agent定義へCLI名を追加する。Cursorは追加しない
5. READMEへ「CLI固有のsession-local lifecycle adapter」であることを記載する

### 確認

- 未指定agentの挙動が変わらない
- headless定義では無効になる
- 未知値はschema errorまたは安全な無効化になる

## 2. mailboxとhidden command

### 変更対象

- 新規 `tools/agent-loop/agent_loop/turnhooks.py`
- `tools/agent-loop/agent_loop/__init__.py`
- `tools/agent-loop/agent_loop/cli.py`
- `tools/agent-loop/agent_loop/sendcmd.py`
- 新規 `tools/agent-loop/test/test_turn_hooks.py`

### 作業

1. `agent_home_subdir("", "loop-hooks")`配下のinstance/active/events path helperを追加する
2. pane/dispatch IDのfilename正規化を追加する
3. directory `0700`、file `0600`、atomic write/exclusive event createを実装する
4. active recordのcreate/read/deleteと`failure_pending`更新を実装する
5. terminal eventのcreate/claim/deleteを実装する
6. hidden `hook-event` subcommandへ`--adapter`、`--status complete|failure|failure-hint`、`--native-event`、任意payload引数を追加する
7. env、token、adapter、instance、pane、dispatch、generationを検証する
8. `slot-release`をKiro completeへの互換aliasへ変更し、直接semaphore releaseを削除する
9. すべての拒否・重複ケースでstdoutなし、exit `0`とする

### 確認

```bash
python3 -m unittest discover -s tools/agent-loop/test -p 'test_turn_hooks.py'
```

- 正常complete/failure
- failure hintのactive更新
- token/adapter/generation不一致
- duplicate event
- path traversal入力
- managed外`slot-release` no-op

## 3. pane起動へのadapter注入

### 変更対象

- `tools/agent-loop/agent_loop/turnhooks.py`
- `tools/agent-loop/agent_loop/session.py`
- `tools/agent-loop/agent_loop/scheduler.py`
- `tools/agent-loop/test/test_cli_profile.py`
- 新規 `tools/agent-loop/test/test_turn_hook_launch.py`

### 作業

1. paneごとのrandom hook tokenとinstance runtime contextを`SessionManager`で生成・保持する
2. managed interactive paneだけへ共通環境変数を追加する
3. launch前にadapter準備関数を1回呼び、argv/envとcleanup pathを返す
4. Schedulerの`_track_active()`からdispatch ID、generation、adapter、tokenを`SlotMonitor.track()`へ渡す
5. prompt送信成功後のtrack開始でactive recordを作る
6. pane cancel/restart/cleanup時にactive/eventとprivate runtimeを削除する
7. headless、external pane、adapter未指定では準備関数を呼ばない

### 確認

- managed paneだけに環境変数がある
- 既存launch fingerprintへ実効argv/env変更が反映される
- prompt送信失敗時にactive recordを作らない
- pane generation更新後の古いeventを受理しない

## 4. Kiro classic adapter

### 変更対象

- `tools/agent-loop/agent_loop/turnhooks.py`
- `tools/agent-loop/agent_loop/session.py`
- 新規 `tools/agent-loop/agent-hooks/kiro/agent-loop.json`
- 新規 `tools/agent-loop/test/test_kiro_turn_hook.py`

### 作業

1. `--v3`を検出したらadapterを無効化する
2. 実ユーザー`~/.kiro`からwhitelistをprivate runtimeへcopyする
3. copy先のdirectory/file permissionを固定する
4. sessions、logs、cache、credential名のfileを除外する
5. project優先でcustom agent JSONを解決する
6. default agent生成またはcustom agent copyへ`stop` hookをmergeする
7. global/projectのsteering、skills、`AGENTS.md` resourceを重複なしで追加する
8. `includeMcpJson: true`を設定する
9. `KIRO_HOME=<private>`と`--agent <generated-name>`をlaunchへ追加する
10. 解決不能なagent形式はWARNING後に画面監視へ戻す

### 確認

- whitelist snapshotと除外対象
- default/custom agentのfield保持
- 既存hookとの併存
- steering/skills/MCP resourceの保持
- source user filesの内容とmtimeが不変

## 5. ClaudeとCodex adapter

### 変更対象

- `tools/agent-loop/agent_loop/turnhooks.py`
- 新規 `tools/agent-loop/agent-hooks/claude/.claude-plugin/plugin.json`
- 新規 `tools/agent-loop/agent-hooks/claude/hooks/hooks.json`
- 新規 `tools/agent-loop/test/test_claude_turn_hook.py`
- 新規 `tools/agent-loop/test/test_codex_turn_hook.py`

### Claude作業

1. asset pathを`--plugin-dir`へ追加する
2. `Stop`をcomplete、`StopFailure`をfailureとしてhidden commandへ渡す
3. pluginにagent、skill、MCP、default settingsを含めない
4. 同じflagがある場合の重複注入を避ける

### Codex作業

1. user configと選択profileの`notify`をstdlib `tomllib`で解決する
2. 既存notifyが安全なstring配列ならJSON環境変数へ保存する
3. one-off `--config notify=[...]`へagent-loop executableの絶対pathと`hook-event`を設定する
4. Codex payloadをterminal event作成後に既存notifyへ渡す
5. 既存CLI overrideなどを安全に解決できない場合はadapterを無効化する
6. hook trust bypassと`CODEX_HOME`変更は行わない

### 確認

- Claude Stop/StopFailureが正しいcallbackへ到達する
- Claude既存plugin/skills/MCP fixtureを維持する
- Codex eventと既存notifyが各1回実行される
- Codex profile notifyを維持する
- malformed notify/二重overrideで画面監視へ戻る

## 6. CopilotとOpenCode adapter

### 変更対象

- `tools/agent-loop/agent_loop/turnhooks.py`
- 新規 `tools/agent-loop/agent-hooks/copilot/plugin.json`
- 新規 `tools/agent-loop/agent-hooks/copilot/hooks.json`
- 新規 `tools/agent-loop/agent-hooks/opencode/plugins/agent-loop.js`
- 新規 `tools/agent-loop/test/test_copilot_turn_hook.py`
- 新規 `tools/agent-loop/test/test_opencode_turn_hook.py`

### Copilot作業

1. asset pathを`--plugin-dir`へ追加する
2. `agentStop`をcompleteへ変換する
3. `errorOccurred` payloadの`recoverable=false`だけをfailure hintへ変換する
4. `COPILOT_HOME`、`--config-dir`、`COPILOT_PLUGIN_DIR_ONLY`を設定しない

### OpenCode作業

1. pluginだけを含むprivate directoryを`OPENCODE_CONFIG_DIR`へ設定する
2. `session.idle`をcompleteへ変換する
3. `session.error`をfailure hintへ変換する
4. `opencode.json`を生成しない

### 確認

- failure hint単体ではslotを解放しない
- hint後のterminal/idleはfailure callbackになる
- user/projectのinstructions、skills、MCP fixtureを維持する

## 7. SlotMonitor統合

### 変更対象

- `tools/agent-loop/agent_loop/semaphore.py`
- `tools/agent-loop/agent_loop/scheduler.py`
- `tools/agent-loop/test/test_inbox_dispatch.py`
- `tools/agent-loop/test/test_session_timeout.py`
- `tools/agent-loop/test/test_turn_hooks.py`

### 作業

1. `SlotMonitor.track()`へturn hook correlation metadataを任意引数として追加する
2. `_check_pane()`冒頭でterminal eventをclaimする
3. activeの`failure_pending`を画面idle判定へ反映する
4. complete/failureを既存`_release()`へ渡す
5. `_release()`、`untrack()`、`fail()`でactive/eventをcleanupする
6. `max_concurrent: 0`でもSlotMonitorがcompletion monitorとして動く既存契約を維持する

### 確認

- native event優先
- screen fallback
- event/screen raceでcallback 1回
- timeout/pane death/cancelでfailure 1回
- hold-slot/oneshotの既存挙動に回帰がない

## 8. install、doctor、ドキュメント

### 変更対象

- `tools/agent-loop/install.sh`
- `tools/agent-loop/agent_loop/doctor.py`
- `tools/agent-loop/README.md`
- `tools/agent-loop/DESIGN.md`
- `docs/designs/agent-loop-design.md`
- `tools/agent-loop/test/test_doctor.py`
- install testまたは既存installer test

### 作業

1. `<install-prefix>/agent-hooks`へasset treeをcopyする
2. CLI binaryの存在確認やglobal config更新を追加しない
3. doctorへasset欠落、permission不正、unsupported adapter/versionのfindingを追加する
4. native hook優先・画面監視fallback・agent-loop内限定をREADME/DESIGNへ記載する
5. `slot-release`が直接untrack/releaseするという旧説明を更新する
6. YAML `hooks`とCLI turn-completion hookの用語を明確に分ける

## 9. 全体検証

### 自動テスト

```bash
python3 -m unittest discover -s tools/agent-loop/test
```

fake CLI統合テストで次を確認する。

1. 各adapterのnative eventでScheduler callbackが1回だけ発火する
2. hookを発火しないfake CLIは画面監視で完了する
3. user/project設定fixtureの内容とmtimeが変わらない
4. headless、external pane、adapter未指定では注入しない
5. cancel/restart後の遅延eventを無視する

### 手動smoke test

対応CLIが利用可能な環境で各1ターンを実行し、ログで次を確認する。

- adapter選択
- native event受理
- callback 1回
- user skills/MCP/instructionsの可視性
- agent-loop終了後にglobal/project設定が不変

Kiro v3とCursorは「adapter無効・画面監視」の確認だけ行う。

## 10. rollback

- CLI単位: agent定義の`interactive.turn_completion`を削除する
- 全体: adapter準備をfeature無効として画面監視だけへ戻す
- mailbox fileは完了判定の補助であり、削除してもdispatch/Scheduler状態は失われない

DB migrationや永続schema migrationはない。
