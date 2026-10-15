# Electron モバイル操作エミュレーション実装計画

作成日: 2026-10-15

## 1. 目的

ElectronでWebページを表示し、DevToolsの画面を開かずに、PCのマウスからタップ・長押し・スワイプ・ピンチを再現する。

メインプロセスの `webContents.debugger` を通じてChrome DevTools Protocol（CDP）を利用する。DevTools UIの表示は不要だが、内部ではデバッグ接続を使う。「デバッグ機構を一切使わない」という要件とは異なる。

## 2. 基本方針

- 1本指の操作はChromiumのマウス→タッチ変換に任せる。
- ピンチは、ページ全体の拡大縮小とWebアプリ独自の2本指処理を区別する。
- 最初は単一のBrowserWindowを対象に実装する。既存アプリがWebContentsViewを使っている場合は、そのwebContentsに適用する。
- Electron標準APIを中心に構成し、追加のランタイム依存は原則不要とする。
- 対象Electron／内蔵Chromiumのバージョンを固定し、experimentalなCDPコマンドは実機で確認する。
- この文書は実装計画であり、対象環境での動作検証は未実施。

## 3. 操作仕様

| PCでの入力 | 再現したい操作 | 実装方針 |
| --- | --- | --- |
| 左クリック | タップ | マウス→タッチ変換 |
| 左ボタンを押したまま待機 | 長押し | マウス→タッチ変換。判定時間と動作はページ側に依存 |
| 左ボタンを押してドラッグ | スワイプ／タッチドラッグ | マウス→タッチ変換 |
| 素早いドラッグと解放 | フリック | 慣性を含めて対象環境で検証 |
| ピンチ操作用ボタン | 指定倍率でのピンチ | CDPからジェスチャーまたは2点タッチを送信 |
| Shift＋ドラッグ | 連続的な2本指ピンチ | 後続段階。標準変換での挙動を先に調査し、不足時に独自実装 |

スワイプによるページスクロール、カルーセルの切替、画像の移動は、ページ側のイベント処理と `touch-action` に依存する。全ページで同じ挙動になるとは想定しない。

## 4. 使用するAPI

| API | 役割 |
| --- | --- |
| `webContents.debugger.attach()` | 対象ページへのCDP接続 |
| `Emulation.setDeviceMetricsOverride` | モバイルviewportと画面寸法の設定 |
| `Emulation.setTouchEmulationEnabled` | タッチ対応と最大タッチ点数の設定 |
| `Emulation.setEmitTouchEventsForMouse` | マウス入力からタッチ操作への変換 |
| `Input.dispatchTouchEvent` | タップ・スワイプ・2点タッチの明示的な入力 |
| `Input.synthesizePinchGesture` | ブラウザ側のピンチジェスチャー生成 |
| `Input.synthesizeScrollGesture` | 自動スクロールの補助。任意のアプリ向けスワイプと同一視しない |
| `Emulation.clearDeviceMetricsOverride` | モバイル画面設定の解除 |

`enableDeviceEmulation()` は画面エミュレーションの別案として使えるが、タッチ操作の設定とは分ける。同じ画面設定を複数APIで重複管理しない。

## 5. 構成

### MobileEmulationController（メインプロセス）

対象webContents単位で以下を管理する。

- `enable(config)` / `disable()`：接続と設定の適用・解除。
- `tap(point)` / `swipe(options)`：必要な場合の明示的入力。
- `pinch(options)`：ブラウザピンチ／2点タッチを選択して実行。
- `cancelGesture()` / `dispose()`：入力中断と後始末。
- 有効状態、接続所有者、実行中ジェスチャー、最後のエラー。

マウス→タッチ変換中の通常ドラッグに、同じ1本指の入力を独自送信しない。二重入力を防ぐ。

### 操作UI

最低限、モードON/OFFとピンチイン／アウトを用意する。既存アプリへの組込み時は、信頼できるホストUIから限定的なIPCでコントローラーを呼び出す。

任意URLを開くページへ汎用の `sendCommand()` やNode.js機能を公開しない。`nodeIntegration: false` と `contextIsolation: true` を基本とし、IPC送信元と引数を検証する。

## 6. 初期化と終了処理

1. BrowserWindowとコントローラーを作成する。
2. 対象ページを読み込み、CDPが応答できる状態で接続・設定する。
3. モバイル画面、タッチ対応、マウス→タッチ変換を順番に有効化する。
4. 初期描画時にタッチ対応を判定するページでは、設定後の再読み込み、または事前設定の適用タイミングを検証する。
5. ページ遷移・再読み込み後に設定が保持されるか確認し、必要な場合だけ再適用する。
6. モード解除時は実行中の操作を終了／キャンセルし、変換・タッチ設定・画面設定を解除する。
7. 自分が所有するCDP接続のみdetachする。既存の別用途の接続は勝手に解除しない。
8. ウィンドウ破棄、接続切断、操作途中のフォーカス喪失を処理する。

Electron公式資料ではDevToolsの起動によるdebugger切断が記載されているため、切断イベントを監視する。失敗時に有効表示だけが残らないようにする。

## 7. スワイプの実装

### 手動操作

`setTouchEmulationEnabled({ enabled: true, maxTouchPoints: 2 })` と `setEmitTouchEventsForMouse({ enabled: true, configuration: 'mobile' })` を有効化し、左ドラッグによるスワイプを確認する。

### 自動入力が必要な場合

開始点・終了点・時間を受け取り、`Input.dispatchTouchEvent` で以下を順次送信する。

1. 1点の `touchStart`。
2. 同じIDを維持し、位置を補間した複数回の `touchMove`。
3. 空のtouchPointsで `touchEnd`。

送信は順序を保ち、前の送信完了を待つ。中断時は `touchCancel` を送り、押しっぱなし状態を残さない。

## 8. ピンチの実装

### A. ページ全体のピンチズーム

`Input.synthesizePinchGesture` に中心座標、倍率、`gestureSourceType: 'touch'` を指定する。ページ側のviewport設定による制約も確認する。

このAPIだけでアプリ独自の2本指ハンドラーまで検証できるとは保証しない。

### B. 画像・SVGなどの独自ピンチ処理

`Input.dispatchTouchEvent` で異なるIDの2点を生成する。中心を固定して2点間距離を増減し、`touchStart → touchMove → touchEnd` を送る。ページがPointer Eventsを使用する場合も、実際に届くイベントとキャンセル動作を検証する。

### C. Shift＋ドラッグ（後続段階）

対象Chromiumの標準マウス変換で対応できるか、最初に確認する。追加実装が必要なら、ホスト側で入力を取得・抑止できる経路を設計し、2点タッチへ変換する。外部Webページ内に透明オーバーレイを置く方式は、通常操作を遮断するため安易に採用しない。

## 9. 座標と競合の扱い

- 明示的なタッチ入力は対象ページのviewportに対するCSSピクセルで扱う。
- ホストUIの位置、WebContentsViewのオフセット、表示縮尺、OSのDPI倍率を混同しない。
- `deviceScaleFactor` をそのまま入力座標へ掛けない。APIごとの単位を確認する。
- スクロール、ページズーム、エミュレーション表示縮尺がある状態でも位置を検証する。
- 通常のマウス変換と独自の2点タッチを同時に流さない。切替時は進行中の入力を終了させる。
- ウィンドウ外へのドラッグ、Esc、フォーカス喪失、ナビゲーション時に操作を中断できるようにする。

## 10. 実装手順

### 段階1：最小動作検証

- 対象Electron／Chromium版を記録する。
- 単一ウィンドウでDevToolsを開かずにモバイル設定を有効化する。
- イベント確認用ページでタップ・長押し・スワイプを確認する。
- 2点タッチによるピンチをボタンから実行する。

### 段階2：独立モジュール化

- コントローラーと設定型を切り出す。
- ON/OFF、切断処理、入力中断、エラー表示を実装する。
- IPCを使う場合は限定APIと入力検証を実装する。

### 段階3：実ページへの組込み

- 縦スクロール、カルーセル、画像／SVG編集画面で検証する。
- リサイズ、遷移、DPI倍率、表示縮尺を確認する。
- 必要に応じてShift＋ドラッグによる連続ピンチを追加する。

## 11. 受入条件

- [ ] DevToolsの画面を開かずに操作できる。
- [ ] タップが1回の操作として処理される。
- [ ] 左ドラッグで縦・横スワイプが動作する。
- [ ] 長押しとフリックは、対応ページで期待動作を確認できる。
- [ ] ブラウザピンチとアプリ独自ピンチを別々に確認できる。
- [ ] 2点タッチで対象アプリの拡大縮小が動く。
- [ ] 画面上の入力位置と実際の対象位置が一致する。
- [ ] モード解除後に通常のマウス操作へ戻る。
- [ ] 操作中断・ページ遷移・接続切断で入力が残らない。
- [ ] タッチ処理の対象要素で、イベント列と `touch-action` の影響を確認できる。

## 12. 制約

モバイルエミュレーションは、iOS SafariやAndroid端末の完全な再現ではない。OS固有のジェスチャー、キーボード、実端末の挙動は別途確認する。特にexperimentalなAPIとShift＋ドラッグの動作は、採用するElectron版で検証してから仕様を確定する。

## 13. 参考資料

- [Electron Debugger](https://www.electronjs.org/docs/latest/api/debugger)
- [Electron webContents](https://www.electronjs.org/docs/latest/api/web-contents)
- [CDP Emulation](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/)
- [CDP Input](https://chromedevtools.github.io/devtools-protocol/tot/Input/)
- [CDP Input定義](https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Input.pdl)
