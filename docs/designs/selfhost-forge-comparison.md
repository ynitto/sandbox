# セルフホスト構成の比較 — GitLab 前提資産の適合

> 最終更新: 2026-07-08 ／ 関連: [`gitea-gitlab-sync-design.md`](gitea-gitlab-sync-design.md)（Gitea 案の設計）,
> [`tools/gitea-sync-bot/`](../../tools/gitea-sync-bot/)（同期ボット）,
> [`tools/kiro-flow/executors/gitlab.py`](../../tools/kiro-flow/executors/gitlab.py)（GitLab 前提資産の中核）
>
> 本書は「上流 GitLab をマスターに保ちつつ、上流アクセス負荷を下げる」ための**ローカル構成の比較資料**。
> **既存の GitLab 前提ツール／スキル資産をどう適合させるか**を軸に、複数案を評価して推奨を示す。

## 0. 確定している前提

- **上流 GitLab がマスター**。目的は「開発者の日常操作を LAN で完結させ、**上流アクセス負荷を下げる**」こと。
- **CI は不要**（GitLab CI 資産の一致は考慮不要 → 最大のトラフィック増幅源も存在しない）。
- **既存資産が GitLab API v4 前提**（後述の棚卸し）。
- ローカルのホストは**個人 Windows PC**（常時稼働・スペックに制約。可用性は弱点）。

## 1. 決定的な切り分け — 「重い部分」と「縛られる部分」は別レイヤ

| レイヤ | 実体 | 重さ | GitLab への縛り |
|---|---|---|---|
| **コード転送** | clone / fetch / push（git データ） | **重い**（バイト量が大きい。10名分の fetch が積み上がる） | **無い**（git プロトコルは forge 非依存） |
| **コラボ（issues/MR/notes）** | GitLab API v4 の JSON | **軽い**（メタデータ JSON） | **強い**（`/api/v4`。Gitea は別 API） |

> **CI 不要なので、コード転送の最大増幅源（パイプラインごとの clone）は無い。**
> 残る「重い」ものは主に開発者の clone/fetch。「縛られる」ものは issues/MR の API だが、これは**軽い**。
> ⇒ **重い部分だけをローカルに寄せ、縛られる（軽い）部分は上流のままにする**選択が成立する（案C）。

## 2. GitLab 前提資産の棚卸し（結合度の実測）

| 資産 | 使っているもの | 結合度 | ローカルを Gitea にした場合の影響 |
|---|---|---|---|
| `kiro-flow/executors/gitlab.py` | `/api/v4` の projects/**issues**/**notes(コメント)**/related_merge_requests を GET/POST/PUT。イシュー駆動ワークフローの中核 | **高** | **動かない**（Gitea API は v4 非互換）。Gitea 用 executor の新規移植が必要 |
| `hermes-gitlab-gateway` | GitLab issues をチャネル化（ポーリング／NAT 裏で動作） | 中〜高 | 移植が必要 |
| `gitlab-review-viewer` | GitLab の issues+MR を並べて表示（Electron） | 中（読み取り） | 移植が必要 |
| `gitlab-obsidian-sync` | GitLab → Obsidian 同期 | 中 | 移植が必要 |
| `issue-mailbox` / `kiro-project` | issues 周辺で GitLab を参照 | 低〜中 | 一部改修 |
| `git-file-sync` / `gitea-sync-bot` | **git レベル（remote URL のみ）** | **無** | **影響なし**（どの forge でも動く） |

**要点**: 縛りは **issues / MR / notes（＝ローカル管理したかった対象）** に集中している。
これらをローカルの **Gitea** に載せると、上表の GitLab 前提資産は**全滅**し、各ツールに **GitLab↔Gitea の二系統**を保守する負担が生じる。

## 3. 案ごとの「適合方法」と構成

### 案A — ローカル GitLab CE（作業インスタンス）
- 構成: 個人PC の WSL2+Docker に GitLab CE。issues/MR/notes とコードをローカルに置き、コードは上流と同期（`gitea-sync-bot` を GitLab↔GitLab で流用）。
- **適合方法**: **既存資産はローカル GitLab に向け先を変えるだけ**（API v4 一致・移植ゼロ）。
- 注意点:
  - GitLab CE は **Pull Mirror（上流→ローカル取り込み）を持たない（EE 専用）** → コード取り込みは結局 `gitea-sync-bot` が担う。
  - **issues/MR は上流と同期する標準機能が無い** → ローカル GitLab に閉じて持つ（上流はコード正本のみ）。
  - **重い（実用 8GB/4vCPU）・個人PCで可用性が弱い・WSL2 のネットワーク/起動の手当てが要る**（詳細は Windows ガイド参照）。

### 案B — ローカル Gitea＋既存ツールを Gitea へ移植
- 構成: [`gitea-gitlab-sync-design.md`](gitea-gitlab-sync-design.md) のまま（軽量）。
- **適合方法**: `executors/gitlab.py` に対応する **`executors/gitea.py` を新規実装**し、viewer/gateway/obsidian-sync も Gitea API 対応にする。
- 評価: **CI 不要でも縛りは issues/notes/MR なので効かない**。移植コストと**二系統保守**が重く、資産が GitLab 前提な現状では割に合わない。

### 案C — 分離：コードだけローカル、issues/MR は上流のまま（★推奨）
- 構成:
  ```
  [開発者 ×10] ──LAN──▶ [ローカル コードミラー] ──(pull/push)──▶ [上流 GitLab（マスター/issues/MR）]
     clone/fetch/push(コード)      軽量な git 置き場        既存ツールは上流の API v4 をそのまま利用
     issues/MR/notes ────────────────────────────────────────▶  (軽量 JSON。負荷は小)
  ```
  - **ローカルは「コードの置き場」だけ**（重い clone/fetch を LAN で吸収）。実体は次のいずれでもよい:
    - Gitea を**コード専用**で使う（issues/MR 機能は未使用）／
    - bare リポジトリ + `git-http-backend`／SSH（最軽量）／
    - 読み取りキャッシュ型（案E）。
  - **issues/MR/notes は上流 GitLab に置いたまま**。
- **適合方法**: **既存の GitLab 前提資産は一切改修しない**（従来どおり上流 `/api/v4` を叩く）。
- 効果と根拠:
  - **重いコード転送は LAN で吸収**され、上流に残るのは issues/MR の**軽量 API**と push だけ → **目的（上流負荷削減）を達成**。
  - **移植ゼロ・二系統保守なし・個人PCでも軽い**。ローカルのコードミラーが落ちても、上流から clone に切り替えれば作業継続できる（可用性リスクを最小化）。
- トレードオフ:
  - 当初の希望「issues/MR も**ローカル管理**」からは外れる（上流のまま）。ただし issues/MR の**トラフィックは小さい**ので、負荷削減目的には影響しない。
  - issues/MR の UI/API レイテンシは上流ネットワーク依存（オフライン耐性は無い）。**これが実要件なら案A/案D**。

### 案D — GitLab Geo セカンダリ（別案・要 EE/有料）
- 構成: 上流 GitLab の**読み取りレプリカ**をローカルに置く（Geo secondary）。clone/fetch はローカルで応答、push は上流へプロキシ。
- **適合方法**: **UI も API も GitLab のまま** → 既存資産は**完全にそのまま**動き、コード転送も削減、上流マスターも維持。
- 注意点: **GitLab Premium（有料）が前提**。かつ**重い**（レプリカ相応のリソース）。予算が許し、"全部 GitLab のまま最小手" を最優先するなら最有力。

### 案E — 読み取りキャッシュ / ミラー（案Cの軽量変種）
- 構成: `git` の HTTP キャッシュ（例: `git-cache-http-server`）や、Gitea/GitLab の**ミラー(取り込み専用)** をローカルに置き、**clone/fetch をキャッシュから**返す。push と issues/MR は上流直。
- **適合方法**: 既存資産は上流のまま（改修ゼロ）。開発者の remote だけキャッシュ URL に向ける。
- 評価: CI 不要で読み取り主体なら**必要十分で最軽量**。書き込みは上流に出る（コード push の頻度・サイズ次第）。

## 4. 比較表

| 観点 | A: ローカルGitLab CE | B: Gitea+移植 | **C: 分離(推奨)** | D: GitLab Geo | E: キャッシュ |
|---|---|---|---|---|---|
| 既存 GitLab 資産の適合 | ◎ 向け先変更のみ | △ 全面移植・二系統保守 | ◎ **改修ゼロ**（上流のまま） | ◎ 完全そのまま | ◎ 改修ゼロ |
| 上流アクセス負荷の削減 | ◎ | ◎ | ○ **コードは削減**/issues小 | ◎ | ○ 読み取り削減 |
| ローカルの軽さ（個人PC） | ✕ 重い(8GB級) | ◎ 軽い | ◎ **軽い** | ✕ 重い | ◎ 最軽量 |
| 可用性（個人PCが落ちる） | ✕ issues/MRごと停止 | △ issues/MR停止 | ◎ **上流へ即フォールバック** | △ | ◎ |
| WSL2 ネットワーク/起動の手間 | 多い | 少 | 少 | 多い | 少 |
| 実装/運用コスト | 中 | **高** | **低** | 中(＋費用) | 低 |
| issues/MR のローカル管理 | ○ ローカル | ○ ローカル | ✕ 上流のまま | ○(レプリカ) | ✕ 上流 |
| 費用 | 無料 | 無料 | 無料 | **有料(EE)** | 無料 |

## 5. 推奨

**前提（上流マスター／CI 不要／資産が GitLab v4 前提／個人PC）では、案C（分離）が最適。**

- 既存の GitLab 前提資産を **1 行も直さず**（上流 `/api/v4` のまま）、**重いコード転送だけを LAN のコードミラーで吸収**して上流負荷を落とせる。
- 個人PCでも**軽く**、ミラーが落ちても**上流へフォールバック**できるため可用性リスクが小さい。
- コードのローカル置き場は `gitea-sync-bot`（既存・remote 非依存）で上流と ff 同期すればよく、**新規実装は不要**。

**例外（案C を採らない条件）**:
- issues/MR を**トラフィック以外の理由**（オフライン耐性・UI レイテンシ・LAN 内完結）で**必ずローカルに置きたい** → 資産をそのまま活かすなら **案A（重さ・可用性を許容）**、予算があれば **案D（Geo）**。
- 逆に「とにかく最軽量で読み取り負荷だけ落としたい」→ **案E**。

## 6. 確認したい1点（実装に進む前）

「issues/MR を**ローカルに置くこと自体**（＝トラフィック以外の理由）」は必須要件ですか？

- **不要（負荷削減が目的）** → **案C** で確定。コードミラー（Gitea コード専用 or bare+http）＋ `gitea-sync-bot` 同期の手順に落とす。
- **必須** → **案A**（資産そのまま・重さ許容）で WSL2+Docker GitLab 構成を詰める。予算次第で **案D** も比較。
