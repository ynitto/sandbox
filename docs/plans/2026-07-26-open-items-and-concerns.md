# 積み残し・懸念・不具合の総覧（2026-07-26）

- 状態: 棚卸し（07-24〜07-26 の計画・詳細設計の消化後に残っているものの単一リスト）
- 出典: [常駐一本化 実装計画 §7](2026-07-24-single-resident-controller-implementation-plan.md) /
  [agent シリーズ改良仕様 §4](2026-07-25-agent-improvement-spec.md) /
  各詳細設計（S1〜S9）の「積み残し」節 / 本棚卸しでの実装確認
- 反映先: 設計正典は `docs/designs/agent-*-design.md` に反映済み。本書は「まだやっていないこと・
  観測が要ること・直っていないこと」だけを持つ

**読み方**: §1 が次の一手（着手可能・他の前提になるもの）。§2 はその完了を待つ連鎖。
§3 は「必要が出たときに拾う」の一覧で、急ぎのものは無い。§4 は意図して残した割り切り
（やらないと決めたことの記録）。§5 は運用で観測すべき懸念。§6 は**この棚卸しで新たに
見つけたもの**で、決着した行には「決着（P0-x / P1-x / P2-x）」を付けてある。
**§7 が修正計画**——§1・§6 をどの順で消化するかを P0〜P3 の 4 段に落としてある。
全体を掴むには §1〜§2 と §6.1、動くなら §7 から。

**進み具合（2026-07-26 時点）**: **P0 / P1 / P2 / P3 は実装済み**で、§6.1 の不具合 7 件と
§6.2 の懸念はすべて決着した（未着手で残るのは `canceled` 識別子の改名だけで、これは
「触るファイルの修正時に限る」という方針そのもの）。**残る最優先は §1.1 の実機 canary（R1）**
で、そこから §2 の連鎖（R2b・検証委譲）へ進む。各段から出た積み残しは §3 の表と
§5 の観測点へ転記済み——**いずれも契機待ちで、いま着手すべきものは無い**。

---

## 1. 次の一手（着手可能・後続の前提）

### 1.1 実機 canary（R1）— 最優先。ここが詰まると §2 全部が動かない

フル 2 台（停止時刻をずらす）+ ワーカー 1 台（POSIX 機）で 1 週間。ランブックは
[`docs/guides/single-resident-canary.md`](../guides/single-resident-canary.md) に用意済みで、
残っているのは実施そのもの。完了条件は二重実行 0・stale done 0・状態欠損 0。

- 事前検証 V1（UNC アクセスの WSL 起動維持効果）・V3（Windows 起動ループ方式）・
  V4（systemd user unit + linger）はこの canary に内包される（単独では動かさない）
- 設計の障害回復表（常駐一本化設計 §6）のうち「常駐体のクラッシュ」「全 PC 停止」
  「WSL VM 停止」の 3 行は回復動作そのものが未検証で、canary が唯一の受け皿
- セットアップガイド（[`single-resident-setup.md`](../guides/single-resident-setup.md)）の
  受入試験を兼ねる: ガイド外の操作が要ったら全てガイドの欠陥として記録・反映する

### 1.2 R10 の grep 検査と CI（R4）— この repo には CI が無い

> **状態: 実施済み（2026-07-26・§7.4 P3-1）**。`.github/workflows/ci.yml`（4 パッケージ +
> dashboard + R10）と `tools/ci/check_user_docs.py` を新設した。以下は当時の記述。
> 検査規則の決着は「本文だけを見る」——コードブロック・インラインコード・リンク先・
> パス・ファイル名を落としてから内部名を探し、例外は行ごとの `r10-allow` 注記に理由を残す。

利用者向け文書に内部名（node / sync / resident）が現れないことの機械検査。CI 基盤自体が
存在しない（`.github/workflows` / `.gitlab-ci.yml` / Makefile いずれも無し・実装確認済み）ので、
検査スクリプトと CI の新規作成になる。同じ CI で 4 パッケージ（agent-project 1,063 /
agent-flow 571 / agent-amigos 176 / agentcore 127 — いずれも本棚卸しで全緑を実測）と
agent-dashboard の `npm test` も回すのが素直。

**検査仕様に決めが要る（今回の棚卸しで判明）**: 素朴な grep は成立しない。利用者向け文書には
`single-resident-setup.md` というガイドファイル名や `agent-node-command.schema.json` という
契約名へのリンクが正当に含まれ、これらは内部名を含む。R10 が隠すのは「製品名としての内部名」
であって契約の語彙ではない（常駐一本化設計 §3.1）ので、検査には「ファイルパス・スキーマ名・
コードブロックを除外して本文だけを見る」規則の設計が要る。

---

## 2. R1 / R2b 待ちの連鎖

| # | 内容 | 待ち先 | 出典 |
|---|---|---|---|
| R2b | **ノード直轄実行** — プロジェクト 0 個のワーカーノードが落札して `NodeWorkerPool` で実行する経路。手動入札はこれが無くても成立する（フルノードの既存経路へ合流）が、ワーカーノードは板の仕事を請けられないまま。dashboard は `engine/status.json` の `board.intake_projects` を見てボタンを理由付き非活性にしている | R1 の後 | 実装計画 §7 R2 / S8 §10 P4-a |
| P4-b | **検証委譲の後半** — 「このノードでは確かめられない」受入基準を他ノードへ回す経路。公示を出す口は R2a で開いたが、請け負えるのはフルノードだけ | R2b | S4/S5 §7-1 / S8 §10 |
| — | **「旧バージョンノードが入札しない」の実機確認** — `agentcore.board.eligible` の `requires.contract_version` 判定として実装済み。ワーカーノードが実際に入札する状態（R2b）にならないと実機で確かめられない | R2b | 実装計画 §7 R2 |

---

## 3. 必要が出たときに拾うもの

いずれも「動作は正しいが最適でない / 入口を足すだけ / 実際に困ってから」の類。
先回りで実装しない理由も含めて出典に記録済み。

| # | 内容 | 拾う契機 | 出典 |
|---|---|---|---|
| P1-b | CLI チャット起動先のパス手入力 UI（main 側は実装済み。入口のテキスト入力を足すだけ） | 宣言済みリポジトリで足りなくなったとき | S3/S2 §6 |
| P1-c | dashboard の repos.yaml/yml 読み取り（候補が減るだけで害は無い）。**前提が変わった（07-26）**: 「YAML パーサを持たないアプリ」という見送り理由は消えた——dashboard は `yaml` を実行時依存に持ち `base/main/yaml.js` で読む（host.yaml の `repos[]` は既にこれで読んでいる）。残っているのはレジストリ（`repos.yaml` / `repos.yml`）側の対応だけで、費用は当時より小さい | レジストリが yaml のプロジェクトで宣言し忘れの可視化が要るとき | S3/S2 §6 |
| P1-d | `cowork.roots` の掃除の口（project 化したフォルダの登録が残る。表示は自動で正しい） | 残骸が邪魔になったとき | S3/S2 §6 |
| P2-b | GitHub / Gitea の forge 実装（`forge` アダプタ境界だけ切って未実装。未対応リモートはフォージ無し運用へ倒れる） | 動作確認できるノードが要るようになったとき | S4/S5 §7 |
| P2-c | `diff2html` 依存の撤去（MR 無しタスクのローカル diff フォールバックが使う） | フォージ無し運用が消えたとき | S4/S5 §7 |
| P3-b | 墓標の自動失効 / 一括 revive（日付は行に持たせてある） | 古い墓標が実害を出したとき | S6/S7 §7 |
| P3-f | `ensure_repo_maps` の sha 取得キャッシュ — plan 前置の無条件化により、オフラインのノードでは plan の頭に repo 数 × 最大 60 秒が乗る | 実際に遅いという申告が出たとき（先回りすると「古い sha で再生成を見送る」別の壊れ方を作る） | S6/S7 §7 |
| P3-g | 日本語タイトルの Jaccard 照合の代替（N-gram 等）— `\w+` トークン化は分かち書き無しの日本語で「完全一致か 0 か」になる。一次防衛はプランナー入力への既存タスク注入に移してある | 重複が実際にすり抜けたとき | S6/S7 §7 |
| P3-h | dashboard から墓標を見る・解除する口（却下は画面から出せるのに解除は CLI `revive` だけの非対称） | 却下の取り消しが実際に要るとき | S6/S7 §7 |
| P3-i | draft → ready の昇格導線（`plan_review: off` で必須項目が埋まらなかったタスクの置き場） | `plan_review: off` の運用が出てきたとき | S6/S7 §7 |
| P4-c | `submitPost` / `award` の `git+` 板対応 — dashboard に手動 post の UI が無く、`board-award` 指示の契約だけ用意済み。**現状の直接書き込みは `git+` 板では誰にも届かない**（README に明記済み） | owner-picks 運用を始めたとき | S8 §10 |
| P4-d | 投機同時実行（speculation）— 契約からも削除済み（実装時に additive で戻す） | 必要が出たとき | 実装計画 W0-10 |
| P4-e | push 配信（forge webhook / long-poll）— 30 秒ポーリングで足りている | 遅いという申告が出たとき | S8 §10 |
| P4-f | `consultation` / `plan-critique` / `delivery-rationale` の対話化 — 構造化見出しの抽出に依存するため対話にすると抽出点が消える | 抽出をやめてよいと判断できたとき | S8 §10 |
| P4-g | 対話診断 tmux セッションの一括掃除（使い捨てなので状態は残らないがセッションは溜まる） | セッションが溜まって困ったとき | S8 §10 |
| — | dashboard の未実装改善（外部通知ルーティング・横断要対応キュー・条件付き自動承認・決定メモリ・メトリクス等） | — | [dashboard 設計書 §8](../designs/agent-dashboard-design.md) |
| — | **host.yaml 検査（W5/W6/W7）を E（fail-fast）へ昇格するか**。P1-3 は警告で入れた——既存 host.yaml に残る未知キーでフリート全台を一斉に起動不能にする方が害が大きいため。S1 §3.3 の E6（`projects[].config`）を宣言どおり fail-fast へ戻すかも同じ判断に含む | canary（§1.1）で警告の発生件数と内容を見てから | P1 詳細設計 §3.3・§8 |
| — | **プロジェクト側 `commands/*.err` の期限掃除**。土台（`agentcore.commands.prune_rejected`）は P1-4 で用意済みで、配線するだけ。状態リポジトリ配下なので古い失敗が全 PC へ配られ、要対応カードのノイズになる | `.err` の残骸が実際に邪魔になったとき（消える条件を増やす前に dashboard の失敗バナー表示規約との突き合わせが要る） | P1 詳細設計 §8 |
| — | **doctor に設定値の検査を足す**（`argv_limit ≤ 0` 等）。agent-flow の doctor は持っているが agent-project には無い。P1-2 で `argv_limit` を足したので対象が 1 つ増えた | doctor へ検査をまとめて足す P3-3 のとき | P1 詳細設計 §8 |
| — | ~~**明示指定の node_id が `normalize_node_id` を通らない**~~ → **決着（2026-07-27）: 正規化しない**。この行と P2 §8-1 の「次の静止点でやる」は、P0 詳細設計 §8 の「明示値は正規化しない（cutover ガイドが『そのまま使う』と約束。この非対称は意図的）」と正面から逆になっていた。**P0 側で決着**——名義の変更は node-id 切替という人の明示操作に閉じたままにし、黙って綴りを書き換えない。不変条件（同じ PC は同じ綴り）は**書き換えではなく検出**で守る: 非正規形の明示宣言を doctor が所見にして人に切替を促す（`agent-project` / `agent-flow` の doctor に配線済み）。これに伴い、静止点の相乗り一覧からこの項目は外れた | 済（検出は doctor。実害が出る側＝板レイアウトの名義綴りは P2-5 で塞いである） | P0 詳細設計 §8 / P2 詳細設計 §7-C・§8-1 / [棚卸し §6-2](2026-07-27-post-canary-backlog.md) |
| — | **スキーマ検証を CI へ**。`schemas/*.json` に対して、テストが作る実データ（`nodes/<id>.json` / `post.json` 等）を突き合わせる。`jsonschema` 依存が無いので、`$defs.node.repos` が実装と食い違っていたことを機械では検出できなかった（人が読み比べて初めて分かった・P2 で 2 度目） | 契約の食い違いをもう 1 度踏んだとき。入れるならまず 1 回流して既存の違反量を見てから（P3-1 の C-4 と同じ型の判断） | P2 詳細設計 §7-E・§8-2 |
| — | **`workloads` 未宣言を doctor が知らせる**。板に参加していて未宣言なら「制限しない（全部引き受ける）」ことを info で示す。**警告にはしない**——宣言忘れと意図的な無制限を区別できない | doctor へ検査をまとめて足すとき（上の設定値検査と同じ回） | P2 詳細設計 §8-3 |
| — | **`max_concurrent: 0`（無制限）の安全弁**。宣言どおり無制限にしたが、PC の資源を使い切りうる。doctor の warn を足すかは別判断（`host.yaml.example` とセットアップガイドには注記済み） | 実際に無制限を使う運用が出てきたとき | P2 詳細設計 §8-4 |
| — | **`agent_flow/gitbus.py:_safe` の置換文字**（不正文字を `_` へ。他は `-`）。板レイアウト側は P2-5 で `protocol.safe_name` へ寄せたが、バス全体のパス綴りは残る。揃えると既存 run のディレクトリ名が変わる | バス側で綴りの割れが実害を出したとき（現状は同一プロセスが書いて読むので割れない） | P2 詳細設計 §8-5 |
| — | **speculation を入れるときの枠の数え方**。P2-3 の自己抑制は「1 委譲 = 1 枠」を前提にしている。投機同時実行（§3 の P4-d）が入ると、同じ委譲を複数ノードが持つので数え方の見直しが要る | speculation を実装するとき | P2 詳細設計 §8-6 |
| — | **手元で `npm test` を回す前に `npm install --omit=dev` が要る**（`nodeRepos.js` が `yaml` を require するため）。CI は先に入れているので緑だが、README と詳細設計のテスト手順にはその 1 行が無い | 次に手元テスト手順を触るとき | P2 詳細設計 §9.2-N |

---

## 4. 意図して残した割り切り（やらないと決めたことの記録）

| 内容 | 理由 | 出典 |
|---|---|---|
| **charter acceptance の LLM 一発合成が残る（P2-d）** — タスク検証は証跡ベースへ置き換えたが、`resolve_charter_acceptance`（マイルストーン収束判定）は今も自然文 → コマンド合成。S5 と同じ問題（合成コマンドの良し悪しを人が判断できない）を抱える設計上の非対称で、**ここが残るあいだ `synth_verify` と静的スクリーニング群も消せない** | 検証対象（タスク単位・成果ブランチ上ではない）も出口（milestone）も違い、別設計が要る。S6 で「基準を書くのはエージェント、直すのは人」の経路ができたので下地は揃った | S4/S5 §6・§7 / S6/S7 §7 |
| red-green（`verify_validate`）を fast path 専用に残す | 差分の常設基準が効くのは verifier 経路だけ。`verify_template` 由来の機械生成コマンドが done の唯一の根拠になる経路には、実行で弁別を確かめる価値が残る | S4/S5 §6 |
| exec 断片合成の解消はしない | あの方式は「テストが `km.<name>` をモンキーパッチできる単一名前空間」を意図した選択で、解消はテスト 2.5 万行の参照モデルごと張り替えになる。配布統合（W3-1）は合成に触らず達成できた | 実装計画 §7 |
| `size` は表示のみ（自動再分解しない） | 自動で計画を作り直す経路を増やすと、人が直した計画が動く理由が増える | S6/S7 §7 |
| md 直接編集の検出はしない | 内容署名の維持コストに見合わない。実害は `planned_title` で塞いだ | S6/S7 §7 |
| `readonly` の強制はしない（宣言 `enforced / best-effort` と画面警告のみ） | レイヤの責務は argv の組み立て。防御を持つと CLI ごとの実装が散らばりとして再発する | S9 §6 |
| 参加ノード表に他 PC の `local`（絶対パス）を出さない | 読み手に意味が無い。~~アダプタ段階で落とす~~ → **P2-2 で「そもそも板へ配らない」へ強めた**（配ってから画面で落とすのは、データが全 PC へ伝播している事実を変えない） | S8 §10 P4-h / P2 §3.2 |
| 画面側の「2 つ目の実装」（CLI 定義ローダ・契約バージョン・URL 正規化）は残す | 候補を出すたびに Python を起動すると描画がプロセス起動待ちになる。代償は**ゴールデンテストで正典に縛る**ことで払う——契約バージョンと URL 正規化は Python 側の正典を実際に読んで突き合わせる | S9 / P2 §3.1.2・§3.5.4 |
| `agent_flow/gitbus.py:_safe` の置換文字は揃えない | バス全体のパス綴りに使われており、変えると既存 run のディレクトリ名が変わる。板レイアウト側だけ `protocol.safe_name` へ寄せた（板の名義は板の規則で綴る、が線引き） | P2 §3.5.5 |
| `_source_repo` の共有 bare ミラーは blobless のまま | フォージ無し運用の自動マージだけが blob 遅延取得のネットワークを要する。確実にしたいノードは host.yaml `repos[].local` にフルクローンを宣言する。S4 でレビューが MR/PR へ寄り出番は縮んだ | S1 §7 / 改良仕様 P1-e |
| `participate` 受理直後のリース窓は縮めない | 受理〜run 作成の間に呼び出し側が死ぬと inbox claim のリース失効まで拾い直されないが、リースを短くすると起動待ち run の二重取りが起きる | [agent-flow 設計書](../designs/agent-flow-design.md) |
| 状態リポジトリ無しの完全ローカル運用は非推奨どまり（git init 縮退は動く） | 公式サポート範囲を広げない | S1 §7 |
| host.yaml を複数ノードへ共有配布する場合の `projects[].root` パス差異 | 「共有配布するなら root をノード間で同一パスに揃える」を運用規約とする | S1 §7 |

---

## 5. 運用で観測が要る懸念

- **notes の同時編集**（P3-e）: `notes/` は状態リポジトリ配下で全 PC へ同期される。衝突解決は
  state 同期の既存規則に委ねており、メモの粒度で衝突が問題になったらファイル名にノード id を
  入れる。
- **1 プロセス集中（常駐一本化設計 C3）**: single-flight・タイムアウト・例外隔離・
  ワーカー分離・self-watchdog で緩和してあるが、実負荷での挙動は canary（§1.1）で観測する。
- **スキル起動の単発実行はノードのセマフォ外**（同 C5）: 呼び出し元の判断だが、同じ
  agent CLI の座席を消費する。status 表示で見えるようにしてある。
- **フリート更新の規律**（同 C13）: 契約変更は静止点で全ノード一斉・スキーマと実装は
  同一コミット。更新漏れノードは「入札しない」に倒す実装は済んでいるが、実機確認は
  R2b 待ち（§2）。
- **host.yaml 検査（P1-3）の警告が実機で何を拾うか**: 未知キー・層違い・型違いを
  警告として入れた。canary の 3 台で「実際に出た警告」を数え、**出ないなら E へ昇格**
  （設定ミスを起動時に止める）、**出るなら文言と救済の妥当性**を見直す。スカラの
  `tags:` / `agent_cli:` / `workloads:` は畳んで救済しているので、救済が働いた回数も
  観測点になる。
- **`max_concurrent` の意味が変わったこと（P2-3）が実機で何を起こすか**: `0` は
  **無制限**になった（以前は「既定 4」に読み替えていた）。canary の前に各 PC の宣言を
  確認し、`0` を「既定のつもり」で書いている PC があれば書き直す（キーごと省略が既定 4）。
  観測点は 2 つ——**枠の自己抑制で入札を控えた回数**（板の仕事が滞留していないか）と、
  **無制限を宣言した PC の負荷**。前者が多いなら上限が実態に対して小さい。
- **`workloads` を誰も宣言していない状態が続くか**（P2-3）: 明示宣言だけを正としたので、
  宣言するまで入札の絞り込みは効かない（＝従来どおり）。canary で「宣言したくなる場面」
  （特定の PC にだけ amigos を寄せたい等）が実際に出るかを見る。出ないなら、この項目は
  契約に残すだけで運用の口を増やさない。

## 6. 今回の棚卸しで新たに見つけたもの

07-26 時点の実装を横断調査した結果。既知の積み残し（§1〜§4）に無いものだけを載せる。
テストは実測済み（agent-project 1,063 件中 §6.1-2 の 1 件だけが間欠失敗。他は全緑）。

### 6.1 不具合（直すべきもの・重要度順）

> **7 件とも決着（実装済み・2026-07-26）**: 1・4 は P0（[詳細設計](2026-07-26-p0-pre-canary-fixes-detailed-design.md)）、
> 2・3 も P0、5・6・7 は P1（[詳細設計](2026-07-26-p1-config-and-safety-detailed-design.md)）。
> 各行の本文は**当時の記述のまま**残す（何が壊れていたかの記録として読む）。
> 実装で確定した差分は各詳細設計の §9 にある。

1. **`remote_review` が Config へ配線されておらず、`observe` が効かない**（高）。
   `CONFIG_DEFAULTS` にキーはあり層検査も通るのに、`Config` dataclass にフィールドが無く
   `build_config` も渡していない。読み出し（`mr.py:397`）は `getattr(cfg, "remote_review",
   "settle")` なので、**プロジェクト yaml に `observe` と書いても常に `settle`** になり、
   S4 の移行用スイッチ（フォージ決着を表示だけに留める）が機能しない。observe 分岐は
   到達不能の死んだコード。S5 の verifier キーで踏んだ「CONFIG_DEFAULTS にあるだけで
   届いていない」欠落（S6/S7 詳細設計 §6 で自省済み）と同型の再発で、機械的に差分を
   取ると Config へ届いていないのはこのキーだけ（**訂正**: `journal_max_bytes` /
   `journal_keep` / `root` にも Config フィールドは無い。ただしこの 3 つはモジュール大域・
   パス起点として別経路で正しく届いており、死んでいるのは `remote_review` だけ——
   P0 詳細設計 §2.4）。設定キー追加時に
   「CONFIG_DEFAULTS ⊆ Config フィールド」を CI で固定する再発防止まで含めて直したい。
2. **`serve` の SIGTERM ハンドラ設置が子プロセスの起動より後**（高）。
   `resident_cli.py` の `cmd_serve` は `_build_resident`（この中で子を start 済み）→
   `write_status()`（git 観測を含み数秒かかりうる）→ シグナルハンドラ設置、の順。
   この窓で SIGTERM が届くと既定ハンドラで即死して `graceful_shutdown` が走らず、
   **子だけが監督者不在で生き残る**（次回起動の子と同一プロジェクトでループが 2 本並ぶ——
   コード自身のコメントが警告している事故そのもの）。
   `tests/test_resident.py::test_serve_exits_cleanly_on_sigterm` が実際に間欠失敗する
   （3 回連続実行で 1 回、`-15 != 0`）。`systemctl restart` の連打や起動直後の
   シャットダウンで踏む。修正はハンドラ設置を `_build_resident` の前へ動かすだけ。
3. **ノード宛て指示ドロップの置き場が、書き手（dashboard）と読み手（常駐体）で別**（高）。
   常駐体は `$AGENT_COMMANDS_DIR` → WSL 側 `~/.agents/commands` を読むが、dashboard の
   `node-commands.js` は Windows 側 `os.homedir()` 基準で書く（同じ dashboard の
   `engine.js` は `wslpath` で WSL 側 home を解決して `engine/status.json` を読んで
   いるのに、この経路だけ通っていない）。正典構成（Windows で dashboard + WSL で
   常駐体）では投函先と取り込み先が別ファイルシステムになり、**手動入札・委任中止・
   落札が押しても何も起きない**（`.err` も出ず pending のまま）——S8-2 が直したはずの
   「押しても効かないボタン」が置き場所を変えて残った形。逃げ道の設定
   `delegation.nodeCommandsDir` は config.js の既定に載っておらず、画面から辿れない。
   旧 `~/.agent` フォールバックの有無も両者で食い違う。
   **追記（P0 詳細設計 §7-A）**: 置き場を揃えても届かない。dashboard は指示レコードの
   `board` に板の**作業ディレクトリ**（`delegation.boardRepos[i]`）を入れるが、常駐体は
   板の**所在**（`host.board`）と完全一致で照合する（`resident_cli.py:362`）ため、
   正典構成（UNC パス 対 `git+<url>`）では必ず不一致で全指示が `.err` へ落ちる。
4. **`Config.node` だけが `normalize_node_id` を通らない**（中〜高）。
   `node_id` 未宣言・環境変数無し（host.yaml を複数 PC へ共有配布する場合の推奨構成）の
   とき、`Config.node` は大文字を保持し、常駐体・板・agent-flow は小文字化する。
   ホスト名に大文字が入る（Windows/WSL では普通）PC は `status/DESKTOP-X.json` と
   `nodes/desktop-x.json` の 2 名義になる。`- node:` の完全一致で判定する
   `task_runnable_here` は、人が板の端末一覧（小文字）を見て書いた `- node:` を
   **どのノードも拾わないまま ready で固める**。`doctor --node-id-cutover` も status 側の
   残骸を見つけられない。`agentcore.nodeid` を 1 実装にした動機の取りこぼし。
5. **組み込み検証プロンプトが `verify_side_effects` を無視する**（中）。
   スキル（backlog-verifier）が見つからない／実行失敗時のフォールバックプロンプトは
   `acceptance` とタイトルしか使わず、副作用制約（作業ツリー外に書かない・外部へ
   書き込まない）が載らない。**安全設定がスキル未導入ノードで黙って落ち**、検証は失敗時に
   リトライで何度も走るので副作用が累積しうる。`rules` / `repo_context` / `recipes` /
   `feedback` も同じ経路で落ちるが、そちらは品質劣化に留まる。
  **追記（P1 詳細設計 §7-I）**: 検証プロンプトの 2 経路のうち、**テストが見ているのは
  組み込みだけ**。テストは中立な一時 cwd で走りエージェントホームも隔離されるため
  `find_skill_script` がリポジトリのスキルを見つけず、既存の `build_verifier_prompt`
  テストは**スキル経路を一度も通っていない**（組み込みが acceptance と DIFF_CRITERION を
  持っていたので緑だった）。どちらの経路も明示的な seam なしには検査できない。
6. **agent-project にだけ argv 長制限の退避（spill）が無い**（中）。
   agent-flow / agent-amigos は `prompt_via: argv` の CLI でプロンプトが上限を超えると
   一時ファイルへ退避するが、agent-project の `_agent_cmd` は無防備。S5/S6 で
   プロンプトは明確に肥大した（verifier 入力 = repo 文脈 + rules + レシピ + feedback、
   planner 入力 = charter 全文 + 既存タスク + 墓標）。既定 CLI の kiro は argv 渡しなので、
   超過すると verifier は全基準 unverifiable、plan は空振りで人へ倒れる。スキーマの
   「argv 長制限を超えると自動で退避に切り替わる」という説明とも矛盾する。
   **追記（P1 詳細設計 §7-A・§7-F）**: 退避の実体は flow / amigos が持つ ad-hoc 版
   （本文をファイルへ出しプロンプトだけ差し替える）で、`headless_cmd` の `spill_path`
   経路とは**別物**。後者は権限フラグを `spill.args`（kiro では `--trust-tools=fs_read`）へ
   置き換えるので、実行して確かめる verifier に使うと全基準 unverifiable に倒れる。
   また E2BIG（`Argument list too long`）は失敗トリアージの env パターンに掛からず
   「内容の問題」に分類され、タスクのリトライ予算を焼く。
7. **`revive` が charter スコープを無視して墓標を消す**（低）。墓標の追記は
   `(指紋, charter)` 単位なのに、削除は指紋一致行を charter 無関係に全部消す。
   複数 charter 運用で意図しない解除が起きる。

### 6.2 実装と契約のずれ・二重実装（懸念）

> **§7.3 P2 で決着した行には「決着」を付けた**（2026-07-26）。残りは §3 か §7.7 へ移すか、
> 契機待ちとして行に理由を残してある。**未決着で残っているのは 1 行だけ**——
> 識別子レベルの `canceled` 残存（低・触るファイルの修正時に限る方針）。

- **決着（P2-1）** **`CONTRACT_VERSION` が 3 箇所に重複定義**: `agentcore/board.py`（入札判定が使う）・
  `resident/status.py`（板へ宣言する値。`contract_compatible` も docstring ごと重複）・
  dashboard `engine.js`。片方だけ上げると「版 2 と宣言しつつ版 1 で判定」になり、
  fail-close の設計ゆえ**誤動作ではなく無言の不参加**に倒れて誰も気付かない。
  「規則が片方だけ育つ」ことを潰すために agentcore へ集約した、その定数が割れている。
  **決着（P2-1・実装済み）**: 正典を `agentcore.board` の 1 か所にし、`resident/status.py` は
  import（`contract_compatible` の重複本体ごと削除）。dashboard の写しは **Python の正典を
  実際に読む**ゴールデンテストで縛った（写しそのものが問題なので、写しを機械に突き合わせ
  させる）。構造テストは値の一致ではなく**同一オブジェクト**を見る（たまたま同じ数を
  2 か所に書いた状態が緑のまま残らないように）。
- **決着（P2-2）** **板の `nodes/<id>.json` に deprecated の `local`（他 PC の絶対パス）を publish している**:
  `repos.schema.json` は `local` を「ホスト固有なので共有レジストリに置けない」と
  deprecated 宣言しているのに、常駐体は host.yaml の `repos[]` を `local` ごと共有 git の
  板へ書く（S8 §6.2 の「速度最適化のヒント」として意図的）。表示では落としているが
  データは配られており、S3 の動機と正面から矛盾する。意図を維持するならスキーマ側の
  文言の決着が要る。あわせて `$defs.node.repos` の宣言（レジストリ形）と実装
  （host.yaml 配列形）も食い違い、スキーマ検証すると落ちる。
  **決着（P2-2・実装済み）**: **publish をやめた**。読み手を全部数えて 1 人も居ないことを
  確認したうえでの判断——入札判定（`declared_repo_ids`）は name と正規化 url、画面
  （`board-adapter.listNodes`）は url からラベルを作るだけ、doctor は心拍だけ。速度最適化と
  しての `local` は請負ノードが**自分の** host.yaml から解決している（板は経路に無い）。
  publish をやめれば矛盾が消えるので `repos.schema.json` の deprecated 文言は触っていない。
  `$defs.node.repos` は実装の形（2 形を受ける・`local` 禁止を `not.required` で表明）へ直した。
- **決着（P2-3）** **`nodes/<id>.json` の `workloads` / `max_concurrent` を入札判定が読まない**: スキーマは
  「workload 不一致・上限超過時は入札しない」と宣言するが、`eligible()` は tags /
  agent_cli / contract_version / repos しか見ない。忙しいノードが板の仕事を掴んだまま
  枠待ちで塞ぎ、空きノードが拾えない。`max_concurrent: 0` の意味もスキーマ（無制限）と
  実装（既定 4）で真逆。
  **追記（P2 詳細設計 §7-A）**: 読まれていないのは宣言だけではない。**agent-amigos は
  `board_eligible` に `agent_cli` を渡していない**（`agent_amigos/board.py:272`）。
  `eligible` の CLI 判定は fail-close なので、`requires.agent_cli` を持つ公示に
  **amigos ノードは永久に入札しない**——`daemon.agent_cli` は存在し、同じ値を
  `assign.py` は畳んで使っているので単なる渡し忘れ。症状は「無言の不参加」で同型。
  **決着（P2-3・実装済み）**: `eligible()` へ `workloads` / `max_concurrent` / `inflight` を
  追加し、flow・amigos の両方へ配線（amigos の `agent_cli` 渡し忘れも修正）。`workloads` は
  **明示宣言だけを正**とし、`amigos_bus` からの導出は publish ごとやめた——導出値を判定に
  使うと、宣言していない PC が黙って入札をやめる。`max_concurrent` はスキーマ側
  （0 = 無制限）へ実装を寄せ、既定 4 は「未宣言時の既定」へ移した（`HostConfig` で未宣言と
  0 を区別する）。枠の件数は板の `status/<who>.json` から数える（`node_inflight`）。
  **更新前の確認**: `0` を「既定 4 のつもり」で書いている PC は書き直す（§7.3 の静止点）。
- **決着（P1-3）** **host.yaml のトップレベルキーは無検査**: `PROJECT_ONLY_KEYS` は定義とテストにしか
  使われず、`_validate_layers` は `defaults` / `overrides` しか見ない。host.yaml の
  トップレベルに `plan_review: false` を書いても、`node_id` を `nodeid` と綴り間違えても、
  警告ゼロで黙って無視される。S1 の E2 契約（defaults/overrides の検査）自体は満たすが、
  「設定したのに効かないことに気付けない」という S1 の設計動機が host.yaml 側だけ抜けている。
  **追記（P1 詳細設計 §7-C・§7-J）**: 無検査なのは綴りと層だけではない。`tags:` /
  `agent_cli:` に**スカラを書くと 1 文字ずつの配列になる**（`[str(a) for a in "codex"]`）。
  板の `nodes/<id>.json` へ `["c","o","d","e","x"]` が publish され、`requires.agent_cli` を
  持つ公示に**永久に入札しない**——入札選別は fail-close なので、誤動作ではなく
  「なぜかこの PC だけ仕事を取らない」という無言の形で出る。`defaults.agent_cli`（スカラ）と
  紛らわしいキーなので誤記は起きやすい。あわせて S1 §3.3 の E6（`projects[].config` は
  エラー）も未実装で、`projects[]` の要素キーには検査自体が無い。
- **決着（P2-4）** **`BoardRepo` の請負側書き込みだけ排他を通らない**: `write_bid` /
  `write_cancelled` / `write_award`（S8 で追加）だけ flock と `_ensure()` を通らず直接
  書く。transport の破損時再クローン（`rmtree`）や `pull --rebase` と競合すると
  入札・中止が消えうる。
  **決着（P2-4・実装済み）**: 3 つとも `with self._locked(): self._ensure()` を通した。
  あわせて 2 つの規律を型で固定した——**ロックの中から `sync_push` を呼ばない**
  （同一プロセスの flock 再入は自分自身と競合して止まる。push は指示を取り込んだ側が
  外で 1 回だけ）と、ロックの入れ子は板 → claim の一方向だけ。`write_bid` の戻り値
  （冪等で書かなかった場合）も受理レシートの文言に出すようにした。
- **決着（P1-4）** **ノード宛て指示に debounce と `.err` 掃除が無い**: プロジェクト側 `ingest_commands` に
  ある「書きかけ猶予」と「成功時の古い `.err` 掃除」が、ノードスコープ側に無い。
  スキーマは書き手として人を認めているのに手置きは即 `.err` 行きで、`.err` は無限に
  溜まり gc tick も見ない。**追記（P1 詳細設計 §7-D・§7-E）**: 受理レシートは
  `write_receipt` の中で prune されるので溜まるのは `.err` だけ。また debounce は
  素朴に「読めないファイルを飛ばす」形にすると**指示の順序が壊れる**（同じ公示への
  「入札 → 中止」が入れ替わり、中止済みの板へ入札を書く）。ノード側の reject は
  JSON 不正のときしか `engine/status.json` に載らず、板不一致・未知指示・公示不在で
  `.err` に落ちた指示は横断ビューに現れない。
- **決着（P2-5）** **`DIFF_CRITERION` の文字列が本体とスキルの 2 箇所**: 片方だけ直すと、
  検証レポートに出る基準文とエージェントが見た基準文が黙ってずれる（判定は番号で
  突き合わせるため）。
  **決着（P2-5・実装済み）**: 本体を正典にし、スキルへは `spec["diff_criterion"]` として
  解決済みの文を渡す（P1-1 の `side_effects_text` と同じ手）。スキル側の定数は受け皿として
  残す（スキルは単体でも動く契約）。あわせて、同じ 2 経路で `workspace.url` が空のときの
  描画が食い違っていた（スキル側だけ空欄になる）のも揃えた。
- **決着（P2-5）** **`repolocal` の Python / JS で吸収規則がずれる**（低）: symlink 解決の有無が
  違い、JS 側は `repos:` の行末コメントで 0 件に読める。どちらも「読めなければミラー
  取得へ落ちる」だけだが、1 実装へ集約した動機に照らすと再発の芽。
  **追記（P2 詳細設計 §2.5・§7-B）**: 行末コメントの件は**既に解消済み**（JS の読みは
  `base/main/yaml.js` の本物のパーサへ移設された）。残るのは symlink 解決の有無だけ。
  一方で**同じ規則の 2 実装がもう 1 組見つかった**——`agentcore.repolocal.normalize_repos`
  と `resident_cli._normalize_host_repos` で、mapping+dict 形の `local: null` が
  後者では `"None"` という**文字列のパス**になる。
  **決着（P2-5・実装済み）**: JS 側に symlink 解決（`fs.realpathSync.native` → 失敗したら
  `path.resolve`）を足し、**両言語をゴールデン表で縛った**（fs に依存しない項目のみ。
  symlink 解決そのものは各言語の個別テストで）。`_normalize_host_repos` は
  `agentcore.repolocal.normalize_repos` を呼ぶ薄い皮にした。
- **一部決着（P2-5）** **識別子レベルの `canceled`（米式）残存**（低）: 語彙統一（W0-9）の対象はデータ値で、
  `mark_canceled` 等の関数名・コメントには米式が残る。書き込む値は `cancelled` で正しく
  実害は無いが、grep のノイズになる。`NodeCapability.write` のパス導出が
  `_safe_node` を通らない件（現経路では実害なし）も同類。
  **追記（P2 詳細設計 §7-C・§7-D）**: 名義まわりの取りこぼしが 2 件ある。
  (a) **明示指定の node_id が `normalize_node_id` を通らない**（agent-flow / agent-amigos の
  `--node-id` と設定値。P0-3 が 1 本にしたのは未宣言時の導出だけ）。
  (b) **板レイアウトの名義綴りが flow だけ別実装**——入札を `protocol.safe_name`（`-`）で
  書き、`_has_own_live_bid` は `gitbus._safe`（`_`）で読む。単独では無害だが (a) と
  重なると**手動入札の受け皿が永久に効かない**（「押しても何も起きない」の 3 度目の形）。
  **決着（P2-5・実装済み）**: (b) と `NodeCapability.write` は塞いだ——板レイアウトの名義は
  `protocol.safe_name`（板の規則）で綴る。`gitbus._safe` 自体は触っていない（置換文字を
  変えると既存 run のディレクトリ名が変わる＝静止点の意味が変わる）。**(a) は未修正**で、
  名義変更を伴うため次の静止点へ（§3 の該当行）。`canceled` の識別子改名も方針どおり
  未着手（触るファイルの修正時に限る）。

### 6.3 文書の綻び

> **5 件とも決着（実装済み・2026-07-26）**: 上 4 件は P3（§7.4。実施結果は §7.6）、
> 「agentcore のテストルートが 2 つ」は CI で両方を明示することで担保した
> （件数は実測とずれていた——正しくは 58 / 74 件。§7.6 に記録）。
> **ただし CI の収集にはもう 1 つ穴があった**——`unittest discover` は関数形式のテストを
> 集めないため、`resident` の単体テスト 4 ファイル 31 件が緑とも赤とも報告されていなかった
> （P2 の実装中に発見・[P2 詳細設計 §9.2-L](2026-07-26-p2-contract-consolidation-detailed-design.md)）。
> 同じコミットで収集を直してある。

- **`docs/guides/multi-pc-operations.md` が常駐一本化前の記述と混在**: 廃止済みの
  `start` / `stop`、通らない `status --root`、旧モデルの「各 PC 1 daemon」が 10 箇所超
  残る一方、一部だけ `serve` へ更新済み。W3-2 の実施結果は本ガイドを「直した」と
  記録しているが、直り切っていない。複数 PC 分担は今回の改修の中心で、最初に読まれる
  ガイドなので優先度は高め。
- **S1 詳細設計が現存しないシンボルを参照**: `_validate_layers` の引数形が実装と違い、
  `_STATE_SIGNIFICANT` は存在しない（同期は除外方式）。結論は正しいが、設計書を根拠に
  読むと存在しない機構を探すことになる。
- **doctor に S1 設計 §3.6 の新検査が入っていない**: E1〜E7 相当の起動前チェック再掲と
  host.yaml `projects[]` の root 存在・origin 一致検査が未実装（設計書の実績節は実装済みと
  記す）。root の綴り間違いが「子の起動失敗 → 隔離」という最も遠い症状でしか見えない。
- **R10 検査は素朴な grep では成立しない**（§1.2 に詳述）: ガイドのファイル名・スキーマ名が
  内部名を正当に含むため、検査規則に除外の設計が要る。
- **agentcore のテストルートが 2 つ**: `agentcore/tests`（74 件）と `tests/`（53 件）。
  片方だけ `discover` すると残りが黙ってスキップされる。R4 の CI では両方を明示するか
  1 ディレクトリへ寄せる。

### 6.4 確認して問題なしだったもの（記録）

- `tombstones.md` / `notes/` / `verifications/` / `verify-recipes/` / `context/` は
  すべて状態同期の対象（同期は除外方式で、これらは除外されない）。複数 PC で墓標や
  検証レポートが共有されない懸念は当たらない。
- `task.schema.json` の status enum・`acceptance` の配列 ↔ 複数行往復は実装と整合。
- `agent-cli.schema.json` の `interactive` 継承規則は Python / JS で一致。
- 旧キー（`state_worktree_dir` / `--profile` 等）のガイド残存は移行対照表としての
  意図的な記載のみ。`gitAutoPush` / `location: daemon|remote` は残存ゼロ。

---

## 7. 修正計画

### 7.0 方針

1. **実機 canary（R1）を段の区切りにする。** canary は「複数 PC + Windows/WSL で 1 週間、
   二重実行 0・stale done 0・状態欠損 0」を確かめる受入試験で、§6.1 の上位 4 件は
   まさにその構成で発現する（孤児化・別名義・届かない指示）。直さずに canary へ入ると、
   起きた異常が設計の問題か既知バグかを切り分けられず、1 週間が無駄になる。
   よって **P0（canary 前）→ R1 実施 → P1（canary と並行可）→ P2（R2b の前まで）→
   P3（文書と CI・随時）** の順に置く。
2. **同型の欠落は再発防止までをセットで直す。** `remote_review` は verifier キーと同じ
   「CONFIG_DEFAULTS にあるのに Config へ届かない」の 2 度目なので、個別修正で終わらせず
   構造をテストで固定する。定数の重複（CONTRACT_VERSION / DIFF_CRITERION）も同じ扱い。
3. **契約に触る修正（P2）は静止点で全ノード一斉**（常駐一本化の規律を踏襲）。
   スキーマと実装は同一コミットで更新する。
4. 規模の目安: S = 半日以内 / M = 1〜2 日。P0 合計でも 2〜3 日で、canary の準備
   （実機 3 台の手配）と並行して終わる分量。

### 7.1 P0 — canary の前に直す（canary の結果を汚すもの）

> 詳細設計: [`2026-07-26-p0-pre-canary-fixes-detailed-design.md`](2026-07-26-p0-pre-canary-fixes-detailed-design.md)。
> 同設計 §7 に、実装との照合で新たに見つけた 8 件（うち 2 件は P0 の中で直す）を載せてある。

| # | 対象 | 修正 | 検証 | 規模 |
|---|---|---|---|---|
| P0-1 | serve の SIGTERM 窓（§6.1-2） | `stopping` イベントの用意とシグナルハンドラ設置を `_build_resident`（子の起動）より**前**へ移す。`write_status()` の git 観測はハンドラ設置後に | 間欠失敗している `test_serve_exits_cleanly_on_sigterm` が**順序修正によって**決定的に緑になること（リトライで誤魔化さない）。起動直後 SIGTERM の注入テストを追加 | S |
| P0-2 | ノード宛て指示の置き場ずれ（§6.1-3） | dashboard の `resolveCommandsDir` を `engine.js` と同じ home 解決（`engine.home` 設定 → `wslpath -w "$HOME/.agents"`）に揃える。`delegation.nodeCommandsDir` を `config.js` の既定へ載せ、設定画面から辿れるようにする。旧 `~/.agent` フォールバックは**新ホームのみに統一**（常駐体側が見ない場所へ書ける状態を残さない） | 投函 → 受理レシート（`processed/`）の往復テストを Windows/WSL パス変換込みで固定。`no-git-writes` と同じ流儀で「Windows home 直書きしない」を構造テスト化 | M |
| P0-3 | `Config.node` の正規化漏れ（§6.1-4） | `_auto_node_name` と `loop.py` の status 名義を `agentcore.nodeid.normalize_node_id` へ寄せ、導出を 1 実装にする。**大文字ホスト名の PC では名義変更になる**ので、node-id 切替と同じ静止点扱い: `doctor --node-id-cutover` の検査対象に「正規化前の名義の `status/` 残骸」を加え、[node-id-cutover ガイド](../guides/node-id-cutover.md)に 1 段追記 | 大文字ホスト名での `Config.node` = `HostConfig.node_id` = 板名義の一致テスト。`task_runnable_here` が小文字 `- node:` を拾う回帰テスト | M |
| P0-4 | `remote_review` の未配線（§6.1-1）+ 再発防止 | `Config` へフィールド追加・`build_config` で配線。あわせて**「CONFIG_DEFAULTS の全キーが Config へ届く」構造テスト**を新設する（`root` 等の意図的除外は明示リストにし、リストへ足すときに理由を書かせる）。verifier キー・remote_review と 2 度踏んだ穴を型として塞ぐ | `remote_review: observe` がフォージ決着を journal 記録だけに留める既存想定のテストを、実際の Config 経由で通す（getattr フォールバックでは通らない形に） | S |

**P0 の完了条件**: 上記 4 件のテストが緑 / canary ランブックの前提欄に「P0 済み」を記録。
その後 R1（実機 canary・§1.1）を実施する。

### 7.2 P1 — 効かない設定・安全性（canary と並行可。実機を要さない）

> **実装済み**（2026-07-26）。詳細設計:
> [`2026-07-26-p1-config-and-safety-detailed-design.md`](2026-07-26-p1-config-and-safety-detailed-design.md)
> （§7 に実装との照合で新たに見つけた 11 件・§9 に実装で確定した差分）。
> **P1-2 は総覧の記述（`headless_cmd` の spill 経路）とは別の方式を採った**——定義側の spill は
> 退避時に権限フラグを `--trust-tools=fs_read` へ置き換えるため、そのまま配線すると
> verifier が実行権限を失って全基準 unverifiable に倒れる（同設計 §7-A）。
> **P1 から出た積み残しは §3 の末尾 3 行と §5 の最終項**（警告を E へ昇格するかの判断・
> プロジェクト側 `.err` の期限掃除・doctor の設定値検査）で、いずれも契機待ち。

| # | 対象 | 修正 | 規模 |
|---|---|---|---|
| P1-1 | 組み込み検証プロンプトの `verify_side_effects` 無視（§6.1-5） | `_builtin_verifier_prompt` をスキル側 `prompt.py` と同じ入力（side_effects・rules・repo_context・recipes・feedback）で組む。**スキル有無で安全制約が変わらない**ことをテストで固定（両者のプロンプトに同じ制約文が載る） | S |
| P1-2 | agent-project の argv spill 欠落（§6.1-6） | `_agent_cmd` に flow / amigos と同じ退避（`headless_cmd` の spill 経路）を配線。閾値・退避先の掃除も揃える | S |
| P1-3 | host.yaml トップレベルの無検査（§6.2） | `HostConfig` 読み込みに未知キー警告と PROJECT_ONLY キー検出を追加。既存運用を壊さないため**警告から始め**、canary 明けに E 系へ昇格するか判断（S1 の E1/E2 と同じ文言カタログに W5 として登録） | S |
| P1-4 | ノード宛て指示の debounce / `.err` 掃除（§6.2） | `agentcore.commands` に debounce と「成功時に同一 id の古い `.err` を消す」を持たせ、プロジェクト側・ノード側の両方が同じ土台を使う形に寄せる。gc tick の対象に `~/.agents/commands/` を追加 | S |
| P1-5 | `revive` の charter スコープ無視（§6.1-7） | `remove_tombstone` に charter 引数を通し、CLI に `--charter` を追加。既定は「指定 charter の墓標 + タグ無し墓標」のみ削除 | S |

### 7.3 P2 — 契約の一本化（静止点で・R2b 設計の前までに）

> **実装済み**（2026-07-26）。詳細設計:
> [`2026-07-26-p2-contract-consolidation-detailed-design.md`](2026-07-26-p2-contract-consolidation-detailed-design.md)
> （§7 に実装との照合で新たに見つけた 9 件・§9 に実装で確定した差分・§9.2 に実装中に
> 見つけた 3 件）。**P2 から出た積み残しは同設計 §8 の 7 行**で、いずれも契機待ち
> （うち §8-1「明示指定 node_id の正規化」は次の静止点で拾う）。
> **実装の過程で、CI が 4 ファイル 31 件のテストを黙って素通りしていたことが分かった**
> （`unittest discover` は関数形式のテストを集めない）。P2 の護りがそこへ入るので、
> 同じコミットで収集を直してある（同設計 §9.2-L）。
> **P2-2 の「決め」は publish をやめる側で決着**（板の `local` は読み手が 1 人も居ないことを
> 実測で確認した——入札判定も画面も url しか見ず、手元クローンの解決は請負ノードが
> 自分の host.yaml から行っている）。**P2-3 の `workloads` は「明示宣言だけを正とする」**——
> 現在の `amigos_bus` からの導出値を判定に使うと、宣言していない PC が黙って入札をやめる。

R2b（ノード直轄実行）は板の契約を最後に固める機会なので、契約に触る修正はそこまでに済ませ、
R2b 設計と衝突させない。

| # | 対象 | 修正 | 規模 |
|---|---|---|---|
| P2-1 | `CONTRACT_VERSION` の 3 重定義（§6.2） | 正典を `agentcore.board` の 1 か所にし、`resident/status.py` は import（`contract_compatible` の重複実装ごと削除）。dashboard（JS）は定数が残るなら Python とのゴールデンテストで同値を固定する | S |
| P2-2 | 板への `local` publish とスキーマの矛盾（§6.2） | **決めが要る。推奨: publish をやめる**——入札可否は url ベースで足り（S3-5 の設計どおり local はヒント）、落札後の worktree 切り出しは自ノードの host.yaml から解決できるので、他 PC の絶対パスを共有リポジトリへ配る必然性が無い。維持する判断なら `repos.schema.json` の deprecated 文言を「共有レジストリ不可・板のノード宣言は可」へ改訂する。どちらでも `$defs.node.repos` の形（レジストリ形 → url/local 配列）は実装へ合わせる | S |
| P2-3 | `workloads` / `max_concurrent` を入札判定が読まない（§6.2） | `eligible()` に workload 照合を追加。`max_concurrent` は「板上の自分名義の非終端 `status/` 件数が上限以上なら入札しない」の自己抑制として実装（枠の真実は板にあるので二重管理しない）。`0` の意味は**スキーマ側（0 = 無制限）へ実装を寄せ**、ワーカープールの既定 4 は「未指定時の既定」へ移す。二重落札の轍（S8 §6.5）を踏まないよう R2b 設計と同時に入れる | M |
| P2-4 | `BoardRepo` 請負側書き込みの排他漏れ（§6.2） | `write_bid` / `write_cancelled` / `write_award` を `with self._locked(): self._ensure()` で他のメソッドと揃える | S |
| P2-5 | 文字列・小物の一本化（§6.2 低群） | `DIFF_CRITERION` を本体定数の 1 か所へ（スキルは生成時に受け取る）。**手は P1-1 で実証済み**——副作用制約は `side_effects_text` として解決済みの文を渡し、スキル側の表は「入力に無いときの受け皿」に降格した。同じ形にすればよい。あわせて `spill_prompt` の指示文（本体側 3 者が自前の文を持ち、定義の `spill.instruction` は Python から使われていない・P1 詳細設計 §7-B）の正典もここで決める。`repolocal` の JS 側 symlink 解決と `repos:` 行末コメント対応。`NodeCapability.write` のパス導出を `_safe_node` へ。`canceled` 識別子の改名は**触るファイルの修正時に限る**（改名だけのコミットは履歴のノイズ） | S |

### 7.4 P3 — 文書と CI（独立・随時。P0〜P2 と並行してよい）

> **状態: 4 件とも実施済み（2026-07-26）**。実装で確定した差分は CHANGELOG の
> 「リポジトリ / agent-project: 文書と CI（P3）」に記録。実施中に見つけたものは §7.6 へ。

| # | 対象 | 修正 | 規模 |
|---|---|---|---|
| P3-1 | CI の新設（§1.2 R4 と統合） | GitHub Actions で 5 系統を回す: 4 パッケージのテスト（**agentcore は 2 テストルートを明示**・§6.3）+ dashboard `npm test` + R10 grep 検査 + P0-4 の設定キー構造テスト。R10 検査は**本文のみ対象**（ファイルパス・スキーマ名・コードブロックを除外）の規則で書く（§1.2） | M |
| P3-2 | `multi-pc-operations.md` の全面改訂（§6.3） | 常駐一本化後のモデル（PC に 1 本の serve + 子の分担・controller リース）で書き直す。存在しないコマンド（`start` / `stop` / `status --root`）を一掃し、W3-2 でやり残した分を完了させる | M |
| P3-3 | doctor の S1 §3.6 検査（§6.3） | host.yaml `projects[]` の root 存在・origin 一致・branch 一致と、E1〜E7 相当の起動前チェックを doctor へ。設定ミスの原因究明を「子の起動失敗 → 隔離表示」から「doctor 一発」へ引き上げる。**キー・型の検査は P1-3 で純関数 `host_config_findings()` になっている**ので、doctor はそれを呼ぶだけ（同じ規則を 2 実装にすると「doctor は緑なのに起動時は警告」になる）。設定値の検査（`argv_limit ≤ 0` 等・agent-flow の doctor にはある）もここで足す | M |
| P3-4 | S1 詳細設計の記述訂正（§6.3） | 現存しないシンボル（`_validate_layers` の引数形・`_STATE_SIGNIFICANT`）へ訂正注記を追記（既存の「実装で確定した差分」節の流儀。本文は書き換えない） | S |

### 7.5 この計画に含めないもの

- §2（R2b・検証委譲・旧バージョンノードの実機確認）は canary（R1）後の実装フェーズで、
  修正ではなく機能追加。P2-3 だけは R2b 設計と同時に入れる接点として明記した。
- §3 の「必要が出たときに拾う」群は契機が来るまで着手しない（先回りしない理由が
  各行に書いてある）。
- §4 の割り切りは修正対象ではない（変えるなら割り切りの再決定が先）。

**この計画の消化状況（2026-07-26）**: P0 / P1 / P2 / P3 の 4 段とも実装済み。
残るのは §1.1 の実機 canary（R1）と、その後の §2（R2b・検証委譲）。
各段から出た積み残しは §3 の表と §5 の観測点へ転記済みで、**いま着手すべきものは無い**
（P2 の積み残しは §7.8 に段としての記録を残してある）。

### 7.6 P3 の実施で分かったこと（2026-07-26）

P3（§7.4）の 4 件を実施した際に見つけたもの。直したものは根拠、直していないものは
なぜ P3 でやらないかを書く。

**直したもの**

| 内容 | 直し方 |
|---|---|
| **セットアップガイド §6 が実装済みの機能を「未実装」と書いていた** — 「ノード能力宣言（板へ `nodes/<pc>.json` を出す）」は R2a の board tick で実装済み（`_node_capability` → `NodeCapability.write`）。ガイドだけが移行前のまま残っていた | §6 を書き直し、残っている見送り（プロジェクトを持たない PC の直轄実行 = R2b）だけにした |
| **利用者向け文書に内部名が 3 箇所**（agent-project README の「node 名順」「state sync push」、canary ランブックの記録表の見出し） — R10 検査を書いて初めて機械で見つかった。人の目だけでは 3 件とも通っていた | 本文は利用者の言葉へ、記録表の見出しは項目名なのでインラインコードへ |
| **`_validate_layers` が判定と出口（`sys.exit`）を兼ねていた** — doctor から再利用できず、P3-3 の起動前チェックは「起動時と少しだけ違う判定」を書くしかない構造だった（doctor が緑でも起動は止まる、が起こる） | 判定を `configfile.layer_findings` へ分離し、fail-fast の出口だけを `_validate_layers` に残した。doctor は同じ関数を呼ぶ |
| **R10 検査の除外規則は「パスらしいトークン」を空白区切りで取ると成立しない** — 日本語は語間に空白が無いため、`heartbeat/lease` を含む一文がまるごと 1 トークンとして落ち、同じ行の内部名まで道連れになる（検査が黙って骨抜きになる） | パス・ファイル名として落とすトークンを **ASCII のパス文字だけ**で構成する規則にした。テストで固定（`tools/ci/tests/`） |
| **agent-flow のテストに間欠失敗が 1 件**（40 回中 3 回）。`test_daemon.OrphanRecoveryTests.test_reclaim_after_owner_lease_expiry` が `reclaim_request(..., lease_sec=0.01)` を使っており、**「自分の claim を書く → 勝者判定」の往復より lease が短いと、claim した瞬間に自分の lease が切れて自分で勝者判定に負ける**。実装は正しく、0.01 秒 lease が非現実的だった。CI は実機より遅いランナーで回るので、間欠的に赤い CI（＝無い CI より悪い）になる前に潰す必要があった | リースの失効を時間ではなく値（`lease_until` を過去へ）で作る形に直した。他のリース系テスト（`orch_lease_until` を過去にする）と同じ流儀。40 回連続で緑 |

**直していないもの（報告）**

- **`flow-archive/` の所有者が居なくなっている**（低・要判断）。同期層は 2 つの除外リストを
  持ち、`_STATE_EXCLUDE_DIRS`（＝コミット対象から外す）には `flow-archive` が入るが、
  `.git/info/exclude` へ書く `DirectStateGit._EXCLUDE_PATTERNS` には入らない。
  `_untrack_excluded` も flow-archive を追跡から外さず、その理由をコード内で
  「viewer が所有・コミットする名前空間だから」と説明している。**dashboard の git 書き込みは
  常駐一本化 P2 で削除された**（`no-git-writes` テストで構造的に禁止）ので、この名前空間は
  いま誰も commit しない。結果として (a) 新しい clone では `git status` に未追跡として残り続け、
  (b) 旧 viewer が一度コミットした clone では「tracked だが誰も commit しない変更」が永久に
  残る——`_untrack_excluded` がまさに防ぐために書かれた状態そのもの。
  直すなら `_EXCLUDE_PATTERNS` と `_untrack_excluded` の両方に `flow-archive` を足すことに
  なるが、**追跡から外す＝全 PC のブランチからファイルが消えるコミットを打つ**ので、
  文書と CI の段（P3）ではなく静止点のある段で扱う。
- **§6.3 の件数が実測とずれていた**: agentcore のテストルートは `agentcore/tests` が 53 件では
  なく **58 件**（`agentcore/agentcore/tests` は 74 件で一致）。CI は件数ではなく両ルートを
  明示することで担保する。

### 7.7 P3 の積み残し（意図して入れなかったもの）

P3 の 4 件は完了しているが、その過程で「今回はやらない」と決めたものと、
ガイド改訂で行き場が無くなった改善案をここへ移す。**急ぐものは無い**。

**CI（P3-1）に入れなかった**

| # | 内容 | 拾う契機 / 理由 |
|---|---|---|
| C-1 | ~~サポート下限（python 3.9）での実行~~ → **決着（07-26）: 下限を 3.11 へ上げた**。「宣言は 3.9 なのに検査は 3.11 だけ」という食い違いは、**検査に宣言を合わせる**方向で解消した（3.9 のジョブを足す案は採らない——誰も動かしていない版を CI で支え続けることになる）。反映先: `tools/agent-tools/install.sh` の版検査、[セットアップガイド §1](../guides/single-resident-setup.md)、agent-flow の README / SKILL | **残っているのは 2 つ**: ①**Ubuntu 22.04 系の既定 python は 3.10** なので、その系のノードは `python3.11` を別途入れる必要がある（インストーラの案内に deadsnakes を書いた。canary の対象機がこれに当たるなら事前に確認する）。②CI は下限（3.11）だけを回しており、**新しい版での実行は無い**——将来 3.13 等で壊れても気付けないので、対象機の版が上がったらそちらも回す |
| C-2 | **`npm run lint`（eslint）** — CI は dashboard の**実行時依存だけ**を入れる（`npm install --omit=dev`）。lint は開発依存（eslint）が要り、`package-lock.json` は `.gitignore` 対象なので `npm ci` による固定もできない | lint を CI に載せるなら「開発依存の導入時間」と「lock を追跡するかどうか」をセットで決める。**依存の固定が無い**ため、実行時依存の解決は毎回最新の範囲内になる（今は 2 パッケージなのでリスクは小さいが、増えたら再考する） |
| C-3 | `concurrency` グループ（連続 push で古い実行を打ち切る）・依存キャッシュ | 実行時間や実行枠が問題になってから |
| C-4 | **R10 検査の対象拡張** — 現在は `docs/guides/` + 主要 README のみ。dashboard の画面文字列は dashboard 側の単体テストが別に固定しており、スキーマの `description` や他ツールの README は対象外 | 対象を広げるなら、まず 1 回流して既存の違反量を見てから（いきなり必須ゲートにすると赤で埋まる） |

**doctor（P3-3）に入れなかった**

> **訂正（2026-07-27）**: P3-3 の行は「doctor は `host_config_findings()` を呼ぶだけ」と
> 記しているが、実際に配線されたのは `configfile.layer_findings`（層契約）だけで、
> host.yaml のキー・型の検査は doctor から呼ばれていなかった——「doctor は緑なのに
> 起動時は警告」が、この行が避けようとしていたまさにその形で残っていた。
> **`doctor_host_config_findings()` として配線済み**（[積み残し棚卸し §2c-3](2026-07-27-post-canary-backlog.md)）。
> 起動時と同じ強度（warn）で、所見ごとに題を分けて内訳が残る（W→E 昇格の判断材料になる）。

- **W1〜W4（廃止フラグ・成果物リポジトリ側 yaml の残存・旧探索先の設定）は所見化していない。**
  再掲したのは E 系（＝起動が止まる条件）だけ。W 系は起動時に警告が出るので、
  「起動できないプロジェクトの原因を doctor から診断する」という §3.6 の目的からは外れる。
  「設定したのに効いていない」の観測を doctor へ寄せたくなったら足す。

**ガイド改訂（P3-2）で行き場が無くなった改善案**

旧 `multi-pc-operations.md` は「必要になったときだけ入れる小さな改善」を本文に抱えていた。
ガイドは運用手順の場なので、改善案だけをここへ移す（**現行実装でも成立することは確認済み**）。

| # | 内容 | 規模 |
|---|---|---|
| G-1 | **run アーカイブの突合** — dashboard の `listArchivedRuns` はエンジン状態と突合せず「live に無いアーカイブ」を全部表示する。他の PC で消した run が `archived` バッジ付きで残り続ける（上限 100 件で自然消滅）。アーカイブ一覧を backlog/archive の run-id と突合して、存在しない run を出さないようにする | M |
| G-2 | ~~**判断待ちの復活ループを塞ぐ条件**~~ → **決着（2026-07-27）: 実装済み**。①`_take_local_on_conflict` に「リモートの削除に決定記録（`decisions/<id>.md`）が伴うなら削除に従う」を足した。②`ensure_needs` は、人の DR が判断待ちでない status を指しているのに手元がまだ判断待ちなら再投影しない（`_already_decided`）。判断待ちへ入れた側は `needs_dr` の印を押すので、決定の**後で**機械が改めて止めた場合は従来どおり自己修復する。孤児票側は `f9d7081` の `reconcile_needs` で解消済み | 済 |
| G-3 | **死活判定の閾値（要確認・低）** — 旧ガイドは「`instances/` の心拍と `status.json` の `fresh_after_sec` で鮮度窓が違い、長い LLM ステップ中に『別マシンで稼働中』と誤表示する」と記していた。常駐一本化で生存の根拠は `engine/status.json` と `status/<node>.json` へ移り、読み手の既定も 120 秒に揃っている。**現状で実害が残るかは未確認**——誤表示を実際に見たら調べる | S |

`flow-archive/` の所有者不在（旧ガイドが「A. 恒久修正」として挙げていたもの）は §7.6 の
「直していないもの」に、より正確な形で記録済み。


### 7.8 P2 の実施で分かったこと（2026-07-26）

P2（§7.3）の 5 件を実施した際に見つけたもの。直したものは根拠、直していないものは
なぜ P2 でやらないかを書く（§7.6 と同じ流儀）。詳細は
[P2 詳細設計 §7・§9.2](2026-07-26-p2-contract-consolidation-detailed-design.md)。

**直したもの**

| 内容 | 直し方 |
|---|---|
| **agent-amigos が `board_eligible` に `agent_cli` を渡していなかった** — `eligible` の CLI 判定は fail-close なので、`requires.agent_cli` を持つ公示に **amigos ノードは永久に入札しない**。`daemon.agent_cli` は存在し、`assign.py` は同じ値を畳んで使っているので単なる渡し忘れ。例外も警告も出ず、板の側からは「誰も手を挙げない公示」に見えるだけになる | `_node_board_declaration` を新設し、`agent_cli`（スカラを 1 要素へ畳む）・`workloads`・`max_concurrent` をまとめて解決して渡す。後 2 者はノードの性質なので正典は host.yaml（agent-flow と同じ判断） |
| **`unittest discover` が 4 ファイル 31 件を集めていなかった** — `resident` の単体テスト（scheduler / supervisor / worker / status）はモジュール直下の `def test_*` で書かれており、`discover` は `TestCase` サブクラスしか集めない。**CI で緑とも赤とも報告されていなかった**（`if __name__` の手動実行でしか走らない）。P3-1 で「4 パッケージの単体テスト」と記録したが、実際にはここが抜けていた。しかも中身は P0-1（SIGTERM 窓）と P2-3（ワーカープール）がまさに触る場所 | 標準の `load_tests` プロトコル + `FunctionTestCase` で拾う（`tools/agent-project/tests/_functest.py`）。**pytest は足さない**——stdlib だけで走るのがこのリポジトリのテストの規約で、CI もそれ前提。P2 の護りがこの 4 ファイルへ入るので、**測れない場所に護りを置かない**ために先に直した |
| **同じ規則の 2 実装がもう 1 組**（`agentcore.repolocal.normalize_repos` と `resident_cli._normalize_host_repos`）— mapping+dict 形の `local: null` が後者では `"None"` という**文字列のパス**になる。`agentcore.repolocal` は「同じ宣言が経路によって違って読める」を潰すためのモジュールなので、その読み方を持ち直すのは動機に反する | agent-project 側を薄い皮にした |
| **検証プロンプトの `workspace.url` 空値の描画が 2 経路で違う** — 組み込みは `or` で既定へ落ちるが、スキルは `get(k, 既定)`。`verifier_input` は `url` キーを常に（空文字でも）入れるので、**スキル経路だけ空欄**になっていた。P1-1 が「見出しと順序を揃える」とした意図の取りこぼし | `DIFF_CRITERION` の正典化と同じコミットで揃えた（同じファイルの同じ関数なので、割ると読み手が 2 度同じ場所を読む） |
| **`workloads` の綴り間違いは型検査を通る** — `[flwo]` は配列で文字列なので型検査に掛からず、結果は「板の仕事を 1 つも受けない」。`agent_cli` のスカラ分解（P1 §7-C）と同じ無言の不参加 | `host_config_findings` へ**語彙の検査**を追加（`flow` / `amigos` 以外は所見）。`budget.max_concurrent` の型検査も同時に |
| **板の `status.state` を `is_terminal`（正典のみ）で読んでいた** — 語彙統一（W0-9）より前のノードが書いた `canceled`（米式）を終端と読まないので、そのノードが落札したまま消えた委譲を「実行中」と読み続ける（枠が永久に空かない）。`is_terminal_read` が存在する理由がまさにこれ | 板の値を読む箇所を `is_terminal_read` へ。**書きは正典のみ**のまま（読みは寛容・書きは厳格） |

**直していないもの（報告）**

- **明示指定の node_id が `normalize_node_id` を通らない**（中）。P0-3 が 1 本にしたのは
  未宣言時の**導出**だけで、agent-flow / agent-amigos の `--node-id`・設定値・
  `$AGENT_AMIGOS_NODE`・`node.json` は素通し。正典構成では常駐体が正規化済みの値を
  `--node-id` で渡すので出ないが、人が直接叩くと板に 2 名義が並ぶ。
  **大文字を含む id を明示している PC では名義が変わる**ので、P0-3 と同じ静止点扱い
  （`doctor --node-id-cutover` の検査対象追加と切替ガイドへの追記が要る）。
  次の静止点（R2b のフリート更新）に相乗りさせる。§3 へ転記済み。
  なお、これと重なると実害が出る側——**板レイアウトの名義綴りが flow だけ別実装**
  （入札を `safe_name`（`-`）で書き `gitbus._safe`（`_`）で読む＝手動入札の受け皿が
  効かない）——は P2-5 で塞いである。
- **スキーマがどのテストでも検証されていない**（中）。`jsonschema` 依存が無いため、
  `board.schema.json` の `$defs.node.repos` が実装と食い違っていたことを機械では
  検出できなかった（人が読み比べて初めて分かった）。契約の食い違いはこれで 2 度目なので
  §3 へ拾う契機付きで転記した。**入れるならまず 1 回流して既存の違反量を見てから**
  （いきなり必須ゲートにすると赤で埋まる——P3-1 の C-4 と同じ判断）。
- **手元で `npm test` を回すには `npm install --omit=dev` が先に要る**（低）。
  `nodeRepos.js` が `yaml` を require するため。CI は先に入れているので緑だが、
  README と各詳細設計の「まとめて回す」手順にはその 1 行が無い。次に手順を触るときに足す。
