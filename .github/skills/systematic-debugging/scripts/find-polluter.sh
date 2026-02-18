#!/usr/bin/env bash
# 不要なファイル/状態を作成するテストを特定する二分探索スクリプト
# 使い方: ./find-polluter.sh <確認するファイルまたはディレクトリ> <テストパターン>
# 例: ./find-polluter.sh '.git' 'src/**/*.test.ts'

set -e

if [ $# -ne 2 ]; then
  echo "使い方: $0 <確認するファイル> <テストパターン>"
  echo "例: $0 '.git' 'src/**/*.test.ts'"
  exit 1
fi

POLLUTION_CHECK="$1"
TEST_PATTERN="$2"

echo "検索中: $POLLUTION_CHECK を作成するテスト"
echo "テストパターン: $TEST_PATTERN"
echo ""

# テストファイルのリストを取得
TEST_FILES=$(find . -path "$TEST_PATTERN" | sort)
TOTAL=$(echo "$TEST_FILES" | wc -l | tr -d ' ')

echo "テストファイル数: $TOTAL"
echo ""

COUNT=0
for TEST_FILE in $TEST_FILES; do
  COUNT=$((COUNT + 1))

  # 汚染が既に存在する場合スキップ
  if [ -e "$POLLUTION_CHECK" ]; then
    echo "警告: テスト $COUNT/$TOTAL の前に汚染が既に存在"
    echo "   スキップ: $TEST_FILE"
    continue
  fi

  echo "[$COUNT/$TOTAL] テスト中: $TEST_FILE"

  # テストを実行
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
