# agent-project / agent-flow task verify 統一設計

## 1. 先に結論

タスクの verify は、次の一つの契約へ統一する。

- **定義する層**: agent-project。利用者が書く受入基準と、任意の固定検証コマンドを唯一の正本にする。
- **実行する層**: agent-flow。変更を作った同じ worktree、同じ成果 revision で、正本から渡された検証計画をそのまま実行する。
- **確定する層**: agent-project。agent-flow が返した構造化 receipt の spec digest、成果 revision、実行結果を検算し、一致した PASS だけでタスクを次の gate または `done` 候補へ進める。

したがって「project と flow のどちらも verify コマンドを生成して実行する」は廃止する。一方だけを
単純に無効化するのでもない。**project が契約を所有し、flow が一度だけ実行し、project が receipt を
検算して状態を確定する**一つの縦の gate にする。

## 2. なぜ前案を採らないか

前案は「PASS 後の状態を書くエンジンが verify を実行する」として、agent-project からの run に
`--no-review` を付けた。しかし、状態所有と実行場所を同一視していた。

agent-project は backlog と `done` を所有するが、成果を作った一時 worktree、依存成果、実行中の
修正ループを直接所有しない。そこで verify を走らせると、成果 revision の取り違え、cwd / PATH /
依存差、失敗後に再び flow を起動する往復が発生する。逆に flow に自由な verify 生成まで任せると、
project の完了条件と似たコマンドをもう一本作り、二重実行と意味のずれが復活する。

問題は「二つの verify をどう呼び分けるか」ではなく、以下が分裂していることである。

1. 完了条件の定義
2. 実行可能なコマンドへの変換
3. 成果 revision 上での実行
4. PASS を状態遷移へ採用する判断

この四つを一つの所有者へ押し込めるのではなく、一つの改ざん検知可能な契約として直列化する。

## 3. 設計書・コンセプトから導く責務

既存コンセプトには「実行は agent-flow、制御と学習は agent-project」とある。agent-project 設計も
実行を agent-flow へ丸ごと委譲し、自身はルーティング、gate、記録に徹するとしている。さらに
agent-flow は worktree、commit、push、失敗したノードの作り直しを所有する。

この境界から、タスク verify の**実行**は agent-flow に置くのが自然である。verify 失敗を最短距離で
同じ run の修正へ戻せ、検証対象 revision と作業環境を一致させられるためである。ただし完了条件の
意味と採否まで flow に移すと project の gate が消える。よって定義と最終確定は agent-project に残す。

「決定的」とは、AIがもっともらしいコマンドを二度生成することではない。入力となる検証計画を固定し、
同一 digest の計画を対象 revision に一度実行し、その receipt の一致を機械的に検算できることである。

## 4. 統一 verify 契約

agent-project は run 投入前に `verification_plan` を確定する。自然文の受入基準をコマンドへ一発変換
してはならない。利用者が明示した固定コマンドはそのまま使い、自然文基準は証跡付き verifier 用の
criterion として構造化する。

```json
{
  "version": 1,
  "task_id": "T-123",
  "workspace": "app",
  "commands": [
    {"command": "npm test -- --runInBand", "source": "user"}
  ],
  "criteria": [
    {"id": "C1", "text": "検索結果が更新後も安定して表示される"}
  ],
  "policy": {"confirm": 1, "timeout_sec": 600},
  "digest": "sha256:..."
}
```

agent-flow はこの plan を planner の自由記述へ混ぜず、構造化入力として受け取る。planner が
`kind: verify` を別生成しても、それは run 内の候補比較・分岐評価に限り、`verification_plan` と同じ
コマンドを実行してタスク完了を主張できない。タスク成果が確定した直後に専用 runner が、同じ
plan digest と成果 revision に対する一つの検証セッションを実行する。セッション内では verifier が
criterion の確認方法を試行錯誤してよいが、criterion の変更・緩和と成果物の修正・commit はできない。
runner は次の receipt を返す。

```json
{
  "version": 1,
  "task_id": "T-123",
  "plan_digest": "sha256:...",
  "result_rev": "0123abcd...",
  "workspace": "app",
  "verdict": "pass",
  "commands": [{"command": "npm test -- --runInBand", "exit_code": 0}],
  "criteria": [
    {
      "id": "C1",
      "text": "...",
      "verdict": "pass",
      "evidence": [
        {"kind": "command", "command": "npm test", "exit_code": 0},
        {"kind": "file", "path": "src/search.js", "summary": "更新後も同じ順序で描画する"}
      ]
    }
  ],
  "started_at": "...",
  "finished_at": "..."
}
```

agent-project は少なくとも次を決定的に照合する。

1. `plan_digest` が投入した正本と一致する。
2. `result_rev` が採用しようとしている成果 revision と一致する。
3. 全 command が終了コード 0、全 criterion が証跡付き pass である。
4. receipt が無い、古い、壊れている、対象 revision が違う場合は PASS とみなさない。

回帰、パス保護、進捗検査も同じ成果 revision を対象にするが、タスク固有 verify と混ぜて再生成しない。
可能なら同じ verification plan の後段へ project policy 由来の固定 gate として加え、一回の runner 起動で
実行する。これにより同じテストスイートを E1 と regression が重ねて呼ぶ場合も、計画の正規化段で
コマンド digest を比較し、完全一致だけを一度に畳める。

### 4.1 利用者が入力するもの

通常の利用者が決めるのは、確認手段ではなく「何が満たされればよいか」である。agent-dashboard の
通常画面から `verify` という内部語とシェルコマンド入力を外し、次の二つに分ける。

| 画面 | 通常入力 | 保存時の意味 |
|---|---|---|
| プロジェクト作成・計画バージョン編集 | **達成条件**（自然文、1 行 1 基準） | `project_acceptance_criteria` |
| タスク追加・編集 | **受入基準**（自然文、1 行 1 基準） | `task_acceptance_criteria` |

タスクの「高度な設定」にだけ、任意の **固定検証コマンド** を置く。これは CI コマンド等を利用者が
既に知っている場合の fast path であり、空が既定である。LLM が入力時にコマンドを一発生成して
自動入力してはならない。`verify_template` も固定コマンドと同じ高度な設定へ移し、技術的な利用者が
決定的な定型検査を選ぶ場合だけ使う。

新規タスクの受入基準は通常、backlog-planner がタイトル、charter、repo 文脈から提案し、人は
計画レビューで基準を直す。曖昧語の lint は非ブロックで残すが、コマンド入力を解決策として強要しない。

### 4.2 保存形式と後方互換

新しい正規形では、裸の `acceptance` / `accept` / `verify` を内部契約に使わない。

```json
{
  "task_acceptance_criteria": [
    {"id": "C1", "text": "不正な行があっても正常な行は取り込まれる"},
    {"id": "C2", "text": "不正な行番号と理由を確認できる"}
  ],
  "verification_commands": [
    {"command": "pytest -q tests/importer", "source": "user"}
  ]
}
```

既存データは agent-project の境界で次のように正規化する。

- task の `acceptance` / `accept` → `task_acceptance_criteria`
- task の `verify` → `verification_commands`（`source: legacy`）
- charter の `acceptance` → `project_acceptance_criteria`
- `verify_template` → 展開後の `verification_commands`（`source: template`）

互換入力を読める期間も、agent-dashboard は新しい正規形だけを書く。移行後に同じ値を新旧両方へ
書く dual-write は行わない。正本が二つに戻るためである。

### 4.3 criterion の検証

固定コマンドと criterion は同じものではない。

- 固定コマンドは plan に書かれた文字列を変更せず実行する。
- criterion は verifier が成果 revision 上で、複数のコマンド、差分、ファイル、ログ、画面等を
  調べて判定する。
- verifier が発見した有効なコマンド列は次回の参考レシピに保存してよいが、固定コマンドへ自動昇格
  させない。
- verifier は成果物を変更しない。`fail` の修正は同じ agent-flow run の作業ループへ戻す。

criterion の verdict は `pass` / `fail` / `inconclusive` の三つとする。

| verdict | 意味 | 次の処理 |
|---|---|---|
| `pass` | 証跡付きで基準を満たす | 全項目の結果を待つ |
| `fail` | 証跡付きで基準を満たさない | 同じ flow の修正ループへ戻す |
| `inconclusive` | 現在の環境では確認不能 | 修正リトライを消費せず、別ノード検証または人へ回す |

固定コマンドが起動でき、終了コードが非 0 なら `fail` である。コマンド自体が存在しない、必要な
外部環境へ到達できない等は `inconclusive` とし、成果物の欠陥と混同しない。別ノードへ委譲しても
同じ plan digest と result revision の receipt を返させる。

### 4.4 agent-dashboard の結果表示

タスク詳細は「受入基準 → 現在の判定 → 証拠」の順に表示する。コマンド、exit code、plan digest、
revision は折りたたんだ「検証の詳細」に置く。通常利用者は、各基準について何を確認し、なぜその
判定になったかを読めればよい。

検証不能時に提供する操作は次に限る。

1. 別の実行環境で検証する。
2. 受入基準を修正する。
3. 高度な設定へ固定検証コマンドを追加する。
4. 成果を修正して再実行する。

証跡を作らずに task を `done` にする操作は置かない。人の検収が必要なタスクでも、検証 receipt と
人の approve は別の gate として直列に扱う。

### 4.5 charter acceptance

charter の達成条件にも task と同じ criterion / receipt プロトコルを使い、自然文からのコマンド一発
合成と安定キャッシュを廃止する。全対象 task の統合 revision を固定できる verifier が
`project_acceptance_criteria` を評価する。対象 revision を agent-flow が所有する場合は同じ
verification plan / receipt 契約で実行を委譲する。

未達 criterion から改善タスクを自動生成せず、基準と証跡を milestone に載せて `awaiting-plan` とする。
全 criterion が pass したらプロジェクト完了の人検収へ進む。この人検収は receipt を置き換えず、
プロジェクト完了を確定する最後の gate である。

## 5. どの層が verify を実行するかを決める一般則

今後の拡張では「状態を書く層」だけで決めない。次の順で決める。

1. **検証対象を所有する最小の層**を選ぶ。対象 revision、実行環境、依存成果を同時に固定できる層である。
2. **失敗を修正できるループと同じ層**で実行する。失敗のたびに層を往復しない。
3. 上位層は検証契約を定義し、receipt を検算して自分の状態遷移を確定する。
4. 下位層は上位の条件を再生成・緩和せず、渡された digest の計画を実行する。

| 対象 | verify 実行層 | 契約・状態の所有層 |
|---|---|---|
| agent-flow run が作る単一タスク成果 | agent-flow | agent-project |
| agent-flow 単独 run の内部的な候補比較・反復 | agent-flow | agent-flow |
| 複数タスクをまたぐ charter acceptance | 全対象 revision を固定できる agent-project verifier（必要なら board へ実行委譲） | agent-project |
| agent-amigos の単一 role 成果 | その role runner | agent-amigos |
| 複数 role を統合した mission acceptance | 統合成果を所有する agent-amigos coordinator | agent-amigos |

新しい実行エンジンを agent-project 配下へ追加する場合、そのエンジンが verification plan / receipt
プロトコルを実装する。project 側にエンジン別の verify 実装を増やしてはならない。対象をローカルで
固定できない外部検証は board へ委譲してもよいが、同じ digest と result revision を receipt で返す。

## 6. 移行順序

この決定は境界プロトコルの変更であり、`--no-review` を先に足すだけでは途中状態が危険になる。
次の順で移行する。

1. verification plan / receipt の schema と digest 正規化を定義する。
2. agent-flow に専用 runner と receipt 出力を追加する。
3. agent-project に旧 `acceptance` / `accept` / `verify` から正規形への読み取りアダプタと receipt 検算を追加する。
4. task criterion の verifier を agent-flow runner 内で実行し、`fail` を同じ修正ループへ、
   `inconclusive` を別ノード検証または人へ返す。
5. charter acceptance を同じ criterion / receipt 方式へ移し、自然文からの一発コマンド合成を削除する。
6. agent-dashboard の通常入力を「達成条件」「受入基準」へ変更し、固定コマンドと template を
   高度な設定へ移す。結果表示は基準×証跡を正とする。
7. この段階では旧 project verify を shadow 実行し、同じ task / revision に対する差異だけ記録する。
8. 実測で一致を確認後、project の旧コマンド実行と dashboard の旧形式書き込みを削除する。
9. planner prompt から完了コマンドの一発生成指示を除き、重複 command の telemetry をゼロにする。

移行完了条件は「同じ task / result revision / plan digest について command 実行が一回だけ」「receipt
不一致で done にならない」「verify fail が同じ flow 修正ループへ戻る」に加え、次の三つとする。

- 通常 UI がシェルコマンドの入力を要求しない。
- task と charter の自然文基準がどちらも criterion / receipt で検証される。
- 新規データに裸の `acceptance` / `accept` / `verify` が書かれない。

## 7. 今回コードを先行変更しない理由

前案の `--no-review` 追加だけを残すと、agent-flow 側の検証を止める一方で receipt が存在せず、結局
agent-project の旧 verify だけへ戻る。逆に project verify を先に消すと、flow の自由生成した判定を
根拠に done にしうる。本設計では schema と両側実装が揃うまで現行動作を維持し、shadow 期間を経て
切り替える。議論対象の責務境界を、部分実装で既成事実化しないためである。

## 8. 実装単位

1. schemas: verification plan / receipt と正規化後の task / charter criterion を定義する。
2. agent-project: 互換入力の正規化、plan digest、receipt 検算、状態遷移を実装する。
3. agent-flow: 専用 runner、verifier セッション、修正ループへの `fail` 返却を実装する。
4. agent-dashboard: 基準中心の入力、固定コマンドの高度な設定、基準×証跡表示へ変更する。
5. migration: shadow telemetry を観測し、旧実行と旧書き込みを順に削除する。

各単位は前段の schema を使い、エンジン固有の別契約を増やさない。

## 9. Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-07-31 |
| 決定者 | チーム |
| 採用案 | 自然文の基準を通常入力とし、固定検証コマンドを高度な設定に残す |
| 却下案 | 自然文基準だけに限定する案（確実な CI の fast path も失うため）、コマンド中心＋LLM 一発生成案（環境差と弱い検証を固定するため） |
| 主な理由 | 利用者は期待結果を定義し、確認方法は成果環境上の verifier が試行錯誤し、機械は receipt の整合だけを決定的に検算する責務分担が最も安定する |
| トレードオフ | criterion 検証ごとに verifier のコストがかかり、証跡の保存量が増える |
| 再評価条件 | verifier コストが支配的になる、基準×証跡でも誤判定率が許容できない、または固定 CI だけで大半の task / charter を検証できる実測が得られたとき |
