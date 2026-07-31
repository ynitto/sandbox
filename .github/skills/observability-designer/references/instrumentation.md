# 計装パターン・リファレンス

## 構造化ログの形

```json
{
  "timestamp": "2026-05-30T12:34:56.789Z",
  "level": "ERROR",
  "service": "order-api",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "event": "payment_failed",
  "order_id": "ord_123",
  "error": "gateway timeout",
  "duration_ms": 5012
}
```

- 1行1JSON。メッセージ文に値を埋め込まず、フィールドに分ける（検索・集計可能に）
- 必須フィールド: timestamp / level / service / trace_id / event
- **載せない**: 氏名・メール・カード番号などの PII（マスキングは privacy-compliance 参照）

### ログレベル運用の目安
| レベル | 用途 |
|--------|------|
| ERROR | 対応が必要な失敗。アラート対象になり得る |
| WARN | 異常だが処理は継続（リトライ成功・フォールバック） |
| INFO | 主要なビジネスイベント（注文確定・ログイン） |
| DEBUG | 開発・調査用の詳細。本番は通常オフ |

## メトリクス: RED と USE

**RED（リクエスト駆動サービス向け）**
- Rate: 秒あたりリクエスト数
- Errors: エラー率・エラー数
- Duration: レイテンシ分布（p50/p90/p99 をヒストグラムで）

**USE（リソース向け）**
- Utilization: 使用率（CPU・メモリ）
- Saturation: 飽和度（キュー長・待ち）
- Errors: ハード/ソフトエラー

### カーディナリティ注意
ラベルに user_id・request_id など高基数の値を入れると時系列が爆発しコスト増。
→ 高基数情報はメトリクスではなくログ/トレースへ。

## OpenTelemetry 最小計装例

Python（自動計装＋手動スパン）:
```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

def place_order(req):
    with tracer.start_as_current_span("place_order") as span:
        span.set_attribute("order.item_count", len(req.items))
        # 外部I/O はネストしたスパンに
        with tracer.start_as_current_span("charge_payment"):
            charge(req)
```

Node.js（OTEL SDK 初期化）:
```js
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
new NodeSDK({ instrumentations: [getNodeAutoInstrumentations()] }).start();
```

## コンテキスト伝播（W3C Trace Context）
サービス間 HTTP 呼び出しで `traceparent` ヘッダを伝播し、トレースを連結する:
```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```
ログにも同じ trace_id を出すことで「ログ ⇄ トレース」を相互ジャンプできる。

## サンプリング戦略
| 方式 | 内容 | 向き |
|------|------|------|
| ヘッドベース | リクエスト開始時に確率で採否決定（例: 10%） | 量が多くコスト重視 |
| テールベース | 完了後にエラー/遅延スパンを優先採取 | 異常を逃したくない |
| 常時100% | 低トラフィック・重要経路 | 量が少ない場合 |

## アラート設計の原則
- **症状ベース優先**: 「SLO違反」「ユーザー向けエラー率上昇」でアラート。CPU高騰そのものではなく、それがユーザー影響に繋がる時だけ鳴らす
- **連続条件**: 単発スパイクで鳴らさず「5分継続」等で抑制
- **重大度分け**: ページ（即時呼出）と通知（営業時間内）を分ける
- SLO・エラーバジェットと連動 → `slo-designer`
