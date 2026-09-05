'use strict';

const MARKER = '<!-- agent-app-instructions -->';

function instructionBlock(instructions) {
  const source = instructions && typeof instructions === 'object' ? instructions : {};
  if (source.enabled === false) return '';
  const text = String(source.text || '').trim();
  const skills = (Array.isArray(source.skills) ? source.skills : [])
    .map((skill) => String(skill || '').trim()).filter(Boolean);
  if (!text && !skills.length) return '';
  const lines = [
    MARKER,
    '## 共通指示',
    '今回の依頼やリポジトリ固有の指示と競合する場合は、それらを優先してください。',
  ];
  if (text) lines.push('', text);
  if (skills.length) lines.push('', '推奨スキル:', ...skills.map((skill) => `- ${skill}`));
  return lines.join('\n');
}

function withInstructions(prompt, instructions) {
  const text = String(prompt || '');
  const block = instructionBlock(instructions);
  if (!block || text.includes(MARKER)) return text;
  return `${block}\n\n## 今回の依頼\n${text}`;
}

function skillCommand(value, prefix) {
  const name = String(value || '').trim().replace(/^[$/]+/, '');
  return name ? `${prefix || '/'}${name}` : '';
}

function planActions(actions, { skillCommandPrefix = '/' } = {}) {
  const skills = [];
  const commands = [];
  for (const action of Array.isArray(actions) ? actions : []) {
    if (!action || action.type === 'skill') {
      const command = skillCommand(action && action.value, skillCommandPrefix);
      if (command) skills.push(command);
    } else if (action.type === 'command' && String(action.value || '').trim()) {
      commands.push({
        command: String(action.value).trim(),
        onError: action.onError === 'fail' ? 'fail' : 'warn',
      });
    }
  }
  return { skillPrompt: skills.join('\n'), commands };
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
