'use strict';

// タスク（ステートマシン）を AI と**tmux の会話で**作る・変えるための材料。
//
//   下書き（sidecar） … `.statemachine/<機械名>/teaching.json`。定義（workflow.yaml）がまだ無い
//                      タスクを一覧に出すためと、そのタスクの会話（agent-app の会話 ID）を
//                      覚えるためだけに持つ。定義ができれば「利用可能」で、この印は要らない。
//   依頼文           … 会話の最初に CLI へ送る本文。statemachine-use スキルの作成モードで
//                      `.statemachine/<機械名>/` を書くこと、見本の依頼の作法（@record 行）、
//                      検証の仕方を伝える。AI はこの会話の中でファイルを直接書く。
//   見本の記録       … ブラウザは、このアプリが Edge をリモートデバッグ付きで起こし、固定文
//                      （teachingProtocol.js）で AI に知らせて、AI 自身が CDP 越しに記録を取る。
//                      押すボタンは 1 つで、開く（準備）→ 記録開始 → 終了の 3 段を進み、段ごとに
//                      別の固定文が届く。準備の操作は見本に入らない。
//                      Windows アプリは、利用者の端末（このアプリ）の winauto で取った記録を AI が
//                      読める Markdown にして `.statemachine/<機械名>/recordings/` へ置く。
//
// 会話そのもの（tmux セッション・依頼の送信・応答の取り出し）は agent-app の会話基盤が担う。

const fs = require('fs');
const path = require('path');
const { randomUUID } = require('crypto');
const store = require('./store');
const model = require('./model');
const protocol = require('../../renderer/teachingProtocol');

const FILE = 'teaching.json';
const RECORDINGS = 'recordings';
const VERSION = 2;

function text(value, max = 4000) {
  return String(value || '').trim().slice(0, max);
}

function fileFor(root, machine) {
  return path.join(store.machineDir(root, machine), FILE);
}

// 目的の 1 行目から保存名を作る（英数字だけを残し、無ければ job-<乱数>）。
function machineNameFor(purpose) {
  const ascii = String(purpose || '').split(/\r?\n/)[0].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return ascii.length >= 3 ? ascii : `job-${randomUUID().slice(0, 8)}`;
}

function normalize(value = {}, machine = '') {
  const input = value && typeof value === 'object' ? value : {};
  return {
    version: VERSION,
    machine: text(input.machine || machine, 120),
    title: text(input.title, 300),
    purpose: text(input.purpose),
    sessionId: /^[0-9a-f-]{36}$/.test(String(input.sessionId || '')) ? String(input.sessionId) : '',
    recordings: (Array.isArray(input.recordings) ? input.recordings : []).map((item) => ({
      file: text(item && item.file, 300),
      source: item && item.source === 'windows' ? 'windows' : 'browser',
      target: text(item && item.target, 500),
      steps: Number(item && item.steps) || 0,
      capturedAt: text(item && item.capturedAt, 80),
    })).filter((item) => item.file),
    createdAt: text(input.createdAt, 80),
    updatedAt: text(input.updatedAt, 80),
  };
}

function load(root, machine) {
  let body;
  try { body = fs.readFileSync(fileFor(root, machine), 'utf8'); } catch (err) {
    if (err && err.code === 'ENOENT') return null;
    throw err;
  }
  try { return normalize(JSON.parse(body), machine); } catch (err) {
    throw new Error(`タスクの下書きを読み取れません: ${err.message}`, { cause: err });
  }
}

function save(root, machine, value) {
  const now = new Date().toISOString();
  const sidecar = normalize({ ...value, machine }, machine);
  sidecar.createdAt = sidecar.createdAt || now;
  sidecar.updatedAt = now;
  const file = fileFor(root, machine);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(sidecar, null, 2)}\n`, 'utf8');
  fs.renameSync(temporary, file);
  return sidecar;
}

// 画面に出す状態。定義があれば「利用可能」、無ければ「下書き」。
function presentStatus({ published = false } = {}) {
  return published ? { status: 'ready', published: true, runnable: true } : { status: 'draft', published: false, runnable: false };
}

function list(root) {
  const base = path.join(root, store.DIR);
  let names;
  try { names = fs.readdirSync(base); } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw err;
  }
  const items = [];
  for (const machine of names.sort()) {
    if (!fs.existsSync(path.join(base, machine, FILE))) continue;
    try {
      const sidecar = load(root, machine);
      const published = store.exists(root, machine);
      items.push({
        machine, title: sidecar.title || machine, purpose: sidecar.purpose, sessionId: sidecar.sessionId,
        published, status: presentStatus({ published }).status, recordings: sidecar.recordings.length,
      });
    } catch { /* 壊れた下書きは開いたときに理由を表示する */ }
  }
  return items;
}

// --- 依頼文 -------------------------------------------------------------------------

// 会話の最初に送る本文。
//   machine   … 保存名（.statemachine/<machine>/）
//   purpose   … 利用者が書いた目的（新規のとき）
//   existing  … 既存の定義を変える会話か
//   skillDir  … statemachine-use スキルの所在（ホスト側のパス。無ければ ''）
//   platform  … このアプリの OS（win32 なら「tmux は WSL、画面は Windows」を伝える）
//   tools     … { browser: bool, windows: bool } 見本を取れる道具（Edge / winauto）がこの端末にあるか
//   endpoint  … ブラウザの見本で AI が接続する CDP の接続先（既定 http://localhost:9222）
function prompt({ machine, purpose = '', existing = false, skillDir = '', platform = process.platform, tools = {}, endpoint = protocol.DEFAULT_ENDPOINT } = {}) {
  const name = String(machine || '').trim();
  const dir = `.statemachine/${name}/`;
  const runner = skillDir ? `python ${skillDir.replace(/[\\/]+$/, '')}/scripts/run_machine.py` : 'python .github/skills/statemachine-use/scripts/run_machine.py';
  const where = platform === 'win32'
    ? 'あなたは WSL の tmux で動いていて、利用者の画面（ブラウザ・Windows アプリ）は Windows 側にあります。Windows 側の playwright-cli を WSL から起こすことはできません。'
    : '利用者の画面はこのアプリと同じ端末にあります。';
  const available = [tools.browser ? 'ブラウザ（Edge）' : '', tools.windows ? 'Windows アプリ（winauto）' : ''].filter(Boolean);
  const lines = [
    `あなたはこのリポジトリで「タスク」（\`statemachine-use\` スキルで動くステートマシン）を${existing ? '変更する' : '作る'}担当です。`,
    `保存先は \`${dir}\`（workflow.yaml と actions/*.md）で、この中だけを書き換えてください。maker.json は書かなくてかまいません。`,
    '',
    '進め方:',
    existing
      ? `1. まず \`${dir}\` の workflow.yaml と actions/*.md を読み、今の工程を短く要約してから、利用者に変更したい点を聞いてください。`
      : '1. 利用者の目的を読み、曖昧な点（固定値か毎回変わる値か・期待する結果・送信や保存などの重要操作）だけを質問してください。分かることは聞かずに進めます。',
    `2. 工程・分岐・完了確認は \`statemachine-use\` スキルの作成モードに従って組みます（scaffold.py で骨組み → 本文を埋める → \`${runner} ${dir}workflow.yaml --dry-run\` で検証）。毎回変わる値は \`{{key}}\` で受けます。`,
    '3. 画面操作（ブラウザ・Windows アプリ）の工程で、実際の画面を見ないと操作を決められないときは、利用者に操作の見本を頼んでください。見本の依頼は、次の 1 行を単独の行として返答に書き、利用者に「操作の見本」のカードでボタンを押すよう伝えて待ちます（そのカードは自動で開き、ボタンは 1 つで押すたびに次の段へ進みます）:',
    `   ${protocol.recordLine('browser', '<開始 URL>')}`,
    `   ${protocol.recordLine('windows', '<アプリ名>')}`,
    `   ${where}`,
    `   ブラウザの見本は 3 段です。利用者がボタンを押すたびに、いまどの段かを言う固定文があなたに届きます。固定文が届く前に自分でブラウザを起こしたり記録を始めたりしないでください。`,
    `     ①「ブラウザを開く」→ \`${protocol.RECORDING_MARKER} open\` で始まる固定文（接続先入り）。このアプリが ${platform === 'win32' ? 'Windows 側で ' : ''}Edge をリモートデバッグ付き（${endpoint}）で起こしたところです。\`playwright-cli attach --cdp=${endpoint}\` で接続だけして、つながったかを 1 行で知らせ、待ちます。**この段では記録を始めません**——利用者はここでログインや目的の画面までの移動をしています。その準備を見本に混ぜないため、ブラウザも操作しません。`,
    `     ②「記録を始める」→ \`${protocol.RECORDING_MARKER} start\` で始まる固定文（準備が終わった合図。記録の起点になるページ入り）。\`playwright-cli recording-start\` で記録を始め、1 行で知らせて待ちます。利用者が操作している間はブラウザを操作しません。`,
    `     ③「終了してAIへ渡す」→ \`${protocol.RECORDING_MARKER} stop\` で始まる固定文。\`playwright-cli recording-stop\` で止め、記録の行をそのまま \`${dir}${RECORDINGS}/<時刻>-browser.md\` に保存し、\`playwright-cli detach\` で切り離してから、その見本を根拠に工程を組みます。`,
    `     途中で \`${protocol.RECORDING_MARKER} cancel\` が届いたら、利用者が取り直します。記録していれば止めて、その記録は保存せずに破棄し、\`playwright-cli detach\` で切り離して ① を待ち直します。接続先に届かないときは、その旨を利用者に伝えてください${platform === 'win32' ? '（WSL のネットワークが mirrored でないと localhost が Windows 側に届きません）' : ''}。`,
    `   Windows アプリの見本は 2 段です（準備は利用者がアプリを開くところで済むので、開く段はありません）。「記録を始める」で \`${protocol.RECORDING_MARKER} start\` の固定文が届き、記録はこのアプリが winauto で取ります。あなた自身は winauto の記録を起こさないでください。「終了してAIへ渡す」で、結果の Markdown（\`${dir}${RECORDINGS}/\`）の所在が届きます。`,
    '   見本はあなたが頼んだときだけ来るとは限りません。利用者は、あなたが頼んでいなくても見せ始めることがあります。固定文が届いたら、いま進めている作業に区切りをつけて受け取り、その見本がどの工程のためのものか分からなければ、工程を書き換える前にそれを確かめてください。',
    available.length ? `   いま見本を取れるのは ${available.join('・')} です。` : '   いまこの端末では見本を取る道具（Edge / winauto）が見つかっていません。見本が要るときはその旨も書いてください。',
    '4. 画面操作の本文では `playwright-cli` スキル（ブラウザ）/ `windows-app-automation` スキル（Windows アプリ）を名指しし、見本の記録にある操作の行（role と名前のロケータ）を本文に載せます。',
    '5. 定義を書き終えたら --dry-run で検証し、工程の並び・毎回変わる値・重要操作を短く報告してください。実行はしません（実行は利用者が画面から行います）。',
    '',
  ];
  if (!existing) lines.push('利用者の目的:', text(purpose, 6000) || '（未記入。まず何を自動化したいかを聞いてください）');
  return lines.join('\n');
}

// 下書きを開き直したときや、公開済みのタスクを編集するときにも、単に以前の tmux へ
// 接続するだけにはしない。CLI の resume が利用できない場合でも、この依頼と保存済みの
// ファイルを起点に作業対象を復元できるようにする。
function resumePrompt({ machine, purpose = '', existing = false, context = '' } = {}) {
  const name = String(machine || '').trim();
  const dir = `.statemachine/${name}/`;
  const target = text(context, 1000);
  return [
    existing ? 'このタスクの編集を開始します。' : 'このタスクの下書き作成を再開します。',
    `対象は \`${dir}\` です。まず workflow.yaml、actions/*.md、および存在する記録を読み直し、現在の内容を会話の前提として引き継いでください。`,
    purpose ? `タスクの目的: ${text(purpose, 6000)}` : '',
    target ? `今回の編集対象: ${target}` : '',
    existing
      ? '現在の定義を短く要約し、今回変更したい内容を利用者に確認してください。まだファイルは変更しないでください。'
      : 'これまでの会話と保存済みの下書きを踏まえ、未確定の点だけを質問して作成を続けてください。',
  ].filter(Boolean).join('\n');
}

// 見本を記録した後に渡す本文（「終了してAIへ渡す」の段。利用者が入力欄で確かめてから送る）。
// 1 行目の印は他の段と同じ形にして、画面が「どの段が会話へ届いたか」を本文から見分けられるようにする。
function demonstrationPrompt({ machine, hostPath, source, target = '', steps = 0, parameters = [], requested = true } = {}) {
  const kind = source === 'windows' ? 'Windows アプリ' : 'ブラウザ';
  return [
    `${protocol.RECORDING_MARKER} stop`,
    `操作の見本（${kind}${target ? `: ${target}` : ''}）を記録しました。${hostPath} を読んでください。`,
    `記録は ${steps} 工程の候補と操作の行に整理してあります${parameters.length ? `（毎回変わる値の候補: ${parameters.join(', ')}）` : ''}。`,
    `この見本を根拠に \`.statemachine/${machine}/\` の工程を組み（または直し）、固定値か毎回変わる値かが曖昧な点だけ質問してください。`,
    requested ? '' : 'この見本がどの工程のためのものか分からなければ、工程を書き換える前に、まずそれを確かめてください。',
  ].filter(Boolean).join('\n');
}

// --- 見本の記録（Markdown） ---------------------------------------------------------

function recordingMarkdown({ source, target = '', steps = [], parameters = [], capturedAt = new Date().toISOString() } = {}) {
  const kind = source === 'windows' ? 'windows' : 'browser';
  const lines = [
    `# 操作の見本（${kind === 'windows' ? 'Windows アプリ' : 'ブラウザ'}）`,
    '',
    `- 記録日時: ${capturedAt}`,
    `- ${kind === 'windows' ? '対象アプリ' : '開始 URL'}: ${target || '（未指定）'}`,
    `- 毎回変わる値の候補: ${parameters.length ? parameters.map((key) => `\`{{${key}}}\``).join(', ') : 'なし'}`,
    '',
    '記録は人が 1 回通った経路です。繰り返し・分岐・失敗時の扱いは含まれていません。',
    `操作の行は \`<操作> <ロケータ> [値]\` で、${kind === 'windows' ? '`winauto`' : '`playwright-cli`'} の 1 コマンドに対応します。パスワードらしい値は残していません。`,
    '',
    '## 工程の候補',
    '',
  ];
  steps.forEach((step, index) => {
    lines.push(`### ${index + 1}. ${step.title || '（無題）'}`);
    if (step.target) lines.push(`対象: ${step.target}`);
    if (step.detail) lines.push('', step.detail);
    const recorded = Array.isArray(step.recorded) ? step.recorded : [];
    if (recorded.length) {
      lines.push('', '記録の行:', '```');
      for (const op of recorded) lines.push(model.recordedLine(kind, op));
      lines.push('```');
    }
    if (step.check) lines.push('', `完了確認の候補: \`${step.check}\``);
    lines.push('');
  });
  return `${lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()}\n`;
}

// 見本を `.statemachine/<machine>/recordings/<時刻>-<種類>.md` へ書き、sidecar に控える。
function saveRecording(root, machine, recording) {
  const source = recording && recording.source === 'windows' ? 'windows' : 'browser';
  const steps = Array.isArray(recording && recording.steps) ? recording.steps : [];
  if (!steps.length) throw new Error('記録に工程がありません');
  const capturedAt = new Date().toISOString();
  const dir = path.join(store.machineDir(root, machine), RECORDINGS);
  fs.mkdirSync(dir, { recursive: true });
  const stamp = capturedAt.replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const name = `${stamp}-${source}.md`;
  const file = path.join(dir, name);
  const target = String(recording.url || recording.app || recording.target || '');
  fs.writeFileSync(file, recordingMarkdown({ source, target, steps, parameters: recording.parameters || [], capturedAt }), 'utf8');
  const current = load(root, machine) || normalize({}, machine);
  const saved = save(root, machine, {
    ...current,
    recordings: [...current.recordings, { file: `${RECORDINGS}/${name}`, source, target, steps: steps.length, capturedAt }],
  });
  return { file, relative: `${store.DIR}/${machine}/${RECORDINGS}/${name}`, source, target, steps: steps.length, parameters: recording.parameters || [], sidecar: saved };
}

module.exports = {
  FILE, RECORDINGS, VERSION, fileFor, machineNameFor, normalize, load, save, list, presentStatus,
  prompt, resumePrompt, demonstrationPrompt, recordingMarkdown, saveRecording,
};
