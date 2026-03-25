---
name: debug-mode
description: ランタイムログによるprintfデバッグ計装スキル。「ログを仕込んで」「計装して」「何が起きているか確認したい」「動作を観察したい」「値を追いたい」で発動。コードにログを埋め込んでランタイムの動作を観察・記録することが唯一の目的。バグ修正の責任は持たない。systematic-debuggingのフェーズ1証拠収集でも呼び出される。Windows/PowerShell 対応。
metadata:
  version: 1.0.0
  tier: stable
  category: debug
  tags:
    - printf-debug
    - logging
    - runtime-analysis
---

# デバッグモード

ランタイム計装による動作観察。
**推測するな。観察せよ。**

## このスキルを選ぶ基準

**debug-mode を使う条件（すべて当てはまる場合）:**
- 目的が「観察・計装」である（ランタイムで何が起きているか知りたい）
- エラーメッセージ・静的解析だけでは動作が追いきれない
- コードにログを埋め込んで実際の値・実行パスを記録する必要がある

**systematic-debugging に委ねる条件（いずれか当てはまる場合）:**
- 目的が「バグ修正・テスト修正」である（修正まで責任を持つ）
- エラーメッセージ・スタックトレースが明確で、コードを読むだけで原因調査が進む
- 複数フェーズの根本原因調査が必要

**両スキルを組み合わせる場合:**
systematic-debugging のフェーズ1「マルチコンポーネント証拠収集」で計装が必要になったとき、debug-mode をサブスキルとして呼び出す。

## 仮説検証フェーズの責務分担

| 仮説検証の方法 | 担当スキル |
|---|---|
| ログ証拠に基づくランタイム検証（実行して確認） | **debug-mode**（ステップ7） |
| コード静的解析・差分比較による検証 | **systematic-debugging**（フェーズ2〜3） |
| 最小限のコード変更でテストして確認 | **systematic-debugging**（フェーズ3） |

debug-mode の仮説検証はあくまでも「ログを仕込んで実行した結果」を根拠とする。静的コード解析や最小変更テストは systematic-debugging が担う。

## 基本原則

1. **ログなしに修正なし** — コードを読むだけでは不十分
2. **複数仮説** — 最低3つ、理想は5つ
3. **証拠に基づく判定** — CONFIRMED / REJECTED / INCONCLUSIVE
4. **クリーンアップ前に検証** — 修正が確認されるまで計装を残す

## ワークフロー

```
問題 → セットアップ → 仮説 → 計装 → ログクリア
    → 再現 → 分析 → 修正 → 検証 → クリーンアップ
```

## ステップ1: 問題の理解

ユーザーから確認する（不明な場合は質問する）:
- 症状（期待値 vs 実際の動作）
- 再現手順
- 直近の変更

## ステップ2: ロギングのセットアップ

環境に合わせて選択する。ログフォーマットとリージョン構文は [references/common.md](references/common.md) を参照。

言語別パターン:
- [JavaScript/TypeScript](references/javascript.md)
- [Python](references/python.md)
- [Ruby](references/ruby.md)
- [Go](references/go.md)
- [Rust](references/rust.md)
- [Java](references/java.md)
- [Kotlin](references/kotlin.md)
- [Swift](references/swift.md)
- [React Native](references/react-native.md)
- [Flutter](references/flutter.md)
- [C/C++](references/c-cpp.md)
- [C#](references/csharp.md)

**Web（ブラウザ + JavaScript/TypeScript）:** コレクターサーバーを起動

```bash
# PowerShell（Windows）
node -e "require('http').createServer((q,s)=>{s.setHeader('Access-Control-Allow-Origin','*');s.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');s.setHeader('Access-Control-Allow-Headers','Content-Type');if(q.method==='OPTIONS'){s.writeHead(204).end();return}let b='';q.on('data',c=>b+=c);q.on('end',()=>{require('fs').appendFileSync('debug.log',b+'\n');s.writeHead(204).end()})}).listen(4567,()=>console.log('Collector: http://localhost:4567'))"
```

**サーバーサイドのみ（Node.js, Python, Ruby, Go 等）:** サーバー不要、直接ファイル書き込み。

## ステップ3: 仮説の生成

**3〜5つの仮説を生成する。1つに固執しない。**

```markdown
## 仮説

### H1: [タイトル]
- 原因: ...
- 検証: YのBefore/AfterでXの値を確認

### H2: [タイトル]
- 原因: ...
- 検証: ...

### H3: [タイトル]
- 原因: ...
- 検証: ...
```

## ステップ4: 計装の挿入

各仮説のログを挿入する。**3〜8箇所。**

**計装ポイント:**
- 関数エントリ（引数）
- 関数エグジット（戻り値）
- 重要な処理のBefore/After
- 分岐パス（どのif/elseが実行されたか）
- 状態変更のBefore/After

**必須: `#region debug:{hypothesisId}` でラップする**

言語別テンプレートは上記のリファレンスファイルを参照。

## ステップ5: 古いログのクリア

```powershell
# PowerShell（Windows）
Remove-Item -Force debug.log -ErrorAction SilentlyContinue

# bash（Linux/macOS）
rm -f debug.log
```

## ステップ6: 再現のリクエスト

```markdown
## 再現手順

1. ロギングが設定済みか確認（必要であればコレクター起動）
2. `debug.log` が削除済みか確認
3. アプリを起動/再起動
4. [バグをトリガーする具体的手順]
5. 完了したら教えてください
```

## ステップ7: ログの分析

```powershell
# PowerShell（Windows）
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json }   # 全件表示
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.h -eq "H1" }  # H1でフィルタ
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Group-Object h  # 仮説別グループ
Get-Content debug.log -Wait | ForEach-Object { $_ | ConvertFrom-Json }  # リアルタイム

# bash（Linux/macOS）- jq がある場合
cat debug.log | jq .
jq 'select(.h == "H1")' debug.log
cat debug.log | jq -s 'group_by(.h)'
tail -f debug.log | jq .
```

各仮説を評価する:

| 判定 | 意味 |
|------|------|
| CONFIRMED | ログがこの仮説を明確に支持する |
| REJECTED | ログがこの仮説を否定する |
| INCONCLUSIVE | データが不十分 |

## ステップ8: 修正

**仮説がCONFIRMEDになった場合のみ。**

- 最小限の差分
- **計装はそのままにしておく**

**すべてREJECTEDの場合:** 別サブシステムから新しい仮説を立てる → ステップ3へ。

## ステップ9: 検証

```markdown
1. `debug.log` を削除
2. アプリを再起動
3. 同じ操作を実行
4. 完了したら教えてください
```

修正前後のログを比較する。

## ステップ10: クリーンアップ

**ユーザーが修正を確認した後のみ。**

```powershell
# PowerShell（Windows）
Select-String -Path src\* -Pattern "#region debug:" -Recurse

# bash（Linux/macOS）
grep -rn "#region debug:" src/
```

計装を削除し、`debug.log` を削除し、コレクターを停止する。

## ログフォーマット

NDJSON（1行1JSON）:

```jsonl
{"h":"H1","l":"state_before","v":{"userId":"123"},"ts":1702567890123}
```

| フィールド | 意味 |
|-----------|------|
| h | 仮説ID |
| l | ラベル |
| v | 値 |
| ts | タイムスタンプ（ミリ秒） |

## 禁止事項

- ❌ ログ分析前に修正を提案する
- ❌ 仮説が1つだけ
- ❌ 検証前に計装を削除する
- ❌「たぶんこれ」という推測
- ❌ setTimeout/sleep を「修正」として使う

## リファレンス

- [references/common.md](references/common.md) - ログフォーマット、リージョン構文、モバイルログ取得
- [references/javascript.md](references/javascript.md) - JavaScript/TypeScript
- [references/python.md](references/python.md) - Python
- [references/ruby.md](references/ruby.md) - Ruby
- [references/go.md](references/go.md) - Go
- [references/rust.md](references/rust.md) - Rust
- [references/java.md](references/java.md) - Java
- [references/kotlin.md](references/kotlin.md) - Kotlin/Android
- [references/swift.md](references/swift.md) - Swift/iOS
- [references/react-native.md](references/react-native.md) - React Native
- [references/flutter.md](references/flutter.md) - Flutter/Dart
- [references/c-cpp.md](references/c-cpp.md) - C/C++
- [references/csharp.md](references/csharp.md) - C#
