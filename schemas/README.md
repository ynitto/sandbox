# schemas/ — ツール横断の共通スキーマ（repos / task / node-budget / mission / amigos-command / delivery）

agent-project・agent-flow・codd-gate・agent-amigos が**データ契約だけで**結合するための独立スキーマ。
ツール同士は互いの実装を知らず、ここで定義する形式だけを読む/書く（結合は常に一方向×データ）。

| スキーマ | 何の契約か | 所有者（変更の主導） |
|----------|-----------|--------------------|
| [`repos.schema.json`](repos.schema.json) | リポジトリレジストリ（identity = **(url, path, base)**＝パス＋ブランチで一意） | 共有（本ディレクトリが正典） |
| [`task.schema.json`](task.schema.json) | 制御層タスク（バックログ 1 件）の JSON 表現 | kiro-projects（Markdown 形の正典は `tools/kiro-projects/backlog.md.example`） |
| [`node-budget.schema.json`](node-budget.schema.json) | ノード単位の実質実行時間の予算（`$AGENT_BUDGET_DIR`＝既定 `~/.agent/budget/` の config.json ＋ ledger/<YYYYMMDD>.jsonl） | 共有（本ディレクトリが正典。初出は agent-amigos 設計書 §3.3） |
| [`mission.schema.json`](mission.schema.json) | 協働ミッションの公示（agent-amigos の `post --roles` に渡すミッション + 役割ミッション表）。バスへ書かれる読取契約（外部ビュアーが読む `mission.json` / `MANIFEST.json` / `final.json` / `cancelled.json`）は `$defs` に文書化 | agent-amigos（検証は stdlib パーサ `normalize_mission`。スキーマは文書化とテスト突き合わせ — enum/既定値の一致をテストで担保） |
| [`amigos-command.schema.json`](amigos-command.schema.json) | agent-amigos への指示ドロップ（`<home>/.agent/agent-amigos/commands/*.json` — post / claim / assign / accept / reject / cancel / say）。投函側は人・agent-dashboard、取り込み側は常駐デーモン | agent-amigos（取り込みの正典は `agent_amigos/commands.py`。コマンド一覧の一致を両側のテストで担保 — Python `CommandSchemaTests` / dashboard `amigos.test.js`） |
| [`delivery.schema.json`](delivery.schema.json) | agent-amigos の納品書（accept 時にオーナーホームの `deliveries/<mid>/delivery.json` へ書かれる受領記録）。バスの `MANIFEST.json` が integrator の組み立て記録（gc 対象）なのに対し、こちらは受入という事実と搬出先の永続記録 | agent-amigos（書き手は owner デーモン。読み手は agent-dashboard の納品一覧と `agent-amigos deliveries`） |

## node-budget — 誰がどう読む/書くか

- **各ツール（記帳・抑制側）**: 1 回の agent CLI 実行ごとに ledger へ 1 行追記
  （O_APPEND・追記専用）し、実行前に「合計消費 ≥ 上限」なら新規実行を控える。
  workload は `routine`（kiro-loop / agent-loop 定常業務）/ `project`（agent-project）/
  `flow`（agent-flow）/ `amigos`（agent-amigos）。**全ワークロード実装済み**:
  - `amigos`: ターン前チェック → 超過中は amigo を paused にし owner へ通知。ターンの
    CLI 実行秒を記帳。
  - `flow` / `project`: LLM 単一チョークポイント（`run_agent` / `_run_agent_cli`）で
    実行前チェック → 超過は `[agent-error:quota] [node-budget]` として既存の環境要因
    フローに乗る（run 即終端・リトライを焼かない／裁定を呼ばず needs へ）。成功実行の
    実測秒を記帳。
  - `routine`（kiro-loop）: スケジューラがサイクル先頭でチェックし、超過中は定期送信・
    webhook キューの dispatch を停止（10 分ごとに警告ログ・キューは保持）。実行秒は
    **セマフォスロットの保持時間**（送信 → 完了検知）で近似して解放時に記帳する
    （`max_concurrent <= 0` でセマフォ未使用のときは計測点が無く記帳されない、が既知の制約。
    タイムアウト強制解放は実行時間として数えない）。`agent-loop`（未統合クローン）へは
    次回のクローン同期で反映する。
- **管理面（agent-dashboard / 各ツール CLI）**: config.json を書き（合計上限
  `execution_minutes`・期間 `period: day|month|total`・ワークロード別内訳上限。
  **0 = 無制限**）、ledger を読んで消費内訳を表示する。依頼側・請負側どちらの
  ノードでも同じ契約（CLI 例: `agent-amigos budget node --limit-minutes 240`）。
  実装済み: agent-dashboard の **Amigos タブ**（`tools/agent-dashboard/src/features/amigos/`）
  がこの契約でワークロード別消費の表示と上限編集を行う。
- 超過チェックはロックなしの読み合計で、上振れは「進行中実行 × 同時実行数」に有界。
  台帳は日付ファイル分割なので日次/月次の集計と gc（古い日付の削除）が安い。

## repos — 誰がどう読むか

```yaml
# repos.yaml（YAML は PyYAML 任意・無ければ JSON。トップレベルは「repo 名 → エントリ」）
app:
  url: git@example.com:team/app.git
  desc: アプリ本体（API・UI）
  base: main
  target: develop        # 省略時 base
  owns: [src/**]         # kiro-projects: 書込先ルーティングの根拠（無指定=参照リポジトリ）
  docs: [docs/**, README.md]   # codd-gate: 分類グロブ（他ツールは無視）
  tests: [tests/**]
shop-api:                # モノレポ: 同じ url を path 別に分ければ別エントリ（identity は url+path+base）
  url: git@example.com:team/shop.git
  path: apps/api
  base: main
  desc: API 側
```

- **kiro-projects**: 手書きの `<project>/repos.{yaml,yml,json}` があればそれをレジストリの正として
  読む（charter.md の `## repos` は**互換入力**。内部的にはこのスキーマの形へ正規化して引き回す）。
  手書きが無ければ **charter の `## repos` から `repos.json` を自動生成**する（`_meta.generated_from`
  マーカー付き・正は charter のまま追従。手で管理するなら `_meta` を消す）——外部ツールへは常に
  「このスキーマのファイル」として渡る。
- **agent-flow**: `--workspace` / `--reference` の値は本スキーマの**1 エントリの射影**
  （`{url, path, base, target, desc}`）。kiro-projects がレジストリから選んで渡す。
- **codd-gate**: `--repos <file>`（設定 `repos_file`）でこのファイルを読む（**charter は読まない＝
  kiro-projects から完全独立**）。`docs/tests/code/dir` は codd-gate 拡張キー（他ツールは未知キー
  として無害に無視——additionalProperties: true が互換性の要）。
- **メタデータ予約**: トップレベルの `_` 接頭辞キー（例 `_meta`）はメタデータ予約で、全消費側が
  repo エントリとして扱わずスキップする。

## task — 誰がどう読む/書くか

```json
{"id": "codd-doc-x-1a2b3c", "title": "src/util.py の変更を docs/util.md へ反映する",
 "verify": "codd-gate check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh",
 "priority": 1, "paths": "docs/util.md", "expect": "changes"}
```

- **kiro-projects が契約の所有者**（読む側）: `enqueue --json` / `inbox/*.json` / `intake_cmd` の
  stdout がこの形式。**未知キーは保持**（前方互換）。verify 無しは inbox=人の triage 行き。
- **供給側**（codd-gate `tasks`・webhook/issue 抽出等）は**この共通スキーマへ直接出力する**
  （特定ツール向け「アダプタ」ではない——スキーマを読める消化先なら何でもよい）。自ツールの内部形式
  （codd-gate なら所見 JSON）を正とし、スキーマ外の消化先へはそこから変換する。
- **agent-flow は対象外**: agent-flow のタスクグラフノード `{id, goal, deps, kind}` は実行層内部の
  分解ステップで層が違う（agent-project → agent-flow の境界は「要求文＋workspace」であって
  task spec ではない）。

## 互換性の規則

1. **未知キーは無視せず保持する**（task）／**無害に無視する**（repos）。キーの削除・意味変更は不可、
   追加のみ可（additive evolution）。
2. スキーマを変えるときは本ディレクトリを先に更新し、各ツールの正典
   （`backlog.md.example` / `charter.md.example` / 各設計書）から参照を張る。
3. 検証は各ツールの stdlib パーサが行う（jsonschema への実行時依存は持たない。スキーマファイルは
   契約の文書化とテストでの突き合わせに使う）。
