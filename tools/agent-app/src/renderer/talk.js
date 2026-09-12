'use strict';

// 依頼にぶら下がる「ひとこと」（人と人のやり取り）の表示。会話画面と共有画面の**両方**が
// この 1 つを使う（端末ミラーと同じく、同じ部品を 2 つの置き場に載せる）。
//
// 見た目は会話画面の吹き出しと同じ作法: 相手は左の枠つき（`--code-bg`）、自分は右の色つき
// （`--accent-bg`）。AI の応答（`.answer-bubble` / `.msg.user`）と同じトークンで書く。
//
// 未読は「その依頼で最後に見た件数」との差。開いている間は見たものとして数える。
(function initTalk() {
  const seen = new Map();          // 依頼 id → 最後に見た件数

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function time(value) {
    const at = Date.parse(value || '');
    return Number.isFinite(at) ? new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  }

  function unread(id, talk) {
    const list = Array.isArray(talk) ? talk : [];
    return Math.max(0, list.length - (seen.get(String(id)) || 0));
  }

  function markRead(id, talk) {
    seen.set(String(id), Array.isArray(talk) ? talk.length : 0);
  }

  function forget(id) { seen.delete(String(id)); }

  // host … 吹き出しを入れる器。id … 依頼。me … 自分の参加者名。read … 見たものとして数えるか
  function render(host, { id = '', talk = [], me = '', read = true } = {}) {
    if (!host) return;
    const atBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 24;
    host.replaceChildren();
    if (!talk.length) {
      host.append(el('p', 'talk-empty', 'まだやり取りはありません'));
      return;
    }
    for (const item of talk) {
      const mine = item.who === me;
      const line = el('article', `talk-line ${mine ? 'mine' : 'them'}`);
      line.append(el('span', 'talk-who', [mine ? '自分' : item.who, time(item.at), item.pending ? '未達' : ''].filter(Boolean).join(' · ')));
      line.append(el('p', 'talk-bubble', item.text));
      host.append(line);
    }
    if (read) markRead(id, talk);
    if (atBottom) host.scrollTop = host.scrollHeight;
  }

  const api = { render, unread, markRead, forget };
  if (typeof window === 'undefined') module.exports = api;
  else window.Talk = api;
}());
