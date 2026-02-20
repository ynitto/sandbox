---
name: skill-recruiter
description: 必要なスキルが見つからないときに、外部リポジトリURLからスキルを取得・検証・公開するスキル。「このスキルを追加して」「URLからスキルをインストールして」「スキルが見つからない、このリポジトリを追加して」「このスキルを採用したい」「外部スキルを登録して」などで発動する。要求されたスキルが未インストールの場合に代替手段として自動起動される場合もある。
---

# Skill Recruiter

外部URLからスキルを取得し、ライセンス・構造・セキュリティ・ネットワーク通信を検証してから公開するスキル。

## トリガー

| トリガー | 説明 |
|---|---|
| スキル不足 | 要求されたスキルが未インストールで、URLを提示された場合 |
| ユーザー直接 | 「このURLのスキルを追加して」など |

## 処理フロー

### Phase 1: URL の収集

スキルが不足していた場合:

```
「必要なスキルがインストールされていません。
 追加したいスキルのGitリポジトリURLを教えてください。
 （GitHub / GitLab / Bitbucket / セルフホスト対応）」
```

ユーザーが直接 URL を指定した場合はそのまま使用する。
URL とともにスキルルートパスの指定がない場合は `skills` をデフォルトとして使う。

-----

### Phase 2: クローンと検証

**スクリプトの実行コマンド（OS別）:**

| OS | コマンド |
|---|---|
| Linux / macOS | `python3 .github/skills/skill-recruiter/scripts/verify_skill.py <URL> [--skill-root <path>]` |
| Windows (Copilot) | `python .github/skills/skill-recruiter/scripts/verify_skill.py <URL> [--skill-root <path>]` |

スクリプトが出力する各行の意味:

| 出力行 | 状態 | 説明 |
|---|---|---|
| `VERIFY_CLONE: ok/fail` | クローン成否 | fail なら即中止 |
| `VERIFY_LICENSE: ok/warn/fail  <名前>` | ライセンス適合性 | 下表参照 |
| `VERIFY_SKILL: ok/fail  <name>  <desc>` | SKILL.md の妥当性 | fail なら中止 |
| `VERIFY_SECURITY: ok/warn` | 簡易セキュリティ | warn は内容を提示 |
| `VERIFY_NETWORK: ok/warn` | 外部通信の可能性 | warn はユーザー確認必須 |
| `VERIFY_RESULT: ok/warn/fail` | 総合判定 | フローを決定する |

#### ライセンス判定基準

| 判定 | ライセンス例 | 対応 |
|---|---|---|
| ✅ ok | MIT, Apache-2.0, ISC, BSD, Unlicense, CC0 | 自動承認 |
| ⚠️ warn | GPL, LGPL, AGPL, MPL | ユーザーに提示して判断を求める |
| ⚠️ warn | LICENSE ファイルなし | 警告を提示してユーザー判断で続行可 |

`VERIFY_RESULT` が `fail` になるのは **クローン失敗** または **SKILL.md 不正**（name/description なし）の場合のみ。ライセンス・ネットワーク通信は warn 止まりでユーザーが選択できる。

-----

### Phase 3: 結果提示とユーザー確認

#### `VERIFY_RESULT: ok` の場合

```
🔍 スキル検証結果: <URL>

  クローン:      ✅ ok
  ライセンス:    ✅ MIT
  スキル構造:    ✅ <name> — <description 冒頭>
  セキュリティ:  ✅ ok（懸念なし）
  ネットワーク:  ✅ ok（外部通信なし）

  総合判定: ✅ インストール可能

このスキルを追加しますか？
  1. 追加する
  2. キャンセル
```

#### `VERIFY_RESULT: warn` の場合（ライセンス・セキュリティ・ネットワーク警告あり）

**ライセンス警告の例:**

```
⚠️ 要確認事項があります

  ライセンス: GPL-3.0（コピーレフト条項が適用される場合があります）

内容を確認の上、判断してください。それでも追加しますか？
  1. 理解した上で追加する
  2. キャンセル
```

**ネットワーク通信警告の例（必ずユーザー確認を取る）:**

```
⚠️ 要確認事項があります

  ネットワーク通信: このスキルは外部サイトと通信する可能性があります。
    🌐 scripts/fetch_data.py: requests\.(get|post|...) パターン検出
    🌐 scripts/update.sh: curl パターン検出

  外部通信を許可すると、データが外部に送信される場合があります。
  スキルのソースコードを確認することを推奨します。

それでも追加しますか？
  1. 内容を確認した上で追加する（外部通信を許可する）
  2. キャンセル
```

**セキュリティ警告の例:**

```
⚠️ 要確認事項があります

  セキュリティ警告:
    scripts/setup.sh: eval\s+\$（パターン検出）

内容を確認の上、判断してください。それでも追加しますか？
  1. 理解した上で追加する
  2. キャンセル
```

ライセンスファイルがない場合:

```
⚠️ 要確認事項があります

  ライセンス: LICENSE ファイルなし（ライセンス不明）
  ライセンスが明示されていないスキルを追加すると、権利関係が不明確になる場合があります。

それでも追加しますか？
  1. 理解した上で追加する
  2. キャンセル
```

#### `VERIFY_RESULT: fail` の場合

```
❌ 検証に失敗しました

  理由: クローンに失敗しました / SKILL.md に name・description がありません

インストールを中止します。
URLまたはスキルルートパスを確認して再度お試しください。
```

-----

### Phase 4: インストール

ユーザーが同意した場合、`.github/skills/git-skill-manager/SKILL.md` の `repo add` → `pull` の手順に従う。

1. **repo add** でリポジトリを登録する
2. **pull** でスキルをインストールする

**インストール先（OS別）:**

| OS | パス |
|---|---|
| Linux / macOS | `~/.copilot/skills/<name>/` |
| Windows (Copilot) | `%USERPROFILE%\.copilot\skills\<name>\` |

-----

### Phase 5: skill-creator 後処理

インストール後に skill-creator の仕様に従い、スキルを本システムに統合する。

#### 5-1. バリデーション

**Linux / macOS:**
```bash
python3 .github/skills/skill-creator/scripts/quick_validate.py ~/.copilot/skills/<name>
```

**Windows (Copilot):**
```powershell
python .github\skills\skill-creator\scripts\quick_validate.py %USERPROFILE%\.copilot\skills\<name>
```

警告があればユーザーに提示する。エラーがある場合は内容を説明し、修正するか続行するかを確認する。

#### 5-2. 実行後フィードバック節の挿入

インストール済み SKILL.md に「実行後フィードバック（必須）」節がなければ追記する。
この節はフィードバックループ（record_feedback.py → skill-evaluator）への接続に必要。

確認パターン: `実行後フィードバック` が SKILL.md に含まれない場合に追記する。

追記するテンプレート（`<name>` を実際のスキル名に置換する）:

```markdown
## 実行後フィードバック（必須）

> **scrum-master 経由の場合**: このセクションをスキップしてください。フィードバックはスプリント終了時に一括で収集されます。

スキルの手順を全て完了したら、ユーザーに確認する:

「<name> の実行はいかがでしたか？
 1. 問題なかった (ok)
 2. 改善点がある (needs-improvement)
 3. うまくいかなかった (broken)」

回答に応じて以下を実行する（git-skill-manager がない環境ではスキップ）:
```
python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_feedback.py'); subprocess.run([sys.executable,s,'<name>','--verdict','<verdict>','--note','<note>']) if os.path.isfile(s) else None"
```

スクリプトの出力に「EVAL_RECOMMEND: promote」または「EVAL_RECOMMEND: refine」が含まれる場合は、
skill-evaluator サブエージェントを起動して評価・昇格フローを進める:
```
skill-evaluator スキルで <name> を評価する。
手順: .github/skills/skill-evaluator/SKILL.md を読んで手順に従ってください。
対象スキル: <name>
```
```

追記後、レジストリの `feedback_history` が空のエントリを初期化する（record_feedback.py が自動で行う）。

#### 5-3. 完了報告

```
✅ セットアップ完了
   スキル: <name>
   場所:   ~/.copilot/skills/<name>/  （Windows: %USERPROFILE%\.copilot\skills\<name>\）
   バリデーション: <結果>
   フィードバック節: <追記済み / 既存>

次回起動時から利用可能です。
使用後は実行後フィードバックを記録してください。
```

## 注意事項

- セキュリティチェックはパターンマッチングによる簡易チェックであり、完全な安全保証ではない
- **ネットワーク通信が検出されたスキルは必ずユーザーの明示的な同意を得てからインストールする**
- ユーザーはインストール前にスキルの内容を自ら確認することを推奨する
- LICENSE なしのスキルはライセンスの権利関係が不明確になるため、採用時はユーザーが自己責任で判断する
- Windows 環境では `python3` の代わりに `python` を使用する（環境によって異なる場合がある）
