# 本番 select 経路の独立検証

> 測定開始時点のレビューも含む。一部 text-only 時の棄権、rating 内の費用情報除外、合計75秒の期限は、その後の実装で対応済み。 [実装結果](implemented-independent.md)・[記録一覧](README.md)。

`integration-validation.py` は `modelselect.select(..., purpose="work", stages=("judge","audit"), quotas={}, judge_model="gemma4:e4b")` を直接呼ぶ。選択された CLI は起動しない。開発済み6ケースを通常順と逆順で1回ずつ評価した。結果は `integration-validation.jsonl`、集計は以下に記載する。

|依頼|通常順|逆順|期待する配置|
|---|---|---|---|
|挨拶|Ollama|Ollama|local|
|引用文の翻訳|Ollama|Ollama|local|
|事実質問|Ollama|Ollama|local|
|agent-appの追加機能を提案してほしい|Claude|Claude|cloud|
|調査・修正・回帰検証|Ollama|Ollama|cloud|
|分散ジョブ設計|Ollama|Ollama|cloud|

順序を逆にしても選択先は6/6一致。期待する配置との一致は両順4/6（合計8/12）。全12件 judge 段で選択、audit fallback なし、例外なし。原依頼はクラウドに変わったが、難しい依頼全般の適切な振り分けが達成されたとはいえない。

通常順で debug の fit は Codex .9820 / Claude .9788 / Ollama .9777、architecture は Claude .9760 / Codex .9745 / Ollama .9685。適合確率が全般に高く、最大値との差 .01 以内を同等とみなす処理で最安 Ollama が選ばれる。これらはタスク成功率ではなく未校正の judge 出力確率。

クラウド3候補のモデルは空文字（provider-default-not-resolved）。モデル名を解決した実行候補で測った結果ではない。モデル不明を明示する実装は機能しているが、具体的な実行モデルや用途に合った実測能力の不足は残る。

判定時間は1.02〜7.80秒、中央値6.90秒。逆順の簡単な依頼は約1秒で、キャッシュ等の影響を含み得る。同時実行・冷起動時の遅延保証ではない。

## コードレビュー（測定開始時点）

- 空候補は normalize_candidates が SelectError。空の配列による audit の IndexError は通常の select 入口では起きない。
- 要件判定が不明または閾値未満なら、unknown として各候補の適合評価へ渡す。全候補が有効な確率を返して閾値未満なら棄権する。
- 一部候補が text-only で、確率がある候補は全て低適合の場合、棄権せず audit に進める余地がある。否定済み候補の安価フォールバックに注意。
- relative_cost/site/quota/rank は適合判定入力から除かれるが、rating 内の average_tokens/rank は残る。能力判定で費用代理情報を避けるなら除外が望ましい。
- 閾値 .6 と同等幅 .01 は実成功率で校正済みの境界ではない。期待例に合わせた幅調整だけでは解決としない。
- 4候補なら要件1回＋適合4回の逐次評価。judge の既定 HTTP timeout は600秒で、途中障害時は全体時間が長くなる余地がある。
