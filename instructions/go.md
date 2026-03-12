# Go コーディング指示

Go プロジェクトに適用するコーディング規範。

## スタイル

- `gofmt` / `goimports` で自動フォーマットする（CI 必須）
- リンタ: `golangci-lint` を使用（設定は `.golangci.yml`）
- Go の慣習を優先し、他言語のパターンを持ち込まない

## 命名

- パッケージ名は小文字・単一単語（`userservice` ではなく `user`）
- インタフェース名は `-er` 接尾辞を使う（`Reader`, `Writer`, `Handler`）
- 頭文字語は大文字統一（`URL`, `ID`, `HTTP`）

## エラー処理

- エラーは返す。`panic` は本当に回復不能な場合のみ使う
- エラーはラップして文脈を付与する: `fmt.Errorf("fetch user: %w", err)`
- `errors.Is()` / `errors.As()` でエラー判定する（型アサーションより安全）

```go
// Bad
if err != nil {
    return err
}

// Good
if err != nil {
    return fmt.Errorf("failed to load config: %w", err)
}
```

## 並行処理

- goroutine を起動したら必ず終了を制御する（`sync.WaitGroup` / `context.Context`）
- `context.Context` は最初の引数として渡す
- チャネルの所有権を明確にし、送信側がクローズする
- データ競合は `go test -race` で検出する

## インタフェース

- インタフェースは利用側（消費者）で定義する
- 大きなインタフェースより小さなインタフェース（1〜3 メソッド）を優先する
- `interface{}` / `any` は避け、型パラメータ（ジェネリクス）を検討する

## パッケージ設計

- パッケージは単一の責務を持つ
- 循環依存を避ける（テストパッケージ `foo_test` で緩和できる）
- 内部パッケージは `internal/` に配置する

## テスト

- テストファイルは同じパッケージに配置（`foo_test.go`）
- テーブル駆動テストを使う
- モックには `testify/mock` または `gomock` を使用する

```go
func TestAdd(t *testing.T) {
    tests := []struct {
        name string
        a, b int
        want int
    }{
        {"positive", 1, 2, 3},
        {"negative", -1, -2, -3},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := Add(tt.a, tt.b)
            if got != tt.want {
                t.Errorf("Add(%d, %d) = %d; want %d", tt.a, tt.b, got, tt.want)
            }
        })
    }
}
```

## モジュール管理

- `go.sum` は必ずコミットする
- 依存は最小限に保ち、標準ライブラリを優先する
- バージョンは `go.mod` でピン留めする
