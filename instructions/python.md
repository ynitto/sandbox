# Python コーディング指示

Python プロジェクトに適用するコーディング規範。

## スタイル

- PEP 8 に従う（行長: 100 文字、インデント: 4 スペース）
- フォーマッタ: `black` / `ruff format` を使用
- リンタ: `ruff` を使用（`flake8` + `isort` の代替）

## 型ヒント

- すべての関数シグネチャに型ヒントを付ける
- `from __future__ import annotations` を先頭に記述（Python 3.10 未満でも遅延評価が有効になる）
- `Optional[X]` より `X | None` を使う（Python 3.10+）
- 戻り値が `None` の場合も `-> None` を明記する

```python
from __future__ import annotations

def parse_user(raw: dict) -> User | None:
    ...
```

## データクラス・Pydantic

- 単純なデータ集約には `@dataclass(frozen=True)` を使う
- バリデーションが必要なデータモデルには Pydantic v2 を使う
- `dict` を引数に渡し回すのは避け、専用のモデルクラスを定義する

## 例外処理

- 裸の `except:` / `except Exception:` を使わない
- 具体的な例外型を捕捉する
- カスタム例外は `Exception` を継承し、意味のある名前を付ける

```python
# Bad
try:
    result = fetch_data()
except:
    pass

# Good
try:
    result = fetch_data()
except HTTPError as e:
    logger.error("API request failed: %s", e)
    raise
```

## パス・ファイル操作

- `os.path` より `pathlib.Path` を使う
- ファイルのオープンには `with` 文を使う（close 漏れ防止）

## ロギング

- `print()` でなく `logging` モジュールを使う
- ログレベル: `DEBUG` < `INFO` < `WARNING` < `ERROR` < `CRITICAL`
- フォーマット文字列は `%s` 記法（`f-string` は遅延評価されないため非推奨）

## 非同期

- `asyncio` を使う場合、`async def` / `await` を一貫して使う
- 同期 I/O をコルーチン内で呼ぶ際は `asyncio.to_thread()` を使う
- タスクのキャンセルを適切に処理する（`asyncio.CancelledError` を握りつぶさない）

## パッケージ管理

- 依存関係は `pyproject.toml` で管理（`setup.py` は非推奨）
- 開発用依存は `[project.optional-dependencies]` または `[tool.uv.dev-dependencies]` に分ける
- ロックファイル（`uv.lock` / `requirements.txt`）をコミットする
