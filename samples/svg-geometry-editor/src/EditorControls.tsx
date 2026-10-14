import { useEffect, useState } from 'react';
import { Button, ButtonGroup, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material';
import type { EditorController } from './useEditorController';
import { decompose } from './geometry';
import { matrixFor } from './document';
import type { EditorMode } from './types';

export function EditorToolbar({ c, mode }: { c: EditorController; mode: EditorMode }) {
  return <Stack className="sge-toolbar" direction="row" spacing={1} alignItems="center">
    <Typography variant="subtitle2" className="sge-title">SVG Editor</Typography>
    {mode === 'edit' && <ButtonGroup size="small" variant="outlined" aria-label="操作ツール">
      <Button variant={c.tool === 'select' ? 'contained' : 'outlined'} onClick={() => c.setTool('select')}>画像</Button>
      <Button variant={c.tool === 'pan' ? 'contained' : 'outlined'} onClick={() => c.setTool('pan')}>画面移動</Button>
    </ButtonGroup>}
    <Tooltip title="縮小"><span><IconButton aria-label="縮小" onClick={() => c.zoom(1 / 1.2)}>−</IconButton></span></Tooltip>
    <Typography variant="body2" aria-label="表示倍率">{Math.round(c.view.zoom * 100)}%</Typography>
    <Tooltip title="拡大"><span><IconButton aria-label="拡大" onClick={() => c.zoom(1.2)}>＋</IconButton></span></Tooltip>
    <Button size="small" onClick={c.resetView}>全体表示</Button>
    {mode === 'edit' && <>
      <Button size="small" disabled={!c.canUndo} onClick={c.undo}>戻す</Button>
      <Button size="small" disabled={!c.canRedo} onClick={c.redo}>やり直す</Button>
      <Button size="small" className="sge-details-toggle" onClick={() => c.setDetailsOpen(!c.detailsOpen)}>{c.detailsOpen ? '詳細を閉じる' : '詳細'}</Button>
    </>}
  </Stack>;
}

export function EditorPanel({ c }: { c: EditorController }) {
  const parts = c.selection ? decompose(matrixFor(c.valid, c.selection)) : null;
  const fields = [
    { key: 'x' as const, label: 'X', value: parts?.x },
    { key: 'y' as const, label: 'Y', value: parts?.y },
    { key: 'scale' as const, label: '倍率', value: parts?.scale },
    { key: 'angle' as const, label: '角度', value: parts?.angle }
  ];
  return <aside className={`sge-panel ${c.detailsOpen ? 'sge-panel-open' : ''}`} aria-label="画像編集パネル">
    <Typography variant="subtitle1" gutterBottom>{c.selection ? `画像: ${c.selection}` : '画像を選択してください'}</Typography>
    <Stack spacing={1.5}>
      {fields.map(field => <NumberField key={field.key} field={field.key} label={field.label} value={field.value} c={c} />)}
      <Typography variant="caption">移動</Typography>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        <Button disabled={!parts} onClick={() => c.nudge(-c.constraints.moveStep, 0)}>←</Button>
        <Button disabled={!parts} onClick={() => c.nudge(0, -c.constraints.moveStep)}>↑</Button>
        <Button disabled={!parts} onClick={() => c.nudge(0, c.constraints.moveStep)}>↓</Button>
        <Button disabled={!parts} onClick={() => c.nudge(c.constraints.moveStep, 0)}>→</Button>
      </Stack>
      <Typography variant="caption">倍率</Typography>
      <Stack direction="row" spacing={0.5}>
        <Button disabled={!parts} onClick={() => c.scaleBy(-0.01)}>−0.01</Button>
        <Button disabled={!parts} onClick={() => c.scaleBy(0.01)}>＋0.01</Button>
      </Stack>
      <Typography variant="caption">回転</Typography>
      <Stack direction="row" spacing={0.5}>
        <Button disabled={!parts} onClick={() => c.rotateBy(-c.constraints.rotationStep)}>−{c.constraints.rotationStep}°</Button>
        <Button disabled={!parts} onClick={() => c.rotateBy(c.constraints.rotationStep)}>＋{c.constraints.rotationStep}°</Button>
      </Stack>
      <Button disabled={!parts} onClick={c.resetImage}>画像を初期状態に戻す</Button>
    </Stack>
  </aside>;
}

function NumberField({ field, label, value, c }: { field: 'x' | 'y' | 'scale' | 'angle'; label: string; value?: number; c: EditorController }) {
  const [draft, setDraft] = useState(value === undefined ? '' : String(Number(value.toFixed(3))));
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setDraft(value === undefined ? '' : String(Number(value.toFixed(3)))); }, [value, focused]);
  return <TextField type="number" size="small" label={label} disabled={value === undefined} value={draft}
    slotProps={{ htmlInput: { step: field === 'scale' ? 0.01 : 1 } }}
    onFocus={() => setFocused(true)}
    onChange={event => { const next = event.target.value; setDraft(next); if (next !== '' && Number.isFinite(Number(next))) c.editNumber(field, Number(next)); }}
    onBlur={() => { c.finishNumber(); setFocused(false); }}
    onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.querySelector('input')?.blur(); }} />;
}
