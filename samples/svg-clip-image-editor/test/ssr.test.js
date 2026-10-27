import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { ClipEditorToolbar, SvgClipImageEditor } from '../src/index.js';
import { SAMPLE } from './helpers.js';

const h = React.createElement;

test('サーバー描画では空の枠だけを出し、例外を出さない', () => {
  const html = renderToString(h(SvgClipImageEditor, { svg: SAMPLE, className: 'mine' }));
  assert.match(html, /class="sce-placeholder mine"/);
  assert.doesNotMatch(html, /<image/);
});

test('描画面はボタンを描かない。ボタン列は別の部品で、読み込み前は押せない', () => {
  assert.doesNotMatch(renderToString(h(SvgClipImageEditor, { svg: SAMPLE })), /<button/);
  const editor = { state: null, call: () => {} };
  const html = renderToString(h(ClipEditorToolbar, { editor, labels: { fit: 'Fit' } }));
  assert.match(html, /class="sce-toolbar"/);
  assert.match(html, />Fit</);
  assert.equal((html.match(/disabled=""/g) || []).length, 8);
});

test('インラインの style に色を書かない（見た目は style.css とクラス名に任せる）', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../src/react/SvgClipImageEditor.js', import.meta.url), 'utf8');
  const toolbar = await readFile(new URL('../src/react/ClipEditorToolbar.js', import.meta.url), 'utf8');
  for (const text of [src, toolbar]) {
    assert.doesNotMatch(text, /#[0-9a-fA-F]{3,6}\b/, '16 進の色を直に書いている');
    assert.doesNotMatch(text, /\b(background|color|borderRadius|boxShadow|padding)\s*:/, 'インラインに見た目のスタイルがある');
  }
});
