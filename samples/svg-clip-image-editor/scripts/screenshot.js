// デモを Chromium で開き、操作の前後のスクリーンショットを撮る（目で確かめるため。CI では回さない）。
//   npm run demo:build && npm run demo:shot -- <出力先のディレクトリ>
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(process.argv[2] ?? path.join(here, '..', 'demo', 'dist'));
const url = pathToFileURL(path.join(here, '..', 'demo', 'index.html')).href;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 760 }, deviceScaleFactor: 1 });
const errors = [];
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto(url);
const editor = page.locator('svg[role="application"]');
await editor.waitFor();
await page.waitForTimeout(400);
const shot = (name) => page.screenshot({ path: path.join(outDir, `${name}.png`) });
await shot('1-initial');

const box = await editor.boundingBox();
const cx = box.x + box.width / 2;
const cy = box.y + box.height / 2;

// ドラッグで端を越えて引っ張る（ゴム）
await page.mouse.move(cx, cy);
await page.mouse.down();
await page.mouse.move(cx + 260, cy + 60, { steps: 12 });
await shot('2-dragging-over-edge');
await page.mouse.up();
await page.waitForTimeout(900);
await shot('3-after-spring');

// Shift + ホイールで回転
await page.mouse.move(cx, cy);
await page.keyboard.down('Shift');
for (let i = 0; i < 6; i++) await page.mouse.wheel(0, 20);
await page.keyboard.up('Shift');
await shot('4-rotating');
await page.waitForTimeout(900);
await shot('5-rotated-settled');

// 外からスロットを切り替える
await page.selectOption('[data-testid="slot"]', 'avatar');
await page.waitForTimeout(600);
await shot('6-avatar');

// すべて 0: 端で止まる
await page.selectOption('[data-testid="motion"]', 'zero');
await page.mouse.move(cx, cy);
await page.mouse.down();
await page.mouse.move(cx + 400, cy, { steps: 12 });
await shot('7-zero-dragging');
await page.mouse.up();
await page.waitForTimeout(200);
await shot('8-zero-released');

console.log(JSON.stringify({ outDir, errors, log: await page.textContent('[data-testid="log"]') }));
await browser.close();
