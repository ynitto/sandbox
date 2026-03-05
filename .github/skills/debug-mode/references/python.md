# Python

デバッグモードの計装パターン（Python 対応）。

## ワンライナー

```python
# region debug:H1
import json, time; open('debug.log','a').write(json.dumps({'h':'H1','l':'label','v':{'key':value},'ts':int(time.time()*1000)})+'\n')
# endregion
```

## 展開版

```python
# region debug:H1
import json
import time

with open('debug.log', 'a') as f:
    f.write(json.dumps({
        'h': 'H1',
        'l': 'user_state',
        'v': {'user_id': user_id, 'cart': cart},
        'ts': int(time.time() * 1000)
    }) + '\n')
# endregion
```
