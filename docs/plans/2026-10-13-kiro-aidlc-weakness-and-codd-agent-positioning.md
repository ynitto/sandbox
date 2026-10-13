# Kiro の AI-DLC の構造的な弱点と、codd-agent で補う位置づけ（調査）

- 日付: 2026-10-13
- 目的: 「AI-DLC の弱点を codd-agent で補う」という説明が成り立つかを確かめ、今後の codd-agent 開発の方針に使う
- 調べた対象:
  - AI-DLC: [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows) の 2.11.0（2026-10-08 リリース。2026-10-13 時点の main を読んだ）
  - Kiro 本体の Specs（`.kiro/specs/` の requirements / design / tasks）
  - codd-agent: [`docs/designs/codd-agent-design.md`](../designs/codd-agent-design.md)、[`tools/codd-agent/README.md`](../../tools/codd-agent/README.md)

## 1. 結論

**説明は成り立つ。ただし「AI-DLC より優れている」ではなく、「AI-DLC が設計上あえて持たない役割を受け持つ」と言うこと。**

AI-DLC は「1 つの依頼（intent）を、要件から実装・運用まで、人の承認を挟みながら進める」仕組みで、
Böckeler の分類でいう **spec-first**（その作業のために仕様を書く）に当たる。成果物は intent ごとの記録として残り、
**作業が終わったあとも保守し続ける正本の設計書（spec-anchored）を持たない**。正本はコードで、
既存コードの理解はコードからの逆起こし（reverse engineering → `codekb/`）で毎回作り直す。

codd-agent は逆に、**すでにある設計書を正本として扱い、実装と設計書を両方向に食い違わせずに変える**ことだけをする。
そのため、次の 3 点は AI-DLC の弱点を補う形で説明できる。

1. **設計書を正本として保ち続ける**（AI-DLC は intent ごとに新しい文書を作り、既存の設計書は読むだけで書き換えない）
2. **影響範囲をスクリプトで測り、計画が扱うまで通さない**（AI-DLC の影響分析は LLM が書く文章で、検査の多くは既定で「助言」止まり）
3. **小さな変更を軽く、毎回同じ強さで検査する**（AI-DLC は検査や承認を厳しくすると重く、既定では緩めている）

一方、要件の聞き取り・ユーザーストーリー・NFR・インフラ・運用・多人数の進行管理は AI-DLC の領分で、
codd-agent が取りに行くべきではない。**「作り始める・大きく変えるときは AI-DLC、できたものを設計書と食い違わせずに
直し続けるのは codd-agent」**という分担が、もっとも無理のない筋になる。

## 2. AI-DLC の仕組み（弱点を見るのに要る分だけ）

| 項目 | 内容 | 根拠 |
|---|---|---|
| 位置づけ | AWS が公開した方法論と、その実行エンジン。Kiro IDE / Kiro CLI のほか Claude Code・Codex・Cursor・opencode・Copilot で動く。Kiro 本体の Specs 機能とは別物 | README |
| 規模 | 5 フェーズ（Initialization・Ideation・Inception・Construction・Operation）、33 ステージ、14 エージェント、11 のワークフロー（scope）、93 の道具、118 種の監査イベント | README |
| 成果物の置き場 | `aidlc/spaces/<space>/intents/<日付>-<名前>/` に intent ごとの記録として置く（requirements.md・functional-design・code-generation-plan など） | `docs/guide/03-spaces-and-intents.md`・`14-artifacts-reference.md` |
| 既存コードの理解 | brownfield ではリバースエンジニアリングのステージが `codekb/<repo>/` に 9 つの文書（architecture・api-documentation など）をコードから作る | `core/aidlc-common/stages/inception/reverse-engineering.md` |
| 利用者の文書 | `knowledge/documents/` に置いた原本を、ツールが索引化して読む。原本は「利用者の持ち物」で、ツールは書き換えない | `docs/guide/03-spaces-and-intents.md` |
| 検査（sensor） | 必須の見出し・上流の参照・トレーサビリティ（要件 ID → ストーリー → ルール → コードのパス）・出典タグなどを決定的に確かめる。**既定の強さは `advisory`**（記録するだけで承認ゲートは開く） | `core/sensors/*.md`・`docs/harness-engineering/06-sensors.md` |
| ガード | Guard Policy は 2.11.0 から `enterprise` 以外で既定 `off`。並行する支援エージェント（collaborators）も既定 off | CHANGELOG 2.11.0・`docs/guide/05-scopes-and-depth.md` |
| コミットの出所 | `aidlc-attest resolve` が、コミットで変わったパスを承認済みの単位と突き合わせ、`verified` / `drifted` / `unattested` などに分ける | `docs/reference/06-hooks-and-tools.md` |

Kiro 本体の Specs（requirements.md / design.md / tasks.md）も同じ spec-first の形で、仕様とコードを保つ手段は
「Kiro に仕様の更新を頼む・手で直す」であり、コードの変更から仕様のずれを自動で検出する仕組みは公式には見当たらない
（Kiro の発表記事も、実装中に計画文書が更新されなくなる「文書のずれ」を課題として挙げている）。

## 3. 構造的な弱点

機能の有無ではなく、**仕組みの選び方から来る弱点**だけを挙げる。機能が足りないだけのものは、AI-DLC の更新の速さ
（2.0 から 2.11 まで数か月、CHANGELOG は 3,000 行超）からすぐ埋まるので、説明の柱にしない。

### W1. 正本の設計書を持たない（spec-first で、spec-anchored ではない）

- 成果物は intent ごとの「記録」で、次の intent はまた新しい requirements.md を作る。10 回の変更のあとの
  「いまの設計」は、10 個の intent の記録に分かれて散らばる
- 既存コードの理解は `codekb/` をコードから作り直す。**正本はコード**で、設計書はコードから導く派生物になる
- 利用者が持つ正式な設計書（別リポジトリや `docs/`）は `knowledge/documents/` の読む材料にはなるが、AI-DLC が
  直す対象ではない。実装を変えた結果、正式な設計書と食い違っても、それを直す段は無い
- 影響: 設計書を契約・レビュー・引き継ぎの正本として持つ組織（受託開発、規制のある業務、設計と実装でチームが分かれる組織）では、
  AI-DLC を回すほど正式な設計書が古くなる

### W2. 影響範囲の分析が LLM の文章で、測っていない

- brownfield の「Blast Radius Analysis」は、共有の知識文書（`core/knowledge/aidlc-shared/brownfield.md`）にある
  「変えるファイルを挙げ、import・テスト・設定の参照を洗い出し、影響を分類して見せる」という手順で、
  モデルが文章として書く。挙げ漏れを機械で落とす仕組みは無い
- トレーサビリティの sensor は決定的だが、確かめるのは **AI-DLC 自身の成果物の中の ID の連なり**
  （受け入れ条件 → ビジネスルール → コードのパス）で、既存の設計書や既存コードの呼び出し元との対応ではない
- 影響: 既存の呼び出し元・同じ名前に触れる設計書・響くテストの見落としは、人がゲートで読んで気づくしかない

### W3. 検査と承認の強さが「重さ」と引き換えで、既定は緩い

- sensor は既定で `advisory`。`blocking` にできるのはゲートで動くものだけで、書き込みのたびに動く sensor は
  `blocking` と書いても助言のまま
- 2.11.0 で Guard Policy の既定を `enterprise` 以外で `off` にし、「New work starts leaner」とした。
  厳しくすると遅く重い、という利用者の声に合わせて緩めたと読める
- 影響: 「AI-DLC を入れたから整合が保証される」とは言えない。厳しさは設定次第で、既定では人のレビューに頼る

### W4. 小さな変更に対して重い

- express・bugfix などの軽い scope はあるが、エンジン（ネイティブの `aidlc` コマンドか Bun、ハーネスごとのフック、
  Kiro IDE 1.1.70 以上などの版の条件）と、intent の作成・承認ゲート・監査記録は共通にかかる
- 実際に使った報告では、ステージごとのレビューの往復に 30 分〜1 時間かかり、監査ファイルだけで 50KB を超えて
  文脈の長さが問題になったとある
- 影響: 「関数の戻り値を 1 つ変えて、設計書の 1 行も直す」程度の日常の保守は、AI-DLC の外で行われがちになり、
  W1 のずれがそこから増える

### W5. AI-DLC を通らなかった変更は「出所が無い」と分かるだけ

- `attest resolve` は、承認を通らずに入った変更を `unattested`、承認後に変わったものを `drifted` と分類できる。
  ただしこれは「承認された中身と一致するか」の検査で、**設計書とコードが食い違っていないか**は見ない
- 影響: 緊急の手直しや、人が直接入れた変更のあと、設計書との食い違いは残ったままになる

### W6. 起点が「やりたいこと」で、設計書の変更から実装へ流す道が細い

- AI-DLC の流れは、依頼（自然文）→ 要件 → 設計 → コードの一方向。設計チームが設計書を先に直したとき、
  その差分を起点に実装への影響を測って直す段は無い（新しい intent に文書を貼って依頼し直すことになる）

## 4. codd-agent が補えること・補えないこと

### 4.1 弱点との対応

| 弱点 | codd-agent の対応 | 設計書の節 |
|---|---|---|
| W1 正本を持たない | 設計書（別リポジトリ・同じリポジトリの別フォルダ）を参照先として正本に扱い、ずれがあれば参照先の変更案と影響範囲を見せ、了承を得て両方を変える。終わった回は `.plans/` に判断の記録として残る | §1・§3・§5.4・§5.5 |
| W2 影響を測っていない | 計画の名前から、自分と参照先の両方を `git grep` と graphify の AST の辺で測り、計画が扱うまで verify-plan が通さない。変えたあとも実際の差分から測り直す。パスでつながるファイル・響くテストも同じ | §4・§4.0・§4.1・§4.2 |
| W3 既定が緩い | verify-plan / verify-apply は設定で緩められない（緩める段階を持たない、§8.1 の判断）。モデルの申告を信じず、根拠のパス・行・見出しの実在まで確かめる | §1.2・§6・§7 |
| W4 小さな変更に重い | 依存は python3 と git だけ。1 回の実行は 1 つの会話で終わる大きさ（`max_files`）に絞る。確認では全文でなく要約を見せる | §1・§1.1・§5.7 |
| W5 通らなかった変更 | `lint` が codd を通らずに入った変更と、もとからある食い違いを決定的に挙げ、本流に渡す 1 行の「やりたいこと」にする。CI の定期実行で回せる | §5.10 |
| W6 設計書から実装へ | 同じ定義を両側に置く対称の作り。設計書の側で動かせば、参照先は実装になり、設計書の変更から実装への影響を測って直す | §1 |

加えて、テストが作った値・画像を設計書の写しへ返す仕組み（§4.3 evidence）は、AI-DLC に相当するものが無く、
「設計書に貼った画面の画像や性能の数値が古くなる」問題への答えになる。

### 4.2 補えないこと（説明で言い過ぎないこと）

- **要件を引き出す・決める**: codd-agent は「やりたいこと」が決まっている前提で動く。要件の聞き取り・ストーリー・
  NFR・インフラ設計・運用は AI-DLC の領分
- **多人数・長期の進行管理**: codd-agent は回をまたぐ状態を持たない（§8）。intent の並行・チームでの分担・
  監査証跡の厚さは AI-DLC が上
- **無人実行**: codd-agent の確認は会話の中で待つ（§2.2）。AI-DLC の Construction の自律モードのような無人の進め方は無い
- **ハーネスの広さ**: codd-agent のカスタムエージェントは Kiro と GitHub Copilot だけ。AI-DLC は 7 種
- **意味のずれ**: 本流が確かめるのは名前・パス・見出し・写した値まで。名前は合っていて中身が食い違うものは、
  サンプルの drift ワークフロー（モデルの判断）に任せている

## 5. 説明の筋書き

相手に渡すときは、次の順で話すと「比べて勝つ」話にならず、併用の提案になる。

1. **AI-DLC は「作る」ための仕組み。** 依頼ごとに要件から実装まで、承認を挟んで進める。成果物はその依頼の記録
2. **そのため、できあがったあとの正式な設計書を保つ役は持たない。** 正本はコードで、既存の設計書は読む材料。
   既定の検査は助言止まりで、影響分析はモデルの文章（W1〜W3）
3. **日常の保守は小さな変更の積み重ねで、そこに AI-DLC は重い。** 外で直された変更から、設計書は古くなっていく（W4・W5）
4. **codd-agent はその「直し続ける」ところだけを受け持つ。** 設計書を正本にし、影響をスクリプトで測り、
   了承を得てから実装と設計書の両方を変える。python3 と git だけで動き、1 回は 1 つの会話で終わる
5. **併用の形**: 新しい機能・大きな改修は AI-DLC の intent で進め、終わったら成果物のうち残すもの（要件・設計・契約）を
   正式な設計書へ移す。以後の変更と、AI-DLC の外で入った変更の手当ては codd-agent で行い、`lint` を CI で回す

一言でまとめるなら: **「AI-DLC で作り、codd-agent で設計書と実装を食い違わせずに保つ」**。

### 5.1 言わないほうがよいこと

- 「AI-DLC は整合を確かめない」: 誤り。sensor・トレーサビリティ・コミットの出所の検査を持つ。
  正しくは「AI-DLC 自身の成果物の中の整合は確かめるが、正式な設計書とコードの整合は扱わない」
- 「AI-DLC は brownfield に使えない」: 誤り。リバースエンジニアリングと brownfield の手順がある。
  正しくは「既存コードを毎回コードから理解し直すので、設計書が正本の組織とは前提が合わない」
- 機能の有無の比較表: AI-DLC は更新が速く、数か月で古くなる。仕組みの選び方（正本をどこに置くか・
  検査を測るか書かせるか）で比べる

## 6. 併用するときに確かめること（未検証）

- **Kiro での同居**: 両方とも `.kiro/agents/` にエージェントを置き、AI-DLC はフック（書き込みの監査・承認前の
  書き込みを止めるガード）を入れる。AI-DLC の intent が進行中のとき、`codd` エージェントの書き込みがガードに
  止められないか、`codd` の書き込みが AI-DLC の監査に「承認されていない変更」として残らないかを試す
- **`aidlc/` フォルダの扱い**: codd-agent は codd 自身の生成物以外を自動では除外しない。AI-DLC の記録
  （`aidlc/spaces/**`）を探す対象に入れると、intent の記録が影響範囲の候補に大量に出る。`exclude` に入れるか、
  逆に参照先の 1 つとして読むかを決める
- **ID の連なり**: AI-DLC の成果物は `AC1.1.1`・`BR1.3` のような ID でつながる。codd-agent の測定は `…` で囲んだ名前と
  パスで動くので、ID がそのまま測る語として働くかを試す

## 7. codd-agent の開発への示唆

### 7.1 強めるもの（差別化の柱）

1. **AI-DLC との併用の手順を README の例として書く**（本体には AI-DLC の名前を入れない。webui-test と同じく、
   組み方は使うプロジェクトの設定と README の例にだけ書く、§5.9 の独立性に合わせる）。`aidlc/` の `exclude` の例、
   intent が終わったあとに成果物を正式な設計書へ移す依頼の書き方、`lint` を CI に置く例
2. **intent の成果物を正式な設計書へ移す手順**: AI-DLC の記録を参照先として読み、正式な設計書の側で codd を動かして
   取り込む流れを、`samples/` にワークフローとして置く（drift と同じく本流の外）。本流に入れないのは、
   どこまで移すかが組織ごとの判断だから
3. **`lint` を「AI-DLC のあとの検査」として売れる形にする**: いまは終了コードと `.codd/lint.md` だけ。CI で読みやすい
   出力（件数の要約・機械が読める形）を足す
4. **影響の測定の確かさを数字で示す**: `tests/scenarios/semantic-graph.md` で測っている取りこぼしと要判断の数を、
   「LLM に書かせた影響分析と比べてどれだけ拾うか」として示せると、W2 の説明が主張から事実になる

### 7.2 AI-DLC から借りるとよいもの

| AI-DLC の仕組み | codd-agent で考えること |
|---|---|
| コミットの出所（`attest resolve`）: 承認された中身と、コミットされた中身を突き合わせる | `.plans/` の記録に、verify-apply を通ったときの中身のハッシュは控えている（`applied.json`）。コミットとの突き合わせを `lint` に足せば、「codd を通ったが、そのあと手で変えた」を見分けられる |
| 出典タグの検査（claim-sources）: 文書の主張ごとに出典を求める | 計画の前提・制約にはすでに根拠のパスを求めている。設計書そのものの記述にまで広げるかは、重さと引き換えなので慎重に |
| ハーネスの広さ: 1 つの中身から 7 種のハーネスへ書き出す | `agents/codd-agent.md` 1 つから Kiro と Copilot へ写す形はできている。Claude Code・Codex への書き出しを足すのは小さな仕事で、AI-DLC と同じ場に並べやすくなる |
| 依頼の大きさに応じた軽い流れ（express・composer） | codd-agent はすでに軽い。逆に、`max_files` を超える依頼を「AI-DLC の intent に回すべき大きさ」と伝える一文を、終わりの報告の「今回やらないこと」に添える案がある |

### 7.3 やらないこと

- 要件の聞き取り・ストーリー・NFR・運用の段を足す（AI-DLC と正面から重なり、単純なハーネスという前提も崩れる）
- 検査の強さの切り替え（advisory / blocking のような段階）を足す（§8.1 で採らなかった理由のとおり、抜け道になる。
  緩められないことが W3 に対する差になる）
- AI-DLC の記録の形式（`aidlc-state.md`・`traceability.json`）を本体で読む（AI-DLC の版が上がるたびに壊れる。
  読むならサンプルのワークフローに閉じる）

## 8. 出典

- [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows)（README・`docs/guide/`・`docs/harness-engineering/06-sensors.md`・
  `docs/reference/06-hooks-and-tools.md`・`core/sensors/`・`core/knowledge/aidlc-shared/brownfield.md`・
  `core/aidlc-common/stages/`・CHANGELOG 2.11.0）
- [AWS DevOps Blog: Open-sourcing adaptive workflows for AI-DLC](https://aws.amazon.com/blogs/devops/open-sourcing-adaptive-workflows-for-ai-driven-development-life-cycle-ai-dlc/)
- [Kiro: Introducing Kiro](https://kiro.dev/blog/introducing-kiro/)（仕様とコードを保つ手段、文書のずれの課題）
- [Kiro: Bugfix specs](https://kiro.dev/docs/specs/bugfix-specs/)
- [Birgitta Böckeler: Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)
  （spec-first / spec-anchored / spec-as-source の分類。本文はこの環境から取得できず、検索結果の要約で確かめた）
- [Thoughtworks Technology Radar: Spec-driven development](https://www.thoughtworks.com/radar/techniques/spec-driven-development)
- [AWS Builder Center: My First Hands-on with AI-DLC, Kiro, and Claude Code](https://builder.aws.com/content/3DL0rrNndOehdyQwqp48zTZKGKY/from-cloud-infrastructure-to-ai-driven-development-my-first-hands-on-with-ai-dlc-kiro-and-claude-code)
  （ステージごとのレビュー往復の時間、監査ファイルの大きさ。個人の報告）
- [Augment Code: Spec-Driven Development for Brownfield Enterprise Codebases](https://www.augmentcode.com/guides/spec-driven-development-brownfield-codebases)
  （Kiro の仕様が実装に追随しないという指摘。競合ベンダーの記事なので傍証にとどめる）
