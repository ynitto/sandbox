# Go

デバッグモードの計装パターン（Go 対応）。

## ワンライナー

```go
// region debug:H1
func(){f,_:=os.OpenFile("debug.log",os.O_APPEND|os.O_CREATE|os.O_WRONLY,0644);defer f.Close();json.NewEncoder(f).Encode(map[string]any{"h":"H1","l":"label","v":value,"ts":time.Now().UnixMilli()})}()
// endregion
```

## 展開版

```go
// region debug:H1
func() {
    f, _ := os.OpenFile("debug.log", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
    defer f.Close()
    json.NewEncoder(f).Encode(map[string]any{
        "h":  "H1",
        "l":  "user_state",
        "v":  map[string]any{"userId": userId, "cart": cart},
        "ts": time.Now().UnixMilli(),
    })
}()
// endregion
```
