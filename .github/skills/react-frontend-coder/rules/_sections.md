# セクション

このファイルでは、セクションの順序、影響度、説明を定義します。
セクション ID（括弧内）は、ルールをグループ化するためのファイル名プレフィックスです。

---

## 1. ウォーターフォールの排除 (async)

**Impact:** CRITICAL  
**Description:** ウォーターフォールは最大の性能劣化要因です。`await` を直列に重ねるたびにネットワーク遅延が積み上がるため、排除効果が最も大きくなります。

## 2. バンドルサイズ最適化 (bundle)

**Impact:** CRITICAL  
**Description:** 初期バンドルサイズを削減すると、Time to Interactive と Largest Contentful Paint を改善できます。

## 3. サーバーサイド性能 (server)

**Impact:** HIGH  
**Description:** サーバー側レンダリングとデータ取得を最適化することで、サーバー内ウォーターフォールを減らし、応答時間を短縮します。

## 4. クライアントデータ取得 (client)

**Impact:** MEDIUM-HIGH  
**Description:** 自動重複排除と効率的なデータ取得パターンにより、重複したネットワークリクエストを減らします。

## 5. 再レンダー最適化 (rerender)

**Impact:** MEDIUM  
**Description:** 不要な再レンダーを減らすことで無駄な計算を抑え、UI の応答性を高めます。

## 6. レンダリング性能 (rendering)

**Impact:** MEDIUM  
**Description:** レンダリング処理を最適化し、ブラウザが実行する必要のある作業量を減らします。

## 7. JavaScript 性能 (js)

**Impact:** LOW-MEDIUM  
**Description:** ホットパスでのマイクロ最適化を積み重ねると、有意義な改善につながります。

## 8. 高度なパターン (advanced)

**Impact:** LOW  
**Description:** 慎重な実装が必要な特定ケース向けの高度なパターンです。
