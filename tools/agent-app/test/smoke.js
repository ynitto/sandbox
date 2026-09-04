'use strict';

// Electron を本当に起動し、疑似 CLI（tmux 上）と会話して各画面のスクリーンショットを撮る。
// npm test には含めない（画面が要る）。Linux なら:
//   SMOKE_OUT=/tmp/shots xvfb-run -a npx electron --no-sandbox test/smoke.js
// 画面のある環境ならそのまま `npx electron test/smoke.js`。SMOKE_OUT（既定: このフォルダ）へ PNG を書く。
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const OUT = process.env.SMOKE_OUT || __dirname;
const APP = process.env.SMOKE_APP || path.join(__dirname, '..');
const ud = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-ud-'));
app.setPath('userData', ud);

// 疑似 CLI と、それを指す定義
const agentsDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-agents-'));
const stub = path.join(agentsDir, 'stub-cli');
fs.writeFileSync(stub, `#!/usr/bin/env bash
stty -echo 2>/dev/null
printf '\\033[1mstub cli\\033[0m ready\\n'
while true; do
  printf '> '
  IFS= read -r line || exit 0
  printf '%s\\n' "$line"
  printf 'thinking… (esc to interrupt)\\n'
  sleep 1
  printf '\\033[1A\\r\\033[2K'
  printf '\\033[32m⏺\\033[0m ## 返答\\n\\n\`%s\` について **Markdown** で答える:\\n\\n- 項目 1\\n- 項目 2\\n\\n\`\`\`js\\nconst x = 1;\\n\`\`\`\\n\\n\`\`\`mermaid\\ngraph LR; A-->B; B-->C\\n\`\`\`\\n' "$line"
done
`, { mode: 0o755 });
fs.writeFileSync(path.join(agentsDir, 'stub.json'), JSON.stringify({
  command: [stub, '--headless'], prompt_via: 'stdin',
  interactive: { command: [stub], ready_pattern: '^[[:space:]]*>[[:space:]]*$', busy_pattern: 'esc to interrupt', ready_timeout_sec: 10 },
}));
process.env.KIRO_AGENTS_DIR = agentsDir;

// リポジトリ（git 付き・変更あり）
const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-app-repo-'));
const run = (args) => execFileSync('git', ['-C', repo, ...args], { stdio: 'pipe' });
run(['init', '-q']); run(['config', 'user.email', 't@example.com']); run(['config', 'user.name', 't']);
fs.mkdirSync(path.join(repo, 'src', 'lib'), { recursive: true });
fs.writeFileSync(path.join(repo, 'README.md'), `# Sample\n\n説明文。**太字** と \`code\`。\n\n\`\`\`mermaid\nsequenceDiagram\n  participant A\n  participant B\n  A->>B: hello\n  B-->>A: world\n\`\`\`\n\n\`\`\`python\ndef f(x):\n    return x * 2\n\`\`\`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n`);
fs.writeFileSync(path.join(repo, 'src', 'index.ts'), `import { x } from './lib/util';\n\nexport function main(argv: string[]): number {\n  // コメント\n  const n = argv.length;\n  return x(n) + 1;\n}\n`);
fs.writeFileSync(path.join(repo, 'src', 'lib', 'util.py'), `def x(n: int) -> int:\n    """doc"""\n    return n * 2\n`);
fs.writeFileSync(path.join(repo, 'Dockerfile'), 'FROM node:22\nRUN npm ci\n');
run(['add', '.']); run(['commit', '-qm', 'init']);
fs.writeFileSync(path.join(repo, 'src', 'index.ts'), `import { x } from './lib/util';\n\nexport function main(argv: string[]): number {\n  const n = argv.length + 1;\n  return x(n) + 2;\n}\n`);
fs.writeFileSync(path.join(repo, 'NEW.md'), '# new\n');

fs.writeFileSync(path.join(ud, 'config.json'), JSON.stringify({ repos: [repo], lastRepo: repo, lastCli: 'stub', transport: 'tmux' }));

require(path.join(APP, 'src/main/main.js'));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function shot(win, name) {
  const img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(OUT, `${name}.png`), img.toPNG());
  console.log('shot', name);
}
async function js(win, code) { return win.webContents.executeJavaScript(code, true); }

app.whenReady().then(async () => {
  await sleep(1500);
  const win = BrowserWindow.getAllWindows()[0];
  win.setSize(1480, 940);
  const errors = [];
  win.webContents.on('console-message', (e, level, message) => { if (level >= 2) errors.push(message); });
  await sleep(2500);
  console.log('notice:', await js(win, "document.getElementById('notice').textContent"));
  console.log('host:', await js(win, "document.getElementById('host-status').textContent"));
  console.log('cli options:', await js(win, "[...document.getElementById('cli').options].map(o=>o.value+':'+o.textContent).join(',')"));
  await shot(win, '01-empty');
  await js(win, "document.getElementById('cli').value='stub'; document.getElementById('prompt').value='こんにちは、テストです'; document.getElementById('send').click();");
  await sleep(1500);
  await js(win, "document.getElementById('term-toggle').click()");
  await sleep(1200);
  await shot(win, '02-working-with-terminal');
  await sleep(2500);
  console.log('phase:', await js(win, "document.getElementById('phase').textContent"));
  console.log('msgs:', await js(win, "document.querySelectorAll('#messages .msg').length"));
  console.log('last:', await js(win, "(document.querySelector('#messages .msg.assistant:last-child')||{}).innerText"));
  await shot(win, '03-answered');
  await js(win, "document.getElementById('term-toggle').click(); document.getElementById('changes-toggle').click();");
  await sleep(1500);
  await shot(win, '04-changes');
  await js(win, "document.getElementById('diff-style').click()");
  await sleep(800);
  await shot(win, '05-changes-side');
  await js(win, "document.getElementById('changes-toggle').click(); document.getElementById('view-files').click();");
  await sleep(800);
  await js(win, "Files.openFile('README.md').then(()=>Files.reveal('README.md'))");
  await sleep(2000);
  await shot(win, '06-md-preview');
  await js(win, "document.getElementById('viewer-mode-code').click()");
  await sleep(500);
  await shot(win, '07-md-code');
  await js(win, "Files.openFile('src/index.ts').then(()=>Files.reveal('src/index.ts'))");
  await sleep(1000);
  await shot(win, '08-ts');
  await js(win, "Files.openFile('src/lib/util.py')");
  await sleep(600);
  await shot(win, '09-py');
  await js(win, "document.getElementById('tree-filter').value='util'; document.getElementById('tree-filter').dispatchEvent(new Event('input'))");
  await sleep(800);
  await shot(win, '10-filter');
  console.log('console errors:', JSON.stringify(errors, null, 1));
  console.log('sessions:', fs.readdirSync(path.join(ud, 'sessions')).map((f) => fs.readFileSync(path.join(ud, 'sessions', f), 'utf8')).join('\n'));
  // 会話を削除して tmux セッションも消えることを見る
  await js(win, "document.getElementById('view-chat').click(); window.confirm = () => true; document.getElementById('session-delete').click();");
  await sleep(1500);
  console.log('tmux after delete:', execFileSync('bash', ['-lc', 'tmux -L agent-app ls 2>&1 || true']).toString().trim());
  app.exit(0);
}).catch((e) => { console.error('SMOKE FAIL', e); app.exit(1); });
