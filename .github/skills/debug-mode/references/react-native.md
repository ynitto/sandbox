# React Native

デバッグモードの計装パターン（React Native 対応）。

## HTTPメソッド（推奨）

Web の JavaScript と同じ。コレクターサーバーの起動が必要。

### ワンライナー

```javascript
//#region debug:H1
fetch('http://localhost:4567',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({h:'H1',l:'label',v:{key:value},ts:Date.now()})}).catch(()=>{});
//#endregion
```

**注意:** 実機で動かす場合は `localhost` を開発マシンのIPに変更:

```javascript
fetch('http://192.168.1.100:4567', ...)
```

## AsyncStorage（デバイス上に保存）

```javascript
//#region debug:H1
import AsyncStorage from '@react-native-async-storage/async-storage';
AsyncStorage.getItem('debug.log').then(log => {
  const entry = JSON.stringify({h:'H1',l:'label',v:{key:value},ts:Date.now()}) + '\n';
  AsyncStorage.setItem('debug.log', (log || '') + entry);
});
//#endregion
```

モバイルデバイスからのログ取得方法は [common.md](common.md) を参照。
