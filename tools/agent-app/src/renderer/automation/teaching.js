'use strict';

// タスクを AI と作る・変える画面（共有ワークベンチ側）。
//
// 会話そのもの（tmux の端末ミラー・依頼の入力・見本の記録）は親の agent-app が持ち
// （renderer/taskTeaching.js）、ここは**どのタスクの会話を出すか**を決めて、その置き場
// （`<slot name="teaching">`）と見出しを描くだけにする。親へは `ctx.view({ … })` で
// 「いまこのタスクの会話を出している / 出していない」を伝える。
//
// 状態語は 2 つ: 定義（workflow.yaml）があれば「利用可能」、無ければ「下書き」。
(function initTeachingFeature(global) {
  const STATUS_LABELS = { draft: '下書き', ready: '利用可能' };

  // main の teaching.presentStatus と同じ判定。定義があるタスクは会話の途中でも「利用可能」。
  function presentTeachingStatus({ published = false } = {}) {
    return published
      ? { status: 'ready', runnable: true, published: true }
      : { status: 'draft', runnable: false, published: false };
  }

  function teachingStatusLabel(status) { return STATUS_LABELS[status] || '下書き'; }

  function createTeachingFeature(ctx) {
    const view = { loading: false, loaded: false, items: [], selected: '', creating: false };
    const e = ctx.escape;
    const root = () => ctx.root();
    const active = () => ctx.isActive();
    const published = (machine) => ctx.machines().some((item) => item.machine === machine);
    const presentOf = (machine) => presentTeachingStatus({ published: published(machine) });

    function reset() {
      Object.assign(view, { loading: false, loaded: false, items: [], selected: '', creating: false });
      if (ctx.view) ctx.view(null);
    }

    // 一覧: 定義があるタスク（そのまま実行できる）と、定義がまだ無い下書き（会話の途中）。
    async function loadItems() {
      const drafts = (await ctx.guard('タスク一覧', () => ctx.bridge.list(root()))) || [];
      const byMachine = new Map(drafts.map((item) => [item.machine, { ...item, published: !!item.published || published(item.machine) }]));
      for (const machine of ctx.machines()) {
        if (byMachine.has(machine.machine)) continue;
        byMachine.set(machine.machine, { machine: machine.machine, title: machine.name, purpose: machine.description || '', published: true });
      }
      view.items = [...byMachine.values()]
        .map((item) => ({ ...item, view: presentTeachingStatus({ published: item.published }) }))
        .sort((a, b) => String(a.title).localeCompare(String(b.title), 'ja'));
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
      view.selected = String(machine || '');
      view.creating = false;
      if (!view.items.some((item) => item.machine === view.selected)) await loadItems();
      ctx.refresh();
    }

    function create() {
      view.selected = '';
      view.creating = true;
      ctx.refresh();
    }

    function selectedItem() {
      return view.items.find((item) => item.machine === view.selected) || null;
    }

    // 親へ「いま出している会話」を伝える。描くたびに呼ぶ（親は同じ内容なら何もしない）。
    function announce(detail) {
      if (ctx.view) ctx.view(detail);
    }

    // タスク詳細のタブ（定義があるタスク）に入れる中身。見出し + 親の会話の置き場。
    function detailHtml() {
      const machine = view.selected;
      announce({ root: root(), machine, creating: false, published: published(machine), title: (selectedItem() || {}).title || machine });
      // タブ名（AI相談）と、その上のタスク名で足りる。ここに見出しや説明を足さない。
      return '<slot name="teaching"></slot>';
    }

    // 定義がまだ無い下書き・新しいタスク（タスク詳細のタブが無いとき）の画面。
    function html() {
      if (view.loading) return '<div class="blank compact"><p>タスクを読み込んでいます…</p></div>';
      if (view.creating) {
        announce({ root: root(), machine: '', creating: true, published: false, title: '' });
        return '<div class="teaching-page"><div class="teaching-head"><div><span class="eyebrow">新しいタスク</span><h2>何を自動化したいですか？</h2></div></div><slot name="teaching"></slot></div>';
      }
      const item = selectedItem();
      if (!item) {
        announce(null);
        return '<div class="blank compact"><h2>タスクを選んでください</h2><p>新しいタスクは、目的を伝えるところから始められます。</p></div>';
      }
      const present = item.view || presentOf(item.machine);
      announce({ root: root(), machine: item.machine, creating: false, published: present.published, title: item.title });
      return `<div class="teaching-page"><div class="teaching-head"><div><h2>${e(item.title || item.machine)}</h2><span class="teaching-badges"><span class="status">${e(teachingStatusLabel(present.status))}</span></span></div></div><slot name="teaching"></slot></div>`;
    }

    function bind() { /* 操作は親（agent-app）の会話面が持つ */ }

    // 実行詳細など他の画面が、そのタスクの状態（利用可能か）を聞くための口。
    function statusOf(machine) { return presentOf(machine); }

    return { html, detailHtml, bind, activate, select, create, reset, rootChanged: reset, loadItems, statusOf, selected: () => view.selected, isCreating: () => view.creating };
  }

  global.createTeachingFeature = createTeachingFeature;
  global.presentTeachingStatus = presentTeachingStatus;
  global.teachingStatusLabel = teachingStatusLabel;
})(window);
