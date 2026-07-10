# git worktree 共有キャッシュ パターン設計書

> 作成日: 2026-06-29
> 対象ブランチ: `claude/kiro-worktree-cost-reduction-8dlgf9`
> 初出の適用先: `tools/kiro-flow/kiro-flow.py`, `tools/kiro-project/kiro-project.py`
> 位置づけ: **特定ツールに依存しない汎用パターン**。git リモート（GitLab/GitHub 等）を
> 「タスクのたびに clone して作業/検証する」あらゆるツール・スキルへ転用できる。

---

## 0. このドキュメントの使い方

「リモート repo を一時ディレクトリへ clone → 作業/検証 → 捨てる」を**繰り返す**ツールは、
リモート（特に GitLab の `git-upload-pack`）に毎回フル/浅 clone を投げて重い pack 生成を
させている。本パターンはそれを **「ホスト共有の bare ミラー1本 + ローカル worktree」** に
置き換え、ネットワーク負荷を「初回1回 + 増分」に圧縮する。

転用したい人は **§3 の不変条件**と **§6 の API 契約**だけ守れば、§7 のチェックリストで
自分のツールへ移植できる。kiro 固有の話は §8 に隔離してある。

---

## 1. 背景・適用条件

### 解決する問題
- 同一リモート repo を **タスク/検証のたびに clone** しており、GitLab で以下が毎回走る:
  - ref advertisement / pack negotiation / **pack 生成（CPU・帯域）**
- フル clone は履歴+blob 全部、浅 clone (`--depth 1`) でも 1 スナップショット分の blob を毎回転送。

### 効く条件（重要）
本パターンの削減効果は **「同一 repo を複数回触る」再利用から生まれる**。
- ✅ 効く: 1 プロジェクトを多タスクで回す自律ループ、worker と検証が同じ repo を触る、run を跨いで同じ repo を使う。
- ⚠️ ほぼ中立: ある repo を**生涯1回しか**触らない単発用途（ミラー初期化の分むしろ僅かに割高）。

→ 「繰り返し同じリモートを触るか?」が採用判断の分かれ目。

---

## 2. アーキテクチャ

```
            ┌──────────────── ホスト共有キャッシュ root（例 $TMPDIR/<tool>-cache/）─────────────┐
            │   <sha1(url)>.git   … URL 単位の bare ミラー（--mirror --filter=blob:none）      │
            │   <sha1(url)>.lock  … その URL の全変更を直列化する flock                         │
            └───────▲────────────────────────────▲───────────────────────────────▲────────────┘
        fetch(増分) │            worktree add     │ (純ローカル・GitLab通信なし)   │
            ┌───────┴───────┐          ┌──────────┴─────────┐           ┌──────────┴─────────┐
            │ task worktree  │          │ verify worktree    │           │ accept worktree    │
            │ (temp/detached)│          │ (temp/detached)    │           │ (temp/detached)    │
            └───────────────┘          └────────────────────┘           └────────────────────┘
```

- **bare ミラー（共有・長命）**: `git clone --mirror --filter=blob:none <url> <cache>`。
  URL 単位で 1 本。プロセスを跨いでホスト内で共有 → ここが負荷削減の本体。
- **worktree（一時・使い捨て）**: タスク/検証ごとに temp へ `git worktree add --detach`。
  ネットワーク通信ゼロ。従来どおり作業後に `rmtree` で捨てる（テンポラリ運用は不変）。
- **URL ロック**: cache への全変更（init/fetch/worktree add/prune）を flock で直列化。

---

## 3. 不変条件（転用時に必ず守る）

この 3 つを破ると「古い repo で作業」「共有 cache の汚染」「ネットワーク障害で即失敗」に化ける。

### INV-1: 鮮度 — 「毎回 fetch してから、fetch 後の SHA で worktree を作る」
- provisioning のたびに**必ず** `git -C <cache> fetch` を実行する（"cache に既にある" で skip しない）。
- worktree は **fetch 直後に解決した `origin/<ref>` の SHA** から作る（cache に残った古い ref を使わない）。
- `--filter=blob:none` でも commit/tree は fresh に取得され、blob は当該 SHA 分だけ backfill される
  ので、checkout 内容は最新コミットと一致する（鮮度は劣化しない）。
- **fetch 失敗時に古い cache を黙って使うのは禁止**。後段の「失敗→NG/フォールバック」へ倒す。

### INV-2: 共有 cache の保全 — 「直列化 + 自己修復 + auto-gc 無効」
- cache の全変更は **URL ロック下**でのみ行う（並行 writer による index.lock/pack 競合を排除）。
- cache 作成時に **`git config gc.auto 0`**（worktree 生存中の自動 repack 事故を防ぐ）。
- fetch が壊れ系エラー（`not a git repository` / `bad object` / `corrupt` 等）→
  **cache を rmtree して再ミラー（nuke & re-mirror）**。共有のため汚染は持続するので自己修復が必須。
- `worktree add` が `locked`/`already registered` で失敗 → `worktree prune` してから 1 回リトライ。

### INV-3: 多段フォールバックで下限を「現状」に固定
- 「partial clone 非対応サーバ」「ロック取得タイムアウト」「worktree add 失敗」等では、
  **従来の direct clone（フル/浅）へ自動退避**する。
- これにより最悪ケースでも **挙動は現状と同等**（本パターンは下振れを作らない）。

---

## 4. GitLab 負荷の見積り（なぜ効くか）

1 バックログ（同一 repo・タスク数 T・worker 数 W・consumer = worker/verify/accept）で比較:

| | 現状 | 本パターン |
|---|---|---|
| 履歴転送 | run 毎・consumer 毎に再送 | **生涯 1 回**（初回ミラーのみ）、以後は差分 |
| worker | フル clone × W | 増分 fetch のみ |
| verify | 浅 clone × T | 増分 fetch × T |
| acceptance | 浅 clone × 評価回数 | 増分 fetch |
| worktree 生成 | — | **GitLab 通信 0** |

- 概算: 現状 `≈ W·(S_history+S_blob) + (T+1)·S_blob` → 本パターン `≈ S_history(初回) + Σ S_delta + blob backfill(コミット重複分は控除)`。
- **削減されるのは「pack 生成 CPU + 帯域」**。リクエスト**回数**は鮮度のため毎回 fetch する分ほぼ不変
  （回数も減らすにはバッチ化が要るが、それは鮮度とのトレードオフ）。
- `--filter=blob:none` はサーバの `uploadpack.allowFilter` が必要（GitLab/GitHub は対応）。
  非対応なら INV-3 のフォールバック、もしくはミラーを filter 無しにする。

---

## 5. エラー耐性の設計

| 事象 | 対策 |
|------|------|
| 一過性のネットワーク障害 | fetch を指数バックオフでリトライ（clone と同じ流儀） |
| cache 破損（中断 pack / index.lock） | health check → nuke & re-mirror（INV-2） |
| worktree admin リーク（temp だけ消えた） | 次回利用時 `worktree prune` で回収。`rmtree` 後にも best-effort prune |
| SIGKILL/OOM で temp 残留 | pid 入り temp 名 + 既存 janitor の age-sweep（従来どおり） |
| ロック長期保持で他プロセスが詰まる | ロック取得タイムアウト → direct clone へ degrade（INV-3） |
| partial clone 非対応サーバ | 検出してフォールバック（INV-3） |
| disk 逼迫（ミラー永続） | cache root の age-sweep + サイズ上限。ミラーは N 個のフル clone より遥かに小さい |

> 移植元が「verify は 1 発 clone・リトライ無し」だった場合、本パターン化のついでに
> **fetch リトライが入る＝エラー耐性は純増**になることが多い。

---

## 6. API 契約（実装の最小インターフェース）

転用先はおおむね以下 4 関数（名前は任意）を用意すれば足りる。すべて **URL ロック下**で動く。

```
cache_path_for(url) -> str
    URL → ホスト共有 cache のパス（sha1(url) でディレクトリ名を決める）。

ensure_cache(url) -> str | None
    cache が無ければ --mirror --filter=blob:none で作成（gc.auto=0）。
    破損していれば nuke & re-mirror。失敗時 None（呼び出し側はフォールバック）。
    ※ ここでは fetch しない。鮮度は provision 側（INV-1）が担保する。

provision_worktree(url, ref, dest) -> str | None
    1) ensure_cache(url)
    2) fetch（リトライ付き。INV-1: 必ず実行）
    3) sha = rev-parse FETCH_HEAD or origin/<ref>   ← fetch 後に解決
    4) git -C <cache> worktree add --detach <dest> <sha>
       失敗時は prune→1回リトライ、なお失敗なら direct clone フォールバック
    返り値 dest（成功）/ None（最終的に失敗）。

release_worktree(dest) -> None
    shutil.rmtree(dest) + best-effort `git -C <cache> worktree prune`。
```

書込ワークフロー（worker 系）の要点:
- worktree は **detached のまま**。作業ブランチは checkout せず
  `git push origin HEAD:refs/heads/<branch>` で送る → 「同一ブランチ二重 checkout 不可」を回避。
- push reject 時は「cache へ fetch → detached のまま `git reset/rebase` → 再 push」。

検証ワークフロー（verify/accept 系）の要点:
- 読み取り checkout なので detached worktree がそのまま使える。cwd は **worktree ルート**。

---

## 7. 採用チェックリスト（他ツール/スキルへの移植手順）

1. [ ] そのツールは**同じリモートを繰り返し**触るか?（§1。単発なら導入しない）
2. [ ] cache root を決める（設定可能に。default は `$TMPDIR/<tool>-cache/`、ホスト共有）。
3. [ ] §6 の 4 関数を実装。URL ロックは既存の flock ヘルパーを流用。
4. [ ] 既存の `clone → 作業 → rmtree` 箇所を `provision_worktree → 作業 → release_worktree` に置換。
5. [ ] INV-1（毎 fetch・fetch 後 SHA）・INV-2（直列化/自己修復/gc.auto=0）・INV-3（フォールバック）を満たす。
6. [ ] 書込系は detached + `push HEAD:refs/heads/<branch>` に変更。
7. [ ] janitor に cache root の age-sweep と `worktree prune` を追加。
8. [ ] サーバの partial clone 可否を確認（不可ならフォールバック経路をテスト）。
9. [ ] git ≥ 2.5（`worktree add --detach`）/ partial clone は git ≥ 2.20 を要件に記載。

---

## 8. 適用先メモ（kiro-flow / kiro-project）

初出の適用は以下。詳細は各ツールの設計書を参照。
- **kiro-flow**: `ensure_workspace_clone`/`_clone_repo`/`_prepare_run_branch`/`finalize_workspace`/
  `cleanup_workspace` を本パターンへ置換。書込先 repo を detached worktree で作業し、
  `push HEAD:refs/heads/kf/<run_id>` で送る。`GitBus` の sparse バスクローンは別系統（既に再利用機構あり）。
- **kiro-project**: `_clone_repo_shallow`（verify/acceptance）を共有 cache + worktree へ置換。
  検証は最新の target ブランチを毎回 fetch してから worktree を作る（INV-1）。
- 両ツールは **同じ cache root を共有**（ホスト共有スコープ）。URL ロックで跨プロセス協調。

---

## 9. 既知の制約・非目標

- リクエスト**回数**は減らさない（鮮度優先）。回数削減はバッチ fetch の別パターンへ。
- ミラーはホスト内共有のため、**マルチホスト分散**では各ホストに 1 本ずつできる（それで十分。
  ホスト跨ぎの共有はしない＝NFS 等の共有 FS 上に置くのは lock セマンティクス的に非推奨）。
- 認証情報は URL/ヘルパー（git credential / CI トークン）に従う。本パターンは資格情報を持たない。
