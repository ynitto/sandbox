'use strict';

// 同じ選択から、画面のプレビューと実際の保存内容を作る。
(function (root) {
  const groups = [
    { id: 'policy', label: '目的・方針', file: 'rules.md' },
    { id: 'knowledge', label: '決定・知識', file: 'knowledge.md' },
    { id: 'outcomes', label: '成果・教訓', file: 'outcomes.md' },
    { id: 'pending', label: '未完了タスク（ワークフローに変換）', file: 'pending.md' },
  ];
  const bytes = text => new TextEncoder().encode(text).length;
  function body(item) {
    let fence = '';
    let firstHeading = true;
    return item.content.split('\n').map(line => {
      const marker = /^\s*(`{3,}|~{3,})/.exec(line);
      if (marker) { if (!fence) fence = marker[1]; else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = ''; return line; }
      if (fence) return line;
      const heading = /^(#{1,6})\s+(.+)/.exec(line);
      if (!heading) return line;
      if (firstHeading && heading[2] === item.title) { firstHeading = false; return ''; }
      firstHeading = false;
      return `${'#'.repeat(Math.min(6, heading[1].length + 2))} ${heading[2]}`;
    }).join('\n').trim();
  }
  function recommended(items) {
    let remaining = 32 * 1024;
    return items.filter(item => {
      const size = bytes(item.content);
      if (item.group === 'pending' || size > 8 * 1024 || size > remaining) return false;
      remaining -= size;
      return true;
    }).map(item => item.id);
  }
  function build(items, selected = recommended(items)) {
    const ids = new Set(selected);
    const chosen = items.filter(item => ids.has(item.id));
    const documents = groups.flatMap(group => {
      const entries = chosen.filter(item => item.group === group.id);
      if (!entries.length) return [];
      const sections = entries.map(item => `## ${item.title}\n\n${body(item)}\n\n出典: ${item.sources.map(source => source.replace(/[\r\n]/g, ' ')).join(' / ')}`).join('\n\n---\n\n');
      const content = `# ${group.label}\n\n${sections}\n`;
      return [{ ...group, content, bytes: bytes(content), items: entries.length }];
    });
    return { documents, items: chosen.length, sources: new Set(chosen.flatMap(item => item.sources)).size, bytes: documents.reduce((sum, doc) => sum + doc.bytes, 0) };
  }
  const api = { groups, bytes, recommended, build };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ProjectImportBundle = api;
})(typeof globalThis === 'object' ? globalThis : this);
