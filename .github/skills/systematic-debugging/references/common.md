# 共通リファレンス

デバッグモードで使用する共通フォーマットと設定。

## ログフォーマット

NDJSON（1行1JSON）:

```json
{"h":"H1","l":"label","v":{"key":"value"},"ts":1702567890123}
```

| フィールド | 意味 |
|-----------|------|
| h | 仮説ID（H1, H2, ...） |
| l | ラベル（このログが何を表すか） |
| v | 値（JSONシリアライズ可能な任意のデータ） |
| ts | タイムスタンプ（ミリ秒） |

## リージョン構文

言語別のデバッグ計装を囲むリージョン構文:

| 言語 | 開始 | 終了 |
|------|------|------|
| JS/TS | `//#region debug:H1` | `//#endregion` |
| Python | `# region debug:H1` | `# endregion` |
| Ruby | `# region debug:H1` | `# endregion` |
| Go | `// region debug:H1` | `// endregion` |
| Rust | `// region debug:H1` | `// endregion` |
| Java/Kotlin | `// region debug:H1` | `// endregion` |
| Swift | `// region debug:H1` | `// endregion` |
| C/C++ | `// region debug:H1` | `// endregion` |
| Dart | `// region debug:H1` | `// endregion` |
| C# | `#region debug:H1` | `#endregion` |

## ログコレクターサーバー

ブラウザやモバイルデバイスからログを収集するためのHTTPサーバー:

```bash
node -e "require('http').createServer((q,s)=>{s.setHeader('Access-Control-Allow-Origin','*');s.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');s.setHeader('Access-Control-Allow-Headers','Content-Type');if(q.method==='OPTIONS'){s.writeHead(204).end();return}let b='';q.on('data',c=>b+=c);q.on('end',()=>{require('fs').appendFileSync('debug.log',b+'\n');s.writeHead(204).end()})}).listen(4567,()=>console.log('Collector: http://localhost:4567'))"
```

## ログ分析

### PowerShell（Windows）

```powershell
# 全件表示
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json }

# H1でフィルタ
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.h -eq "H1" }

# 仮説別グループ
Get-Content debug.log | ForEach-Object { $_ | ConvertFrom-Json } | Group-Object h

# リアルタイム監視
Get-Content debug.log -Wait | ForEach-Object { $_ | ConvertFrom-Json }
```

### bash（Linux/macOS） - jqがある場合

```bash
cat debug.log | jq .                              # 全件表示
jq 'select(.h == "H1")' debug.log                 # H1でフィルタ
cat debug.log | jq -s 'group_by(.h)'              # 仮説別グループ
tail -f debug.log | jq .                          # リアルタイム
```

## モバイルデバイスからのログ取得

### iOS

```bash
# Xcode経由
# Window > Devices and Simulators > デバイス選択 > Download Container

# libimobiledevice経由
idevice_id -l
ideviceinstaller -l
```

デバッグビルドに「ログをエクスポート」ボタンを追加する方法も有効。

### Android

```bash
adb shell run-as com.yourapp cat /data/data/com.yourapp/files/debug.log > debug.log

# 外部ストレージに保存している場合
adb pull /sdcard/Android/data/com.yourapp/files/debug.log
```

### React Native / Flutter

HTTPコレクターサーバー方式を推奨 — ログが開発マシンに直接送られる。
