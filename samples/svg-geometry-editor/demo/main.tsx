import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Button, CssBaseline, Stack, ThemeProvider, Typography, createTheme } from '@mui/material';
import { SvgGeometryEditor, emptyDocument } from '../src';
import type { EditorDocument, EditorMode, EditorNotice } from '../src';
import './demo.css';

function picture(background: string, foreground: string, variant: number) {
  const canvas = document.createElement('canvas');
  canvas.width = 240; canvas.height = 240;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = background; ctx.fillRect(0, 0, 240, 240);
  ctx.fillStyle = foreground;
  if (variant === 1) {
    ctx.beginPath(); ctx.arc(120, 100, 64, 0, Math.PI * 2); ctx.fill();
    ctx.fillRect(35, 166, 170, 38);
  } else {
    ctx.beginPath(); ctx.moveTo(120, 28); ctx.lineTo(210, 192); ctx.lineTo(30, 192); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.fillRect(104, 119, 32, 70);
  }
  return canvas.toDataURL('image/png');
}

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 560">
  <defs>
    <clipPath id="leftClip"><rect x="80" y="90" width="320" height="320" rx="30"/></clipPath>
    <clipPath id="rightClip"><circle cx="0" cy="0" r="155"/></clipPath>
    <mask id="fade"><rect x="0" y="0" width="900" height="560" fill="white"/></mask>
  </defs>
  <rect width="900" height="560" fill="#edf2f8"/>
  <g clip-path="url(#leftClip)"><image id="portrait-a" href="${picture('#ffe0b2', '#ef5350', 1)}" x="80" y="90" width="320" height="320"/></g>
  <g transform="translate(640 250)" clip-path="url(#rightClip)">
    <image id="portrait-b" href="${picture('#c8e6c9', '#377dbd', 2)}" x="-160" y="-160" width="320" height="320" transform="rotate(-8)"/>
  </g>
  <g mask="url(#fade)" fill="none" stroke="#283b59" stroke-width="12">
    <rect x="80" y="90" width="320" height="320" rx="30"/>
    <circle cx="640" cy="250" r="155"/>
  </g>
  <text x="80" y="490" fill="#283b59" font-size="30" font-family="Arial">Fixed frames · editable images</text>
</svg>`;

function App() {
  const [mode, setMode] = useState<EditorMode>('edit');
  const [value, setValue] = useState<EditorDocument>(() => emptyDocument('demo-card', '1'));
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const theme = createTheme({ palette: { primary: { main: '#365a95' } } });
  async function save(document: EditorDocument) {
    // Replace /api/edits with your application's endpoint. The component never fetches.
    await fetch('/api/edits', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(document) });
  }
  return <ThemeProvider theme={theme}><CssBaseline /><main>
    <header><Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1}>
      <div><Typography variant="h5">SVG Geometry Editor</Typography><Typography variant="body2">単一画像を選択し、フレーム内で移動・拡大・回転</Typography></div>
      <Stack direction="row" gap={1}><Button variant={mode === 'view' ? 'contained' : 'outlined'} onClick={() => setMode('view')}>閲覧</Button><Button variant={mode === 'edit' ? 'contained' : 'outlined'} onClick={() => setMode('edit')}>編集</Button></Stack>
    </Stack></header>
    <div className="demo-editor"><SvgGeometryEditor svg={svg} templateId="demo-card" templateRevision="1" mode={mode}
      value={value} selectedId={selected} onSelectionChange={setSelected} onChange={setValue}
      onCommit={document => { setValue(document); setNotice(`確定: ${document.images.length} 件の画像変換`); }}
      onError={(error: EditorNotice) => setNotice(`${error.code}: ${error.message}`)} /></div>
    <section className="demo-output"><Stack direction="row" gap={1} alignItems="center"><Typography variant="h6">保存 JSON</Typography><Button onClick={() => navigator.clipboard.writeText(JSON.stringify(value, null, 2))}>コピー</Button><Button onClick={() => save(value).then(() => setNotice('保存しました')).catch(() => setNotice('デモには保存 API がありません'))}>保存 API 例</Button></Stack><pre>{JSON.stringify(value, null, 2)}</pre><Typography role="status" variant="body2">{notice}</Typography></section>
  </main></ThemeProvider>;
}
createRoot(document.getElementById('root')!).render(<App />);
