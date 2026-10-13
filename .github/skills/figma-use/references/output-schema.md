# 出力の形

## 目次

- [manifest.json](#manifestjson)
- [normalized/&lt;id&gt;.json](#normalizedidjson)
- [missing の理由](#missing-の理由)
- [assets-map.json](#assets-mapjson)
- [手動マッピング（--mapping）](#手動マッピング--mapping)

`export` は `--out`（既定 `figma-output`）に次を書く。`context`・`screenshot`・`assets` は同じ置き場の一部だけを書く。

```
figma-output/
  manifest.json            取得の識別情報と成果物の一覧
  raw/                     API の応答（ノードごと）。元の値はここで確かめる
    nodes-<id>.json
    variables-local.json   --variables で取れたときだけ
  normalized/              正規化した JSON と、それを読みやすくした Markdown
    <id>.json
    <id>.md
  screenshots/<id>.png     ノードの画像
  assets/
    images/<imageRef>.<ext>   画像の塗り（拡張子は中身から決める）
    svg/<名前>--<id>.svg      指定したノードの SVG
    assets-map.json           imageRef・node ID と保存先の対応
  design-summary.md        画面・部品ごとの要約と参照先（事実だけ）
  missing-information.md   足りない情報を理由ごとに
```

ファイル名の `<id>` は node ID の `:` を `-`、`;` を `_` にしたもの（`1:2` → `1-2`、`I1:2;3:4` → `I1-2_3-4`）。
Windows で使えない文字・予約名（CON など）は置き換え、大文字小文字だけ違う名前は `-2` を付けて分ける。
出力先の外へは書かない。成果物の中のパスは出力先からの相対で、区切りは `/`。

## manifest.json

```json
{
  "tool": "figma-use",
  "cliVersion": "1.0.0",
  "generatedAt": "2026-10-13T12:00:00.000Z",
  "status": "complete | partial",
  "source": { "url": "共有用の t= などを落とした URL", "fileKey": "API に渡した key（branch なら branch key）",
              "mainFileKey": "…", "branchKey": null, "nodeIds": ["1:2"], "requestedVersion": null, "depth": null },
  "file": { "name": "…", "version": "応答の version（無ければ null）", "lastModified": "…", "editorType": "figma" },
  "cache": { "used": true, "hits": [{ "endpoint": "…", "fetchedAt": "…", "version": "…" }], "nodesFetchedAt": "…" },
  "variables": "not_requested | resolved | inaccessible | failed",
  "apiCalls": 8,
  "artifacts": {
    "byNode": { "1:2": { "raw": "raw/nodes-1-2.json", "normalized": "normalized/1-2.json",
                         "markdown": "normalized/1-2.md", "screenshot": "screenshots/1-2.png" } },
    "assets": ["assets/images/….jpg", "assets/svg/….svg"],
    "assetsMap": "assets/assets-map.json", "summary": "design-summary.md", "missing": "missing-information.md"
  },
  "failures": [{ "target": "1:9", "stage": "render-png", "message": "…" }]
}
```

`status: partial` のときは `failures` に欠けた物がある。トークンは manifest にもキャッシュにも書かない。

## normalized/&lt;id&gt;.json

```json
{
  "source": { "fileKey": "…", "nodeId": "1:2", "version": "…", "lastModified": "…", "fetchedAt": "…" },
  "nodeId": "1:2",
  "root": { "id": "1:2", "name": "Login", "type": "FRAME", "box": { "x": 0, "y": 0, "width": 375, "height": 812 },
            "layout": { "mode": "VERTICAL", "padding": { "top": 48, "right": 24, "bottom": 48, "left": 24 }, "gap": 16 },
            "fills": [{ "type": "SOLID", "color": "#FFFFFF" }], "styles": { "fill": { "id": "S:bg", "name": "Surface/Default" } },
            "children": [ … ] },
  "missing": [{ "nodeId": "1:3", "nodeName": "Title", "field": "styles.fill", "reason": "unresolved", "detail": "…" }],
  "facts": { "colors": [], "typography": [], "texts": [], "components": [], "imageFills": [],
             "svgCandidates": [], "interactions": [], "annotations": [], "variables": [] },
  "stats": { "nodes": 9 }
}
```

ノードの主なキー（**応答に元の属性が無ければキーごと省く**。既定値で補わない）:

| キー | 元の属性 |
|---|---|
| `visible: false` / `locked` | `visible` / `locked`（表示中なら省く） |
| `box` | `absoluteBoundingBox`（小数第 2 位で丸める） |
| `layout.mode` `wrap` `primaryAxisAlign` `counterAxisAlign` `padding` `gap` `counterAxisGap` `grid` | Auto Layout・グリッドの属性 |
| `layout.self` | 親の Auto Layout の中での振る舞い（`layoutSizing*` `layoutAlign` `layoutGrow` `layoutPositioning` `min/max*`） |
| `layout.constraints` `layout.clipsContent` | 同名 |
| `fills` `strokes` `stroke` `cornerRadius` `cornerRadii` `effects` `opacity` `blendMode` | 同名。色は `#RRGGBB` と `alpha` |
| `text.characters` `text.style` `text.styleOverrides` | TEXT の `characters` `style` `styleOverrideTable` |
| `styles.<役割>` | `styles` の ID と、応答の styles にある名前 |
| `component` | INSTANCE: `componentId` 名前・key・セット名・`properties`・`overriddenFields`。COMPONENT 系: `propertyDefinitions` |
| `codeMapping` | `--mapping` / `--code-connect-dir` で見つけたコード側の部品（事実の転記。正しさは判断しない） |
| `variables` | 変数バインド `{ field, variableId, resolved? }` |
| `annotations` `interactions` `transitionNodeID` `exportSettings` `devStatus` | 同名 |
| `childrenStatus: "not_fetched"` | depth の制限で子を取っていない |

## missing の理由

| reason | 意味 | 例 |
|---|---|---|
| `not_fetched`（未取得） | 取得範囲の外。取り直せば得られる | depth の制限で子が無い |
| `absent`（属性なし） | 応答に属性が無かった | `absoluteBoundingBox` が無い、メインコンポーネントのメタデータが無い |
| `inaccessible`（アクセス不可） | 権限・プランで取れない | `--variables` が 403 |
| `unresolved`（未解決） | ID はあるが中身を引けない | スタイル名・変数の値・範囲外の遷移先 |

API 呼び出しやダウンロードの失敗は missing ではなく manifest の `failures`（missing-information.md の「取得失敗」）に入る。

## assets-map.json

```json
{
  "imageFills": [{ "imageRef": "abc…", "nodeIds": ["1:5"], "scaleMode": "FILL", "file": "assets/images/abc….jpg" }],
  "svg": [{ "nodeId": "1:6", "name": "icon/close", "file": "assets/svg/icon_close--1-6.svg" }],
  "svgNotes": ["SVG をコードとして扱うときの注意"]
}
```

`file: null` は保存に失敗したもの（理由は manifest の `failures`）。

### SVG をコードとして扱うときの注意

- Figma のレンダリング結果であり、元のレイヤー構造やコンポーネントの意味は残らない
- 文字は既定でアウトライン化される。文字のまま欲しければ `--svg-keep-text`
- 色は直値で入る。テーマで色を変えるなら `fill`・`stroke` を `currentColor` などに置き換えるかを決める
- 埋め込む前に不要な `id`・クリップパス・サイズ属性を整理し、外部から来た SVG として `script`・外部参照が無いか確かめる

## 手動マッピング（--mapping）

```json
{
  "components": {
    "5:0": { "component": "Button", "import": "@/components/ui/Button" },
    "button-set-key": { "component": "Button" },
    "Avatar": { "component": "UserAvatar", "import": "@/components/UserAvatar" }
  }
}
```

キーは node ID（`5:0` / `5-0`）、コンポーネント（セット）の key、名前のどれでもよい。照合は
コンポーネント ID → セット ID → key → セットの key → 名前の順。値はそのまま `codeMapping` に入る。
`--code-connect-dir <dir>` は `*.figma.tsx` などの `figma.connect(部品, "URL")` を読み、URL の node-id をキーにする。
