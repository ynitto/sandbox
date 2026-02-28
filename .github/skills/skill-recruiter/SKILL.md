---
name: skill-recruiter
description: 必要なスキルが見つからないときに、外部リポジトリURLからスキルを取得・検証・公開するスキル。「このスキルを追加して」「URLからスキルをインストールして」「スキルが見つからない、このリポジトリを追加して」「このスキルを採用したい」「外部スキルを登録して」などで発動する。要求されたスキルが未インストールの場合に代替手段として自動起動される場合もある。
metadata:
  version: "1.0"
---

# Skill Recruiter

外部URLからスキルを取得し、ライセンス・構造・セキュリティ・ネットワーク通信を検証してから公開するスキル。

## git-skill-manager との使い分け

| 状況 | 使うスキル |
|---|---|
| 初めて見るURLやリポジトリのスキルを安全に試したい | **skill-recruiter**（このスキル） |
| 信頼済みリポジトリから効率よく複数スキルを取得・更新したい | git-skill-manager pull |
| スキルのバージョン管理・フィードバック・チーム共有をしたい | git-skill-manager |

このスキルは内部で git-skill-manager を使用している。URL を渡した場合、
ライセンス・セキュリティ検証を通過後に git-skill-manager の `repo add` + `pull` を自動実行する。

## トリガー

| トリガー | 説明 |
|---|---|
| スキル不足 | 要求されたスキルが未インストールで、URLまたはパスを提示された場合 |
| ユーザー直接 | 「このURLのスキルを追加して」「このパスのスキルを追加して」など |

## 処理フロー

### Phase 1: URL の収集

スキルが不足していた場合:

```
「必要なスキルがインストールされていません。
 追加したいスキルのGitリポジトリURL、またはローカルのスキルディレクトリパスを教えてください。
 （URL例: https://github.com/org/repo）
 （パス例: ~/my-skills, ./local-skill, /path/to/skill）」
```

ユーザーが直接 URL またはパスを指定した場合はそのまま使用する。
ソースとともにスキルルートパスの指定がない場合は `skills` をデフォルトとして使う。

-----

### Phase 2: クローンと検証

```bash
python .github/skills/skill-recruiter/scripts/verify_skill.py <URL またはローカルパス> [--skill-root <path>]
```

ローカルパスの場合はクローンをスキップして直接検証する。

スクリプトが出力する各行の意味:

| 出力行 | 状態 | 説明 |
|---|---|---|
| `VERIFY_CLONE: ok/skip/fail` | クローン成否 | skip=ローカルパス、fail なら即中止 |
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
| ❌ fail | Proprietary, All Rights Reserved, CC-BY-ND, CC-BY-NC-ND 等 | 取り込み不可・即中止 |

`VERIFY_RESULT` が `fail` になるのは以下の場合。いずれもインストールを中止する:
- **クローン失敗**（URLアクセス不可、パス不在）
- **SKILL.md 不正**（name/description なし）
- **改変禁止ライセンス**（Proprietary, All Rights Reserved, CC-BY-ND 等）

ライセンス warn（GPL 等）・ネットワーク通信・セキュリティ警告はユーザーが選択できる。

-----

### Phase 3: 結果提示とユーザー確認

#### `VERIFY_RESULT: ok` の場合

```
🔍 スキル検証結果: <URL またはローカルパス>

  クローン:      ✅ ok（ローカルパスの場合は skip）
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

  理由（いずれか）:
    - クローンに失敗しました
    - SKILL.md に name・description がありません
    - 改変禁止ライセンスのため取り込み不可: <ライセンス名>

インストールを中止します。
```

改変禁止ライセンスの場合は以下のメッセージを追加する:

```
  このスキルのライセンス（<ライセンス名>）は改変・再配布を禁止しています。
  スキルとして取り込むことはできません。
  スキルの作者に連絡してライセンスの変更を依頼するか、別のスキルを探してください。
```

-----

### Phase 4: インストール

ソースの種類によってインストール方法が異なる。

#### URL の場合

`.github/skills/git-skill-manager/SKILL.md` の `repo add` → `pull` の手順に従う。

1. **repo add** でリポジトリを登録する
2. **pull** でスキルをインストールする

#### ローカルパスの場合

`git-skill-manager` は使用せず、直接コピーする:

```python
python -c "
import shutil, os, sys
src = os.path.expanduser('<ローカルパス>')
dst = os.path.expanduser('~/.copilot/skills/<name>')
if os.path.exists(dst):
    print('既にインストール済みです:', dst)
    sys.exit(1)
shutil.copytree(src, dst)
print('コピー完了:', dst)
"
```

インストール先: `~/.copilot/skills/<name>/`

-----

### Phase 5: skill-creator 後処理

インストール後に skill-creator の仕様に従い、スキルを本システムに統合する。

#### 5-1. バリデーション

```bash
python .github/skills/skill-creator/scripts/quick_validate.py ~/.copilot/skills/<name>
```

警告があればユーザーに提示する。エラーがある場合は内容を説明し、修正するか続行するかを確認する。

#### 5-2. Windows / Copilot 環境への適応

Windows 環境（`os.name == 'nt'`）の場合のみ実行する:

```bash
python .github/skills/skill-recruiter/scripts/adapt_for_windows.py ~/.copilot/skills/<name>
```

スクリプトが出力する各行の意味:

| 出力行 | 状態 | 説明 |
|---|---|---|
| `ADAPT_SKILL_MD: ok/skip` | SKILL.md の書き換え | ok=変更あり、skip=変更なし |
| `ADAPT_PYTHON_FILES: ok/skip  N件` | .py shebang の書き換え | ok=変更あり、skip=変更なし |
| `ADAPT_SHELL_WARNING: ok/warn` | .sh ファイルの存在確認 | warn はユーザーに提示 |
| `ADAPT_RESULT: ok/warn` | 総合結果 | warn なら内容を説明する |

`ADAPT_SHELL_WARNING: warn` の場合、以下のメッセージをユーザーに提示する:

```
⚠️ シェルスクリプト (.sh) が含まれています

  <name>/ 内の .sh ファイルは Windows では直接実行できません。
  該当ファイル: scripts/setup.sh など

  対処方法:
    1. Git Bash / WSL2 上で実行する
    2. スクリプトの内容を手動で PowerShell に変換する
    3. このまま使用する（.sh を呼び出す手順は手動で代替する）

どうしますか？
  1. このまま続行する（必要に応じて手動対応）
  2. インストールを取り消す
```

#### 5-3. 完了報告

```
✅ セットアップ完了
   スキル: <name>
   場所:   ~/.copilot/skills/<name>/
   バリデーション: <結果>
   Windows 適応: <適用済み / スキップ（非Windows）>

次回起動時から利用可能です。
フィードバックを記録する場合は「git-skill-manager でフィードバックを記録して」と伝えてください。
```

## 注意事項

- セキュリティチェックはパターンマッチングによる簡易チェックであり、完全な安全保証ではない
- **ネットワーク通信が検出されたスキルは必ずユーザーの明示的な同意を得てからインストールする**
- ユーザーはインストール前にスキルの内容を自ら確認することを推奨する
- LICENSE なしのスキルはライセンスの権利関係が不明確になるため、採用時はユーザーが自己責任で判断する
