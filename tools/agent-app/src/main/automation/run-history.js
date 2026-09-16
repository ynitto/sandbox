'use strict';

// タスク・ワークフローの手動実行の記録。画面の「履歴」タブが読む短い控え（リポジトリごと
// 100 件）と、監査への申告（audit-feed。保存期間の正典は agent-audit のストア）を
// 同じ 1 か所で残す——片方だけ書く経路を作ると、履歴に出るのに集計に出ない実行ができる。
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const audit = require('../audit');
function file(userData, root) { return path.join(userData, 'run-history', crypto.createHash('sha256').update(root).digest('hex') + '.json'); }
function read(userData, root) {
  try { const value = JSON.parse(fs.readFileSync(file(userData, root), 'utf8')); return Array.isArray(value) ? value : []; } catch { return []; }
}
function append(userData, root, record) {
  audit.feedRun(userData, { root, record, kind: record.kind === 'workflow' ? 'workflow' : 'task' });
  const target = file(userData, root);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temp = target + '.tmp';
  fs.writeFileSync(temp, JSON.stringify([record, ...read(userData, root)].slice(0, 100)));
  fs.renameSync(temp, target);
}
function merge(userData, root, snapshot, machines = []) {
  const local = read(userData, root);
  const tasks = [...(snapshot.tasks || [])];
  for (const machine of machines) if (!tasks.some(t => t.machine === machine.machine)) tasks.push({ ...machine, kind: 'statemachine', id: `machine:${machine.machine}`, history: [] });
  return { ...snapshot, tasks: tasks.map(task => ({ ...task, history: [
    ...local.filter(r => r.taskId === task.id || (r.machine && r.machine === task.machine)), ...(task.history || []),
  ].sort((a, b) => String(b.finishedAt || '').localeCompare(String(a.finishedAt || ''))).slice(0, 100) })) };
}
module.exports = { read, append, merge };
