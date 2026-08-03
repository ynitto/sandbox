# kiro-loop トークン削減 提案（rtk / caveman 連携 + セッション横断キャッシュ）

> 作成日: 2026-08-03（v2: エージェント定義 hooks を統合ポイントの主線に変更、
> agent-dashboard 連携の Phase 分け、キャッシュ効果の構造説明を追記）
> 対象: `tools/kiro-loop/kiro-loop.py`, `tools/kiro-loop/install.sh`, `install.py`, agent-dashboard
> 前提: rtk / caveman は `install.py` で導入検討済み（`setup_rtk` / `setup_caveman`）

---

## 1. 背景・目的

kiro-loop は kiro-cli を長寿命 tmux ペインで常駐させ、定期プロンプトを送り続ける。
定常業務エージェントは 24 時間走るため、1 サイクルあたりの消費が小さくても累積コストが大きい。

rtk / caveman は導入検討が済んでいるが、**kiro-cli（kiro-loop 配下のエージェント）には
どちらも効いていない**のが現状:

| ツール | 現状 | ギャップ |
|---|---|---|
| rtk (rtk-ai/rtk) | `install.py setup_rtk` は `RTK_AGENT_INIT_ARGS = {claude, copilot, codex}` のみ。`rtk init` に kiro プロファイルが存在しない | kiro-cli への連携経路がゼロ |
| caveman (JuliusBrussee/caveman) | `npx skills add -g -a kiro-cli` で `~/.kiro/skills/caveman` に入る | スキルは入るが**有効化されない**。Claude Code のような hooks 自動有効化がなく、セッションごとに `/caveman` を送る必要がある。`fresh_context` の `/clear` でも解除される |

---

## 2. トークン消費の現状分析（kiro-loop 経路）

| # | 消費経路 | 内容 | 対応する案 |
|---|---|---|---|
| 1 | **ツール実行の出力** | git / pytest / ls / grep 等の生出力がそのままコンテキストに載る。エージェントの最大消費源 | 案1 (rtk: 60〜90% 削減を標榜) |
| 2 | **エージェントの応答出力** | 冗長な説明文・前置き・確認文 | 案2 (caveman: 約65% 削減を標榜) |
| 3 | **セッション初期化コスト** | 会話開始時の steering / skills 読込。`fresh_context` の `/clear` のたびに全量再読込 | 案4-A |
| 4 | **定期プロンプト本文** | 毎サイクル同じ長文手順（yaml の `prompt:`）を再送し、会話履歴に積まれ続ける | 案4-B |

---

## 3. 統合ポイント: エージェント定義（hooks）の拡張

### 3.1 現状の仕組み

kiro-loop は終了検知（セマフォ解放）のために既にエージェント定義 + hooks を使っている:

- `install.sh` が `~/.kiro/agents/kiro-loop-concurrency.json` を**静的に生成**
  （`stop` hook → `kiro-loop slot-release`、`resources` に skill:// glob、`tools: ["*"]`）
- kiro-loop はペイン起動時にこのファイルの**存在チェックのみ**行い、
  あれば `--agent kiro-loop-concurrency` を付けて起動する
  （`max_concurrent > 0` かつユーザーが `kiro_options.agent` を指定していない場合のみ）

**この既存の統合ポイントを rtk / caveman の注入口として拡張する**のが本提案の主線。
新しい仕組みを増やさず、既に kiro-loop が所有している「エージェント定義 + hooks」に相乗りする。

### 3.2 hooks で何がどこまでできるか

kiro-cli の agent hooks はトリガーごとに性質が異なる（`stop` は実績あり。他は要実機検証）:

| トリガー | できること | rtk / caveman との関係 |
|---|---|---|
| `agentSpawn` | セッション生成時にコマンド出力をコンテキストへ注入 | caveman プリアンブル / rtk 指示の注入（1 回きり・安い）。**`/clear` 後に再発火するかは要検証** |
| `userPromptSubmit` | 毎プロンプト送信時に出力をコンテキストへ注入 | 短い指示（1〜2 行）なら毎回数十トークンで済み、**`/clear` 耐性が構造的に保証される**。caveman 有効化の確実な受け皿 |
| `preToolUse` | ツール実行前の検証・ブロック | **コマンド書換（rewrite）に対応しているかは未確認**。書換不可なら rtk の決定的適用には使えない |
| `stop` | 応答完了時にコマンド実行 | 既存の slot-release（変更なし） |

整理すると:

- **caveman は hooks 拡張だけで完結できる**（実体が「圧縮文体の指示」であり、注入で足りる）
- **rtk は hooks だけでは「指示」まで**。削減の実体はコマンド置換（`git status` → `rtk git status`）
  なので、決定性が欲しければ PATH shim（後述）の併用が要る。preToolUse で書換が可能と
  実機検証で判明すれば shim を廃止して agent 定義に一本化できる

### 3.3 所有権の移動: install.sh 静的生成 → kiro-loop 起動時再生成

hooks を設定で増減させる以上、静的 heredoc のままでは管理できない。
`kiro-loop-concurrency.json` の**所有を install.sh から kiro-loop に移し、
ペイン起動時（または設定変更検知時）にテンプレート + 設定から決定的に再生成**する。

なお agent-instructions の設計時に「kiro-cli --agent JSON への反映」が一度
見送られた経緯がある（install.sh 所有のファイルをエンジンが書き換える副作用が理由 —
`schemas/agent-instructions.schema.json` の tools 説明に明記）。所有権を kiro-loop へ
正式に移すことでこの懸念自体を解消する（install.sh は初回生成のみ、以降は kiro-loop が正典）。

- 生成物冒頭に「自動生成 — 編集は設定側で」マーカーを維持
- **ユーザー独自 agent（`kiro_options.agent`）指定時**: 現状は concurrency agent が使われず
  hooks が一切効かない。再生成方式なら「ユーザー agent を読み込み、hooks / resources を
  マージした派生 agent（例: `<name>-kiro-loop.json`）を生成して使う」で対応できる

---

## 4. 各ツールの連携案

### 案1: rtk（ツール出力の圧縮）

- **1-A: agent 定義に rtk 指示を注入**（Phase 1）:
  agentSpawn hook（`cat ~/.kiro/cache/rtk-instructions.md`）または `resources` に
  RTK.md 相当（「コマンドには rtk を前置せよ」）を追加。導入は軽いが**モデル従順性依存**
- **1-B: PATH shim（決定性の担保）**（Phase 2）:
  `~/.kiro/rtk-shims/`（git → `RTK_SHIM=1 exec rtk git "$@"` 等のラッパー）を
  ペイン起動時に `PATH` 先頭へ注入（`_start_pane` の起動コマンド組み立てで env 指定）。
  モデルが指示に従うかに依存せず決定的に効く。`RTK_SHIM` ガードで rtk 内部の再帰を防止。
  rtk バイナリが無ければ shim を作らない（素のコマンドへフォールバック）
- **1-C: preToolUse でのコマンド書換**（検証待ち）:
  kiro-cli hooks が tool input の書換に対応していれば shim 不要になり agent 定義に一本化
- `install.py setup_rtk` に kiro 分岐を追加（バイナリ導入 / 指示ファイル配置 / shim 生成 /
  `_rtk_agent_configured` の kiro 用マーカー）
- 情報欠落リスクは rtk の `tee`（失敗時フル出力保存）と `exclude_commands` で緩和

### 案2: caveman（応答出力の圧縮）

- **2-A: agent 定義の hooks で有効化**（Phase 1・主線）:
  - agentSpawn で圧縮プリアンブルを注入。`/clear` 後の再発火が確認できればこれで完結
  - 再発火しない場合は userPromptSubmit に短い指示を出す（毎回数十トークンの固定費で
    `/clear` 耐性を構造的に保証）。どちらが成立するかを最初の実機検証項目にする
- **2-B: agent-session-commands（chat モード）での `/caveman full` 送信**（受け皿）:
  既存経路（`_send_session_chat_commands`）で今日から設定のみで有効化できる。
  ただし `/clear` 後に解除される穴があるため、`_dispatch_prompt` の `should_clear` 分岐で
  **chat モード session-commands を再適用する**処理（+数行）を併せて入れる。
  ユーザー独自 agent 指定時（hooks が効かない構成）の受け皿としても維持
- **2-C: per-prompt の opt-out**:
  MR コメント返答など**外部向け文章を書くタスクに caveman 文体は不適**。
  yaml エントリに `output_style: caveman | normal` を追加し、送信前に
  `/caveman <mode>` / `/caveman off` を 1 行前置する

---

## 5. agent-dashboard 連携（Phase 2）

Phase 1 は設定ファイルの手動編集（agent JSON テンプレート / session.json / steering）で
運用開始し、Phase 2 で dashboard から変更できるようにする。既存契約と同じ流儀
（**pull 型・原子書換・revision 単調増加・agent-control status への applied 相乗り**）に乗せる:

- **新契約 `agent-tuning`**（`$AGENT_TUNING_DIR`、既定 `~/.agents/tuning/tuning.json`）:

  ```jsonc
  {
    "revision": 3,
    "rtk":     { "enabled": true, "mode": "shim",        // shim | instruct | off
                 "exclude_commands": ["pytest"] },
    "caveman": { "enabled": true, "mode": "full",        // lite | full | ultra
                 "inject": "agentSpawn" },               // agentSpawn | userPromptSubmit | session-command
    "cache":   { "enabled": false }                      // 案4-A（計測後に判断）
  }
  ```

- kiro-loop は起動時 + 定期ポーリングで tuning.json を読み、
  **エージェント定義の再生成（§3.3）と shim 生成に反映**。適用済み revision を
  agent-control status に相乗りさせ、dashboard 側で適用状況を確認できるようにする
- caveman の on/off/level だけなら、dashboard が既に所有する
  **agent-session-commands の編集 UI** でも管理可能（2-B 経路）。tuning 契約の実装前の
  つなぎとして使える
- session-commands と同様、**委譲先ノードへは伝播しない**（副作用のある設定は
  ノードローカルに閉じる）。schema は `schemas/agent-tuning.schema.json` として追加

---

## 6. 案4: プロンプト・スキルのセッション横断キャッシュ

### 6.1 まず効果の構造を明確にする（v2 追記）

このキャッシュは **API のプロンプトキャッシュ（KV キャッシュ / cache_read 割引）とは別物**。
プロバイダ側の課金割引を作る仕組みではなく、セッションを跨いで LLM の推論状態を
持ち越すこともできない。効果の源泉と、キャッシュの役割を分けて説明する:

- **削減の源泉は「圧縮」**: steering / SKILL.md を caveman-compress で約46% 縮めれば、
  モデルがそれを読むたびに 46% 分の入力トークンが減る。キャッシュ自体は 1 トークンも減らさない
- **キャッシュの役割は「圧縮コストの償却」**: 圧縮自体が LLM 呼び出し（コスト）なので、
  毎セッション圧縮したら本末転倒。content-hash キャッシュにより
  **圧縮は元ファイル変更時の 1 回だけ**になり、成果物を全ペイン・全セッションが使い回す。
  「セッションを跨いで流用」の意味はここ（圧縮済み成果物の共有）であって、
  コンテキストそのものの持ち越しではない

**効果が出る条件**（導入判断はここで決まる）:

```
削減量/日 ≈ (steering+skills の読込トークン × 圧縮率 46%) × 読込回数/日
読込回数/日 = ペイン生成回数 + /clear（fresh_context）回数
```

例: 読込 8k トークン・3 ペイン・fresh_context 60 分 → 72 回/日 × 3.7k ≈ **265k トークン/日**。
一方 `/clear` を使わない長寿命ペインなら読込は起動時の 1 回きりで、効果はほぼ出ない。
つまり**効果は fresh_context の頻度と steering/skills のサイズに比例**する。
現行運用の実測値（`tools/kiro-log-exporter` で読込サイズ × /clear 頻度を 1 週間計測）を
見てから 4-A の導入可否を判断する。**計測が先、実装は後**。

### 案4-A: 圧縮済みコンテキストキャッシュ（計測後に判断）

```
~/.kiro/cache/compressed/
  <sha256(元ファイル)>.md     # 圧縮済み steering / SKILL.md / prompt 本文
  index.json                  # 元パス → hash, mtime, 圧縮日時
```

- kiro-loop 起動時／`install.py` 実行時に mtime + hash で差分検知し、変更分のみ再圧縮
- 参照切替: steering は圧縮版を配置（原本は `.src/` に退避）、skills は agent 定義の
  `resources` glob をキャッシュ側へ向ける（§3.3 の再生成に相乗り）
- index 破損時は原本使用へフェイルセーフ（agent-session-commands と同じ流儀）

### 案4-B: 定期プロンプトのスキル化 + 短縮呼び出し（キャッシュ不要・無条件で有効）

毎サイクル同じ長文手順を送ると、会話履歴に毎回積まれ、**以降の全ターンで履歴として
再送され続ける**（N サイクルで累積 O(N²) 的に効く）。yaml の手順をスキルへ移して
定期プロンプトを 1 行にすれば、履歴の成長自体が抑えられる:

```yaml
prompts:
  - name: "MR コメント返答"
    prompt: "/mr-reply-worker を実行"        # Before: 毎サイクル約500字の手順書
    fresh_context_interval_minutes: 480      # スキル本文の読込は 8 時間に 1 回
```

例: 500 字（≈300 トークン）× 60 分間隔・24 サイクル/clear なら、履歴再送分だけで
約 90k トークン/日/ペインの抑制（プロバイダ側 prompt cache の割引が効いている場合は
実効値はこれより小さい）。**キャッシュ機構なしで成立するため、案4 の中では最優先**。

### 案4-C: `fresh_context_mode: clear | compact`（オプション）

`/clear`（全破棄→全再読込）の代わりに `/compact`（会話要約）を選べる per-prompt
オプション。`/compact` 自体にもコストがかかるため既定は `clear` のまま、
「毎回ほぼ同じ作業をする長寿命ペイン」でだけ選ぶ。

### 案4-D: ltm-use 連携（発展）

stop hook でセッションの知見を ltm-use へ保存し、次セッションでは圧縮済みサマリのみを
agentSpawn で注入する。ltm-use v5 (brain) 設計と接続する長期案。

---

## 7. 効果試算（各ツールの標榜値ベース）

| 施策 | 対象 | 標榜削減率 | 備考 |
|---|---|---|---|
| 案1 (rtk) | ツール出力 | 60〜90% | 出力削減であり請求額の削減率はこれより低い |
| 案2 (caveman) | 応答出力 | 約65% | 外部向け文章タスクは opt-out 前提 |
| 案4-A (compress cache) | 入力（steering/skills） | 約46% × 読込回数 | **fresh_context 頻度に比例。計測が先** |
| 案4-B (プロンプトのスキル化) | 入力（履歴再送） | サイクル毎の累積分 | キャッシュ不要・無条件で有効 |

実測は導入前後 1 週間の `tools/kiro-log-exporter` 集計と `rtk gain` で行い、
node-budget レコードに載せて dashboard から見えるようにする。

---

## 8. 実装ステップ（推奨順）

| Phase | 内容 | 変更方法 | 変更量 |
|---|---|---|---|
| **1（手動・即効）** | エージェント定義の hooks 拡張（caveman: agentSpawn or userPromptSubmit 注入 / rtk: 指示注入）+ `/clear` 後の session-commands 再適用（+数行）+ 案4-B のプロンプトスキル化。**先行して hooks の実機検証**（agentSpawn の `/clear` 後再発火・preToolUse の書換可否） | 設定ファイル手動編集（agent JSON / session.json / steering） | 極小〜小 |
| **2（dashboard 連携）** | `agent-tuning` 契約の新設 + kiro-loop の agent 定義再生成（§3.3、所有権移動込み）+ rtk PATH shim + `install.py setup_rtk` kiro 分岐 + `rtk gain` 計測 | dashboard の設定 UI から revision 付きで配布 | 中 |
| **3（計測後）** | 案4-A 圧縮キャッシュ（1 週間の実測で読込トークン × /clear 頻度が閾値を超えた場合のみ） | tuning 契約の `cache.enabled` | 中 |
| **4（検証後）** | 案1-C preToolUse 書換への一本化 / 案4-C fresh_context_mode / 案4-D ltm-use 連携 | — | 中 |

---

## 9. リスク・留意点

- **圧縮による情報欠落**: rtk 出力・caveman 圧縮で手順やエラー詳細が落ちるリスク。
  rtk は `tee` + `exclude_commands`、caveman はレベル調整と per-prompt opt-out で緩和。原本は必ず残す
- **外部向け出力の品質**: MR コメント・Issue 報告など人が読む文章には
  `output_style: normal` を既定にする
- **kiro-cli hooks の仕様差**: `stop` 以外のトリガー（agentSpawn / userPromptSubmit /
  preToolUse）の挙動、`/clear` 後の再発火、tool input 書換可否は **Phase 1 冒頭で実機検証**する
- **agent 定義の所有権**: install.sh → kiro-loop への移動を明示し、自動生成マーカーと
  「編集は tuning 設定側で」の導線を残す（過去に agent-instructions で見送られた
  懸念への回答として §3.3 に記載）
- **キャッシュの stale 化**: content-hash + mtime の index、破損時は原本へフェイルセーフ
- **ユーザー独自 agent との干渉**: 派生 agent 生成（§3.3）または session-commands 経路（2-B）で受ける
