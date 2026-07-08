# Gitea ⇄ GitLab 同期 — 設計書

> 最終更新: 2026-07-08 ／ 関連: （新規インフラ構成。実装は未着手）
>
> 本書は「LAN 内 Gitea を Issue/MR の管理面にしつつ、コードは GitLab と同期する」構成の
> **設計正典**。実装と差が出たら本書を更新する。

## 0. 背景と要求

- GitLab サーバーへのトラフィックを**最大限下げたい**（clone/fetch/push を LAN 内で完結させたい）。
- **Issue / MR は Gitea（LAN 内）で管理**したい。
- **コードは GitLab をマスター（正本）として同期**したい。
- 想定規模: **最大 10 名程度のユーザー（PC）**。
- 確定した方針（ヒアリング結果）:
  1. コードの書き込みは**双方向**（Gitea 側・GitLab 側の双方で独立に push が起こり得る）。
  2. Issue/MR は当面 **Gitea 一本化**でよいが、**将来 GitLab と同期できるよう概要設計だけは行う**。

## 1. 結論（実現可能性）

- **実現可能**。10 名規模は Gitea にとって全く負荷にならない（Go 製シングルバイナリ、小型機でも動作）。
- ただし **「双方向のコード同期」は Gitea/GitLab の標準ミラー機能だけでは実現できない**。
  標準ミラーは片方向（Pull Mirror = 取り込み専用 / Push Mirror = 送出専用）で、
  両側が同じブランチへ独立に書くと衝突・巻き戻り（force 上書き）が起きる。
- したがって本設計では、**「同期ロボット（reconcile daemon）」を1つ挟む**構成を採る。
  同期ロボットは **fast-forward 可能なときだけ自動同期し、分岐（diverge）したら自動 force せず人手に上げる**。
- Issue/MR は Gitea を正とし、GitLab へは**将来的に API 連携で片方向反映**できる余地を残す（§5）。

## 2. 全体アーキテクチャ

```
                         LAN (高速・低遅延)                     │  WAN
                                                               │
  [開発者 PC ×10]                                              │
     │  clone / push / issue / MR / merge  (ここで完結)         │
     ▼                                                         │
  ┌───────────────┐        ┌─────────────────────┐            │      ┌──────────────┐
  │  Gitea        │◀──────▶│  reconcile daemon     │◀──────────┼─────▶│  GitLab      │
  │  (作業マスター) │  fetch │  (fast-forward 調停)  │  fetch/push│      │ (正本/CI/対外)│
  │  issue / MR    │  push  │  webhook + cron 起動   │            │      │              │
  └───────────────┘        └─────────────────────┘            │      └──────────────┘
```

- **開発者の日常操作（clone/fetch/push/MR/merge/issue）は Gitea で完結** → GitLab への WAN トラフィックは
  「同期ロボットの差分同期」だけに圧縮される（要求①を満たす）。
- **GitLab は正本（バックアップ／対外公開／GitLab CI）** として最新を保持する。GitLab 側の push
  （外部チーム、GitLab CI のタグ付け/バージョンバンプ等）も同期ロボットが Gitea へ取り込む（双方向）。

### 2.1 「マスター」という語の整理

要求の「GitLab をマスターとして同期」は、本設計では次の意味に確定する:

- **正本（source of truth の保管先）= GitLab**：常に最新の全履歴を保持し、バックアップ・対外公開・CI の基盤。
- **作業マスター（日々 write する場）= Gitea**：チームの clone/push/MR/merge の実体。
- 双方向要求があるため GitLab 側 write も許容するが、**同一ブランチへの同時 write は運用ルールで抑制**する（§4）。

## 3. 双方向コード同期の方式（本設計の核心）

### 3.1 なぜ標準ミラーでは不可か

- Gitea **Pull Mirror**: リポジトリがローカル read-only になり、Gitea 側で push/merge できない → Issue/MR 管理と両立しない。
- Gitea **Push Mirror**: Gitea→GitLab の片方向。GitLab 側 write を拾えず、force 上書きで GitLab の独自コミットを消しうる。
- 双方が独立に同じブランチを進めた「分岐」は、git の性質上**自動では安全に統合できない**（マージ＝新コミットが必要）。

### 3.2 採用方式: fast-forward 調停ロボット

同期ロボットは**保護対象の各 ref（ブランチ/タグ）**について、両側の HEAD を比較して次の分類で動く。

| 状態 | 判定 | アクション |
|---|---|---|
| 両側同一 | in-sync | 何もしない |
| Gitea だけ進行 | ff (Gitea→GitLab) | GitLab へ **fast-forward push** |
| GitLab だけ進行 | ff (GitLab→Gitea) | Gitea へ **fast-forward push** |
| 双方進行（分岐） | **diverged** | **自動同期しない**。片側に「統合用 MR」を自動起票し、担当者へ通知（§3.4） |
| 片側に新規 ref | create | 反対側へ ref を作成 |
| 片側で ref 削除 | delete | **既定では自動削除しない**（誤削除防止）。削除同期は許可ブランチのみ opt-in |

- **絶対に `--force` で追従しない**のが安全設計の肝。分岐は必ず人手（MR）で解決する。
- 起動契機は **(a) webhook（Gitea/GitLab の push イベント）で即時**、**(b) cron で定期（例: 2–5 分）** の二段構え。
  webhook が落ちても cron が拾う。

### 3.3 ロボットの1パス擬似コード

```
for repo in repos:
    git fetch gitea --prune
    git fetch gitlab --prune
    for ref in protected_refs(repo):
        g  = rev(gitea/ref)      # 無ければ null
        l  = rev(gitlab/ref)     # 無ければ null
        if g == l: continue
        if l is null:            create ref on gitlab from g; continue
        if g is null:            create ref on gitea  from l; continue
        base = merge_base(g, l)
        if base == l:            push --ff gitea/ref -> gitlab   # Gitea だけ進行
        elif base == g:          push --ff gitlab/ref -> gitea   # GitLab だけ進行
        else:                    handle_diverged(repo, ref, g, l)  # §3.4
```

- 決定的（determinstic）・単発・有界。各 git 呼び出しに個別タイムアウト。ロックで多重起動を防止。
- **LFS**: 対象なら `git lfs fetch --all` を同期に含める（別途検証が必要な既知の癖あり）。

### 3.4 分岐（diverged）時の扱い

1. 同期ロボットは**どちらのコミットも消さない**。
2. 「作業マスター＝Gitea」の原則により、**GitLab の分岐コミットを Gitea 側に統合ブランチとして取り込み**、
   Gitea 上に **統合用 MR**（例: `sync/integrate-gitlab-<branch>-<shortsha>`）を自動起票する。
3. 担当者が Gitea 上でマージ解決 → 通常フローで Gitea が進行 → 次パスで GitLab へ ff 追従。
4. 通知は Gitea Issue コメント＋（任意で）チャット webhook。

### 3.5 運用ルール（衝突頻度を下げる前提条件）

- **同一ブランチへの同時 write を避ける**運用にする（双方向を「安全に」動かす最大のコツ）。
  - 例: `main` は Gitea 側マージのみ。GitLab CI が push する対象は `ci/*` や tag に限定。
  - 外部チームが GitLab へ出す場合は専用ブランチ（`ext/*`）に隔離し、統合は Gitea 側 MR で行う。
- 保護ブランチ（`main` 等）は両側で branch protection を有効化。

## 4. インフラ構成

### 4.1 コンポーネント

- **Gitea**: docker-compose（`gitea` + `postgres`）。10 名なら SQLite でも可だが運用性で PostgreSQL 推奨。
- **reconcile daemon**: 小さなコンテナ（cron + 同期スクリプト）。両リモートへの認証情報を保持。
- **リバースプロキシ/TLS**: LAN 内でも HTTPS 化推奨（社内 CA でも可）。
- **バックアップ**: Gitea データ（リポジトリ + DB + `custom/`）を日次バックアップ。GitLab が正本なので二重の保全。

### 4.2 サイジング（10 名）

- 2 vCPU / 4 GB RAM / SSD で十分。Gitea 自体は数百 MB メモリ級。将来 Gitea Actions を LAN 内 CI に使うなら
  ランナー分のリソースを追加。

### 4.3 認証（最小権限）

- GitLab 側: 同期専用の **Project Access Token（`write_repository` のみ）**。
- Gitea 側: 同期専用ボットユーザー＋リポジトリ権限。
- トークンはロボットコンテナのシークレットに限定保管。開発者 PC には配布しない。

## 5. Issue/MR の将来同期（概要設計のみ）

当面は **Gitea 一本化**（GitLab の Issue/MR 機能は使わない）。ただし将来 GitLab と同期できるよう、
以下の**概要設計**を先に固定しておく（実装は行わない）。

### 5.1 方針

- コード同期（§3）とは**独立したコネクタ**として設計する（疎結合）。git 同期ロボットに混ぜない。
- 同期は **Gitea を正**とした**片方向（Gitea→GitLab）反映を第一段**とし、双方向は第二段の拡張余地とする。
- 実装手段は両者の REST API（Gitea API / GitLab API）＋ **外部 ID マッピング表**（後述）。

### 5.2 対応関係（マッピング）

| Gitea | GitLab | 備考 |
|---|---|---|
| Issue | Issue | タイトル/本文/状態/ラベル/担当者/コメント |
| Pull Request | Merge Request | ブランチ名・状態を対応付け（コードは §3 で既に同期済み前提） |
| Label / Milestone | Label / Milestone | 名前ベースで突き合わせ、無ければ作成 |
| User | User | **アカウント対応表が必須**（メール or 手動マップ） |
| Comment | Note | 冪等化のため元 ID を本文フッタ or 外部保存に記録 |

### 5.3 同期状態の保持

- **マッピング DB**（`gitea_id ⇄ gitlab_id ⇄ last_synced_hash`）を1つ持ち、
  再実行しても重複作成しない（**冪等**）。コメントは内容ハッシュで差分検知。
- 双方向化する場合は各レコードに **updated_at と更新元（origin）** を持たせ、
  新しい方を勝ちとする last-write-wins ＋ 競合時は片側にコメントで注記（自動マージはしない）。

### 5.4 起動

- Gitea/GitLab の webhook（issue/comment/MR イベント）で差分反映＋ cron で全体照合（取りこぼし回収）。

### 5.5 現時点で確定させる制約

- 完全な双方向 Issue/MR 同期は**運用負荷が高い**（ユーザー対応表・権限・通知の二重化）。第一段は片方向に留める。
- **コードのブランチ/コミットは §3 で同期済み**であることを Issue/MR 同期の前提にする（MR の対象 ref が両側に存在する）。

## 6. リスクと制約（明示）

- **双方向 git 同期は「無設定で安全」ではない**。安全性は「fast-forward のみ自動 / 分岐は人手」という規律に依存する。
- **同一ブランチ同時 write が多いと分岐 MR が頻発**する。運用ルール（§3.5）で write 方向を分離するほど楽になる。
- Push Mirror の素朴な force 同期は GitLab の独自コミットを消しうるため**使わない**（本設計はロボット経由）。
- LFS / 巨大リポジトリ / サブモジュールは追加検証が必要。
- Issue/MR 同期は本設計では**概要のみ**。実装時に API レート・ユーザー対応表・冪等化の詳細設計が必要。

## 7. 段階的導入ステップ（提案）

1. **フェーズ0**: Gitea を docker-compose で LAN に構築（TLS・バックアップ込み）。1 リポジトリで PoC。
2. **フェーズ1**: reconcile daemon を導入し、`main` を **片方向（Gitea→GitLab, ff のみ）** で同期して安定確認。
3. **フェーズ2**: GitLab 側 write を許容し**双方向 ff ＋ 分岐 MR 自動起票**を有効化。運用ルール（§3.5）を確定。
4. **フェーズ3（任意・将来）**: Issue/MR コネクタ（§5）を片方向から実装。

---

### 付録: 判断の要点（なぜこの構成か）

- 要求「GitLab トラフィック最小化」「Issue/MR を Gitea 管理」を素直に満たすには、**日々の write を Gitea 側に寄せる**のが最適。
- 一方で「双方向 write」要求があるため、**片方向ミラーではなく調停ロボット**が必要。
- 調停ロボットを **fast-forward 限定＋分岐は人手**にすることで、**自動化の利便**と**履歴を壊さない安全性**を両立する。
