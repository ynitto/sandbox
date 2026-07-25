python install.py --all-skills --agent copilot --skip-config
python install.py --all-skills --agent kiro --skip-config
python install.py --all-skills --agent claude --skip-config
python install.py --all-skills --agent codex --skip-config
python install.py --all-skills --agent cursor --skip-config
# agent-project / agent-flow / agent-amigos は 1 本で入る（tools/agent-tools/install.sh。実装計画 W3-1）。
# 3 エンジンは同じ agentcore と契約バージョンを共有するので、別々に入れると片方だけ古い
# ノードができる。codd-gate は agent-project の installer が隣にあれば同梱で入れる。
bash ./tools/agent-tools/install.sh
bash ./tools/kiro-loop/install.sh