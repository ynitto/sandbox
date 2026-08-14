'use strict';

// 共通設計セッションの project 提案を、人が採用した項目だけ既存の入力契約へ反映する。
// タスクは backlog を直接書かず inbox、メモは notes/、計画は authoring の許可済みファイルへ送る。

const fs = require('fs');
const path = require('path');
const authoring = require('./authoring');
const actions = require('./actions');

const APPLICATION_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$/;

function applicationFile(dir, applicationId) {
  const id = String(applicationId || '').trim();
  if (!APPLICATION_ID_RE.test(id)) throw new Error(`反映 ID が不正です: ${id || '(空)'}`);
  return path.join(dir, '.agent-project', 'design-applications', `${id}.json`);
}

function readApplication(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

function writeApplication(file, application) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(application, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, file);
}

function selectionSet(value, fallback) {
  if (value == null) return new Set(fallback);
  return new Set((Array.isArray(value) ? value : []).map(String));
}

function applyProjectDesignProposal(dir, rawProposal, selection = {}, applicationId) {
  if (!dir) throw new Error('プロジェクトディレクトリが指定されていません');
  const proposal = authoring.normalizeProjectDesignProposal(rawProposal);
  const file = applicationFile(dir, applicationId);
  const previous = readApplication(file);
  if (previous && previous.status === 'done') return { ...previous, replayed: true, file };

  const versionIds = selectionSet(selection.versions, proposal.versions.map((item) => item.id));
  const groupIds = selectionSet(selection.backlogGroups, proposal.backlogGroups.map((item) => item.id));
  const taskIds = selectionSet(selection.tasks,
    proposal.backlogGroups.flatMap((group) => group.tasks.map((task) => task.id)));
  const noteIds = selectionSet(selection.notes,
    proposal.notes.map((item, index) => String(item.id || `note-${index + 1}`)));
  const application = {
    version: 1,
    id: String(applicationId),
    status: 'applying',
    startedAt: new Date().toISOString(),
    items: [],
  };
  const perform = (key, operation) => {
    application.items.push({ key, status: 'applying' });
    writeApplication(file, application);
    const result = operation();
    application.items[application.items.length - 1] = { key, status: 'done', result };
    writeApplication(file, application);
  };

  if (selection.master && proposal.master && proposal.master.content) {
    perform('master', () => authoring.writeProjectFile(dir, 'charter.md', proposal.master.content));
  }
  for (const version of proposal.versions) {
    if (selection.applyVersions === false || !versionIds.has(version.id) || !version.content) continue;
    perform(`version:${version.id}`, () =>
      authoring.writeProjectFile(dir, `charters/${version.id}.md`, version.content));
  }
  for (const [index, note] of proposal.notes.entries()) {
    const id = String(note.id || `note-${index + 1}`);
    if (!noteIds.has(id)) continue;
    perform(`note:${id}`, () => actions.writeNote(dir, {
      name: note.name || id,
      body: note.content || note.body,
    }));
  }
  for (const group of proposal.backlogGroups) {
    if (!groupIds.has(group.id) || !versionIds.has(group.charter)) continue;
    for (const task of group.tasks) {
      if (!taskIds.has(task.id)) continue;
      perform(`task:${task.id}`, () => actions.enqueueToInbox(dir, {
        ...task,
        charter: group.charter,
        task_acceptance_criteria: task.task_acceptance_criteria || task.acceptance,
      }));
    }
  }
  application.status = 'done';
  application.finishedAt = new Date().toISOString();
  writeApplication(file, application);
  return { ...application, replayed: false, file };
}

module.exports = { applicationFile, applyProjectDesignProposal };
