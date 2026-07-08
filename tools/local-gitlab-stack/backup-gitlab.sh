#!/usr/bin/env bash
# backup-gitlab.sh — ローカル GitLab CE の日次バックアップ（WSL ディストロ内 or ホストの cron/タスクで実行）。
# 設計: docs/designs/plan-a-local-gitlab-design.md §5
#
# gitlab-backup はリポジトリ+DB を取るが、秘密(gitlab-secrets.json)と設定(gitlab.rb)は
# 別途退避が必要（これが無いと復元できない）。
set -euo pipefail

CONTAINER="${GITLAB_CONTAINER:-gitlab}"
OUT_DIR="${BACKUP_DIR:-./backup}"          # ネットワーク共有や Windows 側パスを推奨
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${OUT_DIR}/${STAMP}"
mkdir -p "${DEST}"

echo "[backup] gitlab-backup create ..."
# STRATEGY=copy は稼働中でも整合性を取りやすい
docker exec -t "${CONTAINER}" gitlab-backup create STRATEGY=copy CRON=1

echo "[backup] リポジトリ+DB のダンプを取り出し ..."
# 既定の格納先 /var/opt/gitlab/backups から最新の *_gitlab_backup.tar を回収
docker exec -t "${CONTAINER}" sh -c 'ls -1t /var/opt/gitlab/backups/*_gitlab_backup.tar | head -1' \
  | tr -d '\r' | while read -r f; do
      docker cp "${CONTAINER}:${f}" "${DEST}/"
    done

echo "[backup] 秘密と設定を退避（復元に必須）..."
docker cp "${CONTAINER}:/etc/gitlab/gitlab-secrets.json" "${DEST}/"
docker cp "${CONTAINER}:/etc/gitlab/gitlab.rb"           "${DEST}/"

echo "[backup] 完了: ${DEST}"
# 古い世代の削除（例: 14 世代より古いものを削除）
ls -1dt "${OUT_DIR}"/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf
