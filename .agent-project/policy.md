# kiro-project policy（人間による上書き）

# worker の成果物ではない。kiro-project 自身の状態（backlog/needs/repos.json/bus…）を
# エージェントに書き換えさせない（実際 codd-gate タスクの worker が repos.json を改変した）
protect: .kiro-project/**
