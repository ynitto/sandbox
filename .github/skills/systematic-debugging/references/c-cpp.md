# C / C++

デバッグモードの計装パターン（C / C++ 対応）。

## C

### ワンライナー

```c
// region debug:H1
{FILE*f=fopen("debug.log","a");if(f){fprintf(f,"{\"h\":\"H1\",\"l\":\"label\",\"v\":\"%s\",\"ts\":%lld}\n",value,(long long)(time(NULL)*1000));fclose(f);}}
// endregion
```

### 展開版

```c
// region debug:H1
#include <stdio.h>
#include <time.h>

{
    FILE* f = fopen("debug.log", "a");
    if (f) {
        long long ts = (long long)(time(NULL)) * 1000;
        fprintf(f, "{\"h\":\"H1\",\"l\":\"user_state\",\"v\":{\"userId\":%d},\"ts\":%lld}\n",
                user_id, ts);
        fclose(f);
    }
}
// endregion
```

## C++（nlohmann/json 使用）

```cpp
// region debug:H1
#include <fstream>
#include <nlohmann/json.hpp>
#include <chrono>

{
    std::ofstream f("debug.log", std::ios::app);
    nlohmann::json entry = {
        {"h", "H1"},
        {"l", "user_state"},
        {"v", {{"userId", userId}, {"cart", cart}}},
        {"ts", std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count()}
    };
    f << entry.dump() << "\n";
}
// endregion
```
