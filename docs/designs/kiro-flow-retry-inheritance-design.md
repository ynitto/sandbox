# kiro-flow — リトライ時の run データ引き継ぎ・破棄設計

> 作成日: 2026-07-05
> 対象ブランチ: `claude/kiro-flow-req-run-difference-hxpe4a`
> 関連ファイル: `tools/kiro-flow/kiro-flow.py`,
> `tools/kiro-flow/tests/test_kiro_flow.py`,
> `tools/agent-project/agent-project.py`,
> `tools/agent-project/agent-project.yaml.example`

---

## 1. 背景と問題

kiro-project は 1 タスクの各試行を kiro-flow の run として投入する。run-id は決定的で
`req-<backlogハッシュ>-<task.id>-r<retries>`（`_submit_req_id`）。verify=NG や act の
失敗ごとに `task.retries` が +1 され、**次の試行は別 run-id（`…-r1`, `…-r2` …）で新規に投入**
される（`_settle_failure`）。

このとき従来は、

1. **先行 run（`…-r0`）は共有バスに残り続ける**（failed のまま滞留し、gc されるまで
   viewer にも並ぶ）。
2. **新しい run（`…-r1`）は完全にゼロから**開始する。先行 run が途中まで確定させていた
   ノード結果（`results/<node>.json`）・計画（`graph.json`）・中間成果物（`artifacts/`）・
   作業ブランチ `kf/<run-id>` の commit を**すべて捨てる**。

とくに **gitlab executor** のような長時間委譲では、run が部分的に進んだところで
act_timeout に達して失敗扱い→リトライ、という流れが起きやすく、「毎回ゼロからやり直し」は
トークン・時間・（イシュー再起票による）人手を空費する。

> 注: act_timeout 由来の空リトライそのものは、`act_timeout=0`（無制限待ち）で根治する
> （別変更・§7）。本設計は「それでもリトライが起きる場合（真の verify=NG／有限 act_timeout／
> orchestrator クラッシュで再開上限超過など）に、捨てられる先行 run のデータを再利用し、
> 滞留する先行 run を掃除する」ための仕組み。

## 2. ゴール / 非ゴール

**ゴール**

- リトライ run 作成時に、先行（タイムアウト/失敗）run から**再利用可能なものを引き継ぐ**。
- 引き継ぎ後、**先行 run を安全に削除**する（滞留・二重表示・inbox 再 claim を防ぐ）。
- 上記を **kiro-flow 側の 1 プリミティブ**に閉じ込め、呼び出し側（kiro-project）は
  「直前試行の run-id を渡すだけ」にする（安全判断は全部 kiro-flow が持つ）。

**非ゴール**

- 分散した複数 worker が並行 push する `kf/<run-id>` の**マージ戦略の変更**（従来どおり
  rebase リトライで統合）。
- local 都度起動（`kiro-flow run` を run-id 無しで叩く）経路の引き継ぎ。ここは run-id が
  毎回ランダムで試行間の連続性が無く、gitlab 委譲の対象でもないため対象外。

## 3. 用語: run ディレクトリの構成（引き継ぎ対象の棚卸し）

`<bus>/runs/<run-id>/` の中身と、リトライ引き継ぎでの扱い:

| パス | 内容 | 引き継ぎ |
|---|---|---|
| `graph.json` | strategy＋nodes（計画） | **引き継ぐ**（再計画を省く。既存グラフがあれば orchestrate は resume 動作） |
| `tasks/<id>.json` | 各ノードの仕様（goal/deps/kind） | **引き継ぐ** |
| `results/<id>.json` | ノード結果（status/output/data/artifacts/delivery） | **done のみ引き継ぐ**（failed はやり直させる） |
| `artifacts/<id>/` | ノード別の中間成果物（node-id で決定的にアドレス） | **引き継ぐ**（run-id 非依存でそのまま有効） |
| `meta.json` | request/workspace/references/status＋lease/resume 簿記 | **request/workspace/references のみ引き継ぐ**。status は `planning` で作り直し、lease/resume 簿記は引き継がない |
| `claims/<id>/` | ノード claim（wall-clock リース） | **引き継がない**（リースは時刻依存で誤判定の元） |
| `events/<who>.jsonl` | 追記ログ | **引き継がない**（新 run のログは新規） |
| `final.json` | 最終集約 | 引き継がない（新 run が作り直す） |

**run-id にスコープされる唯一の外部リソース = 作業ブランチ `kf/<run-id>`**
（`run_branch_name`）。ここが引き継ぎの肝（§4.2）。

## 4. 設計

### 4.1 プリミティブ `Bus.inherit_from(old_run_id)`

新しい run の `Bus` から呼ぶ。「引き継いでから掃除する」を 1 つに閉じ込める。

```
inherit_from(old_run_id, orphan_grace=0.0):
  1. old_run_id が自分自身 → no-op
  2. old の meta が無い（既に gc 済み等）→ no-op
  3. 安全条件: old が終端（done/failed）でも孤児（生存リース切れ）でもない
     ＝実行中でリース有効 → no-op（走っている run を壊さない）
  4. old が「完全に done」（全ノード done）→ 状態は引き継がず（seed しない）
     ＝同一出力で即 done→再び NG の無限ループを避ける
  5. それ以外（部分的に done で終端/孤児）→ _seed_from(old) で
     graph/tasks/artifacts/done結果/meta を新 run へコピー
  6. 最後に remove_run(old_run_id) で先行 run を掃除
     （runs/<old>/ と inbox/<old>.json と inbox/claims/<old>/ をまとめて削除）
```

- **安全条件（3）**が「走っている run を消さない」保証。呼び出し側が誤って生存中の run-id を
  渡しても no-op になる。
- **完全 done スキップ（4）**が verify=NG リトライの正しさを担保する。verify=NG は「run は
  done だが検収 NG」なので、done 結果を引き継ぐと新 run が即 done→また NG の無限ループになる。
  完全 done の先行 run は**状態を引き継がず掃除だけ**行い、新 run は feedback 付きで新規に
  やり直す（＝従来の verify=NG リトライの意味論を保つ）。

### 4.2 作業ブランチのチェーン（確定済み commit を失わない）

kiro-flow は 1 run の全ノードを**同じ作業ブランチ `kf/<run-id>` に push** して統合する。
リトライで別 run-id にすると新ブランチ `kf/<new>` になり、素朴にやり直すと**先行 run で
done だったノードの commit が新ブランチから消える**。

そこで `_seed_from` は、workspace 付き run では **新 run の workspace spec の `base` を
旧ブランチ `kf/<old>` に差す**。ワークスペースの clone は `base` のブランチから作業ブランチを
分岐するため、`kf/<new>` は `kf/<old>`（＝done ノードの commit を含む）から派生し、
**確定済みの作業を土台に、未完ノードだけを積み増す**。

- 旧ブランチ `kf/<old>` は**削除しない**。`remove_run` が消すのは状態リポジトリ側の
  `runs/<old>/` だけで、成果物リポジトリ上のブランチ `kf/<old>` は別リポジトリにあり残る。
  よって派生元として有効。
- 旧ブランチが存在しない（先行 run が push まで到達しなかった／読み取り専用）場合は、
  clone 側が既定ブランチへフォールバックするので安全。

### 4.3 配線（どこから run-id が流れるか）

```
kiro-project _act_submit
  └ retries>0 のとき prev = _prev_req_id(task,cfg)  (= …-r<retries-1>)
     kiro-flow submit --run-id …-r<retries> --inherit-from <prev>
        └ submit_request が inbox/<new>.json に inherit_from を記録
           └ daemon が accept → _spawn_orchestrator が
              orchestrate … --inherit-from <prev> を起動
                 └ cmd_orchestrate 冒頭（ensure_run より前）で
                    bus.inherit_from(<prev>)  ← ここで引き継ぎ＆掃除
```

引き継ぎを **`ensure_run` より前** に置くのが重要:

- 早すぎ（submit 時点で新 run dir を作る）ると `run_exists(new)` が真になり、daemon が
  「もう run がある」と判断して orchestrator を起動しない（＝走らない）。だから submit では
  inbox に印を書くだけにして、**実際の seed は orchestrate が run dir を作る瞬間**に行う。
- `inherit_from` が meta を seed した後は、`ensure_run` は「meta 有り」を見て上書きしない。
  `graph.json` があるので orchestrate は**再計画せず resume 動作**に入り、done ノードを
  スキップして未完だけ回す。

### 4.4 呼び出し側の責務は最小

kiro-project は「直前試行の run-id（`_prev_req_id`＝retries-1・同 rev）」を
`--inherit-from` で渡すだけ。done/failed/実行中/未存在の判断・seed 可否・削除可否は
**すべて kiro-flow の `inherit_from` が持つ**ので、呼び出し側が誤っても安全側に倒れる。

## 5. 安全性・冪等性

- **走っている run を壊さない**: 終端/孤児のときだけ触る（§4.1-3）。
- **無限ループを作らない**: 完全 done は状態を引き継がない（§4.1-4）。
- **二重実行を作らない**: `remove_run` は inbox 要求と claim も消すため、gc 後の
  再 claim（＝完了 run の再実行）と同じ事故を防ぐ既存不変条件をそのまま利用する。
- **冪等**: 既に seed 済み（新 run に meta 有り）なら seed をやり直さない。再開途中に
  orchestrate が再起動されても、`read_json(meta)!=None` で seed をスキップする。

## 6. テスト（`InheritTests`）

- 部分引き継ぎ: done ノードだけコピー・failed は非引き継ぎ・graph 引き継ぎ・先行 run 削除・
  `inherited_from` 記録。
- 完全 done: seed せず（新 run は白紙）掃除のみ。
- 実行中（生存リース）: 触らない。
- 未存在: no-op。
- workspace: 新 run の `base` が `kf/<old>` にチェーンされる。

kiro-project 側（`TestActTimeoutZeroAndInherit`）: `--inherit-from` はリトライ（retries>0）
のときだけ付き、初回は付かない。値は retries-1 世代の run-id。

## 7. 関連: act_timeout=0（無制限待ち）

本設計と対になる別変更として、kiro-project の `act_timeout=0` を「タイムアウト無効＝完了まで
待つ」とした（`_act_submit` / `_act_run` / `_claim_ttl`）。gitlab 委譲のように人のレビュー往復で
数日かかる run を待ち切れずに空リトライするのを根治する。設定例は
`kiro-project.yaml.example` / `kiro-project.state-git.yaml.example` の gitLab 委譲欄参照。

- `act_timeout=0` で**リトライ自体が減る**（＝本引き継ぎの出番も減る）。
- それでも起きるリトライ（真の verify=NG 等）で、本引き継ぎが「捨てられるデータの再利用＋
  先行 run の掃除」を担う。両者は補完関係。
