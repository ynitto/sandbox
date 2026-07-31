# 依存関係管理（deps）

→ 実装: `scripts/deps.py` — `check_deps()`, `show_graph()`
→ `scripts/manage.py` — `deps_check()`, `deps_graph()`（deps.py への薄いラッパー）

## フロントマタースキーマ

```yaml
metadata:
  version: "x.y.z"
  depends_on:
    - name: <スキル名>
      reason: "<このスキルが前提である理由>"
  recommends:
    - name: <スキル名>
      reason: "<組み合わせると効果が高い理由>"
```

- `depends_on`: 実行の前提となるスキル。欠如している場合は `deps check` が❌を表示し終了コード 1 を返す。
- `recommends`: 欠如しても動作はするが、組み合わせることで効果が高まるスキル。`deps check` は⚠️で表示する。

## エージェントの動作

| トリガー | コマンド | 説明 |
|---------|---------|------|
| 「依存関係を確認して」「前提スキルが揃ってるか確認して」 | `deps check` | 全スキルの充足状況を検証 |
| 「○○の依存を確認して」 | `deps check <skill>` | 指定スキルの充足状況を検証 |
| 「依存グラフを見せて」「スキルの依存関係を図示して」 | `deps graph` | 全依存グラフを Mermaid 出力 |
| 「○○の依存グラフ」 | `deps graph <skill>` | 指定スキルの依存グラフを Mermaid 出力 |

## 出力例（deps check）

```
📦 react-frontend-coder
   ✅ [推奨] test-driven-development
     理由: Red-Green-Refactor を厳密に回したい場合に上位から併用すると効果が高い
   ✅ [推奨] webapp-testing
     理由: ユニットテストでは拾いにくい画面挙動をブラウザで検証できる
```

未インストールの場合:

```
📦 react-frontend-coder
   ❌ [推奨] webapp-testing  ← 未インストール
     理由: 画面挙動の最終確認を補強できる
```
