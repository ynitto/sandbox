# このソリューションの Dagu の DAG 例（一式）

Teams → SharePoint → change-watch → Copilot アプリ → GitLab → 通知メール → Teams 返信、という橋渡しを動かすのに要る Dagu のワークフロー例を、ここに全部そろえた。
設計は [docs/plans/2026-11-23-teams-sharepoint-bridge-design.md](../../../../docs/plans/2026-11-23-teams-sharepoint-bridge-design.md)。全体の構成と流れは [ARCHITECTURE.md](../ARCHITECTURE.md)。

| ファイル | 役割 | 間隔 | 必須 |
|---|---|---|---|
| `change-watch-teams-bridge.yaml.example` | ブリッジの本体。ローカル受信フォルダを change-watch のフォルダ取得元で見て、新しい依頼を 1 件受け取り（get）、検証して（check-file）、Copilot アプリへ提出し（submit）、見終えたと記録する（commit） | 毎分 | 必須 |
| `change-watch-teams-sharepoint-bridge-agent-maintenance.yaml.example` | 保持期間を過ぎた取得結果の整理（gc）。状態はエージェントごとに分けているので、このエージェント用に 1 本置く | 毎日 03:17 | 必須 |
| `copilot-app-job.example.yaml` | copilot-app-jobs を Dagu から呼ぶ例（依頼を置いて結果が出るまで待つ形）。**このソリューションでは通常は使わない**。提出は上のブリッジのワークフローが `run --no-wait` で行い、結果は GitLab の通知メール経由で Teams に返る。提出して結果まで Dagu で待ちたい別運用をするときの参考に同梱した | 任意 | 任意 |

毎分のブリッジ DAG は `change-watch.yaml` の `folder.path` が指すローカル受信フォルダを監視する。直結方式の設定は `change-watch.yaml.example` のコメントを参照。
フェッチャの起動は Windows のタスクスケジューラが担う。登録例は [エージェントの README](../README.md#フェッチャ方式の設定) にある。

## 実行の順序と関係

1. 毎分のブリッジが依頼を Copilot アプリの受け箱（`.copilot-jobs`）に提出する。アプリ側は、アプリの自動実行が受け箱を見に来て処理する（Dagu ではなくアプリ側の仕組み。copilot-app-jobs の README を参照）。
2. 結果の返りは Dagu を通らない。Copilot が GitLab に上げ → GitLab が通知メールを送り → Power Automate が Teams の元の投稿へ返信する（`../power-automate/` のレシピ）。
3. 整理（gc）は本体と独立に、毎日 1 回回ればよい。

agent-dots の tick / listen の DAG はこのソリューションには含まれない（agent-dots は常駐エージェントの別の設計で、ハブ issue 経由の返事の受け取りに change-watch の GitLab 取得元を使う。入口が SharePoint のフォルダである本ソリューションとは経路が違う）。

## 原本とコピー

- ブリッジの 2 本の原本は、このエージェントのフォルダの直下にある同名ファイル。`install.py install --agent teams-sharepoint-bridge-agent` が Dagu の dags フォルダへ置くのは直下の原本のほうで、ここのコピーは「ソリューション一式」として眺めるためのもの。
- `copilot-app-job.example.yaml` の原本は `tools/copilot-app-jobs/dags/` にある。
- コピーが原本とずれないことは、エージェントのテスト（`test/test_teams_bridge.py`）で確かめている。直すときは原本を直し、コピーも合わせること。
