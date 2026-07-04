'use strict';

const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const { GitLabClient } = require('./gitlab');
const { runAgent, buildPrompt } = require('./agent');
const { exportToObsidian, exportContentToObsidian } = require('./obsidian');

function client() {
  return new GitLabClient(loadConfig().gitlab);
}

// すべてのハンドラを {ok, data|error} 形式に揃える
function handle(channel, fn) {
  ipcMain.handle(channel, async (_event, args) => {
    try {
      return { ok: true, data: await fn(args || {}) };
    } catch (err) {
      return { ok: false, error: err && err.message ? err.message : String(err) };
    }
  });
}

// 要約プロンプトへ渡す入力の上限。プロンプトが大きいほどエージェントが
// 遅くなるため、本文・コメント・変更ファイル一覧をここで切り詰める。
const PROMPT_LIMITS = {
  description: 4000, // 本文の最大文字数
  noteCount: 20, // 直近コメント数
  noteBody: 400, // コメント 1 件あたりの最大文字数
  changedFiles: 50, // 変更ファイル一覧の最大件数
};

function clip(text, max, suffix = '\n…（以下省略）') {
  const s = String(text || '');
  return s.length > max ? s.slice(0, max) + suffix : s;
}

function formatNotes(notes) {
  if (!notes.length) return '(コメントなし)';
  const recent = notes.slice(-PROMPT_LIMITS.noteCount);
  const omitted = notes.length - recent.length;
  const lines = recent.map(
    (n) => `- @${n.author} (${n.createdAt}):\n${indent(clip(n.body, PROMPT_LIMITS.noteBody, '…'))}`
  );
  if (omitted > 0) lines.unshift(`（古いコメント ${omitted} 件を省略）`);
  return lines.join('\n\n');
}

function indent(text) {
  return String(text)
    .split('\n')
    .map((l) => `  ${l}`)
    .join('\n');
}

async function buildSummaryPrompt(target) {
  const cfg = loadConfig();
  const detail = await client().getDetail(target);
  const it = detail.item;
  const files = detail.changedFiles.slice(0, PROMPT_LIMITS.changedFiles);
  const omittedFiles = detail.changedFiles.length - files.length;
  const changes = files.length
    ? `# 変更ファイル\n${files.map((f) => `- ${f}`).join('\n')}` +
      (omittedFiles > 0 ? `\n（ほか ${omittedFiles} 件）` : '')
    : '';
  const prompt = buildPrompt(cfg.agent.promptTemplate, {
    typeLabel: it.type === 'issue' ? 'イシュー' : 'マージリクエスト',
    title: it.title,
    url: it.url,
    state: it.state,
    labels: it.labels.join(', ') || '(なし)',
    description: clip(detail.description, PROMPT_LIMITS.description) || '(説明なし)',
    notes: formatNotes(detail.notes),
    changes,
  });
  return { prompt, detail };
}

function registerIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  handle('gitlab:currentUser', () => client().getCurrentUser());
  handle('gitlab:groups', ({ search }) => client().listGroups(search));
  handle('gitlab:projects', (args) => client().listProjects(args));
  handle('gitlab:labels', (args) => client().listLabels(args));
  handle('gitlab:search', (args) => client().searchCandidates(args));
  handle('gitlab:related', ({ target }) => client().listRelated(target));
  handle('gitlab:detail', ({ target }) => client().getDetail(target));

  handle('gitlab:mrStatus', ({ target }) => client().getMR(target));
  handle('gitlab:resolveUrl', ({ url }) => client().resolveUrl(url));

  handle('gitlab:comment', ({ target, body }) => client().addComment(target, body));
  handle('gitlab:deleteBranch', ({ projectId, branch }) =>
    client().deleteBranch(projectId, branch)
  );
  handle('gitlab:updateLabels', ({ target, add, remove }) =>
    client().updateLabels(target, { add, remove })
  );
  handle('gitlab:merge', ({ target }) => client().mergeMR(target));
  handle('gitlab:setState', ({ target, event }) => client().setState(target, event));

  handle('agent:summarize', async ({ target }) => {
    const cfg = loadConfig();
    const { prompt, detail } = await buildSummaryPrompt(target);
    const summary = await runAgent(cfg.agent, prompt);
    return { summary, detail };
  });

  handle('obsidian:export', async ({ target, summary }) => {
    const cfg = loadConfig();
    const detail = await client().getDetail(target);
    const file = exportToObsidian(cfg.obsidian, {
      detail,
      summary: summary || '',
      exportedAt: new Date().toISOString(),
    });
    if (cfg.obsidian.openAfterExport) {
      shell.openExternal(`obsidian://open?path=${encodeURIComponent(file)}`);
    }
    return { file };
  });

  // ペインのアクティブタブの内容（リーダー抽出テキスト / 要約）をそのまま書き出す
  handle('obsidian:exportContent', async (payload) => {
    const cfg = loadConfig();
    const file = exportContentToObsidian(cfg.obsidian, {
      ...payload,
      exportedAt: new Date().toISOString(),
    });
    if (cfg.obsidian.openAfterExport) {
      shell.openExternal(`obsidian://open?path=${encodeURIComponent(file)}`);
    }
    return { file };
  });

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });
}

module.exports = { registerIpcHandlers };
