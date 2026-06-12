# hermes-kiro-acp — ルートB パッチ

本流 [Hermes](https://github.com/NousResearch/hermes-agent) に **`kiro-acp` プロバイダ**を
追加するパッチです。`hermes chat --provider kiro-acp` で、ローカルの `kiro-cli acp`
（ACP サーバ）をサブプロセスとして起動し、Hermes のバックエンドとして駆動します。

これは検討メモの「ルートB（本流に kiro-acp プロバイダを自作する）」の実装です。
本流が既に持つ **copilot-acp のサブプロセス × ACP 機構**を雛形にしており、
**外部依存の追加はありません**（stdlib の `subprocess` / `json` / `threading` のみ）。

## 何ができるか

```
┌─────────────┐   ACP (stdio / JSON-RPC)   ┌──────────────┐
│   Hermes    │ ─────────────────────────▶ │  kiro-cli acp │ ──▶ 推論
│ (クライアント) │ ◀───────────────────────── │ (ACP サーバ)   │
└─────────────┘   text / thought chunks    └──────────────┘
```

- Hermes のオーケストレーション・メモリ・スキルはローカルに残る。
- 外に出るのは Kiro が処理する推論分だけ。
- Kiro のガバナンス設定（モデルアクセスや MCP 制限）はそのまま効く。
- ツール承認は ACP の `session/request_permission` として飛ぶが、本クライアントは
  copilot-acp と同じく**既定で拒否**（headless 安全側）。`fs/read_text_file` /
  `fs/write_text_file` は Hermes 側の `file_safety` ガード（cwd 制限・機密ファイル保護・
  秘匿情報マスク）を通してのみ許可する。

> 注意: `hermes acp` は Hermes 自身を ACP **サーバ**として起動する逆向きのモードです。
> 本パッチがやるのは「Hermes が Kiro を叩く」向きで、別物です。

### Hermes ツールの受け渡し（ツールブリッジ）

ACP にはクライアント側ツールを宣言するプロトコルがないため、Hermes のツール群は
プロンプト内で受け渡しします。読み飛ばし・取りこぼしを防ぐため次の設計です。

- ツール定義は `<tools>…</tools>` タグで明示的に区切り、**1 行 1 スキーマ**で列挙。
  Kiro 自身の組み込みツール・ACP/MCP ツールは使用禁止と明記し、プロンプト末尾でも
  再度念押しする（エージェント系 CLI は自前ツールに流れやすいため）。
- 呼び出しは Hermes 標準の `<tool_call>{"name": …, "arguments": {…}}</tool_call>`
  形式を指示。応答の解析は寛容で、OpenAI 形式
  （`{"id","type","function":{…}}`）、タグ内のコードフェンス、タグなしの
  フェンス付き/裸 JSON もフォールバックで受理する。
- 過去ターンの assistant ツールコールと tool 結果（`tool_call_id` 付き）も
  transcript に復元するので、複数ターンのツールループが Kiro 側から一貫して見える。

## インストール

対象: `NousResearch/hermes-agent`（このパッチはベースコミット `57c67149` 時点で
生成・検証しています）。

### 0. 事前準備（前提条件）

1. **Kiro CLI** をインストールし、ACP サブコマンドが動くことを確認する。

   ```bash
   which kiro-cli                 # 実体パスを確認（Linux/macOS なら ~/.local/bin/kiro-cli が多い）
   kiro-cli acp --help            # acp サブコマンドが存在するか確認
   ```

   > IDE / ヘッドレス環境はシェルの PATH を継承しないことが多いので、後段では
   > **絶対パス**で指定するのが安全です。

2. **Kiro CLI を認証する。** 用途に応じて 2 通り（どちらか一方でよい）。

   - **対話利用 → `kiro-cli login`**（`KIRO_API_KEY` は不要）
     AWS Builder ID / IAM Identity Center / Google / GitHub でログインします。
     リモート環境でもデバイスフロー（URL + ワンタイムコード）に対応。一度ログインすれば
     セッションが HOME 配下にキャッシュされ、Hermes が起動する ACP サブプロセスが再利用します。

     ```bash
     kiro-cli login
     ```

     > 重要: キャッシュは **HOME 配下**に保存されます。本 ACP クライアントはサブプロセスの
     > `HOME` を継承（または Hermes のプロファイル HOME）で渡すため、**`kiro-cli login` を
     > 実行したのと同じ HOME** で Hermes を動かしてください（copilot-acp と同じ前提）。

   - **ヘッドレス / 無人運用（CI など）→ `KIRO_API_KEY`**（`login` は不要）
     この環境変数をセットすると Kiro CLI はブラウザログインを完全にスキップして
     非対話で動きます。**Kiro Pro / Pro+ / Power サブスク限定**の機能です。
     値は CI のシークレットとして渡し、コミットや設定ファイルに直書きしないこと。

     ```bash
     export KIRO_API_KEY="..."   # 子プロセスへ継承される
     ```

3. **Hermes を通常どおりインストールする。**

   公式インストールは、いずれの経路でも **リポジトリを git clone して editable
   install（`pip install -e .`）し、`~/.local/bin/hermes` にコマンドを張る**仕組みです。
   そのため**インストール先のチェックアウトがそのままパッチ適用先**になり、
   検証用に別クローンを作る必要はありません。

   - **経路A: 公式ワンライナー（おすすめ。これが通常インストール）**

     ```bash
     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
     ```

     既定のチェックアウト先は `~/.hermes/hermes-agent`（root は `/root/.hermes/hermes-agent`）。
     `hermes` コマンドは `~/.local/bin/hermes` に張られ、PATH 設定もインストーラが行います。

   - **経路B: ソースから（コントリビュータ手順。これも“通常”の一つ）**

     ```bash
     git clone https://github.com/NousResearch/hermes-agent.git
     cd hermes-agent
     ./setup-hermes.sh     # uv + venv + editable install + ~/.local/bin/hermes シンボリックリンク
     ```

### 1. インストール先のチェックアウトにパッチを当てる

editable install なので、**チェックアウト内のファイルがそのまま `hermes` の実行コード**です。
パッチを当てれば**再インストール不要で即反映**されます（インストール前・後どちらで当ててもよい）。

```bash
# 経路A の既定パスに移動（root は /root/.hermes/hermes-agent）
cd ~/.hermes/hermes-agent
# 経路B の場合は clone したディレクトリ（例: cd ~/hermes-agent）

# 事前チェック（何も出力されず終了コード 0 なら綺麗に当たる）→ 適用
git apply --check /path/to/0001-add-kiro-acp-provider.patch
git apply         /path/to/0001-add-kiro-acp-provider.patch
```

本流は更新が速く、行コンテキストがずれて上記が失敗することがあります。その場合は順に：

```bash
# (a) 3-way マージで当てる（コンフリクトマーカーが入ることがある）
git apply --3way /path/to/0001-add-kiro-acp-provider.patch

# (b) git を介さず当てる（ファジーマッチ・.rej を残す）
patch -p1 < /path/to/0001-add-kiro-acp-provider.patch

# (c) 検証時とまったく同じ状態に当てたい場合はベースコミットに固定してから当てる
#     （経路A のインストーラなら `--commit 57c67149` でも固定可能）
git checkout 57c67149
git apply /path/to/0001-add-kiro-acp-provider.patch
```

それでも当たらない箇所は `*.rej` を見ながら、本 README 末尾の
「変更点の要約」に従って手で当て直してください（追記はすべて copilot-acp と
並列に 1 ブロック足すだけなので、対応箇所はすぐ見つかります）。

> **`hermes update` の注意:** 公式の更新は内部で `git stash` / `git checkout` を行うため、
> 未コミットのパッチは退避・コンフリクトする可能性があります。チェックアウト内で
> `git commit` してから更新するか、更新後に当て直すのが安全です。

### 2. 反映を確認する

```bash
# 新規ファイルが入っているか
ls agent/kiro_acp_client.py plugins/model-providers/kiro-acp/

# プロバイダ一覧に kiro-acp が出るか
hermes models providers | grep -i kiro
```

> `hermes: command not found` のときは `~/.local/bin` が PATH に入っているか確認:
> ```bash
> echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin" || export PATH="$HOME/.local/bin:$PATH"
> ```
> 恒久化はシェルの rc ファイルに追記（公式インストーラは通常これを設定済み）。

### 3. 取り消したいとき

```bash
cd ~/.hermes/hermes-agent      # 経路B は clone 先
git apply --reverse /path/to/0001-add-kiro-acp-provider.patch
# もしくはコミット前なら
git checkout -- . && git clean -fd plugins/model-providers/kiro-acp agent/kiro_acp_client.py
```

## 使い方

```bash
# IDE/ヘッドレスは PATH を継承しないことが多いので、絶対パス指定を推奨
export HERMES_KIRO_ACP_COMMAND="$HOME/.local/bin/kiro-cli"   # which kiro-cli で確認

# 認証は事前準備で済ませた方法に従う:
#   対話利用     → 事前に `kiro-cli login`（KIRO_API_KEY は不要）
#   ヘッドレス運用 → export KIRO_API_KEY="..."（login は不要 / Pro 以上）

hermes chat --provider kiro-acp --model kiro-acp
```

別名でも指定できます（`kiro` / `kiro-cli` / `kiro-agent` / `kiro-acp-agent` → `kiro-acp`）。

### 環境変数

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `HERMES_KIRO_ACP_COMMAND` | `kiro-cli` | 起動する Kiro CLI の実体パス |
| `KIRO_CLI_PATH` | （未設定） | 上の代替（後方互換的な別名） |
| `HERMES_KIRO_ACP_ARGS` | `acp` | CLI に渡す引数（`shlex` で分割） |
| `KIRO_ACP_BASE_URL` | `acp://kiro` | ACP マーカー URL の上書き（`acp+tcp://...` も可） |
| `KIRO_API_KEY` | （未設定） | **ヘッドレス/無人運用時のみ**。セットすると `kiro-cli login` をスキップして非対話認証（Pro 以上）。対話利用では不要 |

## 変更点の要約

新規ファイル:
- `agent/kiro_acp_client.py` — Kiro 用 ACP クライアント（`copilot_acp_client.py` の
  雛形。`acp://kiro` マーカー、`kiro-cli acp` 既定、Copilot 固有の gh-copilot
  非推奨判定は除去）。Hermes ツールのプロンプト受け渡しと `<tool_call>` 解析は
  上記「ツールブリッジ」のとおり強化済み。
- `plugins/model-providers/kiro-acp/{__init__.py,plugin.yaml}` — プロバイダプロファイル登録。

既存ファイルへの追記（いずれも copilot-acp と並列に 1 ブロック追加するだけ）:
- `agent/agent_runtime_helpers.py` — `acp://kiro` / `provider == "kiro-acp"` を
  `KiroACPClient` にディスパッチ。
- `agent/agent_init.py` — Responses API 自動昇格の除外に kiro-acp を追加し、
  `command`/`args` を ACP クライアントへ受け渡し。
- `hermes_cli/providers.py` — `HERMES_OVERLAYS` に `kiro-acp`、表示ラベル、別名。
- `hermes_cli/auth.py` — `PROVIDER_REGISTRY` に `kiro-acp`、別名、`DEFAULT_KIRO_ACP_BASE_URL`、
  および外部プロセス系リゾルバ（`get_external_process_provider_status` /
  `resolve_external_process_provider_credentials`）を**プロバイダ別の起動既定**で
  汎用化（copilot-acp の挙動は不変）。
- `hermes_cli/runtime_provider.py` — 外部プロセス系ルートを kiro-acp にも適用。
- `hermes_cli/models.py` — curated モデル一覧・プロバイダ一覧・別名に kiro-acp を追加。

### スコープ外（必要なら別途）

対話セットアップウィザード（`hermes setup` / `model_setup_flows.py` /
`main.py` のプロバイダ選択分岐）への組み込みは含めていません。
本パッチは `hermes chat --provider kiro-acp` での直接利用を成立させることを目的としています。

## 検証済みの内容

- 変更後の全 Python ファイルが `py_compile` を通過。
- `git apply --check` がベースコミットに対して成功（reverse / forward の往復適用も確認）。
- ツールブリッジのユニットテスト（プロンプト整形と `<tool_call>` 抽出: Hermes 形式 /
  OpenAI 形式 / コードフェンス内 JSON / フェンスのみ / 裸 JSON / 大文字タグ /
  ツールなし時の素通し）を通過。
