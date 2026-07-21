#!/usr/bin/env bash
# migrate-state-repo.sh — 案1「状態専用リポジトリ」への移行支援
#
# 既存の agent-state ブランチ（成果物リポジトリに同居する状態）を、状態専用リポジトリへ
# 移す。これはコピーであり、成果物リポジトリ側の agent-state ブランチや <repo>-agent-state
# worktree は消さない（安定を確認してから人が手動で削除する＝適用は保留）。
#
# 使い方:
#   bash migrate-state-repo.sh --source <成果物repoのパス> --state-repo <専用リポジトリURL> \
#        [--source-branch agent-state] [--dest-branch main] [--dry-run]
#
# 前提: 専用リポジトリ（--state-repo）は事前に作成しておく（Gitea/GitLab の空リポジトリで可）。
# 移行後の設定（agent-project.yaml）:
#   state_repo: <専用リポジトリURL>
#   state_repo_branch: main        # --dest-branch と一致させる
# を書いてエンジンを再起動すると、状態は専用リポジトリの通常 clone に置かれる。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

SOURCE="" STATE_REPO="" SRC_BRANCH="agent-state" DEST_BRANCH="main" DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --source)        SOURCE="$2"; shift 2 ;;
    --state-repo)    STATE_REPO="$2"; shift 2 ;;
    --source-branch) SRC_BRANCH="$2"; shift 2 ;;
    --dest-branch)   DEST_BRANCH="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)       sed -n '2,25p' "$0"; exit 0 ;;
    *) error "不明な引数: $1"; exit 2 ;;
  esac
done

[ -n "$SOURCE" ]     || { error "--source（成果物リポジトリのパス）が必要です"; exit 2; }
[ -n "$STATE_REPO" ] || { error "--state-repo（専用リポジトリURL）が必要です"; exit 2; }
git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 || { error "$SOURCE は git リポジトリではありません"; exit 2; }

# 状態ブランチの存在確認（ローカル or origin）
if git -C "$SOURCE" rev-parse --verify --quiet "refs/heads/$SRC_BRANCH" >/dev/null; then
  SRC_REF="$SRC_BRANCH"
elif git -C "$SOURCE" rev-parse --verify --quiet "refs/remotes/origin/$SRC_BRANCH" >/dev/null; then
  SRC_REF="origin/$SRC_BRANCH"
else
  error "$SOURCE に状態ブランチ $SRC_BRANCH（ローカルも origin も）が見つかりません"
  exit 1
fi
info "移行元: $SOURCE ($SRC_REF) → 専用リポジトリ: $STATE_REPO ($DEST_BRANCH)"

if [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] 次を実行します:"
  echo "  git -C $SOURCE push $STATE_REPO $SRC_REF:refs/heads/$DEST_BRANCH"
  info "[dry-run] 変更は加えません。"
  exit 0
fi

# 状態ブランチの内容を専用リポジトリの dest ブランチへ push（履歴ごと）。
# 専用リポジトリが空なら新規ブランチとして作られる。既に内容があれば非 fast-forward で
# 止まる（意図せぬ上書きを避ける）——その場合は人が中身を確認して判断する。
if git -C "$SOURCE" push "$STATE_REPO" "$SRC_REF:refs/heads/$DEST_BRANCH"; then
  ok "状態を専用リポジトリへ移行しました（$SRC_REF → $DEST_BRANCH）。"
  echo
  info "次の手順:"
  echo "  1. agent-project.yaml に以下を設定:"
  echo "       state_repo: $STATE_REPO"
  echo "       state_repo_branch: $DEST_BRANCH"
  echo "  2. エンジンを再起動（状態は <repo>-agent-state に専用リポジトリの clone として置かれる）。"
  echo "  3. 各 PC は専用リポジトリを clone し直して dashboard に登録。"
  echo "  4. 安定を確認後、旧 agent-state ブランチと <repo>-agent-state worktree を手動削除。"
else
  error "push に失敗しました（専用リポジトリに既存内容がある/権限/ネットワーク）。"
  error "専用リポジトリの $DEST_BRANCH の中身を確認してから再実行してください。"
  exit 1
fi
