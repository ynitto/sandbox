---
name: codd-gate
description: ドキュメント・コード・テストの一貫性を機械的に維持する codd-gate（CoDD 流用の決定的ゲート。agent-project から完全独立で単体で CI/git hook から使え、連携時は state repo の共通チェックから呼ぶ）を運用するスキル。「ドキュメントとコードの整合を常にとって」「一貫性ゲートを入れて」「ドリフトを backlog に積んで」「接続マップを作って」「未文書化・未テストを棚卸しして」「done 前にドキュメント置き去りを止めて」などで発動する。単発のドリフト調査レポートが欲しいだけなら doc-drift-detector を使う。
metadata:
  version: "1.1.0"
  tier: experimental
  category: operations
  tags:
    - coherence
    - documentation
    - drift-detection
    - agent-project
    - verification
---

# codd-gate — doc/code/test 一貫性ゲートの運用

`codd-gate`（`tools/codd-gate/`）は doc↔code↔test の接続マップを毎回フレッシュに作り、
差分を **Green / Amber / Gray / Followup** に分類して受け入れ前に止め、直せない分を
修復タスク（JSON）へ変換する決定的 CLI。**agent-project に依存しない独立ツール**
（python3＋git のみ。インストールは `bash tools/codd-gate/install.sh`）で、単体では CI・
git hook（`verify --base "@{push}"` 等）に差し込む。**LLM 判断をこのゲートに混ぜない**こと
（連携時の修復の知能は agent-project → agent-flow の act が担う）。

| 境界 | 使うもの |
|------|---------|
| 単発のドリフト調査・証拠付きレポート | `doc-drift-detector` |
| 仕様書の新規逆生成 / 書き直し | `code-to-specs` / `technical-writer` |
| **一貫性を常時ゲートし、ドリフトを backlog へ返す** | **本スキル（codd-gate）** |

## 基本操作（単体・これだけで完結する）

```bash
codd-gate scan                              # 接続マップ＋負債棚卸し（.codd-gate/map.json）
codd-gate impact --base origin/main         # 差分の Green/Amber/Gray/Followup（報告のみ）
codd-gate verify --base origin/main         # 差分ゲート（ドリフトで exit 1）
codd-gate verify --debt --max-broken 0      # 負債ラチェット
codd-gate tasks  --debt                     # 負債→修復タスク（JSON。--inbox DIR でファイル出力）
codd-gate check  --doc D --code C --fresh   # 状態アサーション（修復完了の判定に使う）
```

- 「常に」の単体運用は git hook / CI に置く: pre-push に `codd-gate verify --base "@{push}"`、
  CI に `verify --base origin/$BASE_BRANCH && verify --debt --max-broken 0`。
- 複数リポジトリは**共通スキーマ**（`schemas/repos.schema.json`）のレジストリを `--repos <file>` か
  設定 `.kiro/codd-gate.{yaml,json}` の `repos:` で与える（identity = (url, path, base)。`dir:` で
  ローカル checkout、`docs:/tests:/code:` で分類グロブを上書き）。charter.md は読まない。

## 追加情報: agent-project との連携（オプション）

codd-gate は agent-project から完全に独立させる。`.agents/agent-project.yaml` には state repo が持つ
共通チェックを1本だけ設定し、その内側から codd-gate を呼ぶ。

```yaml
regression_cmd: ./tools/check
```

```sh
#!/bin/sh
set -eu
codd-gate verify --base "$AGENT_BASE_REV" --repos ./repos.json
```

差分基準は agent-project が実行時に渡す。旧 `$KIRO_BASE_REV` は後方互換としてのみ扱う。

通常タスクの verify や charter acceptance へ同じ検査を重ねない。検証 CLI を増やす場合は
`tools/check` に1行追加する。既存負債は必要なときに
`codd-gate tasks --debt | agent-project enqueue --json` で明示投入する。codd-gate が生成した修復タスクでは、
タスク自身の `codd-gate check` を完了根拠として使う。

**守ること**: 常駐・繰り返しは agent-project（または cron/CI）に持たせる。codd-gate に watch 的な
長期実行を求めない（どのサブコマンドも単発・有界が設計上の不変条件）。

## ガードレール

- 既存負債（ブラウンフィールド）を差分ゲートで NG にしない。負債は必ず「棚卸し→ラチェット→タスク化」
- 接続の誤検出を疑ったら注釈 `coherence: doc=…` / `code=…` / `test=…` で明示宣言する（推定より優先）
- repo の checkout が解決できないまま「PASS した」と報告しない（codd-gate は exit 2 で止まる）
- 詳細仕様は `tools/codd-gate/README.md`、設計は `docs/designs/codd-gate-design.md` が正典
