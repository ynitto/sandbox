import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { SvgClipImageEditor } from '../src/index.js';
import { SAMPLE } from './helpers.js';

test('サーバー描画では空の枠だけを出し、例外を出さない', () => {
  const html = renderToString(React.createElement(SvgClipImageEditor, { svg: SAMPLE }));
  assert.match(html, /class="sce-root"/);
  assert.doesNotMatch(html, /<image/);
});
