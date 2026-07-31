#!/usr/bin/env bash
# watch.sh — kanban.md の変更を監視して GitLab Issue のラベルを更新する
#
# 依存: fswatch (brew install fswatch)
# 使い方:
#   VAULT_PATH=/path/to/vault GITLAB_TOKEN=glpat-xxx bash watch.sh
#   または config.yaml の global.vault_path / kanban.target_file を参照する場合:
#   bash watch.sh --config /path/to/gitlab-obsidian-sync.yaml

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 引数・設定
# ---------------------------------------------------------------------------

CONFIG_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_FILE="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# config.yaml から vault_path / target_file を取得（python で解析）
if [[ -n "$CONFIG_FILE" ]]; then
  VAULT_PATH=$(python3 - <<EOF
import yaml, sys
with open("$CONFIG_FILE") as f:
    c = yaml.safe_load(f)
print(c.get("global", {}).get("vault_path", "").replace("~", __import__("os").path.expanduser("~")))
EOF
)
  TARGET_FILE=$(python3 - <<EOF
import yaml
with open("$CONFIG_FILE") as f:
    c = yaml.safe_load(f)
print(c.get("kanban", {}).get("target_file", "AI-Tasks/kanban.md"))
EOF
)
fi

VAULT_PATH="${VAULT_PATH:-${OBSIDIAN_VAULT_PATH:-}}"
TARGET_FILE="${TARGET_FILE:-AI-Tasks/kanban.md}"
KANBAN_FILE="${VAULT_PATH}/${TARGET_FILE}"

if [[ -z "$VAULT_PATH" ]]; then
  echo "[watch] ERROR: VAULT_PATH が未設定です。--config か OBSIDIAN_VAULT_PATH 環境変数を指定してください。" >&2
  exit 1
fi

if ! command -v fswatch &>/dev/null; then
  echo "[watch] ERROR: fswatch が見つかりません。brew install fswatch でインストールしてください。" >&2
  exit 1
fi

echo "[watch] 監視開始: $KANBAN_FILE"

# ---------------------------------------------------------------------------
# カラム → ラベルマッピング
# ---------------------------------------------------------------------------

col_to_label() {
  case "$1" in
    *"Todo"*)        echo "todo" ;;
    *"In Progress"*) echo "doing" ;;
    *"Waiting"*)     echo "waiting" ;;
    *"Done"*)        echo "done" ;;
    *"Failed"*)      echo "failed" ;;
    *)               echo "" ;;
  esac
}

# ---------------------------------------------------------------------------
# kanban.md を解析してカード位置を抽出
# issue_iid → label のマップを stdout に出力 (形式: "123 todo")
# ---------------------------------------------------------------------------

parse_kanban() {
  local file="$1"
  local current_col=""
  while IFS= read -r line; do
    if [[ "$line" =~ ^##[[:space:]](.+)$ ]]; then
      current_col="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ \#([0-9]+)\] ]]; then
      local iid="${BASH_REMATCH[1]}"
      local label
      label=$(col_to_label "$current_col")
      if [[ -n "$label" && -n "$iid" ]]; then
        echo "$iid $label"
      fi
    fi
  done < "$file"
}

# ---------------------------------------------------------------------------
# GitLab API: Issue のラベルを更新
# ---------------------------------------------------------------------------

update_issue_label() {
  local iid="$1"
  local new_label="$2"

  # gl.py を使って更新（gitlab-idd スキルのスクリプトを再利用）
  local gl_script
  gl_script=$(find "$SCRIPT_DIR/../.." -name "gl.py" -path "*/gitlab-idd/*" 2>/dev/null | head -1)

  if [[ -z "$gl_script" ]]; then
    echo "[watch] WARN: gl.py が見つかりません。GitLab 更新をスキップします。" >&2
    return
  fi

  # 既存ラベルから status:* を除去して新しいラベルを付与
  local old_labels
  old_labels=$(python3 "$gl_script" get-issue "$iid" --get labels 2>/dev/null || echo "[]")
  local remove_labels
  remove_labels=$(python3 - <<EOF
import json, sys
labels = json.loads('''$old_labels''')
status_labels = [l for l in labels if l.startswith("status:")]
print(",".join(status_labels))
EOF
)

  local add_arg="status:${new_label}"
  local remove_arg="${remove_labels}"

  echo "[watch] Issue #${iid}: ラベル更新 → status:${new_label}"

  python3 "$gl_script" update-issue "$iid" \
    --add-labels "$add_arg" \
    ${remove_arg:+--remove-labels "$remove_arg"} 2>&1 | sed 's/^/[watch]   /'
}

# ---------------------------------------------------------------------------
# 変更検知ループ
# ---------------------------------------------------------------------------

# 初回スナップショット
PREV_STATE=""
if [[ -f "$KANBAN_FILE" ]]; then
  PREV_STATE=$(parse_kanban "$KANBAN_FILE" | sort)
fi

fswatch -o "$KANBAN_FILE" | while read -r _event; do
  sleep 0.5  # 書き込み完了を待つ

  if [[ ! -f "$KANBAN_FILE" ]]; then
    continue
  fi

  NEW_STATE=$(parse_kanban "$KANBAN_FILE" | sort)

  if [[ "$NEW_STATE" == "$PREV_STATE" ]]; then
    continue
  fi

  # 差分を検出: 移動したカード (iid が同じで label が変わったもの)
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    iid="${line%% *}"
    new_label="${line##* }"
    old_label=$(echo "$PREV_STATE" | awk -v id="$iid" '$1==id{print $2}')
    if [[ -n "$old_label" && "$old_label" != "$new_label" ]]; then
      update_issue_label "$iid" "$new_label"
    fi
  done <<< "$NEW_STATE"

  PREV_STATE="$NEW_STATE"
done
