'use strict';

// 一貫性ゲート状態セクション（renderer.js consistencyGateHtml）の表示ロジック。
// main 側の結線判定は test/consistency-gate.test.js が受け持つ。ここは
// 「ペイロードをどうバッジ・導線に写すか」だけを見る。

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const RENDERER_JS = path.join(__dirname, '..', 'src', 'renderer', 'renderer.js');
const rendererSrc = fs.readFileSync(RENDERER_JS, 'utf8');

function grab(name) {
  const at = rendererSrc.indexOf(`function ${name}(`);
  assert.ok(at >= 0, `renderer.js に function ${name} が見つかりません`);
  let depth = 0;
  for (let i = rendererSrc.indexOf('{', at); i < rendererSrc.length; i++) {
    if (rendererSrc[i] === '{') depth++;
    else if (rendererSrc[i] === '}') {
      depth--;
      if (depth === 0) return rendererSrc.slice(at, i + 1);
    }
  }
  throw new Error(`function ${name} の閉じ括弧が見つかりません`);
}

const badges = (html, text) => (html.match(new RegExp(`class="badge (?:info|warn)">${text}<`, 'g')) || []).length;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// eslint-disable-next-line no-new-func
const consistencyGateHtml = new Function('esc', `${grab('consistencyGateHtml')}; return consistencyGateHtml;`)(esc);

// 完了条件そのもの: 識別子と文言が renderer.js に出ていること。
for (const token of ['regression_cmd', 'intake_cmd', '一貫性ゲート']) {
  assert.ok(rendererSrc.includes(token), `renderer.js に ${token} がありません`);
}

// ペイロードが無いプロジェクト（古い main と組み合わせた場合）は何も描かない。
assert.strictEqual(consistencyGateHtml(null), '');
assert.strictEqual(consistencyGateHtml({}), '');

// 両方結線: 結線済みバッジ 2 つ、コマンド表示あり、有効化導線は出さない。
const both = consistencyGateHtml({
  dir: '/ws/.agent-project',
  consistencyGate: {
    configFile: '/ws/.agent/agent-project.yaml',
    regressionWired: true,
    intakeWired: true,
    wired: true,
    regressionCmd: 'codd-gate verify --repos repos.json',
    intakeCmd: 'codd-gate tasks --debt',
  },
});
assert.ok(both.includes('一貫性ゲート'));
assert.ok(both.includes('regression_cmd') && both.includes('intake_cmd'));
assert.strictEqual(badges(both, '結線済み'), 2);
assert.strictEqual(badges(both, '有効'), 1, '全結線の見出しバッジは 有効');
assert.ok(!both.includes('未結線'));
assert.ok(both.includes('codd-gate verify --repos repos.json'));
assert.ok(both.includes('codd-gate tasks --debt'));
assert.ok(!both.includes('有効化'), '全結線なら有効化導線は不要');
assert.ok(!both.includes('data-gate-open'));

// intake_cmd だけ未結線: 未結線バッジと有効化導線が出る。書くのは intake_cmd の行だけで、
// regression_cmd の行も注入 CLI も出さない（README: intake_cmd に対応する注入 CLI は無い）。
const partial = consistencyGateHtml({
  dir: '/ws/.agent-project',
  consistencyGate: {
    configFile: '/ws/.agents/agent-project.yaml',
    regressionWired: true,
    intakeWired: false,
    wired: false,
    regressionCmd: 'codd-gate verify --base "$KIRO_BASE_REV"',
    intakeCmd: null,
  },
});
assert.strictEqual(badges(partial, '結線済み'), 1);
assert.strictEqual(badges(partial, '未結線'), 1);
assert.strictEqual(badges(partial, '一部のみ'), 1, '片方だけ結線の見出しバッジは 一部のみ');
assert.ok(partial.includes('有効化'));
assert.ok(partial.includes('data-gate-open="/ws/.agents/agent-project.yaml"'));
// <root> は p.dir で埋める。埋めないまま貼ると codd-gate が repos.json を開けず regression が常時 FAIL する。
assert.ok(partial.includes("intake_cmd: 'codd-gate tasks --debt --repos /ws/.agent-project/repos.json'"),
  '未結線の intake_cmd の行が実パス付きで提示されていない');
assert.ok(!partial.includes('&lt;root&gt;'), 'p.dir があるのにプレースホルダのまま出している');
assert.ok(!partial.includes('codd_gate_regression.py'),
  'regression_cmd は結線済みなのに注入 CLI を勧めている');
assert.ok(!/<pre[^>]*>[^]*regression_cmd:/.test(partial), '結線済みの行まで書けと言っている');

// regression_cmd だけ未結線 + 設定ファイルあり: 注入 CLI を実パス付きで出す。
const regressionOnly = consistencyGateHtml({
  dir: '/ws/.agent-project',
  consistencyGate: {
    configFile: '/ws/.agents/agent-project.yaml',
    regressionWired: false,
    intakeWired: true,
    wired: false,
    regressionCmd: null,
    intakeCmd: 'codd-gate tasks --debt',
  },
});
assert.ok(regressionOnly.includes('codd_gate_regression.py --config /ws/.agents/agent-project.yaml'));
assert.ok(regressionOnly.includes('--dry-run'), '書かずに試す --dry-run を案内していない');
// install.sh は codd_gate_*.py を zipapp 内へ同梱するだけなので、どこで打てば動くかを書かないと
// コピーしても No such file になる。
assert.ok(regressionOnly.includes('tools/agent-project/'), 'CLI をどこで実行するか示していない');
assert.ok(!regressionOnly.includes('注入 CLI は無い'), 'intake_cmd は結線済みなのに注意書きが出ている');

// 未結線だが値は入っている（regression_cmd は codd-gate 専用キーではない）。
// 「未結線＝何も設定されていない」と読ませないため、設定値そのものは見せる。
const otherCmd = consistencyGateHtml({
  dir: '/ws/.agent-project',
  consistencyGate: {
    configFile: '/ws/.agents/agent-project.yaml',
    regressionWired: false,
    intakeWired: false,
    wired: false,
    regressionCmd: 'make -s smoke',
    intakeCmd: null,
  },
});
assert.strictEqual(badges(otherCmd, '結線済み'), 0, 'codd-gate を指さないコマンドを結線済みと言わない');
assert.ok(otherCmd.includes('make -s smoke'), '設定されているコマンドを隠している');
assert.ok(otherCmd.includes('一貫性ゲートの検査ではありません'),
  '別コマンドが入っていることを説明していない');

// 設定ファイルはあるが両方未結線（ゲート未導入プロジェクトの初期表示）。
// 見出しは「未結線」。貼る 2 行・注入 CLI・注意書き・開くボタンが同時に出る唯一の経路。
const noneWired = consistencyGateHtml({
  dir: '/ws/.agent-project',
  consistencyGate: {
    configFile: '/ws/.agents/agent-project.yaml',
    regressionWired: false,
    intakeWired: false,
    wired: false,
    regressionCmd: null,
    intakeCmd: null,
  },
});
assert.strictEqual(badges(noneWired, '未結線'), 3, '行 2 つ + 見出しバッジ');
assert.ok(!noneWired.includes('一部のみ'), '一度も有効化していない状態を「一部のみ」と言わない');
assert.ok(noneWired.includes('まだ有効になっていない'));
assert.ok(/<pre[^>]*>[^]*regression_cmd:[^]*intake_cmd:/.test(noneWired), '貼る 2 行が揃っていない');
assert.ok(noneWired.includes('codd_gate_regression.py'));
assert.ok(noneWired.includes('注入 CLI は無い'));
assert.ok(noneWired.includes('data-gate-open="/ws/.agents/agent-project.yaml"'));

// 設定ファイル自体が無い: 開くボタンも注入 CLI も出さず（--config は既存ファイル必須）、
// 作成先は README と同じ .agents/agent-project.yaml を示す。
const noConfig = consistencyGateHtml({
  consistencyGate: {
    configFile: null, regressionWired: false, intakeWired: false, wired: false,
    regressionCmd: null, intakeCmd: null,
  },
});
assert.strictEqual(badges(noConfig, '未結線'), 3);
assert.ok(!noConfig.includes('data-gate-open'), '設定ファイルが無いのに開くボタンを出さない');
assert.ok(!noConfig.includes('codd_gate_regression.py'),
  '設定ファイルが無いのに注入 CLI を勧めている（--config は既存ファイルを指す必要がある）');
assert.ok(noConfig.includes('.agents/agent-project.yaml'));
// p.dir が無ければ README と同じプレースホルダに戻す（嘘のパスを出さない）。
assert.ok(noConfig.includes('&lt;root&gt;/repos.json'));

// README との文言一致。ここがズレると画面と README でどちらが正か判断できなくなる。
// 単体配布（agent-dashboard だけを取り出した場合）では README が無いのでスキップする。
const README = path.join(__dirname, '..', '..', 'agent-project', 'README.md');
if (fs.existsSync(README)) {
  const readme = fs.readFileSync(README, 'utf8');
  const quoted = (key) => {
    const m = readme.match(new RegExp('`(' + key + ": '[^`]*')`"));
    assert.ok(m, `README.md から ${key} の行を取り出せません（README 側の書式が変わった）`);
    return m[1];
  };
  assert.ok(noConfig.includes(esc(quoted('regression_cmd'))),
    'regression_cmd の行が README と一致しない');
  assert.ok(noConfig.includes(esc(quoted('intake_cmd'))),
    'intake_cmd の行が README と一致しない');
  assert.ok(readme.includes('python3 codd_gate_regression.py --config '),
    'README の注入 CLI 名が変わった（画面側の文言も合わせること）');
}

// コマンド文字列は必ず esc を通す（値は yaml 由来の外部入力）。
const xss = consistencyGateHtml({
  consistencyGate: {
    configFile: null, regressionWired: true, intakeWired: false,
    regressionCmd: '<img src=x onerror=alert(1)>', intakeCmd: null,
  },
});
assert.ok(!xss.includes('<img'), 'コマンド文字列が素通しされている');
assert.ok(xss.includes('&lt;img'));

console.log('consistency-gate-ui: ok');
