# 画面・API・設計書の 3 つのリポジトリと手順

画面（frontend）・API（backend、Go）・設計書（docs）を別々のリポジトリに置き、参照先を 2 つ持つ。
ファイルに決められた手順（マイグレーションの手順・API の文書の書き方）を踏ませる場面を見る。

## サンプル

作業用のフォルダに 3 つのリポジトリを並べて作る。どれも `main` で始め、作ったものをコミットしてから codd を入れる。

**backend（Go の API）**

- `go.mod`（モジュール名は `example.com/tasks`）、`internal/task/task.go`（`Task` 構造体: `ID`・`Title`・`Done`、JSON の名前は snake_case）、
  `internal/task/handler.go`（`GET /tasks`・`POST /tasks`）、`internal/task/task_test.go`（`go test ./...` で通る）
- `db/migrations/001_tasks.sql`
- `.agents/guides/migration.md` — 先頭に `codd:` で `files: ["db/migrations/*.sql"]`・`change: [create]`・
  `check: ["sh", "tools/check_migrations.sh"]`。本文は「番号は連番」「戻す SQL をコメントで書く」など。`- [ ] 戻す SQL を書いた` を含む
- `tools/check_migrations.sh` — `db/migrations/` の番号が連番で、各ファイルに `-- down:` の行があることを確かめる
- codd: `--side impl --ref docs=../docs --ref web=../frontend --test "go test ./..."`

**frontend（画面）**

- `src/api.js`（`fetchTasks`・`createTask`）、`src/TaskList.js`（タスクの一覧を文字列で描く。完了は「済」）、`test/*.test.js`（node:test）
- codd: `--side impl --ref docs=../docs --ref api=../backend --test "node --test test/*.test.js"`

**docs（設計書）**

- `docs/api/tasks.md` — エンドポイントごとの項目の表（項目・型・必須・説明）
- `docs/screens/task-list.md` — 一覧の画面
- `.agents/guides/api-doc.md` — 先頭に `codd:` で `files: ["docs/api/*.md"]`。本文は「表に型の列を書く」。`- [ ] 項目の表に型を書いた` を含む
- codd: `--side design --ref api=../backend --ref web=../frontend`

## 1. タスクに期限を足す（3 つにまたがる）

**頼むこと**（backend で）: タスクに期限（`due_date`、任意、`YYYY-MM-DD`）を足して。API で受け取り、一覧の画面に出す。マイグレーションも足す。

**エージェントの判断**（わざとこうする）: 最初はマイグレーションに `-- down:` を書かず、`.codd/apply.md` で手順の確かめることにも答えない。
手順は名前で読み込む（`guide migration`・`guide docs:api-doc`）。

**利用者の答え**: 確認は OK。

**期待**

- 計画の検査が、マイグレーションの手順と API の文書の手順（参照先 docs が決めたもの）を読み込んで「従う手順」に挙げるまで通さない
- 参照先が 2 つあっても、画面と設計書のファイルがそれぞれの名前（`web:`・`docs:`）で計画に挙がる
- 手順は、ファイルの名前（`migration`）でも読み込め、「従う手順」にも名前（`migration`・`docs:api-doc`）で挙げてよい。参照先の手順は `docs:.agents/guides/api-doc.md` のように参照先の名前を付けて示す（show・計画と変えたあとの検査の指摘・batch の題のどれでも）
- 変えたあと、確かめることに答えていないことと、手順の `check`（マイグレーションの検査）が落ちたことを、1 回の検査で一緒に出す。
  訊かずにやり直し（AUTO）、1 回で直せば通る。`go test` と画面のテストの両方が動く
- テストが落ちたときは、落ちたテストの出力が見える（要約の末尾だけにしない）

## 2. 設計書の側から項目の説明を直す

**頼むこと**（docs で）: `due_date` は過去の日付を受け付けないと書き、API もそう直して。

**エージェントの判断**（わざとこうする）: API に計画に無い小さな関数（`today()`）を足し、テストは書かない。

**期待**: 設計書の側の codd から、backend の Go のコードとテストを変えられる。`today` のテストが無いことは、訊かずにやり直す
（AUTO APPLY。テストを足すか、「変更不要」と書く）。API の文書の手順は docs の手順として効き、
マイグレーションを足さないのでマイグレーションの手順は求めない。
