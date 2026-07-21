#!/usr/bin/env bash
# migrate-state-repo.sh — 案1「状態専用リポジトリ」への移行支援
#
# 既存の状態（backlog/needs/decisions/charter/charters/project.json など）を、状態専用
# リポジトリへ **状態だけ・リポジトリのルート直下** の形で移す。これはコピーで、元の
# 状態フォルダや worktree は消さない（安定を確認してから人が手動で削除する＝適用は保留）。
#
# なぜ「状態だけ・ルート直下」か:
#   ・旧 agent-state ブランチをそのまま push すると、成果物リポジトリの全ファイルが混ざり、
#     さらに状態が <rel> サブディレクトリに入って、エンジン（clone のルートを状態ルートとして
#     読む）と場所が食い違う（→ バージョン情報などが引き継がれない）。
#   ・そこで既知の「状態エントリ」だけを clone のルート直下へ並べ、確実に読めるようにする。
#
# 使い方:
#   bash migrate-state-repo.sh --state-dir <状態フォルダ> --state-repo <専用リポジトリURL> \
#        [--dest-branch main] [--dry-run]
#
#   --state-dir は「backlog/ や project.json がある実際の状態フォルダ」。worktree 運用なら
#   通常 <repo>-agent-state（sparse なら <repo>-agent-state/.agent-project）。本体同居なら
#   <repo>/.agent-project か <repo> 直下。迷ったら backlog/ が直下にあるフォルダを指定する。
#
# 前提: 専用リポジトリ（--state-repo）は事前に作成しておく（Gitea/GitLab の空リポジトリで可）。
# 移行後の設定（agent-project.yaml）:
#   state_repo: <専用リポジトリURL>
#   state_repo_branch: main        # --dest-branch と一致させる
# を書いてエンジンを再起動すると、状態は専用リポジトリの通常 clone（既定 <repo>-state）に置かれる。

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

# 状態エントリ（_STATE_SIGNIFICANT + _STATE_NOISE と対応。これだけを専用リポジトリへ移す）。
# agent-project.yaml / agent-flow.yaml は **含めない**: これらは起動のブートストラップ設定で、
# エンジンは状態リポジトリを clone する前に cwd（成果物repo or ~/.agent）から読む。状態リポジトリ側に
# 置いても起動時には読まれず、「どちらを編集するのか」の混乱を生むだけ。設定は cwd 側を正とする。
STATE_ENTRIES=(
  charter.md charters backlog needs decisions repos.json policy.md rules.md
  archive DELIVERY.md specs cohorts autonomy project.json
  journal.md status.json status run-log.jsonl
  # bus/ は viewer が実行中 run を見るために同期対象。inbox/commands は入力口。
  bus inbox commands
)

STATE_DIR="" STATE_REPO="" DEST_BRANCH="main" DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --state-dir)     STATE_DIR="$2"; shift 2 ;;
    --state-repo)    STATE_REPO="$2"; shift 2 ;;
    --dest-branch)   DEST_BRANCH="$2"; shift 2 ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)       sed -n '2,33p' "$0"; exit 0 ;;
    *) error "不明な引数: $1"; exit 2 ;;
  esac
done

[ -n "$STATE_DIR" ]  || { error "--state-dir（状態フォルダ。backlog/ 等がある場所）が必要です"; exit 2; }
[ -n "$STATE_REPO" ] || { error "--state-repo（専用リポジトリURL）が必要です"; exit 2; }
[ -d "$STATE_DIR" ]  || { error "$STATE_DIR がありません"; exit 2; }
if [ ! -d "$STATE_DIR/backlog" ] && [ ! -f "$STATE_DIR/project.json" ] && [ ! -f "$STATE_DIR/charter.md" ]; then
  warn "$STATE_DIR に backlog/ も project.json も charter.md も見当たりません。状態フォルダが違うかもしれません。"
fi

# 移すエントリを列挙（存在するものだけ）
present=()
for e in "${STATE_ENTRIES[@]}"; do
  [ -e "$STATE_DIR/$e" ] && present+=("$e")
done
[ "${#present[@]}" -gt 0 ] || { error "$STATE_DIR に移せる状態エントリがありません"; exit 1; }

info "移行元（状態フォルダ）: $STATE_DIR"
info "専用リポジトリ: $STATE_REPO（ブランチ $DEST_BRANCH・ルート直下に状態だけを配置）"
info "移すエントリ: ${present[*]}"

if [ "$DRY_RUN" -eq 1 ]; then
  info "[dry-run] 上記エントリを新しい空コミットとして $STATE_REPO:$DEST_BRANCH へ push します（変更なし）。"
  exit 0
fi

# 一時リポジトリを作り、状態エントリだけをルート直下へ並べて 1 コミットにし、専用リポジトリへ push。
# 履歴は引き継がない（状態は「現在の状態」だけが意味を持つ。履歴は git 側が今後積む）。
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false
git -C "$TMP" init -q -b "$DEST_BRANCH"
git -C "$TMP" config user.email "migrate@agent-project"
git -C "$TMP" config user.name "agent-project migrate"
for e in "${present[@]}"; do
  # -a で属性保持しつつコピー。ディレクトリは中身ごと、ファイルはそのまま。
  cp -a "$STATE_DIR/$e" "$TMP/$e"
done
# 念のため .git 等の混入を防ぐ（cp が nested .git を運ぶことは無いが保険）
find "$TMP" -mindepth 2 -name '.git' -prune -exec rm -rf {} + 2>/dev/null || true
git -C "$TMP" add -A
git -C "$TMP" commit -qm "状態を専用リポジトリへ移行（現時点のスナップショット・状態のみ）"

if git -C "$TMP" push "$STATE_REPO" "HEAD:refs/heads/$DEST_BRANCH"; then
  ok "状態を専用リポジトリへ移行しました（状態だけ・ルート直下）。"
  # 空の専用リポジトリは最初の push で HEAD（既定ブランチ）が $DEST_BRANCH に切り替わらない。
  # 既定が別ブランチのままだと、普通の `git clone`（dashboard や人の clone）が空チェックアウトに
  # なる（エンジンは --branch $DEST_BRANCH で clone するので影響しない）。ローカルの bare なら
  # ここで既定ブランチを直す。リモート（Gitea/GitLab）は Web UI で既定ブランチを設定してもらう。
  if [ -d "$STATE_REPO" ] && git -C "$STATE_REPO" rev-parse --is-bare-repository >/dev/null 2>&1; then
    git -C "$STATE_REPO" symbolic-ref HEAD "refs/heads/$DEST_BRANCH" 2>/dev/null \
      && info "専用リポジトリの既定ブランチを $DEST_BRANCH に設定しました。"
  else
    warn "専用リポジトリの既定ブランチを $DEST_BRANCH にしてください（Git ホストの設定）。"
    warn "そのままだと dashboard や人の 'git clone' が空になります（エンジンは影響なし）。"
  fi
  echo
  info "次の手順:"
  echo "  1. agent-project.yaml に以下を設定:"
  echo "       state_repo: $STATE_REPO"
  echo "       state_repo_branch: $DEST_BRANCH"
  echo "  2. エンジンを再起動すると <repo>-state に専用リポジトリを clone して状態ルートにする"
  echo "     （旧 worktree <repo>-agent-state とは別フォルダ。衝突しない）。"
  echo "  3. エンジンと dashboard が同じ PC なら、dashboard には <repo>-state（clone）を登録する"
  echo "     （成果物リポジトリではなく、この状態 clone を開く）。手動 clone は不要。"
  echo "  4. 別 PC は <repo>-state を各自 clone して dashboard に登録。"
  echo "  5. 安定を確認後、旧 agent-state ブランチと <repo>-agent-state worktree を手動削除。"
else
  error "push に失敗しました（専用リポジトリに既存内容がある/権限/ネットワーク）。"
  error "専用リポジトリの $DEST_BRANCH の中身を確認してから再実行してください。"
  exit 1
fi
