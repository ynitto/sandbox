# schemas/ — ツール横断の共通スキーマ（repos / task）

kiro-autonomous・kiro-flow・codd-gate が**データ契約だけで**結合するための独立スキーマ。
ツール同士は互いの実装を知らず、ここで定義する形式だけを読む/書く（結合は常に一方向×データ）。

| スキーマ | 何の契約か | 所有者（変更の主導） |
|----------|-----------|--------------------|
| [`repos.schema.json`](repos.schema.json) | リポジトリレジストリ（identity = **(url, path, base)**＝パス＋ブランチで一意） | 共有（本ディレクトリが正典） |
| [`task.schema.json`](task.schema.json) | 制御層タスク（バックログ 1 件）の JSON 表現 | kiro-autonomous（Markdown 形の正典は `tools/kiro-autonomous/backlog.md.example`） |

## repos — 誰がどう読むか

```yaml
# repos.yaml（YAML は PyYAML 任意・無ければ JSON。トップレベルは「repo 名 → エントリ」）
app:
  url: git@example.com:team/app.git
  desc: アプリ本体（API・UI）
  base: main
  target: develop        # 省略時 base
  owns: [src/**]         # kiro-autonomous: 書込先ルーティングの根拠（無指定=参照リポジトリ）
  docs: [docs/**, README.md]   # codd-gate: 分類グロブ（他ツールは無視）
  tests: [tests/**]
shop-api:                # モノレポ: 同じ url を path 別に分ければ別エントリ（identity は url+path+base）
  url: git@example.com:team/shop.git
  path: apps/api
  base: main
  desc: API 側
```

- **kiro-autonomous**: `<project>/repos.{yaml,yml,json}` があればそれをレジストリの正として読む。
  charter.md の `## repos` は**互換入力（アダプタ）**として引き続きサポートし、内部的にはこの
  スキーマの形（repo_specs）へ正規化して引き回す。両方あるときは **repos ファイルが勝つ**。
- **kiro-flow**: `--workspace` / `--reference` の値は本スキーマの**1 エントリの射影**
  （`{url, path, base, target, desc}`）。kiro-autonomous がレジストリから選んで渡す。
- **codd-gate**: `--repos <file>`（設定 `repos_file`）で同じファイルを読む。`docs/tests/code/dir` は
  codd-gate 拡張キー（他ツールは未知キーとして無害に無視——additionalProperties: true が互換性の要）。

## task — 誰がどう読む/書くか

```json
{"id": "codd-doc-x-1a2b3c", "title": "src/util.py の変更を docs/util.md へ反映する",
 "verify": "codd-gate check --repo-dir app=. --doc docs/util.md --code src/util.py --fresh",
 "priority": 1, "paths": "docs/util.md", "expect": "changes"}
```

- **kiro-autonomous が契約の所有者**（読む側）: `enqueue --json` / `inbox/*.json` / `intake_cmd` の
  stdout がこの形式。**未知キーは保持**（前方互換）。verify 無しは inbox=人の triage 行き。
- **供給側**（codd-gate `tasks`・webhook/issue 抽出アダプタ等）はこの契約へ変換して書く。
  自ツールの内部形式（codd-gate なら所見 JSON）を正とし、変換はアダプタに閉じる。
- **kiro-flow は対象外**: kiro-flow のタスクグラフノード `{id, goal, deps, kind}` は実行層内部の
  分解ステップで層が違う（kiro-autonomous → kiro-flow の境界は「要求文＋workspace」であって
  task spec ではない）。

## 互換性の規則

1. **未知キーは無視せず保持する**（task）／**無害に無視する**（repos）。キーの削除・意味変更は不可、
   追加のみ可（additive evolution）。
2. スキーマを変えるときは本ディレクトリを先に更新し、各ツールの正典
   （`backlog.md.example` / `charter.md.example` / 各設計書）から参照を張る。
3. 検証は各ツールの stdlib パーサが行う（jsonschema への実行時依存は持たない。スキーマファイルは
   契約の文書化とテストでの突き合わせに使う）。
