# agentcore 仕様書

> 設計の「なぜ」は [`docs/designs/agentcore-design.md`](../designs/agentcore-design.md)。
> 本書は**契約**（モジュール一覧・公開 API・配布・依存の向き）を引く場所です。
> 実装: `tools/agent-tools/agentcore/agentcore/`（33 モジュール・約 13,500 行）／ テスト 40 ファイル・915 件（ルート 2 つ・§6）

---

## 1. 位置づけ

agentcore は **agent-\* ファミリーが共有するものの唯一の実装**を置く内部パッケージです。
独立配布せず、各ツールの zipapp へ同梱します。

```
agentcore を import する側
  agent-project / agent-flow / agent-amigos / agent-loop / agent-audit / tools/agent-tools/eval
agentcore が import する側
  （なし。標準ライブラリだけ）
```

**依存は一方向です。** agentcore はどのエンジンも知らず、エンジン固有の設定ファイル・状態
ディレクトリ・語彙を読みません。入力はすべて呼び出し元が引数で渡します。

---

## 2. モジュール

### 2.1 共通契約の 1 実装（19 モジュール）

| モジュール | 何の単一定義か | 主な公開 API |
|---|---|---|
| `agentcli` | エージェント CLI 定義（`agents/<name>.json`）の唯一のローダ | `load_cli` / `headless_cmd` / `interactive_cmd` / `classify_error` / `resolve_variant` / `costlier_fallback` / `parse_usage` / `spill_prompt` / `plugin_dirs`（[仕様書](./agent-cli-spec.md)） |
| `slashroute` | コマンド面（スラッシュ）の 1 実装。ルート表・用途の宣言・起動形の調停 | `plan` / `resolve` / `classify` / `lookup` / `declaration` / `declarations` / `command_dirs` / `commands` / `spellings` / `render_help` / `parse_line` / `split_leading` / `apply_to_goal`（[仕様書](./agent-herd-spec.md) §13） |
| `protocol` | claim / lease | `list_claims` / `winner` / `write_claim` / `try_claim` / `extend_claim` / `renew_lease` / `release_claim` / `unique_ts` / `write_json_atomic` |
| `transport` | git 転送層 | `git_timeout_for` / `harden_git_env` / `timed_out_result` / `is_lock_error` / `is_corrupt_error` / `backoff_sleep` |
| `board` | 委譲公示板の入札選別とノード契約バージョン | `eligible` / `contract_compatible` / `declared_repo_ids` / `declared_workloads` / `holds_delegation` / `node_inflight` / `status_budget_gate` |
| `verifycontract` | verification plan / receipt の語彙と digest | `build_plan` / `plan_digest` / `plan_errors` / `build_receipt` / `receipt_errors` / `receipt_overall` / `normalize_criteria` / `criterion_id` / `canonical_json` |
| `nodebudget` | node-budget の読取・推定・`can_accept` | `read_config` / `iter_ledger_records` / `totals` / `compute_state` / `state` / `can_accept` / `engine_view` / `amigos_view` / `rate` |
| `commands` | 指示ドロップ（`commands/<name>.json`）の取り込み規約 | `pending` / `read_command` / `reject` / `write_receipt` / `prune_receipts` / `prune_rejected` |
| `interaction` | 人の介在（needs）の検証と決定的決着 | `normalize_spec` / `build_request` / `validate_response` / `resolve` |
| `executioncontract` | 候補ベース実行 3 契約の語彙と形 | `candidate_id` / `qualifications_errors` / `selection_policy_errors` / `execution_receipt_errors` |
| `executionresolver` | 実行直前の候補決定 | `resolve_execution` / `receipt_execution_decision` |
| `nodecontract` | agent-flow のノード語彙と結果契約 | `validate_node_data` / `operation_contract_errors` / `decide_candidates` / `local_patch_blockers` |
| `nodeid` | `node_id`（PC の身元）の正規化 | `normalize_node_id` / `default_node_id` |
| `repolocal` | git URL の正規化一致とローカルクローン解決 | `normalize_repo_url` / `same_repo` / `load_host_declaration` / `resolve_local` / `merge_local` |
| `heartbeat` | 心拍・鮮度 | `now_iso` / `parse_iso` / `is_fresh` / `is_lease_alive` |
| `methods` | `agent-tuning.methods` の条件評価と trial 割付 | `load` / `current_tier` / `role_for` / `matches` / `auto_selectable` / `select` |
| `vocab` | 完了語彙 | `is_terminal` / `is_terminal_read` |
| `promptcompose` | プロンプトキャッシュに適合する注入順の正規化 | `compose` |
| `promptrender` | プロンプトへ注入する構造化データの決定的な圧縮描画 | `dumps_prompt` / `render_table` |

### 2.2 ローカル推論アダプタ（9 モジュール）

| モジュール | 役割 |
|---|---|
| `ollama_adapter` | 引数解釈とモード分岐。`agent-ollama` の入口 |
| `ollama_loop` | ストリーミング呼び出しと bash / read ツールの最小ループ |
| `ollama_context` | 文脈上限の解決と、キャッシュ命中に強い使用量の追跡 |
| `ollama_events` | 進捗イベント（JSONL）。「遅い」と「死んだ」を区別するための証跡 |
| `ollama_skills` | スキルの明示・遅延読み込み |
| `ollama_tui` | デバッグ用の行指向ビュー |
| `ollama_replay` | 記録済みプロンプトのオフライン再生（測定の口） |
| `aider_adapter` | Aider の実測トークンを共通 usage 契約へ渡す。`agent-aider` の実体 |

詳細は [`docs/specs/agent-herd-spec.md`](./agent-herd-spec.md)。

---

## 3. 配布

agentcore は **独立配布しません**。`tools/agent-tools/install.sh` が各 zipapp へ同梱します。

| 成果物 | 中身 |
|---|---|
| `agent-project` / `agent-flow` / `agent-amigos` | エンジンのパッケージ ＋ agentcore |
| `agent-loop` | 同上（エンジン 4 本には含めないが同じ扱い） |
| `agent-audit` | 同上 |
| `agent-ollama` | agentcore（`--with-rich` で rich を同梱） |
| `agent-aider` | `aider_adapter.py` の単体コピー |

同梱するときは `tests/` を除き、`__pycache__` / `.pyc` も含めません（配布物にビルド環境の痕跡を
持ち込まない）。

**自己更新の sparse-checkout には `tools/agent-tools` を含める必要があります。** エンジンの
subdir だけを取ると installer が agentcore を束ねられず、自己更新が永久に失敗します
（各ツールの `TOOL_SUBDIR` が 2 パスを持つのはこのため）。

---

## 4. 制約

| 項目 | 値 |
|---|---|
| Python 下限 | 3.11 |
| 依存 | 標準ライブラリのみ（`rich` は agent-ollama の TUI でだけ任意） |
| 状態 | 持たない。ファイルの置き場は呼び出し元が引数で渡す |
| エンジン固有の知識 | 持たない。CLI 名・エンジン名で分岐しない |

---

## 5. 写しと、それを縛るテスト

agentcore の実装を**そのまま使えない場所**があり、写しを許したうえで機械に
突き合わせさせます。写しを置くこと自体は禁じません——禁じても消えず、見えなくなるだけです。

| 写し | なぜ写すか | 縛るテスト |
|---|---|---|
| agent-dashboard（JS）の CLI 定義ローダ・契約バージョン・git URL 正規化・フォージ決着推定 | 候補を出すたびに Python を起動すると描画がプロセス起動待ちになる | `tools/agent-dashboard/test/*-golden.test.js`（4 本。Python の正典を実際に読んで突き合わせる） |

かつては `agent-aider` 等の `~/.profile` 環境解決も単体ファイル配布の写しでしたが、
agent-herd の zipapp 統合で `hostenv` 1 実装になり、いまは
`agentcore/agentcore/tests/test_hostenv.py` が「同一オブジェクトであること」を縛ります
（写しの AST 比較から、写しが復活していないことの検査へ変わりました）。

---

## 6. テスト

**テストルートが 2 つあります。** 片方だけ走らせると半分が緑のまま素通りするので、CI も
両方を明示しています（`.github/workflows/ci.yml` の `agentcore` エントリ）。

```bash
cd tools/agent-tools/agentcore
python3 -m unittest discover -s tests            # 16 ファイル・261 件
python3 -m unittest discover -s agentcore/tests  # 24 ファイル・654 件
```

| ルート | 対象 | 主なファイル |
|---|---|---|
| `agentcore/tests/` | 共通契約の 1 実装（§2.1） | `test_board` / `test_commands` / `test_executioncontract` / `test_executionresolver` / `test_interaction` / `test_nodebudget` / `test_nodecontract` / `test_nodeid` / `test_promptcompose` / `test_promptrender` / `test_repolocal` / `test_verifycontract` / `test_methods` / `test_agentcli_files` / `test_agentcli_jsonvariant` / `test_compiler_resolver_handshake` |
| `agentcore/agentcore/tests/` | ローダの本体とローカル推論（§2.2） | `test_agentcli` / `test_protocol` / `test_transport` / `test_methods` / `test_aider_adapter` / `test_adapter_env_parity` / `test_ollama_{adapter,loop,context,events,skills,tui,replay}` |

`test_agentcli_files.py` / `test_agentcli_jsonvariant.py`（定義の探索順と `variants` の振り替え）と
`test_agentcli.py`（argv 組み立てとトリアージ）が別ルートに分かれている点に注意してください。
