'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const DIR = path.join(__dirname, '..');

test('install.sh は bash の構文として正しい', { skip: process.platform === 'win32' }, () => {
  execFileSync('bash', ['-n', path.join(DIR, 'install.sh')]);
});

test('install.ps1 は BOM 付き UTF-8（Windows PowerShell 5.1 が日本語を読み違えない）', () => {
  const head = fs.readFileSync(path.join(DIR, 'install.ps1')).subarray(0, 3);
  assert.deepStrictEqual([...head], [0xef, 0xbb, 0xbf]);
});

test('install.py は Windows では install.ps1 を PowerShell で、ほかでは install.sh を呼ぶ', () => {
  const py = process.platform === 'win32' ? 'python' : 'python3';
  const code = 'import json, sys; sys.path.insert(0, sys.argv[1]); import install; ' +
    'print(json.dumps([install.command(["--check", "--skip-browser"], True), install.command(["--with-deps"], False)]))';
  const [win, other] = JSON.parse(execFileSync(py, ['-c', code, DIR], { encoding: 'utf8' }));
  assert.deepStrictEqual(win.slice(1, 6), ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(DIR, 'install.ps1')]);
  assert.deepStrictEqual(win.slice(6), ['-Check', '-SkipBrowser']);
  assert.deepStrictEqual(other, ['bash', path.join(DIR, 'install.sh'), '--with-deps']);
});

test('インストーラはフォルダを直接 -g せず、npm pack で固めてから入れる（元のフォルダを消しても動く）', () => {
  for (const name of ['install.sh', 'install.ps1']) {
    const text = fs.readFileSync(path.join(DIR, name), 'utf8');
    assert.match(text, /\bpack\b.*--pack-destination/, name);
    assert.doesNotMatch(text, /install -g "?\$(TOOL_DIR|ToolDir)\b/, name);
  }
});

// Volta で Node を管理していると、npm install -g は Volta の置き場に入り、npm prefix -g / root -g はそこを指さない。
// 偽の npm・volta で Volta の置き場を再現し、インストーラが実体と shim を見つけられることを確かめる。
test('install.sh は Volta で Node を管理していても入れた webui-test を見つける', { skip: process.platform === 'win32' }, () => {
  const os = require('os');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'webui-test-volta-'));
  try {
    const volta = path.join(tmp, 'volta');
    const shims = path.join(volta, 'bin');
    const image = path.join(volta, 'tools', 'image', 'packages', 'webui-test');
    const nodeImage = path.join(volta, 'tools', 'image', 'node', '20.0.0');
    fs.mkdirSync(shims, { recursive: true });
    const write = (file, body) => fs.writeFileSync(path.join(shims, file), `#!/usr/bin/env bash\n${body}\n`, { mode: 0o755 });
    write('node', `exec "${process.execPath}" "$@"`);
    write('npx', 'exit 0');
    write('npm', [
      'case "$1" in',
      '  pack) d="${@: -1}"; : > "$d/webui-test-0.0.0.tgz"; echo webui-test-0.0.0.tgz ;;',
      `  install) mkdir -p "${image}/lib/node_modules/webui-test/bin" "${image}/bin"`,
      `    printf '#!/usr/bin/env bash\\necho ok\\n' > "${image}/lib/node_modules/webui-test/bin/webui-test.js"`,
      `    chmod +x "${image}/lib/node_modules/webui-test/bin/webui-test.js"`,
      `    ln -sf ../lib/node_modules/webui-test/bin/webui-test.js "${image}/bin/webui-test"`,
      `    ln -sf "${image}/bin/webui-test" "${shims}/webui-test" ;;`,
      `  prefix) echo "${nodeImage}" ;;`,
      `  root) echo "${nodeImage}/lib/node_modules" ;;`,
      'esac',
    ].join('\n'));
    write('volta', `[ "$1 $2" = "which webui-test" ] && [ -e "${image}/bin/webui-test" ] && echo "${image}/bin/webui-test"`);
    const out = execFileSync('bash', [path.join(DIR, 'install.sh'), '--skip-browser'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PATH: `${shims}:/usr/bin:/bin`, VOLTA_HOME: volta },
    });
    assert.match(out, new RegExp(`Volta の置き場に入りました（${image}/lib/node_modules/webui-test）`));
    assert.match(out, /できました/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('install.ps1 も Volta の置き場を volta which で探す', () => {
  const text = fs.readFileSync(path.join(DIR, 'install.ps1'), 'utf8');
  assert.match(text, /volta which webui-test/);
});
