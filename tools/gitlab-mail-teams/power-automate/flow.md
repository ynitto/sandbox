# Power Automate フロー構成（ドラフト）

GitLab の通知メールを Outlook で受け取り、件名・本文から項目を抜き出して Adaptive Card を組み、Teams のチャット／チャネルへ投稿するフローの下書き。  
CLI（`gitlab_mail_teams.py`）と同じカード形を目指すが、**Outlook トリガーからは `X-GitLab-*` ヘッダーが読めない**ため、判定・抽出は件名・本文のパターンに寄せる。式の具体例は [`expressions.md`](expressions.md)。フィクスチャ別の完成カード例は [`expected/`](expected/)。

## 全体像

```
GitLab ──通知メール──▶ Outlook
                          │  トリガー: 新しいメールが届いたとき
                          │  （差出人・件名で絞り込み）
                          ▼
                    件名・本文（プレーンテキスト化）から抽出
                          │  kind 判定 → Adaptive Card 組み立て
                          ▼
                    Teams: アダプティブカードをチャットまたはチャネルに投稿する
```

Outlook での受信から Teams への投稿までが **MS365 内で完結する本番経路**。

## 上流の仕組み ― ハブissue方式

対象には、[gitlab-issue-hub-agent](../../change-watch/agents/gitlab-issue-hub-agent/README.md) が使うハブ issue へのコメント通知メールも含まれ得る。1 つの GitLab issue を介し、メール・Power Automate とローカル側がやりとりするハブissue方式が、ローカルと MS365 をつなぐ標準の橋となる。コメントのみの通知は通常 `unknown` として扱う。

## 1. トリガー

**コネクタ:** Outlook（Office 365 Outlook）  
**トリガー:** 「新しいメールが届いたとき」（V3 など最新版）

### 絞り込み（おすすめ）

| 条件 | 狙い |
|---|---|
| 差出人（From） | GitLab の通知アドレス（例: `gitlab@gitlab.example.co.jp`）。社内の実アドレスに合わせる |
| 件名に含む | 任意。例: プロジェクト名の短い名前、`pipeline for`、`(!` / `(#` など。絞りすぎると取りこぼすので、まずは差出人のみでもよい |
| フォルダ | 受信トレイ、または GitLab 用に振り分けたフォルダ |

トリガー後に「条件」アクションで差出人ドメインや件名を再確認してもよい。

### トリガーから取れる主な値

| 用途 | 典型的な参照 |
|---|---|
| 件名 | `triggerOutputs()?['body/subject']` |
| 本文（HTML のことが多い） | `triggerOutputs()?['body/body']` |
| 差出人表示名 | `triggerOutputs()?['body/from']` 配下の displayName 相当 |
| 受信日時 | `triggerOutputs()?['body/receivedDateTime']`（多くの環境で UTC） |

**取れないもの（重要）:** `X-GitLab-Project-Path`、`X-GitLab-Issue-IID`、`X-GitLab-MergeRequest-IID`、`X-GitLab-Pipeline-Id`、`X-GitLab-Pipeline-Status`、`X-GitLab-NotificationReason` など。CLI はこれらを優先するが、本フローでは使えない。

## 2. 前処理（本文のプレーンテキスト化）

Outlook の本文は HTML のまま届くことが多い。カードの URL・定型句抽出の前に、次のどちらかでプレーンテキストに寄せる。

1. **「HTML をテキストに変換」系のアクション**があればそれを使う。
2. なければ式でざっくりタグ除去（`<br>` / `</p>` を改行に、その他のタグを削除、`&amp;` 等を戻す）。`<a href="…">ラベル</a>` は CLI 同様に `ラベル ( URL )` へ展開できると URL 抽出が安定する。

以降の変数名例:

- `Subject` … 件名
- `BodyText` … プレーンテキスト化した本文
- `FromDisplay` … 差出人の表示名

## 3. 抽出と kind 判定

処理の順序は CLI の `parse_message` に合わせる。

1. **件名の分割** — `Re:` / `Fwd:` / `Fw:` 接頭辞を除き、` | ` でセグメント化。末尾セグメントの `(#128)` / `(!57)` から番号と Issue／MR の候補を取る。
2. **パイプライン候補** — いずれかのセグメントが `Failed pipeline for …` / `Successful pipeline for …` / `Fixed pipeline for …` で始まるか。
3. **kind**
   - パイプライン候補あり → 先頭語が `Failed` のときだけ `pipeline_failed`。Successful / Fixed は `unknown`（状態は `success`）。
   - 末尾が `(!n)` かつ本文に `requested a new review` / `requested review` / `requested you to review` / `was requested to review` を含む → `mr_review_requested`。`(!n)` だけで定型句が無い場合は `unknown`（番号・URL は埋める）。
   - 末尾が `(#n)` かつ本文に `created an issue` / `opened an issue` 等 → `issue_created`。番号だけ／担当変更だけは `unknown`。
   - どれでもない → `unknown`
4. **項目抽出**（詳細は `expressions.md`）
   - `project` … 本文で最初に現れる `/-/` を含む URL の `/-/` より前のパス（フルパス）。件名先頭の短い名前だけでは足りない。
   - `title` … 件名からプロジェクト名セグメントと末尾 `(#n)` / `(!n)` を除いたもの。パイプラインはマッチした `Failed pipeline for main` 全体。
   - `number` … Issue／MR は末尾参照、パイプラインは URL の `/pipelines/` 直後。
   - `url` … 種別に応じた最初の URL。
   - `branch` … MR は `Branches: X to Y` の X。パイプラインは件名の `for` の後。
   - `status` … `opened` / `review_requested` / `failed`（またはパイプラインの success）など。
   - `actor` … From 表示名から `(@user)` を除く。パイプラインは本文 `triggered by X`（差出人が `GitLab` だけのとき特に）。
   - `summary` … CLI 要約の**近似**（定型行・メタ行を落とし 280 字で切る）。
   - `received_at` … トリガーの受信日時（表記は次節）。

## 4. Adaptive Card の組み立て

CLI の `build_card` と同じ構造にする。完成形は `expected/*.card.json` を正とする。

| 要素 | 内容 |
|---|---|
| 見出し TextBlock | kind ごとの固定文言。`pipeline_failed` だけ `color: Attention`、他は `isSubtle: true` |
| 題名 TextBlock | `title`。Issue／MR は末尾に `(#n)` / `(!n)` を付ける。パイプラインは付けない。`markdown: false` |
| FactSet | 順に プロジェクト →（パイプラインなら）パイプライン → ブランチ → 状態 → 操作した人 → 受信。空の項目は出さない。各 fact に `markdown: false` |
| 要約 TextBlock | `summary` があれば。`wrap` / `spacing: Medium` / `markdown: false` |
| Action.OpenUrl | `url` があるときだけ。「GitLab で開く」 |

見出しラベル:

| kind | 見出し |
|---|---|
| `issue_created` | Issue が作成されました |
| `mr_review_requested` | レビューの依頼 |
| `pipeline_failed` | パイプラインが失敗しました |
| `unknown` | GitLab からの通知 |

カード JSON は「変数に JSON を構成」か「文字列を Compose して `json()`」で作る。投稿アクションには **カード本体**（`type: AdaptiveCard` のオブジェクト）を渡す。外側の `attachments` 包みはコネクタ側が付ける想定。

## 5. アクション（Teams へ投稿）

**コネクタ:** Microsoft Teams  
**アクション:** 「アダプティブカードをチャットまたはチャネルに投稿する」（名称はテナントのコネクタ版により前後する）

| 項目 | 設定 |
|---|---|
| 投稿先 | チーム／チャネル、またはチャット |
| アダプティブカード | 前段で組み立てたカード JSON |

`kind` が `unknown` のとき投稿しない、チャネルを分ける、などは条件分岐で足す。

## 6. CLI との等価性が崩れる点（正直な注記）

本フローはヘッダー無しで件名・本文だけを見るため、CLI（ヘッダー優先）と次がずれる／近似になる。

| 項目 | CLI | Power Automate（本フロー） |
|---|---|---|
| プロジェクト | `X-GitLab-Project-Path` 優先 | 本文で最初に現れる `/-/` を含む URL の `/-/` 前から復元。件名先頭の短い名前だけだと `order-service` になり、フルパス `platform/order-service` にならない |
| Issue／MR／Pipeline 番号 | `X-GitLab-*-IID` / `Pipeline-Id` 優先 | Issue／MR は件名末尾の `(#n)` / `(!n)`、パイプラインは URL から取得 |
| パイプライン状態 | `X-GitLab-Pipeline-Status` 優先 | 件名の Failed / Successful / Fixed から推定 |
| レビュー依頼の根拠 | `X-GitLab-NotificationReason`（`review_requested` / `mentioned` 等）も見る | 本文の定型句のみ。メンション通知で定型句が無いと `unknown` に落ちやすい |
| Issue 作成の根拠 | 定型句 **または** 通知理由（`created` 等）かつ非返信 | 定型句中心。理由ヘッダーは使えない |
| `summary` | 署名除去・先頭定型行・メタ行除去・空白正規化・280 字 | 同じ方針の式になるが、HTML→テキストの差や改行処理で**近似**。パイプラインは「Commit Message + 失敗ジョブ」まで式で再現するのが重い |
| `received_at` | メール `Date` の ISO（タイムゾーン付き） | Outlook の `receivedDateTime` は多くの環境で **UTC**（例: `…Z`）。フィクスチャの `+09:00` と文字面が一致しないことがある |
| 差出人 | `.eml` の From 生文字列 | コネクタの displayName / address。カードには通常載せないが、actor 抽出の入力が違う |
| 本文の優先 | `text/plain` 優先、無ければ HTML を整形 | コネクタが HTML だけ渡すことがあり、リンク表記が CLI と微妙にずれる |

フィクスチャ 3 通については、ヘッダー無しでも件名・本文の定型句と URL から **同じ kind・同じカード骨格** まで到達できる想定。`expected/*.card.json` は CLI の `--format card` 出力と一致させた完成例であり、フロー実装の突合せ用。受信日時だけは UTC 化すると FactSet の「受信」が CLI と不一致になる点に注意する。

## 7. 実装時のチェックリスト

- [ ] 差出人を社内 GitLab 通知アドレスに合わせた
- [ ] 本文をプレーンテキスト化し、`/-/issues/` 等が残ることを確認した
- [ ] `issue_created` / `mr_review_requested` / `pipeline_failed` の 3 フィクスチャ相当メールで `expected/` と突合した
- [ ] カードの FactSet 順・`markdown: false`・失敗時の Attention 色を確認した
- [ ] `unknown`（成功パイプライン・コメントのみ等）の扱いを決めた
- [ ] 受信日時を UTC のまま出すか、タイムゾーン変換するかを決めた
