'use strict';

// 「公開」= 定型化したもの（スキル・タスク・ワークフロー）を公開先リポジトリへ push して
// 人に渡すこと。LAN の参加者に依頼やセッションを見せる「共有」（share.js）とは別物で、
// 画面の言葉も分けてある。
//
// タスクの画面（automation/renderer.js）とワークフローの画面（automation/flow.js）が
// 同じ札とカードを出すので、作りはここ 1 か所に置く。スキルは設定 > スキル（skills.js）が
// 一覧の行に同じ状態を出す。
//
// 形は借りもの: 札は `.status`、カードは `.execution-card` + `.execution-card-head`
// （`<h3>` と 1 行の `<p>`、右に操作）。タスクの「定期実行」と同じ。
(function initPublish() {
  const cache = new Map();   // repo|種別|名前 → main が返した状態
  const pending = new Set();
  const listeners = [];
  let notify = () => {};

  const esc = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function keyOf(repo, kind, name) { return `${repo}|${kind}|${name}`; }

  function changed() { for (const fn of listeners) { try { fn(); } catch { /* 画面は止めない */ } } }

  // 読めていなければ裏で読みに行き、返事が来たら描き直してもらう。
  function state(repo, kind, name) {
    if (!repo || !name) return null;
    const key = keyOf(repo, kind, name);
    if (cache.has(key)) return cache.get(key);
    if (pending.has(key)) return null;
    pending.add(key);
    window.api.publish.state(repo, kind, name)
      .then((value) => { cache.set(key, value); })
      // 読めなかったものも覚える。覚えずに描き直すと、同じ問い合わせを延々と繰り返す。
      .catch(() => { cache.set(key, null); })
      .finally(() => { pending.delete(key); changed(); });
    return null;
  }

  function forget(repo, kind, name) { cache.delete(keyOf(repo, kind, name)); }

  // 名前の右に出す札。公開先が未設定のときと、出したものと同じときは何も出さない。
  function badgeHtml(repo, kind, name) {
    const info = state(repo, kind, name);
    if (!info || !info.configured) return '';
    if (info.status === 'unpublished') return '<span class="status warn">未公開</span>';
    if (info.status === 'updated') return '<span class="status warn">未公開の変更</span>';
    return '';
  }

  function detail(info) {
    if (info.improving) return `改善案 ${info.improveBranch} を出しました。取り込みを待っています`;
    if (info.status === 'unpublished') return 'まだ公開先へ出していません';
    if (info.status === 'updated') return `公開したあとで変更しています · 前回 ${info.branch}`;
    return `${info.branch} に公開済み`;
  }

  function button(info, action, cls, text) {
    return `<button type="button" class="${cls}" data-publish-action="${action}" data-publish-kind="${esc(info.kind)}" data-publish-name="${esc(info.name)}">${text}</button>`;
  }

  // 1 枚のカード。押せる操作が何も無いときは出さない（説明だけの面を残さない）。
  function cardHtml(repo, kind, name) {
    const info = state(repo, kind, name);
    if (!info || !info.configured || info.status === 'missing') return '';
    // 主ボタンは 1 つの面に 1 つだけ。タスクとワークフローの主ボタンは「実行」なので、
    // 公開の操作は普通のボタンで出す。
    const actions = [
      info.canPublish ? button(info, 'submit', '', info.status === 'updated' ? '公開し直す' : '公開する') : '',
      info.canImprove ? button(info, 'improve', '', '改善案を出す') : '',
    ].filter(Boolean).join('');
    if (!actions && info.status === 'published' && !info.improving) return '';
    return `<section class="execution-card"><div class="execution-card-head"><div><h3>公開</h3><p>${esc(detail(info))}</p></div>${actions ? `<div class="row">${actions}</div>` : ''}</div></section>`;
  }

  async function act(action, repo, kind, name, target) {
    const before = target ? target.textContent : '';
    if (target) { target.disabled = true; target.textContent = '出しています…'; }
    try {
      const result = action === 'submit'
        ? await window.api.publish.submit({ repo, kind, name })
        : await window.api.publish.improve({ repo, kind, name });
      if (result.skipped) {
        notify(result.skipped === 'no-share-repo'
          ? '公開先リポジトリを設定 > スキルで入れてください'
          : `出せませんでした（${result.error || result.skipped}）`, 'error');
      } else notify(`${result.branch} に出しました`, 'info');
    } catch (error) {
      notify(error.message, 'error');
    } finally {
      if (target) { target.disabled = false; target.textContent = before; }
      forget(repo, kind, name);
      changed();
    }
  }

  // 影の中（ワークベンチ）から上がってくる押下も拾えるよう、経路の先頭を見る。
  function handle(event, repo) {
    const origin = (event.composedPath && event.composedPath()[0]) || event.target;
    const target = origin && origin.closest ? origin.closest('[data-publish-action]') : null;
    if (!target) return false;
    event.preventDefault();
    act(target.dataset.publishAction, repo, target.dataset.publishKind, target.dataset.publishName, target);
    return true;
  }

  function configure(options = {}) {
    if (typeof options.notify === 'function') notify = options.notify;
    if (typeof options.onChange === 'function') listeners.push(options.onChange);
  }

  window.Publish = { configure, state, badgeHtml, cardHtml, handle, forget, changed };
}());
