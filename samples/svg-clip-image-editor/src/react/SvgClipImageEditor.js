// SVG のクリップされた画像を、枠を固定したまま動かして合わせ込むコンポーネント。
// 描くのは描画面（SVG）だけ。ボタン・保存・切り替えは呼び出す側が持つ（ClipEditorToolbar は任意の既定品）。
// 見た目は style.css のクラスに任せ、インラインに置くのは動きに要るものだけにする。
// JSX を使わず React.createElement で書く（ビルドなしで読み込めるように）。

import React, { forwardRef, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  apply,
  bboxOf,
  drawnImageRect,
  focusRect,
  formatMatrix,
  invert,
  parseClipDocument,
  resolveMotion,
  scaleOf,
  serialize,
  shapeToPath,
} from '../core/index.js';
import { EditorEngine } from './engine.js';

const h = React.createElement;

// サーバー描画ではブラウザに入るまで何もしない
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

const fmt = (m) => formatMatrix(m, 6);

const sameState = (a, b) => a && b && a.canUndo === b.canUndo && a.canRedo === b.canRedo && a.covered === b.covered && a.moving === b.moving;

const cx = (...names) => names.filter(Boolean).join(' ');

export const SvgClipImageEditor = forwardRef(function SvgClipImageEditor(props, ref) {
  const {
    svg,
    slot: slotProp,
    focusPadding = 0.25,
    outsideOpacity = 0.35,
    cover = true,
    rotationCover = 'rubber',
    motion: motionProp,
    minScale = 0.1,
    maxScale = 20,
    rotationSnap = 0,
    transforms,
    ariaLabel = '画像の位置を調整',
    className,
    style,
  } = props;
  const uid = 'sce' + useId().replace(/[^A-Za-z0-9_-]/g, '');
  const svgRef = useRef(null);
  const propsRef = useRef(props);
  propsRef.current = props;

  const [parsed, setParsed] = useState(null);
  const [view, setView] = useState(null);
  const [natural, setNatural] = useState(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [focus, setFocus] = useState(null);
  const [, setTick] = useState(0); // 確定のたびに状態（元に戻せるか）を知らせ直す
  // フォーカスの枠はキーボードで入ったときだけ出す（マウス・タッチで触ったときは出さない）
  const [focusRing, setFocusRing] = useState(false);
  const pointerFocus = useRef(false);
  const lastState = useRef(null);

  // ① 解析（ブラウザに入ってから）
  useEffect(() => {
    setParsed(parseClipDocument(svg));
  }, [svg]);

  const model = parsed?.ok ? parsed.model : null;
  const slot = model ? model.slots.find((s) => s.name === (slotProp ?? model.slots[0].name)) ?? null : null;
  const error = parsed && !parsed.ok ? parsed : model && !slot ? { code: 'no_slot', message: `スロット "${slotProp}" がない` } : null;

  useEffect(() => {
    if (error) propsRef.current.onError?.({ code: error.code, message: error.message, issues: error.issues });
  }, [error?.code, error?.message]);

  useEffect(() => {
    if (!model) return;
    propsRef.current.onLoad?.({
      slots: model.slots.map((s) => ({ name: s.name, bboxR: bboxOf(s.clip.hullC.map((p) => apply(s.K, p))) })),
      warnings: model.warnings,
    });
  }, [model]);

  // OS の「視差効果を減らす」
  useEffect(() => {
    const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    if (!mq) return undefined;
    const on = () => setReducedMotion(mq.matches);
    on();
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);

  const motionKey = JSON.stringify(motionProp ?? {});
  const { motion, warnings: motionWarnings } = useMemo(
    () => resolveMotion(motionProp, { reducedMotion }),
    [motionKey, reducedMotion],
  );
  useEffect(() => {
    for (const w of motionWarnings) console.warn(`SvgClipImageEditor: ${w}`);
  }, [motionWarnings]);

  // meet の画像は元の大きさが分からないと描画矩形が決まらない
  const href = slot?.image.href;
  const needsNatural = slot ? !/^\s*none\b/.test(slot.image.preserveAspectRatio) && !/slice/.test(slot.image.preserveAspectRatio) : false;
  useEffect(() => {
    if (!href || !needsNatural || typeof Image === 'undefined') return undefined;
    let alive = true;
    const img = new Image();
    img.onload = () => alive && setNatural({ href, width: img.naturalWidth, height: img.naturalHeight });
    img.src = href;
    return () => {
      alive = false;
    };
  }, [href, needsNatural]);
  const ready = Boolean(model && slot && view && focus);
  const D = slot ? drawnImageRect(slot.image, natural?.href === href ? natural : null) : null;

  // ② エンジン（動きと履歴）
  const engineRef = useRef(null);
  if (!engineRef.current) {
    engineRef.current = new EditorEngine({
      render: (M, { silent }) => {
        setView(M);
        if (!silent) propsRef.current.onChange?.(M, engineRef.current.slotName);
      },
      commit: (name, M, all) => {
        setTick((n) => n + 1);
        const p = propsRef.current;
        if (!p.onCommit) return;
        const out = serialize(engineRef.current.ctx.model, all, { precision: p.precision, outputTransform: p.outputTransform });
        p.onCommit(out, { slot: name, M });
      },
      schedule: (fn) => requestAnimationFrame(fn),
      cancel: (id) => cancelAnimationFrame(id),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id),
      now: () => performance.now(),
    });
  }
  const engine = engineRef.current;

  const targetFocus = useMemo(() => (slot ? focusRect(slot, focusPadding) : null), [slot, focusPadding]);

  useIsoLayoutEffect(() => {
    if (!model || !slot) return;
    engine.update(
      {
        model,
        slot,
        D,
        motion,
        cover,
        rotationCover,
        minScale,
        maxScale,
        rotationSnap,
        focusWidthC: targetFocus.width / scaleOf(slot.K),
        pxPerC: () => {
          const ctm = svgRef.current?.getScreenCTM?.();
          if (!ctm) return 1;
          return Math.sqrt(Math.abs(ctm.a * ctm.d - ctm.b * ctm.c)) * scaleOf(slot.K) || 1;
        },
      },
      (name) => {
        const p = propsRef.current;
        return p.transforms?.[name] ?? p.defaultTransforms?.[name] ?? null;
      },
    );
  });

  // 状態（元に戻せるか・覆えているか・動いているか）が変わったら外に知らせる
  const syncState = () => {
    if (!engine.ctx) return;
    const s = engine.state();
    if (sameState(s, lastState.current)) return;
    lastState.current = s;
    propsRef.current.onStateChange?.(s);
  };
  useEffect(syncState);

  // 制御モード: 親の値を反映する
  useEffect(() => {
    if (!slot || !transforms?.[slot.name]) return;
    engine.external(slot.name, transforms[slot.name]);
  }, [transforms, slot]);

  // 注目領域: 外からスロットを切り替えられたときだけ動く
  useEffect(() => {
    if (!targetFocus) return undefined;
    const from = focus;
    const dur = motion.snapDuration * 1000;
    if (!from || !(dur > 0)) {
      setFocus(targetFocus);
      return undefined;
    }
    let id;
    const t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const e = 1 - (1 - t) ** 3;
      setFocus({
        x: from.x + (targetFocus.x - from.x) * e,
        y: from.y + (targetFocus.y - from.y) * e,
        width: from.width + (targetFocus.width - from.width) * e,
        height: from.height + (targetFocus.height - from.height) * e,
      });
      if (t < 1) id = requestAnimationFrame(step);
    };
    id = requestAnimationFrame(step);
    return () => cancelAnimationFrame(id);
  }, [targetFocus]);

  useEffect(() => () => engine.dispose(), []);

  // スクリーンの点 → C 空間
  const toC = (clientX, clientY) => {
    const ctm = svgRef.current.getScreenCTM();
    const inv = invert([ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f]);
    return apply(invert(engine.ctx.slot.K), apply(inv, [clientX, clientY]));
  };

  // ホイールは passive でないリスナーでないと止められない。Safari のジェスチャも止める
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const dy = (e.shiftKey ? e.deltaY || e.deltaX : e.deltaY) * unit;
      engine.wheel(toC(e.clientX, e.clientY), dy, e.shiftKey);
      syncState();
    };
    const stop = (e) => e.preventDefault();
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('gesturestart', stop);
    el.addEventListener('gesturechange', stop);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('gesturestart', stop);
      el.removeEventListener('gesturechange', stop);
    };
  }, [ready, slot]);

  // 外から呼ぶ操作。呼んだ後に状態を知らせ直す
  const act = (fn) => (...args) => {
    fn(...args);
    syncState();
  };
  useImperativeHandle(
    ref,
    () => ({
      getSvg: () => {
        const p = propsRef.current;
        return engine.ctx ? serialize(engine.ctx.model, engine.allTransforms(), { precision: p.precision, outputTransform: p.outputTransform }) : p.svg;
      },
      getTransforms: () => engine.allTransforms(),
      getState: () => (engine.ctx ? engine.state() : null),
      reset: act(() => engine.reset()),
      fit: act(() => engine.fit()),
      zoom: act((f) => engine.zoom(f)),
      rotate: act((deg) => engine.rotateBy(deg)),
      undo: act(() => engine.undo()),
      redo: act(() => engine.redo()),
    }),
    [],
  );

  if (error) {
    return h('div', { className: cx('sce-error', className), style, role: 'alert' }, error.message);
  }
  if (!ready) {
    return h('div', { className: cx('sce-placeholder', className), style: { aspectRatio: '4 / 3', ...style } });
  }

  const f = focus;
  const shapes = slot.clip.shapes;
  const imgAttrs = {
    href: slot.image.href,
    x: slot.image.x,
    y: slot.image.y,
    width: slot.image.width,
    height: slot.image.height,
    preserveAspectRatio: slot.image.preserveAspectRatio,
  };
  const covered = engine.state().covered;
  const shapePath = (s, i, extra) =>
    h('path', { key: i, d: shapeToPath(s.tag, s.attrs), transform: fmt(s.transform), clipRule: s.attrs['clip-rule'], ...extra });

  const onPointerDown = (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    // キー操作を受けられるように focus する
    pointerFocus.current = true;
    svgRef.current.focus({ preventScroll: true });
    pointerFocus.current = false;
    if (engine.pointerDown(e.pointerId, toC(e.clientX, e.clientY), performance.now())) {
      e.currentTarget.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      syncState();
    }
  };
  const onPointerMove = (e) => engine.pointerMove(e.pointerId, toC(e.clientX, e.clientY), performance.now());
  const onPointerUp = (e) => {
    engine.pointerUp(e.pointerId, performance.now(), { cancel: e.type === 'pointercancel' });
    syncState();
  };

  const onKeyDown = (e) => {
    if (e.key !== 'Shift') setFocusRing(true);
    const w = engine.ctx.focusWidthC * (e.shiftKey ? 0.1 : 0.01);
    const keys = {
      ArrowLeft: () => engine.nudge(-w, 0),
      ArrowRight: () => engine.nudge(w, 0),
      ArrowUp: () => engine.nudge(0, -w),
      ArrowDown: () => engine.nudge(0, w),
      '+': () => engine.zoom(1.1, { mergeKey: true }),
      '=': () => engine.zoom(1.1, { mergeKey: true }),
      '-': () => engine.zoom(1 / 1.1, { mergeKey: true }),
      '[': () => engine.rotateBy(-1, { mergeKey: true }),
      ']': () => engine.rotateBy(1, { mergeKey: true }),
      '{': () => engine.rotateBy(-15, { mergeKey: true }),
      '}': () => engine.rotateBy(15, { mergeKey: true }),
    };
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) engine.redo();
      else engine.undo();
      syncState();
      return;
    }
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      run();
      syncState();
    }
  };

  return h(
    'svg',
    {
      ref: svgRef,
      className: cx('sce-surface', className),
      viewBox: `${f.x} ${f.y} ${f.width} ${f.height}`,
      preserveAspectRatio: 'xMidYMid meet',
      role: 'application',
      'aria-label': ariaLabel,
      tabIndex: 0,
      'data-covered': covered ? 'true' : 'false',
      'data-focus-ring': focusRing ? 'true' : undefined,
      // 動きに要るものだけをインラインに置く（大きさの比・ページのスクロールやズームを止める）
      style: {
        display: 'block',
        width: '100%',
        height: 'auto',
        aspectRatio: `${f.width} / ${f.height}`,
        touchAction: 'none',
        userSelect: 'none',
        WebkitUserSelect: 'none',
        ...style,
      },
      onFocus: () => setFocusRing(!pointerFocus.current),
      onBlur: () => setFocusRing(false),
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
    },
    h('defs', null, h('clipPath', { id: `${uid}-clip`, clipPathUnits: 'userSpaceOnUse', transform: fmt(slot.clip.Q) }, shapes.map((s, i) => shapePath(s, i)))),
    h(
      'g',
      { transform: fmt(slot.K) },
      // ① 画像全体（半透過）
      h('image', { ...imgAttrs, className: 'sce-image-outside', transform: fmt(view), opacity: outsideOpacity }),
      // ② 枠で切り抜いた画像（不透明）
      h('g', { clipPath: `url(#${uid}-clip)` }, h('image', { ...imgAttrs, className: 'sce-image-inside', transform: fmt(view) })),
    ),
    // ③ 枠の輪郭。色は CSS（.sce-frame / [data-covered="false"]）。CSS がなければ文字色の点線
    h(
      'g',
      { transform: fmt(slot.K), pointerEvents: 'none' },
      h(
        'g',
        { className: 'sce-frame', transform: fmt(slot.clip.Q), fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeDasharray: '6 4' },
        shapes.map((s, i) => shapePath(s, i, { vectorEffect: 'non-scaling-stroke' })),
      ),
    ),
    // ④ 当たり判定: 全面（背景は透明）
    h('rect', { x: f.x, y: f.y, width: f.width, height: f.height, fill: 'transparent' }),
  );
});
