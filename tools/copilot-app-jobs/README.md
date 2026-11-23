# copilot-app-jobs — GitHub Copilot のデスクトップアプリに、フォルダ越しで依頼を渡す

Dagu などの外のワークフローから、GitHub Copilot のデスクトップアプリに作業を頼み、結果を受け取る
ための小さな道具です。GitHub のリポジトリが無いローカルのフォルダでも使えます。Copilot の CLI と
VS Code は要りません。

アプリには外からセッションを始める口がありません。そこで、作業フォルダの中に受け箱を作り、
**アプリの自動実行（オートメーション）が受け箱を見に来る**形にします。

```text
Dagu のステップ
  └─ copilot_app_job.py run … 依頼を受け箱に置き、結果が出るまで待つ
        ↓ 受け箱（作業フォルダ/.copilot-jobs）        ↑
Copilot のデスクトップアプリ（作業フォルダをプロジェクトとして開いておく）
  └─ 自動実行が決まった間隔でセッションを始める
      └─ 受け箱の依頼を古い順に処理し、結果を書き戻す
```

## 用意

前提は Python 3.9 以上（標準ライブラリだけで動きます）と、GitHub Copilot のデスクトップアプリです。

1. このフォルダの `copilot_app_job.py` を、たとえば `C:\tools\copilot-app-jobs\` に置きます。
2. アプリで、作業フォルダをプロジェクトとして開きます（ローカルのフォルダを開く）。
3. 自動実行に貼るプロンプトを出します。受け箱もこのとき作られます。

   ```powershell
   python C:\tools\copilot-app-jobs\copilot_app_job.py prompt --dir C:\work\my-folder
   ```

4. アプリで新しい自動実行を作ります。プロジェクトは 2 のフォルダ、実行する場所はローカル、
   プロンプトは 3 で出たものをそのまま貼り、間隔を決めます（下の「空振りを小さくする」）。
5. 自動実行を一度手で動かし、「なし」とだけ答えて終わることを確かめます。

アプリからコマンド（`python`）を実行できることが前提です。

ローカルの自動実行は、**PC が起きていてアプリが開いているときだけ**動きます。

## 依頼を出す

```powershell
python C:\tools\copilot-app-jobs\copilot_app_job.py run --dir C:\work\my-folder --prompt "docs フォルダの誤字を直して"
```

依頼の本文は `--prompt`、`--prompt-file <ファイル>`、標準入力のどれかで渡します。ファイルと標準入力は UTF-8 で
書きます。アプリが処理を
終えると、報告が標準出力に出ます。

| 終了コード | 意味 | やり直し |
|---|---|---|
| 0 | できた | — |
| 1 | アプリが「できなかった」と報告した（理由が標準出力に出る） | 依頼を直してから |
| 2 | 使い方の誤り（フォルダが無い・依頼が空・本文のファイルが無い） | — |
| 3 | 時間内にアプリが拾わなかった。依頼は取り下げ済み | してよい |
| 4 | 取りかかったが時間内に終わらなかった。依頼は残っている | しない（二重になる） |

待つ時間は `--timeout <秒>`（既定 3600）です。**自動実行の間隔 + 作業にかかる時間**より長くします。
置くだけで待たないときは `--no-wait`、受け箱の様子は `status --dir <フォルダ>` で見られます。

Dagu から呼ぶ例は [`dags/copilot-app-job.example.yaml`](./dags/copilot-app-job.example.yaml) にあります。

## 定型メニュー

夜間の依頼文を `nightly_menu.py` で生成できます。別の場所に設置する場合は、`copilot_app_job.py` と
同じフォルダに `nightly_menu.py` と `menu/` を置きます。

| メニュー | 目的 | 必須パラメータ |
|---|---|---|
| [docs-followup](./menu/docs-followup.md) | コード変更に合わせたドキュメント更新案 | `target_dir` |
| [test-maintenance](./menu/test-maintenance.md) | テストケースとコードの追加・更新案 | `lint_results`、`bindings` |
| [changelog-draft](./menu/changelog-draft.md) | CHANGELOG の [Unreleased] 追記案 | `period` |
| [drift-scan](./menu/drift-scan.md) | 検査で見つかった乖離の一覧と優先度 | `doc_drift_results`、`codd_gate_results` |

```powershell
python nightly_menu.py list
python nightly_menu.py generate --menu docs-followup --param target_dir=src --param "revision_range=HEAD~3..HEAD" --out docs-request.md
python nightly_menu.py submit --menu docs-followup --param target_dir=src --dir C:/work/my-folder --name docs-followup
```

`--param k=v` は複数回指定できます。任意パラメータと既定値は各テンプレートの入力欄を参照してください。
相対パスで指定する作業対象・入力・成果物はアプリの作業フォルダを基準にします。
`generate` は `--out` を省略すると標準出力に依頼文を出します。ファイルに保存した場合は
`copilot_app_job.py run --prompt-file docs-request.md --dir C:/work/my-folder` で依頼して結果を待てます。
`submit` は依頼を置いて ID を出し、すぐに終了します。別の受け箱を使う場合は `--box` を指定できます。

毎晩 02:00 に順番に実行する [Dagu の例](./dags/nightly-menu.example.yaml) と、
朝に結果を確認する [検収手順書](./menu/ACCEPTANCE.md) を同梱しています。

## 空振りを小さくする

自動実行は、依頼が無い回もセッションを 1 つ始めます。空振りはゼロにはできないので、**1 回を小さく**し、
**回数を減らし**ます。

1 回を小さくする工夫は、道具の側に入っています。

- 貼るプロンプトは「コマンドを 1 回実行する」の 3 行だけです。手順は書いてありません。
- 依頼の取り出し・結果の書き込み・ファイルの移動は、アプリが呼ぶコマンド（`next`・`done`）がします。
  依頼が無い回は、コマンド 1 回と「なし」の一言で終わります。
- 作業の手順は、依頼があったときにだけ、コマンドの出力としてアプリに渡ります。

残りは、アプリとフォルダの側で減らせます。セッションは始まるたびに、プロジェクトの指示ファイルと、
有効な拡張（プラグイン・MCP サーバー・スキル）の一覧を読み込むためです。

- 受け箱用のプロジェクトでは、使わない拡張を切っておきます。
- 作業フォルダに長い指示ファイルを置かないようにします。
- 自動実行ごとにモデルを選べるなら、軽いモデルにします（依頼の作業も同じモデルで動きます）。

回数を減らすには、次のどちらかにします。

- **間隔を空け、動かす時間帯を絞る。** 依頼を置いてから拾われるまで、最大で間隔のぶん待ちます。
- **セッションに居座らせる。** 依頼が無いとき、コマンドが依頼を待ち続けます。待っている間は
  モデルは動きません。

  ```powershell
  python C:\tools\copilot-app-jobs\copilot_app_job.py prompt --dir C:\work\my-folder --stay 50
  ```

  `--stay 50` なら 50 分待つので、自動実行を 1 時間おきにすれば、空振りは 1 時間に 1 回で、依頼は
  置いてすぐ拾われます。アプリが長く止まるコマンドを打ち切るときは、`--chunk 600` のように 1 回の
  待ちを区切ります（区切るたびに短い 1 往復が増えます）。

`python` が別の名前なら `--python py` のように渡します。

## ほかのフォルダのセッションに振る

アプリの自動実行は、ほかのローカルフォルダで新しいセッションを起こせます。依頼に振り先（`--to`）を付けると、
自動実行はそのフォルダでセッションを起こして依頼を渡し、**終わりを待たずに**次の依頼へ進みます。振られたセッションは
結果を MR や issue に書き、最後に受け箱へ「終わった」と知らせます。

```text
Dagu などのステップ
  └─ copilot_app_job.py run --to C:\work\sandbox --no-wait …   依頼を置いてすぐ終わる
        ↓ 受け箱（作業フォルダ/.copilot-jobs）
Copilot アプリの自動実行（作業フォルダのプロジェクト）
  └─ 振り先のフォルダでセッションを起こして依頼を渡し、dispatched を呼んで次へ（待たない）
        ↓
sandbox フォルダのセッション
  └─ 作業 → 結果を MR・issue に書く → finish を呼ぶ（この振り先が空く）
```

**振り先はいくつでも登録でき、自動実行は受け箱のあるフォルダ（ハブ）に 1 つあれば足ります。** 振り先ごとに
自動実行を作る必要はありません。

**同じ振り先へは同時に 1 つだけ振ります。** 振り先のセッションが `finish` を呼ぶまで、同じ振り先の次の依頼は
受け箱で待ち、置いた順に振られます。振り先が違えば並べて振れます。

1. 振ってよいフォルダを登録します（登録していないフォルダには振りません）。振り先が複数あれば、1 つずつ足します。

   ```powershell
   python C:\tools\copilot-app-jobs\copilot_app_job.py target add --dir C:\work\my-folder --to C:\work\sandbox
   python C:\tools\copilot-app-jobs\copilot_app_job.py target add --dir C:\work\my-folder --to C:\work\docs
   ```

2. 依頼に、結果をどこに書くかを書いて置きます。

   ```powershell
   python C:\tools\copilot-app-jobs\copilot_app_job.py run --dir C:\work\my-folder --to C:\work\sandbox --no-wait --prompt "README の誤字を直して MR を出してください"
   ```

自動実行に貼るプロンプトは、振らない依頼と同じものです（`prompt` が出すもの）。

| コマンド | 呼ぶもの | すること |
|---|---|---|
| `target add` / `remove` / `list` | 人 | 振ってよいフォルダを足す・外す・並べる |
| `dispatched --id <ID>` | 自動実行 | 振り先でセッションを起こしたと記録し、次の依頼を出す。起こせなかったら `--failed "理由"` |
| `route --id <ID> --to <名前>` | 自動実行 | `--to auto` の依頼の振り先を候補から決めて、待ち行列に戻す |
| `finish --id <ID> --status ok --note <URL>` | 振られたセッション | 終わったことと、書いた MR・issue を記録し、振り先を空ける |

- 振ったまま 6 時間 `finish` が来ない依頼は、失敗として記録して振り先を空けます（二重にならないよう、振り直しは
  しません）。時間は `next --dispatch-stale <秒>` で変えられます。
- `run --to` に待つ形（`--no-wait` なし）を使うと、`finish` が来るまで待ちます。振る目的からすると、ふつうは待ちません。
- 振り先のフォルダも、アプリで一度プロジェクトとして開いておきます。

### 振り先をセッションに選ばせる（`--to auto`）

振り先を決めずに置き、自動実行のセッションに候補から選ばせることもできます。候補は JSON で渡します。

```json
{
  "default": "app",
  "choices": {
    "app":  {"dir": "C:/work/app",  "description": "業務アプリのコードと不具合", "append": "結果を group/app に issue で上げてください"},
    "docs": {"dir": "C:/work/docs", "description": "手順書とマニュアル", "append": "結果を group/docs にコミットしてください"}
  }
}
```

```powershell
python C:\tools\copilot-app-jobs\copilot_app_job.py run --dir C:\work\my-folder --to auto --choices-file choices.json --no-wait --prompt "…"
```

- 自動実行が拾うと、候補の名前と `description` を見て 1 つ選び、`route --id <ID> --to <名前> --why "<理由>"` を呼びます。
  合うものが無ければ `default` を選びます。
- 選ぶと、依頼の頭に「この依頼の振り先: 名前（理由）」が、末尾にその候補の `append` が付き、振り先の決まった依頼として
  受け箱の待ち行列に戻ります（置いた順はそのまま）。あとはふつうの振る依頼と同じです。
- 候補のフォルダは、どれも `target add` で登録しておきます。

## 受け箱の中身

受け箱は作業フォルダの `.copilot-jobs` です（`--box` で別の場所にできます）。

| フォルダ | 中身 |
|---|---|
| `pending` | 置いた依頼。ファイルの本文がそのままプロンプトになる |
| `running` | アプリが取りかかった依頼 |
| `dispatched` | ほかのフォルダのセッションに振った依頼（そのセッションが `finish` するまで） |
| `done` | 済んだ依頼 |
| `results` | 結果。1 行目が `status: ok` か `status: failed`、3 行目から報告 |
| `targets.txt` | 振ってよいフォルダ（`target add` で足す） |

済んだものは自動では消えません。たまったら `done` と `results` を消してかまいません。作業フォルダを
git で管理しているなら、`.copilot-jobs/` を無視の対象に入れておきます。

## 気をつけること

- **受け箱に置かれた文はそのまま実行されます。** 受け箱に書き込めるのを、依頼を出す人と仕組みだけに
  しておきます。振り先の登録（`targets.txt`）も同じです。
- アプリのセッションが途中で落ちた依頼は `running` に残り、2 時間たつと先にやり直されます
  （`next --stale <秒>` で変えられます）。
- 同じ作業フォルダの依頼は 1 件ずつ順に処理されます。並べて走らせたいときはフォルダを分け、
  それぞれに自動実行を作ります。

## テスト

```bash
python -m unittest discover -s tools/copilot-app-jobs/test
```

アプリ役をテストが演じます。アプリ本体は呼びません。
