# Java

デバッグモードの計装パターン（Java 対応）。

## Gson あり

### ワンライナー

```java
// region debug:H1
try{var w=new java.io.FileWriter("debug.log",true);w.write("{\"h\":\"H1\",\"l\":\"label\",\"v\":"+new com.google.gson.Gson().toJson(value)+",\"ts\":"+System.currentTimeMillis()+"}\n");w.close();}catch(Exception e){}
// endregion
```

### 展開版

```java
// region debug:H1
try {
    var writer = new java.io.FileWriter("debug.log", true);
    var gson = new com.google.gson.Gson();
    writer.write(String.format(
        "{\"h\":\"H1\",\"l\":\"user_state\",\"v\":%s,\"ts\":%d}\n",
        gson.toJson(Map.of("userId", userId, "cart", cart)),
        System.currentTimeMillis()
    ));
    writer.close();
} catch (Exception e) {}
// endregion
```

## Gson なし

```java
// region debug:H1
try{var w=new java.io.FileWriter("debug.log",true);w.write("{\"h\":\"H1\",\"l\":\"label\",\"v\":\""+value.toString().replace("\"","\\\"")+"\",\"ts\":"+System.currentTimeMillis()+"}\n");w.close();}catch(Exception e){}
// endregion
```
