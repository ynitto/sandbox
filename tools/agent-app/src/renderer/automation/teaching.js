'use strict';

// タスクを AI と作る・変える画面（共有ワークベンチ側）。
//
// 会話そのもの（tmux の端末ミラー・依頼の入力・見本の記録）は親の agent-app が持ち
// （renderer/taskTeaching.js）、ここは**どのタスクの会話を出すか**を決めて、その置き場
// （`<slot name="teaching">`）と見出しを描くだけにする。親へは `ctx.view({ … })` で
// 「いまこのタスクの会話を出している / 出していない」を伝える。
//
// 会話が出るのは 2 か所。どちらも枠（カード・ページ）はこちらが描き、中身は親が入れる。
//   作成 … 定義がまだ無いもの（新しいタスク・作成中の下書き）。`html()`
//   編集 … 定義があるタスクの「手順」タブで「編集」を押したとき。`editorSlotHtml()`
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
      announce(null);
    }

    // 一覧: 定義があるタスク（そのまま実行できる）と、定義がまだ無い下書き（作成の途中）。
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

    function cancelCreate() {
      if (!view.creating) return;
      view.creating = false;
    }

    function selectedItem() {
      return view.items.find((item) => item.machine === view.selected) || null;
    }

    // 親へ「いまどのタスクの会話を出しているか」を伝える。描くたびに呼ぶ（同じ内容なら親は何もしない）。
    let announced = false;
    function announce(detail) {
      announced = true;
      if (ctx.view) ctx.view(detail);
    }

    // 描画のたびに、会話の置き場（slot）を描いたかどうかを親へ伝え直す。
    function beginRender() { announced = false; }
    function endRender() { if (!announced) announce(null); }

    // 「手順」タブで編集に入ったときの置き場。枠（カード）は呼ぶ側が描く。
    function editorSlotHtml(machine) {
      const name = machine && (machine.name || machine.machine);
      announce({ root: root(), machine: String((machine && machine.machine) || ''), creating: false, editing: true, card: true, published: true, title: name || '', agent: ctx.editAgent ? ctx.editAgent() : '' });
      return '<slot name="teaching"></slot>';
    }

    // 定義がまだ無いもの（新しいタスク・作成の途中の下書き）の画面。
    function html() {
      if (view.loading) return '<div class="blank compact"><p>タスクを読み込んでいます…</p></div>';
      if (view.creating) {
        announce({ root: root(), machine: '', creating: true, editing: true, published: false, title: '' });
        return '<div class="blank teaching-create"><span class="eyebrow">新しいタスクを作成</span><h2>何を自動化したいですか？</h2><p>工程や分岐は AI が考えます。ほしい結果を書いてください。</p><slot name="teaching"></slot></div>';
      }
      const item = selectedItem();
      if (!item) {
        announce(null);
        return '<div class="blank compact"><h2>タスクを選んでください</h2><p>新しいタスクは、目的を伝えるところから始められます。</p></div>';
      }
      const present = item.view || presentOf(item.machine);
      announce({ root: root(), machine: item.machine, creating: false, editing: true, published: present.published, title: item.title });
      const done = present.published ? '<button type="button" data-teach-open-steps>手順を見る</button>' : '';
      return `<div class="teaching-page"><div class="teaching-head"><div><span class="eyebrow">作成中のタスク</span><h2>${e(item.title || item.machine)}</h2><span class="teaching-badges"><span class="status">${e(teachingStatusLabel(present.status))}</span></span></div><div class="row">${done}<button type="button" class="danger ghost" data-teach-delete>削除</button></div></div><slot name="teaching"></slot></div>`;
    }

    function bind(main) {
      const open = main.querySelector('[data-teach-open-steps]');
      if (open && ctx.edit) open.addEventListener('click', () => ctx.edit(view.selected));
      const remove = main.querySelector('[data-teach-delete]');
      if (remove) remove.addEventListener('click', async () => {
        const item = selectedItem();
        if (!item || !window.confirm(`「${item.title || item.machine}」を削除しますか？\n作成中の会話情報と操作の見本も削除されます。`)) return;
        const deleted = await ctx.guard('タスクの削除', () => ctx.bridge.remove(root(), item.machine));
        if (!deleted) return;
        view.selected = '';
        await loadItems();
        ctx.changed('tasks', '');
        ctx.toast('タスクを削除しました');
        ctx.refresh();
      });
    }

    // 実行詳細など他の画面が、そのタスクの状態（利用可能か）を聞くための口。
    function statusOf(machine) { return presentOf(machine); }

    return { html, editorSlotHtml, beginRender, endRender, bind, activate, select, create, cancelCreate, reset, rootChanged: reset, loadItems, statusOf, selected: () => view.selected, isCreating: () => view.creating };
  }

  global.createTeachingFeature = createTeachingFeature;
  global.presentTeachingStatus = presentTeachingStatus;
  global.teachingStatusLabel = teachingStatusLabel;
})(window);
