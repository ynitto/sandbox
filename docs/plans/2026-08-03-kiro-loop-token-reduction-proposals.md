# kiro-loop トークン削減 — rtk / caveman 導入計画

> 作成日: 2026-08-03
> v3: スコープを rtk / caveman に限定。Phase 1 は **kiro-loop 無改修** かつ
> **agent-dashboard から設定可能** を成立条件とし、一発適用のパッチスクリプトを同梱。
> 将来拡張（kiro-loop 改修あり・ツール非依存の汎用化）は後半にまとめる。
> 対象: `tools/kiro-loop/`, `install.py`, agent-dashboard, `schemas/`

---

## 1. 背景・方針

kiro-loop は kiro-cli を長寿命 tmux ペインで常駐させ定期プロンプトを送り続けるため、
累積トークンコストが大きい。導入検討済みの rtk / caveman はどちらも kiro-cli には
効いていない（rtk: `rtk init` に kiro プロファイルなし / caveman: スキルは入るが
有効化されず、`fresh_context` の `/clear` でも解除される）。

本計画の方針:

1. **スコープは rtk / caveman の 2 ツールに絞る**（キャッシュ等の他施策は別提案へ分離 → §6）
2. **Phase 1: kiro-loop を改修しない**。既存の設定契約だけで動かし、
   **agent-dashboard から ON/OFF・変更できる**ことを最初から成立させる
3. 将来トークン削減ツールが入れ替わることを前提に、**設定経路は agent-flow 等と
   同じ汎用契約**（ツール名を知らない経路）に乗せる
4. 一発で適用できる**暫定パッチスクリプト**を用意する（rtk / caveman 特化で構わない）
5. **将来拡張**は kiro-loop 改修を許容し、rtk / caveman にとらわれない汎用設計で計画する（§5）

| 消費経路 | 対応 | 標榜削減率 |
|---|---|---|
| ツール実行の出力（git / pytest / ls …） | rtk | 出力の 60〜90% |
| エージェントの応答出力 | caveman | 約 65% |

---

## 2. Phase 1 の構成（kiro-loop 無改修）

kiro-loop が**既に読んでいる** 2 つの設定面だけを使う。本体コードには一切触れない。

```
                    ┌────────────────────────────┐
  agent-dashboard ──┤ ~/.agents/session/session.json      │ ← ON/OFF の正（編集 UI 既存）
  （既存の編集UI）   │   (agent-session-commands 契約)      │
                    └──────────────┬─────────────┘
                                   │ pull（kiro-loop 実装済み）
                                   ▼
     ペイン起動時: chat コマンド送信（/caveman full, rtk 前置指示）
                                   │
                    ┌──────────────┴─────────────┐
                    │ ~/.kiro/agents/kiro-loop-concurrency.json │ ← /clear 補強
                    │   hooks.userPromptSubmit（パッチが追記）   │   （session.json に従属）
                    └────────────────────────────┘
```

### 2.1 設定経路: agent-session-commands 契約（ON/OFF の正）

`schemas/agent-session-commands.schema.json` は dashboard が編集し kiro-loop が
セッション開始時に実行する既存契約（pull 型・原子書換・revision・status 相乗り）。
ここに 2 エントリを追加する:

```jsonc
// ~/.agents/session/session.json
{ "id": "tokred-caveman", "mode": "chat", "run": "/caveman full",
  "when": { "engines": ["kiro-loop"], "agent_cli": ["kiro"] } },
{ "id": "tokred-rtk", "mode": "chat",
  "run": "今後シェルコマンドを実行するときは `rtk` を前置してください（例: rtk git status）。rtk 非対応・失敗時はそのまま実行して構いません。",
  "when": { "engines": ["kiro-loop"], "agent_cli": ["kiro"] } }
```

- **dashboard の既存 session-commands 編集 UI がそのまま設定画面になる**
  （エントリ削除 = 無効化、`run` 編集 = レベル変更、`when` 編集 = 適用範囲変更）
- 契約自体はツール名を知らない。将来 rtk / caveman を別ツールへ替えるときも
  **エントリを差し替えるだけ**で、エンジン側の変更はゼロ（方針 3 を満たす）
- `when` で kiro-loop × kiro に絞ってあるため、agent-flow / claude 等には影響しない

### 2.2 /clear 補強: エージェント定義 hooks（session.json に従属）

session-commands はペイン起動時の 1 回だけなので、`fresh_context` の `/clear` で
効果が消える。kiro-loop を改修せずにこれを塞ぐため、終了検知で実績のある
エージェント定義 `~/.kiro/agents/kiro-loop-concurrency.json` の hooks に
`userPromptSubmit`（毎プロンプト発火）で短い指示を再注入するエントリを足す。

二重管理を避ける要点 — **hook コマンドは session.json を参照し、
対応する tokred-* エントリが存在するときだけ発火する**:

```sh
sh -c 'f="${AGENT_SESSION_DIR:-$HOME/.agents/session}/session.json" && [ -f "$f" ]
       && grep -q tokred-caveman "$f" && ! grep -Eq "\"enabled\":[[:space:]]*false" "$f"
       && cat "$HOME/.kiro/cache/token-reduction/caveman-preamble.md" || true'
```

- dashboard がエントリを消す／契約全体を `enabled: false` にする → hook も沈黙する。
  **スイッチは session.json の 1 箇所のまま**
- rtk 側の hook は `command -v rtk` も確認し、バイナリ不在なら指示を出さない
- 注入ペイロードは `~/.kiro/cache/token-reduction/*.md`（数行の短文。
  userPromptSubmit の毎回コストは数十トークンで、削減額に対し無視できる）
- `stop` hook（`kiro-loop slot-release`）には触れない
- `--inject agentSpawn` でセッション生成時 1 回だけの注入にも切替可
  （`/clear` 後の再発火が実機で確認できればこちらが低コスト）

### 2.3 パッチスクリプト（実装済み・暫定）

`tools/kiro-loop/setup-token-reduction.py` — rtk / caveman 特化の一発適用口:

```
python tools/kiro-loop/setup-token-reduction.py            # 適用（冪等）
python tools/kiro-loop/setup-token-reduction.py --status   # 適用状態の表示
python tools/kiro-loop/setup-token-reduction.py --revert   # 全撤去
python tools/kiro-loop/setup-token-reduction.py --dry-run  # 変更内容の確認のみ
  --caveman-mode lite|full|ultra   --inject userPromptSubmit|agentSpawn
  --skip-rtk   --skip-caveman
```

- やること: (1) ペイロード配置 (2) エージェント定義 hooks 追記 (3) session.json への
  tokred-* エントリ追加（revision++、原子書換、初回のみ `.tokred-backup` 退避）
- 冪等（再実行で差分なし）。`--revert` は自分が追加した分だけを正確に外す
  （hooks は `token-reduction` を含むコマンドのみ削除、`stop` は保全）
- rtk バイナリ / caveman スキルの導入自体は `install.py`（`setup_rtk` /
  `setup_caveman`）の責務のまま。スクリプトは不在を警告しヒントを出すだけ
- **暫定ゆえの特化**: tokred-* という ID 規約・rtk / caveman 決め打ちのペイロードは
  恒久対応（§5）で汎用契約に置き換える前提

### 2.4 Phase 1 の割り切り（既知の制約）

| 制約 | 内容 | 恒久対応 |
|---|---|---|
| hook はファイルレベル設定 | hooks の追記自体は dashboard から編集できない（発火条件が session.json に従属するため実害は ON/OFF 不能ではなく「撤去にスクリプトが要る」こと） | §5.2 agent 定義の再生成 |
| ユーザー独自 agent | `kiro_options.agent` 指定時は concurrency agent が使われず hooks 補強が効かない（session-commands 経路のみ有効） | §5.2 派生 agent 生成 |
| rtk は指示ベース | モデル従順性依存で削減率が安定しない。決定的な PATH shim はペイン env 注入が必要で kiro-loop 改修になるため Phase 1 では見送り | §5.2 env 注入 |
| grep ベースの従属判定 | hook の session.json 参照は `when` 条件までは評価しない簡易判定 | §5.1 契約の一本化 |
| 反映タイミング | session.json の revision を上げても既存ペインには遡及しない（反映はペイン再起動から） | 現契約の仕様どおり |
| hooks トリガーの実機検証 | `userPromptSubmit` / `agentSpawn` の発火仕様（`/clear` 後の挙動含む）は stub でなく実機で確認する。導入直後に `--status` とペインログで確認 | — |

---

## 3. Phase 1 の導入手順

1. 前提導入（未済なら）: `python install.py --agent kiro`（rtk バイナリ / caveman スキル）
2. `python tools/kiro-loop/setup-token-reduction.py --dry-run` で変更内容を確認
3. `python tools/kiro-loop/setup-token-reduction.py` で適用 → `--status` で確認
4. kiro-loop のペインを再起動（次のペイン生成から反映）
5. 実機検証: ペインで `/caveman` の効き・`/clear` 後の hook 再注入・rtk 前置の実施率を確認
6. 以後の ON/OFF・調整は dashboard のセッション開始コマンド編集（tokred-* エントリ）で行う

効果計測は導入前後 1 週間を `tools/kiro-log-exporter` 集計と `rtk gain` で比較する。

---

## 4. 外部向け出力の品質ガード（Phase 1 から必須）

MR コメント・Issue 報告など**人が読む成果物に caveman 文体を混入させない**。
Phase 1 ではペイロード内の指示で担保する（caveman-preamble.md に
「人間へ提出する成果物は通常の文体で書く」を明記済み）。タスク単位で確実に
外したい場合は該当プロンプトの文面に `/caveman off` を前置する運用とし、
per-prompt オプション化は §5.2 で扱う。

---

## 5. 今後の拡張計画（kiro-loop 改修あり・ツール非依存）

Phase 1 が「既存契約への相乗り + 特化パッチ」であるのに対し、恒久対応は
**「トークン削減ツールを差し替え可能な部品として扱う汎用機構」**として設計する。

### 5.1 汎用契約 `agent-tuning` の新設

rtk / caveman という語彙を持たない、注入と環境の宣言だけからなる契約を
`schemas/agent-tuning.schema.json` として追加する（agent-instructions /
agent-session-commands と同じ流儀: pull 型・原子書換・revision 単調増加・
agent-control status への applied 相乗り・**委譲先ノードへ伝播しない**）:

```jsonc
// $AGENT_TUNING_DIR（既定 ~/.agents/tuning/）の tuning.json
{
  "version": 1, "revision": 5, "enabled": true,
  "injections": [        // コンテキスト注入の宣言（ツール名を知らない）
    { "id": "style-compress", "trigger": "every_prompt",   // session_start | every_prompt
      "source": { "type": "file", "path": "~/.agents/tuning/payloads/style.md" },
      "when": { "engines": ["kiro-loop"], "workloads": ["routine"], "agent_cli": ["kiro"] } }
  ],
  "env": [               // ペイン環境の宣言（PATH shim 等を決定的に効かせる）
    { "id": "cmd-wrapper", "path_prepend": ["~/.agents/tuning/shims"],
      "vars": { "SOME_FLAG": "1" }, "when": { "engines": ["kiro-loop"] } }
  ],
  "profiles": {          // ワークロード／プロンプト単位の上書き
    "default": { "injections": ["style-compress"], "env": ["cmd-wrapper"] },
    "external-facing": { "injections": [] }        // 外部向け文章タスク用（品質ガード）
  }
}
```

- caveman は `injections` の 1 エントリ、rtk は `env`（shim）+ `injections`（指示）の
  エントリに**降格**する。ツール交替は payload / shim の差し替えだけで完結
- dashboard には tuning 編集画面を追加（グローバル設定ページの並びに置く。
  applied revision の突き合わせで各ノードの反映状況を可視化）

### 5.2 kiro-loop 改修項目（汎用機構の実装点）

| # | 改修 | 内容 | 効果 |
|---|---|---|---|
| 1 | **`/clear` 後の再適用** | `_dispatch_prompt` の `should_clear` 分岐で、chat モードの session-commands と `session_start` injections を再適用 | hooks 補強（grep 従属判定）が不要になり、Phase 1 の暫定構造を解消 |
| 2 | **エージェント定義の再生成** | `kiro-loop-concurrency.json` の所有を install.sh から kiro-loop へ移し、テンプレート + tuning 契約から起動時に決定的に再生成。ユーザー独自 agent 指定時は hooks / resources をマージした派生 agent（`<name>-kiro-loop.json`）を生成 | hooks が設定駆動になり dashboard から間接編集可能に。独自 agent の穴も塞がる（agent-instructions 設計時に見送られた「install 所有ファイルの書換」懸念は、所有権の正式移動で解消） |
| 3 | **ペイン env 注入** | `_start_pane` の起動コマンドに tuning 契約の `env`（PATH 前置・変数）を反映 | PATH shim が決定的に効く。rtk に限らず任意のコマンドラッパーで再利用可能 |
| 4 | **per-prompt profile** | kiro-loop.yaml エントリに `tuning_profile: external-facing` を追加し、送信時に profile の injections 差分を適用 | 外部向け文章タスクの品質ガードを設定で強制（§4 の運用対応を置換） |
| 5 | **計測の組み込み** | `rtk gain` / ペイン別トークン集計を node-budget レコードへ相乗りし dashboard で可視化 | 削減効果と品質劣化の監視。ツール交替の判断材料 |

実装順は 1 → 2 → 3（それぞれ独立に価値が出る。4・5 は並行可）。

### 5.3 堅牢性の設計原則（汎用機構に共通）

- **フェイルセーフ**: tuning.json 不在／破損／`enabled: false` は「注入なし」で
  エンジンを止めない（session-commands と同じ）。shim はラップ先バイナリ不在時に素通し
- **品質ガード**: profile による外部向けタスクの注入除外を既定で用意。
  圧縮ツール導入時は必ず opt-out 経路をセットで定義する
- **可観測性**: applied revision + 計測値（削減量／エラー率）を status 相乗りで
  dashboard へ。効果が出ていない・品質が落ちたことに気づける状態を保つ
- **原本保全**: 生成物にはマーカー、書換前バックアップ、`--revert` 相当の撤去手段を常備

### 5.4 パッチスクリプトからの移行

1. §5.2-1〜3 の実装が入った時点で `setup-token-reduction.py --revert` を実行し
   Phase 1 の追記分を撤去
2. 同内容を tuning.json の `injections` / `env` エントリとして dashboard から登録
3. スクリプトは 1〜2 リリース残して DEPRECATED 表示 → 削除

---

## 6. スコープ外（別提案へ分離）

以下は v2 まで本書にあったが、rtk / caveman への集中のため分離した。
効果が fresh_context 頻度・コンテキストサイズの実測に依存するため、
**Phase 1 の計測結果（§3-6）を見てから別提案として起こす**:

- 圧縮済みコンテキストのセッション横断キャッシュ（caveman-compress の成果物共有）
- 定期プロンプトのスキル化による履歴成長の抑制
- `fresh_context_mode: clear | compact`
- ltm-use 連携（セッション知見の要約持ち越し）

---

## 7. リスク・留意点

- **圧縮による情報欠落**: rtk は `tee`（失敗時フル出力保存）と `exclude_commands`、
  caveman はレベル調整と opt-out で緩和。原本・バックアップは必ず残す
- **kiro-cli hooks の仕様**: `userPromptSubmit` / `agentSpawn` の発火仕様と `/clear` 後の
  挙動は実機検証が前提（`stop` のみ実績あり）。検証結果次第で `--inject` の既定を見直す
- **rtk 指示の従順性**: Phase 1 は指示ベースのため削減率が振れる。実測して
  期待値を下回るなら §5.2-3（env 注入 shim）の優先度を上げる
- **設定の一貫性**: Phase 1 の grep 従属判定は簡易（`when` 条件まで評価しない）。
  違和感が出たら恒久対応を待たず hooks を `--revert` して session-commands 単独運用に戻せる
