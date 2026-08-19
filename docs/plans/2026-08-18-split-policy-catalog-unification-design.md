# split_policy をカタログへ寄せる設計検討（未実装）

対象の会話: `docs/plans/2026-08-15-workflow-feature-improvement-implementation.md` 第 1〜5 段の
質疑から。「split_policy が methods と同じ統一を受けていないのは一貫性を欠くのでは」という
指摘を受け、統合できるか・すべきかを検討した。本書は検討結果と推奨案のみで、**実装はしていない**。

## 現状の事実

`split_policy`（`behavior` / `file`）は `SPLIT_POLICY_DIRECTIVES[split_policy(policy)]` で
プロンプト文字列を選ぶことにしか使われていない（`patterns.py`・`orchestrate.py` を全数確認済み。
他の分岐・制限には一切関与しない）。この点で `granularity` や `tier` とは性質が違う——
`GRANULARITY_FACTORS` は並列数の倍率という**構造的な効果**を持ち、`tier_planning_granularity()`
は basic tier で auto を finest へ倒すという**制御分岐**も持つ。`TIER_PLANNER_DIRECTIVES` /
`TIER_EVALUATOR_DIRECTIVES` / `TIER_SPLIT_DIRECTIVES` も同様に、値がテキスト選択以外の意味
（実行 tier そのもの）を担っている。**split_policy だけが「値 → 固定テキストの選択」という
用途に完全に閉じている**。これが、methodと同じ形（`when` 条件付きでプロンプトへ差し込む宣言）に
寄せられるかもしれない、と考えた根拠。

## 検討した統合先

### 案 A: `trials` / `variants`（agent-tuning の A/B 実験プリミティブ）— 不適合と判断

`agentcore/methods.py` の `select()` を読むと、`trials`/`variants` は次の性質を持つ:

- **variant は必ず 2 つ**（`len(variants) == 2` を厳格に要求。3 択以上を表現できない）
- **選択は `assignment_key`（タスク/再試行の識別子）からのハッシュ**（`_variant_index`）で決まる。
  人が明示した意図ではなく、**A/B 比較の対照群をタスクごとに安定させるための決定的疑似ランダム**。
- **`enabled` で丸ごとオプトイン**——宣言しても常に効くとは限らない（測定の母数を絞る用途）。
- 目的は「効果を測る」ことで、`trial_rec` の記録（どの variant が実際に効いたか）も
  効果測定のための証跡として設計されている。

split_policy に要る性質はこの正反対で、**「振る舞い」か「file」かを利用者が明示的に選び、
その run では常にどちらか一方だけが確実に効く**（測定用の疑似ランダム割付ではない）。
`trials`/`variants` を流用すると、`--split-policy file` と指定したのに、ハッシュの都合で
`behavior` 側のテキストが選ばれる余地を生む——A/B 実験の道具を確定的な選択に転用する
カテゴリ違いの流用になる。**この案は推奨しない。**

### 案 B: 現状維持

`SPLIT_POLICY_DIRECTIVES` を今のまま Python の辞書に置く。追加の実装は不要。
欠点は、`integration-verify` や `design-document-format` と違って**文面をリポジトリ側で
上書きできない**——split_policy だけが「同梱の日本語文言を書き換えられない」例外として残る。

### 案 C（推奨）: 文面だけをカタログへ、選択の形は変えない

CLI/config の面は今のまま——`--split-policy` / 設定 `split_policy` は `behavior` / `file` の
2 値だけを受け付ける（enum を広げない。「file 境界分割」という選択肢を増やす要求は今のところ無い）。
変えるのは**文面の正典の置き場所**だけ:

- `methods/split-policy-behavior.json` / `methods/split-policy-file.json` を新設。
  `kind: rule` のまま（新しい kind は要らない）、`fragments` に現行の文言をそのまま移す。
  `when` は付けない（`selection`/`when` による自動適用・工程ごと選択のどちらの対象にもしない
  ——split_policy はこれまでどおり CLI/config の値で直接選ばれる、既存 2 系統とは違う第 3 の
  選ばれ方であることを維持する）。
- `split_policy_directive(policy)` は、まず該当 id（`split-policy-<policy>`）をカタログから引き、
  `fragments` の `role: "planner"` テキストがあればそれを使う。**カタログに無ければ
  `SPLIT_POLICY_DIRECTIVES` の同じ文言へフォールバックする**——`methods/` が存在しない・
  壊れている環境でも split_policy の指示が消えない保証を今と同じ強さで保つ
  （`integration-verify` が同じ理由でカタログ欠如時に「標準装備を諦める」フェイルクローズと
  違うのは、split_policy はエンジンの分解方針そのものであり、無指定の run が突然
  無方針になってよい機能ではないため）。
- リポジトリの `.agents/methods/split-policy-file.json` を同 id で置けば、そのリポジトリだけ
  「file 境界分割」の指示文を書き換えられる（例: 「必ずモジュール単位で」等、プロジェクト固有の
  粒度に寄せる）。

この案は新しい選択プリミティブを発明せず、`integration-verify`/`design-document-format` と
まったく同じ「カタログの id 引き＋フォールバック」パターンを再利用するだけなので実装コストが低い。
一方で、`granularity`/`tier` 系の辞書（`GRANULARITY_SCOPE_DIRECTIVES` 等）は対象外のままにする
——それらは値が構造的効果も持つ genuine なエンジンパラメータで、split_policy とは性質が違う。

## 推奨と次のステップ

**案 C を推奨する。** ただし優先度は高くない——今のところ split_policy の文面を
プロジェクトごとに変えたいという実際の要望は無く、案 C が解決するのは「一貫性の見た目」であって
機能的な不足ではない（現行の `SPLIT_POLICY_DIRECTIVES` は動作として何も壊れていない）。
実装するかどうかは、実際にプロジェクト固有の分割方針を書きたいという要望が出た時点で
判断するのがよい。実装する場合の見積り: `patterns.py` の `split_policy_directive` 変更、
`methods/split-policy-*.json` 2 件の新設、golden 更新、`SplitPolicyTests` への
カタログ優先・フォールバックのテスト追加——第 4・5 段と同程度の小さな変更。
