# gitlab-repo-sync

設定した**リポジトリの組（ペア）**を、設定した**ルール**に従って同期するだけの単体コマンド。

ローカルネットワークの GitLab とクラウドの GitLab を双方向に揃える、といった用途向け。
どちらもセルフホストで、クラウド側にはできるだけ負荷をかけたくない、という前提で作ってある。

**スケジュールは持たない。** cron / systemd timer / Windows タスクスケジューラなど、
外の仕組みから呼ばれる前提の 1 ショットコマンド。多重起動はロックで防ぐので、
外部トリガが重なっても壊れない。

```bash
bash tools/gitlab-repo-sync/install.sh     # ~/.local/bin/gitlab-repo-sync が入る
$EDITOR ~/gitlab-repo-sync.yaml
gitlab-repo-sync list                      # 何と何が繋がるか
gitlab-repo-sync sync --dry-run            # 何が起きるか（リモートは変えない）
gitlab-repo-sync sync
```

## 何をするか

ペアの両側の ref を比べ、**fast-forward できるときだけ**自動で同期する。

| 状態 | アクション |
|---|---|
| 両側同一 | 何もしない |
| a だけ進行 | b へ ff push |
| b だけ進行 | a へ ff push |
| 片側にしか無い | もう片側へ作成 |
| 片側で消えた | 既定は作り直す。`propagate_deletes` 有効時だけ削除を伝播 |
| **双方進行（分岐）** | **どちらも触らない。**レポートに出して人手へ回す |
| タグの指し先が食い違う | 同上（タグの付け替えは自動で伝播させない） |

安全側の原則:

- **`--force` は既定で使わない。** 一方向の戦略で `allow_force` を明示したときだけ。
  双方向では `allow_force` を立てても force しない（どちらが正か決まっていない関係で
  強制上書きすると、勝った側の都合でもう片方の作業が消える）。
- push は常に `<sha>:<ref>` の明示 refspec で行うので、判定側にバグがあっても
  非 fast-forward は git が拒否する（二重の安全弁）。
- 削除は「無い」という事実だけでは新規未作成と区別できないため、前回のスナップショットと
  突き合わせ、**残っている側が前回から動いていない**ときにだけ削除とみなす。

## クラウド側の負荷

1 回の実行で、**リポジトリ 1 個あたりのネットワーク接続は最大 3 回**（`ls-remote` /
`fetch` / `push`）。ペアを何組書いても、リポジトリの数以上には増えない。

- ref 一覧は `git ls-remote` 1 回で全件取る（履歴は転送しない）。
- 前回のスナップショットから両側とも変化が無ければ、**fetch も push もしない**。
- 必要なオブジェクトが既にストアにあれば fetch を省く。
- fetch も push も refspec をまとめて 1 コマンドにする（ref ごとに接続しない）。
- ペアは直列に処理する（同時接続は常に 1 本）。

## clone を重複させない仕組み

同じリポジトリが複数のペアに出てきても、clone は 1 つで済む。

URL を正規化（資格情報・末尾 `.git`・ホストの大小文字を無視）してリポジトリを同定し、
**ペアで繋がったリポジトリ群＝連結成分ごとに 1 つの bare リポジトリ**（共有ストア）を作る。
各リポジトリの ref は `refs/sync/<リポジトリの slug>/…` に分けて置く。

```
pairs:
  - {name: to-cloud,  a: local/app, b: cloud/app}
  - {name: to-backup, a: local/app, b: backup/app}
```

この設定なら、`local/app` `cloud/app` `backup/app` の 3 つが 1 つのストアを共有する。
`local/app` の fetch は 1 回。merge-base も同じリポジトリ内の計算になるので、
比較のためにワークツリーを作る必要もない。

無関係なリポジトリまで 1 つのストアに混ぜることはしない（肥大化と巻き添え障害を避ける）。

実際にどう畳まれるかは `gitlab-repo-sync list` で確認できる:

```
ペア 2 組 / リポジトリ 3 個 / 共有ストア 1 個

ストア gitlab-local-team-app-7803b51b -> ~/.gitlab-repo-sync/store/gitlab-local-team-app-7803b51b
  リポジトリ gitlab-local-team-app-7b99b352      http://gitlab.local/team/app.git
  リポジトリ gitlab-example-com-team-app-c34a611 https://gitlab.example.com/team/app.git
  リポジトリ backup-example-com-team-app-36b2f79 https://backup.example.com/team/app.git
  ペア to-cloud   gitlab-local-team-app-7b99b352 <-> gitlab-example-com-team-app-c34a611  [shared-branches]
  ペア to-backup  gitlab-local-team-app-7b99b352  -> backup-example-com-team-app-36b2f79  [publish]
```

## 必要環境

- `git`（PATH 上・必須）
- Python 3.9 以上（標準ライブラリのみ。CI で回しているのは 3.11）
- PyYAML — YAML 設定を使う場合のみ（`pip install --user pyyaml`）。JSON 設定なら不要

## インストール

```bash
bash tools/gitlab-repo-sync/install.sh
bash tools/gitlab-repo-sync/install.sh --prefix /usr/local/bin
bash tools/gitlab-repo-sync/install.sh --no-config          # 設定の雛形を置かない
```

zipapp 単一ファイルとして `~/.local/bin/gitlab-repo-sync` に入る。
**agent-* ファミリーとは何も共有しない独立コマンド**で、`agentcore` にも依存しない。
設定の雛形（`config.yaml.example`）を `~/gitlab-repo-sync.yaml` へ 600 で置く。

開発木から直接動かすこともできる:

```bash
cd tools/gitlab-repo-sync && python3 -m gitlab_repo_sync list --config ./my.yaml
```

## 使い方

```
gitlab-repo-sync [--config PATH] [-v] [-q] <サブコマンド>

  sync [--pair NAME] [--dry-run]   設定内のペアを同期する
  status                           前回結果と未解決の分岐・衝突を表示する
  list                             ペア・リポジトリ・共有ストアの対応を表示する
  refs                             最後に突き合わせた ref とストア内の置き場所
```

| オプション | 説明 |
|---|---|
| `--config` / `-c` | 設定ファイル。省略時は `./gitlab-repo-sync.yaml` → `~/gitlab-repo-sync.yaml` |
| `--verbose` / `-v` | 実行する git コマンドまで出す |
| `--quiet` / `-q` | 進捗を標準出力へ出さない。警告と失敗は標準エラーへ出す（cron 向け） |
| `--pair` / `-p` | そのペアだけ処理する。共有ストアの単位は変わらない |
| `--dry-run` | push せず予定だけ表示。状態も更新しない |

終了コード:

| 値 | 意味 |
|---|---|
| 0 | 正常（分岐が残っていても既定では 0） |
| 1 | 同期に失敗（到達不可・push 拒否・設定の誤りなど） |
| 2 | 分岐や書き込み衝突が残っている（`fail_on_diverged: true` のときだけ） |

## 設定

詳細は [`config.yaml.example`](./config.yaml.example)。骨格は 3 つ。

### credentials — PAT はグローバルに 1 か所

```yaml
credentials:
  - host: "gitlab.local"
    token: "${LOCAL_GITLAB_TOKEN}"
  - host: "*.example.com"
    token: "${CLOUD_GITLAB_TOKEN}"
    username: "oauth2"
```

URL のホスト名（glob 可・上から順に最初の一致）で引かれ、**http(s) の URL にだけ**
埋め込まれる。ssh の URL とローカルパスは触らない（鍵で認証するため）。
`$VAR` / `${VAR}` は環境変数から展開される。

ペアごとにトークンを書かせないのは、同じホストの資格情報が設定内に散ると
失効時の差し替えを漏らすため。トークンは URL に埋めて git へ渡すだけで、
`.git/config` にも remote 定義にも書き残さない。ログ出力では常に伏せ字にする。

### rules — 名前つきの同期ルール

```yaml
defaults:                       # rules の各項目はここを継承する
  strategy: "bidirectional-ff"
  include: ["refs/heads/main", "refs/tags/*"]
  exclude: ["refs/heads/tmp/*"]

rules:
  shared-branches:
    include: ["refs/heads/main", "refs/heads/release/*", "refs/tags/*"]
    exclude: ["refs/heads/feature/*"]
  publish:
    strategy: "a-to-b"
    allow_force: true
    propagate_deletes: true
```

| キー | 説明 |
|---|---|
| `strategy` | `bidirectional-ff`（既定）/ `a-to-b` / `b-to-a` |
| `include` | 同期する ref の glob。**空なら何も同期しない**（設定漏れで全 ref を流さない） |
| `exclude` | 除外する ref の glob。`include` より優先 |
| `propagate_deletes` | 片側で消えた ref をもう片側からも消すか（既定 `false`） |
| `allow_force` | 一方向戦略で、書かれる側が進行/分岐していても強制上書きするか（既定 `false`） |

戦略の詳細:

| strategy | 挙動 |
|---|---|
| `bidirectional-ff` | ff できるものだけ双方向に同期。分岐は触らず報告。`allow_force` は無視される |
| `a-to-b` | a を正とし b にだけ書く。b が進んでいたら既定はスキップ、`allow_force` なら上書き |
| `b-to-a` | その逆 |

`a-to-b` + `allow_force` + `propagate_deletes` の組み合わせが「b を a の完全な複製にする」
ミラー運用。b 側の独自の変更は消えるので、片側書き込みに運用を寄せられる場合だけ選ぶこと。

### pairs — 同期するリポジトリの組

```yaml
pairs:
  - name: "app"
    a: "http://gitlab.local/team/app.git"
    b: "https://gitlab.example.com/team/app.git"
    rule: "shared-branches"

  - name: "docs"
    a: "http://gitlab.local/team/docs.git"
    b: "https://gitlab.example.com/team/docs.git"
    rule: "shared-branches"
    include: ["refs/heads/main"]      # ルールの一部だけ上書きできる
```

`a` / `b` はどちらが「正」かを決めない（方向は `strategy` が決める）。
`name` を省略すると URL から自動生成される。`enabled: false` で一時停止でき、
無効化してもストアの単位は変わらない（有効化した瞬間に clone をやり直さない）。

### その他

| キー | 説明 |
|---|---|
| `store_dir` | 共有ストアの置き場（既定 `~/.gitlab-repo-sync/store`） |
| `state_dir` | スナップショットと実行結果の置き場（既定 `~/.gitlab-repo-sync/state`） |
| `log_file` | ログの追記先（省略時は標準出力だけ） |
| `git_timeout` | git 1 コマンドのタイムアウト秒（既定 900） |
| `lock_timeout_minutes` | ロックを異常終了の置き土産とみなすまでの分（既定 180） |
| `fail_on_diverged` | 分岐・衝突が残っているとき終了コード 2 を返すか（既定 `false`） |

## 実行タイミングの与え方

このコマンドはスケジュールを持たない。外から呼ぶ。

**cron（毎日 2:30）**

```cron
30 2 * * * $HOME/.local/bin/gitlab-repo-sync sync --quiet
```

**systemd --user timer**

```ini
# ~/.config/systemd/user/gitlab-repo-sync.service
[Service]
Type=oneshot
ExecStart=%h/.local/bin/gitlab-repo-sync sync --quiet

# ~/.config/systemd/user/gitlab-repo-sync.timer
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true          # マシンが落ちていた分は次回起動時に回収される
[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now gitlab-repo-sync.timer
loginctl enable-linger "$(whoami)"     # ログアウト後も動かす
```

**Windows タスクスケジューラから WSL 越しに**

```
schtasks /Create /TN GitLabRepoSync /SC DAILY /ST 02:30 ^
  /TR "wsl.exe -d Ubuntu -- /home/<user>/.local/bin/gitlab-repo-sync sync --quiet"
```

呼び出しが重なっても、後から来た方はロックを見て黙って降りる（終了コード 0）。
短い間隔で何度キックしても、変化が無ければリポジトリあたり `ls-remote` 1 回で終わる。

## 分岐が出たときの運用

分岐（同じブランチが両側で別々に進んだ）は**自動では解決しない**。`status` に残り続ける:

```
$ gitlab-repo-sync status
PAIR                   RULE           STRATEGY           STATUS    LAST RUN
app                    shared-branches bidirectional-ff  diverged  2026-08-03T02:30:11
    分岐: refs/heads/main
```

人手での解決手順は普通のマージと同じ。どちらかで相手を取り込んで push すれば、
次の実行で ff になって自然に解消する。

そもそも分岐を減らすには、**ブランチ単位で書き込み側を分ける**のがいちばん効く
（`main` はローカル側でだけマージする、など）。protected branch で片側を read-only に
できるなら、`bidirectional-ff` のままでも分岐はほぼ発生しなくなる。

### 書き込み衝突

`A⇔B` と `C⇔B` のようにペアを繋ぐと、1 つのリポジトリへ 2 経路から書き込みが向くことがある。
同じ ref に別々の内容を書こうとしたら、**両方のペアを止めて**報告する
（片方を後勝ちにすると、実行順で結果が変わってしまう）。

## テスト

```bash
cd tools/gitlab-repo-sync && python3 -m unittest discover -s tests
```

判定コア（純粋関数）に加えて、ローカルの bare リポジトリを「両側の GitLab」に見立てた
end-to-end テストで、双方向 ff・分岐時に両側とも動かさないこと・共有ストアが 1 つに
畳まれること・書き込み衝突が止まることを実 git で検証する（ネットワーク不要）。

## 他のツールとの使い分け

| ツール | 向いている構成 |
|---|---|
| **gitlab-repo-sync**（これ） | 設定した組を定期バッチで同期。外部スケジューラ駆動。リポジトリを複数ペアで共有しても clone は 1 つ |
| [`gitea-sync-bot`](../gitea-sync-bot/) | webhook 主導でほぼリアルタイムに同期し、分岐したら統合 MR を自動起票する。Gitea を管理面に置く構成が主対象（GitLab ⇄ GitLab でも動く） |
| [`git-file-sync`](../git-file-sync/) | リポジトリ同士ではなく、**フォルダの中身**を git 経由で同期する（git を裏方にした簡易 Dropbox） |

判定の考え方（ff だけ自動・分岐は人手）は `gitea-sync-bot` と同じで、
設計の背景は [`docs/designs/gitea-gitlab-sync-design.md`](../../docs/designs/gitea-gitlab-sync-design.md) §3 に詳しい。

## 制約

- 完全リアルタイムの双方向同期は保証しない（ff のみ自動・分岐は人手）。
- LFS とサブモジュールは追加検証が必要。
- Issue / MR / Wiki は同期しない（コードの ref だけが対象）。
- ペア内で ref 名は同じである必要がある（`main` を相手側の `develop` へ、といった
  付け替えはしない）。
