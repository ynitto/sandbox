# 候補ごとの独立適合評価の実装

2026-09-21。ユーザー指示「次の改善を実施」に対応。

## 変更
- agent-herd select の local judge 段を、要求水準の判定＋候補ごとの価格を伏せた適合評価へ変更。
- 他の候補名、配置順、価格、site、quota、rank、rating内の平均トークン/rankを個別評価には渡さない。
- 最大適合度との差0.01以内、かつ既存min_confidence（既定0.6）以上だけを同等候補とし、既存audit順位を適用。最後の同点はcandidate IDで安定化。
- 要求水準はjudgeが意味を評価する。キーワードでクラウドを強制する規則はない。
- 要求水準の確信度不足・text-only出力はunknownとして渡す。
- 各fitのyes確率は別々の適合推定であり、候補間で合計1になる分布でも、実作業成功率でもない。
- 評価した候補で適合基準を満たすものがない場合、最安候補へ戻さずselected=nullで理由を返す。一部text-only＋既知低適合でも同様。
- 全てtext-only／judgeサービス利用不能など、能力を評価できなかった場合の従来auditフォールバックは維持。
- 追加判定は通常4候補で計5問い合わせ。合計75秒を上限とし、appの90秒選択タイムアウト内に収める。
- routeと本家Jevの判定方式は変えない。

## モデルと証拠
- model_sourceをexplicit/definition-default/provider-default-not-resolvedとして明示。
- クラウドCLIのモデルが未指定なら実モデル名を創作しない。現設定のクラウド候補はこの状態。
- ratingsは同じ用途だけ照合。異なるCLIの明示された同名モデルの評価は拒否し、完全CLI+モデル一致を優先する。
- 旧model-only記録はCLIが記録されていないことを明示する。
- 標本数0/欠落・不正な率・矛盾する重複は格付けに使わない。
- 出典・件数・条件を保持。既存auditのpass_rateはノード完了率であり、独立検証による品質合格率とは説明しない。
- qualification_refsは保持するが、生のqualification台帳は読み込まない。
- 現在のratingsは測定なし。能力の実測が新たに得られたとは扱わない。

## アプリ
- CLIのmethod=independent-fitを受け取り、「ローカル判定（候補ごとの適合評価）」と表示する。
- 保存セッションにもmethodを残す。
- selected=nullの理由をユーザーへ返す。既存の入力保持経路を使う。

## 検証
本番modelselect.selectの直接実行、開発6件×通常/逆順:
- 選択先の順序一致6/6、全てjudge段、例外0。
- 期待する配置一致4/6ずつ。元依頼は両順Claude。挨拶/翻訳/事実はOllama。
- 不具合調査と分散設計は両順Ollamaのまま。順序依存の低減であり、能力の過大評価が解消したとは言わない。
- 1.02〜7.80秒、中央値6.895秒（キャッシュの影響あり）。
- 独立プロトタイプの別6件でも通常/逆順の結果一致6/6。抽出の過剰cloud選択と改善提案の保留が各1件あり、期待配置は4/6ずつ。
- yes/noラベル自体の偏りと判断スコアの校正は未解決。

テスト:
- modelselect: 32件
- judge: 30件
- route: 18件
- model_selection_eval: 30件
- agent-app app/model-selection: 計77件
すべて通過。Pythonテストはworkspace .venvのPython3.14を使用。システムPythonでは既存tarfile API差分等があるため、Python3.14で確認した。

## 反映
~/.local/bin/agent-herd zipappにmodelselect.py、modelfit.py、herdcli.pyを同期。
他のエントリとhardlinkを保持。
バックアップ:
 /var/folders/8c/s6jh85ls4tq3fmzkl0jk5jcc0000gn/T/agent-herd-independent-backup-gop85yyu/agent-herd

実インストール済みCLIのselectに元依頼を入力:
selected=claude、model=""（未指定）、stage=judge、method=independent-fit、score=0.9265。
実行先CLIは起動していない。アプリ側表示の反映には再起動が必要。
以前のセッションで確定したモデルは維持されるため、新しい依頼で評価する。

## 再現記録
- integration-validation.py/.jsonl/.md: 本番経路
- independent-holdout.py/.jsonl/-report.md: 独立評価
- independent.py/.jsonl: 開発実験
