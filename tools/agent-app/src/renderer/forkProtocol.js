'use strict';

// 会話を別のリポジトリへ分岐する「約束事」。main（依頼文に作法を添え、分岐先の依頼文を組む側）と
// renderer（AI の返答から分岐の依頼を拾い、ボタンで分岐する側）の両方が同じ 1 か所を読む。
// Node からも読める純粋なモジュール（teachingProtocol.js と同じ作り）。
//
//   分岐の依頼 … AI が、いま開いている作業フォルダの外にある別のローカルフォルダへ書き込む必要が
//                 出たとき、返答の中に次の 1 行を単独で書き、その下に依頼の本文を続ける。
//                 @fork <フォルダの絶対パス>
//                 <そのフォルダで行う作業の依頼（自己完結した文章）>
//                 本文は次の @fork 行か返答の終わりまで。1 つの返答に複数書いてもよい。
//   分岐        … 利用者が回答の下のボタンを押すと、このアプリがそのフォルダ（登録済みリポジトリ）を
//                 作業フォルダとする新しい会話を作り、元の会話の所在を添えた依頼文を最初のターンとして送る。
//                 分岐先は CLI の文脈を引き継がない（cwd が違う）ので、本文は自己完結している必要がある。
(function exposeForkProtocol() {
  const MARKER = '@fork';
  const LINE_RE = /^\s*(?:[>*\-•]\s*)?@fork(?:\s*[:：]?\s+(.+?))?\s*$/i;
  const MAX_PROMPT_CHARS = 20000;

  function cleanFolder(raw) {
    return String(raw || '').trim().replace(/^[`"'「<]+|[`"'」>]+$/g, '').trim();
  }

  // 返答本文から分岐の依頼をすべて拾う。無ければ空配列。
  //   [{ folder, prompt }] … folder は書かれたままのパス（登録済みかはここでは見ない）
  function parseForkRequests(text) {
    const lines = String(text || '').split(/\r?\n/);
    const found = [];
    let current = null;
    const close = () => {
      if (!current) return;
      const body = current.lines.join('\n').replace(/^\s*```[\w-]*\s*$/gm, '').trim();
      if (current.folder) found.push({ folder: current.folder, prompt: body.slice(0, MAX_PROMPT_CHARS) });
      current = null;
    };
    for (const line of lines) {
      const m = LINE_RE.exec(line);
      if (m) { close(); current = { folder: cleanFolder(m[1]), lines: [] }; continue; }
      if (current) current.lines.push(line);
    }
    close();
    return found;
  }

  // 返答本文から分岐の依頼を 1 つ拾う（最初の 1 件）。無ければ null。
  function parseForkRequest(text) {
    return parseForkRequests(text)[0] || null;
  }

  // 共通指示に添える作法。分岐先として選べるのは登録済みのリポジトリだけなので、その一覧を示す。
  function instruction({ repos = [], current = '' } = {}) {
    const others = (Array.isArray(repos) ? repos : []).map((r) => String(r || '')).filter((r) => r && r !== current);
    const lines = [
      '## 別のフォルダへの書き込み',
      `いまの作業フォルダの外にある別のローカルフォルダへ書き込む必要が出たときは、自分では書き込まず、返答に \`${MARKER} <フォルダの絶対パス>\` の 1 行を単独で書き、その下にそのフォルダで行う作業の依頼を自己完結した文章で続けてください。利用者が確認すると、そのフォルダを作業フォルダとする別の会話が始まり、その依頼が最初に送られます。分岐先はこの会話の文脈を持ちません。`,
    ];
    if (others.length) lines.push(`分岐先に選べるフォルダ: ${others.join(' 、 ')}`);
    return lines.join('\n');
  }

  // 分岐先の最初のターンに送る依頼文。元の会話の所在を 1 行添える。
  function forkPrompt({ originRepo = '', originTitle = '', prompt = '' } = {}) {
    const repoName = String(originRepo || '').split(/[\\/]/).filter(Boolean).pop() || '';
    const title = String(originTitle || '').trim();
    const where = repoName ? `${repoName} の会話${title ? `「${title}」` : ''}` : (title ? `会話「${title}」` : '');
    const head = where ? `${where}からの依頼です。元の会話の作業フォルダ: ${originRepo}` : '別の会話からの依頼です。';
    return `${head}\n\n${String(prompt || '').trim()}`.trim();
  }

  const protocol = { MARKER, MAX_PROMPT_CHARS, parseForkRequests, parseForkRequest, instruction, forkPrompt };
  if (typeof window === 'undefined') module.exports = protocol;
  else window.ForkProtocol = protocol;
}());
