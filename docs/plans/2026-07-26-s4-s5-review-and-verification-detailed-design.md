# S4 + S5 詳細設計: 検収の MR/PR 一本化と、証跡ベースの検証

ステータス: 実装済み（詳細設計 + 実装で確定した差分を反映）
入力: [`2026-07-25-agent-improvement-spec.md`](2026-07-25-agent-improvement-spec.md) §3 S4（C5）/ S5（C12）
前提: [`2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md`](2026-07-26-s3-s2-node-repos-and-cowork-roots-design.md)（ノード固有ローカルクローンの解決器）
実装フェーズ: Phase 2（S4 → S5 の順）

S4 と S5 は 1 本にまとめる。両方が同じ 1 枚（検収カード）の中身を決めており、別々に設計すると
「人が何を見て決めるのか」が二重定義になるため。

---

## 1. S4: 検収の MR/PR 一本化と決着契約

### 1.1 現状（調査結果）

| 要素 | 状態 |
|---|---|
| タスク MR の作成（GitLab） | 実装済み。`mr.py:80-116` `ensure_task_mr` が review 到達時に `ap/<task-id>` → target の MR を冪等作成 |
| 承認時の MR 決着 | 実装済み。`mr.py:119-165` `finalize_task_mr`（クリーンならマージ / 差分なしならクローズ / 未クリーンは差し戻しコメント） |
| 却下時の後始末 | 実装済み。`mr.py:~270` `close_task_mr` |
| 検収カードへの MR URL | 実装済み。`_settle_review`（`mr.py:318-380`）が `- MR:` 行と frontmatter へ載せる |
| **フォージ側シグナルからの決着** | **未実装**。`flow.js:115-176` の推定は**フロー画面の表示先読み専用**でタスク状態に反映されない |
| **ローカル diff の前提** | **問題箇所**。`base/main/git.js:225-227` は `fs.existsSync(root)` を要求し、`delivery.path` は worker の `/tmp` 一時 worktree を指すので dashboard のマシンには存在しない |

つまり「作る・承認で決着させる」側は既にあり、欠けているのは
**(a) フォージ側の人の操作を agent-project の決着に変換する経路**と
**(b) 検収カードの中身がローカルパス前提であること**の 2 つだけ。

### 1.2 比較検討: MR/PR を誰が作るか

仕様書 S4-1 は「settle 時に agent-project が作る」としているが、ここは
「ローカル diff を撤去してよいか」を左右するので、先に決める。

| 案 | 長所 | 短所 |
|---|---|---|
| **① agent-project（常駐体）が settle 時に作る**〈現行の延長〉 | 実装が既にある。書き込みは常駐体のみ＝single-resident 設計の原則に一致。検収カードが立つ瞬間に MR URL が確定するので**カードの必須項目にできる**。フォージのトークンは常駐ノード 1 台に置けばよい | 常駐体が止まっていると MR ができない（が、そのときはカード自体が立たないので実害は無い） |
| ② worker（agent-flow）が push 直後に作る | MR が最速で立つ。push した本人なので source/target を最も正確に知る | **フォージの書き込みトークンを全ワーカーノードへ配ることになる**。板経由で他ノードが請け負う構成（S3・S8 の前提）では、委譲先が信頼境界の外にあるほど破綻する |
| ③ dashboard が検収カードを開いたときに作る | 人の PC には既にトークンがあることが多い | 仕様 S4-4「dashboard は決着状態の表示に徹し、git・フォージへの書き込みは行わない」に正面から反する。常駐体と競合して同じ source_branch に 2 本作りうる。**dashboard を開かないノードでは MR が永遠にできず、フォージ側の CI もレビューも始まらない**（＝レビューの起点が人の操作待ちになる） |

**採用: ①**。②③ は「トークンの配布範囲」と「書き込み口の数」を増やす方向で、どちらも一本化の逆を向く。

### 1.3 では、ローカル diff は撤去してよいか

**作成者が誰かは、この問いを決めない。**決めるのは「MR が存在しうるか」だけである。
フォージ（GitLab / GitHub / Gitea）が無い・トークンが無い・リモートが素の git サーバのとき、
**誰が作っても MR は存在しない**。完全撤去は「その運用では dashboard 上で差分が一切見られない」を意味する。

仕様 S4-6 はフォージ無し運用を明示的にスコープ外とし「diff はローカル git を人が直接見る」としているので、
完全撤去でも仕様には従う。しかし S3 で **host.yaml `repos[]` によるノード固有ローカルクローン宣言**が入った結果、
dashboard は「消える `/tmp` の一時 worktree」ではなく**このノードの恒久クローン**から差分を取れるようになっている
（`nodeRepos.resolveLocalRepo` / `chatCwdChoices` が既に使っている経路）。撤去すべきだったのは
**`delivery.path` 前提の壊れた経路**であって、差分表示そのものではない。

**決定:**

1. 検収カードの**正は MR/PR 一本**。MR がある間は差分レビューへの動線は MR リンクだけを出す（カード内で diff を開かせない）
2. `delivery.path`（worker の一時 worktree パス）を使う経路は**撤去**する
3. **MR URL を持たないタスクに限り**、S3 のノード宣言でローカルクローンを解決できたときだけ「差分を見る」を出す。解決できなければボタンを出さず、理由（「このPCにローカルクローンの宣言がありません」）を表示する

`diff2html` 依存はこのフォールバックのために残る。これは仕様 S4-2 の「廃止する」からの意図的な逸脱であり、
根拠は「S4-6 がスコープ外とした運用を、S3 の成果で**追加コストほぼゼロで**救えるようになった」こと。

### 1.4 決着契約（S4-3）

**決定的シグナルのみで決着する。コメント本文のキーワードマッチは検収の決着に使わない。**

| フォージ側の事象 | agent-project の決着 |
|---|---|
| MR/PR がマージされた | approve（done 確定。`finalize_task_mr` は「決着済み」として素通り） |
| MR/PR が未マージでクローズされた | reject |
| `status:changes-requested` ラベル付与、または Changes Requested レビュー | revise（**未解決**レビューコメントの本文を feedback に注入して ready へ） |
| 上記以外（コメントのみ・承認だけでマージ未了 等） | 何もしない（人の明示操作を待つ） |

**「未解決」の判定**は `finalize_task_mr` が既に使っている GitLab の discussions API
（`resolvable && !resolved`）をそのまま使う。解決済みコメントまで feedback に流すと、
一度直した指摘が毎回積み直されて収束しない。

**仕様 S4-3「`GITLAB_REJECT_HINTS` 等は廃止」の適用範囲**: 廃止するのは**タスク MR の検収決着**に関する限り。
`flow.js:115-176` の同名テーブルは **agent-flow の gitlab executor（イシュー駆動委譲）の決着先読み**であり、
`executors/gitlab.py` の `_decision_from_comments` と一致させることが明示された別系統なので、本仕様では触らない。
片方だけ消すと「乖離させない」という既存の約束が壊れる。

### 1.5 ポーリングと反映（S4-4）

- 照会と書き込みは **agent-project の常駐体の sync 周期**が担う。`mr.py` に `poll_task_mrs(cfg, tasks)` を足し、`mr_iid` を持つ review 状態のタスクだけを対象に GitLab を照会する（開いている MR の件数分の API 呼び出し。件数は検収待ちタスク数に比例＝有界）
- 決着は既存の revise / approve 契約ファイルに**合流させる**。新しい状態遷移経路は作らない
- dashboard は表示に徹する。承認/差し戻しボタンは「フォージを使わない判断」の口として残す
- 到達不能（ネットワーク断・トークン失効）は**決着しない**（現状維持）。フォージが見えないことを「未マージ＝reject」と読むと、回線が切れただけで成果が却下される

### 1.6 設定（S4-5）

プロジェクト yaml に `remote_review: settle | observe`（既定 `settle`）を追加する。
`observe` はフォージ照会の結果を needs 票へ**表示するだけ**で決着に使わない（移行用）。
新規キーは `CONFIG_DEFAULTS` に足すだけで `PROJECT_ONLY_KEYS` に落ちる（差集合定義。S1 §設定 2 層）。

### 1.7 フォージ別の対応順（未決事項 3 の決着）

**決着: GitLab 先行。GitHub/Gitea はアダプタ境界だけ切って未実装とする。**

`mr.py` の GitLab 直叩き部分を `forge` インタフェース（`create_mr` / `read_mr` / `list_unresolved_notes` / `merge` / `close` / `comment`）として切り出し、実装は GitLab のみ置く。
`_gl_parse_repo` がホストを解釈できないリモートは「フォージ無し」として扱い、S4-6 の従来運用（dashboard のボタン決着 + §1.3 のローカル diff フォールバック）へ倒す。

GitHub/Gitea を今実装しない理由は、**動作確認できる環境が無い状態で書いた API クライアントは、動くかどうか分からないコードが増えるだけ**だから。境界を切っておけば、必要になったノードで 1 ファイル足すだけで載る。

**認証情報の置き場**: 既存の `_gl_token`（環境変数 `GITLAB_TOKEN` / `GL_TOKEN` → rc ファイル）を踏襲し、
**host.yaml にもプロジェクト yaml にも書かない**。前者は共有しないファイルだが平文で PC に残り、
後者は state repo 経由で全 PC へ配られる。設定 2 層はどちらも秘密の置き場ではない、を契約として明記する。

---

## 2. S5: 証跡ベースのエージェント検証

### 2.1 現状（調査結果）

| 経路 | 実装 | 評価 |
|---|---|---|
| 人が書く `verify:`（決定的シェル） | `verify.py:6-38` `run_verify` | 機能している（最速・最優先） |
| `verify_template`（決定的展開） | `verify.py:188-212` | 機能している |
| `accept:` → LLM 一発合成 | `verify.py:496-526` `synth_verify` | **問題**。合成 → 静的スクリーニング（散文判定・`sh -n`・恒真式検出・Windows シェル検出）で最大 2 回リトライ。実行して直すループが無い |
| red-green（変更を弁別しない verify の検出） | `verify.py:57-96` `run_verify_at_rev` / `verify_undiscriminating` | 別実行で act 前ツリーを検証する。temp clone のタスクは対象外 |
| 検証済みコマンドの再利用 | `flow.py:753` `find_learned_verify` | 「たまたま通る劣化した検証」も再利用しうる |
| agent-flow の verify ノード | `agent.py:915-923` の role + `waits.py:271-285` `_normalize_verify` | **機能している**。エージェントが検算し `verify=pass\|fail` + JSON を返すフェイルクローズ |

settle の合流点は `mr.py:585-690`。ここで verify を実行し、flake / no-progress / red-green / 回帰 を順に見て
review か done か failure へ振り分けている。**S5 が差し替えるのは「ok, flaky, vmsg を得る部分」だけ**で、
その後の分岐（保護パス・自律レベル・回帰ゲート）は変えない。

### 2.2 コンセプト

「**1 行のシェルコマンドの exit 0**」を done の根拠にするのをやめ、
「**受入基準チェックリストに対する、検証エージェントの証跡付き判定**」を根拠にする。
人がレビューする対象を「コマンド（良し悪しを判断できない）」から「**基準と証跡（判断できる）**」へ移す。

### 2.3 受入基準チェックリストの表現

バックログ md に `- acceptance:` 行を**複数書ける**形にする。

```
- acceptance: CLI チャットの起動先ドロップダウンに宣言済みリポジトリが並ぶ
- acceptance: 宣言が無いリポジトリは非活性で理由付きで表示される
- acceptance: 既存の tmux セッション名の付け方を壊していない
```

`Task.extra` は `list[tuple[str, str]]` で、同名キーの複数行はそのまま保持され `- {k}: {v}` として書き戻される
（`model.py:18,97,110`）。**スキーマ変更もパーサ変更も要らない**。

読み出しは新設の `task_acceptance(task) -> list[str]` に集約し、後方互換の優先順位を固定する:

1. `acceptance:` 行が 1 つ以上 → それをチェックリストとする
2. 無く `accept:`（自然文 1 行）がある → 1 項目のチェックリストとして扱う
3. どちらも無く `verify:` / `verify_template:` がある → 決定的 fast path のみ（従来どおり）
4. どれも無い → 従来どおり「verify 未定義 → 人の判断へ」（`_settle_failure` の既存分岐）

**常設基準**（チェックリストに常に 1 項目追加される）:
> 「このタスクの差分が、基準の対象範囲に実在すること」

これが red-green（`verify_validate` / `run_verify_at_rev`）の代替。act 前ツリーで別実行する代わりに、
verifier に「変更が無い / 無関係な場所にしか無い」を fail として言わせる。

書式の正典は `backlog.md.example` に置き、S6 の `backlog-planner` はこの書式で生成する（仕様書の
「S5 の acceptance 書式は S6 と同時に確定させる」に対する答え＝**S5 側で確定させ、S6 が従う**）。

### 2.4 verifier の実行形態

**内蔵実行 + スキルでプロンプト差し替え**（backlog-planner と対称）。

- 実行は agent-project 内蔵の LLM 1 回呼び出し（`_run_agent_cli(purpose="verify")`）。既存の `agents: verify:` 上書き・ノード予算・失敗トリアージがそのまま効く
- プロンプトと出力契約は **`.github/skills/backlog-verifier/`**（SKILL.md + `scripts/prompt.py`）として同梱し、解決順は flow-planner と同じ（プロジェクト → git root → `~/.agents/skills` → skill-registry）。上位にプロジェクト独自の backlog-verifier を置けば全面カスタマイズできる
- 設定キー `verifier_skill`（既定 `backlog-verifier`）でスキル名も差し替え可能にする（flow-planner の名前固定が S6 で問題になっているので、最初から解いておく）

命名は `backlog-planner`（バックログを書く）と対になる `backlog-verifier`（バックログのタスクを検証する）。

### 2.5 verifier run の契約

**入力**（スキルへ渡す）: タスク（id / title / why / 作業概要 / out_of_scope）、受入基準リスト、
成果ブランチと base の情報、`context/<repo>.md`（あれば）、`rules.md`、**検証レシピ**（§2.8）、前回の失敗理由。

**ワークスペース**: タスクの成果ブランチのクローン。`_task_verify_cwd`（`verify.py:122-174`）を再利用し、
S3 の `merge_local` でノードのローカルクローンから切り出せるときはそこから取る（ネットワーク越しの clone を避ける）。

**副作用の範囲**（未決事項 4 の決着 → §4-1）: 作業ツリー内に限定。ビルド・テスト・grep・起動確認は可。

**出力**: Markdown 本文 + 末尾に JSON。フェイルクローズ正規化は agent-flow の `_normalize_verify` と同じ規則
（明示の pass 表明が無ければ fail）を基準ごとに適用する。

```jsonc
{
  "criteria": [
    { "id": 1,
      "verdict": "pass",              // pass | fail | unverifiable
      "evidence": {
        "commands": ["npm test -- needs.test.js"],
        "output": "24 passing …",     // 出力の要約（先頭/末尾を残す）
        "files": ["src/renderer/sections/needs.js:812"]
      },
      "note": "…" }
  ]
}
```

**証跡の必須化**（自己欺瞞への防御）: `verdict=pass` なのに `evidence.commands` も `evidence.files` も空の基準は、
**決定的に `fail` へ落とす**（LLM の判断を待たない機械チェック）。「確認しました」だけで pass にできる穴を塞ぐ。

### 2.6 判定と settle への接続

| verifier の結果 | settle |
|---|---|
| 全基準 pass | 従来の PASS と同じ（`delivery_review` が on なら review へ、off なら done へ） |
| 1 つでも fail | 従来の NG と同じ（`_settle_failure`）。**失敗した基準と証跡**を feedback にして積み直す |
| pass も fail も無く `unverifiable` を含む | **リトライを焼かない**。既存の環境要因失敗と同じ経路（`mr.py:424-450` の `_block` + `env_resume`）で人へ回す。理由（「このノードに `docker` が無い」等）を needs に明記 |

`unverifiable` を「板で他ノードへ検証委譲」に回す経路（仕様書 S5-2 の (a)）は、板の請負実行が W1-11 待ちなので
**本設計では人検収へ直行（b）のみ**を実装する。委譲は S8 と同時に足せるよう、判定結果に理由コードだけ残す。

> **訂正（2026-07-27）**: 待ち先は実装計画の再編で W1-11 → R2b に変わり、その R2b と同時に
> **(a) の検証委譲も実装済み**（P4-b。`flow.delegate_verification` — まず板へ「検証だけ」を
> 公示し、板が無い・rev が取れない・決着しない場合だけ (b) 人検収へ落ちる）。
> 上の表の `unverifiable` 行は「委譲を試した後のフォールバック」と読み替えること。

**決定的 fast path**: `verify:` / `verify_template:` を持つタスクは従来どおり `run_verify_stable` を直接実行し、
verifier を呼ばない（コスト最小）。`verify_confirm`（flake 判定の複数回実行）もこの経路にだけ適用する。
verifier 側の揺れは、同一レポート内の再試行として verifier 自身に扱わせる。

### 2.7 検証レポートの保存と検収カード

状態リポジトリの `verifications/<task-id>/<rev>.md` に保存する（`<rev>` は検証した成果コミット）。
needs 票（検収カード）には**要約**を載せる:

```markdown
## 検証（基準 5 件中 5 件 pass）
| # | 基準 | 判定 | 証跡 |
|---|---|---|---|
| 1 | CLI チャットの起動先ドロップダウンに… | pass | `npm test -- chat-cwd` / 24 passing |
| … |
全文: verifications/T12/9f3a1c2.md
```

**人検収では人がこの表を読む**。これが「人がコマンドの良し悪しを判断できない」への答えで、
検収の材料が「基準 + 証跡 + MR（S4）」に揃う。`evidence` が空だった基準（§2.5 で fail に落とした分）は
**警告として目立たせる**——抜き取り監査の入口を、別機能ではなくカードの中に置く。

### 2.8 検証レシピ

verifier が見つけた有効なコマンド列を `verify-recipes/<正規化タイトル指紋>.md` に保存する
（`find_learned_verify` / `verify_lib_path` の置き換え）。

- 次回 verifier への**参考情報**（「まずこれを試せ」）としてのみ渡す
- **独立した決定的ゲートには昇格させない**。環境が変われば壊れるものを done の唯一の根拠にしない
- 保存するのは pass した基準の証跡コマンドのみ

### 2.9 廃止するもの

| 対象 | 場所 | 理由 |
|---|---|---|
| `synth_verify` と静的スクリーニング群 | `verify.py:244-526`（`_synth_verify_prompt` / `_verify_is_degenerate` / `_looks_like_shell_command` / `_first_command_line` / `_code_fence_lines` / `_join_continuations` ほか） | 一発合成そのものをやめる。「LLM の出力から 1 行のコマンドを取り出す」ためだけの解析が約 280 行あり、これが不要になるのが S5 の実利 |
| `verify_validate` / `run_verify_at_rev` / `verify_undiscriminating` | `verify.py:57-96` + 設定キー | 常設基準（§2.3）が代替する |
| `find_learned_verify` / `save_validated_verify` | `flow.py:753` ほか | 検証レシピ（§2.8）が置き換える |
| 能力宣言による事前実行可否ゲート | （仕様 S5-2 の前版案。未実装） | `unverifiable` として実行時に扱う |

`ensure_verify`（`verify.py:529-566`）は残すが、`accept:` → 合成の枝を落とし、
`verify_template:` の決定的展開だけを残す。

### 2.10 設定キー

いずれもプロジェクト yaml 専有（`CONFIG_DEFAULTS` へ足せば `PROJECT_ONLY_KEYS` に落ちる）。

| キー | 既定 | 意味 |
|---|---|---|
| `verifier` | `true` | 証跡ベース検証を使う。`false` で決定的 verify のみ（`acceptance:` は表示のみ＝移行用） |
| `verifier_skill` | `"backlog-verifier"` | プロンプト・出力契約を提供するスキル名 |
| `verify_side_effects` | `"workspace"` | `workspace`=作業ツリー内のみ / `network`=ネットワーク到達も許す（§4-1） |
| `remote_review` | `"settle"` | S4-5 |

### 2.11 不変条件（変えないもの）

- **done は機械検証の PASS のみが根拠**（自己申告で done にしない）
- **必ず有限回で止まる**（verifier は 1 run・`verify_timeout` とトークン予算の内側）
- 変わるのは検証の**表現**（コマンド 1 行 → 基準リスト）と**実行者**（シェル → エージェント）だけ

---

## 3. 実装単位

| # | 対象 | 内容 |
|---|---|---|
| S4-a | agent-project `mr.py` | GitLab 直叩きを `forge` インタフェースへ切り出し（実装は GitLab のみ） |
| S4-b | agent-project `mr.py` | `poll_task_mrs`（決着契約 §1.4）+ 常駐体 sync 周期への接続 |
| S4-c | agent-project `configfile.py` | `remote_review` |
| S4-d | agent-dashboard | 検収カードを「基準×証跡 + MR リンク」構成へ。`delivery.path` 前提の diff 経路を撤去 |
| S4-e | agent-dashboard | MR 無しタスクのみ、S3 のノード宣言から解決したクローンでローカル diff（`git.js diffRange` は再利用） |
| S5-a | `.github/skills/backlog-verifier/` | SKILL.md + `scripts/prompt.py`（入出力契約） |
| S5-b | agent-project `verify.py` | `task_acceptance` / `run_verifier` / レポート正規化（証跡必須のフェイルクローズ） |
| S5-c | agent-project `mr.py` | settle の verify 部分を fast path / verifier に振り分け。`unverifiable` を `env_resume` 経路へ |
| S5-d | agent-project | 検証レポート保存（`verifications/`）+ 検証レシピ（`verify-recipes/`） |
| S5-e | agent-project | §2.9 の削除と `configfile.py` のキー整理 |
| S5-f | agent-dashboard | 検収カードの検証表 + 証跡欠落の警告 |
| — | `backlog.md.example` / README / CHANGELOG | `acceptance:` 書式の正典化 |

**順序**: S4-a → S4-b/c → S4-d/e → S5-a/b → S5-c/d → S5-e/f。
S5-c（settle の差し替え）が一番リスクが高いので、その前に S5-b のレポート正規化をテストで固めてから入る。

---

## 4. 未決事項の決着（仕様書 §5-3・§5-4）

### 4-1. verifier の副作用の許容範囲（DB・外部サービスに触る検証）

**決着: 既定は作業ツリー内のみ。外部到達が要る基準は `unverifiable` に倒す。設定で緩められる。**

`verify_side_effects: "workspace"`（既定）では、verifier に「作業ツリーの外を変更しない・
外部サービスへ書き込まない」を指示し、それが要る基準は `unverifiable`（理由: 外部依存）として返させる。
`"network"` にすると HTTP 到達を伴う確認（`endpoint-returns` 相当）まで許す。

**DB や外部サービスへ書き込む検証は、どちらの設定でも許可しない。**理由は 2 つ:
検証が失敗したときに何が壊れたか分からなくなること、そして verifier は失敗すると
リトライで**何度も**走るので、副作用が累積すること。そこまで要る検証は人の `verify:`（決定的コマンド）
として明示的に書くべきで、そのときは書いた人が責任範囲を分かっている。

### 4-2. verifier 自体の暴走・自己欺瞞への防御

**決着: 4 段。いずれも決定的（LLM の善意に依存しない）。**

1. **証跡必須**（§2.5）— `pass` なのに実行コマンドも参照ファイルも無い基準は機械的に `fail`
2. **フェイルクローズ**（§2.5）— 明示の pass 表明が無ければ fail（agent-flow の `_normalize_verify` と同規則）
3. **差分の常設基準**（§2.3）— 「差分が基準の対象範囲に実在すること」を必ず 1 項目入れる。何も変えずに全 pass を返す道を塞ぐ
4. **人検収カードでの抜き取り監査**（§2.7）— レポート要約を検収カードに常設し、証跡が薄い基準を警告表示する。**別機能としての「監査モード」は作らない**——人が毎回見る 1 枚に載っていないものは、結局見られない

verifier に成果物のコミット権は与えない（クローンの作業ツリー変更は破棄する）。
検証が成果物を「直して」pass にする道を残すと、検証と実装の境界が消える。

---

## 5. テスト計画

**S4**
1. `poll_task_mrs`: merged → approve 契約 / 未マージ closed → reject / `status:changes-requested` → revise / コメントのみ → 何もしない
2. revise に注入されるのは**未解決**の discussion のみ（解決済みは含めない）
3. フォージ到達不能（HTTP エラー・タイムアウト）は決着しない（reject に倒れない）
4. `remote_review: observe` はフォージの状態を表示するが決着しない
5. `forge` 未対応リモート（GitHub URL・素の git サーバ）は「フォージ無し」に倒れ、従来のボタン決着が効く
6. 検収カード: MR がある → diff ボタンを出さず MR リンクのみ
7. 検収カード: MR が無く、ノード宣言でクローンを解決できる → diff が出る / 解決できない → 理由付きで非活性
8. 回帰: `delivery.path`（存在しない `/tmp` パス）を渡しても壊れない（＝もう参照していない）

**S5**
9. `task_acceptance`: 複数 `acceptance:` 行 / `accept:` 1 行へのフォールバック / どちらも無い / md 往復で行が保たれる
10. レポート正規化: 明示 pass 無し → fail（フェイルクローズ）。`evidence` 空の pass → fail
11. `unverifiable` を含む結果は `_settle_failure` へ行かず、リトライを消費せずに人へ（`env_resume` が立つ）
12. 全基準 pass → 従来の PASS と同じ分岐（`delivery_review` on で review、off で done）
13. 決定的 `verify:` があるタスクは verifier を呼ばない（fast path。`agent_run` の呼び出し回数 0）
14. 常設基準（差分の実在）がチェックリストに必ず入る
15. 検証レポートが `verifications/<task-id>/<rev>.md` に保存され、needs 票に要約が載る
16. 検証レシピが保存され、次回 verifier 入力に「参考」として渡る。**決定的 `verify:` には昇格しない**
17. `verify_side_effects: workspace` の指示がプロンプトに載る（`network` で変わる）
18. スキル解決: プロジェクト直下の `backlog-verifier` が同梱より優先される / `verifier_skill` で名前を差し替えられる
19. 回帰: `verifier: false` で従来の決定的 verify のみの動作に戻る

---

## 6. 実装で確定した差分

| 項目 | 実装 |
|---|---|
| **`acceptance:` はスキーマ変更が要らなかった** | `Task.extra` が `list[tuple[str, str]]` で、同名キーの複数行はそのまま保持され `- {k}: {v}` で書き戻される。設計では「`schemas/task.schema.json` の拡張」を想定していたが、パーサもシリアライザも触らずに済んだ |
| **`synth_verify` は charter acceptance 用に残した** | §2.9 は「`synth_verify` とその静的スクリーニング群（約 280 行）を削除」だったが、`project.py:resolve_charter_acceptance`（charter の acceptance をマイルストーン収束判定へ変換する経路）が同じ関数を使っていた。**タスク検証経路からは外した**が、charter acceptance は検証対象（タスク単位・成果ブランチ上ではない）も出口（milestone）も違うので、変換には別の設計が要る。積み残し 5 へ |
| **red-green を fast path 専用として残した** | §2.9 は `verify_validate` / `run_verify_at_rev` / `verify_undiscriminating` を廃止としたが、常設基準（`DIFF_CRITERION`）が効くのは **verifier 経路だけ**。`verify_template` 由来の機械生成コマンドが done の唯一の根拠になる fast path には、実行で弁別を確かめる価値が残る。廃止すると護りの範囲が狭まるので残した |
| **MR を誰が作るか（§1.2 の比較検討）** | agent-project 常駐体を採用。実装は既存 `ensure_task_mr` の延長で、フォージ境界（`forge_available`）を挟んだだけで済んだ |
| **`flow.js` のキーワード推定は触らない** | §1.4 の整理どおり。agent-flow の gitlab executor（イシュー駆動委譲）の先読みで、`executors/gitlab.py` と一致させる約束がある別系統。検収決着では使わない |
| **既存バグの発見**: `git.js` の検収 diff | `delivery.path` を使う経路は、dashboard が agent-project と別マシンなら**そもそも動いていなかった**（worker の作業ツリーは `/tmp` で消える）。設計では「撤去すべき壊れた経路」と書いたが、実際には「壊れたまま動いているように見えていた経路」だった。`repoUrl` を渡してノード宣言から引き直すようにし、解決できないときは理由を表示する |
| **needs 票への受け渡し** | 検証要約は frontmatter の `verification:` 1 行 JSON（`delivery:` と同じ流儀）。dashboard 側は壊れていれば `null`（要約を出さない）——表示できないことより、誤った要約を出す方が悪い |

**実績**: agent-project 971 件 / agent-dashboard 全スイート green。

## 7. 積み残し

1. ~~**`unverifiable` の板への検証委譲**（仕様 S5-2 の (a)）— 板の請負実行が W1-11 待ち。判定結果に理由コードだけ残して S8 と同時に接続する~~ → **実装済み（2026-07-27・P4-b）**。板の請負実行（R2b）と同時に接続した（§2.6 の訂正注記を参照）
2. **GitHub / Gitea の forge 実装** — 境界だけ切って未実装（§1.7）
3. **S6 との接続** — `acceptance:` を**生成する**のは S6 の `backlog-planner`。本設計では書式を確定し、既存タスク（`accept:` のみ）を後方互換で吸収するところまで
4. **`diff2html` 依存** — MR 無し運用のフォールバックとして残る（§1.3）。フォージ無し運用が消えたら撤去できる
5. **charter acceptance の LLM 合成** — `resolve_charter_acceptance` は今も自然文 → コマンドの一発合成に依存している（§6）。S5 と同じ問題（合成されたコマンドの良し悪しを人が判断できない）を抱えるが、検証対象も出口も違うので別設計。ここが残るあいだ `synth_verify` とその静的スクリーニング群も残る
6. **`verifier` の実行環境** — verifier は agent-project が動くノードのワークスペースで走る。「このノードでは確かめられない」基準は `unverifiable` として人へ回るが、~~**他ノードへ検証を委譲する経路**は板の請負実行（W1-11）待ちで未接続（積み残し 1 と同じ待ち先）~~ → **接続済み（2026-07-27・P4-b）**。`unverifiable` はまず板へ検証委譲し、決着しない場合のみ人へ回る
