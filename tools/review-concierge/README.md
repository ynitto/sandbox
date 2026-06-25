# review-concierge

**マージ判断を「1 枚の Obsidian ノート」に集約する独立監視デーモン。**

gitlab-idd の自律ワークフローが `status:review-ready` まで進めたイシューを監視し、
イシュー本文・コメント・MR・差分・CI を **1 枚の判断ノート** に融合して Obsidian Vault に書き出す。
人間はそのノートだけを見て、frontmatter の `decision` を `approve` / `reject` にするだけで判断が GitLab に反映される。

> レビュアーの仕事を「散らばった情報を集めて読む」から「キュレーション済みの 1 枚を見て決める」へ変える。

---

## なぜこの形か（設計上の制約と解）

| 制約 | 解 |
|---|---|
| 既存スキル(gitlab-idd)を肥大化させたくない | **無改修**。`gl.py` のヘルパーを import 再利用するだけの独立ツール |
| 別ループで監視・ヘルプする仕組みが良い | kiro-loop / gitlab-obsidian-sync と同じ **非 LLM デーモン**。状態変化時だけ重い処理を起動 |
| 一枚ページをどこに見せるか | **Obsidian ノート**（callout・Dataview・段階開示・内部リンク） |
| リポジトリに置くと肥大化 | Vault は**コードリポジトリの外**。成果物はコミットされない |
| issue は表現力が乏しい | Obsidian Markdown ≫ GitLab。リスク段階開示・判断キューが使える |
| 課題: issue と MR の往復 | **1 ノートに融合**。`[[issue-<id>]]` で既存ノートへリンク＆バックリンク |
| 課題: 膨大で読みきれない | キュレーション＋**リスク段階開示**（🔴必読は展開・🟡⚪は畳む） |
| マージ責任は人間 | デーモンは勝手にマージしない。**人間が Obsidian で明示承認した時だけ**マージ実行 |

`gitlab-idd` のレビュアー役（受け入れ条件の権威ある評価）はそのまま。本ツールはその上に乗る
**人間向けの準備＋意思決定レイヤ**であり、役割が混ざらないので既存フローを汚さない。

---

## アーキテクチャ

```
 GitLab                      review-concierge (非 LLM デーモン)            Obsidian Vault (repo 外)
 ┌──────────────┐  poll      ┌────────────────────────────────────┐      ┌────────────────────────┐
 │ status:        │─────────▶│ scan: review-ready を検知(差分のみ) │      │ Review/Inbox/issue-42  │
 │ review-ready   │          │   ├ gl.py で issue/MR/diff/CI 取得   │─────▶│   = 1 枚の判断ノート    │
 └──────────────┘          │   ├ 受け入れ条件抽出・リスクトリアージ │      │ Review/Queue.md        │
        ▲                    │   └ [任意] review_command で AI 補強 │      │   = Dataview 判断キュー │
        │ writeback          └────────────────────────────────────┘      └────────────────────────┘
        │ (approve→merge /                    ▲ decision: approve/reject              │
        │  reject→reopen)                     └───────────────────────────────────────┘
        └──────────────────────────── 人間はここだけ見て決める
```

---

## インストール

```bash
bash tools/review-concierge/install.sh           # ~/.local/bin/review-concierge
cp tools/review-concierge/review-concierge.yaml.example ~/review-concierge.yaml
$EDITOR ~/review-concierge.yaml                   # vault_path など
```

依存は Python 3 のみ（PyYAML 推奨。無ければ `review-concierge.json` でも可）。
接続情報(host/project/token)は **gitlab-idd と共有**（git remote ＋ `GITLAB_TOKEN` ／ `connections.yaml`）。
`gl.py` が別の場所にある場合は `GL_SCRIPTS_DIR` を設定。

---

## 使い方

```bash
# 単発スキャン（review-ready を 1 周し、新着/更新分のノートを生成）
review-concierge scan      --config ~/review-concierge.yaml

# 常駐（poll_interval_sec ごとに scan + writeback。非 LLM）
review-concierge watch     --config ~/review-concierge.yaml

# 人間の決定を GitLab に反映（承認→ラベル更新＋[任意]マージ / 差し戻し→コメント＋リオープン）
review-concierge writeback --config ~/review-concierge.yaml

# Dataview 判断キューだけ再生成
review-concierge queue     --config ~/review-concierge.yaml

# ネットワーク不要の自己テスト
review-concierge selftest
```

### レビュアーの操作（Obsidian 側）

1. `Review/Queue.md` を開く → リスク降順・受入の少ない順で判断待ちが並ぶ。
2. 1 枚ノートを開く → 🔴 必読差分・受け入れ条件・自動チェックを確認。
3. frontmatter を編集して保存:
   - 承認: `decision: approve` ＋ `confirmed_by: <自分の名前>`
   - 差し戻し: `decision: reject` ＋ `confirmed_by:` ＋ 「差し戻し理由」見出しに修正要望を記入
4. 次回の `writeback`（または `watch` の定期実行）で GitLab に反映され、ノートは `Review/Archive/` へ退避。

> `merge_on_approve: true` の場合、承認確定でそのまま MR をマージします（人間の明示承認＝人間がマージを押した、と扱う）。
> `false` にすると `status:approved` 付与＋「マージお願いします」コメントまでに留め、最終マージは GitLab 上で人間が押します。

---

## AI レビュー本文の生成（任意）

`review_command` に「raw バンドル(JSON)を stdin で受け取り、キュレーション済みノート本文を stdout に返す」コマンドを
設定すると、ノート冒頭に AI レビュー（観点・信頼度・必読 3 点）が挿入されます。
本文の書式・観点は `.github/skills/review-concierge/SKILL.md` に従ってください
（`agent-reviewer` の観点を再利用）。未設定でも、決定論パケット（受け入れ条件トレーサビリティ・
リスク段階開示・自動チェック）だけで十分実用になります。

---

## 関連

- `.github/skills/gitlab-idd` — 自律 IDD ワークフロー（無改修で連携）
- `.github/skills/agent-reviewer` — レビュー観点リファレンス（AI 本文生成で再利用）
- `.github/skills/review-concierge` — AI レビュー本文の生成手順とノートレイアウト規約
- `tools/gitlab-obsidian-sync` — GitLab ⇔ Obsidian 双方向同期（Vault 規約を共有）
