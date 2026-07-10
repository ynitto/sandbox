## kiro-flow-072641-2: kiro-flow: タスクグラフの構築と依存解決ロジックを実装する
- status: ready
- source: charter
- priority: 0
- verify: `python -c "from tools.kiro_flow.graph import build_graph; g = build_graph({'tasks': [{'id': 'a'}, {'id': 'b', 'depends_on': ['a']}]}); assert list(g.topological_order()) == ['a', 'b']" && echo PASS`
- retries: 0
- workspace: sandbox
