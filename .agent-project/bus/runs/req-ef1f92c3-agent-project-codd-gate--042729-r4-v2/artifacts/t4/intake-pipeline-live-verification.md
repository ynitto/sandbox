# t4: intake結線 — 実データによるエンドツーエンド稼働確認と、完了条件grepが構造的に通らない理由の確定

**差別化の切り口**: t1（静的調査）・t2（検出層のライブプローブ）に対し、t4 は **intake 側
（`codd-gate tasks --debt` の出力→backlog 取り込み）を実データで最後まで通す**。モックではなく
この run の実環境で `codd-gate tasks --debt` を実行し、その stdout を `codd_gate_debt.parse_debt_output`
（`run_intake` が内部で使う本物の関数）に通してレコード単位の検証まで確認する。あわせて、
完了条件grepが `- verify:` の記載どおりには**構造的に成立し得ない**ことを、`decisions/` の
決定記録・origin/main の実体・`_task_verify_cwd` の実装の3点から確定させる。

## (a) 成果サマリー

**intake結線（ドリフト検出→タスク化）は実データで最後まで動作することを確認した。新規実装は不要。**
残るのはコードではなく、backlog task 自身の `- verify:` 文言が指すファイルパスの不一致であり、
これは t1 が状況証拠から推測した「バグ」ではなく **人（nitto）による明示的な revise（DR-0005）
の結果**であることを `decisions/` から確定した。そのため t4 の範囲でこれを無断で書き換えることは
せず、判断材料として報告する。

### intake パイプラインの実地確認
- `.agent/agent-project.yaml` の `intake_cmd`（`codd-gate tasks --debt --repos repos.json --repo-dir src=.`）を、
  `run_intake()`（`model.py:493`）が実際に使う cwd＝`cfg.workdir`（＝この control-plane root）と同じ条件で
  そのまま実行した。**exit=0、stdout は JSON 配列 20件**（`id`/`title`/`verify`/`paths`/`priority`/`expect`/`note`
  を持つ debt レコード）。
- その stdout を、`run_intake()` が実際に呼ぶ `codd_gate_debt.parse_debt_output`（同梱 sibling module。
  レコード単位検証パス）にそのまま通した。**20件すべてパース成功・エラー0件**。`to_spec()` の
  出力キーは `enqueue_task` が要求する spec 形式と一致。
- backlog の既存タスクID集合と突き合わせ、`run_intake()` の冪等スキップ判定（`sid in existing` なら
  再投入しない）をエミュレートした。現時点では20件とも未登録＝**そのまま流せば20件が新規 enqueue
  される状態**（＝実際にドリフト→タスク化が機能する状態にある）。
- 以上により「codd-gate tasks の出力を agent-project の backlog/intake へ取り込む結線」は、
  設定・コード・実データの3点すべてで動作を確認した。t4 の範囲でファイル変更は行っていない
  （t1方針どおり既存実装の追加確認に限定）。

### 完了条件grepが通らない理由の確定（t1の未解決事項への回答）
t1 は「DR-0005で `.agent/` prefix が誤って落ちたと見られる状況証拠」と推測に留めていたが、
`decisions/agent-project-codd-gate--042729.md` を直接確認し、次の事実列を確定した。

1. **DR-0002**（2026-07-15, actor: nitto, action: revise）: verify を
   `.agent/agent-project.yaml` に設定（正しいパス）。
2. **DR-0005**（2026-07-16, actor: nitto, action: revise, reason: 「要対応画面で検証コマンドを変更」）:
   verify を bare `agent-project.yaml` に**人が明示的に変更**。現在の backlog 記載・本タスクへ
   渡された完了条件はこのDR-0005の値そのもの。
3. `_task_verify_cwd`（`verify.py:108`）の実装により、`- workspace: src` を持つこのタスクの
   verify は **origin から都度シャロークローンした一時ディレクトリ**を cwd として実行される
   （既存の永続チェックアウトやこの control-plane とは無関係）。
4. `git show origin/main:.agent/agent-project.yaml` を確認したところ、`regression_cmd` は
   既に完了条件の正規表現に一致する値が push 済み（ローカル `/Users/nitto/Workspace/sandbox`
   にある未コミット差分とは無関係に、origin 側は既に整合）。
5. しかし bare `agent-project.yaml`（ディレクトリなし）は origin/main のどこにも存在せず、
   README（`tools/agent-project/README.md:693`）が明記する探索順も `--config` → `./.agent/` →
   `~/.agent/` のみで bare ルートは対象外。

**結論**: 現在の verify 文言（DR-0005 の bare パス）は、コード・設定をどう直しても
origin/main 上に存在しないファイルを参照しているため**構造的に一致しようがない**。
かつ本タスクの参照リポジトリ `https://github.com/ynitto/sandbox`（main）は読み取り専用で
push禁止のため、bare ファイルを新設して辻褄を合わせる対応も取れない（取るべきでもない —
README の探索順に無い shadow file を作ると将来の設定読み込みと乖離する）。

## (b) 検証内容と結果

| 検証項目 | 方法 | 結果 |
|---|---|---|
| intake_cmd の実地実行 | `codd-gate tasks --debt --repos repos.json --repo-dir src=.` を `cfg.workdir` 相当の cwd（control-plane root）で実行 | **exit=0**、JSON配列 **20件** |
| debt レコードのパース | `codd_gate_debt.parse_debt_output(stdout)`（`run_intake()` が実使用する関数そのもの） | **20件パース成功・エラー0件**、`to_spec()` のキーが enqueue spec 形式と一致 |
| 冪等スキップ判定のエミュレート | 出力 spec の `id` と `backlog/*.md` の stem 集合を突き合わせ | 20件とも未登録＝新規 enqueue 対象（重複投入なし） |
| regression_cmd/intake_cmd の wiring 判定関数 | `codd_gate_wiring.regression_wired`/`intake_wired` に `.agent/agent-project.yaml` の値を通す（t2で実施済み・再確認） | 両方 `True`（t2と同結果を再現） |
| 完了条件grepの成立可否 | `decisions/agent-project-codd-gate--042729.md` 読み取り、`_task_verify_cwd`（`verify.py:108`）実装確認、`git show origin/main:.agent/agent-project.yaml` / `git rev-parse HEAD origin/main` 確認 | DR-0005が人による明示的変更であること、origin/mainには bare `agent-project.yaml` が存在しないことを確定。**現状の verify 文言では exit=0 になり得ない** |
| ユニットテスト | `pytest tests/test_codd_gate_*.py`（sandbox/tools/agent-project） | 81 passed（t1/t2と同数を再現） |

## (c) 採用した前提・未解決事項・範囲外で見つけた問題

**採用した前提**
- t1 の方針決定（t3/t4は新規実装ではなく既存値・既存実装の整合確認に限定）を踏襲し、
  `.agent/agent-project.yaml` の内容・`tools/agent-project/*.py` のコードには一切変更を加えていない。
- 参照リポジトリ `https://github.com/ynitto/sandbox`（main）は指示どおり読み取り専用として扱い、
  実行（pytest・codd-gate CLI）のみ行いファイルへの書き込み・commit・push は行っていない。
- 「完了条件を満たすまで反復」の指示に対しては、満たす手段が (i) 人の意思決定（DR-0005）を
  無断で覆す、(ii) 読み取り専用の参照リポジトリへ shadow file を書き込む、の2つしかなく、
  どちらも本タスクの制約（人の判断を尊重する／read-only厳守／範囲外の問題は直さず報告する）に
  反するため、**grepを無理に通す操作はしない**という判断を採用した。これは t1・t2 が既に
  採っている方針との一貫性でもある。

**未解決事項（評価役・人の判断に委ねる）**
- DR-0005（bare パスへの変更）が意図的な仕様変更なのか、「要対応画面」操作時の入力ミスなのかは
  記録からは断定できない。もし意図的なら、bare `agent-project.yaml` を正規の置き場にする設計変更
  （README探索順の拡張含む）が別途必要になる。もし誤りなら、`.agent/agent-project.yaml` に戻す
  revise を人に依頼するのが最短。いずれにせよ**次の一手は人の判断**であり、t4/t6 のどちらであっても
  エージェント単独では確定できない。

**範囲外で見つけた問題（報告のみ）**
- t2 が報告した「参照専用リポジトリ側 `.agent/agent-project.yaml` の未コミット差分
  （`--repos .agent-project/repos.json` → `--repos repos.json`）」は、今回確認した時点でも
  ローカルの `/Users/nitto/Workspace/sandbox` チェックアウトに残存している（origin/main 自体は
  未変更）。verify が origin から都度クローンする以上この差分は verify 結果に影響しないが、
  誰の変更か不明なまま放置されている点は変わらず未解決。
- intake の実地実行により、現時点で backlog 未登録の codd-gate debt（壊れた参照リンク）が
  20件検出された。これは今回のタスク（結線の実装確認）の対象外の実データであり、
  実際に `run_intake()` を稼働させれば自動でタスク化される想定のため、ここでは enqueue を
  実行せず件数の報告のみに留めた。
