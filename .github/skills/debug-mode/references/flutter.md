# Flutter (Dart)

デバッグモードの計装パターン（Flutter / Dart 対応）。

## 基本パターン

### ワンライナー

```dart
// region debug:H1
import 'dart:convert';
import 'dart:io';
File('debug.log').writeAsStringSync(jsonEncode({'h':'H1','l':'label','v':{'key':value},'ts':DateTime.now().millisecondsSinceEpoch})+'\n', mode: FileMode.append);
// endregion
```

### 展開版

```dart
// region debug:H1
import 'dart:convert';
import 'dart:io';

final entry = {
  'h': 'H1',
  'l': 'user_state',
  'v': {'userId': userId, 'cart': cart},
  'ts': DateTime.now().millisecondsSinceEpoch,
};

File('debug.log').writeAsStringSync(
  jsonEncode(entry) + '\n',
  mode: FileMode.append,
);
// endregion
```

## path_provider を使う場合

アプリのドキュメントディレクトリに書き込む:

```dart
// region debug:H1
import 'package:path_provider/path_provider.dart';

Future<void> debugProbe(String h, String l, Map<String, dynamic> v) async {
  final dir = await getApplicationDocumentsDirectory();
  final file = File('${dir.path}/debug.log');
  final entry = jsonEncode({
    'h': h,
    'l': l,
    'v': v,
    'ts': DateTime.now().millisecondsSinceEpoch,
  });
  await file.writeAsString('$entry\n', mode: FileMode.append);
}

await debugProbe('H1', 'user_state', {'userId': userId, 'cart': cart});
// endregion
```

実機からのログ取得: Android は `adb shell run-as com.yourapp cat /data/data/com.yourapp/files/debug.log > debug.log`、iOS は Xcode の「Window > Devices and Simulators > Download Container」から行う。
