'use strict';

const { ipcMain, shell } = require('electron');
const { loadConfig, saveConfig } = require('./config');
const { GitLabClient } = require('./gitlab');
const { runAgent, buildPrompt } = require('./agent');
const { exportToObsidian } = require('./obsidian');
const kiro = require('./kiro');

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

function formatNotes(notes) {
  if (!notes.length) return '(コメントなし)';
  return notes
    .map((n) => `- @${n.author} (${n.createdAt}):\n${indent(n.body)}`)
    .join('\n\n');
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
  const changes = detail.changedFiles.length
    ? `# 変更ファイル\n${detail.changedFiles.map((f) => `- ${f}`).join('\n')}`
    : '';
  const prompt = buildPrompt(cfg.agent.promptTemplate, {
    typeLabel: it.type === 'issue' ? 'イシュー' : 'マージリクエスト',
    title: it.title,
    url: it.url,
    state: it.state,
    labels: it.labels.join(', ') || '(なし)',
    description: detail.description || '(説明なし)',
    notes: formatNotes(detail.notes),
    changes,
  });
  return { prompt, detail };
}

function registerIpcHandlers() {
  handle('config:get', () => loadConfig());
  handle('config:save', ({ config }) => saveConfig(config));

  handle('gitlab:groups', ({ search }) => client().listGroups(search));
  handle('gitlab:projects', (args) => client().listProjects(args));
  handle('gitlab:labels', (args) => client().listLabels(args));
  handle('gitlab:search', (args) => client().searchCandidates(args));
  handle('gitlab:related', ({ target }) => client().listRelated(target));
  handle('gitlab:detail', ({ target }) => client().getDetail(target));

  handle('gitlab:comment', ({ target, body }) => client().addComment(target, body));
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

  // kiro-autonomous needs（判断待ち/検収待ち・MADR 互換 ADR）
  handle('kiro:needs:list', () => kiro.listNeeds(loadConfig().kiroAutonomous.root));
  handle('kiro:needs:read', ({ file }) =>
    kiro.readNeeds(loadConfig().kiroAutonomous.root, file)
  );
  handle('kiro:needs:feedback', ({ file, text }) =>
    kiro.submitFeedback(loadConfig().kiroAutonomous.root, file, text)
  );
  handle('kiro:needs:approve', ({ id, project, reason }) =>
    kiro.approveNeeds(loadConfig().kiroAutonomous, { id, project, reason })
  );

  handle('agent:summarizeNeeds', async ({ file }) => {
    const cfg = loadConfig();
    const n = kiro.readNeeds(cfg.kiroAutonomous.root, file);
    const prompt = buildPrompt(cfg.agent.needsPromptTemplate, {
      title: n.title || file,
      content: n.raw,
    });
    const summary = await runAgent(cfg.agent, prompt);
    return { summary };
  });

  handle('shell:openExternal', ({ url }) => {
    if (!/^https?:\/\//.test(url)) throw new Error(`外部で開けない URL です: ${url}`);
    return shell.openExternal(url);
  });
}

module.exports = { registerIpcHandlers };
