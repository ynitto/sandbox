# feature: agent-audit — 監査タブ（収集の駆動とトークン利用量の表示）

[agent-audit](../../../../agent-audit/) CLI をダッシュボードから呼び、収集済みの
実行証跡からトークン利用量と実行品質を表示する制御面。dashboard（Windows）から
WSL 内の agent-audit を `wsl.exe -e bash -lc` 経由で呼ぶ（kiro-loop の exec と
同じ流儀。Linux ネイティブではローカルの bash）。

## 呼ぶのは LLM を使わない段だけ

| 操作 | サブコマンド | 備考 |
|---|---|---|
| 今すぐ収集 / 定期収集 | `collect` | 増分・決定的。main プロセス内で直列化する（agent-audit 側にロックが無いため） |
| トークン利用量 | `usage --period … --by … --json` | JSON 出力が dashboard 向けの契約。集計ロジックはこちらへ複製しない |
| 実行品質 | `stats --period … --json` | 同上 |
| 設定を点検 | `doctor` | 非ゼロ終了でも本文を返す（点検結果そのものが見たい情報） |

`extract` / `distill` などの LLM 段はこの画面からは呼ばない。LLM の消費リズムは
agent-audit 側の間隔・蓄積ゲート設定が正で、GUI から不用意に駆動しない。

## 設定（`config.agentAudit`・監査タブで編集）

- `command` … 起動コマンド。空なら PATH の `agent-audit`。未インストールなら
  `python3 ~/repo/tools/agent-audit/agent-audit.py` のようにインタープリタごと指定
- `distro` … WSL ディストロ名（Windows のみ）。空なら既定ディストロ
- `configPath` … `--config` で渡す agent-audit 設定ファイル（WSL 内パス）。
  源泉の一覧など agent-audit 自体の設定はこのファイルが正
- `auditDir` … `--audit-dir` で渡す収集データの保存先（WSL 内パス）。空なら `~/.agents/audit`
- `collectIntervalMin` … 定期収集の間隔（分）。0 で無効。アプリを開いている間だけ、
  全体ポーリングの周期で経過を確認して `collect` を起動する

グローバル引数（`--config` / `--audit-dir`）はサブコマンドの**前**に置く
（agent-audit の argparse はサブコマンド後のグローバル引数を受け付けない）。

## 表示の約束

実測トークン（CLI セッションログの報告値）と推定トークン（実行時間からの換算）は
性質が違うため**合算しない**（agent-audit の設計不変条件）。表でも別列で示す。
