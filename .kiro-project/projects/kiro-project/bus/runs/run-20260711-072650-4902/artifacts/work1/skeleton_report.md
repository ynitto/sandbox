# kiro-flow 骨格作成レポート

## 結果サマリ

既存コードを調査した結果、`tools/kiro-flow/` に以下のファイルが既に存在することを確認。
骨格として機能する `main.py` はすでに完成済みであり、内容を確認・整理して成果物として出力した。

---

## ディレクトリ構造（既存）

```
tools/kiro-flow/
├── kiro-flow.py          # 本番実装（完全版・約6500行）
├── main.py               # 骨格スタブ（エントリーポイント・ヘルプ表示）
├── __init__.py
├── README.md
├── install.sh
├── kiro-flow.yaml.example
├── kiro-flow.state-git.yaml.example
├── executors/
└── tests/
```

---

## エントリーポイント（main.py）の骨格

`main.py` は以下の構成で実装済み:

### サブコマンド構成
| サブコマンド | 説明 |
|-------------|------|
| `run`       | ワークフローを実行する（`--goal` 必須） |
| `status`    | 実行中の run の状態を表示する |
| `result`    | 完了した run の最終結果を表示する |
| `clean`     | 古い run を掃除する |
| `daemon`    | デーモン（ワーカー）を起動する |

### ヘルプ表示

```
usage: kiro-flow [-h] [--version] <command> ...

kiro-flow — 分散 Dynamic Workflow 実行エンジン

サブコマンド:
  run     ワークフローを実行する
  status  実行中の run の状態を表示する
  result  完了した run の最終結果を表示する
  clean   古い run を掃除する
  daemon  デーモン（ワーカー）を起動する

使用例:
  kiro-flow run --goal "React ダッシュボードを作成する"
  kiro-flow status --run-id abc123
  kiro-flow result --run-id abc123
  kiro-flow clean --older-than 7d
```

---

## 備考

- `kiro-flow.py` には本番実装（完全版）が存在する。これは `main.py` の骨格を大幅に拡張したもの。
- 後続タスクが実装を進める場合は `kiro-flow.py` の既存実装を参照すること。
- `main.py` は骨格テンプレートとして利用可能。
