---
name: ms365-copilot-use
description: Microsoft 365 Copilot Chat（m365.cloud.microsoft）を Playwright で操作し回答を Markdown/JSON で取得する。「M365 Copilot に聞いて」「Microsoft 365 Copilot に質問」「M365 Copilot で検索」「M365 Copilot の回答を保存」「M365 Copilot の履歴」で発動。
metadata:
  version: 0.1.0
  tier: experimental
  category: integration
  tags:
    - microsoft-365
    - copilot
    - playwright
    - browser-automation
    - sso
---

# ms365-copilot-use

`playwright` CLI で導入した Chromium を Python から駆動し、Microsoft 365 Copilot Chat（`https://m365.cloud.microsoft/chat`）にプロンプトを送って回答を取得する。社内ネットワーク上の Entra ID SSO が効く環境を前提とし、永続プロファイル（`user-data-dir`）にセッションを保持して 2 回目以降はサインイン不要にする。

セットアップ手順: [`references/setup-guide.md`](references/setup-guide.md)
UI が変わったときの調整方法: [`references/selectors.md`](references/selectors.md)

---

## 前提条件

```bash
pip install playwright
playwright install chromium
```

- `playwright` CLI（pip 同梱）で Chromium バイナリを取得する。
- 既定の永続プロファイル: `~/.ms365_copilot_profile`（`--user-data-dir` で変更可）。
- 社内 PC（Entra ID 参加）でブラウザを開くとサインインなしに Copilot が利用できる環境であること。SSO が効かない場合は初回のみ headed 起動で手動サインインする。

---

## 基本ワークフロー

### Step 1: 初回サインイン（headed 起動）

社内 SSO が自動で通る環境ならスキップしてよい。明示的にサインインしたい場合は `--login` で headed を強制する:

```bash
python scripts/ask_copilot.py --login
```

ブラウザが開いたら、Copilot Chat の入力欄が表示されることを確認して閉じる。`~/.ms365_copilot_profile` にセッションが永続化される。

### Step 2: 質問を送って回答を取得

```bash
# 最小: 標準出力に Markdown で出力
python scripts/ask_copilot.py --prompt "今期の社内通達を要約して"

# Markdown と JSON に同時保存（JSON は会話履歴形式）
python scripts/ask_copilot.py \
    --prompt "セキュリティポリシーの更新点を教えて" \
    --output-md ./answer.md \
    --output-json ./conversation.json

# ファイルからプロンプトを読み込む
python scripts/ask_copilot.py --prompt-file ./question.txt --output-md ./answer.md

# 標準入力からプロンプトを読み込む
echo "週次レポートのテンプレを作って" | python scripts/ask_copilot.py --output-md ./answer.md

# 初回サインイン時など UI を確認したい場合は headed で実行
python scripts/ask_copilot.py --prompt "..." --headed

# タイムアウトを延長（既定: 応答待ち 180 秒、UI 描画待ち 60 秒）
python scripts/ask_copilot.py --prompt "..." --response-timeout 300
```

### Step 3: 出力を活用

- `--output-md`: 最終回答を Markdown として保存。CodeBlock / 表 / リスト構造は DOM の見出し・list 要素から再構成する。
- `--output-json`: 1 ターン分の会話履歴を JSON で保存。引用 (citations) があれば `citations: [{title, url}]` として含める。
- 何も指定しなければ最終回答を標準出力に Markdown で書き出す。

---

## オプション一覧（`ask_copilot.py`）

| オプション | 既定 | 説明 |
|-----------|------|------|
| `--prompt TEXT` | — | 送信するプロンプト。未指定時は `--prompt-file` か標準入力から読む |
| `--prompt-file PATH` | — | プロンプトをファイルから読む |
| `--url URL` | `https://m365.cloud.microsoft/chat` | Copilot Chat の URL |
| `--user-data-dir PATH` | `~/.ms365_copilot_profile` | 永続プロファイルの場所 |
| `--output-md PATH` | — | 回答 Markdown の保存先 |
| `--output-json PATH` | — | 会話 JSON の保存先 |
| `--headed` | False | headed モードで起動（既定は headless） |
| `--login` | False | サインイン用に headed で開いて、入力欄が現れるまで待って終了する |
| `--response-timeout SEC` | 180 | 応答完了まで待つ最大秒数 |
| `--ui-timeout SEC` | 60 | 入力欄の出現を待つ最大秒数 |
| `--stable-seconds SEC` | 3 | 応答テキストが変化しない時間がこの値を超えたら完了とみなす |
| `--channel msedge\|chromium` | `chromium` | Playwright で使うブラウザチャンネル。社内ポリシーで Edge が必要なら `msedge` |
| `--screenshot PATH` | — | 回答画面のスクリーンショットを保存 |

---

## 出力フォーマット

### Markdown（`--output-md`）

```markdown
# Microsoft 365 Copilot 回答

- 日時: 2026-05-20T12:34:56+09:00
- プロンプト: 今期の社内通達を要約して

## 回答

…回答本文（DOM から再構成した Markdown）…

## 引用

1. [タイトル](https://...)
2. [タイトル](https://...)
```

### JSON（`--output-json`）

```json
{
  "url": "https://m365.cloud.microsoft/chat",
  "timestamp": "2026-05-20T12:34:56+09:00",
  "messages": [
    {"role": "user", "text": "今期の社内通達を要約して"},
    {
      "role": "assistant",
      "text": "…",
      "markdown": "…",
      "citations": [
        {"title": "...", "url": "https://..."}
      ]
    }
  ]
}
```

---

## 進め方の判断フロー

```
依頼 → サインイン済みか？
  ├─ No → `--login` で headed 起動して SSO 完了を確認
  └─ Yes → `--prompt` で質問を送る
              ├─ 応答が崩れる / セレクタが変わった
              │     → `references/selectors.md` を見て調整
              │     → `--headed --screenshot` で UI を確認
              └─ 正常 → `--output-md` / `--output-json` で保存
```

---

## エラー対処

| エラー / 症状 | 対処 |
|---|---|
| `playwright` が無い | `pip install playwright && playwright install chromium` |
| サインイン画面で止まる | `--login --headed` でサインインして再実行。多要素認証要件はブラウザ画面で対応 |
| 入力欄が見つからない (`ui-timeout`) | UI 変更の可能性。`--headed --screenshot` で確認し `references/selectors.md` のセレクタ候補を更新 |
| 応答が途中で切れる | `--response-timeout` と `--stable-seconds` を増やす |
| Chromium ではなく Edge が必要 | `--channel msedge` を指定 |
| 別セッションのロック | 同じ `--user-data-dir` を複数プロセスで開かない。`--user-data-dir` を分ける |
| 社内プロキシで起動しない | 環境変数 `HTTPS_PROXY` / `HTTP_PROXY` を設定する |

---

## スクリプト構成

```
scripts/
└── ask_copilot.py       ← 質問送信・応答取得・Markdown/JSON 出力
references/
├── setup-guide.md       ← Playwright CLI 導入と SSO 動作確認
└── selectors.md         ← Copilot UI のセレクタと調整方法
```

---

## 制限事項

- Copilot の UI は不定期に変わるため、セレクタが破綻したら `references/selectors.md` を更新する。
- ストリーミング中の中間出力は捨て、最終確定の回答だけを Markdown 化する。
- ファイル添付・画像生成・プラグイン呼び出しはこのスキルの対象外。
- アカウントを跨ぐ場合は `--user-data-dir` を分ける。
