# 分散クレジット協調とプロジェクト知識循環 実装計画

> 作成: 2026-07-29  
> 上位コンセプト: [agent-tools コンセプト正典](../designs/agent-tools-concept.md)  
> 対象課題: P1〜P5 / 柱 1・柱 2 / C1・C2・C5・C7・C8  
> 状態: 計画（本書は現行実装を置き換えず、additive に拡張する）

## 1. 目的と設計境界

個人 PC 上のエージェント CLI を実行主体のまま、利用枠をチームの仕事へ協調的に割り当てる。
同時に、各ノードで得た成功・失敗・指摘をプロジェクトの強制可能なルールへ蒸留し、適用結果から
継続的に改善する。共有 API キー、中央スケジューラ、常時稼働 SaaS は導入しない。

守る境界は次のとおり。

- agent-board は契約だけを持ち、実行も落札の中央判断も持たない。
- node-budget の所有者は各ノードであり、他ノードが上限を書き換えない。
- agent-flow は作業を実行するが、プロジェクトルールの正本を持たない。
- agent-project は知識の捕捉・再利用・昇格を担うが、決定的検査は verify / codd-gate へ委ねる。
- git へ出すのは最小化した契約と証跡で、資格情報、ローカルパス、生の会話は共有しない。

## 2. 現実装の棚卸し

| 領域 | 現在使えるもの | 不足しているもの |
|---|---|---|
| ノード分担 | `nodes/<id>.json` の能力宣言、bid / award、claim / lease / fencing、away、ノード直轄 worker | 利用枠スナップショットの共通契約、鮮度・観測不能、残量を考慮した適格性と説明 |
| ノード予算 | `node-budget` v2 のノード内共通 config / append-only ledger、token 推定、workload 配分 | CLI ベンダーの残量取得 adapter、板へ出せる秘匿化要約、予約と実績の照合、組織契約時の管理者向け集約 |
| 知識捕捉 | run ブリーフ、archive、検証証跡、人判断からの learn / avoid、`decisions/` | skill / rule の版、発生ノード、根拠参照、適用範囲を統一して記録する envelope |
| 再利用・昇格 | 類似 learn の自動解決、hit 閾値、`rules.md` 常時注入と自動昇格、linked project、ltm-use | 複数ノード候補の同一性・競合処理、品質効果の評価、反証、失効・退役、昇格監査 |
| 強制と検証 | `rules.md` の prompt 注入、機械 verify、証跡ベース検証、codd-gate | 「注入した」ではなく「適用された」の確認、rule version 固定、機械化可能なルールの gate 化 |
| 人の操作面 | dashboard の検収・needs・board・予算管理の足場 | ノード横断残量の鮮度表示、落札理由、知識候補の比較、昇格 / 保留 / 退役の裁定 UI |

現状にも `learn → rules.md → ltm-use` の縦の経路はある。新しい知識ストアを並立させず、この経路に
provenance、評価、ライフサイクルを足す。node-budget も置き換えず、ローカル台帳から共有可能な
要約を射影する。

## 3. 追加するデータ契約

### 3.1 `node-budget-summary`（ノードが書く射影）

板の `nodes/<id>.json` から参照する、秘密を含まない利用枠の要約を追加する。正本は従来どおり
各ノードの node-budget で、要約は応札判断用の期限付き射影にすぎない。

必須候補フィールド:

- `contract_version`, `node_id`, `observed_at`, `expires_at`
- `source`: `local-ledger | cli-reported | manual | unavailable`
- `capacity`: token または実行時間の `limit`, `used`, `reserved`（未知値は `null`）
- `can_accept`: 所有者ノードが計算した bool と、固定語彙の `reason_codes[]`
- workload ごとの実効上限。金額、契約 ID、ユーザー名、アクセストークンは含めない

他ノードはこの値を再計算せず、期限切れ・未知・契約版不一致を fail-close で非適格にする。ただし
人が明示した無制限は「未知」と区別する。予約は落札時に reservation ID を作り、開始・完了・失効を
append-only に残す。クラッシュ時は lease と同じ有限 TTL で解放する。

### 3.2 `knowledge-observation`（実行が書く観測）

新しい正本を作るのではなく、run ブリーフ / archive / decisions に共通メタデータを持たせる。

- identity: observation ID、project、task、run、発生 node、時刻
- input: `rules.md` の content hash、使用 skill 名と version、関連 learn ID
- outcome: verify verdict、証跡 URI / hash、差し戻し分類、rollback の有無
- candidate: 一般化した guidance、scope、applicability、expiry、provenance ID 群
- privacy: redaction 結果と共有可否。生プロンプトを必須にしない

既存 Markdown を人が編集できる性質を残す。構造化 sidecar を導入する場合も Markdown を正として
一方向に生成し、二重書き込みにしない。

### 3.3 `project-rule` ライフサイクル

`rules.md` の各自動昇格項目に安定 ID と状態を付ける。

```text
candidate → trial → active → deprecated
              └────→ rejected
active → suspended → trial | deprecated
```

- candidate: 捕捉済み、まだ全タスクへ強制しない
- trial: 適用範囲を限定し、必ず評価を採る
- active: 再現した効果と根拠を持ち、全該当タスクへ注入する
- suspended: 悪化または競合を検出し、新規適用を止める
- deprecated / rejected: 履歴は消さず、適用しない

遷移は append-only の decision で説明可能にする。自動遷移は trial までと suspension に限定し、
active への既定経路は既存の hit 閾値に outcome 条件を加える。セキュリティや破壊的操作に関する
ルールは人の承認を要求する。

## 4. 実装フェーズ

### Phase 0 — 観測と互換性の固定

1. 実 fixture を使い、node-budget、board node 宣言、decisions、rules 昇格の現行形式を契約テスト化。
2. `agent-project stats` / doctor に、rule 注入数、learn hit、昇格数、根拠欠落数を読み取り専用で追加。
3. 共有禁止項目の redaction テストを先に追加する。

**完了条件**: 挙動を変えずに基準値が取れ、旧ファイルだけでも全ツールが動く。

### Phase 1 — 分散利用枠の観測

1. `schemas/node-budget-summary.schema.json` を追加し、node 宣言から版付きで参照可能にする。
2. agentcore に要約の検証・期限判定・reason code 語彙を 1 実装で追加する。
3. resident がローカル node-budget から原子的に射影し、board へ publish する。
4. agent-board の eligible に `can_accept` と freshness を加え、決定理由を dry-run / dashboard に返す。
5. CLI ごとの残量取得は任意 adapter とし、取得不能時は台帳推定または `unavailable` に倒す。

**完了条件**: アカウント画面を巡回せず、全ノードの「受けられる / 受けられない / 不明」と鮮度を
一覧でき、期限切れノードへ自動落札しない。

### Phase 2 — 予約による協調分担

1. award と同時に予算 reservation を作成し、同一ノードの並行落札が残量を二重利用しないようにする。
2. agent-flow / amigos / project の開始時に予約を引き継ぎ、実績 ledger と結び付けて close する。
3. 未開始・ノード停止は lease expiry で解放し、回収を冪等にする。
4. 落札タイブレークは既存の決定性を維持し、残量を連続的な優先度にせず適格性と bucket だけに使う。

**完了条件**: 同じスナップショットを読んだノードが同じ落札結果を導き、合計予約が所有者上限を
超えず、プロセス kill 後にも有限時間で再入札できる。

### Phase 3 — 知識観測 envelope

1. agent-flow の run 結果に rule hash、skill version、node、evidence hash を additive に付加する。
2. agent-project が既存の brief / decisions 捕捉時に observation ID と provenance を保存する。
3. 同一 observation の再取込を ID で冪等化し、git merge で順序が変わっても候補集合を同じにする。
4. linked project / ltm へ渡す前に project scope と privacy を検査する。

**完了条件**: 別ノードの結果を発生元まで辿れ、同じ観測を二重に hit 計上せず、秘密を共有しない。

### Phase 4 — 蒸留・強制・継続改善

1. 既存 learn 昇格器に candidate / trial / active と outcome 集計を加える。
2. title 類似だけに依存せず、scope と applicability を先に決定的フィルタし、意味的統合は候補提示に
   留める。競合候補は自動マージせず needs へ送る。
3. active rule の hash を実行要求へ固定し、実行結果で適用版を照合する。
4. 決定的に表せる rule は codd-gate recipe への提案を生成する。生成物は既存 gate のレビューと
   テストを通るまで有効化しない。
5. active 適用後に verify fail / rollback が閾値を超えたら fail-close で suspended にし、人へ
   根拠を一度だけ提示する。期限切れ候補も退役候補にする。

**完了条件**: 「共有された」ではなく「どの実行へ適用され、品質へどう効いたか」を説明でき、
悪化するルールが新規タスクへ適用され続けない。

### Phase 5 — dashboard と運用移行

1. fleet 画面にノードごとの capacity bucket、鮮度、予約、reason code を表示する。
2. board の入札・落札詳細に、能力・利用枠・repo 担当の適格性説明を表示する。
3. knowledge 画面に provenance、適用数、PASS / fail / rollback、競合、状態遷移を集約する。
4. 人の操作は promote / suspend / revise / deprecate の契約投函だけにし、dashboard を第二の書き手にしない。
5. doctor に stale publisher、孤立 reservation、根拠なし active rule、未知 rule hash を追加する。

**完了条件**: 管理者は各 CLI の資格情報を預からずに分担状況を把握でき、ルール管理者は根拠を
別画面へ探しに行かず一度で裁定できる。

## 5. テスト戦略

- **契約テスト**: 新旧 schema、未知フィールドの additive 互換、版不一致の fail-close。
- **決定性テスト**: git 取込順、時計差、同票、再実行が変わっても eligible / award / candidate 集合が同じ。
- **故障注入**: publish 中断、期限切れ、ノード停止、予約後未開始、重複 observation、merge conflict。
- **予算不変条件**: `used + live reservations <= limit`（無制限・未知を別値として扱う）。
- **知識不変条件**: active は provenance と成功 outcome を持つ、suspended は新規要求へ入らない、
  rule hash 不一致は成功扱いしない。
- **プライバシーテスト**: token、ホームディレクトリ、リモート URL の credential、生プロンプトを fixture に
  混ぜ、共有ファイルへ出ないことを確認する。
- **E2E**: 3 ノード（余裕あり / 枯渇 / stale）で落札 → 実行 → 観測共有 → trial → active → 反証 →
  suspended を通す。外部 API は fake adapter とローカル bare git で代替する。

## 6. 移行とロールバック

1. すべての新フィールドは optional で開始し、reader → shadow writer → 観測 UI → enforcement の順に出す。
2. Phase 1 は `budget_summary.enforce: false` を既定にし、観測値と従来落札の差を監査してから有効化する。
3. 知識ライフサイクル導入時、既存の人手 `rules.md` は `active/manual` として保持し、自動退役しない。
4. 新 envelope を読めない旧ノードは入札の contract version で識別し、混在期間は新契約必須の仕事だけを
   fail-close にする。
5. 機能 flag を戻せば従来の node-budget、board、learn 昇格へ戻れる。append-only の reservation / decision /
   observation は削除せず、reader が無視する。

## 7. 非目標と見送り

- 組織の請求 API を一元管理すること、契約枠をノード間で技術的に移転すること。
- 最安モデル選択や金額最適化を中央で行うこと。一次指標は所有者が宣言した利用可能性である。
- 生の会話ログを全社ナレッジとして収集すること。
- LLM が生成したルールを無検証で active や codd-gate にすること。
- スキル配布基盤を置き換えること。スキルの同期・系譜は node-federation に残し、本計画は
  「プロジェクトがいつ使うか」と「使った結果」を接続する。

## 8. 実装順と成果物

| 順 | 主な成果物 | 依存 | 解決する課題 |
|---|---|---|---|
| 0 | 現行 fixture、stats、privacy guard | なし | P4・P5 の基準線 |
| 1 | budget summary schema / publisher / eligible | 0 | P1・P2 |
| 2 | reservation ledger / workload 接続 | 1 | P1 |
| 3 | observation envelope / provenance | 0 | P4・P5 |
| 4 | rule lifecycle / enforcement / suspension | 3 | P3・P4・P5 |
| 5 | dashboard / doctor / 移行完了 | 1〜4 | P1〜P5 の運用 |

Phase 1 と Phase 3 は並行可能だが、各 phase 内は schema と reader を writer より先に実装する。
各 PR は上位コンセプトの課題 ID と原則を明記し、中央調整主体や第二の正本を増やしていないことを
レビューする。
