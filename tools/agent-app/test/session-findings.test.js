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
  assert.equal(sources.length, 2);
  assert.equal(sources[0].target.repo, 'C:\\work\\project');
  assert.equal(sources[0].target.id, 'session-1');
  assert.equal(attention.project(sources).unread, 2);
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
