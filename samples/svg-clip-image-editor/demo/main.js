// デモ: 切り替えの入口・動きの定数・ボタン列・保存はコンポーネントの外に置く。
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ClipEditorToolbar, DEFAULT_MOTION, SvgClipImageEditor, useClipEditor } from '../src/index.js';
import { SAMPLE_SVG } from './sample.js';

const h = React.createElement;
const ZERO = Object.fromEntries(Object.keys(DEFAULT_MOTION).map((k) => [k, 0]));

function Demo() {
  const editor = useClipEditor();
  const [slots, setSlots] = useState([]);
  const [slot, setSlot] = useState('hero');
  const [motionMode, setMotionMode] = useState('default');
  const [rotationCover, setRotationCover] = useState('rubber');
  const [output, setOutput] = useState(SAMPLE_SVG);
  const [log, setLog] = useState('');

  return h(
    'main',
    { className: 'page' },
    h('section', { className: 'card' },
      h('div', { className: 'row' },
        h('label', null, '画像 ',
          h('select', { value: slot, onChange: (e) => setSlot(e.target.value), 'data-testid': 'slot' },
            slots.map((s) => h('option', { key: s.name, value: s.name }, s.name)))),
        h('label', null, '動き ',
          h('select', { value: motionMode, onChange: (e) => setMotionMode(e.target.value), 'data-testid': 'motion' },
            h('option', { value: 'default' }, '既定（ゴム・慣性あり）'),
            h('option', { value: 'zero' }, 'すべて 0（端で止まる）'))),
        h('label', null, '回転 ',
          h('select', { value: rotationCover, onChange: (e) => setRotationCover(e.target.value), 'data-testid': 'rotation' },
            h('option', { value: 'rubber' }, 'ゴムで見せる'),
            h('option', { value: 'grow' }, '即座に拡大して覆う'))),
      ),
      h(SvgClipImageEditor, {
        ref: editor.ref,
        onStateChange: editor.onStateChange,
        svg: SAMPLE_SVG,
        slot,
        rotationCover,
        motion: motionMode === 'zero' ? ZERO : undefined,
        onLoad: ({ slots: list }) => setSlots(list),
        onCommit: (svg, { slot: name, M }) => {
          setOutput(svg);
          setLog(`${name}: matrix(${M.map((v) => +v.toFixed(3)).join(' ')})`);
        },
        onError: (e) => setLog(`${e.code}: ${e.message}`),
      }),
      h(ClipEditorToolbar, { editor }),
    ),
    h('section', { className: 'card' },
      h('div', { className: 'row' }, h('strong', null, '出力'), h('span', { className: 'sub', 'data-testid': 'log' }, log)),
      h('img', { className: 'preview', alt: '出力の SVG', src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(output) }),
    ),
  );
}

createRoot(document.getElementById('root')).render(h(Demo));
