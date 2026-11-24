# devkit

IT 系の小物 CLI 10本を、1つのコマンド `devkit` のサブコマンドに統合したツールキットです。
Node.js の標準ライブラリだけで動作し、外部依存はありません。
各コマンドは元の独立ツール（jwt-inspect、ip-calc など）の挙動・オプション・終了コードをそのまま再現しています。

## ローカルコマンドとして登録する

`npm link` を使うと `devkit` が PATH に入り、どこからでも呼べます。

```sh
cd tools/devkit
npm link
devkit --help
```

登録を解除するときは `npm unlink -g devkit` です。
代わりに `npm install -g .` でも登録できます。
登録しなくても、ファイルを指定すればそのまま使えます。

```sh
node tools/devkit/bin/devkit.js jwt "$TOKEN"
```

必要な Node.js は 18 以降です。

## 使い方

```sh
devkit <コマンド> [引数]
devkit <コマンド> --help   # 各コマンドの使い方
```

| コマンド | 説明 | 元ツール |
| --- | --- | --- |
| `jwt` | JWT の中身を表示し、時刻クレームと署名を確認する | jwt-inspect |
| `ip` | IPv4 / IPv6 のサブネット計算、包含・重なりの判定 | ip-calc |
| `env` | 2つの .env ファイルを比較する（値は既定で隠す） | env-diff |
| `cron` | cron 式から次回の実行時刻を表示する | cron-next |
| `csv` | CSV の行数・列ごとの型や分布などの概要を表示する | csv-peek |
| `ts` | Unix epoch と ISO 8601 日時を相互変換する | timestamp-converter |
| `semver` | SemVer の検証・比較・bump・レンジ判定 | semver-tool |
| `toc` | Markdown の目次を生成・更新する | markdown-toc |
| `url` | URL の分解表示とクエリパラメータの編集 | url-inspect |
| `branches` | 古いローカル Git ブランチの棚卸しと掃除候補の表示 | git-stale-branches |
| `uuid` | UUID v4 を生成し、指定された UUID の形式を検証します | — |
| `pw` | 暗号学的乱数でパスワードを生成します | — |
| `b64` | Base64 をエンコードまたはデコードします | — |
| `hash` | ハッシュまたは HMAC を計算します | — |
| `json` | JSON を整形・検証し、指定したパスから値を抽出します | — |
| `regex` | JavaScript の正規表現で全マッチを検索し、必要に応じて置換します | — |
| `dns` | 指定したホストの DNS レコードを取得します | — |
| `http` | HTTP(S) 応答のステータス、所要時間、ヘッダを表示します | — |
| `port` | 指定したホストの TCP ポートに接続できるかを調べます | — |
| `cert` | TLS 証明書の発行先、発行者、有効期間、残り日数、SAN を表示します | — |

## プログラムから使う（API）

ClipIt のようなホストアプリでは、`listCommands()` をコマンド選択メニューに使い、
選択したコマンドを `runCommand()` で実行できます。`example` には各コマンドの実行例が入ります。以下の例では `token` に画面から受け取った JWT を渡します。

```js
const { listCommands, runCommand, run } = require("./tools/devkit");

const commands = listCommands(); // [{ name, help, usage, example }, ...]

async function inspectToken(token) {
  const { code, stdout, stderr } = await runCommand("jwt", [], { stdin: token });
  return { code, text: code === 0 ? stdout : stderr };
}
```

`run(["jwt", token])` のように、コマンド名を含む引数列も渡せます。
実行結果の `stdoutBuffer` には、捕捉した標準出力が Buffer で入ります。
全コマンドの出力が API の戻り値で取得できます（`csv` の集計結果を含む）。
終了コード `1` はコマンド固有の失敗と、API 側で捕捉した予期しない例外の両方を表します（区別が必要な場合は `stderr` の内容を見てください）。`options.stdin` に string / Buffer 以外を渡すなど API の使い方が不正な場合は、結果オブジェクトではなく TypeError が投げられます。

## 終了コード

全体の約束は次のとおりです（詳細は各コマンドの節）。

| コード | 意味 |
| --- | --- |
| 0 | 正常 |
| 1 | チェック不一致（差分あり、範囲外、目次が古い、など） |
| 2 | 入力・使用法エラー（理由は標準エラーに出力） |

例外として `jwt` は署名検証の不一致・検証不可で 3 を返します。

## コマンド詳細

### jwt

```sh
devkit jwt "$TOKEN"
printf '%s\n' "$TOKEN" | devkit jwt
devkit jwt --file token.txt --tz Asia/Tokyo
devkit jwt --secret "$JWT_SECRET" "$TOKEN"
```

Header と Payload を整形して表示し、署名の有無とバイト数を表示します。
位置引数、`--file`、標準入力の順で入力を選び、トークン中の空白と改行は除去します。
`exp`、`nbf`、`iat`、`auth_time` は日時として表示し（既定は UTC、`--tz` で IANA タイムゾーン指定可）、
期限切れと有効開始前の警告を標準出力に出します。警告は終了コードに影響しません。
既定では署名を検証せず、`--secret` を指定した場合に限り HS256 を検証します。

終了コード: 0=デコード成功（`--secret` 時は検証も成功）／1=実行時エラー（ファイル読み込み失敗、タイムゾーン指定エラー、空の `--secret`）／2=JWT の形式不正または引数不正／3=署名不一致または HS256 以外で検証不可。

### ip

```sh
devkit ip info 192.168.1.10/24
devkit ip info 2001:db8::/64 --json
devkit ip contains 192.168.1.0/24 192.168.1.10
devkit ip overlap 192.168.1.0/24 192.168.1.128/25
```

CIDR は `IPアドレス/プレフィックス長` で指定し、ホストビットが立っていても受け付けます
（`192.168.1.10/24` は `192.168.1.0/24` として扱います）。
`info` はネットワークアドレス、プレフィックス長、アドレス総数、使用可能ホスト範囲と個数などを表示します。
IPv4 の /0〜/30 ではネットワークとブロードキャストの両端をホスト範囲から除き、
/31 は両端の2つ、/32 は単一アドレスを使用可能とします。
IPv6 では範囲内の全アドレスがホスト範囲で、ブロードキャストは「なし」と表示します。
プライベート等の判定はネットワーク単位です。

終了コード: `contains` は含まれる場合 0、含まれない場合 1。`overlap` は重なる場合 0、重ならない場合 1。
引数や CIDR の不正、IPv4 と IPv6 の混在比較は 2。

### env

```sh
devkit env .env.example .env
devkit env --json .env.example .env
devkit env --show-values .env.example .env
```

第1引数が A、第2引数が B です。片方だけにあるキー、値が異なるキー、
各ファイル内で重複したキーとその定義行を報告します。
CI ログに秘密情報を残さないよう、既定では値をマスクします（空値は `(空)` と表示）。
`--json` では `only_in_a`、`only_in_b`、`different`、`duplicates_a`、`duplicates_b` を出力し、
`--show-values` を併用すると値も含まれます。比較には重複キーの最後の定義を使います。

終了コード: 0=差分なし／1=差分あり（重複だけの場合を含む）／2=入力エラー。

### cron

```sh
devkit cron "30 7 * * *" --from "2026-11-23T00:00:00" --tz Asia/Tokyo
devkit cron "0 9 * * MON-FRI" --count 3 --tz Asia/Tokyo
```

5フィールドの cron 式から次回の実行時刻を表示します（既定5件）。
フィールドは「分 時 日 月 曜日」で、`*`、範囲 `a-b`、ステップ `*/n`・`a-b/n`、
カンマ区切りのリストが使え、月と曜日は名前（`JAN`、`MON` など）でも指定できます。
曜日番号は 0 と 7 が日曜です。日と曜日の両方が制限されている場合は OR 条件になります
（どちらかが `*` で始まる場合は AND 条件）。
`--from` は検索の起点で、起点より後の実行だけを表示します。オフセットのない日時は
計算先のタイムゾーンで解釈し、夏時間で存在しない現地時刻は移行前のオフセットで解釈します。
夏時間の切り替えで存在しない現地時刻の実行は飛ばし、同じ現地時刻が2回現れる場合は両方を表示します。
`--tz` の既定値はローカルタイムゾーンです。

終了コード: 0=表示できた／1=検索期間（約8年）内に次の実行が見つからない／2=式や引数の不正。

### csv

```sh
devkit csv records.csv
devkit csv records.csv --limit 1000 --sample 3 --json
devkit csv records.tsv --delimiter tab --no-header
devkit csv - --encoding shift_jis < records-sjis.csv
```

行数・列数、列名、列ごとの型（integer / decimal / boolean / datetime / string / mixed）と
値の分布（空欄数、ユニーク数、数値列の最小・最大）、先頭のサンプル行を表示します。

| オプション | 動作 |
| --- | --- |
| `--delimiter 文字` | 区切り文字を指定します。タブは `tab` または `'\t'` でも指定できます。未指定時は先頭行から推定します。 |
| `--no-header` | 先頭行をデータに含め、列名を `col1`、`col2` のように付けます。 |
| `--sample N` | 先頭のサンプルを最大 N 行表示します。既定は 5 行です。 |
| `--limit N` | 先頭 N データ行までを集計します。行数や列統計はその範囲の概算になり、ファイル全体は数えません。 |
| `--json` | `row_count`、`column_count`、`headers`、`columns`、`sample_rows` などを JSON で出力します。`limit` は常に含まれ、未指定時は `null` です。 |
| `--encoding 名前` | 文字コード名を指定して自動推定を上書きします。 |

文字コードは UTF-8（BOM 付きを含む）、Shift_JIS、cp932 を自動で見分けます。
列数は読み取った範囲の最大幅で、ヘッダーと幅が違うデータ行の件数は標準エラーに警告し、
JSON では `uneven_row_count` に記録します。不足するセルは空欄として扱い、
ヘッダーより右の列には `colN` と付けます。
ユニーク数の集計には全行の値を保持するため、巨大なファイルではメモリ使用量に注意してください。

終了コード: 0=正常／2=入力エラー（読み込み失敗、デコード不可、CSV の形式不正、出力失敗）。

### ts

```sh
devkit ts --tz Asia/Tokyo 1700000000 1700000000000
devkit ts --tz Asia/Tokyo '2024-01-01 09:00:00'
printf '%s\n' '2024-01-01T00:00:00Z' '2024-01-02' | devkit ts --tz UTC --json
devkit ts --now --tz UTC
```

Unix epoch と ISO 8601 / RFC 3339 の日時を相互変換します。
位置引数がない場合は標準入力を1行ずつ読み、空行をスキップします。
各結果には epoch 秒、epoch ミリ秒、UTC の日時、指定タイムゾーンまたはローカルの日時が含まれます。

| オプション | 説明 |
| --- | --- |
| `--unit s\|ms\|auto` | 数値入力の単位。既定は `auto`。 |
| `--tz <IANA名>` | 日時の解釈と表示に使うタイムゾーン。 |
| `--now` | 現在時刻を先頭に含めます（`input` は `"now"`）。 |
| `--json` | 結果を JSON で出力します。 |

変換規則:

- 符号付きの整数・小数は epoch として解釈します。自動判定では絶対値が `1000000000000` 以上ならミリ秒、それ未満なら秒です。
- 日時は `Z`（小文字の `z` も可）、`±hh:mm` のオフセット、日付のみ、空白区切りに対応します。日付のみの場合は午前0時です。オフセットがない場合は、指定タイムゾーンまたはローカル時刻として解釈します。
- JSON には `input`、`epoch_s`、`epoch_ms`、`utc`、`local`、`timezone` が含まれます。入力が1件で成功した場合はオブジェクト、複数件の場合は成功した結果の配列、成功が0件の場合は空配列を返します。

既知の制限:

- 表現できる年の範囲は 1〜9999 年です。自動判定で秒とみなされた値がこの範囲を超える場合があります。ミリ秒として扱うには `--unit ms` を指定してください。
- うるう秒には対応していません。精度はマイクロ秒までで、epoch 入力は最寄りのマイクロ秒に丸め、ちょうど中間なら偶数側に丸めます。
- 夏時間の切り替えで曖昧な時刻を指定する場合は、入力に明示的なオフセットを付けてください。歴史的なオフセットに秒が含まれる場合、RFC 3339 出力では最寄りの分に丸め、同じ瞬間を表すよう表示時刻を調整します。

終了コード: 0=すべて変換できた／1=標準入力で変換に失敗した行がある／2=引数やタイムゾーンの不正、位置引数の変換失敗、または入力なし。

### semver

```sh
devkit semver check v1.2.3+build.7
devkit semver compare 1.0.0-rc.1 1.0.0
devkit semver bump 1.2.3 --part pre --pre-id beta
devkit semver satisfies 1.4.0 '>=1.2.0 <2.0.0'
```

| コマンド | 動作 |
| --- | --- |
| `check <version>` | 有効なら `valid` を表示します。 |
| `compare <a> <b>` | 優先順位の比較結果を `-1`、`0`、`1` で表示します。 |
| `bump <version> --part major\|minor\|patch\|pre [--pre-id <id>]` | 指定した部分を更新し、結果を表示します。 |
| `satisfies <version> <range>` | 判定結果を `true` または `false` で表示します。 |

レンジは `^`、`~`、ワイルドカード（`1.2.x`）、部分指定（`^1.2`）、
比較演算子の組み合わせ（`>=1.2.0 <2.0.0`）に対応します。
プレリリースを含む上限境界など、単純化した解釈をしている箇所はテストで挙動を固定しています。

終了コード: `check` は無効なバージョンで 1、それ以外のコマンドは結果が偽の場合 1（`satisfies`）、
引数やバージョン・レンジの解釈に失敗した場合は 2（`check` の解釈失敗は 1）。

### toc

```sh
devkit toc README.md
devkit toc --create README.md
devkit toc --check README.md
devkit toc --stdout --min-level 2 --max-level 4 README.md
devkit toc - < README.md
```

Markdown の ATX 見出しから目次を生成し、`<!-- toc -->` と `<!-- /toc -->` の間を更新します。
コードフェンスや HTML コメントの中の見出し・マーカーは無視します。

| 引数 | 説明 |
| --- | --- |
| `<file.md>` | 更新する UTF-8 ファイル。`-` を指定すると標準入力から読み、更新後の全文を標準出力へ出します。 |
| `--min-level N` | 目次に含める最小の見出しレベル。既定値は 2 です。 |
| `--max-level N` | 目次に含める最大の見出しレベル。既定値は 6 です。 |
| `--create` | マーカーが未挿入の場合、最初の ATX 見出しの直後にマーカーと目次を挿入します。 |
| `--check` | 入力を変更せず、目次が最新かを検査します。 |
| `--stdout` | ファイルを書き換えず、更新後の文書を標準出力へ出します。 |

アンカーは GitHub の生成規則に合わせます（小文字化、空白は `-`、重複時は `-1`、`-2` を付与）。
HTML ブロック内の `#` 行の扱いなど、Markdown パーサとしての完全な解釈は範囲外です。

終了コード: 0=正常／1=目次が古い、または未挿入（`--check` 時）／2=マーカー不足や読み書きのエラー。

### url

```sh
devkit url 'https://example.com/search?q=hello+world&flag'
devkit url --json --drop flag --set 'q=日本語' --add 'tag=新着' 'https://example.com/search?q=old&flag'
devkit url --fix-scheme 'example.com:8080/path'
devkit url --decode 'hello%20world'
devkit url --encode 'a/b c' --safe '/'
```

URL の構成要素とクエリパラメータを表示し、クエリを編集して URL を再構築します。

| オプション | 説明 |
| --- | --- |
| `--json` | `components` オブジェクトと `params` 配列を JSON で出力します。編集時は `rebuilt` も含みます。 |
| `--drop key` | 同じキーのパラメータをすべて削除します。 |
| `--set key=value` | 同じキーのパラメータを最初の出現位置で1件にまとめ、新規キーは末尾に追加します。 |
| `--add key=value` | パラメータを末尾に追加します。 |
| `--fix-scheme` | スキームが省略されていれば HTTPS を補います。 |
| `--decode 文字列` | 文字列をパーセントデコードします。 |
| `--encode 文字列` | 文字列をパーセントエンコードします。 |
| `--safe 文字列` | `--encode` でエンコード対象から除外する文字を追加します。 |

編集オプションは複数回指定でき、コマンドライン上の並びにかかわらず **drop → set → add** の順で適用します。
`--set` と `--add` はそれぞれ指定順に処理します。編集オプションのキーはフォーム形式でデコードして突き合わせ、
値は入力された文字列として扱います。
分解表示ではホスト名を小文字で表示しますが、`rebuilt` ではクエリ以外は元の表記を保持します。
クエリは出現順に表示し、`?flag` は値なし（JSON では `null`）、`?a=` は空文字として区別を保持します。
キーと値は UTF-8 でデコードし、`+` を空白として扱います（単独の `--decode` では `+` はそのまま）。
不正なエスケープや UTF-8 がある場合は元の文字列を表示し、標準エラーに警告します。
ホストが必要なスキームは `http`、`https`、`ws`、`wss`、`ftp`、`ssh` で、
`ssh` 以外はポート未指定時に既定値を表示し、JSON では `port_is_default` で区別します。
DNS 解決や接続確認は行いません。再構築時はクエリをフォーム形式でエンコードし直すため、
元のエスケープ表記（例 `%20`）は変わることがあります。

終了コード: 0=正常／2=URL の解釈に失敗したか、引数が無効。

### branches

```sh
devkit branches
devkit branches --base release --days 30 --protect staging --json
devkit branches --delete-merged
```

対象リポジトリの中で実行します。ローカルブランチの最終コミットとマージ状況を表示し、
掃除候補を一覧にします。削除は明示的に指定した場合のみ行います。

| オプション | 説明 |
| --- | --- |
| `--base BRANCH` | マージ判定に使うローカルブランチ。既定は `main`。 |
| `--days N` | 古さのしきい値となる非負整数。既定は 90 日。 |
| `--protect BRANCH` | 保護するブランチ名を追加します。繰り返し指定できます。現在のブランチと `main`、`master`、`develop`、`--base` のブランチは常に対象外です。 |
| `--delete-merged` | マージ済みの候補を `git branch -d` で削除します。既定では報告のみです。候補であれば古さにかかわらず削除対象になります。 |
| `--json` | ブランチごとのオブジェクト配列を標準出力に書きます。 |

注意: マージ判定は `--base` を基準にしますが、`git branch -d` 自体は上流ブランチ
（なければ HEAD）を基準に判定するため、`--base` が異なる場合は削除を拒否されることがあります。
その場合は理由を添えて失敗を表示します。

終了コード: 0=正常／2=リポジトリや `--base` の解決に失敗、または削除に失敗したブランチがある。

### uuid

```sh
devkit uuid --count 3
```

UUID v4 を生成し、指定された UUID の形式を検証します。

| オプション | 説明 |
| --- | --- |
| `--count N` | 生成個数。既定は 1。 |
| `--upper` | 生成結果を大文字にします。 |
| `--json` | 生成結果または検証結果を JSON で出力します。 |

### pw

```sh
devkit pw --length 24
```

暗号学的乱数でパスワードを生成します。有効な各文字種を少なくとも1文字含みます。

| オプション | 説明 |
| --- | --- |
| `--length N` | 文字数。既定は 16。 |
| `--count N` | 生成個数。既定は 1。 |
| `--no-symbols` | 記号を除きます。 |
| `--no-ambiguous` | 0、O、1、l、I を除きます。 |

### b64

```sh
devkit b64 --decode aGVsbG8=
```

Base64 をエンコードまたはデコードします。デコード結果はバイト列として出力します。

入力は位置引数、`--file`、標準入力の順に優先します。

| オプション | 説明 |
| --- | --- |
| `--decode` | デコードします。 |
| `--url` | base64url のアルファベットを使います。 |
| `--file FILE` | 入力ファイルを指定します。 |

### hash

```sh
devkit hash --algo sha256 hello
```

ハッシュまたは HMAC を計算します。照合が不一致なら終了コード 1 を返します。

入力は位置引数、`--file`、標準入力の順に優先します。

| オプション | 説明 |
| --- | --- |
| `--algo ALGO` | md5 / sha1 / sha256 / sha512。既定は sha256。 |
| `--hmac KEY` | 指定した鍵で HMAC を計算します。 |
| `--check EXPECTED` | 16進ダイジェストを照合します。 |
| `--file FILE` | 入力ファイルを指定します。 |
| `--json` | ダイジェストと照合結果を JSON で出力します。 |

### json

```sh
devkit json --path user.name '{"user":{"name":"sample"}}'
```

JSON を整形・検証し、指定したパスから値を抽出します。パスが見つからない場合は終了コード 1 を返します。

入力は位置引数、`--file`、標準入力の順に優先します。

| オプション | 説明 |
| --- | --- |
| `--indent N` | インデント幅を 0〜10 で指定します。既定は 2。 |
| `--minify` | 空白を除きます。 |
| `--check` | 構文が正しければ `valid` を出力します。 |
| `--path PATH` | ドット区切りのパスで値を抽出します。配列は 0 始まりです。 |
| `--file FILE` | 入力ファイルを指定します。 |

### regex

```sh
devkit regex '([0-9]+)' 'ID: 123'
```

JavaScript の正規表現で全マッチを検索し、必要に応じて置換します。対象文字列を省略すると標準入力を読み、マッチがなければ終了コード 1 を返します。

| オプション | 説明 |
| --- | --- |
| `--flags FLAGS` | i / m / s / u を指定できます。 |
| `--replace TEXT` | 全マッチを指定文字列に置換します。 |
| `--json` | マッチ位置、キャプチャ、件数、置換結果を JSON で出力します。 |

### dns

```sh
devkit dns example.com --type A
```

指定したホストの DNS レコードを取得します。対象のレコードがなければ終了コード 1 を返します。

| オプション | 説明 |
| --- | --- |
| `--type TYPE` | A / AAAA / CNAME / MX / TXT / NS / ALL。既定は ALL。 |
| `--json` | ホスト名と取得できたレコードを種別ごとに JSON で出力します。 |

### http

```sh
devkit http https://example.com/
```

HTTP(S) 応答のステータス、所要時間、ヘッダを表示します。本文は読み捨てます。

| オプション | 説明 |
| --- | --- |
| `--method METHOD` | HTTP メソッド。既定は GET。 |
| `--timeout MS` | 各リクエストの制限時間。既定は 10000 ミリ秒。 |
| `--no-follow` | リダイレクトを無効にします。既定は最大10回まで追跡します。 |
| `--json` | 最終 URL、応答情報、リダイレクト履歴を JSON で出力します。 |

### port

```sh
devkit port example.com --port 80,443
```

指定したホストの TCP ポートに接続できるかを調べます。1つでも接続できなければ終了コード 1 を返します。

| オプション | 説明 |
| --- | --- |
| `--port PORT[,PORT...]` | 必須。1〜65535 のポートをカンマ区切りで指定します。 |
| `--timeout MS` | 接続の制限時間。既定は 3000 ミリ秒。 |
| `--json` | 各ポートの接続結果を JSON で出力します。 |

### cert

```sh
devkit cert example.com --warn-days 30
```

TLS 証明書の発行先、発行者、有効期間、残り日数、SAN を表示します。証明書の信頼性にかかわらず情報を取得し、残り日数がしきい値未満なら終了コード 1 を返します。

| オプション | 説明 |
| --- | --- |
| `--port PORT` | 接続先ポート。既定は 443。 |
| `--warn-days DAYS` | 残り日数の警告しきい値。既定は 30 日。 |
| `--json` | 証明書情報を JSON で出力します。警告は標準エラーに出力します。 |

## 新しいコマンドの追加方法

1. `commands/_template.js.example` を `commands/<名前>.js` にコピーします。
2. `name`（サブコマンド名）、`help`（一覧用の1行説明）、`usage`、`example`（実行例）、`run(argv, io)` を書き換えます。
   `commands/` に置かれた `.js` ファイル（先頭が `_` でないもの）は起動時に自動発見されるので、
   登録リストの編集は不要です。
3. `tests/<名前>.test.js` にテストを書き、`npm test` で全件パスを確認します。

コマンドの規約:

- 終了コードは 0=正常、1=チェック不一致、2=入力・使用法エラー を守ります。
- 使用法エラーは `core/args.js` の `UsageError` を投げるか `ArgParser` に任せます
  （usage と理由が標準エラーに出て終了コード 2 になります）。
- 出力は `io.out` / `io.err` を使い、`console.log` は使いません。
- 外部依存は追加しません（Node 標準ライブラリのみ）。

## テスト

```sh
cd tools/devkit
npm test   # node --test と同じ
```

全コマンドのテストが `tests/` にあります（元の Python テストを移植したもの）。
`csv` のテストには、Python の `PYTHONIOENCODING` 固有のケース1件
（標準出力の文字コードを ascii に制限する検証）だけ、Node に対応する仕組みがないため
スキップしているものがあります。
