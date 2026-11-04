"""dagu-api-watch の Dagu 連携。本体（apiwatch）からは独立していて、本体はこれを import しない。

    adapter  受け渡しのアダプタ（delivery.adapter: dagu）。dagu enqueue / REST API で DAG のキューへ入れる
    dags     Dagu の DAG を書き出す `dags` コマンド（取得元ごとの取得・再送・整理）

このフォルダを置かなければ、本体は Dagu なしで動く（delivery.adapter: command、スケジュールは cron などで）。
"""


def register_cli(add) -> None:
    """本体のコマンド行に `dags` を足す（本体が見つけたときだけ呼ばれる）。"""
    from .dags import register
    register(add)
