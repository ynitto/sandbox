'use strict';
// One agent/model control for conversation, task and workflow popovers.
window.ExecutionChoice = (() => {
  const controls = new WeakMap();
  function sync(agent, model, options = {}) {
    if (!agent || !model) return;
    let ui = controls.get(agent);
    if (!ui) {
      const agentField = agent.closest('label');
      const modelField = model.closest('label');
      const modeField = document.createElement('label');
      modeField.textContent = '選択方法';
      const mode = document.createElement('select');
      mode.dataset.executionMode = '';
      mode.add(new Option('自動選択', 'auto'));
      mode.add(new Option('手動指定', 'manual'));
      modeField.append(mode);
      agentField.before(modeField);
      const note = document.createElement('p');
      note.className = 'sub'; note.setAttribute('role', 'status');
      modeField.after(note);
      const suggestions = document.createElement('datalist');
      suggestions.id = `${model.id}-suggestions`;
      modelField.append(suggestions);
      model.setAttribute('list', suggestions.id);
      model.dataset.executionModel = '';
      model.placeholder = '既定のモデル';
      model.setAttribute('aria-label', 'モデル');
      ui = { modeField, mode, note, agentField, modelField, suggestions, options, manual: '' };
      controls.set(agent, ui);
      mode.onchange = () => {
        if (ui.options.changeMode) ui.options.changeMode(mode.value);
        else {
          if (mode.value === 'auto') { ui.manual = agent.value; agent.value = 'auto'; }
          else agent.value = ui.manual || [...agent.options].find(o => o.value !== 'auto' && !o.disabled)?.value || '';
          agent.dispatchEvent(new Event('change', { bubbles: true }));
        }
        sync(agent, model, ui.options);
      };
      agent.addEventListener('change', () => {
        if (agent.value !== 'auto') ui.manual = agent.value;
        model.value = '';
        model.dispatchEvent(new Event('input', { bubbles: true }));
        sync(agent, model, ui.options);
      }, true);
    }
    ui.options = options;
    const automatic = options.automatic ? options.automatic() : agent.value === 'auto';
    const locked = options.locked ? options.locked() : agent.disabled;
    ui.mode.value = automatic ? 'auto' : 'manual';
    ui.mode.disabled = locked;
    ui.modeField.hidden = !!options.shared;
    ui.note.textContent = locked ? '実行先の変更は新しい会話から適用できます。' : '依頼内容に応じてエージェントとモデルを選びます。';
    ui.note.hidden = options.shared || (!automatic && !locked);
    ui.agentField.hidden = automatic;
    agent.disabled = locked;
    for (const option of agent.options) if (option.value === 'auto') option.hidden = true;
    const models = [...new Set((options.models?.(agent.value) || []).filter(Boolean))];
    ui.suggestions.replaceChildren(...models.map(name => new Option(name, name)));
    ui.modelField.hidden = automatic;
    model.disabled = locked || automatic;
  }
  return { sync };
})();
