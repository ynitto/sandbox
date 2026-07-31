# エアロック・パケット プロトコル v1

世界B（ローカル: kiro-cli / GitHub Copilot / GitLab）と世界A（MS365: Copilot /
SharePoint / Outlook）の間にはソフトの橋が無い。越境できるのは

- **Playwright** によるブラウザ操作（B→A、回答を読んで持ち帰る）
- **人間のコピー&ペースト**（クリップボードは通る）
- **GitLab 通知 → Outlook メール**（B→A の限定経路）

だけ。どの経路でもルーティング情報と本文が 1 個のテキストで運べ、かつ
**人間のコピペが途中で切れたら気づける**よう、本文の CRC32 を必ず付ける。

## 書式

```
===== COPILOT365 PACKET v1 BEGIN =====
id: 1781318914718-ea3b78c2
to: ms365
from: kiro
intent: ask
created: 2026-06-13T02:48:34Z
reply_to:
crc32: 7ca3c18f
----- BODY -----
<自由テキスト / Markdown 本文>
===== COPILOT365 PACKET v1 END =====
```

- **BEGIN / END マーカー**で囲うので、人間が周囲の UI ごとコピーしても、
  `extract_all()` がパケットだけを取り出せる。
- **ヘッダ**はフラットな `key: value`（ルーティング専用、YAML パーサ不要）。
- **crc32** は BODY の UTF-8 バイト列に対する `zlib.crc32`（8 桁 16 進）。
  デコード時に再計算して一致しなければ `PacketError`（＝コピペ途中切れを検知）。

## ヘッダ

| キー | 必須 | 意味 |
|------|------|------|
| `id` | ◯ | 時刻順ソート可能な一意 ID |
| `to` | ◯ | 宛先。`ms365` / `outlook` / `sharepoint` / `kiro` / `gitlab` / `human` |
| `from` | ◯ | 送信元（同じ語彙） |
| `intent` | ◯ | `ask` / `answer` / `approve` / `notify` / `context` / `error` |
| `created` | ◯ | UTC ISO8601 |
| `reply_to` | 任意 | 返信元パケットの `id`（会話を連鎖させる） |
| `crc32` | ◯ | BODY のチェックサム |
| その他 | 任意 | 未知キーは `extra` として保持（例: `thread: T-42`） |

## 典型フロー

```
世界B (kiro-cli 司令塔)                         世界A (MS365)
  │  Packet(to=ms365, intent=ask)
  │  ──[ Playwright で Copilot に投入 ]──▶  Copilot が回答
  │  ◀─[ DOM/OCR で回収して持ち帰る ]──
  ▼
  Packet(to=kiro, intent=answer, reply_to=…)   ← inbox に保存して後続処理へ
```

クリップボード経路（Playwright を使わない/使えない時）:

```
B: clip-export "…"  → パケットをクリップボードへ → 人間が MS365 に貼る
A: 人間が回答をコピー → B: clip-import → CRC32 検証付きで取り込み
```

## 実装

- コーデック: [`packet.py`](./packet.py)（依存ゼロ・`python packet.py selftest` で検証）
- CLI: [`copilot365_bridge.py`](./copilot365_bridge.py)
- JSON Schema（参考）: [`schema/packet.schema.json`](./schema/packet.schema.json)
