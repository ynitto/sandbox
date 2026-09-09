'use strict';

const forkProtocol = require('../renderer/forkProtocol');

const MARKER = '<!-- agent-app-instructions -->';

// fork … { repos, current }。別のリポジトリへの分岐（@fork 行）の作法を添えるときだけ渡す
//        （instructions.forkEnabled が効いていて、会話の種類が「会話」のとき）。
function instructionBlock(instructions, { fork = null } = {}) {
  const source = instructions && typeof instructions === 'object' ? instructions : {};
  if (source.enabled === false) return '';
  const text = String(source.text || '').trim();
  const forkText = fork && source.forkEnabled !== false ? forkProtocol.instruction(fork) : '';
  if (!text && !forkText) return '';
  const lines = [
    MARKER,
    '## 共通指示',
    '今回の依頼やリポジトリ固有の指示と競合する場合は、それらを優先してください。',
  ];
  if (text) lines.push('', text);
  if (forkText) lines.push('', forkText);
  return lines.join('\n');
}

function withInstructions(prompt, instructions, options = {}) {
  const text = String(prompt || '');
  const block = instructionBlock(instructions, options);
  if (!block || text.includes(MARKER)) return text;
  return `${block}\n\n## 今回の依頼\n${text}`;
}

function skillCommand(value, prefix) {
  const name = String(value || '').trim().replace(/^[$/]+/, '');
  return name ? `${prefix || '/'}${name}` : '';
}

function planActions(actions, { skillCommandPrefix = '/', slashNative = true, availableSkills = [] } = {}) {
  const skills = [];
  const commands = [];
  const warnings = [];
  const available = new Set(Array.isArray(availableSkills) ? availableSkills : []);
  for (const action of Array.isArray(actions) ? actions : []) {
    if (!action || action.type === 'skill') {
      const command = skillCommand(action && action.value, skillCommandPrefix);
      if (!command) continue;
      const name = String(action.value || '').trim().replace(/^[$/]+/, '').split(/\s+/, 1)[0];
      if (!slashNative && !available.has(name)) {
        warnings.push(`${name} は利用可能なスキルではないため、このエージェントではスキップしました`);
        continue;
      }
      skills.push({ command, name, onError: action.onError === 'fail' ? 'fail' : 'warn' });
    } else if (action.type === 'command' && String(action.value || '').trim()) {
      commands.push({
        command: String(action.value).trim(),
        onError: action.onError === 'fail' ? 'fail' : 'warn',
      });
    }
  }
  return { skills, commands, warning: warnings.join('\n') };
}

async function runCommands(commands, run, { eachMs = 60000, totalMs = 120000 } = {}) {
  const startedAt = Date.now();
  const information = [];
  const warnings = [];
  for (const item of Array.isArray(commands) ? commands : []) {
    const remaining = totalMs - (Date.now() - startedAt);
    if (remaining <= 0) {
      const error = new Error('起動時アクション全体がタイムアウトしました');
      error.code = 'STARTUP_ACTION_FAILED';
      error.information = information;
      throw error;
    }
    const result = await run(item.command, Math.min(eachMs, remaining));
    const ok = !!(result && result.ok);
    information.push({
      type: 'command', title: item.command, status: ok ? 'success' : 'error',
      detail: String((result && (result.output || result.error)) || '').trim().slice(0, 4000),
    });
    if (ok) continue;
    const message = `起動時アクションに失敗しました: ${item.command}`;
    if (item.onError === 'fail') {
      const error = new Error(message);
      error.code = 'STARTUP_ACTION_FAILED';
      error.information = information;
      throw error;
    }
    warnings.push(message);
  }
  return { information, warning: warnings.join('\n') };
}

module.exports = { MARKER, instructionBlock, withInstructions, skillCommand, planActions, runCommands };
