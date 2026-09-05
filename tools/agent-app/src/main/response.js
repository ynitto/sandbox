'use strict';

const MAX_ITEMS = 200;
const MAX_DETAIL_CHARS = 4000;
const FILE_ACTIONS = { add: 'created', create: 'created', update: 'modified', modify: 'modified', delete: 'deleted' };

function text(value, limit = MAX_DETAIL_CHARS) {
  return String(value == null ? '' : value).trim().slice(0, limit);
}

function createCollector(cli) {
  const thinking = [];
  const information = [];
  const addThinking = (item) => { if (thinking.length < MAX_ITEMS && item.text) thinking.push(item); };
  const addInformation = (item) => { if (information.length < MAX_ITEMS && item.title) information.push(item); };

  function codexEvent(event) {
    if (!event || event.type !== 'item.completed' || !event.item) return;
    const item = event.item;
    if (item.type === 'reasoning') {
      addThinking({ text: text(item.text || item.summary), status: 'done' });
      return;
    }
    if (item.type === 'command_execution') {
      addInformation({
        type: 'command',
        title: text(item.command),
        status: Number(item.exit_code) === 0 && item.status !== 'failed' ? 'success' : 'error',
        detail: text(item.aggregated_output),
      });
      return;
    }
    if (item.type === 'file_change') {
      for (const change of Array.isArray(item.changes) ? item.changes : []) {
        addInformation({
          type: 'file',
          title: text(change.path),
          status: item.status === 'failed' ? 'error' : 'success',
          action: FILE_ACTIONS[change.kind] || text(change.kind) || 'modified',
        });
      }
    }
  }

  return {
    push(line) {
      const beforeThinking = thinking.length;
      const beforeInformation = information.length;
      const added = () => ({ thinking: thinking.slice(beforeThinking), information: information.slice(beforeInformation) });
      if (String(cli || '').toLowerCase() !== 'codex') return added();
      let event;
      try { event = JSON.parse(String(line || '')); } catch { return added(); }
      codexEvent(event);
      return added();
    },
    addThinking,
    addInformation,
    parts() { return { thinking: thinking.slice(), information: information.slice() }; },
  };
}

module.exports = { createCollector };
