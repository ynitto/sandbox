---
name: security-reviewer
description: セキュリティレビューを実施するスキル。「セキュリティ診断して」「脆弱性を確認して」「OWASP に準拠してるか確認して」「セキュリティをチェックして」「認証の実装を確認して」「シークレット漏洩がないか確認して」などのリクエストで発動する。OWASP Top 10 に基づく脆弱性パターンを検出し、重要度付きで報告する。
metadata:
  version: "1.0.0"
---

# security-reviewer

コードを OWASP Top 10 および一般的なセキュリティベストプラクティスの観点でレビューし、脆弱性を重要度付きで報告する。修正が必要な場合は具体的な修正案を提示する。

## レビューワークフロー

### Step 0: スコープチェックを行う

以下に該当する場合はレビューを中断し、ユーザーに確認する:

- レビュー対象のコードが存在しない・空
- レビュー対象が自動生成コードのみ（セキュリティ上の変更が含まれない場合）

該当しない場合は次のステップへ進む。

### Step 1: レビュー対象を確認する

1. **対象コードの役割** — このコードは何をするものか（API・認証・決済・ファイル処理等）
2. **使用技術スタック** — 言語・フレームワーク・DB・外部サービス
3. **レビューの深度** — 全体的なセキュリティ監査か、特定箇所の集中確認か
4. **git コンテキスト** — diff や git blame が提供されている場合は変更の背景を把握する

変更の目的・背景が不明な場合はユーザーに確認してから進む。

### Step 2: セキュリティ脆弱性を検出する

以下のカテゴリを順番に確認する。**diff がある場合は変更行を中心に確認し、関連する周辺コードも対象とする。**

#### 1. シークレット管理

- [ ] API キー・パスワード・トークン等がハードコードされていないか
- [ ] 機密情報がコメントや文字列リテラルとして残っていないか
- [ ] `.env` ファイルや設定ファイルが誤ってコミットされていないか
- [ ] 環境変数の存在確認（undefined のまま使用していないか）

```typescript
// ❌ NG: ハードコードされたシークレット
const apiKey = "sk-proj-xxxxx"

// ✅ OK: 環境変数から取得し、存在確認も行う
const apiKey = process.env.OPENAI_API_KEY
if (!apiKey) throw new Error('OPENAI_API_KEY not configured')
```

#### 2. 入力バリデーション

- [ ] すべてのユーザー入力がバリデーションされているか
- [ ] ホワイトリスト方式（許可するものを明示）を使用しているか
- [ ] ファイルアップロードのサイズ・形式・拡張子チェックがあるか
- [ ] エラーメッセージに機密情報が含まれていないか

```typescript
// ✅ OK: スキーマバリデーション (zod)
import { z } from 'zod'
const schema = z.object({
  email: z.string().email(),
  name: z.string().min(1).max(100),
})
const validated = schema.parse(input)
```

#### 3. SQL インジェクション

- [ ] すべての DB クエリがパラメータ化されているか
- [ ] 文字列結合で SQL を構築していないか
- [ ] ORM / クエリビルダーを正しく使用しているか

```typescript
// ❌ NG: 文字列結合 SQL → SQL インジェクション脆弱性
const query = `SELECT * FROM users WHERE email = '${email}'`

// ✅ OK: パラメータ化クエリ
await db.query('SELECT * FROM users WHERE email = $1', [email])
```

#### 4. 認証・認可

- [ ] トークンが httpOnly クッキーに保存されているか（localStorage は XSS に脆弱）
- [ ] センシティブな操作の前に認可チェックが実施されているか
- [ ] ロールベースアクセス制御（RBAC）が適切に実装されているか
- [ ] セッション管理が安全か（有効期限・無効化）
- [ ] JWT の検証（署名・有効期限・クレーム）が正しいか

```typescript
// ✅ OK: httpOnly クッキー
res.setHeader('Set-Cookie',
  `token=${token}; HttpOnly; Secure; SameSite=Strict; Max-Age=3600`)

// ✅ OK: 認可チェックを先に実施
export async function deleteUser(userId: string, requesterId: string) {
  const requester = await db.users.findUnique({ where: { id: requesterId } })
  if (requester?.role !== 'admin') {
    return { error: 'Unauthorized', status: 403 }
  }
  await db.users.delete({ where: { id: userId } })
}
```

#### 5. XSS（クロスサイトスクリプティング）

- [ ] ユーザー提供の HTML が適切にサニタイズされているか
- [ ] `dangerouslySetInnerHTML` や `innerHTML` の使用箇所を確認
- [ ] Content Security Policy (CSP) ヘッダーが設定されているか
- [ ] テンプレートエンジンでエスケープが有効になっているか

```typescript
// ❌ NG: 未検証の innerHTML
element.innerHTML = userInput

// ✅ OK: DOMPurify でサニタイズ
import DOMPurify from 'isomorphic-dompurify'
const clean = DOMPurify.sanitize(userInput, {
  ALLOWED_TAGS: ['b', 'i', 'em', 'strong', 'p'],
  ALLOWED_ATTR: []
})
```

#### 6. CSRF（クロスサイトリクエストフォージェリ）

- [ ] 状態変更を伴う操作（POST/PUT/DELETE）に CSRF 対策があるか
- [ ] SameSite=Strict または SameSite=Lax がクッキーに設定されているか
- [ ] CSRF トークンの検証が実装されているか

#### 7. レート制限

- [ ] すべての API エンドポイントにレート制限があるか
- [ ] 認証エンドポイントに特に厳しいレート制限があるか
- [ ] 高コストな処理（検索・AI 呼び出し等）に制限があるか

```typescript
// ✅ OK: レート制限
import rateLimit from 'express-rate-limit'

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 分
  max: 5, // 15 分に 5 回まで
  skipSuccessfulRequests: true
})
app.use('/api/auth/login', authLimiter)
```

#### 8. センシティブデータの露出

- [ ] ログにパスワード・トークン・個人情報が含まれていないか
- [ ] エラーレスポンスにスタックトレース・内部情報が含まれていないか
- [ ] レスポンスから不要な機密フィールドが除外されているか
- [ ] 通信が HTTPS で暗号化されているか

```typescript
// ❌ NG: 内部エラー情報をユーザーに返す
catch (error) {
  return res.status(500).json({ error: error.message, stack: error.stack })
}

// ✅ OK: 汎用メッセージを返し、詳細はサーバーログのみ
catch (error) {
  console.error('Internal error:', error)
  return res.status(500).json({ error: 'An error occurred. Please try again.' })
}
```

#### 9. 依存関係のセキュリティ

- [ ] 既知の脆弱性を持つ依存関係がないか（`npm audit` / `pip-audit` 等）
- [ ] ロックファイル（`package-lock.json` 等）がコミットされているか
- [ ] 依存関係が定期的に更新されているか

#### 10. セキュリティヘッダー

- [ ] `Content-Security-Policy` が設定されているか
- [ ] `Strict-Transport-Security`（HSTS）が設定されているか
- [ ] `X-Frame-Options` / `X-Content-Type-Options` が設定されているか
- [ ] `helmet` 等のミドルウェアが使用されているか

```typescript
// ✅ OK: helmet でセキュリティヘッダーを一括設定
import helmet from 'helmet'
app.use(helmet())
```

### Step 3: 重要度を判定する

各検出事項に重要度を付与する:

| 重要度 | 基準 | 対応 |
|--------|------|------|
| **Critical** | 直ちに悪用可能な脆弱性（SQLi・認証バイパス・シークレット漏洩等） | 必ず修正 |
| **High** | 条件次第で悪用されうる問題（CSRF・XSS・認可不備等） | 修正を強く推奨 |
| **Medium** | セキュリティ態勢を弱める問題（レート制限なし・ヘッダー未設定等） | 修正を推奨 |
| **Low** | ベストプラクティスからの逸脱（ログの過剰出力等） | 改善を検討 |

**推測の域を出ない指摘は報告しない。** コードを見て明確に問題と判断できるものだけを報告する。

### Step 4: OWASP Top 10 への対応状況を確認する

レビュー対象コードに関係するカテゴリのみ評価する:

| カテゴリ | 確認項目 |
|----------|---------|
| A01: アクセス制御の不備 | 認可チェック・RBAC・RLS（Row Level Security） |
| A02: 暗号化の失敗 | HTTPS・機密データの暗号化・弱い暗号アルゴリズム |
| A03: インジェクション | SQL・コマンド・LDAP・XSS インジェクション |
| A04: 安全でない設計 | 脅威モデリング・セキュリティ設計パターンの欠如 |
| A05: セキュリティの設定ミス | デフォルト設定・不要な機能の有効化・エラー情報の漏洩 |
| A06: 脆弱なコンポーネント | 既知 CVE のある依存関係 |
| A07: 認証の失敗 | 弱いパスワードポリシー・セッション管理・ブルートフォース対策 |
| A08: データの整合性の失敗 | CSRF・署名検証・信頼できないデシリアライズ |
| A09: セキュリティログの失敗 | 監査ログ・異常検知・機密情報のログ混入 |
| A10: SSRF | 外部リクエストの検証・内部サービスへのアクセス制御 |

### Step 5: 結果を報告する

#### 報告フォーマット

```
## セキュリティレビュー結果: [問題なし ✅ | 要修正 ❌]

### 検出された脆弱性

#### 🔴 Critical: <問題の要約>
場所: <ファイル名:行番号>
問題: <何が問題か・なぜ危険か>
修正案:
```<言語>
<修正後のコード例>
```

#### 🟠 High: <問題の要約>
...（同形式）

#### 🟡 Medium: <問題の要約>
...（同形式）

#### 🔵 Low: <問題の要約>
...（同形式）

### OWASP Top 10 対応状況
（レビュー対象に関係するカテゴリのみ）

| カテゴリ | 状態 | 備考 |
|----------|------|------|
| A03: インジェクション | ✅ 問題なし / ❌ 問題あり | <詳細> |
| ...      | ...  | ...  |

### デプロイ前チェックリスト
- [ ] シークレットが環境変数に移行済み
- [ ] すべてのユーザー入力がバリデーション済み
- [ ] DB クエリがパラメータ化済み
- [ ] 認証・認可チェックが実装済み
- [ ] XSS 対策（サニタイズ・CSP）が実施済み
- [ ] CSRF 対策が実施済み
- [ ] レート制限が設定済み
- [ ] エラーレスポンスに機密情報が含まれていない
- [ ] セキュリティヘッダーが設定済み
- [ ] 依存関係に既知の脆弱性がない

### サマリー
- Critical: X件 / High: X件 / Medium: X件 / Low: X件
- 総評: <変更の目的達成度・特に危険な箇所・優先して対処すべき項目>
```

## レビューの原則

- **確信のある指摘だけをする** — コードを見て明確に問題と判断できるものだけ報告する
- **修正案を必ず示す** — 「問題がある」だけでなく「どう直すか」を具体的なコード例で示す
- **根拠を示す** — なぜそれが脆弱性か（悪用シナリオを含めて）を説明する
- **文脈を尊重する** — プロジェクトの技術スタック・制約に合った修正案を提示する
- **優先度を明確にする** — Critical から順に対処できるよう重要度を明記する

## 補助スクリプト

### scripts/

- **scan_vulnerabilities.py** — OWASP Top 10 ベースの脆弱性パターン静的スキャン（grep/正規表現）

```bash
# カレントディレクトリを再帰スキャン
python .github/skills/security-reviewer/scripts/scan_vulnerabilities.py

# 対象ディレクトリを指定
python .github/skills/security-reviewer/scripts/scan_vulnerabilities.py --path src/

# JSON 形式で出力
python .github/skills/security-reviewer/scripts/scan_vulnerabilities.py --json

# 重要度フィルタ（critical のみ）
python .github/skills/security-reviewer/scripts/scan_vulnerabilities.py --severity critical
```

**検出カテゴリ**（17パターン）: ハードコードシークレット、SQLインジェクション、コマンドインジェクション、XSS、JWT脆弱性、CORS設定、デバッグモード、SSRF 等

**終了コード**: 0 = Critical/High なし / 1 = Critical/High 検出 / 2 = 読み取りエラー

---

## 参考リソース

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [Web Security Academy (PortSwigger)](https://portswigger.net/web-security)
- [helmet.js](https://helmetjs.github.io/)
- [API Security Checklist](https://github.com/shieldfy/API-Security-Checklist)
