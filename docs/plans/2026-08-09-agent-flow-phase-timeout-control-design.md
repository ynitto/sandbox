# Agent Flow 実行フェーズ表示・タイムアウト制御設計

## 背景

agent-dashboard から開始した agent-flow run は、作業グラフが全件完了した後も自然文の検証や
最終処理を続ける。現在の run 契約にはその段階が無いため、画面では「100%・実行中」のまま
活動が見えず、正常な検証と停滞を区別できない。

また、エージェント CLI 1 回の上限は `agent-flow.yaml` の `agent_timeout` で全用途共通にしか
指定できない。検証だけを短くする、planner や worker には長い上限を与える、といった運用を
agent-dashboard から行えない。タイムアウトは一時障害として再試行されるため、既定 600 秒・
2 回再試行では、1 用途だけで最大約 30 分「100%・実行中」に見えることがある。

## 採用方針

1. run の現在段階を、グラフ進捗と独立した `phase` として明示する。
2. agent-dashboard の既存 agent-control と用途別エージェント編集を拡張し、agent-flow の
   共通タイムアウトと用途別上書きを管理する。
3. 作業グラフの進捗率、run の終端 status、現在 phase を混ぜない。
4. 固定検証コマンド、GitLab の決着待ち、lease、poll の運用タイマーは今回の対象外とする。

新しい設定ファイル、専用設定ページ、疑似的な 99% 表示、検証用の偽グラフノードは作らない。

## Run フェーズ契約

`runs/<run-id>/meta.json` に次の省略可能キーを追加する。

```jsonc
{
  "status": "running",
  "phase": "verifying",
  "phase_started_at": "2026-08-09T01:23:45Z"
}
```

`phase` は次の値を取る。

| phase | 意味 |
|---|---|
| `planning` | 作業グラフを計画している |
| `executing` | グラフ上の作業ノードを実行・待機している |
| `evaluating` | 継続、再計画、修復要否を判定している |
| `verifying` | verification plan を実行している |
| `finalizing` | receipt と最終結果を確定している |

orchestrator は各境界で `phase` と `phase_started_at` を同時に更新し、同じ内容を event に残す。
`status` の `running / done / failed / cancelled` 契約は変更しない。古い run のように `phase` が
無い場合、dashboard と CLI は status とグラフ状態から「実行中」または「完了処理中」へ縮退し、
未知の値でも画面を落とさない。

## Dashboard と CLI の表示

- run 一覧と詳細に、現在段階を日本語で表示する。
- グラフの数値は「作業ステップ 14/14」のように対象を明記し、run 全体の完了率とは呼ばない。
- `verifying` では「検証中」、`finalizing` では「結果を確定中」と表示する。
- heartbeat / lease が生きている限り phase は正常稼働として扱い、長時間同一 phase の警告は
  今回追加しない。
- agent-flow CLI の status にも同じ phase を表示し、dashboard と診断時の語彙を揃える。

## タイムアウト契約

`$AGENT_CONTROL_DIR/control.json` の flow workload と用途別 override に `timeout_sec` を追加する。

```jsonc
{
  "workloads": {
    "flow": {
      "timeout_sec": 900,
      "agents": {
        "verify": {"timeout_sec": 300},
        "planner": {"timeout_sec": 1200}
      }
    }
  }
}
```

対象は agent-flow が `run_agent(..., purpose=...)` で呼ぶ全用途である。

- `planner`
- `evaluator`
- `worker`
- `work / generate / classify / synthesize / verify / filter / judge / reduce / split / map`

解決順は次のとおりとする。

1. `control.workloads.flow.agents.<purpose>.timeout_sec`
2. `control.workloads.flow.timeout_sec`
3. CLI 定義 `agents/<name>.json` の専用 `timeout`
4. `agent-flow.yaml` の `agent_timeout`
5. 環境変数と組み込み既定 600 秒

用途がノード kind で用途別指定が無い場合、エージェント選択と同様に `agents.worker` の
`timeout_sec` へフォールバックする。control は各呼び出し直前に読むため、保存後に開始する run
だけでなく、稼働中 run の次のエージェント呼び出しから適用される。すでに動いている subprocess
の deadline は変更しない。

タイムアウトは「1 回の呼び出し上限」であり、`transient_retries` による再試行回数は変更しない。

## 設定 UI

全体設定の既存「エージェント」タブにある agent-flow ブロックを再利用する。

- 機能共通欄に「1回の上限（分）」を追加する。
- 「用途 / 担当ごとの変更」表は agent-flow の場合だけ同じ列を追加する。
- 空欄はその override を削除し、下位設定を継承する。
- 入力はネイティブの `input type="number"`、1 以上の整数、単位は分とする。
- `0 = 無制限` はハングを恒久化し得るため dashboard では提供しない。必要な場合は既存の
  agent-flow 設定ファイルを直接編集する。
- 各入力には可視ラベルまたは `aria-label` を付ける。
- 「再試行が有効な場合は、この上限が複数回適用される」ことを補足する。

保存時に分から秒へ変換し、main process 側でも有限・正数・整数を検証する。無効値では
`control.json` を書き換えず、既存の設定を保持したままエラーを返す。

## エラー処理と互換性

- `phase` と `timeout_sec` は additive な省略可能キーとし、古い writer / reader と共存する。
- 未知 phase は汎用の「実行中」表示へ縮退する。
- 不正な timeout は agent-flow 側でも無視して従来の上限へフォールバックし、run を落とさない。
- dashboard の保存境界では不正値を拒否し、誤設定を黙って保存しない。
- timeout 例外には実際に適用した秒数を記録し、再試行後の原因確認を可能にする。

## テスト

- Bus の phase 更新が時刻と event を記録し、終端 status を壊さないこと。
- orchestrator が planning / executing / evaluating / verifying / finalizing の境界を記録すること。
- dashboard が新旧 run の phase と作業ステップ進捗を正しく表示すること。
- agent-control が flow 共通値と用途別 `timeout_sec` を検証・深いマージ・削除できること。
- agent-flow の timeout 解決順、worker フォールバック、稼働中の control 再読込を確認すること。
- UI が分単位で保存し、空欄継承と不正値拒否を行うこと。
- 既存の retry 回数、固定コマンド timeout、GitLab / lease / poll の挙動が変わらないこと。

## 比較した案

| アプローチ | 実装コスト | リスク | 保守性 | 拡張性 | 学習コスト | 推奨度 |
|---|---|---|---|---|---|---|
| 共通値＋用途別上書きを agent-control に追加 | 低 | 低 | 高 | 高 | 低 | ★★★ |
| 既存 `agent_timeout` の共通値だけを dashboard に追加 | 最低 | 低 | 高 | 低 | 低 | ★★☆ |
| GitLab / lease / poll を含む全タイマーを公開 | 高 | 高 | 低 | 高 | 高 | ★☆☆ |

## 既存スキルとの関係

- `ui-designer`: 既存フォームを維持し、数値入力、ラベル、フォーカス、補足文の基本品質を確認する。
- `systematic-debugging`: 「100%・実行中」を表示だけで隠さず、phase 欠落という発生源を直す。
- `test-driven-development`: 契約と解決順を先に失敗テストで固定してから実装する。
- `self-checking`: agent-flow と dashboard の境界をまたぐ変更を最終確認する。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-09 |
| 決定者 | チーム |
| 採用案 | run phase の明示化＋agent-control による flow 共通値・用途別タイムアウト上書き |
| 却下案 | 共通 timeout だけの UI（verifier 等を個別調整できない）、全運用タイマーの公開（設定過多と誤操作リスク） |
| 主な理由 | 既存の run metadata、agent-control、用途別編集 UI を再利用し、停滞の見え方と役割別 timeout を最小の契約追加で解決できるため |
| トレードオフ | 固定検証コマンド、GitLab 待機、lease、poll の timeout は dashboard から変更できない |
| 再評価条件 | 運用タイマーの変更頻度が上がる、または同一 phase の停滞検知・通知が必要になった場合 |
