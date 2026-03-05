# JavaScript / TypeScript

デバッグモードの計装パターン（Web・Node.js 対応）。

## HTTPメソッド（ブラウザ + Node.js）

コレクターサーバーの起動が必要。ブラウザ・Node.js の両方で動作。

### ワンライナー

```javascript
//#region debug:H1
fetch('http://localhost:4567',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({h:'H1',l:'label',v:{key:value},ts:Date.now()})}).catch(()=>{});
//#endregion
```

### 展開版

```javascript
//#region debug:H1
fetch('http://localhost:4567', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    h: 'H1',
    l: 'user_state',
    v: { userId, cart, timestamp: new Date().toISOString() },
    ts: Date.now()
  })
}).catch(() => {});
//#endregion
```

## ファイル書き込みメソッド（Node.js のみ）

直接ファイルに書き込む。ブラウザでは動作しない。

### ワンライナー

```javascript
//#region debug:H1
require('fs').appendFileSync('debug.log',JSON.stringify({h:'H1',l:'label',v:{key:value},ts:Date.now()})+'\n');
//#endregion
```

### 展開版

```javascript
//#region debug:H1
const fs = require('fs');
fs.appendFileSync('debug.log', JSON.stringify({
  h: 'H1',
  l: 'user_state',
  v: { userId, cart },
  ts: Date.now()
}) + '\n');
//#endregion
```
