#!/usr/bin/env bash
# 不要なファイル/状態を作成するテストを特定する二分探索スクリプト
# 使い方: ./find-polluter.sh <確認するファイルまたはディレクトリ> <テストパターン>
# 例: ./find-polluter.sh '.git' 'src/**/*.test.ts'

set -euo pipefail

if [ $# -ne 2 ]; then
  echo "使い方: $0 <確認するファイル> <テストパターン>"
  echo "例: $0 '.git' 'src/**/*.test.ts'"
  exit 1
fi

POLLUTION_CHECK="$1"
TEST_PATTERN="$2"

# プロジェクトルート外へのアクセスを防ぐ
if [[ "$TEST_PATTERN" == *".."* ]]; then
  echo "エラー: テストパターンに '..' は使用できません" >&2
  exit 1
fi

PROJECT_ROOT="$(pwd -P)"

echo "検索中: $POLLUTION_CHECK を作成するテスト"
echo "テストパターン: $TEST_PATTERN"
echo ""

# テストファイルのリストを取得し、プロジェクトルート外のパスを除外する
mapfile -t TEST_FILES < <(find . -path "./$TEST_PATTERN" | sort)

# プロジェクトルート外のパスをフィルタ
SAFE_FILES=()
for f in "${TEST_FILES[@]}"; do
  resolved="$(realpath "$f" 2>/dev/null || echo "")"
  if [[ -n "$resolved" && "$resolved" == "$PROJECT_ROOT"* ]]; then
    SAFE_FILES+=("$f")
  fi
done

TOTAL="${#SAFE_FILES[@]}"

if [ "$TOTAL" -eq 0 ]; then
  echo "テストファイルが見つかりませんでした。パターンを確認してください: $TEST_PATTERN"
  exit 1
fi

echo "テストファイル数: $TOTAL"
echo ""

COUNT=0
for TEST_FILE in "${SAFE_FILES[@]}"; do
  COUNT=$((COUNT + 1))

  # 汚染が既に存在する場合スキップ
  if [ -e "$POLLUTION_CHECK" ]; then
    echo "警告: テスト $COUNT/$TOTAL の前に汚染が既に存在"
    echo "   スキップ: $TEST_FILE"
    continue
  fi

  echo "[$COUNT/$TOTAL] テスト中: $TEST_FILE"

  # テストを実行（テスト自体の失敗は無視して汚染の有無のみ確認する）
  npm test "$TEST_FILE" > /dev/null 2>&1 || true

  # 汚染が出現したか確認
  if [ -e "$POLLUTION_CHECK" ]; then
    echo ""
    echo "汚染者を発見!"
    echo "   テスト: $TEST_FILE"
    echo "   作成物: $POLLUTION_CHECK"
    echo ""
    echo "汚染の詳細:"
    ls -la "$POLLUTION_CHECK"
    echo ""
    echo "調査方法:"
    echo "  npm test $TEST_FILE    # このテストのみ実行"
    echo "  cat $TEST_FILE         # テストコードを確認"
    exit 1
  fi
done

echo ""
echo "汚染者は見つかりませんでした — 全テストクリーン!"
exit 0
