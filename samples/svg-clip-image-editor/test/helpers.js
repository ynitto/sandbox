import { JSDOM } from 'jsdom';

export const { DOMParser } = new JSDOM('').window;
export const env = { DOMParser };

/** 設計書 §4.1 の例（下辺に切り欠きのある枠） */
export const SAMPLE = `<?xml version="1.0" encoding="UTF-8"?>
<!-- 例 -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
  <defs>
    <clipPath id="hero-clip" clipPathUnits="userSpaceOnUse">
      <path d="M200 150 H600 V450 H420 Q400 380 380 450 H200 Z"/>
    </clipPath>
  </defs>
  <rect width="800" height="600" fill="#f4f1ea"/>
  <g data-clip-slot="hero" clip-path="url(#hero-clip)">
    <image href="photo.jpg" x="0" y="0" width="1200" height="800"
           preserveAspectRatio="xMidYMid slice"
           transform="matrix(0.5 0 0 0.5 100 100)"/>
  </g>
  <text x="400" y="540" text-anchor="middle">見出し</text>
</svg>
`;

/** 契約を満たす最小の文書を組み立てる */
export function doc({ defs = '', body = '', clip = '<rect x="10" y="10" width="80" height="60"/>', slotAttrs = '', image = '', around = (s) => s } = {}) {
  const slot = `<g data-clip-slot="a" clip-path="url(#c)"${slotAttrs}><image href="a.png" width="200" height="100"${image}/></g>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><clipPath id="c">${clip}</clipPath>${defs}</defs>${around(slot)}${body}</svg>`;
}
