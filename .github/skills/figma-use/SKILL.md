---
name: figma-use
description: Figma の URL やデザインが出たら使う。Figma REST API で画面の構造・スクショ・画像・SVG・スタイル・コンポーネントを取得し、不足情報を明示する（MCP 不要、Node.js 24）。「この Figma を見て」「Figma の画面を取ってきて」「Figma のアイコンを書き出して」「デザインどおり実装したいので情報を取って」「Figma のトークンを設定して」で発動。
metadata:
  version: 1.0.0
  tier: experimental
  category: integration
  tags:
    - figma
    - design
    - rest-api
    - node
---

# Figma Use

Figma の URL を受け取り、同梱の CLI で REST API から事実を取り、エージェントが読める形で保存する。
**このスキルが担うのは取得・正規化と、足りない情報の明示まで。** React の設計（既存部品への対応付け、
コンポーネント分割、Props・状態・Hooks）はこのスキルの責務外で、後工程のスキル・エージェントが行う。
受け渡し方は [references/handoff.md](references/handoff.md)。Figma への書き込みはしない。

Figma MCP と同じツール・同じ出力を提供するものではない（対応は末尾の表）。

## 前提

- Node.js 24 以上（標準モジュールだけで動く。`npm install` は不要）。Windows 11 の PowerShell と WSL2 Ubuntu で動く
- Figma の Personal Access Token。スコープは **`file_content:read` だけで足りる**
  - `file_metadata:read`: `--check-update` を使うときだけ
  - `file_variables:read`: `--variables` を使うときだけ（Enterprise プランのみ）
- 配置と PowerShell / WSL2 の例は [references/setup.md](references/setup.md)

### 認証情報（connections.yaml / FIGMA_TOKEN）

他の `*-use` スキルと同じ `connections.yaml` から読む。無ければ環境変数 `FIGMA_TOKEN` を使う。

```yaml
# {agent_dir}/connections.yaml（例: .github/connections.yaml・~/.claude/connections.yaml）
figma:
  - label: default
    token: ${FIGMA_TOKEN}       # 環境変数を参照する。トークンの値を直接書かない
  - label: client-a
    token: ${FIGMA_CLIENT_A_TOKEN}
```

探す順序は `$SKILL_CONNECTIONS_FILE` → ワークスペースの `{agent_dir}/connections.yaml` →
グローバルの `{agent_dir}/connections.yaml` → 環境変数 `FIGMA_TOKEN`。`--label` で切り替える。
**トークンをコマンド引数・ログ・成果物・Git 管理のファイルに書かない。** CLI もトークンを受け取る
引数を持たず、表示もしない。ユーザーがチャットにトークンを貼ったら使わずに、環境変数へ設定するよう頼む。

## ワークフロー

`F` は `node {skill_home}/figma-use/scripts/figma_cli.mjs` の略。URL は必ず引用符で囲む（`&` を含むため）。

1. **目的を確認する**: 対象の URL・ノードと、目的（情報取得だけ／設計書／実装）を確かめる。
   node-id の無い URL ならファイル全体が対象になるので、どの画面かを inspect の後で決める。
2. **キャッシュを確かめる**: 既に出力フォルダがあれば `manifest.json` の `file.version` と `generatedAt` を見て、
   足りていれば API を呼ばずにそれを読む。最新か確かめたいときだけ `--check-update`（API を 1 回呼ぶ）、
   取り直すなら `--refresh`。初回は `F doctor` で環境と認証情報の有無を見る（疎通は `--network <URL>`）。
3. **全体を把握する**: `F inspect "<URL>"` でページ・フレームの ID・名前・種類を見る。深く見る必要がある所だけ
   `--node <id> --depth 3` で掘る。ファイル全体を深く取らない（Tier 1 のレート制限が厳しい）。
4. **必要な画面・部品だけ取る**: `F export "<URL>" --node <id> [--node <id>...] --out figma-output`。
   構造だけなら `F context`。巨大な画面は `--depth` で抑え、足りない所を別ノードとして追加取得する。
5. **見た目と素材を確かめる**: `screenshots/` を開いて構造と見比べる。アイコンは `normalized/<id>.md` の
   「SVG 書き出しの候補」から選んで `--svg <id>`（まとめてなら `--svg-candidates`）。画像の塗りは自動で取る。
6. **読む順番**: `design-summary.md` → 画面ごとの `normalized/<id>.md` → 必要な部分だけ `normalized/<id>.json`
   → 元の値を確かめるときだけ `raw/`。大きな JSON を毎回全文読まない。
7. **不足を明示する**: `missing-information.md`（未取得・属性なし・アクセス不可・未解決・取得失敗と、
   API では取れない情報）を確認し、ユーザーに訊くべきことをまとめる。値が無い所を推測で埋めない。
8. **後工程へ渡す**: React の設計・実装を頼まれていれば、[references/handoff.md](references/handoff.md) の
   受け渡し項目をそろえて、設計・実装を担うスキル（例: react-frontend-coder）へ進む。このスキルの中で
   React の設計判断をしない。頼まれた範囲が情報取得だけなら、成果物の場所と不足情報を報告して終える。

## コマンド早見表

| コマンド | 用途 | 主なオプション |
|---|---|---|
| `doctor` | Node・認証情報の有無・connections.yaml・キャッシュの場所 | `--network <URL>`（API を 1 回呼ぶ） |
| `inspect` | ページ・フレーム・ノードの一覧 | `--node` `--depth`（既定 2） `--json` `--out` |
| `context` | ノードと子孫の生 JSON・正規化 JSON・Markdown | `--node`（複数可） `--depth` `--variables` `--mapping` `--code-connect-dir` |
| `screenshot` | ノードの画像を保存 | `--format png\|jpg\|svg\|pdf` `--scale`（0.01〜4） |
| `assets` | 画像の塗りと SVG、対応表 `assets-map.json` | `--svg <id>` `--svg-candidates` `--svg-keep-text` |
| `export` | 上をまとめて保存し、manifest・要約・不足情報を書く | context と assets のオプション全部 |

共通: `--label` `--refresh` `--check-update` `--file-version <id>` `--cache-dir` `--timeout` `--retries`。
詳細は `F <コマンド> --help`。出力の形は [references/output-schema.md](references/output-schema.md)、
使う API と制約は [references/api-reference.md](references/api-reference.md)。

終了コード: 0 成功 / 2 引数の誤り / 3 認証・権限 / 4 見つからない / 5 レート制限 / **6 一部失敗** /
7 通信・サーバー / 1 その他。6 のときは成果物が欠けている（`manifest.json` の `status: partial` と
`failures`）。成功として報告しない。

## エラーへの対処

| 症状 | 対処 |
|---|---|
| 終了コード 3（401・403） | トークンの期限・スコープ（`file_content:read`）・ファイルの共有権限を確認する。`F doctor` で出所を見る |
| 終了コード 4（404） | URL の file key・node-id を確かめる。branch の URL なら branch のまま渡す |
| 終了コード 5（429） | 時間を置く。キャッシュがある取得は `--refresh` を外せば API を呼ばない。表示された席の種別（Collab・View は上限が低い）をユーザーに伝える |
| 終了コード 7（接続エラー） | プロキシのある環境なら `NODE_USE_ENV_PROXY=1`（doctor が注意を出す）。それでも届かなければ、その環境から api.figma.com へ出られないことをユーザーに伝え、手元で `export` した成果物を渡してもらう |
| 終了コード 6 | `missing-information.md` の「取得失敗」を見て、対象を絞って再実行する |
| Figma Make・FigJam・Slides の URL | 対象外として拒否される。デザインファイル（/design/）の URL をもらう |

## Figma MCP の作業との対応

| Figma MCP でしていた作業 | このスキルでの代わり | 区分 |
|---|---|---|
| `get_metadata`（ノードの構造） | `inspect` | CLI で代替 |
| `get_screenshot` | `screenshot`（実ファイルまで保存） | CLI で代替 |
| `get_design_context`（構造・スタイルの文脈） | `context` / `export` の正規化 JSON と Markdown。コードの生成はしない | CLI ＋ エージェントの処理が必要 |
| `get_variable_defs`（変数） | `--variables`（Enterprise のみ）。使えなければバインド ID だけを残す | 条件付きで代替 |
| `get_code_connect_map` | REST API では取れない。`--mapping`（手動）か `--code-connect-dir`（Code Connect のファイルを読む） | 手動・リポジトリから代替 |
| 画像・アイコン素材 | `assets`（imageRef・node ID と保存先の対応表付き） | CLI で代替 |
| デザインからのコード生成・既存部品への置き換え | 後工程のスキル・エージェントが行う（handoff.md） | このスキルの対象外 |
| デザインシステムのルール作成 | 後工程のエージェントが行う | このスキルの対象外 |
| デスクトップアプリで選択中のノードを読む | 対応しない。URL の node-id か `--node` で指定する | 対象外 |
| Figma への書き込み（デザインの作成・変更） | 対応しない | 対象外 |
