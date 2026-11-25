# agent-dots — 目標を預けて、裏で進めてもらう

目標を 1 つ預けると、GitHub Copilot のデスクトップアプリが裏で少しずつ進め、報告と質問と承認の依頼だけを
GitLab のハブの issue に書きます。返事はハブの issue にコメントすれば届きます。個人で使う道具です。

```text
Dagu（15 分おき）
  tick     起こす理由（始め・返事・決めた時刻）が無ければ、ここで終わる（アプリは動かない）
  ask      Copilot アプリに依頼を渡す（copilot-app-jobs の受け箱）
             └ アプリは目標のフォルダで作業し、外へ出したいことを outbox/ に「提案」として書く
  act      提案を policy.json と照らして片付ける（許していないものは捨てて記録する）
             └ 聞いてからやるものは承認を頼み、承認されたら動かす
  comment  報告と質問をハブの issue にコメントする → GitLab の通知メール

Dagu（毎分）
  ハブの issue の「【名前】…」のコメント → その名前の目標の inbox/ に置く → 次の tick で起きる
```

起こすかどうかと、外へ書くかどうかは、このフォルダのスクリプトが決めます。エージェント（Copilot アプリ）は
提案を書くだけで、ハブの issue へは直接書きません。

## 用意

前提は Python 3.9 以上（標準ライブラリだけで動きます。業務係を使うときは PyYAML も）、GitHub Copilot のデスクトップアプリ、Dagu です。

1. [copilot-app-jobs](../copilot-app-jobs/README.md) を置きます（例: `C:\tools\copilot-app-jobs\`）。
2. [gitlab-issue-hub-agent](../change-watch/agents/gitlab-issue-hub-agent/README.md) を入れて、ハブの issue を決めます
   （その README の「入れ方」の 1〜5）。ハブの issue の通知をオンにしておくと、報告と質問がメールで届きます。
3. このフォルダの `dot.py` を置きます（例: `C:\tools\agent-dots\`）。

## 目標を作る

1. 目標のフォルダを作ります。名前は、ハブの issue で呼びかけるときに使います（空白と【】は使えません）。

   ```powershell
   python C:\tools\agent-dots\dot.py init --dir C:\work\dots\estimate --name 見積り
   ```

2. `goal.md` に、目標と**完了の条件**を書きます。
3. Copilot アプリで、このフォルダをプロジェクトとして開き、自動実行を 1 つ作ります。貼るプロンプトは
   copilot-app-jobs が出します（間隔は 1 時間、`--stay 50` で居座らせると、依頼を置いてすぐ拾われます）。

   ```powershell
   python C:\tools\copilot-app-jobs\copilot_app_job.py prompt --dir C:\work\dots\estimate --stay 50
   ```

4. ツールを止めるフックを置きます（下の「ツールを止める」）。

   ```powershell
   python C:\tools\agent-dots\dot.py hooks --dir C:\work\dots\estimate
   ```

5. [`dags/agent-dots-tick.example.yaml`](./dags/agent-dots-tick.example.yaml) を Dagu のワークフローの置き場にコピーし、
   名前・`DOT`・パスを直します。目標ごとに 1 つ置きます。
6. [`dags/agent-dots-listen.example.yaml`](./dags/agent-dots-listen.example.yaml) を 1 つだけ置き、`DOTS`（目標のフォルダを並べた
   親フォルダ）とパスを直します。目標がいくつあっても 1 つで足ります。
7. 一度ハブの issue の今のコメントを読み飛ばします（今あるコメントを覚えるだけで、何もしません）。

   ```powershell
   change-watch --plugin gitlab_hub --config <設定> --connections <接続設定> get --source gitlab-hub-notes --consumer dots --no-jitter
   ```

最初の tick で目標が動き出し、計画やメモを作って、報告か質問をハブの issue に書きます。

## 話しかける

ハブの issue に、1 行目を `【名前】` で始めたコメントを書きます。

```text
【見積り】 前提は B で進めてください
```

次の tick で目標が起き、このコメントを読んで続きを進めます。目標が書くコメントは `【名前より】` で始まります。

| 書き方 | 意味 |
|---|---|
| `【見積り】承認 a3` | 承認待ちの a3 を動かしてよい。次の片付けで動き、結果がハブの issue に書かれる |
| `【見積り】却下 a3 まだ早い` | a3 は動かさない。理由は目標に伝わる |
| `【見積り】q2 B で` | 質問 q2 への返事 |
| `【見積り】…` | それ以外はふつうの話しかけ |

承認待ちは、決めた日数（既定 7 日）返事が無いと取り下げます。

## 目標が書ける提案

エージェントは作業を終える前に、目標のフォルダの `outbox/` に提案を 1 件 1 ファイルの JSON で書きます。

| 提案 | 例 | どうなるか |
|---|---|---|
| メモ | `{"kind": "note", "body": "…"}` | `notes/` の日ごとのファイルに 1 行足す。次に起きたときの依頼に載る |
| 報告 | `{"kind": "act", "action": "hub.comment", "body": "…"}` | ハブの issue にコメントする |
| コマンドを動かす | `{"kind": "act", "action": "run", "name": "publish", "input": "…", "why": "…"}` | policy.json に登録したコマンドを、決めた段（すぐ動かす・承認を頼む）で動かす。`input` は標準入力に渡る。結果は次に起きたときに届く |
| 質問 | `{"kind": "ask", "question": "…", "options": ["A", "B"]}` | ハブの issue に番号（q1 …）と選択肢つきで書く |
| 次に起きる時刻 | `{"kind": "wait", "until": "2026-11-25T09:00"}` | その時刻を過ぎた最初の tick で起きる（タイムゾーンを書かなければこの PC の時刻） |
| 完了 | `{"kind": "done", "evidence": ["…"]}` | 完了の検査があれば動かし、通れば終わる。無ければ「確かめてください」と書いて止まる |

時刻を決めなければ、次は返事が来るまで起きません。提案を 1 件も書かずに終わった回は、届いていた返事を戻して
やり直します（2 回まで。それでも無ければ捨てて記録します）。

## やってよいことを決める（policy.json）

```json
{
  "actions": {"hub.comment": "auto"},
  "commands": {
    "publish": {"run": ["python", "C:/tools/publish.py"], "level": "ask", "description": "下書きを共有フォルダに置く", "timeout": 600}
  },
  "approvers": [],
  "approval_expire_days": 7,
  "quiet_hours": "22:00-07:00",
  "wake_per_day": 24,
  "done_check": ["python", "check.py"]
}
```

- `actions`：ハブへの報告（`hub.comment`）を `auto`（聞かずに書く）・`ask`（承認されてから書く）・`deny`（書かない）の
  どれにするか。ここに書いても、知らない作用は通しません。
- `commands`：目標に動かしてよいコマンドを名前で登録します。`run` はコマンドと引数のリスト（シェルは通しません）、
  `level` は `auto`・`ask`（既定）・`deny`、`description` は承認を頼むときと依頼文に出す説明、`timeout` は秒です。
  コマンドは目標のフォルダで動き、提案の `input` を標準入力（UTF-8）で受け取ります。登録していないコマンドは動きません。
- `approvers`：承認・却下できる GitLab のユーザー名。空なら、人が書いたコメントならだれでも。
- `approval_expire_days`：承認待ちを取り下げるまでの日数（0 なら取り下げない）。
- `quiet_hours`：コメントを書かない時間帯（日をまたいでよい）。この間の報告・質問・承認の依頼は溜めておき、明けてから
  まとめて書きます。空ならいつでも書きます。
- `wake_per_day`：1 日に起きてよい回数。使い切ると、その日は起きません（0 なら上限なし）。
- `done_check`：完了の検査。目標のフォルダで動かし、終了コード 0 で完了にします。空なら、完了は人が確かめます。
- `proactive_per_day`・`proactive_idle_min`・`tools`：下の「手の空いた時間に調べる」と「ツールを止める」。

## 手の空いた時間に調べる

起こす理由が無くても、利用者とのやりとりが `proactive_idle_min` 分（既定 120）無ければ、1 日 `proactive_per_day` 回
（既定 0 = しない）まで、目標を起こして調べものをさせます。

```json
{"proactive_per_day": 2, "proactive_idle_min": 120}
```

- この回は読むだけです。書ける提案はメモ・質問・次に起きる時刻だけで、報告・コマンド・完了は捨てます。
- フックが、ファイルを作れる先を `notes/` と `outbox/` に絞り、`tools.shell_allow` のコマンドも止めます。
- `quiet_hours` の間と、決めた時刻がすぐ先にあるときは起こしません。`wake_per_day` の回数にも数えます。

## 蓄積から次の仕事を見つける（Discovery）

`harvest` は許可されたソースから候補を拾い、`.dot/discovery/` の索引に保存します。手が空いたときの
`tick` で候補を評価し、`act` が受け付けた提案をキューに入れます。Phase 1 は全件が承認待ちになり、
承認後の `act` が仕事を登録します。登録先の目標や業務係が既存のループで進め、結果は提案元の
`.dot/discovery/history.jsonl` に残ります。

Phase 2 の意味検索の第1段階では、標準ライブラリのみでローカル索引の候補間の TF-IDF
コサイン類似度を計算します。出典を確認済みの候補が 20 件を超える場合、評価資料に関連候補を
最大 10 件追加し、類似性の高い候補を同じ行にまとめます。提案の `sources` には各出典の表記を
個別に指定します。sqlite-vec などのベクトル永続化は後段で扱います。

### 設定

目標の `policy.json` に次のように設定します。既存の目標は `discovery` 節を省略できます。

```json
{
  "proactive_per_day": 1,
  "discovery": {
    "enabled": true,
    "auto_execute": [],
    "proposals_per_day": 5,
    "require_approval": ["medium以上", "external"],
    "collectors": {
      "agent_dots": {"enabled": true, "paths": ["C:/work/dots/estimate"]},
      "plans": {"enabled": true, "paths": ["C:/work/project/docs/plans"]},
      "skill_feedback": {"enabled": true, "paths": ["C:/work/agent-home/skill-registry.json"]},
      "vault": {"enabled": false, "paths": ["C:/work/Work Notes"], "vault": "Work Notes", "mode": "auto"}
    },
    "goal_root": "C:/work/dots",
    "routine_dir": "C:/work/dots/routines"
  }
}
```

| 項目 | 型・既定 | 意味 |
|---|---|---|
| `enabled` | boolean、`false` | 収集、評価、新規提案の受付、承認済み提案の新規登録を有効にする。停止中もキューの表示と登録済みの仕事の結果照合はできる |
| `proposals_per_day` | 0 以上の整数、`5` | 目標単位の 1 日の受付上限。0 で新規受付と評価を止める。提案を受け付けた日で数える |
| `auto_execute` | 文字列配列、`[]` | Phase 2 で有効。許可条件は `readonly` と `small/local-write`。前者はリスク、後者は規模とリスクの組で照合する |
| `require_approval` | 文字列配列、`["medium以上", "external"]` | `medium以上` は `medium` と `large`、`external` はリスク区分との一致。自動承認の条件より優先する |
| `collectors.<名前>.enabled` | boolean、下表 | ソースごとの有効化。省略したコレクタにも下表の既定を適用 |
| `collectors.<名前>.paths` | パスの文字列配列、下表 | コレクタが読めるルートまたはファイル。相対パスは目標フォルダ基準。実体に解決して範囲を検査する |
| `collectors.vault.vault` | 文字列、省略可 | CLI に渡す Vault 名または ID。CLI を使う場合は `paths` に Vault ルートを 1 つ指定し、そのルートと対応づける |
| `collectors.vault.mode` | `auto` / `files`、`auto` | `auto` は公式 CLI を優先し、CLI が使えない場合に直接読みに切り替える。`files` は直接読みを使う |
| `goal_root` / `routine_dir` | ディレクトリのパス文字列、省略可 | 管理対象の目標を並べる親フォルダと業務係の目標。相対パスは提案元の目標基準。登録先を解決できない承認済み提案は理由を記録して待つ |

| コレクタ名 | Phase 1 既定 | `paths` の既定と指定先 |
|---|---|---|
| `agent_dots` | 有効 | 自分の目標。複数の目標を読むときは個別に指定 |
| `plans` | 有効 | 未指定。対象リポジトリの `docs/plans` を指定 |
| `skill_feedback` | 有効 | `skill-feedback-digest` と同じ規則で解決した `<AGENT_HOME>/skill-registry.json`。解決できなければ未指定扱い |
| `prompt_assets` / `session_logs` | 無効 | 未指定。スニペットやスキルの置き場、または各ツールの履歴フォルダをそれぞれ指定 |
| `knowledge` / `git_todo` | 無効 | 未指定。文書の置き場、またはローカルリポジトリのルートを指定 |
| `codd_retro` / `ltm_use` / `moltbook` | 無効 | 未指定。振り返りの出力先、記憶のファイルやエクスポート、ナレッジの蓄積先をそれぞれ指定 |
| `vault` | 無効 | 未指定。Vault のルートを指定 |

任意のパスが未指定なら、そのソースはスキップします。評価を起こす条件は既存の
`proactive_per_day`、`proactive_idle_min`、`wake_per_day`、`quiet_hours` です。
`proactive_per_day` が既定の 0 なら収集だけが動きます。登録後の仕事には実行先の policy が適用されます。

### 収集と採否

`require_approval` に該当せず `auto_execute` に一致する提案は、受付時に自動承認します。
キューには `decision.by = "policy"` と承認理由が残り、journal には一致した条件が残ります。
次の `act` が承認を履歴に記録して登録を進めます。自動承認の効力は登録までで、
実行には登録先の既存 policy・フック・`done_check` が適用されます。
自動承認された提案も `proposals_per_day` の受付枠を消費します。

```sh
python dot.py harvest --dir C:/work/dots/estimate
python dot.py proposals list --dir C:/work/dots/estimate
python dot.py proposals show --dir C:/work/dots/estimate p123
python dot.py proposals approve --dir C:/work/dots/estimate p123 --reason "計画と完了条件を確認した"
python dot.py proposals reject --dir C:/work/dots/estimate p456 --reason "既存の計画で対応済み"
```

`--dir` には同じ目標フォルダを指定し、一覧に出た ID を使います。

| コマンド | すること |
|---|---|
| `list --dir <目標> [--status pending\|approved\|rejected\|all] [--json]` | 既定は承認待ち。受付日時の新しい順、同時刻は ID 順。JSON は提案レコードの配列で、0 件なら `[]` |
| `show --dir <目標> <id> [--json]` | 根拠、全出典と該当箇所、規模・リスク、推奨ルート、採否と理由、登録先・処理状況を表示。JSON は提案レコード 1 件 |
| `approve --dir <目標> <id> --reason <理由>` | `pending` を `approved` にして、次の act に渡す |
| `reject --dir <目標> <id> --reason <理由>` | `pending` を `rejected` にして、次の act に渡す |

採否には空白以外の理由を書きます。同じ判断を再送すると最初の判断が表示されます。
承認した提案の登録が止まっている場合は `show` で理由を確認できます。

| 終了コード | harvest | proposals |
|---|---|---|
| 0 | 収集完了、または設定で無効 | 表示または判断の記録が完了。空の一覧、同じ判断の再送も含む |
| 1 | ソースや索引の処理に失敗。stderr と journal を確認 | ID が無い、逆の判断が記録済み、またはキューの読み書きに失敗。stderr を確認 |
| 2 | 引数、目標フォルダ、設定が不正 | 引数、目標フォルダ、理由の形式が不正 |
| 3 | 目標ロックの競合 | 目標ロックの競合 |

日次収集は [harvest の DAG 例](dags/agent-dots-harvest.example.yaml) を目標ごとに置き、
パスと時刻を合わせます。例は毎朝 8 時に動き、ロック競合（終了コード 3）だけを再試行します。
既存の tick DAG が公開済みの索引を使い、依頼を起こさなかった回も `act` が採否を取り込みます。

### Obsidian Vault を読む

1. Obsidian を **1.12.7+ のインストーラ**で更新し、設定 → 一般 →「コマンドラインインターフェイス」を
   有効にして案内どおりに登録します。Windows ではターミナルを開き直し、ユーザーセッションで動く
   Dagu も起動し直して PATH を反映します。[Obsidian 公式 CLI リファレンス](https://obsidian.md/help/cli)
2. 対象 Vault を開き、harvest を動かすユーザーの GUI セッションで次を確認します。

   ```sh
   obsidian version
   obsidian vault="Work Notes" search query="task-todo:" format=json
   ```

3. `collectors.vault` に同じ Vault のルート 1 つと Vault 名または ID を設定し、
   `"enabled": true`、`"mode": "auto"` で harvest を一度動かします。出典が索引に載り、
   Vault のファイルが変わっていないことを確認します。
4. 公式 CLI は GUI セッションでアプリに接続します。Dagu が Windows サービスで動き、
   対話セッションに届かない場合は、サービス実行ユーザーが Vault を読めることを確認し、
   同じルートを指定して `"mode": "files"` で動かします。`auto` で CLI が使えなかった回は、
   journal に理由を残してファイルの直接読みに切り替えます。

## ツールを止める（フック）

`dot.py hooks --dir <目標のフォルダ>` は、目標のフォルダの `.github/hooks/agent-dots.json` に、Copilot がツールを使う前に
呼ぶフック（`preToolUse`）を置きます。フックは `policy.json` の `tools` と照らして、次のものを止めます。

| ツール | 通すもの | 足し方 |
|---|---|---|
| ファイルを読む | 目標のフォルダの中 | `tools.read_allow` にフォルダを足す |
| ファイルを書く | 目標のフォルダの中。ただし `policy.json`・`state.json`・`journal.jsonl`・`.dot/`・`.github/`・受け箱は変えられない（受け箱の `tmp/` に報告を書くのは通す） | 足せない |
| コマンド | 受け箱とのやりとり（`copilot_app_job.py next` / `done`）だけ。`;`・`&&`・パイプ・リダイレクトなどでつないだものは止める | `tools.shell_allow` に正規表現を足す。`tools.shell` を `allow` にすると全部通す、`deny` にすると全部止める |
| Web | なし | `tools.web_allow` にホスト名を足す（`*` で全部） |
| そのほか（MCP のツールなど） | なし | `tools.allow` にツールの名前を足す。`tools.deny` に書いたツールは必ず止める |

```json
{
  "tools": {
    "read_allow": ["C:/work/specs"],
    "shell_allow": ["^git (status|log|diff)\\b"],
    "web_allow": ["docs.example.com"]
  }
}
```

止めたときは、理由がエージェントに伝わります。通したもの・止めたものは `.dot/hook.jsonl` に 1 行ずつ残ります
（ツールの名前と理由だけ。中身は残しません）。判定に失敗したときは止めます。

**フックが効いているかを最初に確かめてください。** Copilot アプリでこのフォルダを開き、「C:\Windows に test.txt を
作って」と頼みます。断られ、`.dot/hook.jsonl` に `deny` の行が増えれば効いています。増えなければ、アプリがプロジェクトの
フックを読んでいません。そのときはプラグインとして入れます（すべてのプロジェクトで動きますが、目標のフォルダの外では
何もしません）。

```powershell
python C:\tools\agent-dots\dot.py hooks --plugin C:\tools\agent-dots-plugin
```

出てきたフォルダを、アプリの Customize → Plugins → 歯車 → マーケットプレイスの追加で足し、`agent-dots` を入れます。

## 定型業務を頼む（業務係）

目標の 1 つを「業務係」にすると、ハブの issue に書いた依頼に合うステートマシン（statemachine-use の定義）を選んで
回します。合うものが無ければ、スキルを組み合わせて新しく作り、試運転してから登録します。実行の記録から直して、版を上げていきます。

```powershell
python C:\tools\agent-dots\dot.py init --dir C:\work\dots\work --name 業務
```

```json
{
  "routines": {
    "skills_dirs": ["C:/Users/you/.copilot/skills"],
    "sources": ["C:/work/repo/.statemachine"],
    "write_skills": ["outlook-use", "redmine-use", "teams-use"]
  }
}
```

- `skills_dirs`：スキルの置き場（statemachine-use と、業務に使うスキルが入っているところ）。これを書くと業務係になります。
- `sources`：既にあるステートマシンの置き場（読むだけで、書き換えません）。agent-app のタスクの `.statemachine/` も使えます。
- `write_skills`：外のシステムに書き込むスキル。試運転では動かしません。
- `publish_to`：作ったステートマシンを登録する場所（既定は目標のフォルダの `.statemachine/`）。

### 頼む

```text
【業務】10 月の売上の報告を作って、部長に送って
```

1. 業務係が手持ちの一覧から合うものを選び、依頼から入力を埋めます。足りない入力は質問が来ます。
2. そのステートマシンを初めて使うときは、使うスキル（外へ書くものに印）を見せて承認を求めます。承認すると、
   以後は聞かずに回します。中身が変わったら、もう一度承認を求めます。
3. 次に起きたとき、Copilot アプリがステートマシンを最後まで回し、結果をハブの issue に書きます。

### 手持ちに無い業務

1. 業務係がスキルを選んで、目標のフォルダの `routines/<名前>/` に下書きを作ります。下書きは検証され、通らなければ直させます（3 回まで）。
2. 元の依頼の入力で試運転します。外へ書くスキルは動かさず、書く中身を作るところまでにします。
3. 試運転の結果と使うスキルを見せて承認を求めます。承認すると `.statemachine/<名前>/` に登録します。
   却下すると、理由を添えて直します。

### 直す・戻す

| 書き方 | 意味 |
|---|---|
| `【業務】直す r3 宛先は設定から読む` | その回の記録と理由をもとに直した版を作り、r3 と同じ入力で試運転して、承認されたら版を上げて差し替える |
| `【業務】直す monthly-report 件名を変える` | 最後の回の入力で、同じように直す |
| `【業務】戻す monthly-report` | 1 つ前の版に戻す |

同じステートマシンが直近 3 回で 2 回失敗したら、聞かずに直す版を作ります（登録するには承認が要ります）。
ほかの置き場（`sources`）のものを直したときは、元は書き換えず、目標のフォルダに直した版を登録します（以後はそちらを使います）。

業務は 1 件ずつ進めます。12 時間進まなかった業務は取り下げます。

| ファイル | 中身 |
|---|---|
| `routines/<名前>/` | 作っている・直している下書き |
| `.statemachine/<名前>/` | 登録したステートマシン |
| `.dot/routines.json` | 台帳（承認した中身の指紋・使うスキル・版・実行の回数と成否） |
| `.dot/runs/<名前>/<rN>.json` | 実行の記録（入力・最後のステート・成否・各ステートの出力の 1 行目） |
| `.dot/routines-review.json` | 最新の見直し報告（日時・検査結果・関連する回 ID・処置） |
| `.dot/routines-history/<名前>/` | 前の版 |

### 見直し（定期）

```powershell
python dot.py review --dir C:/work/dots/estimate
```

業務が有効な目標で、台帳と実行記録を決定的な条件で検査します。定期実行には週 1 回の [Dagu の例](dags/agent-dots-review.example.yaml) を使えます。最新の報告は `.dot/routines-review.json` にあり、`status` で日時と要見直しの件数を確認できます。

| 検査 | 処置 |
|---|---|
| `stale_failure` | 直近の実行で失敗していないステートの失敗集計を自動で整理 |
| `repeat_failure` | 直近 3 回で 2 回以上失敗し、改善の予定・進行中の改善・下書きの登録承認待ちがなければ、改善の回を追加 |
| `contradiction` | 同じ入力で成功と失敗が混じる回を要見直しにする |
| `orphan_run` | 台帳にも一覧にも無い業務の記録を要見直しにする |
| `stale_draft` | 試運転中・承認待ちで、関連する回の終了から 7 日以上たった下書きを要見直しにする。回がなければ下書きの `workflow.yaml` の更新日時を使う |
| `steady` | 実行が 10 回以上あり、直近 10 回がすべて成功したら、失敗集計を消して `status` に「安定」を表示。その後失敗すると外れる |

「要見直し」は、報告書の該当する回や下書きを人が確認する項目です。原因の記録や下書きが変わるまで毎回の見直しで出続け、同じ項目は初出の日時（`first_seen`）で見分けられます。改善の予定は通常の業務の流れで扱い、版の差し替えには承認が要ります。

## 様子を見る

```powershell
python C:\tools\agent-dots\dot.py status --dir C:\work\dots\estimate
```

やったこと・捨てた提案・起きた理由は `journal.jsonl` に 1 行ずつ残ります。

| ファイル | 中身 |
|---|---|
| `goal.md` | 目標と完了の条件（人が書く） |
| `policy.json` | やってよいこと・起きる回数・完了の検査 |
| `state.json` | いまの状態・承認待ち・質問（スクリプトだけが書く。手で直さない） |
| `inbox/` | 届いた返事 |
| `outbox/` | エージェントの提案（片付けると `.dot/outbox-done/` へ移る） |
| `notes/` | エージェントの自分へのメモ |
| `journal.jsonl` | 記録 |
| `.dot/hook.jsonl` | フックが通したツール・止めたツール |
| `.github/hooks/agent-dots.json` | フックの設定（`dot.py hooks` が置く） |

## 気をつけること

- **フックが止めるのは、Copilot が使うツールだけです。** 許したコマンド（`tools.shell_allow`・`commands`）の中で何が
  起きるかまでは見ません。目標のフォルダに触られて困るものを置かず、ハブの issue へ書けるトークンをアプリから
  見える場所に置かないでください。
- 業務係が回すステートマシンの中では、承認したスキルのスクリプトが外のシステムに書き込みます。登録の承認では、
  外へ書くスキルの一覧をよく見てください。
- ツールの名前と引数の形は Copilot の版で変わることがあります。知らないツールは止めるので、何かが動かなくなったら
  `.dot/hook.jsonl` を見て `tools.allow` に足してください。
- 自動実行は、PC が起きていてアプリが開いているときだけ動きます。
- 完了の条件を検査にできるなら `done_check` を書きます。書かなければ、終わったかどうかは人が決めます。
- `commands` に登録するのは、入力を受けても困らないコマンドだけにします。目標は `input` の中身を自由に書けます。
- 承認されたコマンドが動いている途中で止まった（PC が落ちたなど）ときは、やり直さずに「途中で止まった」と書きます。
  二重に動くと困るためです。結果は自分で確かめてください。

## テスト

```bash
python -m unittest discover -s tools/agent-dots/test
```

アプリ役をテストが演じます。アプリ本体・GitLab・Dagu は呼びません。
