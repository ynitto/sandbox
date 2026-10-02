'use strict';
// 条件（自然文）からテストケースファイルを作る。エージェント CLI（Kiro CLI / GitHub Copilot CLI）に
// 書式の説明・条件・対象画面の要素一覧を渡し、返ってきた YAML を検査してから保存する。
// 検査に落ちたら、エラーを添えてもう一度だけ頼み直す（--retries で回数を変えられる）。

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const YAML = require('yaml');
const { normalize } = require('./casefile');
const { initState, loadState } = require('./probe');

const FORMAT_REFERENCE = fs.readFileSync(path.join(__dirname, 'format-reference.md'), 'utf8');

// プロンプトは長くなる（書式の説明 + 画面の要素一覧）ので、コマンドライン引数ではなくファイルで渡し、
// エージェントにはそのファイルを読むよう頼む。Windows の .cmd 経由でも引数の引用が壊れない。
// explore は playwright-cli をシェルで動かすので、コマンド実行も許す起動形に替える。
const AGENTS = {
  kiro: {
    command: ['kiro-cli', 'chat', '--no-interactive', '--trust-tools=fs_read'],
    explore: ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools'],
  },
  copilot: {
    command: ['copilot', '-s', '--no-color', '--allow-all-tools', '-p'],
    explore: ['copilot', '-s', '--no-color', '--allow-all-tools', '-p'],
  },
};

function splitCommand(s) {
  const out = [];
  let cur = '';
  let quote = null;
  let has = false;
  for (const ch of s) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch; has = true;
    } else if (/\s/.test(ch)) {
      if (cur || has) out.push(cur);
      cur = ''; has = false;
    } else {
      cur += ch; has = true;
    }
  }
  if (cur || has) out.push(cur);
  return out;
}

function agentCommand(opts) {
  const custom = opts.agentCmd || process.env.WEBUI_TEST_AGENT_CMD;
  if (custom) return Array.isArray(custom) ? custom : splitCommand(custom);
  const a = AGENTS[opts.agent || 'kiro'];
  if (!a) throw new Error(`--agent は ${Object.keys(AGENTS).join(' / ')} のどれかです（ほかの CLI は --agent-cmd で指定）`);
  return opts.explore ? a.explore : a.command;
}

function buildPrompt({ conditions, url, pageInfo, existing, baseUrl, feedback, explore }) {
  const parts = [
    'あなたは Web アプリのテスト設計者です。下の「条件」を満たすテストケースファイルを作ってください。',
    '',
    '## 守ること',
    '- 出力は下の書式に沿った YAML を 1 つだけ、```yaml と ``` で囲んで返す。説明の文章は書かない。',
    '- 要素は「対象の書き方」の表の上の方（role / label / text）を優先して指す。「画面の要素一覧」に出ている名前をそのまま使い、無い名前を想像で作らない。',
    '- 各ケースには確かめるステップ（expect）を入れる。操作だけのケースにしない。',
    '- 結果を目で確かめられるよう、確かめたい画面の状態で screenshot ステップを入れる。',
    '- 正常系に加えて、条件から読み取れる異常系・境界値のケースも作る（条件で指定があればそれに従う）。',
    '- ケース ID は TC-001 から連番。条件に要件 ID があれば requirement に書く。',
    '',
    FORMAT_REFERENCE,
    '',
    '## 条件',
    conditions.trim(),
  ];
  if (baseUrl || url) parts.push('', '## 対象', `- baseUrl: ${baseUrl || new URL(url).origin}`, ...(url ? [`- 最初に開くページ: ${url}`] : []));
  if (pageInfo) {
    parts.push('', '## 画面の要素一覧（実際にページを開いて取ったアクセシビリティツリー）', `タイトル: ${pageInfo.title}`, `URL: ${pageInfo.url}`, '```yaml', pageInfo.aria, '```');
  }
  if (explore) {
    parts.push('', '## 画面を操作して確かめる（playwright-cli）',
      `ブラウザは開いてあり、最初のページを表示しています。次のコマンドをシェルで実行して画面を操作できます（先頭は毎回このとおりに書く）: \`${explore.command}\``,
      `- 画面の要素を見る: \`${explore.command} snapshot\`（出力の [ref=e12] が要素の番号）`,
      // probe のときは ref での操作を案内しない（下の確かめ方と食い違う）。画面の移り方だけ残し、ref は読むためだけと 1 行で言う
      ...(explore.probe
        ? [`- 画面を移る: \`${explore.command} goto <URL>\` / \`go-back\``,
          '- snapshot の ref（e12 など）は画面を読むためだけに使い、操作にも YAML にも書かない。']
        : [`- 操作する: \`${explore.command} click e12\` / \`fill e8 "文字"\` / \`press Enter\` / \`goto <URL>\` / \`go-back\``]),
      `- 文字を探す: \`${explore.command} find "保存"\``,
      `- 条件に出てくる画面まで実際に進み、各画面で snapshot を取ってから、そこで見た役割と名前でケースを書く。${explore.probe ? '' : 'ref（e12 など）は YAML に書かない。'}`,
      '- 削除・購入・送信など、取り消せない操作は条件で求められていない限り実行しない。',
      '- ブラウザは閉じなくてよい（webui-test が閉じる）。');
    if (explore.probe) {
      parts.push('', '## 状態を変える操作の前に確かめる',
        'クリック・入力・選択・チェック・Enter での送信は、直前に対象を確かめてから行う。確かめていない操作はこのコマンドが断る。',
        `- 確かめる: \`${explore.command} probe --role button --name "保存"\`（ほかに --label / --text / --placeholder / --test-id / --exact）`,
        '  - 返ってきた JSON の ready が true（一致が 1 つ・見えている・押せる）のときだけ、その probeId で操作する',
        '  - matches が 2 以上なら役割・名前・exact で 1 つに絞り直す。見えない・押せない対象は操作しない',
        `- 操作する: \`${explore.command} click --probe probe-0001\` / \`fill --probe probe-0002 "文字"\` / \`select --probe probe-0003 値\` / \`check --probe probe-0004\` / \`press Enter --probe probe-0005\``,
        '- 1 回操作するか画面を移ると、それまでの probe は使えない。次の操作の直前にもう一度 probe する',
        `- テストで確かめたいことに気づいたら残す: \`${explore.command} observe "保存すると「保存しました」と出る"\``,
        '- YAML の対象には、ready になった probe と同じ指定（role と name など）を書く。');
    }
  }
  if (existing) parts.push('', '## 今あるテストケースファイル（これを直す・足す）', '```yaml', existing.trim(), '```');
  if (feedback) parts.push('', '## 前回の出力の問題（直して出し直してください）', feedback);
  return parts.join('\n');
}

// 画面を開いて、エージェントが要素を正しい名前で指せるようにアクセシビリティツリーを取る
async function snapshotPage(url, opts = {}) {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true, ...(opts.executablePath ? { executablePath: opts.executablePath } : {}) });
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 }, ...(opts.locale ? { locale: opts.locale } : {}) })).newPage();
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
    let aria = await page.locator('body').ariaSnapshot();
    const limit = opts.maxChars || 20000;
    if (aria.length > limit) aria = aria.slice(0, limit) + '\n# …（長いので省略）';
    return { title: await page.title(), url: page.url(), aria };
  } finally {
    await browser.close();
  }
}

// エージェントの出力から YAML を取り出す（最後の ```yaml ブロック。無ければ全体）
function extractYaml(output) {
  // eslint-disable-next-line no-control-regex
  const clean = output.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\r/g, '');
  const blocks = [...clean.matchAll(/```(?:ya?ml)?[ \t]*\n([\s\S]*?)\n[ \t>]*```/g)].map((m) => m[1]);
  const candidates = blocks.length ? blocks.reverse() : [clean];
  for (const c of candidates) {
    // Kiro は行頭に "> " を付けて返すことがある
    const text = /^> /m.test(c) && c.split('\n').every((l) => !l || l.startsWith('>')) ? c.replace(/^> ?/gm, '') : c;
    try {
      const data = YAML.parse(text);
      if (data && typeof data === 'object' && data.cases) return { text: text.trim() + '\n', data };
    } catch (_) { /* 次の候補 */ }
  }
  return null;
}

function runAgent(argv, promptFile, opts = {}) {
  const instruction = `ファイル「${promptFile}」を読み、そこに書かれた指示に従って、テストケースの YAML を 1 つだけ \`\`\`yaml ブロックで返してください。`;
  const [cmd, ...args] = argv;
  const hasPlaceholder = args.some((a) => a.includes('{prompt}') || a.includes('{prompt_file}'));
  const finalArgs = hasPlaceholder
    ? args.map((a) => a.replace('{prompt_file}', promptFile).replace('{prompt}', instruction))
    : [...args, instruction];
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, finalArgs, {
      cwd: opts.cwd || process.cwd(),
      env: { ...process.env, WEBUI_TEST_PROMPT_FILE: promptFile, ...(opts.exploreEnv || {}) },
      // Windows の npm グローバル（copilot.cmd など）は shell 経由でないと起動できない
      shell: process.platform === 'win32' && !/\.exe$/i.test(cmd),
      windowsHide: true,
    });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { out += d; if (opts.verbose) process.stderr.write(d); });
    child.stderr.on('data', (d) => { err += d; if (opts.verbose) process.stderr.write(d); });
    child.on('error', (e) => reject(new Error(`エージェント「${cmd}」を起動できません: ${e.message}`)));
    const timer = opts.timeoutMs ? setTimeout(() => child.kill(), opts.timeoutMs) : null;
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      if (code !== 0 && !out.trim()) return reject(new Error(`エージェントが失敗しました（終了コード ${code}）: ${err.trim().slice(-500)}`));
      resolve(out);
    });
  });
}

// playwright-cli の場所。このツールに入っているもの（optionalDependencies）を優先し、無ければ PATH のもの。
// 戻り値はコマンドの配列（先頭が実行ファイル）。
function findPlaywrightCli() {
  if (process.env.WEBUI_TEST_PLAYWRIGHT_CLI_BIN) return splitCommand(process.env.WEBUI_TEST_PLAYWRIGHT_CLI_BIN);
  try {
    return [process.execPath, require.resolve('@playwright/cli/playwright-cli.js')];
  } catch (_) {
    return ['playwright-cli'];
  }
}

function quoteArg(a) {
  return /^[\w@%+=:,./\\-]+$/.test(a) ? a : `"${a.replace(/"/g, '\\"')}"`;
}

function runQuiet(argv, cwd) {
  return new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), { cwd, windowsHide: true, shell: process.platform === 'win32' && !/\.(exe)$/i.test(argv[0]) && argv[0] !== process.execPath });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    child.on('error', (e) => resolve({ code: -1, out: e.message }));
    child.on('close', (code) => resolve({ code, out }));
  });
}

// エージェントが操作するブラウザを先に開いておく。セッション名を決めて渡すので、エージェントの
// コマンドはこのブラウザだけに届き、終わったら webui-test が閉じる。ブラウザはこのツールの Chromium を使う。
async function openExploreSession(url, workDir, opts) {
  const bin = findPlaywrightCli();
  const session = `webui-test-${process.pid}-${Date.now().toString(36)}`;
  let executablePath = opts.executablePath;
  if (!executablePath) {
    try { executablePath = require('playwright').chromium.executablePath(); } catch (_) { executablePath = null; }
    if (executablePath && !fs.existsSync(executablePath)) executablePath = null;
  }
  const config = path.join(workDir, 'playwright-cli.json');
  fs.writeFileSync(config, JSON.stringify({ browser: { browserName: 'chromium', launchOptions: { headless: true, ...(executablePath ? { executablePath, channel: 'chromium' } : {}) } } }, null, 2));
  const base = [...bin, `-s=${session}`];
  const r = await runQuiet([...base, 'open', url, `--config=${config}`], opts.cwd || process.cwd());
  if (r.code !== 0) throw new Error(`playwright-cli でブラウザを開けません（npm install で @playwright/cli を入れるか、PATH に playwright-cli を置いてください）:\n${r.out.trim().slice(-800)}`);
  return {
    session,
    command: base.map(quoteArg).join(' '),
    close: () => runQuiet([...base, 'close'], opts.cwd || process.cwd()),
  };
}

// 条件からテストケースファイルを作って outFile に保存する。戻り値 { file, cases, attempts }
async function generate(opts) {
  const argv = agentCommand(opts);
  const existing = opts.outFile && opts.update && fs.existsSync(opts.outFile) ? fs.readFileSync(opts.outFile, 'utf8') : null;
  const pageInfo = opts.url && opts.snapshot !== false ? await snapshotPage(opts.url, opts) : null;
  // 依頼ファイルは作業ディレクトリの下に置く（Copilot CLI は既定で作業ディレクトリの外を読まない）
  const base = path.join(opts.cwd || process.cwd(), '.webui-test');
  fs.mkdirSync(base, { recursive: true });
  const workDir = fs.mkdtempSync(path.join(base, 'request-'));
  const retries = opts.retries ?? 1;
  let feedback = null;
  let explore = null;
  let probe = null;
  const probeResult = () => (probe ? { evidenceDir: probe.evidenceDir, evidenceFile: path.join(probe.evidenceDir, 'explore-evidence.jsonl'), stats: loadState(probe.state).stats } : undefined);
  try {
    if (opts.probeBeforeAct && !opts.explore) throw new Error('--probe-before-act は --explore と一緒に使います');
    if (opts.explore) {
      if (!opts.url) throw new Error('--explore には --url（最初に開くページ）が要ります');
      explore = await openExploreSession(opts.url, workDir, opts);
      if (opts.probeBeforeAct) {
        // エージェントには playwright-cli の代わりに見張り役（webui-test browse）を渡す
        probe = { state: path.join(workDir, 'probe-state.json'), evidenceDir: opts.evidenceDir || path.resolve(opts.cwd || process.cwd(), 'webui-test-results', `explore-${Date.now().toString(36)}`) };
        initState(probe.state);
        fs.mkdirSync(probe.evidenceDir, { recursive: true });
        const guard = [process.execPath, path.join(__dirname, '..', 'bin', 'webui-test.js'), 'browse', `--session=${explore.session}`, `--state=${probe.state}`, `--evidence=${probe.evidenceDir}`];
        explore = { ...explore, command: guard.map(quoteArg).join(' '), probe: true };
      }
      opts = { ...opts, exploreEnv: { WEBUI_TEST_PLAYWRIGHT_CLI: explore.command } };
    }
    for (let attempt = 1; attempt <= retries + 1; attempt += 1) {
      const prompt = buildPrompt({ conditions: opts.conditions, url: opts.url, baseUrl: opts.baseUrl, pageInfo, existing, feedback, explore });
      const promptFile = path.join(workDir, `request-${attempt}.md`);
      fs.writeFileSync(promptFile, prompt);
      if (opts.log) opts.log(`エージェントに依頼しています（${attempt} 回目）: ${argv[0]}`);
      const output = await runAgent(argv, promptFile, opts);
      const got = extractYaml(output);
      if (!got) {
        feedback = '出力から YAML（cases を持つもの）を取り出せませんでした。```yaml ブロックで YAML だけを返してください。';
        continue;
      }
      const { errors } = normalize(got.data);
      if (errors.length) {
        feedback = errors.map((e) => `- ${e}`).join('\n');
        if (opts.log) opts.log(`書式の誤りがありました（${errors.length} 件）。直してもらいます`);
        continue;
      }
      if (!got.data.baseUrl && (opts.baseUrl || opts.url)) {
        got.data = { suite: got.data.suite, baseUrl: opts.baseUrl || new URL(opts.url).origin, ...got.data };
        got.text = YAML.stringify(got.data, { lineWidth: 0 });
      }
      const header = `# webui-test generate で作成（${new Date().toISOString()}）\n# 条件: ${opts.conditions.trim().split('\n').join('\n#       ')}\n`;
      fs.mkdirSync(path.dirname(path.resolve(opts.outFile)), { recursive: true });
      fs.writeFileSync(opts.outFile, header + got.text);
      return { file: opts.outFile, cases: got.data.cases.length, attempts: attempt, probe: probeResult() };
    }
    const err = new Error(`エージェントの出力が書式に合いませんでした:\n${feedback}`);
    err.attempts = retries + 1;
    err.probe = probeResult();
    throw err;
  } finally {
    if (explore) await explore.close();
    fs.rmSync(workDir, { recursive: true, force: true });
    try { fs.rmdirSync(base); } catch (_) { /* ほかの依頼が残っていれば消さない */ }
  }
}

module.exports = { generate, findPlaywrightCli, openExploreSession, buildPrompt, extractYaml, snapshotPage, splitCommand, agentCommand, FORMAT_REFERENCE, AGENTS };
