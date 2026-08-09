# adhoc-flow — クイック実行（プロジェクト非依存の agent-flow 投入・フロービルダー）

計画 [2026-08-08 資源効率計画](../../../../../docs/plans/2026-08-08-agent-tools-resource-efficiency-plan.md)
のトラック M（S21・S22）。効く柱・原則: **柱2×柱3 / C3・C7** — 軽い仕事にプロジェクト一式
（charter・バックログ・受入基準）を強いず、既存契約への入口だけを足す。

## なにをする面か

- **投入**: 要求テキスト（+ 任意のフロープリセット）から agent-flow の単発 run を起動する。
  書くのは公式契約だけ——`<bus>/inbox/<run-id>.json`（submit_request 契約）。プリセットの
  ノード列は同契約の `plan` フィールド（ユーザー定義フロー）として運び、agent-flow の
  orchestrate が planner を通さず検証だけで実行する（検証の正典はエンジン側
  `plan_strategy_user`。不正な plan はフォールバックせず失敗終端する）。
- **フロービルダー**: テンプレート（7 パターンの形の種）からノード列を組み、ノードごとに
  kind・依存・エージェント CLI・モデルを設定できる。F17 の手法（`methods/` カタログ +
  独自手法）をフローに添えると、投入時に **run 専用の agent-tuning スナップショット**
  （`~/.agents/flow/tuning/<run-id>/tuning.json`、`source: methods/<id>@<hash>` 付きの複製）を
  書き、`AGENT_TUNING_DIR` で agent-flow に読ませる。選択があるとその run では端末全体の
  tuning.json は読まれない（置換。どの手法が効いたかを run 単位で決定的にするため）。
- **プリセットの保存**: ビルダーの成果物は viewer の config.json（`adhocFlow.presets`）に
  持つ。宣言データだけで、新しい状態ファイルは作らない（C7）。入力（要求テキスト）を
  変えて使い回す口は goal 中の `{{request}}` 置換（置換の実装はエンジン側の 1 か所）。
- **監視**: run の読み取りは agent-project feature の flow.js（バスパーサ）をそのまま再利用
  する（バスの読み手を 2 実装にしない）。既定バスは `~/.agents/flow/bus`（プロジェクトの
  バスとは分離。アドホックはバックログの状態に一切触れない）。
- **昇格（S22）**: run の成果を正式な仕事にするときは、既存の inbox 投函契約
  （`dashboard:enqueue` と同じ `actions.enqueueToInbox`）で agent-project のタスクとして
  起票する。宛先は実行エンジンが担当しているプロジェクトのみ（C1）。

## アドホックは done を名乗らない（C5）

受入基準も verify も無い実行の成果は「終了（未検収）」と表示し、完了扱いにしない。
バックログの状態ファイルは一切書かない。正式化は昇格導線だけが口で、昇格したタスクは
通常の受入基準と verify を通る。

## 実行系

`agent-flow --bus <bus> --run-id <id> run --from-inbox` を nohup で切り離して起動する
（run は heartbeat・park 監視・停滞回収を自分で持つ自己完結の実行なので、dashboard の
生存に依存しない——C6）。新しい実行系・デーモンは作らない。ログは
`~/.agents/flow/logs/<run-id>.log`。

## 設定（config.json の `adhocFlow`）

| キー | 意味 |
|---|---|
| `busDir` | アドホック run のバス（既定 `~/.agents/flow/bus`） |
| `agentFlowCommand` | agent-flow の起動コマンド（既定: PATH の `agent-flow`） |
| `distro` | WSL ディストロ名（Windows のみ） |
| `tuningRoot` | 手法スナップショットの置き場（既定 `~/.agents/flow/tuning/`） |
| `presets` | 保存済みフロー定義（ビルダーの成果物） |

## IPC

`adhocFlow:overview` / `adhocFlow:run` / `adhocFlow:submit` / `adhocFlow:resubmit` /
`adhocFlow:cancel` / `adhocFlow:deleteRun` / `adhocFlow:savePreset` /
`adhocFlow:deletePreset` / `adhocFlow:promote`

テスト: `node test/adhoc-flow.test.js`
