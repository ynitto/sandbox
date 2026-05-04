# Phase 5: 対話による精緻化

**目的**: Question Bank の未解決疑問をユーザーとの対話で解消し、ドラフトに反映する。

---

## 手順

### 5-1. 疑問の全体規模を提示する

```
未解決疑問の概要:
  合計: 28件
  ├── critical: 3件（未解決なら章が空欄のまま）
  ├── important: 15件（推測で進めているが確認推奨）
  └── nice-to-have: 10件（仕様書は成立するが品質向上に寄与）

カテゴリ別:
  business_rule: 8件
  architecture_decision: 6件
  security_compliance: 5件
  ...

対話を開始します。まず critical な疑問から確認します。
```

### 5-2. 優先度別クラスタリング

疑問を以下の順で提示する:
1. `critical`（全件）
2. `important`（カテゴリごとにまとめて）
3. `nice-to-have`（まとめて一覧提示し、必要なものだけ対応）

同一カテゴリの疑問はグループ化して提示する。「まとめてスキップ」も可能にする。

### 5-3. 個別疑問の解消ダイアログ

各疑問に対して以下の形式で提示する:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Q-014 [critical / business_rule]
「注文のキャンセル期限（発注後24時間）の根拠は何か？
 法的要件か、業務上のルールか？」
[REF: src/orders/cancel_policy.py:42]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

対処方法を選んでください:
  a) 推測で進める
     → 推測内容: 「業界標準のクーリングオフ期間を参考にしたと推測」
  b) 正解を入力する → [回答を入力してください]
  c) SME確認が必要（後で確認する）→ status を asked に変更
  d) 永遠に不明（未確定事項として明示）→ status を abandoned に変更
```

**各選択肢での処理**:

| 選択 | questions.json 更新 | ドラフト更新 |
|---|---|---|
| a) 推測で進める | `status: answered`, `answer: "[ASSUMED: ...]"` | `[CONFIDENCE: LOW]` + `[ASSUMED]` を記載 |
| b) 正解を入力 | `status: answered`, `answer: "{回答}"`, `answered_at` | 確定した内容で記述、`[CONFIDENCE: HIGH]` |
| c) SME確認が必要 | `status: asked` | `[ASK SME]` を残す |
| d) 永遠に不明 | `status: abandoned` | `[ASSUMED]` 推測を記載 or `[BLOCKED]` のまま |

### 5-4. critical疑問の [BLOCKED] 解除

`status: answered` になった critical 疑問に対応する `[BLOCKED]` 節を、得られた回答で記述する。

### 5-5. 進捗の表示

疑問が多い場合、一定件数ごとに進捗を表示する:

```
進捗: 12/28件 完了
  ✓ answered: 8件
  ○ asked: 3件（SME待ち）
  × abandoned: 1件

続けてよいですか？（「はい」「スキップして次のカテゴリへ」「ここで終了」）
```

---

## 完了条件

- [ ] 全 `critical` 疑問が `answered` / `asked` / `abandoned` のいずれかになっている
- [ ] ドラフトの `[BLOCKED]` 節が解消または `[ASSUMED]` で仮記述されている
- [ ] `questions.json` のステータスが更新されている
- [ ] ユーザーに精緻化の結果（answered/asked/abandoned の件数）を報告する
- [ ] ユーザーがPhase 6への移行を承認している
