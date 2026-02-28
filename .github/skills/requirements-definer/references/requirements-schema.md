# requirements.json スキーマ

requirements-definer が出力する `requirements.json` のスキーマ定義。

## 目次

- [スキーマ定義](#スキーマ定義)
- [拡張フィールド（オプション）](#拡張フィールドオプション)
- [出力例（小規模・Tier 1）](#出力例小規模tier-1)
- [出力例（中〜大規模・Tier 2）](#出力例中大規模tier-2)

## スキーマ定義

```json
{
  "goal": "string (必須) プロジェクトの最終目標を1文で",
  "functional_requirements": [
    {
      "id": "string (必須) F-01 形式の一意ID",
      "name": "string (必須) 要件名",
      "description": "string (必須) 要件の内容",
      "user_story": "string (任意) As a [ユーザー], I want [機能], so that [価値] 形式",
      "persona": "string (任意) ペルソナID (P-01等)。personas 定義時に使用",
      "moscow": "string (任意) must|should|could|wont。Tier 2 使用時に設定",
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

## 拡張フィールド（オプション）

以下のフィールドはすべてオプション。使用した手法に応じて出力する。

### personas（ペルソナ定義）

複数のユーザー種別が存在する場合に定義する。`functional_requirements[].persona` から参照される。

```json
{
  "personas": [
    {
      "id": "string (必須) P-01 形式の一意ID",
      "name": "string (必須) ペルソナ名（仮名）",
      "description": "string (必須) 属性・動機・課題"
    }
  ]
}
```

### user_story_map（ストーリーマッピング）

Tier 2 で作成したストーリーマップの構造を記録する。`flow` がユーザー行動の大ステップ、`stories` が各ステップのストーリーを MoSCoW で分類したもの。

```json
{
  "user_story_map": {
    "flow": ["string (必須) ユーザー行動フローの大ステップ"],
    "stories": {
      "<flow-step>": {
        "must": ["string ストーリー"],
        "should": ["string ストーリー"],
        "could": ["string ストーリー"]
      }
    }
  }
}
```

### customer_journey（カスタマージャーニーマップ）

BtoC プロダクト等で UX の全体設計を行った場合に記録する。

```json
{
  "customer_journey": [
    {
      "phase": "string (必須) フェーズ名",
      "action": "string (必須) ユーザーの行動",
      "emotion": "string (必須) 感情",
      "touchpoint": "string (必須) タッチポイント",
      "pain_point": "string (必須) 課題"
    }
  ]
}
```

## 出力例（小規模・Tier 1）

```json
{
  "goal": "個人向けTODO管理WebアプリをReactで構築する",
  "functional_requirements": [
    {
      "id": "F-01",
      "name": "TODO作成",
      "description": "タイトル・期限・優先度を指定してTODOを登録できる",
      "user_story": "As a 個人ユーザー, I want タイトル・期限・優先度を指定してTODOを登録する, so that やるべきことを忘れずに管理できる",
      "moscow": "must",
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
      "user_story": "As a 個人ユーザー, I want TODO/進行中/完了のステータスを切り替える, so that 作業の進捗を把握できる",
      "moscow": "must",
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

## 出力例（中〜大規模・Tier 2）

```json
{
  "goal": "BtoC向けECサイトをNext.jsで構築する",
  "personas": [
    {
      "id": "P-01",
      "name": "佐藤花子",
      "description": "30代女性、会社員。日用品をオンラインで手軽に購入したい。比較検討より時短を重視"
    },
    {
      "id": "P-02",
      "name": "山田店長",
      "description": "40代男性、店舗オーナー。商品登録・在庫管理・売上確認を効率化したい"
    }
  ],
  "user_story_map": {
    "flow": ["商品を探す", "カートに入れる", "決済する", "配送を確認する"],
    "stories": {
      "商品を探す": {
        "must": ["キーワード検索", "カテゴリ閲覧"],
        "should": ["フィルタリング", "ソート"],
        "could": ["AIレコメンド"]
      },
      "カートに入れる": {
        "must": ["商品追加", "数量変更", "カート一覧"],
        "should": ["お気に入り保存"],
        "could": ["まとめ買い割引表示"]
      },
      "決済する": {
        "must": ["クレジットカード決済"],
        "should": ["コンビニ払い"],
        "could": ["ポイント利用"]
      },
      "配送を確認する": {
        "must": ["注文履歴", "配送状況表示"],
        "should": ["メール通知"],
        "could": ["配送日時変更"]
      }
    }
  },
  "functional_requirements": [
    {
      "id": "F-01",
      "name": "キーワード検索",
      "description": "商品名・説明文をキーワードで全文検索できる",
      "user_story": "As a 買い物客 (P-01), I want キーワードで商品を検索する, so that 欲しい商品をすぐに見つけられる",
      "persona": "P-01",
      "moscow": "must",
      "acceptance_criteria": [
        {
          "given": "商品が10件以上登録されている",
          "when": "検索ボックスに「タオル」と入力して検索する",
          "then": "商品名または説明文に「タオル」を含む商品が一覧表示される"
        }
      ]
    },
    {
      "id": "F-02",
      "name": "カテゴリ閲覧",
      "description": "商品カテゴリ別に一覧を閲覧できる",
      "user_story": "As a 買い物客 (P-01), I want カテゴリ別に商品を閲覧する, so that 興味のあるジャンルから商品を探せる",
      "persona": "P-01",
      "moscow": "must",
      "acceptance_criteria": [
        {
          "given": "カテゴリ「日用品」に商品が5件登録されている",
          "when": "カテゴリ「日用品」を選択する",
          "then": "該当カテゴリの商品5件が一覧表示される"
        }
      ]
    },
    {
      "id": "F-03",
      "name": "お気に入り保存",
      "description": "気になる商品をお気に入りに保存して後で確認できる",
      "user_story": "As a 買い物客 (P-01), I want 商品をお気に入りに保存する, so that 後でまとめて購入を検討できる",
      "persona": "P-01",
      "moscow": "should",
      "acceptance_criteria": [
        {
          "given": "ユーザーがログイン済みで商品詳細ページを表示している",
          "when": "お気に入りボタンを押す",
          "then": "商品がお気に入りリストに追加される"
        }
      ]
    }
  ],
  "non_functional_requirements": [
    {
      "id": "N-01",
      "name": "レスポンス",
      "description": "商品検索APIは95パーセンタイルで500ms以内"
    },
    {
      "id": "N-02",
      "name": "セキュリティ",
      "description": "決済情報はPCI DSS準拠の外部サービスに委譲し、自サーバーにカード情報を保持しない"
    }
  ],
  "scope": {
    "in": ["キーワード検索", "カテゴリ閲覧", "カートCRUD", "クレジットカード決済", "注文履歴", "配送状況表示"],
    "out": [
      { "feature": "AIレコメンド", "note": "Could。v2以降で検討" },
      { "feature": "ポイント利用", "note": "Could。ポイントシステム設計後に対応" },
      { "feature": "管理者向け商品登録UI", "note": "P-02向け。別フェーズで対応" }
    ]
  }
}
```
