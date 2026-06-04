# git-file-sync

git リポジトリを同期ハブとして使う双方向ファイル同期ツール。

ローカルフォルダと「git リポジトリ上のフォルダ」を **対 (ペア)** にして登録し、
定期的に双方向同期する。git のクローンを介して `pull` / `push` するため、
**複数マシン間でフォルダ内容を同期**できる（git を裏方にした簡易 Dropbox）。

## 特徴

- 複数のペア（`ローカルフォルダ ⇔ リポジトリ内サブフォルダ`）を登録・管理
- ペアごとに **定期ポーリング間隔** を設定
- 前回同期スナップショットを基準にした **3-way 差分** で、変更の発生源を正しく判定
  - ローカルだけ変更 → リポジトリへ反映
  - リポジトリだけ変更 → ローカルへ反映
  - 両方変更 → **コンフリクト** としてポリシーで採用側を決定
- **コンフリクトポリシーを設定可能**（グローバル既定 + ペア単位の上書き）
  - `mine` (= `local` / `ours`) … 自分（ローカル）を採用
  - `theirs` (= `remote`) … 他人（リポジトリ）を採用
- コンフリクトで負けた側は `*.conflict` バックアップとして残す（任意）
- **削除も伝播**（片側で消したファイルはもう片側からも削除）

## 必要環境

- Python 3.9+
- `git` コマンド（PATH 上にあること）
- 設定を YAML で書く場合は PyYAML（`pip install pyyaml`）。JSON 設定なら不要。

## セットアップ

```bash
# 1. 設定ファイルを用意
cp tools/git-file-sync/config.yaml.example ~/git-file-sync.yaml
$EDITOR ~/git-file-sync.yaml

# 2. 一度だけ同期して動作確認
python3 tools/git-file-sync/sync.py --config ~/git-file-sync.yaml --once

# 3. 常駐させて定期同期（Ctrl-C で終了）
python3 tools/git-file-sync/sync.py --config ~/git-file-sync.yaml
```

## 使い方

```
python3 sync.py [--config CONFIG] [--once] [--sync PAIR] [--dry-run] [-v]

  --once         全ペアを 1 回同期して終了
  --sync PAIR    指定ペアのみ 1 回同期して終了
  --dry-run      実際のコピー/削除/コミットを行わず、予定だけ表示
  --config       設定ファイルのパス（省略時は既定の探索順）
  -v / --verbose デバッグログ（git コマンド等）を表示
```

### インタラクティブモード

`--once` / `--sync` を付けずに起動すると、バックグラウンドで定期同期しつつ
コマンドを受け付ける：

```
sync [<pair>]        全ペアまたは指定ペアを今すぐ同期
list                 登録済みペアを表示
status               最終同期時刻とステータスを表示
interval <pair> <m>  ポーリング間隔（分）を変更
policy <pair> <p>    コンフリクトポリシーを変更（mine / theirs）
help                 コマンド一覧
quit                 終了
```

## 設定

設定ファイルの探索順：

1. `--config` で明示指定したパス
2. カレントディレクトリの `git-file-sync.yaml` / `.yml` / `.json`
3. `$HOME` の同名ファイル

詳細は [`config.yaml.example`](./config.yaml.example) を参照。主要項目：

| キー | 説明 |
|------|------|
| `repository.remote` | 同期ハブのリモート URL。省略すると `worktree` をローカル git として init |
| `repository.branch` | 同期に使うブランチ（既定 `main`） |
| `repository.worktree` | リポジトリのローカルクローン置き場（このツール専用を推奨） |
| `repository.auto_push` | コミット後に自動 push するか（既定 `true`） |
| `defaults.poll_interval_minutes` | 既定ポーリング間隔（分） |
| `defaults.conflict_policy` | 既定コンフリクトポリシー（`mine` / `theirs`） |
| `defaults.keep_conflict_backup` | 負けた側を `.conflict` で残すか |
| `defaults.ignore` | 除外パターン（glob / ディレクトリ接頭辞） |
| `pairs[].name` | ペア名 |
| `pairs[].local_path` | ローカルフォルダ（絶対パス推奨） |
| `pairs[].repo_subpath` | リポジトリのワークツリーからの相対サブパス（`""` で直下） |
| `pairs[].conflict_policy` | このペアのポリシー（`defaults` を上書き） |
| `pairs[].poll_interval_minutes` | このペアの間隔（`defaults` を上書き） |
| `state_dir` | 前回同期スナップショットの保存先（既定 `~/.git-file-sync/state`） |

## 動作のしくみ

1 サイクルで以下を実行する：

1. `repository.worktree` が無ければ `clone`（remote 省略時は `init`）
2. `git fetch` + `git merge`（コンフリクトポリシーに応じて `-Xours` / `-Xtheirs`）で
   リモートの変更をワークツリーに取り込む
3. 各ペアについて 3-way 差分同期：
   - 基準 = 前回同期スナップショット（`state_dir` 内の JSON）
   - `ローカル現在` と `リポジトリ現在` を基準と比較し、変更源を判定して反映
   - 両側変更はコンフリクトとしてポリシーで決着、負けた側は `.conflict` 退避
   - 新しい一致状態を次回の基準スナップショットとして保存
4. リポジトリ側に変更があれば `commit` し、`auto_push` なら `push`
   （push 失敗時は指数バックオフで最大 4 回リトライ）

> **注意**: 同じファイルを複数マシンで同時編集した場合、後勝ち／ポリシー勝ちで
> 一方の編集が `.conflict` に退避されます。git 履歴には全コミットが残るため、
> 必要なら `repository.worktree` で `git log` から復元できます。

## 自動起動の例

cron で 5 分ごとに 1 サイクル回す：

```cron
*/5 * * * * /usr/bin/python3 ~/sandbox/tools/git-file-sync/sync.py --config ~/git-file-sync.yaml --once >> ~/git-file-sync.log 2>&1
```

または常駐プロセスとして `--once` 無しで起動し、設定の `poll_interval_minutes` に任せる。
