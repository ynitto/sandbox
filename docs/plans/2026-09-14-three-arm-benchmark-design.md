# 固定3パターン比較の設計と実装

ユーザーが2026-09-14に承認した提案を、評価用の独立コマンドとして実装する。
実行契約と操作例の正典は [ORCHESTRATION.md](../../tools/agent-tools/eval/ORCHESTRATION.md)。

Singleはbaselineの1 session、Cascadeはstarterから公開gate失敗時にbaselineへ1回昇格、
Critiqueはbaselineから別familyのtool-less criticへ渡してbaselineで1回修正する。
同じ課題・commit・制限で最終のprivate判定を比較する。

再利用: worker seed/check、agentcore.agentcli、eval_io。
追加: orchestration_eval、orchestration_report、設定例、分岐・隔離・課金欠損のテスト。
既存methodsとagent-audit trialsは2群契約のまま維持する。

初版はT1〜T3の9実行smokeに対応。モデルの実ID・認証は実験環境で指定する。
12課題pilotには独立fixtureを追加し、その後に未使用課題で確認する。
本実装作業では有料モデル呼び出しによる品質・費用の実測結果を作らない。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-09-14 |
| 決定者 | ユーザー（設計案に対する実装依頼） |
| 採用案 | 既存部品を再利用する評価用アダプター |
| 却下案 | runtime導入、汎用workflow拡張、手動3群集計 |
| 主な理由 | 現行運用を変えず、構成別の実測を得る |
| トレードオフ | CLIによって費用が不明。criticは標準でClaude CLI、任意でChat Completions互換API |
| 再評価条件 | 独立fixtureの実測と未使用課題での再現が揃った時点 |

2026-09-15追記: ユーザーの「APIではなくローカルCLIで試す」指示により、標準criticをClaude CLIへ変更。
APIキー不要の通常ログインを利用し、ツールなし・空ディレクトリ・新規sessionでレビューする。
旧API manifestは互換性を保持する。
