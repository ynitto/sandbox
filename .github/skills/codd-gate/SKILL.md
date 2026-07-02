---
name: codd-gate
description: ドキュメント・コード・テストの一貫性を機械的に維持する codd-gate（CoDD 流用の決定的ゲート。単体で CI/git hook から使え、kiro-autonomous とはオプションでプラグイン連携する）を運用するスキル。「ドキュメントとコードの整合を常にとって」「一貫性ゲートを入れて」「ドリフトを backlog に積んで」「接続マップを作って」「未文書化・未テストを棚卸しして」「done 前にドキュメント置き去りを止めて」などで発動する。差分ゲート（verify）・負債ラチェット（--debt）・修復タスク生成（tasks）を kiro-autonomous の regression/acceptance/enqueue に結線する。単発のドリフト調査レポートが欲しいだけなら doc-drift-detector を使う。
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
差分を **Green / Amber / Gray / Followup** に分類して受け入れ前に止め、直せない分を
修復タスク（JSON）へ変換する決定的 CLI。**kiro-autonomous に依存しない独立ツール**
（python3＋git のみ。インストールは `bash tools/codd-gate/install.sh`）で、単体では CI・
git hook（`verify --base "@{push}"` 等）に差し込む。**LLM 判断をこのゲートに混ぜない**こと
（連携時の修復の知能は kiro-autonomous → kiro-flow の act が担う）。

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
- 複数リポジトリは**自前の設定ファイル** `.kiro/codd-gate.{yaml,json}` の `repos:` に書く
  （identity = (url, path, base) ＝パス＋ブランチで一意。`dir:` でローカル checkout、
  `docs:/tests:/code:` で分類グロブを上書き）。外部フォーマットへの依存は無い。

## 追加情報: kiro-autonomous への結線（オプション連携。有効化は設定だけ）

kiro-autonomous の install.sh は隣に codd-gate があれば同梱インストールする。結線は kiro-autonomous が
公式に定義する外部 CLI 差し込み点（設計書 §4.1: E1 verify/acceptance・E2 regression_cmd・
E3 intake_cmd）だけを使う。この順で提案する:

1. **差分ゲート**: `.kiro/kiro-autonomous.yaml` に
   `regression_cmd: 'codd-gate verify --base "$KIRO_BASE_REV"'`
2. **受入ラチェット**: charter `## acceptance` に
   `- codd-gate verify --debt --max-broken 0 --max-undocumented <現状値>`
   （現状値は `codd-gate scan` で測ってから入れ、改修の進行に合わせて下げる）
3. **負債の自動返済**: 同 yaml に `intake_cmd: 'codd-gate tasks --debt'`（watch の周期で冪等取り込み。
   タスク id が冪等キー）。同種負債の山は `--cohort` を足して pilot-then-batch に分解を委ねる。
   単発なら `codd-gate tasks --debt | kiro-autonomous enqueue --json` / `--inbox <project>/inbox/`。
   別 repo 追随タスクは `workspace:` 付きで出るのでルーティングはそのまま乗る
4. 修復タスクの verify は `codd-gate check`（状態アサーション）を使う。履歴 grep を書かない
5. レジストリを charter と二重管理したくなければ `--charter <charter.md>` アダプタで
   `## repos` を共用できる（任意。単体利用ではネイティブ `repos:` を使う）

**守ること**: 常駐・繰り返しは kiro-autonomous（または cron/CI）に持たせる。codd-gate に watch 的な
長期実行を求めない（どのサブコマンドも単発・有界が設計上の不変条件）。

## ガードレール

- 既存負債（ブラウンフィールド）を差分ゲートで NG にしない。負債は必ず「棚卸し→ラチェット→タスク化」
- 接続の誤検出を疑ったら注釈 `coherence: doc=…` / `code=…` / `test=…` で明示宣言する（推定より優先）
- repo の checkout が解決できないまま「PASS した」と報告しない（codd-gate は exit 2 で止まる）
- 詳細仕様は `tools/codd-gate/README.md`、設計は `docs/designs/codd-gate-design.md` が正典
