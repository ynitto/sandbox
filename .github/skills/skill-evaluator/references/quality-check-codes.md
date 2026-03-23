# 品質チェックコード一覧

`quality_check.py` が出力するコードと意味の対照表。

## 品質チェック（ERROR / WARN）

| コード | 深刻度 | 内容 |
|---|---|---|
| `FM_NO_FRONTMATTER` | ERROR | SKILL.md にフロントマターがない |
| `FM_UNKNOWN_KEY` | ERROR | 許可されていないフロントマターキーがある（使用可能: name / description / license / allowed-tools / metadata / compatibility） |
| `NAME_FORMAT` | ERROR | name が kebab-case でない |
| `NAME_TOO_LONG` | ERROR | name が 64 文字超 |
| `NAME_RESERVED_WORD` | ERROR | name に予約語（anthropic 等）が含まれる |
| `NAME_AMBIGUOUS` | WARN | name が曖昧・汎用的すぎる（helper, utils, tools 等） |
| `DESC_MULTILINE` | ERROR | description に YAML ブロックスカラー（`>` / `|`）が使われている。一行のダブルクォート形式で記述すること |
| `DESC_XML_TAG` | ERROR | description に XML タグが含まれる |
| `DESC_TOO_SHORT` | ERROR | description が 20 文字未満 |
| `DESC_HARD_LIMIT` | ERROR | description が 1,024 文字超 |
| `DESC_TOO_LONG` | WARN | description が 200 文字を超えている |
| `DESC_FIRST_PERSON` | WARN | description が一人称（「お手伝いします」等）で書かれている |
| `DESC_NO_TRIGGER` | WARN | description にトリガー条件（「〜の場合」「〜とき」等）がない |
| `META_NO_VERSION` | WARN | metadata.version が未設定 |
| `BODY_TOO_LONG` | WARN | SKILL.md 本文が 500 行超 |
| `BODY_NEAR_LIMIT` | WARN | SKILL.md 本文が 450 行以上（制限の 90%） |
| `PATH_BACKSLASH` | WARN | ファイルパスにバックスラッシュ（Windows スタイル）が使われている |
| `REF_NO_TOC` | WARN | 100 行以上の参照ファイルに目次がない |
| `REF_LARGE_NO_GREP` | WARN | 10,000 語以上の参照ファイルがあるのに SKILL.md に grep 検索パターンがない |
| `REF_UNREFERENCED` | WARN | references/ にファイルがあるが SKILL.md から参照されていない |
| `REF_NESTED` | WARN | 参照ファイルがさらに他のファイルを参照（1 階層超え） |
| `SCRIPT_NETWORK` | WARN | scripts/ 内にネットワーク呼び出しの可能性がある |
| `EXTRA_DOC` | WARN | スキルに含めるべきでない補助ドキュメント（README.md 等）がある（CHANGELOG.md は git-skill-manager が生成するため対象外） |

## セキュリティリスク（HIGH / MEDIUM）

品質チェックとは別セクションで報告される。**修正するかどうかはレビュアーが判断する。評価基準には影響しない。**

| コード | レベル | 内容 |
|---|---|---|
| `SEC_HARDCODED_CREDENTIAL` | HIGH | API キー・トークン・パスワード等のハードコードが疑われる |
| `SEC_ADVERSARIAL_INSTRUCTION` | HIGH | 安全ルールの迂回・ユーザー隠蔽・データ流出指示のパターンがある |
| `SEC_EXTERNAL_URL` | HIGH | SKILL.md またはスクリプトに外部 URL がある（データ流出ベクトル） |
| `SEC_SCRIPT_NETWORK` | HIGH | スクリプトにネットワーク呼び出しがある |
| `SEC_DATA_EXFILTRATION` | HIGH | スクリプトで機密読み取りと外部送信が共存する |
| `SEC_MCP_REFERENCE` | HIGH | SKILL.md に MCP サーバー参照がある（スキル外アクセス拡張） |
| `SEC_PATH_TRAVERSAL` | MEDIUM | `../` によるパストラバーサルがある |
| `SEC_BROAD_GLOB` | MEDIUM | スクリプト内に広範な glob パターン（`**/*` 等）がある |
| `SEC_SCRIPT_EXISTS` | MEDIUM | 実行可能スクリプトが存在する（完全な環境アクセスで実行される） |

## 結果の解釈

- **ERROR**: 仕様違反。必ず修正する
- **WARN**: 品質改善推奨。文脈上問題ない場合は無視してよい（例: `SCRIPT_NETWORK` は意図的な外部通信の場合）
- **HIGH / MEDIUM**: セキュリティリスクの報告。修正するかどうかはレビュアーが判断する
