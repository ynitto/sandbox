---
name: dependency-auditor
description: 依存関係のセキュリティ脆弱性・ライセンス適合性・最新性を監査するスキル。「依存関係を監査して」「パッケージの脆弱性を確認して」「ライセンスを確認して」「古いライブラリを調べて」「npm auditして」「依存関係のセキュリティチェック」「CVEを確認して」「ライセンス違反がないか確認して」「サプライチェーンリスクを調べて」などで発動する。npm/pip/cargo/go/gem/maven/gradle など主要パッケージマネージャーに対応する。
metadata:
  version: 1.0.0
  tier: experimental
  category: review
  tags:
    - dependencies
    - security
    - license
    - audit
    - supply-chain
---

# dependency-auditor

プロジェクトの依存関係を **セキュリティ・ライセンス・最新性** の3軸で監査し、リスクと対処方針を報告する。

## パス解決

このSKILL.mdが置かれているディレクトリを `SKILL_DIR` とする。スクリプトは `scripts/` から、ライセンス分類は `references/license-guide.md` を参照する。

---

## ワークフロー

### Step 0: スコープチェック

以下に該当する場合は確認する:
- 依存関係ファイル（package.json / requirements.txt / Cargo.toml 等）が存在しない
- ロックファイルが存在しない（バージョンが固定されていないため監査精度が低下する旨を伝える）

### Step 1: パッケージマネージャーを検出する

`audit_deps.py` でプロジェクト内の依存管理ファイルを自動検出する:

```bash
# 自動検出してすべて監査
python ${SKILL_DIR}/scripts/audit_deps.py

# 特定ディレクトリを指定
python ${SKILL_DIR}/scripts/audit_deps.py --path ./

# 監査軸を絞る（security / license / outdated）
python ${SKILL_DIR}/scripts/audit_deps.py --check security
python ${SKILL_DIR}/scripts/audit_deps.py --check license
python ${SKILL_DIR}/scripts/audit_deps.py --check outdated

# JSON 出力
python ${SKILL_DIR}/scripts/audit_deps.py --json

# 重要度フィルタ
python ${SKILL_DIR}/scripts/audit_deps.py --severity high
```

検出対象ファイル:

| ファイル | パッケージマネージャー |
|---------|-------------------|
| `package.json` / `package-lock.json` | npm / yarn / pnpm |
| `requirements.txt` / `Pipfile.lock` / `pyproject.toml` | pip / Poetry / uv |
| `Cargo.toml` / `Cargo.lock` | cargo (Rust) |
| `go.mod` / `go.sum` | Go modules |
| `Gemfile` / `Gemfile.lock` | Bundler (Ruby) |
| `pom.xml` | Maven (Java) |
| `build.gradle` / `build.gradle.kts` | Gradle (Java/Kotlin) |

---

### Step 2: セキュリティ脆弱性を監査する

スクリプトが出力した検出コマンドを実行して CVE 情報を収集する。スクリプトが実行できない環境では、以下を手動で実行する:

```bash
# npm
npm audit --audit-level=moderate
npm audit --json | python ${SKILL_DIR}/scripts/audit_deps.py --parse-npm-json

# pip
pip install pip-audit
pip-audit --format json

# cargo
cargo install cargo-audit
cargo audit --json

# Go
go list -json -m all | nancy sleuth

# Bundler
gem install bundler-audit
bundle-audit check --update
```

各脆弱性について以下を確認する:
- **CVE ID** と CVSS スコア
- **影響を受けるバージョン** と修正済みバージョン
- **修正方法**: アップデート / 代替パッケージ / 回避策

---

### Step 3: ライセンスを確認する

`references/license-guide.md` のライセンス分類表を参照して、各依存パッケージのライセンスを評価する。

ライセンスリスク分類:

| リスク | ライセンス例 | 問題 |
|--------|------------|------|
| 🔴 **要確認** | GPL v2/v3, AGPL | コピーレフトによりソース開示義務の可能性 |
| 🟡 **注意** | LGPL, MPL, EPL | 条件付き利用可。確認が必要 |
| 🟢 **許容** | MIT, Apache 2.0, BSD, ISC | 商用・プロプライエタリ利用可 |
| ⚪ **要調査** | 独自ライセンス, ライセンス不明 | 個別に確認が必要 |

確認ポイント:
- **直接依存 + 推移的依存** を含めてチェックする
- GPL な依存を含む場合、アプリ全体のライセンスへの影響を検討する
- AGPL は SaaS でも影響を受けることに注意する

---

### Step 4: 最新性・メンテナンス状況を確認する

```bash
# npm（古いパッケージの確認）
npm outdated

# pip
pip list --outdated

# cargo
cargo outdated

# Go
go list -u -m all
```

メンテナンス状況の評価基準:
- 最終コミットが **2年以上前** → 非メンテナンスの疑い
- GitHub のスター数が急減 / アーカイブ済み → 移行先を検討
- セキュリティパッチのみで機能追加なし → 安定 or 停滞を判断する

---

### Step 5: 結果を報告する

#### 報告フォーマット

```
## 依存関係監査結果

### 検出されたパッケージマネージャー
- <npm / pip / cargo 等>: <依存パッケージ数>件（直接: X件 / 推移的: Y件）

---

### 🔒 セキュリティ脆弱性

#### 🔴 Critical [CVE-XXXX-XXXXX / CVSS: X.X]
パッケージ: <name>@<version>
脆弱性: <説明>
修正バージョン: <version>
対処: <アップデートコマンド or 代替パッケージ>

#### 🟠 High [CVE-XXXX-XXXXX / CVSS: X.X]
...（同形式）

#### 🟡 Moderate / Medium
...（同形式）

---

### 📄 ライセンス問題

#### 🔴 要確認（コピーレフト）
パッケージ: <name>@<version>
ライセンス: <GPL v3 等>
リスク: <ソース開示義務の可能性。商用利用要確認>
対処: <代替パッケージ候補 or 法務確認>

#### 🟡 注意（要条件確認）
...（同形式）

---

### 🕐 最新性・メンテナンス

#### 放棄リスクあり
パッケージ: <name>@<現在> → <最新>
最終更新: <日付>
対処: <移行候補 or 継続判断理由>

#### メジャーバージョン遅延（2世代以上）
パッケージ: <name>@<現在> → <最新>
対処: <更新コマンド / ブレーキングチェンジ確認>

---

### デプロイ前チェックリスト
- [ ] Critical / High 脆弱性が0件
- [ ] コピーレフトライセンスの法務確認済み
- [ ] ロックファイルがコミット済み
- [ ] 依存関係の自動更新（Dependabot / Renovate）が設定済み

### サマリー
- セキュリティ: Critical X件 / High X件 / Moderate X件
- ライセンス: 要確認 X件 / 注意 X件
- 最新性: メジャー遅延 X件 / 放棄リスク X件
- 総評: <優先対処事項>
```

---

## 判定スキーマ（machine-readable）

skill-mentor など呼び出し元が結果を集約するために使用する構造化フォーマット。
Step 5 の報告末尾に `<!-- verdict-json -->` コメントで囲んで出力する。

```json
{
  "skill": "dependency-auditor",
  "verdict": "CLEAN | NEEDS_ATTENTION | CRITICAL_RISK",
  "severity_summary": {
    "critical": 0,
    "high": 0,
    "moderate": 0,
    "license_issues": 0,
    "abandoned": 0
  },
  "blocking": false,
  "blocking_issues": [
    {
      "type": "vulnerability | license | abandoned",
      "severity": "Critical | High",
      "package": "パッケージ名@バージョン",
      "cve": "CVE-XXXX-XXXXX",
      "cvss_score": 9.1,
      "summary": "問題の要約（1行）",
      "fix": "修正バージョンまたは対処方法"
    }
  ]
}
```

**`verdict`**: `critical=0 && high=0 && license_issues=0` → `CLEAN`、`critical=0` → `NEEDS_ATTENTION`、`critical>0` → `CRITICAL_RISK`。
**`blocking`**: Critical 脆弱性またはコピーレフトライセンス問題が1件以上の場合のみ `true`。

---

## 補助リソース

- **scripts/audit_deps.py** — パッケージマネージャー自動検出・監査コマンド実行・結果集約
- **references/license-guide.md** — ライセンス分類・条件早見表
