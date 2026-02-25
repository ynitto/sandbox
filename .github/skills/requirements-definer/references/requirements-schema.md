# requirements.json スキーマ

requirements-definer が出力する `requirements.json` のスキーマ定義。

## スキーマ定義

```json
{
  "goal": "string (必須) プロジェクトの最終目標を1文で",
  "functional_requirements": [
    {
      "id": "string (必須) F-01 形式の一意ID",
      "name": "string (必須) 要件名",
      "description": "string (必須) 要件の内容",
      "acceptance_criteria": [
        {
          "given": "string (必須) 前提条件",
          "when": "string (必須) 操作・イベント",
          "then": "string (必須) 期待結果"
        }
      ]
    }
  ],
  "non_functional_requirements": [
    {
      "id": "string (必須) N-01 形式の一意ID",
      "name": "string (必須) 要件名",
      "description": "string (必須) 要件の内容（数値目標を含む）"
    }
  ],
  "scope": {
    "in": ["string (必須) In スコープの機能名"],
    "out": [
      {
        "feature": "string (必須) Out スコープの機能名",
        "note": "string (任意) 除外理由・将来対応の備考"
      }
    ]
  }
}
```

## 出力例

```json
{
  "goal": "個人向けTODO管理WebアプリをReactで構築する",
  "functional_requirements": [
    {
      "id": "F-01",
      "name": "TODO作成",
      "description": "タイトル・期限・優先度を指定してTODOを登録できる",
      "acceptance_criteria": [
        {
          "given": "ユーザーがログイン済みである",
          "when": "タイトル「買い物」・期限「2025-03-01」・優先度「高」を入力して送信する",
          "then": "TODOが一覧に追加され、ステータスが「TODO」で表示される"
        },
        {
          "given": "ユーザーがログイン済みである",
          "when": "タイトルを空のまま送信する",
          "then": "バリデーションエラーが表示されTODOは登録されない"
        }
      ]
    },
    {
      "id": "F-02",
      "name": "ステータス管理",
      "description": "TODO/進行中/完了の3ステータスを切り替えられる",
      "acceptance_criteria": [
        {
          "given": "ステータスが「TODO」のタスクが存在する",
          "when": "「進行中」に変更する",
          "then": "ステータスが「進行中」に更新され一覧に即時反映される"
        }
      ]
    }
  ],
  "non_functional_requirements": [
    {
      "id": "N-01",
      "name": "レスポンス",
      "description": "API応答は95パーセンタイルで500ms以内"
    },
    {
      "id": "N-02",
      "name": "可用性",
      "description": "月次稼働率99.5%以上"
    }
  ],
  "scope": {
    "in": ["TODO CRUD", "ステータス管理", "一覧表示・フィルタリング"],
    "out": [
      { "feature": "チーム共有・権限管理", "note": "v2以降で検討" },
      { "feature": "モバイルアプリ", "note": "Webのみ対応" },
      { "feature": "メール通知", "note": "今回は対象外" }
    ]
  }
}
```

## scrum-master との連携

scrum-master は `requirements.json` を読み込み、`functional_requirements` の各エントリをバックログのタスクに変換する。`goal` フィールドはプランJSONの `goal` にそのまま使用する。
