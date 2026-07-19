# Agent Dashboard: グローバル指示（agent-instructions）設計

> 日付: 2026-07-19
> 対象: `tools/agent-dashboard/` `tools/agent-flow/` `tools/agent-project/` `tools/kiro-loop/` `tools/agent-loop/` `schemas/`
> 関連: [`2026-07-19-agent-dashboard-orchestration-token-budget-design.md`](2026-07-19-agent-dashboard-orchestration-token-budget-design.md)（agent-control 契約）・
> [`../designs/agent-cli-plugin-design.md`](../designs/agent-cli-plugin-design.md)（drop-in 契約）・
> [`../designs/node-federation-design.md`](../designs/node-federation-design.md)（スキル実体の配布）

## 背景と目的

各エンジン（agent-project / agent-flow / kiro-loop / agent-loop）で動くエージェント CLI へ、
**ノード共通の指示**（使うべきスキル・ツールの方針・行動規範などの指示文）を注入したい。
CLAUDE.md / AGENTS.md のような「エージェントへの常設指示書」に相当するが、次の 2 点が異なる。

1. **agent-dashboard から動的に変更できる** — ファイルを直接編集して回るのではなく、
   管理面で編集すると稼働中のエンジンに順次反映される。
2. **委譲した別ノードにも伝播する** — agent-flow の GitBus で他マシンのワーカーへ
   タスクが渡るとき、共通指示も（軽量な形で）一緒に届く。

現状、この層は存在しない。近いものはあるが、いずれも目的が違う。

| 既存機構 | 範囲 | 内容 | 動的変更 | 委譲伝播 |
|---|---|---|---|---|
| `charter.md` / `rules.md` / `brief/`（agent-project） | **プロジェクト単位** | 定義・規則・run 制約 | ファイル編集（dashboard の authoring あり） | ○（`build_request` で request 文字列に畳み込み） |
| `control.json`（agent-control） | ノード横断 | **どの CLI / モデルを使うか**の選択のみ。指示文は運ばない | ○（pull 型・revision 付き） | —（各ノードのローカル契約） |
| `agents/<name>.json`（agent-cli drop-in） | ノード横断 | CLI の起動方法（argv / env / エラー分類） | ○（dashboard の drop-in 編集） | — |
| kiro-cli `--agent <name>.json`（kiro-loop / agent-loop） | ループ 1 系統 | prompt / tools / resources(skill) / hooks | インストール時生成・ペイン起動時に固定 | — |
| `withInputAssist`（dashboard cowork） | dashboard 発の起動のみ | 固定文の前置 | コード埋め込み | — |

本設計は、この空白を **`agent-instructions`** という新しいデータ契約で埋める。
本リポジトリの原則（**結合はデータ契約のみ・pull 型・原子書換・エンジンは単純、知能は管理面**）を
維持し、push 型 IPC やエンジン間のコード依存は導入しない。

## 前提となる調査結果

エンジンは注入面から見ると 2 系統に分かれる。

- **プロンプト組立系**（agent-project / agent-flow）: LLM 呼び出しごとに CLI を spawn し、
  指示はすべて**単一のプロンプト文字列**へ連結される（`--append-system-prompt` 相当や
  CLAUDE.md の読込はどの CLI 経路にも存在しない）。組立の急所は
  agent-project `request.py: build_request`（charter / rules / brief をここで畳み込む）と、
  agent-flow `agent.py: execute_agent` → flow-worker スキルの `prompt.py`（payload 渡し）。
- **tmux 常駐系**（kiro-loop / agent-loop）: 長寿命の `kiro-cli chat` ペインへ
  `tmux paste-buffer` でプロンプトを送る。ペインは `--agent <name>.json`
  （`~/.kiro/agents/kiro-loop-concurrency.json`。tools / resources(skill://) / hooks を宣言）
  付きで起動され、install.sh がこのファイルを生成している。

伝播については、agent-flow の run は `runs/<run-id>/meta.json` に `request` / `workspace` /
`references` を持ち、GitBus 構成ではこのディレクトリ一式が他ノードへ同期される。
**ワーカーのプロンプトは各ノードでローカルに組み立てられる**ため、共通指示は
「meta.json に載せてバスで運ぶ」か「全ノードにファイルを配る」のどちらかになる。
前者が brief（run ブリーフ）で実証済みの経路である。

## 検討した案

### 案 A: control.json に指示文フィールドを足す

agent-control は revision・status ハートビート・pull 読取が既に揃っており、相乗りは実装が最小。
しかし agent-control の契約は「**どれを使うか**だけを言う」（選択・ライフサイクル）と定義して
出荷済みで、コンテンツ（指示本文）を混ぜると契約の意味論が濁る。指示文は更新粒度も
編集 UI も異なる。**不採用**（ただし status ハートビートは相乗りする — 後述）。

### 案 B: 各ノードに agent.md を配布（git-file-sync / node federation で同期）

ファイルは人にも読みやすいが、(1) 配布の到達保証がエンジンと独立の同期機構頼みになり
「run 単位の一貫性」（同じ run の全ワーカーが同じ指示で動く）を作れない、(2) 動的変更の
反映確認（どのノードがどの版を適用中か）を別途作る必要がある。**不採用**。
スキル**実体**の配布は引き続き node federation（git-skill-manager）の責務とし、
本契約はスキルを**名前で参照**するに留める。

### 案 C: 新契約 agent-instructions（採用）

`$AGENT_INSTRUCTIONS_DIR`（既定 `~/.agent/instructions/`）の `instructions.json` を正典とする
独立契約。budget（`~/.agent/budget/`）・control（`~/.agent/control/`）・drop-in
（`~/.agent/agents/`）と同じ置き場所の流儀・同じ pull 型・同じ原子書換。
指示文の**委譲伝播だけは agent-flow の meta.json スナップショット**に乗せ、
run 単位の一貫性と GitBus 同期をそのまま利用する。

## データ契約

正典: `schemas/agent-instructions.schema.json`（新設。stdlib の json だけで読める）。
置き場所: `$AGENT_INSTRUCTIONS_DIR`（既定 `~/.agent/instructions/`）の `instructions.json`。
書き手は管理面（agent-dashboard / CLI / 人）のみ・原子書換。読み手は各エンジン。

```json
{
  "version": 1,
  "revision": 5,
  "enabled": true,
  "text": "回答は日本語。破壊的変更の前に必ず既存テストを確認する。…",
  "skills": [
    "karpathy-guidelines",
    { "name": "self-checking", "note": "成果物の提出前に自己評価に使う" }
  ],
  "tools": {
    "allow": ["fs_read", "fs_write", "execute_bash"],
    "deny_note": "外部への push 系操作は人の確認を経る"
  },
  "max_chars": 2000,
  "updated_at": "2026-07-19T12:00:00Z",
  "updated_by": "dashboard"
}
```

| キー | 意味 |
|---|---|
| `revision` | 単調増加。適用状況の突き合わせに使う（agent-control と同じ流儀） |
| `enabled` | false なら全エンジンで完全 no-op（削除せず一時停止できる） |
| `text` | 指示本文（Markdown）。これが主役 |
| `skills` | 推奨スキルの**名前参照**（文字列 or `{name, note}`）。実体は運ばない — 各ノードにローカル存在する場合のみ効く |
| `tools` | `allow`: kiro-cli `--agent` の `tools` へ**強制反映**できる唯一のフィールド（常駐系のみ）。プロンプト組立系では助言テキストになる。`deny_note`: 助言テキスト専用（kiro agent 形式に否定リストが無いため強制はしない） |
| `max_chars` | レンダリング後ブロックの上限（既定 2000・ハード上限 8000。`rules.md` の 1200 上限と同じ発想で、プロンプトを圧迫しない） |

未知キーは無害に無視（additive 進化）。ファイル不在・parse 失敗・`enabled: false` は
すべて「指示なし」と同義で、**エンジンの動作を止めない**（警告ログのみ）。

### レンダリング規則（決定的・共通）

各エンジンは同一の規則で `instructions.json` → テキストブロックに描画する（LLM 不使用・
stdlib のみ。drop-in と同じく**各ツールが自前の小さなレンダラを持つ**）。

```
<!-- agent-instructions rev:5 -->
## 共通指示（agent-dashboard 管理・全ノード共通）
回答は日本語。破壊的変更の前に必ず既存テストを確認する。…

推奨スキル（ローカルに存在する場合のみ適用）:
- karpathy-guidelines
- self-checking — 成果物の提出前に自己評価に使う
ツール方針: 外部への push 系操作は人の確認を経る
```

- 先頭のマーカー行 `<!-- agent-instructions rev:N -->` は**二重注入防止**の判定に使う:
  注入先の文字列に `<!-- agent-instructions` が既に含まれていれば注入しない。
- `max_chars` 超過は末尾切り詰め（`…` 付与）。マーカー行は必ず残す。

### 適用状況ハートビート

新しい status ファイル系統は作らない。エンジンは既存の agent-control
`status/<tool>-<pid>.json`（`additionalProperties: true`）へ
**`instructions_revision_applied`（整数）** を additive に追記する。
dashboard はこれを読み、「rev 5 を配ったが kiro-loop はまだ rev 4」を可視化する。

## 注入点（エンジン別）

対象は「**成果物を作る実行エージェント**」に限定する。エンジン内部のメタ LLM 呼び出し
（planner / evaluator / prioritize / adjudicate / doctor / charter assist など）へは注入しない —
これらは各エンジンが出力形式を厳密に定義しており、共通指示の混入は迷走要因にしかならない。

### agent-flow（伝播の本丸）

1. **run 作成時にスナップショット**: `submit` / `ensure_run` で、投入ノードの
   `instructions.json` をレンダリングして `runs/<run-id>/meta.json` に additive キー
   `instructions: {revision, text}` として保存する。ただし `meta.request` に既にマーカーが
   含まれる場合（上流が注入済み）はスナップショットしない。
2. **ワーカーで注入**: `execute_agent` が flow-worker スキル `prompt.py` へ渡す payload に
   `instructions`（レンダリング済みブロック）を追加し、組み込み fallback プロンプトにも
   同じブロックを前置する。ワーカーは**ローカルの instructions.json を読まない** —
   run の一貫性基準は投入ノードのスナップショット（brief と同じ哲学:
   「全分散ノードがこれを唯一の一貫性基準として遵守」）。
3. GitBus 構成では meta.json がバス同期でそのまま他ノードへ届くため、**伝播のための
   追加機構は不要**。載るのは有界のテキストのみ（≦ max_chars）で軽量。
4. 旧ワーカーは未知キー `instructions` を無視するだけ（互換）。opt-out として
   `agent-flow run --no-global-instructions` を足す。

### agent-project

自前の注入は行わない。act（実作業）は agent-flow へ委譲されるため、上記スナップショットが
そのまま効く（同一マシンで agent-flow が走るので同じ `~/.agent/instructions/` を読む）。
`build_request` に足す案は、agent-flow 側と**二重注入**になるため採らない
（マーカー判定はあるが、責務は一箇所に置く）。優先順位は結果として
**タスク > brief > プロジェクト（charter / rules） > 共通指示**となる — 共通指示は
request 本文より後段（ワーカーのプロンプト組立時）に足される最弱の層で、
プロジェクト規則と矛盾したら負ける。この順序を flow-worker プロンプト内の文言で明示する。

### kiro-loop / agent-loop（tmux 常駐系）

1. **paste 時注入（text）**: `send_prompt` 経路で、ペインごとに「最後に注入した revision」を
   記録し、**未注入または revision が変わったときだけ**次の送信プロンプトの先頭へブロックを
   前置する。長寿命チャットに毎回付けるのは文脈の汚染なので revision 差分のときのみ。
   これで「dashboard で変更 → 次の定期送信 / inbox 配送から反映」の動的性が出る。
2. **ペイン起動時反映（skills / tools）**: install.sh が一度だけ生成していた
   `~/.kiro/agents/kiro-loop-concurrency.json` を、**ペイン起動時の再生成**へ移す。
   再生成時に `instructions.json` の `skills` を `resources`
   （`skill://~/.kiro/skills/<name>/**` 等、既存の探索ルートに沿った glob）へ、
   `tools.allow` を `tools` へマージする（concurrency の hooks は維持）。
   ユーザーが `kiro_options.agent` で自前のエージェントを指定している場合は
   上書きせず、テキスト注入（1.）のみ行う。skills / tools の反映は**ペイン再起動時**が
   反映点になる（動的性の限界として明記。text は paste 時なので即時）。
3. inbox メッセージ（エージェント間メッセージング）には載せない — 受信側ループが
   自ノードの instructions.json で注入するので、メッセージ本文を汚す必要がない。
4. agent-loop へは kiro-loop からのクローン同期で反映する（既存の流儀）。

### agent-dashboard 自身（cowork）

`cowork.js` の `withInputAssist` と同じ位置で、定常業務 / チャット窓の起動プロンプトへ
ブロックを前置する（`withGlobalInstructions`）。dashboard は書き手であると同時に
自分が起動する CLI に対する読み手でもある。

## agent-dashboard 側の変更

orchestration feature（`src/features/orchestration/`）に追加する。budget / control /
drop-in と同じ管理面の一部として扱う。

- **main**: `instructions.js` — `load()` / `save(patch)`（revision 自動インクリメント・
  スキーマ検証・atomicWriteJson）。`control.js` とほぼ同型。
  IPC: `orchestration:instructionsGet` / `orchestration:instructionsSave`。
- **skills 一覧の供給**: 選択候補を出すため、エンジンと同じ探索順
  （`<root>/.github/skills/` → `~/.agent/skills/` → `~/.kiro/skills/`）でスキル名を列挙する
  `orchestration:skillsInventory` を足す（SKILL.md の存在確認のみ。中身は読まない）。
- **renderer**: オーケストレーションタブに「共通指示」カード。
  - `enabled` トグル / 本文 textarea / スキル選択（inventory からチェック + note 編集）/
    `tools.allow` / `deny_note` / `max_chars`
  - **プレビュー**: レンダリング結果ブロックをそのまま表示（エンジンが見るものと同一）
  - **適用状況**: `status/*.json` の `instructions_revision_applied` を revision と
    突き合わせ、未反映のエンジンをハイライト（「kiro-loop はペイン再起動で skills 反映」等の
    注記を出す）

## 不変条件

- **契約のみで結合**: エンジン間・dashboard 間にコード依存を増やさない。レンダラは各ツールが
  stdlib（json / 文字列操作）だけで持つ。PyYAML 等の依存を増やさない。
- **フェイルセーフ**: ファイル不在・破損・disabled は no-op。共通指示のせいでタスクが
  失敗・停止することはない（注入は常に best-effort）。
- **有界**: 注入ブロックは max_chars（ハード上限 8000）で必ず切る。バスに載るのも同じ有界
  テキストのみ。スキル実体・添付は運ばない。
- **決定的**: レンダリング・二重注入判定・切り詰めは決定的（LLM 不使用）。
- **最弱の層**: タスク指示・brief・プロジェクト（charter / rules）と矛盾する場合、
  共通指示が負けることをレンダリング文言でも保証する。
- **メタ LLM 非対象**: planner / 裁定 / prioritize / doctor 等の内部呼び出しへは注入しない。

## 互換性と移行

- 全変更が additive: 新スキーマ・meta.json の新キー・status の新キー・payload の新キー。
  旧エンジン / 旧 flow-worker スキルは未知キーを無視するだけで壊れない。
- `instructions.json` が無いノードは従来どおり動く（インストール手順の変更なし）。
- flow-worker スキルの `prompt.py` は `instructions` payload を扱うよう更新するが、
  未更新でも fallback プロンプト側の注入は効かない旧挙動に留まるだけ（安全側）。

## 段階導入

1. **契約と管理面**: `schemas/agent-instructions.schema.json` + dashboard の編集 UI / IPC /
   プレビュー（書けるだけで読み手ゼロの状態。無害）
2. **agent-flow**: スナップショット + ワーカー注入 + `--no-global-instructions` +
   status 追記（伝播の本丸。agent-project 経由の act はここで自動的にカバーされる）
3. **kiro-loop**: paste 時注入（revision 追跡）+ ペイン起動時の managed agent 再生成 +
   status 追記
4. **dashboard cowork**: 起動プロンプト前置 + 適用状況表示
5. **agent-loop**: kiro-loop からのクローン同期

## テスト

- スキーマ: 例の妥当性・未知キー許容・`skills` の文字列 / オブジェクト混在
- レンダラ（各ツール）: 決定性・max_chars 切り詰め・マーカー保持・空 / 破損ファイルの no-op
- 二重注入: request にマーカーがあるときスナップショットしない / 前置しない
- agent-flow: meta.json スナップショットの有無 / GitBus 同期後のワーカー注入 /
  旧 meta（キー無し）の互換
- kiro-loop: revision 変化時のみ前置される / `kiro_options.agent` 指定時は agent JSON を
  触らない / concurrency hooks が再生成後も残る
- dashboard: save で revision が増える / 適用状況の未反映ハイライト

## 非目標

- **スキル実体の配布**（node federation / git-skill-manager の責務。本契約は名前参照のみ）
- メタ LLM 呼び出し（planner / 裁定 / prioritize / doctor）への注入
- プロジェクト単位・ワークロード単位の指示上書き階層（将来 `workloads:` を additive に
  足せる形にはしてあるが、v1 はノード 1 枚のグローバルのみ。プロジェクト単位は既存の
  charter / rules が担う）
- push 型の即時反映・ペイン再起動なしでの `--agent`（skills / tools）反映
- agent-amigos への展開（status / control と同様に追随可能だが本設計の範囲外）
