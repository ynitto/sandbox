---
name: codd-gate
description: ドキュメント・コード・テストの一貫性を機械的に維持する codd-gate（CoDD 流用の決定的ゲート・kiro-autonomous プラグイン）を運用するスキル。「ドキュメントとコードの整合を常にとって」「一貫性ゲートを入れて」「ドリフトを backlog に積んで」「接続マップを作って」「未文書化・未テストを棚卸しして」「done 前にドキュメント置き去りを止めて」などで発動する。差分ゲート（verify）・負債ラチェット（--debt）・修復タスク生成（tasks）を kiro-autonomous の regression/acceptance/enqueue に結線する。単発のドリフト調査レポートが欲しいだけなら doc-drift-detector を使う。
metadata:
  version: "1.0.0"
  tier: experimental
  category: operations
  tags:
    - coherence
    - documentation
    - drift-detection
    - kiro-autonomous
    - verification
---

# codd-gate — doc/code/test 一貫性ゲートの運用

`codd-gate`（`tools/codd-gate/`）は doc↔code↔test の接続マップを毎回フレッシュに作り、
差分を **Green / Amber / Gray / Followup** に分類して done 確定前に止め、直せない分を
kiro-autonomous の修復タスクへ変換する決定的 CLI。**LLM 判断をこのゲートに混ぜない**こと
（修復の知能は kiro-autonomous → kiro-flow の act が担う）。

| 境界 | 使うもの |
|------|---------|
| 単発のドリフト調査・証拠付きレポート | `doc-drift-detector` |
| 仕様書の新規逆生成 / 書き直し | `code-to-specs` / `technical-writer` |
| **一貫性を常時ゲートし、ドリフトを backlog へ返す** | **本スキル（codd-gate）** |

## 基本操作

```bash
codd-gate scan  [--charter <charter.md>] [--repo-dir name=dir ...]   # 接続マップ＋負債棚卸し
codd-gate verify --base "$KIRO_BASE_REV"                              # 差分ゲート（exit 0/1）
codd-gate verify --debt --max-broken 0                                # 負債ラチェット
codd-gate tasks  [--base REV|--debt] | kiro-autonomous enqueue --json # ドリフト→修復タスク
```

複数リポジトリは charter の `## repos` を共用（identity = url+path+base ＝パス＋ブランチで一意）。
codd-gate 専用キー `- docs:`/`- tests:`/`- code:` を repo エントリに追記して分類を上書きできる
（kiro-autonomous は未知キーとして無視する）。

## kiro-autonomous への結線（頼まれたらこの順で提案する）

1. **差分ゲート**: `.kiro/kiro-autonomous.yaml` に
   `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'`
2. **受入ラチェット**: charter `## acceptance` に
   `- codd-gate verify --debt --max-broken 0 --max-undocumented <現状値>`
   （現状値は `codd-gate scan` で測ってから入れ、改修の進行に合わせて下げる）
3. **負債の返済**: `codd-gate tasks --debt | kiro-autonomous enqueue --json`
   （または `--inbox <project>/inbox/`）。別 repo 追随タスクは `workspace:` 付きで出るので
   ルーティングはそのまま乗る
4. 修復タスクの verify は `codd-gate check`（状態アサーション）を使う。履歴 grep を書かない

## ガードレール

- 既存負債（ブラウンフィールド）を差分ゲートで NG にしない。負債は必ず「棚卸し→ラチェット→タスク化」
- 接続の誤検出を疑ったら注釈 `coherence: doc=…` / `code=…` / `test=…` で明示宣言する（推定より優先）
- repo の checkout が解決できないまま「PASS した」と報告しない（codd-gate は exit 2 で止まる）
- 詳細仕様は `tools/codd-gate/README.md`、設計は `docs/designs/codd-gate-design.md` が正典
