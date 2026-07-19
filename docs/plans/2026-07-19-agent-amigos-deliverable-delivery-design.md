# agent-amigos 成果物の納品設計 — 納品棚と受け取り動線

> 作成日: 2026-07-19 ／ ステータス: **実装済み**
> 実装: `tools/agent-amigos/agent_amigos/delivery.py`（搬出・納品書）、
> `tools/agent-dashboard/src/features/amigos/main/deliveries.js`（納品一覧）＋
> `missions.js` の `readDeliverablePreview`（受入プレビュー）。
> 正典スキーマ: `schemas/delivery.schema.json`。
> 前提: [`agent-amigos-design.md`](../designs/agent-amigos-design.md) §8（統合と返却）、
> [`agent-project-design.md`](../designs/agent-project-design.md) §5.4（納品書・DELIVERY.md）

## TL;DR

作るもの: **accept 時にオーナーホームの `deliveries/<mid>/` へ成果物を自動搬出する「納品棚」**と、
その納品書（`delivery.json` + `DELIVERY.md` 追記）、そして agent-dashboard の
**受入プレビューと納品一覧**。

主要な決定は 3 つ。

1. 納品は push 型にする（accept の副作用として搬出）。collect は補助に降格。
2. 成果物の正本は種別で分ける。コードは workspace.repo、文書と調査はファイル本体を納品棚へ、
   大きいバイナリは repo か共有パスへ逃がして納品棚には参照を置く。
3. dashboard は納品棚とバスを読むだけ。受入操作は既存の commands 投函（accept / reject）を使い、
   新しい書き込み経路は作らない。

却下した主要案: バスを永続納品先にする案（gc と衝突）、dashboard に独自のダウンロード API を
生やす案（結合規律を破る）。

読むべき人: agent-amigos の受入フローを触る人、dashboard の Amigos タブを触る人。

---

## 1. 何が問題か

現状の返却経路は「integrator が `deliverable/` を組み、オーナーが `collect --out` で取り出す」
だけで、次の 3 つが欠けている。

1. **永続先がない**。バスは gc される（GitBus はブランチ削除、ローカルは keep-days）。
   accept 済みの成果物でも、collect し忘れれば消える。取り出した後も「どのミッションの
   成果物をどこに置いたか」は依頼者の記憶頼み。agent-project には archive/ と DELIVERY.md が
   あるのに、amigos には対応物がない。
2. **人が読む形で提示されない**。成果物はプログラムに限らず調査結果・ドキュメント・画像に
   及ぶのに、受入判定の材料は CLI の status とファイルを自分で開くことだけ。dashboard は
   deliverable の有無と partial フラグしか見せない。受入待ち（reviewing）で止まっている
   ミッションに気づいても、中身を見るには端末へ移動するしかない。
3. **受け取りの動線が CLI に閉じている**。dashboard から post した依頼者が、同じ画面で
   成果物を確認して受け取る道がない。post → 進捗閲覧までは dashboard で完結するのに、
   最後の一歩だけ CLI に戻るのは不揃い。

## 2. 全体像

```
 integrator ──▶ バス deliverable/ + MANIFEST.json     … 一時的な受け渡し場所（現状どおり・gc 対象）
                     │
                     │ owner: accept（manual / agent）
                     ▼
 owner デーモン ──▶ <home>/deliveries/<mid>/           … 納品棚（永続・gc 対象外）
                     ├ 成果物ファイル本体（文書・調査・小さい画像）
                     ├ delivery.json                    … 納品書（機械可読）
                     └ <home>/DELIVERY.md へ 1 行追記    … 納品一覧（人間可読）
                     ▲
 agent-dashboard ────┘ 読むだけ。受入待ちのプレビューはバスの deliverable/ を読み、
                       受入操作は commands 投函（accept / reject — 実装済みの経路）
```

バスの `deliverable/` は「受け渡しの場」、納品棚は「受け取った後の置き場」。
agent-project の needs（検収待ち）→ archive + DELIVERY.md（納品）と同じ二段構えに揃える。

## 3. 設計判断

### 3.1 push 型納品を既定にする（collect は補助へ）

**判断**: accept が成立した時点で、owner デーモンが `deliverable/` を
`<home>/deliveries/<mid>/` へ搬出する。`collect` コマンドは残すが、
「納品棚以外の場所へ改めてコピーしたい」ときの補助に位置づけを変える。

**文脈**: 依頼者が取りに行く pull 型は、取り忘れという失敗モードを持つ。バスが gc される以上、
取り忘れは成果物の喪失に直結する。accept という明示の意思表示があるのだから、
その瞬間に手元へ確保するのが自然だと思う。

**却下した案**:

- **バスを永続納品先にする** — gc 方針と真っ向から衝突する。バスを消せなくなるか、
  納品済みだけ選択的に残す複雑さを抱えるかの二択になり、どちらも悪い。
  バスは調整の場であって倉庫ではない（設計書 §8.3 の分離原則）。
- **collect 必須のまま通知だけ強化する** — 通知を見て手を動かすのは人。
  自動化できる搬出を人の作業として残す理由がない。

**トレードオフ**: 納品棚の容量は増え続ける。gc は `agent-amigos gc` に
`--deliveries-keep-days`（既定は無期限）を足して人の判断で消す。自動では消さない。
確信度: 高。

### 3.2 成果物の正本は種別で分ける

**判断**: 納品棚に本体を置くのは文書・調査結果・小さい画像などのファイル成果物。
コードは従来どおり workspace.repo の統合ブランチが正本で、納品棚には参照
（repo / branch / commit hash）だけを書く。ファイルサイズ上限（既定 10MB/ファイル）を超える
バイナリは搬出せず、MANIFEST の由来情報とともに「参照のみ」として納品書に記す。

**文脈**: 成果物の多様さがこの設計の出発点だった。全部をバス経由にすると動画一つで
バスリポジトリが死ぬし、全部を repo にすると「調査レポート 1 枚のために
リポジトリを掘る」ことになる。境界はサイズと種別で機械的に引く。

**却下した案**: 種別ごとに納品先ツールを変える案（文書は Obsidian、画像は共有フォルダ、の
ような振り分け）。届け先が散ると「どこに何があるか」の問いが復活して、納品棚を作る意味が
半減する。まず一箇所に集め、外部への転送（wiki-use への取り込みなど）は納品後の
任意の後段に置く。

**トレードオフ**: 10MB という線は暫定。integrator が大きい成果物を作る運用が実際に出てから
見直せばよい。確信度: 中。

### 3.3 納品書は delivery.json と DELIVERY.md の二層

**判断**: 搬出時に納品棚へ `delivery.json` を書き、ホーム直下の `DELIVERY.md` へ 1 行追記する。

`delivery.json` の中身（正典スキーマは `schemas/delivery.schema.json` として切る）:

- ミッションのメタ: mid / title / goal / 受入日時 / acceptance の種別（manual か agent か）
- 受入結果: accepted、partial とその理由（MANIFEST から引き継ぐ）
- ファイル一覧: パス・ハッシュ・由来ロール（MANIFEST から）・搬出済みか参照のみか
- コード成果物の参照: repo / integration ブランチ / merge commit
- 消費予算: events 集計の execution 秒（依頼者が「いくらかかったか」を後から見られる）

`DELIVERY.md` は人間可読の受領一覧で、agent-project の DELIVERY.md と同じ形式に寄せる。
1 ミッション 1 行、詳細は納品棚の個票へのリンク。

**却下した案**: MANIFEST.json をそのまま納品書として使う案。MANIFEST は integrator の
組み立て記録で、受入結果と消費予算を持たない。受入という事実を刻む場所は別に要る。
確信度: 高。

### 3.4 dashboard は提示面に徹する（書き込み経路を増やさない）

**判断**: dashboard の Amigos タブに次を足す。書き込みは一切足さない。

1. **受入プレビュー**: reviewing のミッションで、バスの `deliverable/` の中身を表示する。
   markdown はレンダー、画像はインライン表示、テキストはそのまま、バイナリはメタ情報のみ。
   同じ画面に accept / reject（フィードバック欄つき）ボタンを置き、既存の commands 投函へ流す。
2. **受け取り済み成果物もミッションの中で見せる**: 納品を独立した一覧にすると、利用者が
   考える単位（ミッション）と画面の単位がずれる。納品はミッションへ結び付け、詳細の
   「受け取った成果物」節で 1 の受入プレビューと同じ見え方で開けるようにする。中身は
   詳細を開いたときだけ読む（一覧のポーリングで全文・画像を運ばない）。gc でバスから
   消えたミッションの納品だけは行き場が無いので「過去の成果物」として別に並べる。

**文脈**: dashboard がバスへ書かない・ホームの commands 投函だけという結合規律は、
amigos feature の README が明文化している既定路線で、ここを崩す理由がない。
プレビューは読むだけなので規律の内側に収まる。accept の実行主体はあくまで
owner デーモン（3.1 の搬出もデーモンの仕事）で、dashboard はボタンを置くだけ。

**却下した案**: dashboard に deliverable のダウンロード API を生やす案。dashboard の
プロセスがファイル搬出の書き手になると、書き手が owner デーモンと dashboard の 2 つに増え、
「真実は常にバス、書き手は所有権で分割」の不変条件が崩れる。ダウンロードしたければ
納品棚をファイラで開けばよい。確信度: 高。

## 4. 受入待ちの通知

reviewing への遷移と納品完了は、依頼者が気づけなければ意味がない。ただしここは
新機構を作らず、既存の仕掛けに乗せるだけにする。

- dashboard: 受入待ちミッションをタブのバッジ数に含める（未回答質問と同列の扱い）。
- CLI: `agent-amigos status` の先頭に受入待ちを出す（現状も phase は出るので、並び順の調整）。

メールや外部通知への転送はやらない。欲しくなったら agent-loop のイベントフックの領分。

## 5. 非目標

- **オンプレ外への共有・公開**はしない（本体の非目標を引き継ぐ）。
- **wiki-use / Obsidian への自動取り込み**はしない。納品棚という一次置き場を固めるのが先で、
  ナレッジ化は納品後に人（またはスキル）が選んでやる後段とする。
- **依頼者がオーナーホームに触れないリモート構成の配送**はしない。納品棚はオーナーホームに
  生える。dashboard がそのホームを homeDirs で見られることを前提にする（hub 構成では
  hub ホストのデータディレクトリと同様、届く範囲の共有で足りる）。
- **reject 済み・cancel 済み成果物の保全**はしない。納品棚に載るのは accept だけ。
  差し戻しの経過はバスの artifacts と events に残っており、それで足りる。

## 6. 実装の当たり

小さい。owner デーモンの accept 処理（`commands.py` / `ownerops.py`）に搬出を足し、
`delivery.json` の組み立ては MANIFEST と mission.yaml と events 集計の合成で書ける。
dashboard 側はプレビューが本体で、`amigos:overview` IPC に deliverable の内容と
deliveries 一覧を足す拡張になる。スキーマの正典化（`schemas/delivery.schema.json`）を
先に切ってから両側を実装する順がよい。
