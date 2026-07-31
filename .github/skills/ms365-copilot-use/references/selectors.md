# Copilot UI セレクタと調整方法

Microsoft 365 Copilot Chat の UI は不定期に変更される。`scripts/ask_copilot.py` のセレクタ候補リストを更新するための調査手順。

## セレクタ候補（現行）

`scripts/ask_copilot.py` の以下のリストを順に試している:

- `INPUT_SELECTORS`: 質問入力欄
- `ASSISTANT_MESSAGE_SELECTORS`: アシスタントの応答メッセージ（最後の要素を採用）
- `STOP_BUTTON_SELECTORS`: ストリーミング中に出る「停止」ボタン

最初にマッチした要素を採用する設計なので、新しい候補は **先頭** に追加していく。

## UI が壊れたときの調査手順

### 1. headed + スクリーンショットで状態を確認

```bash
python scripts/ask_copilot.py \
    --prompt "テスト" \
    --headed \
    --screenshot /tmp/copilot.png
```

`/tmp/copilot.png` を見て、入力欄や応答領域の位置を確認する。

### 2. DevTools でセレクタを探す

`--headed` 起動中のブラウザで F12 を開き、入力欄を Inspect する。安定して使えるのは以下の属性:

- `aria-label`, `aria-labelledby`
- `role="textbox"`, `role="article"`
- `contenteditable="true"`
- `data-testid`, `data-*` 属性

クラス名は難読化されているため避ける。

### 3. Playwright Codegen で対話的に取得

```bash
playwright codegen https://m365.cloud.microsoft/chat \
    --load-storage ~/.ms365_copilot_profile
```

CLI 上で操作を記録すると、Playwright が推奨するロケータを表示してくれる。
`get_by_role("textbox", name="...")` のようなロケータが見つかれば、それを `INPUT_SELECTORS` の先頭に追加する（このスクリプトは CSS セレクタのみ受け取るため、`page.get_by_role` を直接使うように改修してもよい）。

### 4. 応答完了の判定が崩れたとき

- 「停止」ボタンの `aria-label` が変わった可能性 → `STOP_BUTTON_SELECTORS` を追加
- ストリーミングが速すぎる / 遅すぎる → `--stable-seconds` を調整（既定 3 秒）
- 応答が複数のメッセージに分割される → `ASSISTANT_MESSAGE_SELECTORS` を見直す

## よくある UI 変更パターン

| 症状 | 推測される変更 | 対応 |
|---|---|---|
| 入力欄が見つからない | プレースホルダ文言の言語切替 / 構造変更 | aria-label / contenteditable で追加 |
| 応答が空 | DOM の入れ子が深くなった | `ASSISTANT_MESSAGE_SELECTORS` 候補追加 |
| 応答が無限に待たれる | 「停止」ボタンの属性変更 | `STOP_BUTTON_SELECTORS` 候補追加 |
| 引用が取れない | 引用 UI が aria-list ベースに変わった | `extract_citations` の JS を更新 |

## URL の代替候補

- `https://m365.cloud.microsoft/chat`（標準）
- `https://copilot.cloud.microsoft/`
- `https://www.office.com/chat`（リダイレクト経由）

テナント設定によってリダイレクト先が変わるため、`--url` で明示的に指定できるようにしている。
