# feature: agent-audit — 監査（収集の駆動とトークン利用量の表示）

[agent-audit](../../../../agent-audit/) CLI をダッシュボードから呼び、収集済みの
実行証跡からトークン利用量と実行品質を表示する制御面。dashboard（Windows）から
WSL 内の agent-audit を `wsl.exe -e bash -lc` 経由で呼ぶ（agent-loop の exec と
同じ流儀。Linux ネイティブではローカルの bash）。

## 置き場所: 全体設定の「利用状況」（独立タブは持たない）

扱う数字は**この端末のもの**で、選択中プロジェクトとは無関係だ。独立タブにすると
プロジェクトのタブ列に無関係なものが並ぶ。そこで画面側は renderer コアの
`registerGlobalSettingsPanel('usage', …)` で**節へ面を差し込む**（`registerFeatureTab` と
同じ形の登録簿）。面は自分の容れ物（`global-settings-slot-agent-audit`）だけを描き直す
——全体設定ごと描き直すと、他の節で入力中の欄が飛ぶ。

集計の取得は節が表示されたとき（`reveal`）に初回だけ走る。利用状況を開いていない
あいだは CLI を起こさない。

## 利用状況の数字はここが 1 か所で出す

当初この節には集計が 2 つ並んでいた——画面がノード予算の台帳から自分で足した「利用量」と、
agent-audit が集計した「実測のトークン利用量」である。台帳（`budget-ledger`）は agent-audit の
源泉の 1 つで、そこへ CLI のセッションログを突き合わせた分だけ agent-audit の集計の方が確かだ。
同じ話題の数字を 2 つ置く理由が無いので、**表示は agent-audit の集計へ一本化**した
（コンセプト正典 C7: 同じ判断の根拠を 2 つ置かない）。

- 合計・機能別（workload）・エージェント別（agent_cli）は `agentAudit:summary`（`usage --json` を
  2 軸ぶん）から描く。合計は main 側で畳む——表ごとに画面が足すと、片方の取得だけ失敗したときに
  食い違った数字が並ぶ
- **手動上限はノード予算が正**（`node-budget` の `tokens` / ワークロード・CLIの実効上限）。
  CLI別上限は「利用状況 → 設定」で保存する
- エージェント別の利用量の右に、CLI quota の使用率・`reset_at` と100%基準の棒をまとめる。
  70%未満は緑、70%からオレンジ、90%から赤にし、色だけに頼らず使用率と復旧日時も棒の上へ出す。
  `allocation.agents.<cli>.max_tokens` は手動上限として続けて表示し、tier ごとのモデル候補は詳細へ置く。
  同じquotaを Resource Controller の候補選択も読む。70%から同tierの緑候補を優先し、90%からは
  下位tierに緑/オレンジ候補がある場合だけ降格する。80%未満へ回復し最小保持時間を過ぎると、
  予算方針が決めたtierへ一段ずつ戻る。全候補が制限中ならquota所有のpauseにし、復旧時だけ解除する。
  復旧日時の完全表記と取得元は tooltip に置く
- ゲージは**期間が予算の期間と一致するときだけ**残量を出す。上限はその期間の消費に掛かるので、
  別期間の集計へ重ねると嘘の残量になる
- agent-audit が使えない・まだ収集していない端末では、台帳だけの集計（`orchBudgetPanelHtml`）へ
  フォールバックし、**どちらを見ているかを画面に明示する**（黙って別の数字に差し替えない）

## 呼ぶのは LLM を使わない段だけ

| 操作 | サブコマンド | 備考 |
|---|---|---|
| 利用状況を収集 / 定期収集 | `collect` | 実行記録と対応CLI quotaを増分収集。main プロセス内で直列化する |
| トークン利用量 | `usage --period … --by … --json` | JSON 出力が dashboard 向けの契約。集計ロジックはこちらへ複製しない |
| 利用状況の 1 枚 | `usage`（`--by workload` と `--by agent_cli`） | main 側の `summary()` が 2 軸を 1 往復で取り、合計を畳む |
| 実行品質 | `stats --period … --json` | 同上 |
| 設定を点検 | `doctor` | 非ゼロ終了でも本文を返す（点検結果そのものが見たい情報） |

`extract` / `distill` などの LLM 段はこの画面からは呼ばない。LLM の消費リズムは
agent-audit 側の間隔・蓄積ゲート設定が正で、GUI から不用意に駆動しない。

## 設定（`config.agentAudit`・全体設定「利用状況」の「収集の設定」で編集）

- `command` … 起動コマンド。空なら PATH の `agent-audit`。未インストールなら
  `python3 ~/repo/tools/agent-audit/agent-audit.py` のようにインタープリタごと指定
- `distro` … WSL ディストロ名（Windows のみ）。空なら既定ディストロ
- `configPath` … `--config` で渡す agent-audit 設定ファイル（WSL 内パス）。
  源泉の一覧など agent-audit 自体の設定はこのファイルが正
- `auditDir` … `--audit-dir` で渡す収集データの保存先（WSL 内パス）。空なら `~/.agents/audit`
- `collectIntervalMin` … 定期収集の間隔（分、既定5分）。0 で無効。アプリを開いている間だけ、
  全体ポーリングの周期で経過を確認して `collect` を起動する

グローバル引数（`--config` / `--audit-dir`）はサブコマンドの**前**に置く
（agent-audit の argparse はサブコマンド後のグローバル引数を受け付けない）。

## 表示の約束

実測トークン（CLI セッションログの報告値）と推定トークン（実行時間からの換算）は
性質が違うため**合算しない**（agent-audit の設計不変条件）。表でも別列で示す。
