'use strict';

(function initTeachingFeature(global) {
  function createTeachingFeature(ctx) {
    const view = {
      loading: false,
      loaded: false,
      items: [],
      selected: '',
      session: null,
      creating: false,
      busy: false,
      requestId: '',
      progress: '',
      result: null,
      input: '',
      recordOpen: false,
      recording: false,
      recordSource: 'browser',
      recordTarget: '',
      approval: null,
      trial: null,
      trialResult: null,
      parameters: {},
      agent: '',
      model: '',
      consumedIntentId: '',
    };

    const e = ctx.escape;
    const root = () => ctx.root();
    const active = () => ctx.isActive();
    const statusLabel = (status) => ({
      draft: '下書き',
      'needs-trial': '試運転が必要',
      'awaiting-confirmation': '確認待ち',
      ready: '利用可能',
    })[status] || '下書き';
    const statusClass = (status) => status === 'ready' ? 'ok' : status === 'awaiting-confirmation' ? 'warn' : '';

    function reset() {
      Object.assign(view, {
        loading: false, loaded: false, items: [], selected: '', session: null, creating: false,
        busy: false, requestId: '', progress: '', result: null, input: '', recordOpen: false,
        recording: false, approval: null, trial: null, trialResult: null, parameters: {},
        agent: '', model: '', consumedIntentId: '',
      });
    }

    async function loadItems() {
      const taught = (await ctx.guard('タスク一覧', () => ctx.bridge.list(root()))) || [];
      const byMachine = new Map(taught.map((item) => [item.machine, item]));
      for (const machine of ctx.machines()) {
        if (byMachine.has(machine.machine)) continue;
        byMachine.set(machine.machine, {
          machine: machine.machine,
          title: machine.name,
          purpose: machine.description || '既存のタスクです。AIと内容を確認できます。',
          status: 'needs-trial',
          lastTrial: null,
          legacy: true,
        });
      }
      view.items = [...byMachine.values()].sort((a, b) => String(a.title).localeCompare(String(b.title), 'ja'));
    }

    async function activate() {
      if (!root()) return;
      view.loading = true;
      if (active()) ctx.refresh();
      await loadItems();
      view.loaded = true;
      view.loading = false;
      if (view.selected && !view.items.some((item) => item.machine === view.selected)) view.selected = '';
      if (active()) ctx.refresh();
    }

    async function select(machine) {
      view.selected = machine;
      view.creating = false;
      view.result = null;
      view.trialResult = null;
      view.session = await ctx.guard('教えた内容', () => ctx.bridge.read(root(), machine));
      const existing = ctx.machines().find((item) => item.machine === machine);
      if (view.session && existing && !view.session.title) {
        view.session.title = existing.name || machine;
        view.session.understanding.purpose = existing.description || existing.purpose || '';
      }
      if (view.session?.pendingRequest?.kind === 'questions') {
        view.result = { status: 'questions', questions: view.session.pendingRequest.questions || [] };
      } else if (view.session?.pendingRequest?.kind === 'demonstration') {
        view.result = { status: 'demonstration', demonstration: view.session.pendingRequest.demonstration };
      }
      ctx.refresh();
    }

    function create() {
      view.selected = '';
      view.session = null;
      view.creating = true;
      view.result = null;
      view.input = '';
      ctx.refresh();
    }

    async function startNew(options = {}) {
      const purpose = String(view.input || '').trim();
      if (!purpose || view.busy) return;
      const session = await ctx.guard('新しいタスク', () => ctx.bridge.create(root(), purpose, options));
      if (!session) return false;
      view.session = session;
      view.selected = session.machine;
      view.creating = false;
      view.input = '';
      await loadItems();
      await askAi('');
      return true;
    }

    async function startFromIntent(intent) {
      if (!intent || intent.root !== root() || !intent.id || intent.id === view.consumedIntentId) return false;
      view.consumedIntentId = intent.id;
      view.agent = String(intent.agent || '');
      view.model = String(intent.model || '');
      view.input = String(intent.purpose || '').trim();
      view.creating = true;
      ctx.refresh();
      const started = await startNew({ attachments: Array.isArray(intent.attachments) ? intent.attachments : [] });
      if (!started) {
        view.consumedIntentId = '';
        return false;
      }
      if (ctx.started) ctx.started(intent.id, view.session.machine);
      return true;
    }

    async function askAi(message) {
      if (!view.session || view.busy) return;
      view.busy = true;
      view.progress = 'AIが理解した内容を整理しています…';
      view.result = null;
      ctx.refresh();
      const started = await ctx.guard('AIとの相談', () => ctx.bridge.aiStart({
        root: root(), mode: 'teach', machine: view.session.machine, message,
        agent: view.agent || ctx.agent(), model: view.model || ctx.model(),
      }));
      if (!started) {
        view.busy = false;
        view.progress = '';
        ctx.refresh();
        return;
      }
      view.requestId = started.requestId;
      view.input = '';
    }

    async function startRecording() {
      const payload = { root: root(), source: view.recordSource };
      if (view.recordSource === 'windows') payload.app = view.recordTarget;
      else payload.url = view.recordTarget;
      const result = await ctx.guard('操作の見本', () => ctx.bridge.recordStart(payload));
      if (!result) return;
      view.recording = true;
      ctx.refresh();
    }

    async function stopRecording() {
      const payload = { root: root(), source: view.recordSource };
      if (view.recordSource === 'windows') payload.app = view.recordTarget;
      else payload.url = view.recordTarget;
      const recording = await ctx.guard('操作の見本', () => ctx.bridge.recordStop(payload));
      if (!recording) return;
      view.recording = false;
      view.recordOpen = false;
      view.session = await ctx.guard('見本の保存', () => ctx.bridge.addEvidence(
        root(), view.session.machine, recording, view.result?.demonstration?.instruction || '操作の見本',
      ));
      if (view.session) await askAi('');
    }

    function activeGeneration() {
      if (!view.session) return null;
      return view.session.generations.find((item) => item.id === view.session.activeGenerationId) || null;
    }

    async function startTrial(approved = false) {
      const generation = activeGeneration();
      if (!generation || view.trial) return;
      const trialId = view.approval?.trialId || `trial-${Date.now().toString(36)}`;
      const staged = await ctx.guard('試運転の準備', () => ctx.bridge.stage(
        root(), view.session.machine, generation.id, trialId, approved,
      ));
      if (!staged) return;
      if (staged.approvalRequired) {
        view.approval = staged;
        ctx.refresh();
        return;
      }
      view.approval = null;
      view.trialResult = null;
      const started = await ctx.guard('試運転', () => ctx.bridge.runStart({
        root: root(), machine: staged.trialMachine, mode: 'run',
        agent: ctx.agent(), model: ctx.model(), parameters: view.parameters,
      }));
      if (!started) {
        await ctx.bridge.cleanup(root(), staged.trialMachine).catch(() => {});
        return;
      }
      view.trial = {
        requestId: started.requestId,
        trialMachine: staged.trialMachine,
        generationId: generation.id,
        trialId: staged.trialId,
        lines: [],
      };
      ctx.refresh();
    }

    async function finishTrial(matches) {
      if (!view.trialResult || !view.session) return;
      const outcome = matches ? 'passed' : 'failed';
      view.session = await ctx.guard('試運転結果', () => ctx.bridge.recordTrial(root(), view.session.machine, {
        id: view.trialResult.trialId,
        generationId: view.trialResult.generationId,
        outcome,
        summary: matches ? '期待した結果を確認しました' : '期待した結果と異なりました',
        observed: [view.trialResult.summary],
      }));
      if (!view.session) return;
      if (matches) {
        view.session = await ctx.guard('利用可能にする', () => ctx.bridge.confirm(
          root(), view.session.machine, view.trialResult.generationId,
        ));
        await loadItems();
        view.trialResult = null;
        ctx.toast('このタスクを利用できるようにしました');
        ctx.changed(view.session.machine);
        ctx.refresh();
      } else {
        const detail = view.trialResult.summary;
        view.trialResult = null;
        await askAi(`試運転の結果が期待と異なりました。観測結果: ${detail}`);
      }
    }

    function messagesHtml() {
      const messages = view.session?.messages || [];
      return messages.map((item) => `<article class="teaching-message ${item.role}"><span>${item.role === 'user' ? 'あなた' : 'AI'}</span><p>${e(item.text)}</p></article>`).join('')
        || '<div class="blank compact"><p>変更したいことをAIへ伝えてください。</p></div>';
    }

    function responseCardHtml() {
      if (view.busy) return `<article class="teaching-card"><span class="status active">理解中</span><p>${e(view.progress)}</p></article>`;
      const result = view.result;
      if (!result) return '';
      if (result.status === 'questions') return result.questions.map((question) => `<article class="teaching-card question"><span class="eyebrow">確認したいこと</span><h3>${e(question.text)}</h3>${question.reason ? `<p>${e(question.reason)}</p>` : ''}${question.example ? `<small>例: ${e(question.example)}</small>` : ''}</article>`).join('');
      if (result.status === 'demonstration') return `<article class="teaching-card demonstration"><span class="eyebrow">操作を見せてください</span><h3>${e(result.demonstration.instruction)}</h3><p>${e(result.demonstration.reason)}</p><button type="button" data-teach-record-open>操作を見せる</button></article>`;
      return `<article class="teaching-card candidate"><span class="status">試運転が必要</span><h3>タスクの進め方を準備しました</h3><p>${e(result.summary || '代表的な入力で結果を確認してください。')}</p><button type="button" class="primary" data-teach-trial>試運転する</button></article>`;
    }

    function recordHtml() {
      if (!view.recordOpen) return '';
      return `<article class="teaching-card demonstration"><h3>操作の見本</h3><div class="grid2"><div class="field"><label>見せる画面</label><select data-teach-record-source ${view.recording ? 'disabled' : ''}><option value="browser" ${view.recordSource === 'browser' ? 'selected' : ''}>ブラウザ</option><option value="windows" ${view.recordSource === 'windows' ? 'selected' : ''}>Windowsアプリ</option></select></div><div class="field"><label>${view.recordSource === 'windows' ? 'アプリ名' : '開始URL'}</label><input data-teach-record-target value="${e(view.recordTarget)}" ${view.recording ? 'disabled' : ''}></div></div><div class="row">${view.recording ? '<button type="button" class="primary" data-teach-record-stop>見本を終了</button><span class="status active">記録中</span>' : '<button type="button" class="primary" data-teach-record-start>見本を開始</button><button type="button" data-teach-record-close>閉じる</button>'}</div></article>`;
    }

    function approvalHtml() {
      if (!view.approval) return '';
      const actions = view.approval.actions.map((item) => `<li><strong>${e(item.target || item.action)}</strong><span>${e(item.effect || '外部の状態を変更します')}</span></li>`).join('');
      return `<article class="teaching-card approval"><span class="status warn">承認が必要</span><h3>試運転で次の操作を行います</h3><ul>${actions}</ul><div class="row"><button type="button" class="primary" data-teach-approve>この試運転だけ許可</button><button type="button" data-teach-reject>今回は実行しない</button></div></article>`;
    }

    function trialHtml() {
      const generation = activeGeneration();
      if (!generation) return '';
      const variables = generation.jobSpec?.variables || [];
      const fields = variables.map((item) => `<div class="field"><label>${e(item.label || item.key)}</label><input data-teach-param="${e(item.key)}" value="${e(view.parameters[item.key] || '')}"></div>`).join('');
      const running = view.trial ? `<article class="teaching-card teaching-trial"><span class="status active">試運転中</span><h3>結果を確認しています</h3><pre>${e(view.trial.lines.join('\n') || '開始しています…')}</pre></article>` : '';
      const result = view.trialResult ? `<article class="teaching-card teaching-trial result"><span class="status ${view.trialResult.ok ? 'ok' : 'warn'}">試運転結果</span><h3>${view.trialResult.ok ? '実行が完了しました' : '実行を完了できませんでした'}</h3><p>${e(view.trialResult.summary)}</p><p>期待した結果になっていますか？</p><div class="row"><button type="button" class="primary" data-teach-match>期待どおり</button><button type="button" data-teach-mismatch>結果が違う</button></div></article>` : '';
      if (running || result) return `${running}${result}`;
      return `<article class="teaching-card teaching-trial"><span class="eyebrow">試運転</span><h3>代表的な入力で結果を確認</h3>${fields ? `<div class="teaching-parameters">${fields}</div>` : '<p>追加の入力はありません。</p>'}<button type="button" class="primary" data-teach-trial>試運転する</button></article>`;
    }

    function understandingHtml() {
      const u = view.session?.understanding || {};
      const rows = [
        ['目的', u.purpose],
        ['毎回変わる値', (u.variables || []).map((item) => item.label || item.key).join('、')],
        ['期待結果', (u.expectedResults || []).join('、')],
        ['重要操作', (u.importantActions || []).map((item) => item.effect || item.action).join('、')],
        ['まだ曖昧な点', (u.unknowns || []).join('、')],
      ];
      return `<aside class="teaching-understanding"><h3>AIの理解</h3>${rows.map(([label, value]) => `<div><span>${label}</span><p>${e(value || 'なし')}</p></div>`).join('')}</aside>`;
    }

    function workspaceHtml() {
      const session = view.session;
      const stage = session.status === 'ready' ? 4 : session.status === 'awaiting-confirmation' ? 3 : activeGeneration() ? 3 : session.evidence.length ? 2 : 1;
      const progress = ['目的を理解中', '方法を確認中', '試運転中', '利用可能'];
      const runnable = session.status === 'ready' || ctx.machines().some((item) => item.machine === session.machine);
      return `<div class="teaching-head"><div><span class="eyebrow">タスクを教える</span><h2>${e(session.title || session.machine)}</h2><span class="status ${statusClass(session.status)}">${e(statusLabel(session.status))}</span></div><div class="row">${runnable ? '<button type="button" class="primary" data-teach-run>実行する</button>' : ''}<button type="button" data-teach-edit>高度な編集</button></div></div><ol class="teaching-progress">${progress.map((label, index) => `<li class="${index < stage ? 'done' : index === stage - 1 ? 'current' : ''}">${e(label)}</li>`).join('')}</ol><div class="teaching-workspace"><section class="teaching-conversation" aria-label="AIとの会話"><div class="teaching-messages">${messagesHtml()}${responseCardHtml()}${recordHtml()}${approvalHtml()}${trialHtml()}</div><div class="teaching-composer"><textarea rows="3" data-teach-message placeholder="${view.result?.status === 'questions' ? '質問への回答を入力' : '変更したいことや補足を入力'}">${e(view.input)}</textarea><button type="button" class="primary" data-teach-send ${view.busy ? 'disabled' : ''}>変更を相談する</button></div></section>${understandingHtml()}</div>`;
    }

    function createHtml() {
      return `<div class="blank teaching-create"><span class="eyebrow">新しいタスクを教える</span><h2>何を自動化したいですか？</h2><p>工程や分岐はAIが考えます。普段の言葉で、ほしい結果を教えてください。</p><textarea rows="6" data-teach-purpose placeholder="例: 毎月、売上画面から前月分を取得してレポートを作りたい">${e(view.input)}</textarea><div class="row"><button type="button" class="primary" data-teach-create-start>AIに相談する</button><button type="button" data-teach-create-cancel>戻る</button></div></div>`;
    }

    function html() {
      if (view.loading) return '<div class="blank compact"><p>タスクを読み込んでいます…</p></div>';
      const list = view.items.map((item) => `<button type="button" class="execution-item ${item.machine === view.selected ? 'is-on' : ''}" data-teach-select="${e(item.machine)}"><strong>${e(item.title)}</strong><span>${e(statusLabel(item.status))}${item.purpose ? ` · ${e(item.purpose)}` : ''}</span></button>`).join('');
      const detail = view.creating ? createHtml() : view.session ? workspaceHtml() : '<div class="blank compact"><h2>タスクを選んでください</h2><p>新しいタスクは、目的を伝えるところから始められます。</p></div>';
      return `<div class="teaching-page"><header class="teaching-page-head"><div><h1>タスク</h1><p>AIに目的を伝え、必要なときだけ操作を見せて、結果で確認します。</p></div><button type="button" class="primary" data-teach-new>新しいタスクを教える</button></header><div class="execution-layout teaching-layout"><aside class="execution-list">${list || '<p class="muted small">まだタスクがありません。</p>'}</aside><section class="execution-detail">${detail}</section></div></div>`;
    }

    function bind(main) {
      const one = (selector, fn) => { const node = main.querySelector(selector); if (node) node.addEventListener('click', fn); };
      one('[data-teach-new]', create);
      one('[data-teach-create-cancel]', () => { view.creating = false; ctx.refresh(); });
      one('[data-teach-create-start]', () => startNew());
      for (const node of main.querySelectorAll('[data-teach-select]')) node.addEventListener('click', () => select(node.dataset.teachSelect));
      const purpose = main.querySelector('[data-teach-purpose]');
      if (purpose) purpose.addEventListener('input', () => { view.input = purpose.value; });
      const message = main.querySelector('[data-teach-message]');
      if (message) message.addEventListener('input', () => { view.input = message.value; });
      one('[data-teach-send]', () => askAi(view.input));
      one('[data-teach-record-open]', () => { view.recordOpen = true; ctx.refresh(); });
      one('[data-teach-record-close]', () => { view.recordOpen = false; ctx.refresh(); });
      const source = main.querySelector('[data-teach-record-source]');
      if (source) source.addEventListener('change', () => { view.recordSource = source.value; ctx.refresh(); });
      const target = main.querySelector('[data-teach-record-target]');
      if (target) target.addEventListener('input', () => { view.recordTarget = target.value; });
      one('[data-teach-record-start]', startRecording);
      one('[data-teach-record-stop]', stopRecording);
      for (const input of main.querySelectorAll('[data-teach-param]')) input.addEventListener('input', () => { view.parameters[input.dataset.teachParam] = input.value; });
      one('[data-teach-trial]', () => startTrial(false));
      one('[data-teach-approve]', () => startTrial(true));
      one('[data-teach-reject]', () => { view.approval = null; ctx.refresh(); });
      one('[data-teach-match]', () => finishTrial(true));
      one('[data-teach-mismatch]', () => finishTrial(false));
      one('[data-teach-edit]', () => ctx.edit(view.session.machine));
      one('[data-teach-run]', () => ctx.run(view.session.machine));
    }

    function onAiProgress(payload) {
      if (payload.mode !== 'teach' || (view.requestId && payload.requestId !== view.requestId)) return false;
      view.requestId = payload.requestId;
      view.progress = payload.message || 'AIが検討しています…';
      if (active()) ctx.refresh();
      return true;
    }

    function onAiResult(payload) {
      if (payload.mode !== 'teach' || (view.requestId && payload.requestId !== view.requestId)) return false;
      view.busy = false;
      view.progress = '';
      view.requestId = '';
      if (payload.ok && payload.result) {
        view.result = payload.result;
        if (payload.result.session) view.session = payload.result.session;
        loadItems().then(() => { if (active()) ctx.refresh(); });
      } else if (!payload.cancelled) {
        ctx.toast(payload.error || 'AIの提案を受け取れませんでした', true);
      }
      if (active()) ctx.refresh();
      return true;
    }

    function onRunLine(payload) {
      if (!view.trial || payload.requestId !== view.trial.requestId) return false;
      view.trial.lines.push(payload.line);
      if (view.trial.lines.length > 80) view.trial.lines.shift();
      if (active()) ctx.refresh();
      return true;
    }

    async function onRunExit(payload) {
      if (!view.trial || payload.requestId !== view.trial.requestId) return false;
      const current = view.trial;
      view.trial = null;
      await ctx.bridge.cleanup(root(), current.trialMachine).catch(() => {});
      view.trialResult = {
        ok: !!(payload.result && payload.result.ok),
        summary: payload.result?.error || payload.error || (payload.result?.ok ? '実行処理は完了しました。画面や成果を確認してください。' : '実行に失敗しました。'),
        generationId: current.generationId,
        trialId: current.trialId,
      };
      if (active()) ctx.refresh();
      return true;
    }

    return { html, bind, activate, select, create, startFromIntent, reset, rootChanged: reset, onAiProgress, onAiResult, onRunLine, onRunExit };
  }

  global.createTeachingFeature = createTeachingFeature;
})(window);
