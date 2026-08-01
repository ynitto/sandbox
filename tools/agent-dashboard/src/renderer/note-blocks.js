'use strict';

(function exposeNoteBlocks(root) {
  function noteBlockFingerprint(heading, text) {
    const value = `${String(heading || '').trim()}\n${String(text || '').trim().replace(/\s+/g, ' ')}`;
    let hash = 0x811c9dc5;
    for (let i = 0; i < value.length; i += 1) {
      hash ^= value.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  }

  function parseNoteBlocks(body) {
    const blocks = [];
    let heading = '';
    let lines = [];
    let kind = '';
    let fence = '';
    const flush = () => {
      const text = lines.join('\n').trimEnd();
      if (text.trim()) blocks.push({ heading, text, fingerprint: noteBlockFingerprint(heading, text) });
      lines = [];
      kind = '';
      fence = '';
    };

    for (const line of String(body || '').replace(/\r\n?/g, '\n').split('\n')) {
      if (kind === 'fence') {
        lines.push(line);
        if (line.trim().startsWith(fence)) flush();
        continue;
      }
      const headingMatch = /^\s{0,3}#{1,6}\s+(.+?)\s*$/.exec(line);
      if (headingMatch) {
        flush();
        heading = headingMatch[1].replace(/\s+#+\s*$/, '').trim();
        continue;
      }
      const fenceMatch = /^\s*(```+|~~~+)/.exec(line);
      if (fenceMatch) {
        flush();
        lines = [line];
        kind = 'fence';
        fence = fenceMatch[1].slice(0, 3);
        continue;
      }
      if (!line.trim()) {
        flush();
        continue;
      }
      const bullet = /^\s*(?:[-+*]|\d+[.)])\s+/.test(line);
      if (bullet) {
        flush();
        lines = [line];
        kind = 'bullet';
        continue;
      }
      if (kind === 'bullet' && /^\s+/.test(line)) {
        lines.push(line);
        continue;
      }
      if (kind && kind !== 'paragraph') flush();
      if (!kind) kind = 'paragraph';
      lines.push(line);
    }
    flush();
    return blocks;
  }

  const api = { parseNoteBlocks, noteBlockFingerprint };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.noteBlocks = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
