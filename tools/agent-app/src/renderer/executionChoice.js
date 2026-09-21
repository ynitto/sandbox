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
      const modelChoiceField = document.createElement('label');
      modelChoiceField.textContent = 'モデル';
      const choice = document.createElement('select');
      choice.dataset.executionModel = '';
      modelChoiceField.append(choice);
      modelField.before(modelChoiceField);
      modelField.firstChild.textContent = 'モデル名';
      model.placeholder = 'モデル名';
      model.setAttribute('aria-label', 'モデル名');
      ui = { modeField, mode, note, agentField, modelField, modelChoiceField, choice, options, manual: '', custom: false };
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
      choice.onchange = () => {
        ui.custom = choice.value === '__custom__';
        model.value = ui.custom ? '' : choice.value;
        model.dispatchEvent(new Event('input', { bubbles: true }));
        model.dispatchEvent(new Event('change', { bubbles: true }));
        sync(agent, model, ui.options);
        if (ui.custom) model.focus();
      };
      agent.addEventListener('change', () => {
        if (agent.value !== 'auto') ui.manual = agent.value;
        ui.custom = false;
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
    ui.modelChoiceField.hidden = automatic;
    ui.choice.disabled = locked;
    agent.disabled = locked;
    for (const option of agent.options) if (option.value === 'auto') option.hidden = true;
    const models = [...new Set((options.models?.(agent.value) || []).filter(Boolean))];
    if (model.value && !models.includes(model.value)) ui.custom = true;
    ui.choice.replaceChildren(new Option('既定のモデル', ''));
    for (const name of models) ui.choice.add(new Option(name, name));
    ui.choice.add(new Option('モデル名を入力', '__custom__'));
    ui.choice.value = ui.custom ? '__custom__' : model.value;
    ui.modelField.hidden = automatic || !ui.custom;
    model.disabled = locked || automatic;
  }
  return { sync };
})();
