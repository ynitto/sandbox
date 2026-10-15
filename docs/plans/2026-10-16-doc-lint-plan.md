# リポジトリ横断ドキュメント評価・規約管理ツール 計画書

- 状態：設計案（改訂 2）
- 作成日：2026-10-15
- 改訂日：2026-10-16
- 名称：doc-lint
- 基本方針：LLM に依存せず、Node.js だけで動く既存 OSS を組み合わせる

## 改訂履歴

| 日付 | 内容 |
| --- | --- |
| 2026-10-15 | 初版 |
| 2026-10-16 | yomiyasu を評価器から外した。Java と Chromium を必須依存から外し、図の検証を純 JS／WASM の実装に替えた。対応 OS と名称を確定した。既存スキルとの境界を 17 章に追加した。 |

## 1. 目的

リポジトリ内の文書群を対象に、文書単体の品質と、ファイル間の整合性を評価する。

評価対象は、重複、矛盾、用語、文体、構成、参照関係、図と本文の整合性とする。

横断評価から共通ルールや文書種別ごとのフォーマット候補を抽出し、採用した規約を執筆と次回以降の検査で共有する。

CLI を操作の入口とし、一般的な linter／test runner と同様の出力・終了コード・CI 連携を提供する。

## 2. 基本方針

- 基本機能は LLM を使わず、構文解析、辞書、ルール、統計的類似度で実現する。
- 実行基盤は Node.js だけとする。Java、ブラウザ（Chromium）、Python を必須にしない。
- 対応 OS は Windows、macOS、Linux とする。
- 依存は npm パッケージとして管理し、リポジトリごとのロックファイルで固定する。
- 設定と依存バージョンはリポジトリごとに管理する。
- 執筆にも使う規約は、対象文書と同じフォルダに配置する。
- 通常の検査では文書や規約を書き換えない。
- 自動抽出した規約は候補とし、明示的な採用操作を経て適用する。
- 確定的な違反と、人による確認が必要な候補を区別する。

## 3. 対象ファイル

初期対象：

- Markdown
- AsciiDoc
- PlantUML
- Mermaid
- Markdown／AsciiDoc 内に埋め込まれた図

必要に応じて、以下も仕様の照合元として扱う。

- 設定ファイル
- API 定義
- スキーマ
- 正式な用語辞書・仕様値一覧

## 4. 全体構成

```text
リポジトリの文書・設定・スキーマ
    │
    ▼
収集・形式別解析
    │ 本文、見出し、段落、表、参照、図、出典位置
    ▼
共通の文書モデル・横断索引
    │
    ├─ 単体評価
    │    textlint、markdownlint、図の構文検証
    │
    └─ 横断評価
         重複、用語、文体、構成、仕様、参照、図
    │
    ▼
結果の統合・傾向分析
    ├─ コンソール／JSON／SARIF／HTML／CI 判定
    │
    └─ ルール・フォーマット候補
                  │
                  ▼
            レビュー・採用
                  │
                  ▼
               規約管理
                  │
                  └─ 執筆ガイド・テンプレート・次回評価へ反映
```

既存ツールの結果を変換するアダプターと、文書群を比較する横断評価器を分離する。

## 5. 技術構成

| 役割 | 採用候補 | 実行基盤 |
| --- | --- | --- |
| CLI・実行管理 | Node.js／TypeScript | Node.js |
| Markdown 解析 | unified／remark | Node.js |
| AsciiDoc 解析 | Asciidoctor.js | Node.js（Ruby 版を JS に変換したもの。Java 不要） |
| 文章評価 | textlint | Node.js |
| Markdown 構文検査 | markdownlint | Node.js |
| Mermaid 構文検証 | `mermaid` の `parse()` ＋ `jsdom` | Node.js（Chromium 不要） |
| PlantUML 構文検証 | `@plantuml/mcp-js` 同梱の TeaVM 版エンジン ＋ `@viz-js/viz` | Node.js ＋ WASM（Java・Graphviz 不要） |
| 重複・類似分析 | 正規化ハッシュ、文字 n-gram | Node.js |
| 保存 | JSON | ― |
| 出力 | コンソール、JSON、SARIF、HTML | ― |

TF-IDF・MinHash・SQLite は、文字 n-gram の総当たりと JSON 保存で処理が追いつかなくなった時点で導入する。それまでは入れない。

### 5.1 図の検証方式（2026-10-16 検証済み）

Node.js v24 上で次を確認した。

**Mermaid**（`mermaid@12.2.0`、`jsdom`）

- `jsdom` で `window`／`document` を用意すると、`mermaid.parse()` が flowchart、sequence、class、state、ER、gantt、pie、mindmap、architecture のすべてで動いた。
- 構文誤りは `Parse error on line N` として行番号付きで返る。
- `jsdom` がないと、flowchart・class・state・gantt が `DOMPurify.addHook is not a function` で失敗する。`jsdom` は必須依存とする。
- 依存の合計は約 225 MB。

**PlantUML**（`@plantuml/mcp-js@0.2.2`、エンジン PlantUML 1.2026.8）

- 公式が TeaVM で JavaScript に変換したエンジンで、`checkSyntax()` / `renderSvg()` / `explain()` / `version()` を export している。
- `checkSyntax()` は `{valid, diagramType, errorLineNumber, errorMessage, errorLine}` の JSON を返す。
- `explain()` は行ごとに「クラス Order を作成」「Order から Customer への関連」のような解釈と行番号を返す。8.4 の図と本文の照合で、名称と関係の抽出に使える見込みがある。ただし出力は英文の説明文なので、パース方法は Phase 4 で検証する。
- Graphviz が必要な図（class、state、component など）は、グローバルの `Viz` に `@viz-js/viz`（Graphviz の WASM 版）を渡して動かす。`@plantuml/mcp-js/server.js` の方式をそのまま使う。
- エンジンは `console.log` に出力することがあるため、標準出力を結果専用にする 13.1 の方針に合わせて、呼び出し中は標準エラーへ逃がす。
- 既知の限界：`if` を閉じない activity 図が `valid: true` になった。Java 版と同じ判定かは未確認。構文検証は「Java 版で通るものを落とさない」ことを優先し、検出漏れは許容する。
- ライセンスは `@plantuml/core`、`@plantuml/mcp-js` とも MIT。
- `@plantuml/mcp-js` は MCP サーバとしての配布物で、`engine.js` を部品として使うのは想定外の使い方になる。`@plantuml/core` が Node 向けの `checkSyntax` を公開すれば乗り換える。

## 6. 共通の文書モデル

### 6.1 抽出する情報

- ファイルパス、形式、言語、文書種別
- メタデータ
- 見出し、節、段落、リスト、表
- コードブロック、引用、図
- リンク、アンカー、include、相互参照
- 用語、名称、識別子
- 文末、文長などの文章統計
- 対象・属性・条件・値・単位からなる仕様上の事実
- 元ファイルの行・列

本文、コード、引用、図を区別し、文章用ルールをコードなどへ誤適用しない。

正規化や include 展開後も元文書への対応を保持する。図の検証結果は図の中の行番号で返るため、埋め込み図は文書内の開始行を足して元文書の行に戻す。位置情報を正確に取得できない場合は、その精度を明示する。

### 6.2 文書種別

README、設計書、手順書、ADR などの種別を扱う。

種別は設定やメタデータによる明示指定を優先する。推定する場合は根拠を提示し、手動で修正できるようにする。

## 7. 単体評価

既存の linter をアダプター経由で実行し、元評価器の rule ID と重要度を保持して共通の指摘形式に変換する。

| 評価器 | 対象 | 備考 |
| --- | --- | --- |
| textlint | Markdown、AsciiDoc（プラグイン経由） | 日本語の文章ルール |
| markdownlint | Markdown | 構文・書式 |
| Mermaid 構文検証 | `.mmd`、埋め込み Mermaid | 5.1 参照 |
| PlantUML 構文検証 | `.puml`／`.plantuml`、埋め込み PlantUML | 5.1 参照 |

AsciiDoc の textlint 対応は、`textlint-plugin-asciidoctor` 等の保守状況を Phase 0 で確認する。使えなければ、Asciidoctor.js で本文を取り出して textlint に渡す。

異なる評価器の点数を無条件に平均しない。

## 8. 横断評価

| 評価軸 | 方法 | 主な出力 |
| --- | --- | --- |
| 完全重複 | 正規化した段落・節のハッシュ比較 | 同一内容の所在 |
| 類似重複 | 文字 n-gram | 類似箇所、類似度 |
| 用語 | 推奨語・別名辞書、出現分布 | 表記揺れ、名称の不一致 |
| 文体 | 文末、文長などの分布 | 文体違反、種別内のばらつき |
| 構成 | 見出し・メタデータのスキーマ照合 | 必須項目不足、順序・階層の不統一 |
| 仕様 | 対象・属性・条件・値・単位の照合 | 同一条件での仕様値の不一致 |
| 参照 | 文書間のリンク・include・アンカーのグラフ | 参照先欠落、孤立文書 |
| 図と本文 | 名称・識別子・関係の照合 | 名称や関係の不一致 |

### 8.1 重複

段落単位と節単位で評価する。

定型文、ライセンス文、意図的な転載などは除外できるようにする。類似箇所は、直ちに違反とせず整理候補として提示する。

### 8.2 文体・構成

同じ文書種別の中で比較する。

README、設計書、手順書などを一律の文体・構成に揃えることは目的としない。

### 8.3 仕様の矛盾

以下の単位で事実を抽出・比較する。

```text
対象 ＋ 属性 ＋ 適用条件 ＋ 値 ＋ 単位 ＋ 出典
```

環境、バージョン、単位などを揃えて比較する。

初期対象：

- タイムアウト
- ポート
- 対応バージョン
- 上限値
- デフォルト値

照合元は、文書同士と、利用者が設定で明示した正式な設定ファイル・スキーマに限る。実装コードとの照合は扱わない（17 章）。多数の文書に書かれている値を、自動的に正しい値とはみなさない。

自然言語全般の意味的な矛盾は保証せず、確定できないものは確認候補として扱う。

### 8.4 図

構文検証と意味解析を分離する。

初期は名称・識別子の照合を優先する。PlantUML は `explain()` の出力、Mermaid は `parse()` が返す図の内部データから名称と関係を取り出せるかを Phase 4 で検証し、取り出せた図種別から対応する。対応図種別は明示する。

## 9. 共通ルール・フォーマット管理

### 9.1 管理対象

| 対象 | 内容 |
| --- | --- |
| 共通ルール | 用語、表記、リンク形式 |
| 種類別ルール | 文体、記述規則 |
| 構成スキーマ | 必須見出し、順序、メタデータ |
| 執筆テンプレート | 記入例を含む Markdown／AsciiDoc |
| 図の規約 | 名称、識別子、凡例 |
| 仕様の照合規則 | 正式な参照元、属性、適用条件 |

検査用スキーマと、執筆用テンプレートは分離する。

### 9.2 候補の抽出

以下の傾向から候補を抽出する。

- 用語の出現頻度
- 見出し列の共通部分
- メタデータの出現率
- 文末の分布
- 図の名称・識別子のパターン

候補には、出典、対象件数、出現率、例外を付ける。サンプル数が少ない場合は明示する。

### 9.3 ライフサイクル

```text
候補 → 採用 → 非推奨 → 廃止
```

規約には以下を持たせる。

- 安定した ID
- 適用範囲
- 根拠・出典
- 状態
- 責任者
- バージョン
- 変更理由
- 重要度・閾値
- 例外

変更前に既存文書へ試験適用し、増減する指摘と影響ファイルを確認できるようにする。

## 10. 規約の配置と継承

設定はリポジトリごとに管理する。

用語、文体、構成など、執筆にも利用する規約は対象文書と同じフォルダに配置する。

```text
repository/
  package.json          # doc-lint と評価器・ルールの依存
  package-lock.json     # 解決済みバージョン（tools.lock の役割）
  .doc-lint/
    config.yaml
    exceptions.yaml
    candidates/

  docs/
    DOC_RULES.md
    doc-rules.yaml
    glossary.yaml

    design/
      DOC_RULES.md
      doc-rules.yaml
      templates/
        design.md
```

役割：

- config.yaml：評価器、プリセット、対象、出力、CI 設定
- package.json／ロックファイル：依存ツール・ルールの固定バージョン
- exceptions.yaml：除外、理由、期限
- doc-rules.yaml：機械可読な規約の正本
- DOC_RULES.md：規約から生成する執筆ガイド
- glossary.yaml：推奨語、別名、禁止表記
- templates/：執筆用のひな形

親フォルダの規約を継承し、近いフォルダと個別文書の指定を優先する。共通の必須規約には上書き禁止を指定できるようにする。

生成ガイドには正本と再生成方法を記載し、正本とのずれを検出する。

個別文書の設定例：

```yaml
version: 1

defaults:
  style: desu-masu

documents:
  architecture.md:
    type: design
    style: dearu

  recovery.md:
    type: runbook
    requiredSections:
      - 前提条件
      - 手順
      - 確認方法
      - 復旧方法
```

パスは設定ファイルの所在を基準に解決する。

## 11. 導入と依存管理

### 11.1 方針

すべての評価器・ルール・図エンジンが npm パッケージなので、依存の解決・固定・導入は npm に任せる。doc-lint 独自のロックファイルや導入処理は作らない。

- 前提は Node.js だけ（バージョンは Phase 0 で決める。検証は v24 で行った）。
- npm を使う理由は、Node.js に同梱されていて Windows・macOS・Linux で追加の導入が要らないため。
- パッケージは各リポジトリの `node_modules` に入り、グローバル環境は変更しない。
- リポジトリ間で設定や依存バージョンは混在しない。

### 11.2 推奨プリセット

日本語向けの推奨プリセットを提供する。

textlint の候補：

- textlint-rule-preset-ja-technical-writing
- textlint-rule-preset-japanese
- textlint-rule-prh

ルールの重複・競合を確認して採用する。

追加、削除、有効化、無効化、オプション変更をリポジトリ設定で行う。他の評価器も同じ方式で管理する。

### 11.3 再現性

- `doc-lint install` は `npm install`、`doc-lint install --frozen-lockfile` は `npm ci` を呼ぶ。
- CI ではロック固定モードを使う。
- doctor で Node.js のバージョン、不足パッケージ、評価器が実際に起動するか（図エンジンの WASM 読み込みを含む）を確認する。
- check は暗黙のインストールを行わない。

## 12. CLI

以下はコマンド案とする。

```sh
doc-lint init --preset ja
doc-lint install
doc-lint install --frozen-lockfile
doc-lint doctor

doc-lint check .
doc-lint check docs/ --format stylish
doc-lint check . --format json
doc-lint check . --format sarif --output results.sarif
doc-lint check . --format html --output report.html

doc-lint tools add <tool-or-rule>
doc-lint tools remove <tool-or-rule>

doc-lint rules discover docs/
doc-lint rules preview <candidate-id>
doc-lint rules apply <candidate-id>
```

- tools add/remove は config.yaml と package.json を更新し、npm で導入・削除する。
- 設定を直接編集し、install で同期する運用にも対応する。
- rules discover は候補を生成する。
- rules preview は規約の差分と影響を表示する。
- rules apply は明示操作として規約・ガイド等を更新する。
- check は検査対象と横断比較の対象範囲を明示する。

npm で同名の別パッケージ `doc-lint` が公開されていないかは Phase 0 で確認する。

## 13. 出力仕様

### 13.1 コンソール

一般的な linter／test runner に準じる。

```text
docs/api.md:42:1  error    spec/value-conflict
  timeout が docs/design.md:88 の記述と一致しません
  api.md: 30s / design.md: 60s

docs/guide.md:18:3  warning  terminology/preferred-term
  「ユーザ」ではなく「ユーザー」を使用してください

12 files checked, 1 error, 1 warning
```

標準出力は評価結果、標準エラーは進捗と実行診断に使う。JSON 出力にログを混ぜない。

### 13.2 構造化出力

指摘には以下を含める。

- ルール ID
- 主たるファイル・行・列
- 比較相手などの関連位置
- メッセージ
- 重要度
- 確度
- 検出根拠
- 元評価器の情報
- ツール・規約バージョン

JSON、SARIF、HTML を提供する。

### 13.3 終了コード

| コード | 意味 |
| --- | --- |
| 0 | 設定した失敗条件に該当する指摘なし |
| 1 | 品質検査による失敗 |
| 2 | 設定不備、必須依存不足、評価器の実行失敗 |

警告の扱い、重大度の閾値、新規指摘だけを失敗にするかを設定できるようにする。

既定では error だけを失敗とする。これ以上の閾値は doc-lint 側で決めず、利用者がリポジトリごとに設定する。

必須評価器の実行失敗は成功扱いにしない。

### 13.4 指標と差分

- 重複率
- 用語違反数
- 構成準拠率
- 仕様不整合件数
- 前回からの増減
- 新規指摘
- 評価できなかった形式・項目

各指標の分母と除外条件を明示する。指標の合否閾値に既定値は置かない。

総合点は補助とし、重大な不整合は独立表示する。確度と重要度を分離し、確認候補と確定的な違反を混同しない。

## 14. 実装段階

### Phase 0：調査・仕様固定

- 図の構文検証を Java・Chromium なしで行う方式を確認する（済。5.1）。
- 対応 OS を決める（済。Windows、macOS、Linux）。
- 3 OS で図エンジンと textlint が動くことを CI で確認する。
- 必要な Node.js の最低バージョンを決める。
- AsciiDoc を textlint に通す方法を決める。
- 推奨プリセットと競合解決方針を決める。
- CLI、共通文書モデル、指摘形式、規約スキーマを定義する。

受け入れ条件：

- LLM 不要の評価経路が明確である。
- Node.js 以外の実行基盤なしで全評価器が動く。
- 対応範囲と未対応範囲が明示されている。

### Phase 1：導入と単体評価

- init、install、doctor、check を実装する。
- textlint、markdownlint、図の構文検証を統合する。
- 推奨ルールの導入と追加・削除に対応する。
- コンソール、JSON、SARIF と終了コードを提供する。

受け入れ条件：

- Windows、macOS、Linux で `npm ci` だけで必要な依存が揃う。
- リポジトリごとに異なる設定・バージョンを使用できる。
- ロックファイルから再現できる。
- 品質違反と実行失敗を区別できる。

### Phase 2：基本の横断評価

- 重複、用語、文体、構成、参照を実装する。
- 文書種別、関連位置、除外を扱う。
- 処理時間をファイル数と合わせて記録し、性能の基準値を取る。

受け入れ条件：

- 複数形式・複数ファイルの既知の問題を検出できる。
- 比較対象の両側の位置を提示できる。
- 文書種別と除外に応じて比較範囲が変わる。

### Phase 3：規約の抽出と共有

- 共通ルール・種類別フォーマット候補を抽出する。
- 試験適用、採用、変更、廃止に対応する。
- フォルダ継承と個別文書設定を実装する。
- 執筆ガイドとテンプレートを生成する。

受け入れ条件：

- check は文書や規約を書き換えない。
- 採用操作で対象文書のそばに規約を作成・更新できる。
- 出典、例外、変更履歴を保持できる。
- ガイドと正本のずれを検出できる。

### Phase 4：整合性評価の拡張

- 仕様値・条件を抽出する。
- 設定・スキーマとの照合に対応する。
- 対応する図種別と本文の整合性を検査する。
- 履歴、ベースライン、HTML レポート、キャッシュを拡張する。

受け入れ条件：

- 環境やバージョンの違いを誤って矛盾にしない。
- 対応図種別で本文との不整合を検出できる。
- 未対応・未評価範囲を明示できる。

## 15. 検証方針

- 各形式の代表例と境界例を含む検証用リポジトリを用意する。
- 横断指摘の位置、仕様の条件、規約継承、例外を重点的に検証する。
- install の再実行、追加・削除、固定バージョン、依存失敗を 3 OS で確認する。
- 図の構文検証は、Java 版 PlantUML と Mermaid CLI で通る図を誤って落とさないことを確認する（比較は開発時だけ行い、利用者環境には入れない）。
- JSON／SARIF の妥当性と終了コードの契約を検証する。
- 類似度・候補抽出は、人が判定した例で誤検知と見逃しを評価する。
- 同じ入力・設定・依存バージョンで結果が安定することを確認する。

## 16. 確定事項と未確定事項

確定：

| 項目 | 決定 |
| --- | --- |
| 名称 | doc-lint |
| 対応 OS | Windows、macOS、Linux |
| 実行基盤 | Node.js のみ |
| yomiyasu | 使わない |
| 図の構文検証 | Mermaid は `mermaid` ＋ `jsdom`、PlantUML は TeaVM 版エンジン ＋ `@viz-js/viz` |
| 閾値 | 既定は error のみ失敗。その他は利用者がリポジトリごとに設定する |
| 性能目標 | 定めない。Phase 2 で処理時間を記録し、必要になった時点で決める |

未確定（Phase 0 で決める）：

- Node.js の最低バージョン
- npm 上の名称衝突の有無と配布方式
- AsciiDoc を textlint に通す方法
- 推奨ルールのバージョンと競合解決
- 最初に意味解析へ対応する図種別

## 17. 既存スキルとの境界

手元の既存スキルと役割が重なる部分を確認し、doc-lint の範囲を次のように決めた。

| 既存スキル | 重なる部分 | doc-lint の扱い |
| --- | --- | --- |
| codd-gate | 文書からコードへの参照切れ（broken-ref、dangling-ref）、Markdown リンクの解決、コード変更に文書が追随しているか（doc-stale）。python3 と git で動く決定的な CLI | 文書から実装コードへの参照とコード変更への追随は codd-gate に任せる。doc-lint の参照評価は文書同士のリンク・アンカー・include に限る。Markdown のファイルリンク切れは両方が検出しうるので、併用時は片方で除外できるよう設定項目を用意する |
| doc-drift-detector | 文書に書かれた設定値・コマンド・API とコードの照合（8.3 と近い） | LLM が判断するスキルなので、決定的な doc-lint には取り込まない。doc-lint は文書同士と、明示した正式な設定・スキーマとの照合だけを扱う |
| grill-with-docs | `CONTEXT.md` に確定した用語を記録する | 用語の正本は glossary.yaml とする。`CONTEXT.md` を用語辞書の入力として読み込めるようにするかは、利用者の要望が出てから決める |
| mermaid-diagrammer | Mermaid の検証に `mmdc`（Chromium 依存）を使う | doc-lint 側は Chromium を使わない方式を採る。mermaid-diagrammer の検証を 5.1 の方式に寄せるかは、そちらのスキルの判断とする |
| spec-value-finder | Office 文書から仕様値を出典付きで探す | 対象形式と目的が違う（記入シートを埋める）。重なりなし |
| slop-police、technical-writer、agent-reviewer | 日本語の文章品質・文書レビュー | いずれも LLM が読む指示書で、機械検査ではない。doc-lint が生成する DOC_RULES.md を、これらのスキルが執筆時に参照する関係になる |
