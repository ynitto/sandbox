# agent-loop: headless エージェント CLI での定常業務実行設計

> **実装状況（2026-08-11）**: 実装計画 1〜6・8・9 を実装済み（`toolloop.py` と
> `test/test_headless_route.py` を新設）。**未実施は 7（tmux tail ウィンドウの自動起動）と
> 10（statemachine への機械層の逆輸入）、および証跡ゲートの判定層**。詳細は
> 「実装計画」末尾の残作業のまとめを参照。

## 背景と課題

agent-loop の実行モデルは「tmux ペインに対話 CLI を常駐させ、定期プロンプトを send-keys で
送る」だが、対話セッションは**会話を人に見せるための可視化手段**であって、実行の必須要件では
ない（2026-08-11 ユーザー指示）。現状は `agents/<name>.json` に `interactive` 節が無い定義
を `agent_cli` に指定すると、デーモンが起動時に `interactive_cmd` の組み立てで fail fast する。
実際に sandbox-test の定常業務（`agent_cli: aider` + gemma4:e4b）がこのエラーで起動できない。

ただし分岐点は `interactive` の有無ではない。定義を並べると、**ツールループを CLI が内蔵するか**
が本当の境目だと分かる。

| CLI | headless command | ツールループ |
|---|---|---|
| claude / codex / copilot / cursor / kiro / opencode | `claude -p` / `codex exec` / `kiro-cli chat --no-interactive` ほか | CLI が内蔵 |
| aider | `aider --message` | 無い（渡されたファイルを編集するだけ） |
| ollama 素 | `agent-ollama {model}` | 無い（純粋な推論） |

対話 CLI 経路で agent-loop が薄くて済んでいたのは、ツールループを CLI 側が持っていたから。
プロンプトを送れば探索も編集もコマンド実行も CLI の中で回って終わるので、agent-loop は
「送って待つ」だけでよかった。ツールループ非内蔵の CLI へ同じ扱いをすると、着手すらしない
（aider は「チャットに入っているファイルしか編集しない」——`aider.json` の errors に登録済み）。

さらに、ツールループを供給しても**受入条件**が無ければ done を機械検証できない。定型業務側の
statemachine ハーネスで偽 done を止めているのは、各 state の Output Contract と証跡ゲート
（この実行で read/write/run していないファイルの自己申告を却下する）であり、定期プロンプトには
その照合相手が無い。実測でも自由文の仕事は受入 2/21 で、狭く仕様された state は 3/3 だった。

柱と原則: 柱3 / C9 — 定常業務を足る最小のローカルモデルへ流せるようにする。
柱2 / C3 — 定期駆動の適用範囲を広げ、人の介入なしで回る仕事を増やす。
C5 — done の根拠を機械検証に置き、ツールループの追加で検証を緩めない。

## 要件

1. ツールループ内蔵の CLI（層2）は headless 1 回で定期プロンプトを実行できる。tmux 常駐を必須
   としない。
2. ツールループ非内蔵の CLI（層3）は、ハーネスがツールループを供給したうえで実行する。
3. 層の判定は `agents/<name>.json` の**宣言**による。定義の他フィールドからの推測はしない。
4. 層3 の定期プロンプトは受入条件（`acceptance`）を前提とする。無い entry は**警告して実行し、
   「検証なし」として記録する**（done の根拠にしない）。層2 の entry では警告しない。
5. 実行するエージェントとモデルを **entry ごと**に指定できる（任意。省略時は entry 共通設定）。
6. セッションを所定ターン保つ機能を維持する。CLI / モデルの差し替えは**セッション境界**で
   適用し、無限キープの entry では切り替わらなくてよい。差し替えの保留で実行を捨てない。
7. 受入条件の形式は統一 verify の語彙に揃える（自然文チェックリスト）。新しい書式を作らない。
8. 証跡ゲートは LLM を介さず機械で強制する。
9. tmux 可視化を維持する。実行ごとのログを tmux ウィンドウで表示し、CLI が動く様子が見える。
10. semaphore・slot lease・cooldown・node-budget 記帳・lifecycle は従来と同じ契約で効く。
11. 既存の設定ファイルが無改変で従来どおり動く。headless は opt-in（層3 のみ強制）。
12. 対話前提の機能は黙って劣化させない。fresh_context の clear と `slash` は WARNING 付きで
    スキップ、Ralph 多段・external target は headless 非対応として明示エラー。

## 検討した案

| アプローチ | 実装コスト | リスク | 保守性 | 推奨度 |
|---|---|---|---|---|
| A: 層2 だけ headless へ移し、層3 は定型業務専用に据え置く | 低 | 低 | 高 | ★★☆ |
| B: 定期プロンプトを 1 state の workflow に合成し既存ハーネスへ流す | 低 | 中 | 低 | ★☆☆ |
| C: 定義に層を宣言させ、層3 にはツールループと受入条件を要求する | 中 | 低 | 高 | ★★★ |

C を採用する。A は実装が軽く層2 の改善としては正しいが、今回落ちている構成（aider）を救えない。
B は追加コードがほぼ不要な代わりに、Output Contract が無いという本質は移動するだけで解けず、
定期プロンプト 1 件ごとに statemachine-use スキルと Python 3.10 と PyYAML を引きずる。

C を推す決め手は**伝える内容が正しくなる**こと。現在のエラーは「aider に interactive が無い」と
いう配管の都合を人に見せている。本当に伝えるべきは「この CLI は自分でツールを回さないので、
受入条件が無いと done を検証できない」で、それは起動時に判定できる。

## 採用設計

### 層の宣言（agents/<name>.json）

`headless_autonomy` を追加する。値は `tool-loop`（CLI が内蔵）か `single-shot`（非内蔵）。
未宣言は `single-shot` として扱う——安全側に倒し、ツールループを供給する。`file_flag` の有無
などからの推測はしない（推測は定義契約の明示エラー原則に反する）。

schemas/agent-cli.schema.json に同じフィールドを追加する。

### cliprofile: headless プロファイル

`_resolve_cli_profile` は `interactive` 節の無い spec をエラーにせず、
`CliProfile(mode="headless", ...)` を返す。headless プロファイルでは待機判定
（classify / is_ready / is_idle）と clear / save / exit コマンドを使わない。
壊れた定義・未知の名前は従来どおり fail fast のまま。

### scheduler: headless 実行枝

プロファイルが headless のとき、ensure_session と ready 判定と SlotMonitor を通らず、
次の経路で実行する。

1. スロット取得。キーは pane ではなく合成 ID（`headless:<root_id>`）。
2. 層2（`tool-loop`）は `headless_cmd(spec, model, prompt)` の argv を 1 回 subprocess 実行する。
   cwd・timeout・env・stdin は組み立て結果どおり。
3. 層3（`single-shot`）はツールループを供給する（次節）。
4. 出力は実行ごとの JSONL ログへ書く（statemachine ハーネスと同じ流儀）。
5. tmux 配下なら実行ログを tail するウィンドウを開いて可視化する。tmux 外（テスト・CI）は
   可視化なしで実行だけ行う。
6. exit 0 かつ受入判定 pass で完了。失敗は `classify_error` で分類し、transient は既存の
   リトライ規則へ、それ以外は失敗として記録する。
7. 終了時にスロット解放・cooldown・node-budget 記帳（実行秒は subprocess の実測。対話経路の
   スロット保持時間による近似より正確になる）。

### 層3 のツールループ

statemachine ハーネスのループを**ゴール単位**で切り出して共用する。statemachine 側は
「状態遷移」と「限定ツールループ」が混ざっているが、定期プロンプトに要るのは後者だけ。

切り出す範囲（ゴールに依存しない部分）:

- 限定ツール契約（`read_files` / `write_files` / `run` / `final`）とラウンド上限
- ファイル割付の分離（`--read` と `--file`）
- 説明文に混ざった JSON を括弧の釣り合いで拾うパーサ
- シェル禁止・実行ファイルの所在限定・パス正規化・NUL 拒否
- コンテキスト節約（stdout は末尾を切る、大きい成果物は自動再投入しない）
- 小型モデル向けのプロンプト規律（シミュレートするな、TOOL_RESULT なしに完了を主張するな）
- 証跡ゲート（`_sm_final_evidence_error` 相当）

切り出さない範囲（statemachine 固有）: state 遷移、`next_state.py`、条件の LLM 評価、
state ごとの `max_retries`。

初期ファイル割付は受入条件から取る。基準文にバッククォートで書かれたプロジェクト内ファイルを
`--read` へ載せる（statemachine の `_sm_action_project_files` と同じ方式）。これで「aider は
ファイルを渡さないと着手しない」問題が、受入条件を書くことで自動的に解ける。

### entry ごとのエージェントとモデル

現状、CLI とモデルは全体設定の `agent_cli` / `agent_cli_options.model` の一本しかない。
定期プロンプトは entry ごとに重さが違う（要約は小型で足り、設計レビューは上位が要る）ので、
entry 単位で指定できるようにする。

**どちらも任意フィールド**。省略した entry は entry 共通設定（agent-loop.yaml トップレベル）を
参照する。片方だけの指定も許す（CLI だけ変えてモデルは共通、など）。既存の設定ファイルは
1 文字も変えずに従来どおり動く。

```yaml
agent_cli: aider                  # entry 共通（従来どおり）
agent_cli_options:
  model: gemma4:e4b

prompts:
  - name: ログ要約                 # 共通設定をそのまま使う
    prompt: ...
  - name: 設計レビュー
    prompt: ...
    agent_cli: codex              # この entry だけ上位で回す
    model: gpt-5.6-terra
```

解決順は既存の doctrine を壊さない。**管理面（control.json）の `workloads.routine` 宣言が
最優先**で、そこが空欄なら下位へ委ねる、という現在の意味をそのまま保つ。

1. control.json の `workloads.routine`（予算枯渇時の `degraded` 差し替えを含む）
2. entry の `agent_cli` / `model`
3. entry 共通設定の `agent_cli` / `agent_cli_options.model`
4. 既定

entry を control.json より上に置かない。管理面は予算・quota・段の判断で全体を倒す権限を持って
おり、entry が上書きできると「予算が枯れても degrade が効かない entry」ができてしまう。
現在の control.json は `routine: {agent_cli: null, model: null}` なので、実運用では entry の
指定がそのまま効く。

CLI ごとにプロファイル（argv・待機判定・層）が変わるため、entry ごとに CliProfile を解決して
持つ。同じ CLI とモデルを指す entry は同じプロファイルを共有する。

### セッション継続と CLI / モデル差し替えの共存

セッションを所定ターン保つ機能は**維持する**。上位段では会話文脈を跨いで継続したいケースが
あり、headless 化の副作用で失ってよいものではない。

既存の機構をそのまま使う。`session_policy` は `persistent`（既定）/ `oneshot` / `sandbox` /
`external`、entry の `clean_session: N` は「同じセッションで N 回成功したら建て直す」。
`persistent` かつ `clean_session` 無しが無限キープ。

**CLI / モデルの再解決はセッション境界で行う。** 境界は既にあるものを使い、新設しない。

| セッション設定 | 境界 | 差し替えが効くタイミング |
|---|---|---|
| `oneshot` / headless 経路 | 毎回 | 次の実行 |
| `clean_session: N` | N 回成功ごと | 次の建て直し |
| 無限キープ（`persistent`・`clean_session` 無し） | デーモン再起動のみ | 再起動後 |

無限キープで実行中に切り替わらないことは受け入れる。会話文脈を保つと選んだ以上、途中で実行
主体が入れ替わる方が害が大きい。agent-loop は終了時に全ペインを畳むので、再起動が確実な境界に
なる——今すぐ切り替えたい人にはそれが逃げ道になる。

#### 経路の選択

既定は**従来どおり対話キープ**。headless は opt-in にする（既存の設定ファイルが無改変で
従来と同じ挙動になることを優先する）。entry の任意フィールド `session` で選ぶ。

- `keep`（既定）——対話ペインを保つ。従来動作。
- `per-run`——実行ごとに使い捨て。headless 経路。

層3（`single-shot`）の CLI は対話 command を持たないので、`session: keep` が指定されていても
`per-run` で動かし、その旨を警告する。

#### 現状の欠陥（同じ PR で直す）

**1. モデル不一致で実行が永久に捨てられる。**

```python
if existing_model != launch_spec.get("effective_model"):
    self._fail_execution(req, None, reason="model_mismatch")
    return "discard"
```

既存ペインのモデルと要求モデルが違うと run を捨てる。

現状これは発火しない。`model` は `exec_meta.get("model")` すなわちリクエスト単位の値で、
定期プロンプトは持たないため両辺とも `None` になる。**entry ごとの `model` を足すと発火する。**
稼働中に entry のモデルを変える（config reload、または control.json の書き換え）と、その entry
の既存ペインは旧モデルのまま新リクエストは新モデルになる。無限キープでは境界が来ないので
discard が続き、その entry は動かなくなる（ライブロック）。今回の設計が作り出す経路。

正しい振る舞いは**捨てないこと**。境界があるなら建て直して新しい CLI / モデルで実行し、
境界が無い（無限キープ）なら**古いセッションのまま実行して警告する**。どちらも run は通す。

なお agent-loop は終了時に全ペインを畳む（`SessionManager.stop()` を `atexit` と
SIGHUP / SIGTERM / SIGINT ハンドラから呼ぶ）。したがってペインが再起動をまたぐことはなく、
**デーモンの再起動は確実なセッション境界になる**。無限キープの entry で差し替えを今すぐ効かせ
たい人には、再起動が正規の逃げ道になる。例外は SIGKILL / クラッシュで `atexit` が走らなかった
場合だけで、そのときは残存ペインへ再アタッチしうる——fingerprint 比較はこの残骸も拾う。

**2. `launch_fingerprint` が誰にも比較されていない。**

`session.py` はペイン起動のたびに `launch_fingerprint(profile_name, full_argv, session_cwd)`
を計算して保存するが、比較する実装が無い。モデル単独比較（`effective_model`）より広く、
CLI 切り替えも argv 変更も拾えるので、**差し替え判定はこの fingerprint で行う**。
既存の `effective_model` 比較は fingerprint 比較へ置き換える。

**3. `revision_applied` が適用値ではなく最新値を報告する。**

`_write_status` は毎回 control.json を読み直して `rec["revision_applied"] = ctl.get("revision")`
を書く。名前は applied だが中身は「いま読んだ revision」。dashboard の「設定の反映」バッジは
これを見るため、再起動も建て直しもしていない agent-loop が「反映済み」と表示される。
**実際に解決へ使った revision** を保持して報告するよう直す。

**4. `restart_required` を誰も読んでいない。**

agent-loop が書くだけで consumer が存在しない（schemas の定義とテスト 1 本のみ）。
意味を「セッション境界待ち」に改め、dashboard の「設定の反映」列で見せる。無限キープの entry で
差し替えが保留されていることが、この列で人に分かるようにする。誰も読まないフィールドを
書き続ける状態をやめる。

### 受入条件（agent-loop.yaml）

prompts entry に `acceptance` を追加する。**自然文 3〜7 項目のチェックリスト**。
語彙は task.schema.json の `task_acceptance_criteria` に揃える。

```yaml
prompts:
  - name: ログ要約
    prompt: agent-audit で取得したログから重要な情報を抽出し、要約してください。
    interval_minutes: 600
    acceptance:
      - reports/audit-digest.md が今回の実行で更新されている
      - 直近 24 時間のエラーが発生元ごとに件数付きで列挙されている
      - 各項目に元ログの行番号が付いている
```

決定的シェルコマンドを人に書かせる方式は採らない。agent-project が一度採って捨てた道で、
理由は backlog-verifier の SKILL.md にある——環境差で大半が失敗し、かつ「たまたま通る劣化した
検証」を人が見抜けない。人は自然文だけ書き、コマンドは検証時にエージェントが試行錯誤して
合成する。確認方法を人が知っている場合だけ `verification_commands` を任意で併記できる。

層2 では `acceptance` は任意。あれば同じ検証を通し、無くても警告しない——ツールループを内蔵
する CLI は従来どおり自由文で動くので、新方式に従わないこと自体は問題ではない。

層3 で `acceptance` が無い entry は**警告して実行し、結果を「検証なし」として記録する**。
起動は止めない（移行のため）。ただし検証なしの実行を done の根拠にしない（C5）。
警告文には dashboard の AI 補完へ導く一文を入れる。

層は実行時に解決される点に注意する。予算枯渇で段が降格すると、昨日まで層2 だった entry が
今日は層3 になる。この降格で「`acceptance` 無しの層3 実行」が生まれたときも同じ扱い
（警告 + 検証なし記録）にする。降格を理由に黙って検証を落とさない。

### 受入判定と証跡ゲート

#### 現行 statemachine の方式とその穴

証跡ゲートは **agent-loop 側のハーネス**（`agent_loop/statemachine.py` の
`_sm_final_evidence_error`）にある。statemachine-use スキルのスクリプト
（`engine.py` / `next_state.py` / `run_machine.py`）には証跡ロジックが無く、
スキル側は workflow の検証と遷移だけを担う。

workflow スキーマに成果物ファイルを宣言する仕組みも無い。`output_key` は出力文字列を
context へ保存するだけ、`output_validator` は第 1 行の `startswith:` 検証だけ。実際の照合は
モデルの最終出力本文を `^path:\s*(.+)$` で走査し、拾ったファイルについて次を確かめている。

1. 実在するか
2. この state の `evidence` にあるか（read_files / write_files / run の引数として触れた）
3. この実行の `touched` にあるか（write / run が生成・検証した）

つまり**出力本文の慣習に乗った opt-in のゲート**であり、二つの穴がある。

- `path:` 行が無ければ素通しする（`if not m: return ""`）。モデルが書き忘れれば無効になる。
- 成果物を作らない state（判断・分岐・context への要約）は正当に `path:` を持たない。
  「検証不要」と「書き忘れ」を機械が区別できない。

#### 採用する方式: 受入条件を入力にした二層ゲート

ゲートの根拠を「モデルの出力本文の慣習」から「**人が承認した宣言**」へ移す。

**機械層（LLM 不要・決定的・フェイルクローズ）**
受入条件の自然文からバッククォートで書かれたプロジェクト内パスを抽出し
（`_sm_action_project_files` と同じ方式）、そのファイルについて実在・`touched` 所属・
mtime/size の変化を照合する。1 件でも欠ければ fail。ここに LLM を挟むと自己承認の穴が戻る。

**判定層（検証エージェント）**
パスを含まない基準を証跡付きで判定する。不変条件は backlog-verifier と同じ。

1. フェイルクローズ——明示の pass が無い基準は fail
2. 証跡必須——pass なのに実行コマンドも参照ファイルも無い基準は fail へ落とす
3. `unverifiable` はリトライを焼かない（環境不足は人へ回す）

この二層構造は backlog-verifier が `verification_commands`（決定的）と
`task_acceptance_criteria`（自然文判定）で既に持っているものと同じ形（C7）。

成果物ファイルを作らない仕事は受入条件側で宣言する（task.schema.json の `no_diff` と同型）。
宣言があれば機械層は「宣言した参照先を判定で実際に読んだこと」を問う基準へ差し替える。

検証を実行と同じエージェントにやらせない。層3 の実行が aider/gemma4 なら、判定は別呼び出しに
する（既定は同じ段の別プロセス、必要なら上位段）。

#### statemachine への逆輸入

機械層は workflow の state にも `acceptance` を持たせれば同じ実装で使える。定期プロンプトと
statemachine で証跡ゲートを 2 実装に分けない（C7）。`path:` 慣習は当面残し、`acceptance` を
持つ state から順に置き換えて段階的に畳む。

### dashboard: 受入条件とプロンプトの AI 補完

受入条件を人がゼロから書くのは負担が大きいので、dashboard から下書きを生成できるようにする。
**新しい機構は作らない**——charter 補完と同型にする。

生成の対象は受入条件だけでなく**プロンプト本文の書き直しも含む**。受入条件を立てると仕事の
仕様が締まるので、元の自由文プロンプトと食い違う。1 回の生成で「締めたプロンプト本文」と
「受入条件」を対にして出し、人が両方まとめて承認する。

- 生成は段（tier）を選んで実行する。既存の `profiles.resolveTier` をそのまま使う。
- 生成プロンプトの正典は backlog-planner（受入基準を書かせるプランナーとして既にある）。
- 機械層が効くように、成果物のパスはバッククォートで基準文へ入れさせる。
- dashboard の LLM 呼び出しは読み取り専用の助言のみ。**ファイルへの書き込みは authoring.js が
  行う**（「人が書く上位入力だけを書く」護りをエージェント経由で迂回しない、C4）。
- 生成物は下書きとして画面に出し、**人が承認して初めて** agent-loop.yaml へ確定する。
  承認を飛ばすと、AI が書いた基準を AI が満たす閉ループになる。
- 補完に使う段と、その定期プロンプトを実行する段は別でよい（上位段に書かせて下位段に実行させる
  のが想定の主用途）。

### 起動時の検証

- headless プロファイルで起動したこと、entry ごとの CLI・モデル・層をログに明示する。
- 層3 の CLI が割り当たった entry に `acceptance` が無ければ**警告**する。文言は配管ではなく
  理由を言う（「この CLI は自分でツールを回さないため、受入条件が無いと done を検証できません。
  dashboard の補完で受入条件を作れます」）。層2 の entry では何も言わない。
- Ralph 多段・external target を含む entry は起動時に明示エラー（headless 非対応）。

## エラー処理

- timeout: 定義の `timeout` で kill し transient 扱い（リトライ規則へ）。
- 空出力: `empty_output_is_error` を尊重して失敗にする。
- 受入判定 fail: 実行は失敗として記録する。リトライは既存規則に従い、無限には回さない。
- ツール要求の契約違反（シェル要求・作業フォルダ外パス）: 実行せず却下理由をループへ返す。
- 実行ログ書き込み失敗: 実行は止めない（best-effort。budget 記帳と同じ方針）。
- lifecycle=pause/stop: 新規 subprocess を起動しない。実行中のものは timeout まで見届ける。

## テスト方針

- cliprofile: `interactive` 無し定義が headless プロファイルへ解決される。壊れた定義は従来
  どおりエラー。
- 層の宣言: `tool-loop` は 1 回実行、`single-shot` はツールループ経由。未宣言は `single-shot`。
- entry ごとの CLI/モデル: entry の指定が効く。省略した entry は共通設定へ落ちる。片方だけの
  指定でも解決できる。control.json の `workloads.routine` が明示されていれば entry より優先し、
  degraded 差し替えが entry 指定を上書きする。既存の設定ファイルが無改変で従来どおり動く。
- セッション境界: `oneshot` / `session: per-run` は次の実行で差し替わる。`clean_session: N` は
  N 回目の建て直しで差し替わる。無限キープは差し替わらず `restart_required` が立つ。
- **モデル不一致で run を捨てない**。境界があれば建て直して実行、無ければ旧セッションのまま
  実行して警告。稼働中に entry のモデルを変えた無限キープの entry がライブロックしない。
- 終了時に全ペインが畳まれる（既存挙動の回帰）。再起動後は新しい CLI / モデルで起動する。
- fingerprint 比較: モデルだけでなく CLI 切り替え・argv 変更でも差し替え判定が立つ。
- `revision_applied` が「実際に解決へ使った revision」を報告する（再起動も建て直しもしていない
  のに「反映済み」と出ない）。
- 既定経路: `session` 未指定の entry が従来どおり対話キープで動く。層3 は指定に関わらず
  `per-run` で動き警告する。
- 起動時検証: 層3 × `acceptance` 無しが警告され、起動は続く。層2 × `acceptance` 無しは無言。
- 検証なし記録: 層3 × `acceptance` 無しの実行結果が done の根拠にならない。
- 段の降格で層2 から層3 へ落ちた entry も同じ警告と記録になる。
- dispatch: スタブ argv で headless 実行が完了し、スロット解放と budget 記帳が起きる。
- 証跡ゲート（機械層）: 受入条件が挙げたファイルが未生成・未変更なら fail。実行で触っていない
  ファイルを成果として申告した出力が fail になる。パスを含まない基準は判定層へ回る。
- 証跡ゲート（判定層）: 証跡なしの pass が fail へ落ちる。`unverifiable` はリトライを焼かない。
- 成果物を作らない宣言がある定期プロンプトで、機械層が参照先の読み取りを問う基準へ差し替わる。
- slash・fresh_context 付き entry が WARNING 付きでスキップされる。
- 失敗分類: 非 0 exit・空出力・timeout が定義の errors とリトライ規則へ流れる。
- tmux 外でも実行だけは成立する。
- dashboard: 補完が読み取り専用で走り、承認前は yaml が書き換わらない。
- 既存の対話経路のテスト（test_statemachine / test_node_budget ほか）を壊さない。

## 実装計画

1. **済** `agents/<name>.json` と schemas/agent-cli.schema.json へ `headless_autonomy` を追加し、
   既存定義に宣言を入れる。
2. **済** cliprofile へ `mode="headless"` を追加し、起動時 fail fast を解除する。
3. **済** `agent_loop/statemachine.py` のツールループをゴール単位で切り出して共用モジュール
   （`toolloop.py`）にする（state 遷移・`next_state.py` 連携・state ごとの `max_retries` は
   statemachine 側に残す）。
4. **済** scheduler へ headless 実行枝（層2 は 1 回実行、層3 はツールループ）を追加する。
5. **済** prompts entry へ任意の `agent_cli` / `model` / `session` を追加し、CliProfile をセッション
   境界で解決する。`model_mismatch` の discard を「境界があれば建て直し、無ければ警告して続行」
   へ直し、判定を `launch_fingerprint` の比較に置き換える。あわせて `revision_applied` を実際に
   解決へ使った revision にし、`restart_required` を dashboard の「設定の反映」列で見せる。
6. **済** prompts entry へ `acceptance` を追加し、層3 × 未設定の警告と「検証なし」記録を入れる。
   証跡ゲートの機械層を、受入条件を入力に取る形で `toolloop.py` へ実装する。
7. **未** tmux tail ウィンドウの可視化。実行ログの JSONL 出力（`~/.agents/runs/headless/`）と
   進行表示までは入っているが、ログを追う専用ウィンドウの自動起動は未実装。
8. **済** dashboard の AI 補完（段選択・読み取り専用・プロンプト本文と受入条件を対で生成・
   人の承認で確定）。`acceptance` の YAML 書き戻しも含む。
9. **済** テストを追加し、対象テスト・全テスト・ESLint を通す
   （`test/test_headless_route.py` 新設。agent-loop 314 件・dashboard 影響範囲すべて通過）。
10. **未** 機械層を statemachine へ逆輸入する（workflow state の `acceptance` 対応。`path:` 慣習との
    併存から始める）。現状の statemachine は従来どおり出力本文の `path:` 行だけを見ており、
    ここを畳むまで証跡ゲートの根拠が定期プロンプトと statemachine で揃わない。

### 残作業のまとめ

| # | 未実施 | 影響 |
|---|---|---|
| 7 | tmux tail ウィンドウの自動起動 | 実行の様子を見るにはログファイルを人が開く必要がある |
| 10 | statemachine への機械層の逆輸入 | statemachine は `path:` 慣習のままで、書き忘れると証跡ゲートが効かない |

判定層（パスを含まない基準を検証エージェントが証跡付きで判定する層）も未実装。現状は機械層
（宣言されたファイルの実在・touched・変化）だけが動き、パスを含まない基準は判定されない。
そのため受入条件を書いても「ファイルが出来たか」までしか機械検証していない。

## Decision Record

| 項目 | 内容 |
|---|---|
| 決定日 | 2026-08-11 |
| 決定者 | ユーザー |
| 採用案 | C: 定義に層を宣言させ、層3 にはツールループと受入条件を要求する |
| 却下案 | A: 層2 のみ対応（今回の構成を救えない）、B: 1 state workflow への合成（検証の穴が残り依存が重い） |
| 主な理由 | 人に伝わる内容が配管ではなく実行契約の言葉になるため。受入条件は検証を可能にするだけでなく、自由文の仕事を狭く仕様する効果を持つ（実測: 自由文 2/21、仕様済み 3/3） |
| トレードオフ | 層3 で受入条件の無い既存プロンプトは警告付きで動き続けるが「検証なし」扱いになり、done の根拠にならない。無限キープの entry では CLI / モデルの差し替えが適用されない。初版は Ralph 多段・external target・fresh_context clear・tuning 注入に非対応 |
| 移行方針 | 起動を止めない。既定は従来どおり対話キープで、headless は opt-in（層3 のみ強制）。層3 × 受入条件なしは警告のみ。層2 は無警告（従来どおり自由文で動くため）。段の降格で層3 へ落ちた場合も同じ扱い |
| 併せて直す欠陥 | `model_mismatch` の discard（entry ごとの model 追加で顕在化するライブロック）、比較されない `launch_fingerprint`、適用値でない `revision_applied`、consumer の無い `restart_required` |
| 再評価条件 | ツールループ非内蔵の CLI で多段実行や外部ペイン連携が必要になった場合、または受入条件の自動生成が人の承認なしで足る精度に達した場合 |
