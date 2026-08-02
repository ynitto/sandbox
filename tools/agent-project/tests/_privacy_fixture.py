"""共有ファイルの redaction 契約テストで使う、すべて架空の入力値。"""

PRIVACY_FIXTURE = {
    "sensitive": {
        "token": "TOKEN_FIXTURE_4f8a2d6c_NEVER_REAL",
        "home_directory": "/home/privacy-fixture-user",
        "raw_prompt": "RAW_PROMPT_FIXTURE: publish the confidential roadmap verbatim",
        "raw_credential": (
            "https://privacy-fixture-user:FIXTURE_PASSWORD_73ac@example.invalid/private.git"
        ),
    },
    "safe": {
        "task_id": "PRIVACY-FIXTURE-42",
        "status": "ready",
        "relative_path": "src/report.py",
        "public_url": "https://example.invalid/public/repository",
    },
}
