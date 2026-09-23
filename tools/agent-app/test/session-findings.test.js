'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const findings = require('../src/main/sessionFindings');
const attention = require('../src/main/attention');
const store = require('../src/main/store');

test('回答に明記された問題と回避策だけを原文のまま拾う', () => {
  const text = [
    '## 問題点',
    '- WSL 側のパスを Windows の API に渡すとファイルが見つからなかった。',
    '```text',
    'エラーが出た: token=secret',
    '```',
    '回避策: Windows のパスへ変換してから渡した。',
    '問題はありません。',
  ].join('\n');
  assert.deepEqual(findings.extractText(text), [
    { kind: 'problem', excerpt: 'WSL 側のパスを Windows の API に渡すとファイルが見つからなかった。' },
    { kind: 'workaround', excerpt: 'Windows のパスへ変換してから渡した。' },
  ]);
});

test('セッション単位で重複を除き、Windows のリポジトリと発言を結び付ける', () => {
  const session = {
    id: 'session-1', repo: 'C:\\work\\project', kind: 'conversation', title: '調査',
    messages: [
      { role: 'user', text: '問題点: ユーザーの依頼', at: '2026-09-23T00:00:00Z' },
      { role: 'assistant', text: '問題点: tmux の接続で失敗した。\n回避策: 再接続して回避した。', at: '2026-09-23T00:01:00Z' },
      { role: 'assistant', text: '問題点: tmux の接続で失敗した。', at: '2026-09-23T00:02:00Z' },
    ],
  };
  const extracted = findings.fromSession(session);
  assert.equal(extracted.length, 2);
  assert.deepEqual(extracted.map((item) => item.index), [1, 1]);
  const sources = attention.findingSources([{ ...session, findings: extracted }]);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].key, 'finding:session-1');
  assert.equal(sources[0].finding.items.length, 2);
  assert.equal(sources[0].finding.problems, 1);
  assert.equal(sources[0].finding.workarounds, 1);
  assert.equal(sources[0].target.repo, 'C:\\work\\project');
  assert.equal(sources[0].target.id, 'session-1');
  assert.equal(attention.project(sources).unread, 1);
  const conversation = attention.conversationSources([{ ...session, result: { at: '2026-09-23T00:02:00Z', outcome: 'done' } }]);
  assert.equal(attention.project([...conversation, ...sources]).unread, 1);
});

test('同じセッションで後から見つかった記述は既読後に再表示する', () => {
  const source = { id: 'session-3', repo: '/repo', kind: 'conversation', title: '接続調査',
    findings: [
      { id: 'a', kind: 'problem', excerpt: '接続できなかった', index: 1, at: '2026-09-23T00:01:00Z' },
      { id: 'b', kind: 'workaround', excerpt: '再接続した', index: 2, at: '2026-09-23T00:02:00Z' },
    ] };
  const [group] = attention.findingSources([source]);
  assert.equal(group.resultAt, '2026-09-23T00:02:00Z');
  assert.equal(attention.project([group], { seen: { [group.key]: { resultAt: '2026-09-23T00:02:00Z' } } }).unread, 0);
  source.findings.push({ id: 'c', kind: 'problem', excerpt: '別の失敗', index: 3, at: '2026-09-23T00:03:00Z' });
  const [updated] = attention.findingSources([source]);
  assert.equal(updated.key, group.key);
  assert.equal(attention.project([updated], { seen: { [group.key]: { resultAt: group.resultAt } } }).unread, 1);
});

test('保存済みの編集セッションからも受信箱の材料を作る', () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-findings-'));
  const directory = path.join(userData, 'sessions');
  fs.mkdirSync(directory);
  fs.writeFileSync(path.join(directory, 'session-2.json'), JSON.stringify({
    id: 'session-2', repo: '/repo', kind: 'task', task: { machine: 'report' }, title: '日次レポート',
    messages: [{ role: 'assistant', text: '原因: 入力の月が空だったため実行に失敗した。', at: '2026-09-23T00:00:00Z' }],
  }));
  const summaries = store.listSessions(userData, '', { kind: '' });
  const [source] = attention.findingSources(summaries);
  assert.equal(source.target.kind, 'task');
  assert.equal(source.target.id, 'report');
  assert.equal(source.finding.sessionId, 'session-2');
});
