# agent-flow-design.md 抽出 — README導線用ダイジェスト（t3）

**差別化の切り口**: 本タスクは4設計中 `agent-flow-design.md` 1件に絞り、
「一行要旨」だけでなく「対象読者」「全18見出しの構造」「関連設計への双方向リンク（本文が
参照する側／本文を参照してくる側）」まで抽出した。README には既に一行要旨のみの導線が
実装リポジトリ側に存在する（下記「範囲外で見つけた事実」参照）ため、synth 系タスクが
一行要旨で足りるか・構造や関連設計まで載せるべきかを判断できるよう、両方の粒度を用意した。

対象ファイル: `/Users/nitto/Workspace/sandbox/docs/designs/agent-flow-design.md`（866行）

---

## 1. 要旨

**一行要旨（README掲載用）**:
> git 共有バス（ローカルディレクトリ／共有 git リポジトリ）上でタスクグラフを動的生成し、
> 複数ワーカーへ分散実行する Dynamic Workflow 実行基盤 `agent-flow` の設計書。

**補足（3〜4行版、必要なら）**:
kiro-cli を頭脳に、要求からワークフローパターン（7種）と並列数を選んでタスクグラフを
組み立て、常駐デーモンが orchestrator / worker をオンデマンド起動する。通信はファイルのみ
（メッセージバスの実体はローカルdirまたは共有gitリポジトリ）で、これが複数PCへの分散を
可能にする。旧 `kiro-flow` から改称移行済み（旧実装・旧設計は削除済み）。

## 2. 対象読者（本文に明記なし・内容からの推定）

ファイル冒頭に「対象読者」の明記はないため、本文構成・関連ファイル欄・ADR/Draft節の
内容から推定した（推定であることを明記）:

- **主読者**: `agent-flow` 本体（`tools/agent-flow/agent-flow.py` 等）の実装・保守担当者。
  claim プロトコル、Bus抽象、ワーカーバス（executor プラグイン）など実装詳細に踏み込む節が
  過半を占める。
- **副読者**: `agent-project` 側で実行層への委譲を設計・変更する担当者。`agent-project` と
  `agent-flow` は相互参照関係にあり（§18 は agent-project 側 `agent-project-design.md §11`
  と責務境界を明示的に切り分けている）、両設計を跨いで読む前提がある。
- **副読者**: worker 実行系プロンプト（`flow-worker` スキル）や gitlab executor 経由で
  他エージェントに実行を委譲する側（§9.0, §18）。

## 3. 主要見出し（## レベル、全18節）

1. 概要
2. 背景・目的（既存ツールとの差別化を含む）
3. 全体アーキテクチャ
4. メッセージバス設計
5. claim プロトコル（分散ロックの肝）
6. 転送層（Bus 抽象）
7. ワークフローパターン（7 パターン）
8. orchestrator
9. worker（§9.0 実行系プロンプトのスキル外出し flow-worker を含む）
10. デーモン（オンデマンド起動）
11. CLI / サブコマンド
12. 整合性・障害対応
13. テスト
14. 既知の制限・今後
15. マイルストーン履歴（M1〜M7, P1〜P4）
16. ADR: ワークフロースクリプトの動的生成は当面採用しない
17. ADR: タスクのハングは lease ではなく task timeout で守る
18. 設計提案: gitlab 人コメントの人/エージェント判別・emit と分解への還元（Draft・未実装）

## 4. 関連設計（本文中の実参照。grep実測、推測なし）

**agent-flow-design.md が参照する側（outbound）**:
- `agent-project-design.md`（§18 冒頭、`§11` 統一学習バス・蒸留・recall・verify 品質を名指し）
  — 責務境界を「gitlab executor（本ツール）はコメントを運ぶだけ／蒸留・learn・recall・verify
  は agent-project」と明記。
- `git-worktree-cache-pattern.md`（§9.0、worker の git 利用規約が拠る汎用パターンとして参照）。

**agent-flow-design.md を参照してくる側（inbound、`grep -rl` 実測）**:
- `agent-tools-rename-design.md` — 改称移行方針の対象として言及。
- `agent-project-design.md` — 実行層としての相互参照（t1棚卸しでも確認済み）。
- `docs/designs/README.md`（実装リポジトリ側に既存） — 主要4設計の1件として掲載済み。

**ファイル内で概念的に言及されるが設計書として存在しない/別系統のもの**（README導線には含めない）:
- `kiro-loop`（既存ツールとの差別化表、hooks流儀の引用元）
- `gitlab-idd`（gitlab executor が委譲する先のスキル。設計思想の引用元）
- `git-file-sync`（設計思想の引用元）

## 5. README導線フォーマット（相対リンク＋一行要旨）

```markdown
- [`agent-flow-design.md`](./agent-flow-design.md) — git 共有バス上でタスクグラフを動的生成し複数ワーカーへ分散実行する Dynamic Workflow 基盤の設計書。
```

相対リンクの起点は `docs/designs/README.md` と同一ディレクトリ内（`./agent-flow-design.md`）。

---

## 検証

- 主要見出し18件: `grep -n '^#' agent-flow-design.md` の実行結果と1件ずつ突き合わせて記載（省略・改変なし）。
- 関連設計（outbound）: `grep -noE '[a-zA-Z0-9_-]+-design\.md|[a-zA-Z0-9_-]+-pattern\.md' agent-flow-design.md` で
  ヒットした2ファイルそれぞれの前後文脈を `sed -n` で読み、参照の性質（責務境界の切り分け／利用規約の引用元）を確認した。
- 関連設計（inbound）: `grep -rl "agent-flow-design.md" docs/designs/` の実行結果3件をそのまま記載。
- 一行要旨の文言: ファイル本文 §1「概要」の記述に基づく（新規の要約表現ではなく本文語彙を優先）。
- 対象読者: 本文に明示的な節がないため「推定」であることをそのまま報告に残した（断定していない）。

## 採用した前提・未解決事項

- 「README導線に使える形」を、(a) 一行要旨のみの最小形と (b) 対象読者・構造・関連設計まで
  含む詳細形の両方を用意する、と解釈した（synth 側でどちらを採用するか判断できるように）。
- 対象読者は本文に明記がないため断定を避け、関連ファイル欄・相互参照の宛先から推定した旨を
  明記した。誤りがあれば synth 側での訂正を想定する。
- README の相対リンクは `docs/designs/README.md` を起点と仮定した（t1棚卸しにより実装リポジトリ
  側に同ディレクトリ内 README.md が既存であることを確認済み）。

## 範囲外で見つけた事実（本タスクの範囲外・報告のみ）

- t1棚卸しの報告どおり、`docs/designs/README.md` は実装リポジトリ
  `/Users/nitto/Workspace/sandbox` 側に既に存在し、`agent-flow-design.md` を含む主要4設計
  すべてへのリンクと一行要旨を既に掲載済み（本ダイジェスト §5 の一行要旨とほぼ同内容）。
  本 worktree（`.agent-project`）には `docs/designs` 自体が存在しない。README の新規作成・
  上書き判断は本タスク（t3: 抽出のみ）の範囲外とし、synth 系タスクの判断に委ねる。
