#!/usr/bin/env node
'use strict';
// テスト用のエージェント CLI の代役。依頼ファイル（WEBUI_TEST_PROMPT_FILE）を読み、
// FAKE_AGENT_MODE に応じた YAML を返す。依頼の写しを FAKE_AGENT_LOG に追記する。
const fs = require('fs');
const prompt = fs.readFileSync(process.env.WEBUI_TEST_PROMPT_FILE, 'utf8');
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
// explore: 渡された playwright-cli で画面を見て、見えた要素名を記録する（本物のエージェントの代わり）
if (process.env.FAKE_AGENT_MODE === 'explore') {
  const { execSync } = require('child_process');
  const cmd = process.env.WEBUI_TEST_PLAYWRIGHT_CLI;
  if (!cmd || !prompt.includes(cmd)) { process.stderr.write('explore の指示がありません'); process.exit(3); }
  const snap = execSync(`${cmd} snapshot`, { encoding: 'utf8' });
  if (process.env.FAKE_AGENT_LOG) fs.appendFileSync(process.env.FAKE_AGENT_LOG, `SNAPSHOT:\n${snap}\n=====\n`);
}
// probe / naive: 確認用の画面（editor.html）で「保存」を押すケースを作る、決まった手順のエージェント。
// 最初は画面の文字「保存」で対象を指す（素朴な選び方）。見張り役（browse）を渡されたときは probe の結果を見て、
// 1 つに決まらなければ役割と名前で指し直す。probe は加えて、ref での直の操作を試して断られる。
if (process.env.FAKE_AGENT_MODE === 'probe' || process.env.FAKE_AGENT_MODE === 'naive') {
  const { spawnSync } = require('child_process');
  const cmd = process.env.WEBUI_TEST_PLAYWRIGHT_CLI;
  const guarded = / browse /.test(cmd) && prompt.includes('## 状態を変える操作の前に確かめる');
  const sh = (args) => {
    const r = spawnSync(`${cmd} ${args}`, { shell: true, encoding: 'utf8' });
    if (process.env.FAKE_AGENT_LOG) fs.appendFileSync(process.env.FAKE_AGENT_LOG, `$ ${args}\n${r.stdout}${r.stderr}\n`);
    return { code: r.status, json: (() => { try { return JSON.parse(r.stdout); } catch (_) { return null; } })() };
  };
  let target = '{ text: 保存 }';
  if (guarded) {
    if (process.env.FAKE_AGENT_MODE === 'probe') sh('click e3');
    const p1 = sh('probe --text 保存');
    let ready = p1.json && p1.json.ready ? p1.json : null;
    if (!ready) {
      const p2 = sh('probe --role button --name 保存');
      if (p2.json && p2.json.ready) { ready = p2.json; target = '{ role: button, name: 保存 }'; }
    }
    if (ready) sh(`click --probe ${ready.probeId}`);
    const p3 = sh('probe --label タイトル');
    if (p3.json && p3.json.ready) sh(`fill --probe ${p3.json.probeId} "hunter2-secret"`);
    sh('observe "保存すると「保存しました」と出る" --role status');
  } else {
    sh('find 保存');
  }
  const yaml = `suite: メモの編集
cases:
  - id: TC-001
    title: 保存すると保存しましたと出る
    steps:
      - goto: /editor.html
      - click: ${target}
      - expect: { target: { role: status }, text: 保存しました }
      - screenshot: 保存後
`;
  process.stdout.write(`\`\`\`yaml\n${yaml}\`\`\`\n`);
  process.exit(0);
}
const retried = prompt.includes('前回の出力の問題');
const mode = process.env.FAKE_AGENT_MODE || 'good';
const yaml = mode === 'bad-then-good' && !retried ? bad : mode === 'always-bad' ? bad : good;
process.stdout.write(`\x1b[32m考えています…\x1b[0m\n以下がテストケースです。\n\n\`\`\`yaml\n${yaml}\`\`\`\n`);
