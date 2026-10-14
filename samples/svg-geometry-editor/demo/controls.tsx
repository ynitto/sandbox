import { useEffect, useState } from 'react';
import { Button, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material';
import { decompose, rotateBy, scaleTo, setPosition, translateBy } from '../src';
import type { EditorMode, Matrix } from '../src';

export interface ToolbarProps {
  mode: EditorMode; zoom: number; onZoom: (factor: number) => void; onResetView: () => void;
  canUndo: boolean; canRedo: boolean; onUndo: () => void; onRedo: () => void;
  detailsOpen: boolean; onToggleDetails: () => void;
}

export function EditorToolbar(p: ToolbarProps) {
  return <Stack className="demo-toolbar" direction="row" spacing={1} alignItems="center">
    <Tooltip title="縮小"><span><IconButton aria-label="縮小" onClick={() => p.onZoom(1 / 1.2)}>−</IconButton></span></Tooltip>
    <Typography variant="body2" aria-label="表示倍率">{Math.round(p.zoom * 100)}%</Typography>
    <Tooltip title="拡大"><span><IconButton aria-label="拡大" onClick={() => p.onZoom(1.2)}>＋</IconButton></span></Tooltip>
    <Button size="small" onClick={p.onResetView}>全体表示</Button>
    {p.mode === 'edit' && <>
      <Button size="small" disabled={!p.canUndo} onClick={p.onUndo}>戻す</Button>
      <Button size="small" disabled={!p.canRedo} onClick={p.onRedo}>やり直す</Button>
      <Button size="small" className="demo-details-toggle" onClick={p.onToggleDetails}>{p.detailsOpen ? '詳細を閉じる' : '詳細'}</Button>
    </>}
  </Stack>;
}

export interface PanelProps {
  selection: string | null;
  matrix: Matrix | null;
  /** `null` removes the image's edit. `final` marks an undoable step. */
  onEdit: (matrix: Matrix | null, final: boolean) => void;
  onFinishTyping: () => void;
  open: boolean;
  minScale: number; maxScale: number; moveStep: number; rotationStep: number;
}

type Field = 'x' | 'y' | 'scale' | 'angle';

export function EditorPanel(p: PanelProps) {
  const m = p.matrix;
  const parts = m ? decompose(m) : null;
  const clampScale = (s: number) => Math.min(p.maxScale, Math.max(p.minScale, s));
  const edit = (next: (m: Matrix) => Matrix, final = true) => { if (m) p.onEdit(next(m), final); };
  const type = (field: Field, v: number) => edit(m =>
    field === 'x' ? setPosition(m, v, m[5])
    : field === 'y' ? setPosition(m, m[4], v)
    : field === 'scale' ? scaleTo(m, clampScale(v))
    : rotateBy(m, v - decompose(m).angle), false);
  const fields: Array<{ key: Field; label: string }> = [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'scale', label: '倍率' }, { key: 'angle', label: '角度' }];
  return <aside className={`demo-panel ${p.open ? 'demo-panel-open' : ''}`} aria-label="画像編集パネル">
    <Typography variant="subtitle1" gutterBottom>{p.selection ? `画像: ${p.selection}` : '画像を選択してください'}</Typography>
    <Stack spacing={1.5}>
      {fields.map(f => <NumberField key={f.key} label={f.label} step={f.key === 'scale' ? 0.01 : 1} value={parts?.[f.key]}
        onType={v => type(f.key, v)} onFinish={p.onFinishTyping} />)}
      <Typography variant="caption">移動</Typography>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        <Button disabled={!m} onClick={() => edit(m => translateBy(m, -p.moveStep, 0))}>←</Button>
        <Button disabled={!m} onClick={() => edit(m => translateBy(m, 0, -p.moveStep))}>↑</Button>
        <Button disabled={!m} onClick={() => edit(m => translateBy(m, 0, p.moveStep))}>↓</Button>
        <Button disabled={!m} onClick={() => edit(m => translateBy(m, p.moveStep, 0))}>→</Button>
      </Stack>
      <Typography variant="caption">倍率</Typography>
      <Stack direction="row" spacing={0.5}>
        <Button disabled={!m} onClick={() => edit(m => scaleTo(m, clampScale(decompose(m).scale - 0.01)))}>−0.01</Button>
        <Button disabled={!m} onClick={() => edit(m => scaleTo(m, clampScale(decompose(m).scale + 0.01)))}>＋0.01</Button>
      </Stack>
      <Typography variant="caption">回転</Typography>
      <Stack direction="row" spacing={0.5}>
        <Button disabled={!m} onClick={() => edit(m => rotateBy(m, -p.rotationStep))}>−{p.rotationStep}°</Button>
        <Button disabled={!m} onClick={() => edit(m => rotateBy(m, p.rotationStep))}>＋{p.rotationStep}°</Button>
      </Stack>
      <Button disabled={!m} onClick={() => p.onEdit(null, true)}>画像を初期状態に戻す</Button>
    </Stack>
  </aside>;
}

function NumberField({ label, step, value, onType, onFinish }: { label: string; step: number; value?: number; onType: (v: number) => void; onFinish: () => void }) {
  const shown = value === undefined ? '' : String(Number(value.toFixed(3)));
  const [draft, setDraft] = useState(shown);
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setDraft(shown); }, [shown, focused]);
  return <TextField type="number" size="small" label={label} disabled={value === undefined} value={draft}
    slotProps={{ htmlInput: { step } }}
    onFocus={() => setFocused(true)}
    onChange={event => { const next = event.target.value; setDraft(next); if (next !== '' && Number.isFinite(Number(next))) onType(Number(next)); }}
    onBlur={() => { onFinish(); setFocused(false); }}
    onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.querySelector('input')?.blur(); }} />;
}
