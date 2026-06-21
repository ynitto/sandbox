# kiro-autonomous — タスク単位の自律レベルと実績連動の自動昇格（設計メモ）

- 日付: 2026-06-21
- 位置づけ: [運用・外部操作レイヤ設計書](2026-06-19-kiro-autonomous-ops-design.md) §8.11（`--level` の段階導入）の拡張。
- 状態: **設計確定（実装前）**。本書合意後に実装に着手する。

## 0. 背景と狙い

`--level`（report / assisted / unattended）は **run 全体のダイヤル**で、`report` だと消化ループ自体に入らない
（`kiro-autonomous.py` の run_loop 冒頭・`cfg.level == "report"`）。一方、**実運用では自律度はタスク（backlog）毎に違う**:
決済コードは人の承認を要し、typo 修正は無人で良い。現状は「特定タスクを**締める**」方向（`- review: human` /
policy `gate:` / `protect:`）はあるが、

1. backlog の中の **1 件だけ `report`（自動実行せず塩漬け）** にできない、
2. グローバル assisted の中で **雑魚タスクだけ unattended に緩める** 手段がない（締める一方通行）、

という隙間がある。本設計はこれを埋める **(A) タスク単位の `- level:` 上書き** と、信頼を実績で明け渡す
**(B) 実績連動の自動昇格（opt-in）** を入れる。

> 不変条件: 「done は verify=PASS のみが根拠」「必ず有限停止」「`protect`/`gate`/`regression` は自動緩和しない」
> は本設計でも維持する。本設計が動かすのは **act/done の権限の出し入れ**だけ。

---

## A. タスク単位の `- level:`（手動の上書き）

タスク行に `- level: report|assisted|unattended` を許可。**明示指定はそのタスクをピン留め**し、Part B の自動昇格の
対象外にする（手動の意思を機械が覆さない）。

### A-1. 実効 level の決定順（上が優先）
1. タスクの `- level:`（明示・ピン）
2. 自動昇格が算出した track の level（B、`--auto-level` 有効時のみ）
3. グローバル `--level`
4. 上記の上に `protect` / `gate` / `review: human` / `regression` で **締める方向のみ**反映（緩まない）

「緩めは A-1/2/3 のどれか、締めは 4 が常に上乗せ」。**安全網は level に依らず常時有効**。

### A-2. 各 level のタスク単位の意味
| level | act | done | ループ上の扱い |
|------|-----|------|----------------|
| `report` | しない | — | 実行せず「計画」に載せ `needs` に塩漬け。未充足 `after:` 同様に **actionable から除外**するためループは `drained` で収束する |
| `assisted` | する | 人が `approve` | `review: human` と同一経路（review→approve で done／feedback で差し戻し） |
| `unattended` | する | 自動 | 既存ゲートを通れば自動 done |

`report` タスクは「解禁待ち」。人が `- level:` を外す/上げる、または `approve` するまで実行されない。
全 ready が `report` なら即 `drained`（計画一覧を提示して停止）。

---

## B. 実績連動の自動昇格（opt-in `--auto-level`）

同種タスクを人が承認し続け **手戻りが少なければ** その種別の level を自動で 1 段ずつ上げ、**手戻りが出たら下げる**。
信頼は得るだけでなく失う。既定 off。

### B-1. 「同種」= track ラベル（明示・予測可能）
- タスクに `- track: <name>`（例 `docs-typo` / `dep-bump`）を付けた群を 1 単位とする。
- **タイトル類似(Jaccard)は採らない**：曖昧マッチで高リスク案件が緩むのを避ける。track は明示 opt-in のみ
  （既存 `learn` の Jaccard とは別軸）。track 無しタスクは自動昇格の対象外（A の明示/グローバルで動く）。

### B-2. 「手戻り(rework)」の定義（run-log の終端イベントから機械的に算出）
- **clean done**: `approve` 差し戻し無し・regression 無し・noprogress 無しで done。
- **手戻り**: ① review からの**差し戻し**（feedback 付き ready 復帰）② 回帰検知 ③ 偽 done(noprogress) ④ done 後の revert。
  - `max_retries` 内で自己回復した NG retry は**手戻りに数えない**（ループが処理済み＝人の手は要っていない）。
- `rework_rate = 手戻り件数 / 直近 window 件の完了`（window は **直近 N 件**、既定 `level_window = 10`）。

### B-3. 昇格 / 降格ラダー（track 毎・状態を永続化）
- 出発点(floor) = グローバル `--level`（track に `floor:` を持たせれば上書き可。将来拡張）。
- **昇格**: 現 level で **連続 clean ≥ `level_promote_after`（既定 5）** かつ window の
  `rework_rate ≤ level_rework_max`（既定 0）→ 1 段上げ（`report→assisted→unattended`）、**ceiling まで**。
- **降格**: 現 level で手戻り 1 件 → **1 段下げ**＋clean streak リセット＋`decisions/` に記録。
  **降格 2 回で `assisted` にピンし自動管理を停止**（以後は人の管理下＝`needs` へ）。
- すべての遷移を `decisions/` に**根拠（streak・rework_rate・直近イベント）付きで記録**。
  「なぜこの track が unattended になったか」が後から監査できる。

### B-4. 上限(ceiling)と安全
- ceiling = `auto_level_max`（**既定 `assisted`**）。すなわち**自動では assisted までしか上がらない**。
  完全無人化（`unattended` への自動到達）は **`auto_level_max: unattended` を人が明示**したときのみ解禁。
- `protect` / `gate` / `regression` / `require_progress` は **自動緩和の対象外**。unattended に上がっても番人は残る。
- **コールドスタート**: 実績ゼロの新 track は floor 据え置き、飛び級しない。

### B-5. 状態の置き場（既存の流儀を流用）
- `<root>/autonomy/<track>.json` … `{ level, clean_streak, window:[直近 N 件の終端], demotions, updated }`。
  粒度・永続の作法は `promote_threshold` / `learn` ストアに合わせる。**run-log を一次ソースに増分更新**。

---

## C. 追加する設定キー / タスク書式

| 種別 | キー | 既定 | 意味 |
|------|------|------|------|
| タスク | `- level: …` | （無＝グローバル/自動） | A の上書き（ピン） |
| タスク | `- track: <name>` | （無＝非適応） | B の同種グルーピング |
| config/CLI | `auto_level` / `--auto-level` | `false` | B を有効化（opt-in） |
| config/CLI | `auto_level_max` | `assisted` | 自動昇格の ceiling（`unattended` で完全自動到達を解禁） |
| config | `level_promote_after` | `5` | 昇格に要する連続 clean 数 |
| config | `level_window` | `10` | 手戻り率の評価窓（直近 N 件） |
| config | `level_rework_max` | `0.0` | 昇格を許す最大 rework_rate |

CLI の真偽は既存どおり `--auto-level/--no-auto-level` の三値で config を上書き。

---

## D. 維持する不変条件 / テスト面

**不変条件**: done は verify=PASS のみ／必ず有限停止（report は actionable 除外で `drained`）／`protect`・`gate`・
`regression` は自動緩和しない／明示 `- level:` は機械が覆さない。

**テスト想定**:
- 解決順（明示 > 自動 > グローバル、締めは常時上乗せ）。
- `report` タスクの actionable 除外と `drained` 収束（全 report でも収束）。
- 連続 clean で昇格 / 手戻りで 1 段降格 / 降格 2 回で assisted ピン＋自動停止。
- ceiling 既定 assisted を超えない／`auto_level_max: unattended` でのみ unattended へ。
- `protect` は昇格後も緩まない。
- コールドスタート据え置き／track 無しは非適応。
- 遷移の `decisions/` 監査記録。
- 既定（`--auto-level` off・`- level:`/`- track:` 無し）で**従来挙動が完全不変**。

---

## E. 実装範囲の見取り（合意後）
1. `parse_task`: `- level:` / `- track:` を解釈。
2. 実効 level 解決関数 `resolve_level(task, cfg, autonomy_store)` を新設し、run_loop の `assisted = cfg.level == "assisted"`
   と report 早期 return を**タスク単位**に置き換え（`report` は選択時に actionable から除外）。
3. 自動昇格ストア（`autonomy/<track>.json`）の読み書きと、終端イベント時の clean/手戻り更新・昇降格・decision 記録。
4. Config/CLI/CONFIG_DEFAULTS にキー追加。
5. テスト追加・GUIDE.md / README / 設計書(§8.11) 追記。

> 既定値・しきい値（5 / 10 / 0 / ceiling=assisted）は初期値。運用後 `stats`/`runlog` を見て調整余地あり。
