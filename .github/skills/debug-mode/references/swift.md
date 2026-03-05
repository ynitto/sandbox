# Swift

デバッグモードの計装パターン（Swift / iOS・macOS 対応）。

## 基本パターン

```swift
// region debug:H1
if let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first {
    let file = dir.appendingPathComponent("debug.log")
    let data = try? JSONSerialization.data(withJSONObject: ["h":"H1","l":"label","v":value,"ts":Int(Date().timeIntervalSince1970*1000)])
    if let data = data, var str = String(data: data, encoding: .utf8) {
        str += "\n"
        if let handle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(str.data(using: .utf8)!)
            handle.closeFile()
        } else {
            try? str.write(to: file, atomically: true, encoding: .utf8)
        }
    }
}
// endregion
```

## ヘルパー関数あり

```swift
// region debug:H1
func debugProbe(_ h: String, _ l: String, _ v: [String: Any]) {
    let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
    let file = dir.appendingPathComponent("debug.log")
    let entry = ["h": h, "l": l, "v": v, "ts": Int64(Date().timeIntervalSince1970 * 1000)] as [String: Any]
    if let data = try? JSONSerialization.data(withJSONObject: entry),
       var str = String(data: data, encoding: .utf8) {
        str += "\n"
        if FileManager.default.fileExists(atPath: file.path) {
            if let handle = try? FileHandle(forWritingTo: file) {
                handle.seekToEndOfFile()
                handle.write(str.data(using: .utf8)!)
                handle.closeFile()
            }
        } else {
            try? str.write(to: file, atomically: true, encoding: .utf8)
        }
    }
}

debugProbe("H1", "user_state", ["userId": userId, "cart": cart])
// endregion
```

iOS デバイスからのログ取得方法は [common.md](common.md) を参照。
