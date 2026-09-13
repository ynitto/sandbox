'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
function file(userData, root) { return path.join(userData, 'run-history', crypto.createHash('sha256').update(root).digest('hex') + '.json'); }
function read(userData, root) {
  try { const value = JSON.parse(fs.readFileSync(file(userData, root), 'utf8')); return Array.isArray(value) ? value : []; } catch { return []; }
}
function append(userData, root, record) {
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
