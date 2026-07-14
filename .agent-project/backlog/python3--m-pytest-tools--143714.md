## python3--m-pytest-tools--143714: 受入条件を満たす: python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q --cov=tools/kiro-project --cov=tools/kiro-
- status: ready
- source: acceptance
- priority: 0
- verify: `python3 -m pytest tools/kiro-project/tests tools/kiro-flow/tests -q --cov=tools/kiro-project --cov=tools/kiro-flow --cov-fail-under=70`
- retries: 0
- charter: v0.1
- assess: c=2 r=1 a=1
- workspace: sandbox
- routed_by: owns
