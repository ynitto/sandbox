# 自動選択の判定傾向調査（2026-09-21）

> 変更前の調査記録。本文の「現行」「本番には追加していない」は調査時点を指す。 [実装結果](implemented-independent.md)・[記録一覧](README.md)。

## 結論
ルールベースのクラウド限定は撤回。現行判定器は難易度を直接尋ねれば区別するが、現行selectの質問では難しい依頼もローカルに偏った。まず質問・能力情報を評価すべきで、キーワード強制に進む根拠にはしない。

## 実験条件
- 本家Jevは設定・実験プロセス環境ともAPIキー未設定（enabled=false）。本家Jev APIの傾向は未測定。
- 実測対象は稼働中のローカルjudge、gemma4:e4b。modelselect/route/judgeのソースはインストール済みzipappと一致。
- 候補は実設定に合わせ cursor、codex、claude、ollama/gemma4:e4b。候補定義のクラウド3種はrelative_cost=1、ローカルは0。全候補がtool-loop。
- capabilityの実測格付け、context_tokensは候補記述に無い。quota/budget/ratingsは投入していない。稼働可否の再計測ではなく、候補選択そのものの比較。
- selectは現行のstate/questionを生成してjudge.evaluateへ渡した。CLIの実作業は起動していない。
- route比較は候補タスク・ワークフロー・スキルを空にした固定条件。元セッションの全候補状態は保存されておらず、当時の完全再現ではない。
- 8依頼 × select/難易度/routeの24評価。追加10評価で反復、順序、指示文、難易度情報を比較。routeはhandling/routineの2質問なのでOllama呼び出しは合計42回。
- 難易度質問は調査・多段推論・設計判断・根拠ある提案をcomplexと定義した実験用の問い。本番には追加していない。語を含むだけの翻訳を対照群に入れた。
- 数値は選択肢ラベルのlogprobsを正規化した値で、実作業の正答率・成功率ではない。表示1.0000は丸め値。少数の便宜標本であり一般的精度とは扱わない。

## 基本比較
|依頼|直接尋ねた難易度|難易度の選択値|select|selectの選択値|route handling|
|---|---|---:|---|---:|---|
|こんにちは|simple|1.0000|ollama/gemma4:e4b|1.0000|answer (0.9997)|
|日本の首都はどこですか|simple|0.9985|ollama/gemma4:e4b|1.0000|answer (0.9954)|
|agent-appの追加機能を提案してほしい|complex|0.9998|ollama/gemma4:e4b|1.0000|answer (0.6609)|
|agent-appの既存コードと仕様を調査し、未実装の追加機能を3案提案してほしい。各案の根拠、影響範囲、リスク、優先順位も示して。|complex|1.0000|ollama/gemma4:e4b|1.0000|answer (0.7210)|
|agent-appで送信すると画面が崩れる。コードとログを調査し、原因を特定して修正し、回帰テストで検証して。|complex|1.0000|ollama/gemma4:e4b|0.9999|converse (0.9943)|
|「追加機能を提案してほしい」を英訳して|simple|0.9966|ollama/gemma4:e4b|1.0000|answer (0.9882)|
|複数ノード間のジョブ重複実行を防ぐ設計を提案して。ネットワーク分断、再起動、冪等性、既存DBとの整合性を考慮して。|complex|1.0000|ollama/gemma4:e4b|1.0000|answer (0.6638)|
|Suggest additional features for agent-app|complex|0.9992|ollama/gemma4:e4b|1.0000|answer (0.9688)|

挨拶・事実質問・引用文の翻訳はsimple、5種類の提案・調査・設計はcomplex。selectは全8件をOllamaにした。
元依頼のrouteは今回answer=0.6609で、既定閾値0.6を超える。当時の「未決定」は再現していない。候補状態など条件差があるので、過去の未決定原因は断定しない。

## 条件を一つずつ変えた比較
|依頼|条件|選択|選択値|
|---|---|---|---:|
|proposal_short|repeat1|ollama/gemma4:e4b|1.0000|
|proposal_short|repeat2|ollama/gemma4:e4b|1.0000|
|proposal_short|reversed|ollama/gemma4:e4b|1.0000|
|proposal_short|capability_first|ollama/gemma4:e4b|0.6780|
|proposal_short|difficulty_context|ollama/gemma4:e4b|0.9994|
|debug|repeat1|ollama/gemma4:e4b|0.9999|
|debug|repeat2|ollama/gemma4:e4b|0.9999|
|debug|reversed|ollama/gemma4:e4b|1.0000|
|debug|capability_first|cursor|0.8661|
|debug|difficulty_context|ollama/gemma4:e4b|0.9996|

- 同条件で追加2回ずつの結果は一致。ただし既定の決定的生成とキャッシュを含むため、独立した統計サンプルには数えない。
- 候補順を逆にしてもOllama選択。単純な先頭/末尾偏重だけでは説明できない。
- policyのみを「能力・検証を先に比較し、同等能力の候補間でコストを比較」に変更すると、不具合調査はCursor 0.8661へ変化。短い提案はOllamaのままだが1.0000から0.6780へ低下。
- 「complex」という判断結果をstateへ足すだけでは提案・不具合調査ともOllamaのまま。

## 解釈と次の評価案
難易度の質問では区別できているので「モデルが難しさを理解できない」とは結論できない。現行policyの安価優先と、候補間の具体的能力差が記載されていないことが偏りの有力な要因。policyの影響は比較実験で確認できたが、能力情報の追加効果は未測定。
難易度ラベルの追加だけでも不十分。次は能力を優先する質問と、実測に基づく候補別の対応能力・制約を比較する。未知依頼で評価し、簡単な依頼までクラウドに寄らないかも見る。
本家Jevを評価するには接続設定が必要。同じstate/questionsと評価依頼群を使い、ローカルjudgeとは別集計する。
本番の選択方針・閾値は変更していない。先に追加したrequiresCloudと専用テストは撤回し、元のモデル選択テスト10件が通過。

## 再現・生データ
- probe.py: 基本比較スクリプト（results.jsonlへ追記）
- contrast.py: 条件比較スクリプト（contrast.jsonlへ追記）
- results.jsonl / contrast.jsonl: 入力state、質問、全選択分布を保存
