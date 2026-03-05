# Kotlin

デバッグモードの計装パターン（Kotlin / Android 対応）。

## 標準 Kotlin

### ワンライナー

```kotlin
// region debug:H1
java.io.File("debug.log").appendText("""{"h":"H1","l":"label","v":${org.json.JSONObject(mapOf("key" to value))},"ts":${System.currentTimeMillis()}}"""+"\n")
// endregion
```

### 展開版

```kotlin
// region debug:H1
import org.json.JSONObject
import java.io.File

File("debug.log").appendText(
    JSONObject(mapOf(
        "h" to "H1",
        "l" to "user_state",
        "v" to mapOf("userId" to userId, "cart" to cart),
        "ts" to System.currentTimeMillis()
    )).toString() + "\n"
)
// endregion
```

## Android（Contextあり）

```kotlin
// region debug:H1
context.openFileOutput("debug.log", Context.MODE_APPEND).bufferedWriter().use {
    it.write("""{"h":"H1","l":"label","v":${JSONObject(mapOf("key" to value))},"ts":${System.currentTimeMillis()}}""" + "\n")
}
// endregion
```

Android デバイスからのログ取得は `adb shell run-as com.yourapp cat /data/data/com.yourapp/files/debug.log > debug.log` で行う。
