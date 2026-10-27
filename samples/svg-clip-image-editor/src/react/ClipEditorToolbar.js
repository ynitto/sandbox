// 任意の既定のボタン列。使わなければ、useClipEditor の state と ref で自前のボタンを組める。
// 見た目は style.css の .sce-toolbar / .sce-button に任せる（クラス名で当て直せる）。

import React, { useCallback, useMemo, useRef, useState } from 'react';

const h = React.createElement;

export const DEFAULT_LABELS = Object.freeze({
  zoomOut: '縮小',
  zoomIn: '拡大',
  rotateLeft: '左に 90°',
  rotateRight: '右に 90°',
  fit: '枠に合わせる',
  undo: '元に戻す',
  redo: 'やり直す',
  reset: 'リセット',
});

/**
 * エディタとボタンをつなぐ。
 *   const editor = useClipEditor();
 *   <SvgClipImageEditor ref={editor.ref} onStateChange={editor.onStateChange} … />
 *   <ClipEditorToolbar editor={editor} />
 * state: { canUndo, canRedo, covered, moving }（読み込み前は null）
 */
export function useClipEditor() {
  const ref = useRef(null);
  const [state, setState] = useState(null);
  const onStateChange = useCallback((s) => setState(s), []);
  const call = useCallback((name, ...args) => ref.current?.[name]?.(...args), []);
  return useMemo(() => ({ ref, state, onStateChange, call }), [state, onStateChange, call]);
}

export function ClipEditorToolbar({ editor, labels, className }) {
  const l = { ...DEFAULT_LABELS, ...labels };
  const ready = Boolean(editor.state);
  const button = (text, title, onClick, disabled = false) =>
    h('button', { type: 'button', className: 'sce-button', title, 'aria-label': title, onClick, disabled: !ready || disabled }, text);
  return h(
    'div',
    { className: ['sce-toolbar', className].filter(Boolean).join(' '), role: 'toolbar' },
    button('−', l.zoomOut, () => editor.call('zoom', 1 / 1.1)),
    button('+', l.zoomIn, () => editor.call('zoom', 1.1)),
    button('⟲ 90°', l.rotateLeft, () => editor.call('rotate', -90)),
    button('⟳ 90°', l.rotateRight, () => editor.call('rotate', 90)),
    button(l.fit, l.fit, () => editor.call('fit')),
    h('span', { className: 'sce-toolbar-spacer' }),
    button('↶', l.undo, () => editor.call('undo'), !editor.state?.canUndo),
    button('↷', l.redo, () => editor.call('redo'), !editor.state?.canRedo),
    button(l.reset, l.reset, () => editor.call('reset')),
  );
}
