---
name: webapp-testing
description: Playwright を使ってローカル Web アプリを検証するスキル。フロントエンド機能の確認、UI 挙動のデバッグ、スクリーンショット取得、ブラウザコンソールログ確認に対応する。「Webアプリをテストして」「画面挙動を検証して」などのリクエストで発動する。GitHub Copilot / Claude Code 両環境で利用可能。
license: Complete terms in LICENSE.txt
metadata:
  version: "1.0"
---

# webapp-testing

ローカル Web アプリをテストするときは、Python + Playwright で自動化スクリプトを作成する。

## 利用可能な補助スクリプト

- `scripts/with_server.py` - サーバー起動・待機・コマンド実行・停止を一括管理（複数サーバー対応）

**最初に必ず `--help` を実行して使い方を確認する。**
必要になるまでスクリプト本体を読まず、まずはブラックボックスとして実行する。コンテキストを汚染せず、安定した運用を優先する。

## 進め方の判断フロー

```
依頼内容 → 静的HTMLか？
    ├─ Yes → HTMLファイルを直接読み、セレクタを特定
    │         ├─ 成功 → そのセレクタで Playwright スクリプトを作成
    │         └─ 不十分/失敗 → 下の動的アプリ扱いに切り替え
    │
    └─ No（動的Webアプリ）→ サーバーは起動済みか？
        ├─ No → `python scripts/with_server.py --help` を実行
        │        補助スクリプトで起動管理しつつ Playwright を書く
        │
        └─ Yes → 偵察してから操作（Reconnaissance-then-action）
            1. 画面遷移して `networkidle` まで待つ
            2. スクリーンショット取得 or DOM確認
            3. 描画済み状態からセレクタを特定
            4. 特定したセレクタで操作を実行
```

## `with_server.py` 利用例

まず `--help` を確認し、その後に利用する。

**単一サーバー:**
```bash
python scripts/with_server.py --server "npm run dev" --port 5173 -- python your_automation.py
```

**複数サーバー（例: backend + frontend）:**
```bash
python scripts/with_server.py \
  --server "cd backend && python server.py" --port 3000 \
  --server "cd frontend && npm run dev" --port 5173 \
  -- python your_automation.py
```

自動化スクリプト側には Playwright ロジックだけを書く（サーバー管理は補助スクリプトが担当）。
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True) # Chromium は headless で起動
    page = browser.new_page()
    page.goto('http://localhost:5173') # サーバーは起動済み
    page.wait_for_load_state('networkidle') # 重要: JS実行完了まで待つ
    # ... 自動化ロジック
    browser.close()
```

## Reconnaissance-Then-Action パターン

1. **描画後DOMを確認する**:
   ```python
   page.screenshot(path='/tmp/inspect.png', full_page=True)
   content = page.content()
   page.locator('button').all()
   ```

2. 確認結果から **セレクタを特定** する

3. 特定したセレクタで **操作を実行** する

## よくある落とし穴

❌ 動的アプリで `networkidle` 待機前に DOM を調べる
✅ `page.wait_for_load_state('networkidle')` 後に調査する

## ベストプラクティス

- `scripts/` の補助スクリプトをまず検討し、ブラックボックスとして活用する
- 同期スクリプトでは `sync_playwright()` を使う
- 終了時は必ずブラウザを閉じる
- `text=` / `role=` / CSS / ID など説明的なセレクタを使う
- `page.wait_for_selector()` や `page.wait_for_timeout()` を適切に入れる

## 参照ファイル

- `examples/` - 典型パターンのサンプル
  - `element_discovery.py` - ボタン/リンク/入力要素の探索
  - `static_html_automation.py` - `file://` URL の静的HTML操作
  - `console_logging.py` - コンソールログの収集