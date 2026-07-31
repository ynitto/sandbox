# copilot365-bridge — MS365 Copilot を「呼び出せる道具」にする世界B側ブリッジ

社内では **kiro-cli / GitHub Copilot(VSCode) / MS365 Copilot** のみ利用可。GitHub・
他 SaaS・他エージェントは不可。この制約下で 3 つのエージェントを自律的に連携させる
ための、**MS365 側だけが欠けていた橋**を埋めるツール。

## なぜ必要か — 世界が 2 つに割れている

| | 世界A（組織クラウド） | 世界B（開発ローカル） |
|---|---|---|
| メンバー | MS365 Copilot / OneDrive / SharePoint / Outlook | kiro-cli / GitHub Copilot(VSCode) / GitLab |
| 性質 | 組織知の宝庫。閉じている | 実行・コードの場。閉じている |
| 自動化面 | Power Automate / Copilot Studio | **kiro-cli（スクリプト可）** + GitLab CI |

**A↔B を橋渡しするソフトは存在しない**（GitHub 不可、GitLab は A から見えない、
SharePoint は B から見えない）。実際に越境できるチャネルはこれだけ:

| 方向 | チャネル | 自動化 |
|---|---|---|
| B→A | **Playwright で MS365 Web を人間操作模倣**（回答を読んで持ち帰る） | ◎ |
| B→A | GitLab 通知 → Outlook メール | ○ 限定 |
| A→B | 人間のコピー&ペースト（クリップボードは通る） | △ |

ポイントは **Playwright は世界B で動き、A を操作し、結果を読んで B へ持ち帰る**こと。
つまり A→B の「届かない」問題は、**B から取りに行く（pull）**ことで回避できる。

## 何をするか — MS365 Copilot のサブルーチン化

既存ツール（`hermes-gitlab-gateway` / `kiro-loop` / `issue-mailbox` /
`makaroshki-bridge`）は世界B 内の自律ループを既に成立させている。本ツールはそこに

```
ask_org_context(prompt) -> answer
```

という MS365 Copilot 呼び出しを足す。**kiro-cli を上位の司令塔に昇格させ、閉じた対話
製品 MS365 Copilot を関数のように叩く**のが設計の核。

```
GitLab Issue / CI イベント
  → kiro-cli が起動（司令塔）
  → 組織文脈が要る → copilot365-bridge ask（Playwright で MS365 へ B→A→B）
  → kiro が spec 化・実装、GitHub Copilot(VSCode) が編集補助
  → MR 作成・CI、結果を GitLab→Outlook で通知
  → 人間承認は「メール返信」→ watch-approvals が検知して再開
```

## エアロック・パケット

越境テキストは [`PACKET.md`](./PACKET.md) のパケット 1 個に固める。本文の **CRC32 を
必ず付ける**ので、人間のコピペが途中で切れても `PacketError` で検知できる。

## クイックスタート

```bash
# 依存なしでコアを検証
python packet.py selftest

# MS365 に触れずに配線確認（スタブ回答）
python copilot365_bridge.py ask --mock --packet "Teams で合意した仕様を要約して"

# outbox を監視して中継する常駐（--mock で動作確認、--once で 1 巡）
python copilot365_bridge.py daemon --mock --once

# 承認メール検知の配線確認
python copilot365_bridge.py watch-approvals --mock --reply-to <packet-id>
```

本番（実 MS365）:

```bash
pip install -r requirements.txt
playwright install chromium
cp copilot365-bridge.yaml.example copilot365-bridge.yaml   # セレクタ等を調整
python copilot365_bridge.py ask "請求書 API の決定事項を教えて"   # 初回はヘッドフルで SSO/MFA を手動通過
```

## サブコマンド

| コマンド | 役割 |
|----------|------|
| `ask` | MS365 Copilot に 1 問投げて回答パケットを得る（`--mock` でスタブ） |
| `daemon` | `outbox` を監視し `to=ms365` のパケットを ask して `inbox` へ返す |
| `watch-approvals` | Outlook の承認返信を検知して resume シグナルを出す |
| `clip-export` | パケットをクリップボードへ（人間が MS365 に貼る用） |
| `clip-import` | クリップボード（MS365 からコピー）からパケットを取り込む |
| `selftest` | パケットコーデックの単体テスト |

## 既存ツールへの接続

`mailbox_dir` の `outbox` / `inbox` をハブとして共有するだけ。

- **kiro-loop / kiro-loop-messaging**: kiro が「MS365 に聞きたい」依頼を `outbox` に
  パケットで投函 → `daemon` が回答を `inbox` に返す → kiro が回収。
- **hermes-gitlab-gateway**: GitLab イシュー起点のワーカーが、組織文脈の不足を
  `ask` で補完。
- **makaroshki-bridge / issue-mailbox**: 同じパケット書式を運搬路に乗せられる。

## セレクタが壊れたら

MS365 の DOM はテナント / 時期で変わる。壊れたら **`copilot365-bridge.yaml` の
`selectors` だけ**直す（コードは触らない）。DOM が取れない面は自動でスクリーンショット
＋（任意で）OCR フォールバックに落ちる。

## 注意（無視できない）

- **社内ポリシー / Conditional Access**: MS365 の自動操作はデバイス準拠・MFA・利用規約
  に抵触しうる。本ツールは **本人のログイン済みプロファイルを使うヘッドフル運用を既定**
  とし、認証情報は保存しない。可能な部分は正規面（Power Automate / Copilot Studio /
  Graph）へ寄せること。**導入前に情シスへ一声を強く推奨**。
- **脆さ対策**: セレクタは設定 1 箇所に集約、失敗時はスクショ＋OCR、CRC32 で取りこぼし
  検知 ── を最初から組み込んでいる。

## ファイル

```
copilot365-bridge/
  packet.py                       # エアロック・パケット コーデック（依存ゼロ・selftest 同梱）
  copilot365_bridge.py            # CLI 本体
  PACKET.md                       # パケット プロトコル仕様
  schema/packet.schema.json       # ヘッダ語彙の参考スキーマ
  copilot365-bridge.yaml.example  # 設定サンプル
  requirements.txt
```
