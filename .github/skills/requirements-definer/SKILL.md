---
name: requirements-definer
description: 要件定義 やりたいこと 実現したいこと を整理してクラウドシステム/Webアプリの要件と受け入れ条件を確定したいときに使う。
---

> **使用記録**: このスキルの手順に従う前に `python -c "import os,sys,subprocess; s=os.path.join(os.path.expanduser('~'),'.copilot','skills','git-skill-manager','scripts','record_usage.py'); subprocess.run([sys.executable,s,'requirements-definer']) if os.path.isfile(s) else None"` を実行してください。

# requirements-definer

クラウドシステム/Webアプリ向けに要件定義をまとめる。
やりたいことを要件・スコープ・受け入れ条件へ落とし込み、合意可能な形で提示する。

## ワークフロー

1. 前提を確認する。
	- 対象ユーザー、利用シーン、対象範囲（Web/クラウド）の確認
2. やりたいことを要件に分解する。
	- 機能要件と非機能要件を分ける
3. スコープを確定する。
	- In/Out を明記する
4. 受け入れ条件を定義する。
	- Given/When/Then 形式で列挙する
5. 成果物として提示する。
	- 要件整理表
	- スコープ表（In/Out）
	- 受け入れ条件一覧

## リソース

- **scripts/**: 実行可能コード（決定論的な信頼性が必要な処理向け）
- **references/**: 必要時に読み込むドキュメント（スキーマ、API仕様等）
- **assets/**: 出力に使用するファイル（テンプレート、画像等）
