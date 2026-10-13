# 使う Figma REST API と制約

2026-10-13 に公式ドキュメント（developers.figma.com/docs/rest-api/ の file-endpoints・scopes・
personal-access-tokens・file-node-types・file-property-types・variables-endpoints・rate-limits・errors）で
確かめた内容。Figma は上限やスコープを変えることがあるので、食い違ったら公式を正とする。

## 認証

- ヘッダー `X-Figma-Token: <Personal Access Token>`。トークンは作成時に一度だけ表示され、期限とスコープを作成時に決める
- Figma の Settings → Security で作成・失効する。身に覚えのない利用があれば即失効する

## 呼ぶエンドポイント

| CLI | エンドポイント | スコープ | レート制限の Tier |
|---|---|---|---|
| `inspect`（node 指定なし）・`doctor --network` | `GET /v1/files/:key?depth=N` | `file_content:read` | 1 |
| `inspect`（node 指定）・`context`・`export` | `GET /v1/files/:key/nodes?ids=…&depth=N` | `file_content:read` | 1 |
| `screenshot`・`assets --svg`・`export` | `GET /v1/images/:key?ids=…&format=…&scale=…` | `file_content:read` | 1 |
| `assets`・`export`（画像の塗り） | `GET /v1/files/:key/images` | `file_content:read` | 2 |
| `--variables` | `GET /v1/files/:key/variables/local` | `file_variables:read`（Enterprise の正規メンバーのみ） | 2 |
| `--check-update` | `GET /v1/files/:key/meta` | `file_metadata:read` | 3 |

- `:key` は file key か branch key。branch の URL（`/design/<file>/branch/<branch>/…`）では branch key を使う
- `ids` は複数をカンマで渡せる。CLI は nodes を 20 件、images を 10 件ずつまとめる
- `nodes` の応答で値が `null` のノードは、存在しないかアクセスできない。CLI は部分失敗として記録する
- `version` を渡すとその版を取る。応答の `version`・`lastModified` を manifest に残す（応答に無ければ null）

## 画像

- `GET /v1/images`: `format` は jpg / png / svg / pdf、`scale` は 0.01〜4。32 メガピクセルを超えると縮小される
- 応答の URL は一時的（30 日で失効）。CLI は実ファイルまで保存し、URL は成果物に残さない
- 値が `null` の ID はレンダリング失敗（非表示・不透明度 0・存在しないノードなど）
- SVG のオプション: `svg_outline_text`（既定 true。`--svg-keep-text` で false）、`svg_include_id`、`svg_simplify_stroke`、
  `contents_only`、`use_absolute_bounds`。CLI は `svg_outline_text` 以外を既定のままにする
- `GET /v1/files/:key/images` は画像の塗りの `imageRef` → URL（14 日以内に失効）の対応を返す

## ノードの属性（正規化で扱うもの）

- 共通: `id` `name` `type` `visible` `locked` `rotation` `boundVariables` `componentPropertyReferences`
- FRAME 系（GROUP・COMPONENT・COMPONENT_SET・INSTANCE も）: `absoluteBoundingBox` `layoutMode`
  `primaryAxisAlignItems` `counterAxisAlignItems` `padding*` `itemSpacing` `counterAxisSpacing` `layoutWrap`
  `layoutSizingHorizontal/Vertical` `layoutPositioning` `fills` `strokes` `cornerRadius` `rectangleCornerRadii`
  `effects` `opacity` `clipsContent` `constraints` `styles` `annotations`（現状 1 件まで） `interactions`
  `exportSettings` `devStatus`
- TEXT: `characters` `style`（TypeStyle: `fontFamily` `fontWeight` `fontSize` `lineHeightPx` `letterSpacing` …）
  `characterStyleOverrides` `styleOverrideTable`
- INSTANCE: `componentId` `componentProperties`（`type` `value` `boundVariables`） `overrides`
- COMPONENT / COMPONENT_SET: `componentPropertyDefinitions`
- `size`・`relativeTransform` は `geometry=paths` を付けたときだけ返る。CLI は付けない（`absoluteBoundingBox` を使う）
- 色は 0〜1 の `r` `g` `b` `a`。CLI は `#RRGGBB` と不透明度に直す

## Variables

- 値（`valuesByMode`）とコレクション・モード名は `variables/local` でしか取れない。Enterprise 以外は 403
- ノード側の `boundVariables` は `{ "type": "VARIABLE_ALIAS", "id": "VariableID:…" }`。CLI は解決できなくてもこの ID を残す
- 他ファイルのライブラリの変数は、このファイルの `variables/local` に含まれないことがある（未解決として残る）

## レート制限

- Tier 1（files・nodes・images）は 1 分あたり Pro 10 / Org 15 / Enterprise 20（Full・Dev 席）。
  **View・Collab 席は Tier 1 が月 20 回まで**、Tier 2 は 1 分 5 回、Tier 3 は 1 分 10 回
- Personal Access Token の上限は、作成者と、各ファイルのプランで決まる
- 429 の応答には `Retry-After`（秒）、`X-Figma-Plan-Tier`、`X-Figma-Rate-Limit-Type`（low = Collab・View）、
  `X-Figma-Upgrade-Link` が付く。CLI は `Retry-After` だけ待って再試行し（既定 3 回、120 秒を超える待ちはしない）、
  席の種別を表示する
- 「最新か」を確かめるのも API 呼び出しになる。CLI は既定でキャッシュを確かめずに使い、`--check-update`
  （Tier 3 の meta を 1 回）か `--refresh` を指定したときだけ呼ぶ

## エラー

| 状態 | 意味 | CLI の扱い |
|---|---|---|
| 400 | 引数の誤り、または取得量が大きすぎてタイムアウト | 再試行しない。ノード・depth を絞るよう案内 |
| 401・403 | トークン無効・期限切れ・スコープ不足・権限なし | 再試行しない。終了コード 3 |
| 404 | ファイル・ノードが無い | 再試行しない。終了コード 4 |
| 429 | レート制限 | `Retry-After` で再試行。尽きれば終了コード 5 |
| 500 | 大きなレンダリングのタイムアウトなど | 指数バックオフで再試行。尽きれば終了コード 7 |

## REST API で扱えないもの

- Figma Make・Figma Sites・Slides・FigJam ボードの URL（CLI が拒否する）
- Code Connect の対応（`--mapping` か `--code-connect-dir` で補う）
- Figma MCP 専用の機能（コード生成・デザインシステムのルール作成・デスクトップアプリの選択の読み取り）
- デザインの書き込み（このスキルの対象外）
