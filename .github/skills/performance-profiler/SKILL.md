---
name: performance-profiler
description: コードのパフォーマンスボトルネックを特定・改善するスキル。「パフォーマンスを改善して」「遅い原因を調べて」「プロファイリングして」「ボトルネックを探して」「N+1問題を直して」「メモリリークを調べて」「最適化して」「応答が遅い」「処理が重い」「重くなった」「チューニングして」などで発動する。静的解析による性能アンチパターン検出と、言語別プロファイリング計装の両方を提供する。
metadata:
  version: 1.0.0
  tier: experimental
  category: debug
  tags:
    - performance
    - profiling
    - optimization
    - bottleneck
---

# performance-profiler

コードの性能問題を **静的解析（アンチパターン検出）** と **動的プロファイリング（計装）** の2つのアプローチで特定・改善する。

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR` とする。スクリプトは `scripts/` から、言語別プロファイリングコマンドは `references/profiling-commands.md` を参照する。

---

## ワークフロー

### Step 0: スコープチェック

以下に該当する場合は確認する:
- 対象コード・プロジェクトが存在しない
- 「遅い」箇所や処理の特定が一切できない（計測データも仮説もない）

### Step 1: コンテキストを把握する

1. **言語・フレームワーク** — Python/JS/Go/Java/Rust/Ruby等、ORMやHTTPフレームワークも確認する
2. **問題の性質** — 遅い箇所の仮説があるか。実測データ（レイテンシ・CPU・メモリ）があるか
3. **アプローチの選択**:

   | 状況 | アプローチ |
   |------|-----------|
   | コードを見て問題を探したい | **Step 2: 静的解析** から開始 |
   | 実行して計測したい | **Step 3: プロファイリング計装** から開始 |
   | 両方 | Step 2 → Step 3 の順で実施 |

---

### Step 2: 静的解析（アンチパターン検出）

`detect_antipatterns.py` を実行して自動検出する:

```bash
# カレントディレクトリを解析
python ${SKILL_DIR}/scripts/detect_antipatterns.py

# 対象ディレクトリを指定
python ${SKILL_DIR}/scripts/detect_antipatterns.py --path src/

# 特定言語のみ
python ${SKILL_DIR}/scripts/detect_antipatterns.py --lang python

# JSON 出力（他ツール連携用）
python ${SKILL_DIR}/scripts/detect_antipatterns.py --json
```

スクリプトの検出結果をもとに、以下の観点で **コードを直接確認** する:

#### N+1 クエリ
- ループ内で ORM / DB アクセスが繰り返されていないか
- `SELECT *` で必要以上のデータを取得していないか
- `eager_load` / `prefetch_related` / `JOIN` で解決できないか

#### 非効率なループ・データ処理
- ネストループで同じデータを繰り返し走査していないか（O(n²) の罠）
- ループ内で不変な処理（ファイル読み込み・正規表現コンパイル等）を繰り返していないか
- リスト内包表記が `generator` に置き換えられる箇所はないか（メモリ節約）
- 文字列を `+` でループ連結している箇所はないか（`join` で O(n) に改善）

#### 不要なI/O・ブロッキング
- 非同期コンテキストで同期I/O を呼んでいないか（`requests` in `async def` 等）
- ループ内でファイル open/close を繰り返していないか
- 結果をキャッシュできる繰り返し外部API呼び出しはないか

#### メモリ問題
- 大きなオブジェクトを不必要にコピーしていないか（参照渡しで十分な箇所）
- グローバル変数・クラス変数にデータを蓄積し続けていないか（メモリリーク候補）
- 大量データを一度にメモリに展開していないか（ストリーミング処理を検討）

#### 並行性の未活用
- 独立したI/O処理が直列に並んでいないか（`asyncio.gather` / `Promise.all` / goroutine で並列化可能）
- CPU バウンド処理で GIL の影響を受けているか（multiprocessing / 別言語バインディングを検討）

---

### Step 3: プロファイリング計装

言語別のプロファイリング手順は `references/profiling-commands.md` を参照する。

計装の手順:
1. **ホットパスの特定**: プロファイラを実行して CPU 時間・メモリ使用量の上位関数を特定する
2. **マイクロベンチマーク**: 疑わしい関数を単体で計測して仮説を検証する
3. **Before/After 計測**: 改善前後で必ず数値を比較して効果を確認する

---

### Step 4: 改善案を提示する

#### 報告フォーマット

```
## パフォーマンス分析結果

### 検出された問題

#### 🔴 Critical（即時対応推奨）
**[カテゴリ] <問題の要約>**
場所: <ファイル名:行番号>
問題: <何がなぜ遅いか>
改善前:
<問題のあるコード例>
改善後:
<修正後のコード例>
改善効果の見込み: <O(n²)→O(n) / DB クエリ数 N→1 等>

#### 🟡 Warning（改善推奨）
...（同形式）

#### 🔵 Suggestion（検討を推奨）
...（同形式）

### プロファイリング結果（計測データがある場合）
- ホットパス上位: <関数名 / 処理時間の割合>
- メモリピーク: <MB>
- DB クエリ数: <N件>

### 優先改善ロードマップ
1. [最優先] <対応方針>（期待効果: <数値>）
2. ...

### サマリー
- Critical: X件 / Warning: X件 / Suggestion: X件
- 推定改善効果: <全体的な見通し>
```

---

## 判定スキーマ（machine-readable）

skill-mentor など呼び出し元が結果を集約するために使用する構造化フォーマット。
Step 4 の報告末尾に `<!-- verdict-json -->` コメントで囲んで出力する。

```json
{
  "skill": "performance-profiler",
  "verdict": "OPTIMIZED | NEEDS_IMPROVEMENT | CRITICAL_BOTTLENECK",
  "severity_summary": {
    "critical": 0,
    "warning": 0,
    "suggestion": 0
  },
  "blocking": false,
  "issues": [
    {
      "severity": "Critical | Warning | Suggestion",
      "category": "N+1 | ループ | I/O | メモリ | 並行性 | その他",
      "summary": "問題の要約（1行）",
      "location": "ファイル名:行番号",
      "estimated_impact": "DB クエリ数 N→1 / O(n²)→O(n) / メモリ X MB削減 等"
    }
  ]
}
```

**`verdict`**: `critical=0 && warning=0` → `OPTIMIZED`、`critical=0 && warning>0` → `NEEDS_IMPROVEMENT`、`critical>0` → `CRITICAL_BOTTLENECK`。
**`blocking`**: Critical が1件以上の場合のみ `true`。

---

## 言語別クイックリファレンス

詳細なプロファイリングコマンドは `references/profiling-commands.md` を参照。

| 言語 | 推奨プロファイラ | クイックコマンド |
|------|---------------|----------------|
| Python | cProfile, py-spy, memory_profiler | `python -m cProfile -s cumtime script.py` |
| Node.js / TS | --prof, clinic.js, 0x | `node --prof app.js` |
| Go | pprof | `go tool pprof http://localhost:6060/debug/pprof/profile` |
| Java | JProfiler, async-profiler | `java -agentpath:libasyncProfiler.so=start,file=profile.jfr` |
| Rust | flamegraph, perf | `cargo flamegraph` |
| Ruby | stackprof, derailed_benchmarks | `bundle exec stackprof --mode cpu --out tmp/stackprof.dump` |

---

## 補助スクリプト

- **detect_antipatterns.py** — 静的解析による性能アンチパターン検出（言語横断・grep/AST ベース）
