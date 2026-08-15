"""共有ファイルの redaction 契約テストで使う、すべて架空の入力値。"""

_TOKEN = "ghp_FIXTURE_4f8a2d6c_NEVER_REAL"
_BEARER = "FIXTURE_BEARER_9f3c_NEVER_REAL"
_RAW_PROMPT = "publish the confidential roadmap verbatim"
_RAW_PROMPT_JA = "秘密のロードマップをそのまま公開"
_AMOUNT = "FIXTURE_USD_91.25"
_AMOUNT_DOLLAR = "$91.25"
_CREDENTIAL = "https://privacy-fixture-user:FIXTURE_PASSWORD_73ac@example.invalid/private.git"

PRIVACY_FIXTURE = {
    "cases": {
        "token_value": {
            "payload": _TOKEN,
            "forbidden": (_TOKEN,),
            "marker": "TOKEN",
        },
        "token_embedded": {
            # Word characters touch both sides: the old `\bghp_...` matcher missed this.
            "payload": f"diagnostic-prefix{_TOKEN}diagnostic-suffix",
            "forbidden": (_TOKEN,),
            "marker": "TOKEN",
        },
        "token_bearer": {
            "payload": f"Authorization: Bearer {_BEARER}",
            "forbidden": (_BEARER,),
            "marker": "TOKEN",
        },
        "raw_prompt": {
            "payload": f"raw_prompt={_RAW_PROMPT}",
            "forbidden": (_RAW_PROMPT, "confidential roadmap"),
            "marker": "PROMPT",
        },
        "raw_prompt_ja": {
            "payload": f"生プロンプト={_RAW_PROMPT_JA}",
            "forbidden": (_RAW_PROMPT_JA, "秘密のロードマップ"),
            "marker": "PROMPT",
        },
        "posix_home_directory": {
            "payload": "/home/privacy-fixture-user/private/report.txt",
            "forbidden": ("/home/privacy-fixture-user",),
            "marker": "HOME",
        },
        "macos_home_directory": {
            "payload": "/Users/privacy-fixture-user/private/report.txt",
            "forbidden": ("/Users/privacy-fixture-user",),
            "marker": "HOME",
        },
        "windows_home_directory": {
            "payload": r"C:\Users\privacy-fixture-user\private\report.txt",
            "forbidden": (r"C:\Users\privacy-fixture-user",),
            "marker": "HOME",
        },
        "raw_credential": {
            "payload": f"credential={_CREDENTIAL}",
            "forbidden": (_CREDENTIAL, "FIXTURE_PASSWORD_73ac"),
            "marker": "CREDENTIAL",
        },
        "amount_labeled": {
            "payload": f"amount={_AMOUNT}",
            "forbidden": (_AMOUNT,),
            "marker": "AMOUNT",
        },
        "amount_dollar": {
            "payload": f"price quote {_AMOUNT_DOLLAR} for the run",
            "forbidden": (_AMOUNT_DOLLAR,),
            "marker": "AMOUNT",
        },
    },
    "safe": {
        "task_id": "PRIVACY-FIXTURE-42",
        "status": "ready",
        "relative_path": "src/report.py",
        "public_url": "https://example.invalid/public/repository",
        # task 原価記帳（usd=）は現行共有経路が使う。AMOUNT はこれを検出しない。
        "run_cost_note": "tokens=1200 usd=0.05",
    },
}
