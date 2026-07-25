# 実機 canary 実施ランブック（1 週間）

> 参照計画: [`docs/plans/2026-07-24-single-resident-controller-implementation-plan.md`](../plans/2026-07-24-single-resident-controller-implementation-plan.md) W3-3。
> 参照設計: [`docs/plans/2026-07-24-single-resident-controller-design.md`](../plans/2026-07-24-single-resident-controller-design.md) §6（障害と回復）・§7（Windows/WSL 配置）。
> セットアップ手順: [`single-resident-setup.md`](single-resident-setup.md)。**canary 中はこのガイドだけを見る。**

1 週間、実機 3 台で日常運用しながら §6 の回復表を 1 行ずつ踏む。目的は 2 つ:

1. **回復表の各行が実機で本当に自動回復するか**を確かめる（人の出番が表示確認だけで済むか）。
2. **セットアップガイドの受入試験**。ガイドに書いていない操作が必要になったら、それは全て
   ガイドの欠陥として §5 に記録し、ガイドへ反映する。「知っていたから通れた」は失敗と数える。

---

## 1. 台数と役割

| 役割 | 台数 | 構成 | 停止時刻（availability） |
|---|---|---|---|
| フル A | 1 | Windows + WSL・systemd 常駐（4a） | `daily_stop: "23:30"` |
| フル B | 1 | Windows + WSL・Windows 起動ループ（4b） | `daily_stop: "01:30"` |
| ワーカー | 1 | POSIX 機（macOS / Linux）・`projects` 空 | 宣言しない（常時稼働） |

**停止時刻をずらす**のが要点。同時に落ちると「生存 PC が補完する」（§6 の計画停止行）を
確かめられない。フル A と B で常駐化方式を分けるのも意図的で、4a / 4b の両方が
「PC 起動時に上がる・死んだら上げ直される」を満たすかを 1 回の canary で見る。

## 2. 開始前

```bash
# 3 台すべてで（ガイド §1 の手順そのまま）
git clone <このリポジトリ> && cd <クローン先>
bash tools/install.sh
```

開始条件を 3 台で確認し、記録する:

```bash
agent-project doctor                  # 構成の検査。指摘が 0 になるまで開始しない
agent-project status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); \
  print("node:", d["node"], "contract:", d["contract_version"], \
        "children:", [(c["name"], c["alive"]) for c in d["children"]])'
```

| 台 | node | contract_version | children | doctor 指摘 | 記録 |
|---|---|---|---|---|---|
| フル A | | | | | |
| フル B | | | | | |
| ワーカー | | | | | |

**この時点でガイド外の操作をしたら §5 に書く**（doctor の指摘を消すために何をしたか、
それはガイドに書いてあったか）。

---

## 3. チェックリスト（各 1 回以上）

各項目は **再現操作 → 確認コマンド → 期待値 → 記録** の 4 段。期待値と違ったら
「実測」欄に起きたことをそのまま書く（推測は書かない）。

### C1. controller の引継ぎ

対応: §6「PC の計画停止」「PC の突然死」。controller lease を持つ台を止めて、別の台が
制御面を引き継ぐか。

- **再現**: controller を持っている台（下の確認コマンドで判る）で `agent-project serve` を
  Ctrl-C で止める（graceful）。
- **確認**: 別の台で、状態リポジトリの `status/` と controller の名義を見る。

  ```bash
  agent-project status --json | python3 -c 'import json,sys; \
    print(json.load(sys.stdin)["sync_health"])'
  # 引き継ぎ後、止めた台の名義が controller から外れ、別の台に移っていること
  ```

- **期待**: lease 期限（既定 120s）以内に別の台が controller になる。**二重に controller が
  立たない**。止めた台のタスクは他の台が拾える状態に戻る（claim 解放）。
- **記録**: 引継ぎに要した秒数 ____ / 二重 controller の有無 ____ / 実測 ____

### C2. 全台停止からの復帰

対応: §6「全 PC 停止」。

- **再現**: 3 台すべての常駐体を止める。止めている間に、いずれか 1 台でタスクを 1 件投函する
  （`agent-project enqueue` 等・ローカル滞留させる）。その後 1 台だけ起動する。
- **確認**:

  ```bash
  agent-project status              # 起動した台が単独で回り始めるか
  # 状態リポジトリに滞留分が push されているか（他の台を上げる前に）
  ```

- **期待**: 最初に復帰した台がローカル滞留を push し、消化を再開する。**滞留が消えない**
  （状態欠損 0）。
- **記録**: 滞留件数 投函 ____ / 復帰後 ____ / 実測 ____

### C3. 予定 drain（毎晩の計画停止）

対応: §6「PC の計画停止」。ガイド §2 の 3 段（drain 開始 → daily_stop → grace 満了で pause）が
実機で順に起きるか。**毎晩起きるので観測は 1 晩待てばよい**（時刻を前倒しして試してもよい）。

- **再現**: フル A の `daily_stop` を現在時刻 +5 分に一時変更し、`drain_before_sec: 120`・
  `shutdown_grace_sec: 60` にして待つ。
- **確認**: 3 つの時点で状態を撮る。

  ```bash
  # (a) drain 開始後（daily_stop の 2 分前）: 走っているタスクは続行・新規 claim は止まる
  agent-project status
  # (b) daily_stop 直後: まだ子は生きている（grace 中）
  agent-project status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); \
    print([(c["name"], c["alive"], c["paused"], c["deaths"]) for c in d["children"]])'
  # (c) grace 満了後: paused=true・alive=false・deaths は増えていない
  ```

- **期待**: (a) 走っているタスクが**途中で殺されない** / (b) grace 中はまだ生きている /
  (c) `paused=true` かつ **`deaths` が増えない**（計画停止は失敗として数えない）。
  時間帯が戻ると人の操作なしで `paused=false` に戻り、子が上がる。
- **記録**: (a) ____ / (b) ____ / (c) deaths 前 ____ → 後 ____ / 翌朝の自動復帰 ____

### C4. PC の突然死と fencing 拒否

対応: §6「PC の突然死」。復帰した台の**古い結果が拒否される**ことがこの項目の核心。

- **再現**: フル B でタスクを 1 件実行中にする。実行中に **VM ごと強制終了**する
  （Windows 側で `wsl --terminate <distro>`。graceful 停止ではないことが重要）。
- **確認**: 他の台で lease 失効後の回収を見る。その後フル B を起動し、死ぬ直前の run が
  結果を書き込もうとして拒否されるかを見る。

  ```bash
  # 他の台: claim が回収され、別の台が同じタスクを拾えること
  agent-project status
  # 復帰したフル B: 古い claim トークンでの settle が拒否されていること（journal に残る）
  grep -n "fencing\|stale\|拒否" <プロジェクトルート>/journal.md | tail -5
  ```

- **期待**: lease 失効で claim が回収され、**同じタスクが 2 台で同時に走らない**（二重実行 0）。
  復帰した台の古いトークンでの settle は拒否される（stale done 0）。
- **記録**: 回収に要した秒数 ____ / 二重実行の有無 ____ / fencing 拒否の journal 行 ____

### C5. self-watchdog の発火

対応: §6「常駐体のハング」。3 段構え（`Restart=always` / 内蔵 self-watchdog / `WatchdogSec`）の
うち内蔵 watchdog を実機で踏む。

- **再現**: フル A（systemd 常駐）で、常駐体プロセスに `SIGSTOP` を送って周期処理を止める。

  ```bash
  systemctl --user show -p MainPID agent-project.service   # PID を得る
  kill -STOP <pid>
  ```

- **確認**:

  ```bash
  journalctl --user -u agent-project.service -n 40 --no-pager
  systemctl --user show -p NRestarts agent-project.service
  ```

- **期待**: `WatchdogSec=90` を超えたところで systemd が殺して上げ直す（`NRestarts` が増える。
  `SIGSTOP` では内蔵 watchdog も止まるので、この操作が踏むのは 3 段目）。**内蔵 watchdog を
  踏むには**代わりに tick を長時間ブロックさせる（例: gc tick の対象プロジェクトを巨大にする）。
  どちらの経路で復帰したかを記録する。
- **記録**: 踏んだ段（2 / 3）____ / 復帰までの秒数 ____ / NRestarts 前 ____ → 後 ____

### C6. 子プロジェクトの隔離

対応: §6「子が連続クラッシュ」。**人の出番が「status の隔離表示を見て原因修正」だけで
済むか**を確かめる。

- **再現**: フル A の登録プロジェクトのうち 1 件の設定を壊す（`agent-project.yaml` を不正な
  YAML にする）。他のプロジェクトが動き続けることも同時に見る。
- **確認**:

  ```bash
  agent-project status
  agent-project status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); \
    print([(c["name"], c["quarantined"], c["paused"], c["deaths"]) for c in d["children"]])'
  ```

- **期待**: 指数バックオフの後に `quarantined=true`。**他のプロジェクトは止まらない**。
  表示は「休止中（時間で戻る）」と**別の文言**になっている（`paused` と混ざらない）。
  設定を直して常駐体を再起動すると回復する（隔離の自動解除は無い — それが仕様）。
- **記録**: 隔離までの死亡回数 ____ / 他プロジェクトへの影響 ____ / 表示文言 ____

### C7. スキル起動の併走（C14）

対応: 設計 §1.3 C14。常駐体が回している最中に人が単発実行を直接叩く。

- **再現**: ワーカー機で、常駐体が amigos の手番を回している最中に、同じロールの単発実行を
  手で叩く。

  ```bash
  agent-amigos run --mission <mid> --role <role> --once --agent-cli <cli>
  ```

- **確認**:

  ```bash
  ls ~/.agents/amigos/turns/            # 実行中の手番マーカー（PC 単位の上限の根拠）
  agent-project status --json | python3 -c 'import json,sys; \
    print(json.load(sys.stdin)["running_runs"])'
  ```

- **期待**: 同じ (mission, role) が**同時に 2 つ走らない**。PC 全体の同時実行数が
  `max_concurrent` を超えない（手で叩いた分も枠に数えられる）。手番が終わったら
  マーカーは消え、常駐体が次の手番を回せる（**進まなくならない**）。
- **記録**: 同時実行の最大数 ____ / 上限 ____ / 併走後に常駐体が手番を回せたか ____

### C8. 板委譲の往復（result ペイロード込み）

対応: 設計 §4.4。依頼 → 入札 → 実行 → 結果の書き戻し → 検収を 1 往復させる。

- **再現**: フル A から板へ 1 件委譲し、ワーカー機に拾わせる。

  ```bash
  agent-project board-offload --task <task-id>      # 依頼側
  ```

- **確認**: 板の公示ディレクトリと、依頼側のタスク状態。

  ```bash
  ls <board>/delegations/<delegation-id>/           # post / award / result
  python3 -c 'import json;print(json.load(open("<board>/delegations/<id>/result.json")))'
  # result_notes / discoveries / reject_guidance が載っているか
  agent-project status                              # 依頼側で offloaded を抜けたか
  ```

- **期待**: 結果が `result.json` に書き戻り、依頼側が読んで settle する。**読んだ後に公示が
  板から消える**（`delegations/<id>/` が無くなる）。読む前に消えることは無い。
  `result_notes` / `discoveries` / `reject_guidance` が空でない。
- **記録**: 往復に要した時間 ____ / 公示の削除タイミング ____ / ペイロードの有無 ____

### C9. Windows 起動ループ方式での VM 復帰

対応: §6「WSL VM 停止」・§7。事前検証 V1 / V3 / V4 の実機確認もここで兼ねる。

- **再現**: フル B（4b 方式）で Windows 側から VM を落とす（`wsl --terminate <distro>`）。
  その後、(a) 何もせず待つ / (b) Windows を再起動する の 2 通りを試す。
- **確認**:

  ```powershell
  schtasks /query /tn agent-project /v /fo LIST | Select-String "Last Result","Last Run Time"
  ```
  ```bash
  # WSL 側（復帰後）
  agent-project status
  ```

- **期待**: (b) ログオン時トリガーで VM ごと上がり、常駐体が起動する。(a) は
  タスクスケジューラの「失敗時の再起動」設定次第——**どちらだったかを事実として記録する**
  （設計は V1「UNC アクセスが VM 起動を維持するか」を未検証としている。ここが初回検証）。
- **記録**: (a) 自動復帰 ____ / (b) 自動復帰 ____ / `wsl.exe` の終了コード伝播（V3）____ /
  linger + systemd の自動起動（V4・フル A で確認）____ / UNC アクセスでの VM 起動（V1）____

### C10. 更新の往復（完了条件）

対応: §6「更新漏れの古いノード」。完了条件の「全ノードが git pull + install.sh で更新でき」。

- **再現**: リポジトリに 1 コミット入れて、3 台で更新する。1 台だけ**あえて更新しない**。

  ```bash
  git pull && bash tools/install.sh     # 更新する台
  agent-project update --now            # 自己更新経路を使う台（1 台はこちらで試す）
  ```

- **確認**:

  ```bash
  agent-project status --json | python3 -c 'import json,sys; \
    print(json.load(sys.stdin)["contract_version"])'
  ```

- **期待**: 3 台とも更新できる。`agent-project update --now` の自己更新が**成功する**
  （sparse-checkout に共通ライブラリが含まれていないと失敗する経路がある — W3-1 で直した）。
  契約バージョンを上げた場合、更新しなかった台は**入札しない**（誤動作せず不参加）。
- **記録**: `git pull + install.sh` ____ / `update --now` ____ / 旧バージョン台の挙動 ____

> **注**: 「旧バージョンノードが入札しない」の完全な確認には板の請負 tick が要るが、これは
> **未実装**（設計 §4.2・ガイド §6）。現状で確かめられるのは `contract_compatible` の判定と、
> 既存の板参加（amigos / flow 側）の範囲まで。ここは canary の限界として記録し、
> 請負 tick の実装時に再確認する。

---

## 4. 日次の観測（1 週間・毎日）

チェックリストとは別に、毎日 1 回 3 台で撮る。**完了条件の「二重実行 0・stale done 0・
状態欠損 0」はこの累積で判定する**。

```bash
agent-project status --json > canary-$(date +%F)-$(hostname).json
```

| 日 | 台 | 二重実行 | stale done | 状態欠損 | recent_errors | 備考 |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |

判定の根拠:

- **二重実行**: 同じタスク id / (mission, role) が 2 台または 2 プロセスで同時に走った形跡
  （journal の claim 行・`running_runs`・板の award）。
- **stale done**: 死んだ台の古いトークンで settle された done（fencing をすり抜けたもの）。
- **状態欠損**: 投函したのに消えたタスク・needs・成果物。

## 5. ガイドの欠陥記録（受入試験の本体）

**ガイドに書いていない操作をしたら必ず 1 行足す。** ここが空のまま canary が終わったら、
それはガイドが完成した証拠になる。逆にここが埋まる分だけガイドを直す。

| # | 何をしようとしたか | ガイドの記述 | 実際に必要だった操作 | ガイドへの反映 |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |

記録の観点:

- コマンドが違った / オプションが足りなかった
- 前提（git 認証・PATH・python の版・WSL の配置）に書いていない条件があった
- 表示の意味が分からず、コードを読んで初めて理解した（**利用者向け表示の欠陥**）
- 内部名（node / sync / resident）がそのまま出ていて意味が伝わらなかった

## 6. 終了判定

すべて満たしたら P3 完了。1 つでも欠けたら、欠けた項目を実装計画へ差し戻す。

- [ ] C1〜C10 を各 1 回以上実施し、期待値どおり（または差分を実測として記録）
- [ ] 日次観測 7 日分で 二重実行 0 / stale done 0 / 状態欠損 0
- [ ] §5 の欠陥記録がすべてガイドへ反映済み（`single-resident-setup.md` を更新）
- [ ] 3 台とも `git pull + install.sh` で更新でき、自己更新（`update --now`）も成功
- [ ] `agent-project doctor` の指摘が 3 台で 0
