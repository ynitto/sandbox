#!/usr/bin/env node
'use strict';
// テスト用のエージェント CLI の代役。依頼ファイル（WEB_TEST_PROMPT_FILE）を読み、
// FAKE_AGENT_MODE に応じた YAML を返す。依頼の写しを FAKE_AGENT_LOG に追記する。
const fs = require('fs');
const prompt = fs.readFileSync(process.env.WEB_TEST_PROMPT_FILE, 'utf8');
if (process.env.FAKE_AGENT_LOG) fs.appendFileSync(process.env.FAKE_AGENT_LOG, prompt + '\n=====\n');
const good = `suite: 生成したスイート
cases:
  - id: TC-001
    title: ログイン画面が出る
    steps:
      - goto: /
      - expect: { visible: { role: heading, name: ログイン } }
      - screenshot: ログイン画面
`;
const bad = `suite: 誤りのあるスイート
cases:
  - id: TC-001
    title: 誤り
    steps:
      - tap: { text: ログイン }
`;
const retried = prompt.includes('前回の出力の問題');
const mode = process.env.FAKE_AGENT_MODE || 'good';
const yaml = mode === 'bad-then-good' && !retried ? bad : mode === 'always-bad' ? bad : good;
process.stdout.write(`\x1b[32m考えています…\x1b[0m\n以下がテストケースです。\n\n\`\`\`yaml\n${yaml}\`\`\`\n`);
