'use strict';

// タスク・ワークフローを AI と作る会話（tmux）。会話基盤（ipc）の上に載る薄い層で、
// 会話 1 つをタスクの保存名（kind: 'task'）またはワークフローの id（kind: 'workflow'）へ紐づけ、
// 最初の依頼（教示の固定文）を会話と同じ経路で送る。
//
// 会話の実行そのもの（tmux の起動・同時実行枠・応答中かどうか）は ipc が持っているので、
// ここでは次の 5 つだけを受け取る:
//   presentSession … 保存した会話を画面の形にする
//   appRoot        … 同梱スキル（statemachine-use）を探す土台
//   busy           … その会話がいま応答中か
//   queuedTurnIds  … 同時実行枠が持っている会話 ID（新しいセッションに差し替えてよいかの判定）
//   runTurn        … 1 ターン送る（同時実行枠を通す ipc の経路）

const fs = require('fs');
const path = require('path');
const store = require('./store');
const host = require('./host');
const settings = require('./settings');
const agentCli = require('./agentCli');
const { userData, requireRepo } = require('./paths');
const projectIpc = require('./projectIpc');
const teaching = require('./automation/teaching');
const machineStore = require('./automation/store');
const flowStore = require('./automation/flow-store');
const flowTeachingStore = require('./automation/flow-teaching-store');
const flowTeachingModel = require('./automation/flow-teaching-model');
const flowTeachingPrompt = require('./automation/flow-teaching-prompt');
const automationTools = require('./automation/tools');
const recordingBrowser = require('./automation/browser');

function create(deps) {
  function configurePendingSession(ud, id, cfg, p) {
    const session = store.readSession(ud, id);
    if (!p.policy || session.messages.length || session.modelSelection || deps.busy(id)
      || deps.queuedTurnIds(cfg.execution.maxConcurrent).includes(id)) return;
    const selected = settings.resolve(cfg, p);
    store.updateSession(ud, id, { ...selected, allocation: selected.allocation || '' });
  }
  // ---- タスクを AI と作る会話（tmux）。会話基盤をそのまま使い、kind: 'task' の会話をタスクに紐づける ----
  //
  // 手動実行の画面と同じく、作成・変更も tmux の端末ミラーの中で進める。CLI は会話と同じ
  // 定義・同じ起動方針で起こし、cwd はリポジトリ本体。最初の依頼（teaching.prompt）が
  // statemachine-use の作成モードと、見本の依頼の作法（@record 行）を伝える。
  // ブラウザの見本は、**この端末**（Windows ならその Windows 側）で Edge をリモートデバッグ付きで起こし、
  // 固定文で AI に知らせて AI 自身が CDP 越しに記録を取る（automation:teach:browser。固定文は renderer が
  // 会話の送信経路で送る）。ボタンは 1 つで、押すたびに「開く（準備）→ 記録開始 → 終了」と進み、段ごとに
  // 別の固定文（@recording open / start / stop）が渡る。Windows アプリの見本（winauto）はこの端末で取り、できた Markdown の所在を
  // WSL 表記に直して会話へ送る。

  function teachingTools() {
    return {
      browser: !!recordingBrowser.findBrowser({ resolvePath: (name) => agentCli.resolvePath(name) }),
      windows: process.platform === 'win32' && !!agentCli.resolvePath('winauto'),
    };
  }

  // 「ブラウザを開く」: Edge（無ければ Chrome）を記録専用プロファイルで、リモートデバッグ付きで起こす。
  // この時点ではまだ記録は始まらない（利用者がログインや画面の移動をする）。
  function launchTeachingBrowser(p) {
    return recordingBrowser.launchRecordingBrowser({
      url: p.url, profileDir: path.join(userData(), recordingBrowser.PROFILE_DIR),
      resolvePath: (name) => agentCli.resolvePath(name),
    });
  }

  // 「記録を始める」: 準備の間に利用者が移動した先を記録の起点として AI へ渡すため、いま開いている
  // ページを DevTools から読む。読めなくても記録は始められるので、失敗は url: '' で返す。
  function teachingBrowserPage() {
    return recordingBrowser.activePage();
  }

  function teachingSkillDir(repo, cfg) {
    const dir = automationTools.findSkillDir({ root: repo, configured: cfg.automationSkillDir, appRoot: deps.appRoot() });
    return dir ? host.toHostPath(dir) : '';
  }

  function taskConversationView(ud, repo, machine) {
    const summary = store.findTaskSession(ud, repo, machine);
    const session = summary ? deps.presentSession(store.readSession(ud, summary.id)) : null;
    return {
      machine, session, sidecar: teaching.load(repo, machine), published: machineStore.exists(repo, machine),
      tools: teachingTools(),
    };
  }

  function prepareTeaching(p) {
    const ud = userData();
    const repo = requireRepo(p.repo);
    const cfg = store.loadConfig(ud);
    const purpose = String(p.purpose || '').trim();
    const machine = String(p.machine || '').trim() || teaching.machineNameFor(purpose);
    machineStore.machineDir(repo, machine);            // 保存名の字種を検査する（不正なら投げる）
    const existing = machineStore.exists(repo, machine);
    let sidecar = teaching.load(repo, machine);
    if (!existing && !sidecar) {
      if (!purpose) throw new Error('教えたいタスクを入力してください');
      sidecar = teaching.save(repo, machine, { title: purpose.split(/\r?\n/)[0].slice(0, 80), purpose });
    }
    let summary = store.findTaskSession(ud, repo, machine);
    const replacing = !!summary && p.newSession;
    if (!summary) {
      const selected = settings.resolve(cfg, p.policy ? p : { policy: 'direct', cli: p.cli || cfg.execution.tiers.medium.cli, model: p.model });
      const created = store.createSession(ud, {
        repo, cli: selected.cli, model: selected.model, policy: selected.policy, tier: selected.tier, allocation: selected.allocation,
        readonly: false, autoApprove: p.autoApprove != null ? !!p.autoApprove : cfg.execution.defaultAutoApprove,
        transport: 'tmux', worktree: '', kind: 'task', task: { machine }, project: projectIpc.projectFor(repo, cfg),
      });
      summary = { id: created.id };
      sidecar = teaching.save(repo, machine, { ...(sidecar || { title: machine, purpose }), sessionId: created.id });
    } else if (sidecar && sidecar.sessionId !== summary.id) {
      sidecar = teaching.save(repo, machine, { ...sidecar, sessionId: summary.id });
    }
    if (replacing) {
      if (deps.queuedTurnIds(cfg.execution.maxConcurrent).includes(summary.id) || deps.busy(summary.id)) {
        throw new Error('応答の完了後に新しいセッションを作成してください');
      }
      const selected = p.policy ? settings.resolve(cfg, p) : null;
      const created = store.replaceEditingSession(ud, summary.id, {
        readonly: false, ...(selected ? { ...selected, allocation: selected.allocation || '' } : p.cli ? { cli: p.cli, model: p.model || '' } : {}),
        autoApprove: p.autoApprove != null ? !!p.autoApprove : store.readSession(ud, summary.id).autoApprove,
      });
      summary = { id: created.id };
      sidecar = teaching.save(repo, machine, { ...sidecar, sessionId: created.id });
    }
    // 既にある会話でも権限は画面の選択に合わせる（自動承認へ切り替えたら、次の依頼で CLI を起動し直す）
    configurePendingSession(ud, summary.id, cfg, p);
    if (p.autoApprove != null) store.updateSession(ud, summary.id, { autoApprove: !!p.autoApprove });
    const session = store.readSession(ud, summary.id);
    return { ud, repo, cfg, purpose, machine, existing, sidecar, session };
  }

  function prepareTeachingView(p) {
    const prepared = prepareTeaching(p);
    return { ...taskConversationView(prepared.ud, prepared.repo, prepared.machine), existing: prepared.existing };
  }

  async function startTeaching(p, send) {
    const { ud, repo, cfg, purpose, machine, existing, sidecar, session } = prepareTeaching(p);
    const busy = deps.busy(session.id);
    let started = false;
    // 再開時の説明は CLI の復元結果に応じて runTmux で省略する。初回依頼は必ず送る。
    if (!busy) {
      const common = { machine, purpose: sidecar ? sidecar.purpose : purpose, existing };
      const prompt = session.messages.length
        ? teaching.resumePrompt({ ...common, context: p.context })
        : teaching.prompt({ ...common, skillDir: teachingSkillDir(repo, cfg), tools: teachingTools() });
      const context = String(p.context || '').trim().slice(0, 1000);
      const result = await deps.runTurn(session.id, {
        prompt, policy: session.policy, cli: session.cli, model: session.model, readonly: false, autoApprove: session.autoApprove,
        skillMode: 'off', skills: [], attachments: [],
      }, send, {
        resumeContext: session.messages.length ? (context ? `今回の編集対象: ${context}` : '') : undefined,
      });
      started = result.started !== false;
    }
    return { ...taskConversationView(ud, repo, machine), existing, started };
  }

  // ---- ワークフローを AI と作る会話（タスクと同じ作り。kind: 'workflow'） ---------------------
  //
  // 下書き（.agents/workflows/.teaching/<id>.json）が会話 ID を覚え、AI は会話の中で
  // .agents/workflows/<id>.json を直接書く。画面はその 1 ファイルを読んで「候補の工程」を出す。

  function flowConversationView(ud, repo, id) {
    const summary = store.findWorkflowSession(ud, repo, id);
    const session = summary ? deps.presentSession(store.readSession(ud, summary.id)) : null;
    let workflow = null;
    try { workflow = flowStore.read(repo, id); } catch { workflow = null; }   // まだ書かれていない
    return { workflowId: id, session, sidecar: flowTeachingStore.load(repo, id), workflow, published: !!(workflow && !workflow.issues.some((item) => item.level === 'error')) };
  }

  function prepareFlowTeaching(p) {
    const ud = userData();
    const repo = requireRepo(p.repo);
    const cfg = store.loadConfig(ud);
    const purpose = String(p.purpose || '').trim();
    const id = String(p.workflowId || '').trim() || flowTeachingPrompt.workflowIdFor(purpose);
    let sidecar = flowTeachingStore.load(repo, id);
    let existing = true;
    try { flowStore.read(repo, id); } catch { existing = false; }
    if (!existing && !sidecar.title) {
      if (!purpose) throw new Error('教えたいワークフローを入力してください');
      sidecar = flowTeachingStore.save(repo, id, flowTeachingModel.createSession({ workflowId: id, title: purpose.split(/\r?\n/)[0].slice(0, 80), purpose }));
    }
    let summary = store.findWorkflowSession(ud, repo, id);
    const selected = p.policy ? settings.resolve(cfg, p) : null;
    const current = summary ? store.readSession(ud, summary.id) : null;
    // 編集画面で起動先を変えたら、旧 CLI の履歴や復元 ID を持たない会話へ切り替える。
    const changedSelection = !!(current && selected && (
      (selected.allocation === 'auto') !== (current.allocation === 'auto')
      || (selected.allocation !== 'auto' && (selected.cli !== current.cli || selected.model !== current.model))
    ));
    const replacing = !!summary && (p.newSession || changedSelection);
    if (!summary) {
      const initial = selected || settings.resolve(cfg, { policy: 'direct', cli: p.cli || cfg.execution.tiers.medium.cli, model: p.model });
      const created = store.createSession(ud, {
        repo, cli: initial.cli, model: initial.model, policy: initial.policy, tier: initial.tier, allocation: initial.allocation,
        readonly: false, autoApprove: p.autoApprove != null ? !!p.autoApprove : cfg.execution.defaultAutoApprove,
        transport: 'tmux', worktree: '', kind: 'workflow', workflow: { id }, project: projectIpc.projectFor(repo, cfg),
      });
      summary = { id: created.id };
    }
    if (replacing) {
      if (deps.queuedTurnIds(cfg.execution.maxConcurrent).includes(summary.id) || deps.busy(summary.id)) {
        throw new Error('応答の完了後に新しいセッションを作成してください');
      }
      const created = store.replaceEditingSession(ud, summary.id, {
        readonly: false, ...(selected ? { ...selected, allocation: selected.allocation || '' } : p.cli ? { cli: p.cli, model: p.model || '' } : {}),
        autoApprove: p.autoApprove != null ? !!p.autoApprove : store.readSession(ud, summary.id).autoApprove,
      });
      summary = { id: created.id };
    }
    if (sidecar.sessionId !== summary.id) sidecar = flowTeachingStore.save(repo, id, { ...sidecar, sessionId: summary.id });
    configurePendingSession(ud, summary.id, cfg, p);
    if (p.autoApprove != null) store.updateSession(ud, summary.id, { autoApprove: !!p.autoApprove });
    return { ud, repo, purpose, id, existing, sidecar, session: store.readSession(ud, summary.id) };
  }

  async function startFlowTeaching(p, send) {
    const { ud, repo, purpose, id, existing, sidecar, session } = prepareFlowTeaching(p);
    const busy = deps.busy(session.id);
    let started = false;
    if (!busy) {
      const common = { id, purpose: sidecar.understanding.purpose || purpose, existing };
      const prompt = session.messages.length
        ? flowTeachingPrompt.resumePrompt({ ...common, context: p.context })
        : flowTeachingPrompt.prompt(common);
      // 埋め込み CLI が開いていても編集開始の指示は必要。runTmux の再開省略を避ける。
      const result = await deps.runTurn(session.id, {
        prompt, policy: session.policy, cli: session.cli, model: session.model, readonly: false, autoApprove: session.autoApprove,
        skillMode: 'off', skills: [], attachments: [],
      }, send, {
        resumeContext: session.messages.length ? prompt : undefined,
      });
      started = result.started !== false;
    }
    return { ...flowConversationView(ud, repo, id), existing, started };
  }

  // AI が書いた定義を、この下書きの「候補」として取り込む（試運転と利用可能にする手順は今までどおり）。
  function adoptFlowDraft(p) {
    const repo = requireRepo(p.repo);
    const id = String(p.workflowId || '').trim();
    const read = flowStore.read(repo, id);
    if (read.issues.some((item) => item.level === 'error')) throw new Error('定義にまだ直すところがあります');
    const session = flowTeachingStore.load(repo, id);
    const active = session.generations.find((item) => item.id === session.activeGenerationId);
    if (active && active.digest === read.digest) return session;               // 変わっていない
    const next = flowTeachingModel.addGeneration(session, {
      id: `file-${read.digest.slice(0, 12)}`, summary: read.workflow.description || read.workflow.name,
      workflow: read.workflow, digest: read.digest,
    });
    return flowTeachingStore.save(repo, id, next);
  }

  // Windows アプリの見本を保存し、AI へ渡す本文を**返す**。送りはしない——本文は入力欄に入り、
  // 利用者が見たものの補足を足してから送る（ブラウザの「終了してAIへ渡す」と同じ扱い）。
  // 送る経路が会話の 1 本だけになるので、AI が応答中でもここで断る必要がない。
  function demonstrate(p) {
    const repo = requireRepo(p.repo);
    const machine = String(p.machine || '').trim();
    const saved = teaching.saveRecording(repo, machine, p.recording);
    const hostPath = host.toHostPath(saved.file);
    const prompt = teaching.demonstrationPrompt({
      machine, hostPath, source: saved.source, target: saved.target, steps: saved.steps,
      parameters: saved.parameters, requested: p.requested !== false,
    });
    return { file: saved.file, relative: saved.relative, hostPath, source: saved.source, steps: saved.steps, prompt };
  }

  return {
    teachingTools, launchTeachingBrowser, teachingBrowserPage, taskConversationView,
    prepareTeachingView, startTeaching, demonstrate,
    flowConversationView, prepareFlowTeaching, startFlowTeaching, adoptFlowDraft,
  };
}

module.exports = { create };
