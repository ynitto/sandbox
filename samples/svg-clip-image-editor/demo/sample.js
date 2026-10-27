// デモ用の SVG（2 つのスロットと飾り）。画像は SVG で描いた写真の代わり。

const photo = (hue, label) => `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="hsl(${hue},70%,62%)"/><stop offset="1" stop-color="hsl(${hue + 30},80%,88%)"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="800" fill="url(#sky)"/>
  <circle cx="900" cy="230" r="110" fill="hsl(${hue + 180},90%,70%)"/>
  <path d="M0 620 L260 360 L470 560 L700 300 L1200 700 L1200 800 L0 800 Z" fill="hsl(${hue + 120},35%,32%)"/>
  <path d="M0 720 L380 520 L640 690 L900 560 L1200 760 L1200 800 L0 800 Z" fill="hsl(${hue + 120},45%,22%)"/>
  <g font-family="sans-serif" font-size="64" font-weight="700" fill="#fff" fill-opacity="0.85">
    <text x="40" y="90">${label}</text><text x="1060" y="770">↘</text>
  </g>
  <g stroke="#fff" stroke-opacity="0.35" stroke-width="2">
    ${Array.from({ length: 11 }, (_, i) => `<line x1="${i * 120}" y1="0" x2="${i * 120}" y2="800"/>`).join('')}
    ${Array.from({ length: 7 }, (_, i) => `<line x1="0" y1="${i * 133}" x2="1200" y2="${i * 133}"/>`).join('')}
  </g>
</svg>`;

const toDataUri = (svg) =>
  'data:image/svg+xml;base64,' +
  (typeof btoa === 'function' ? btoa(unescape(encodeURIComponent(svg))) : Buffer.from(svg, 'utf8').toString('base64'));

export const SAMPLE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
  <defs>
    <clipPath id="hero-clip" clipPathUnits="userSpaceOnUse">
      <path d="M120 90 H680 V400 H450 Q400 330 350 400 H120 Z"/>
    </clipPath>
    <clipPath id="avatar-clip">
      <circle cx="0" cy="0" r="70"/>
    </clipPath>
    <linearGradient id="card" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#fdfbf7"/><stop offset="1" stop-color="#efe9df"/>
    </linearGradient>
  </defs>
  <rect width="800" height="600" fill="url(#card)"/>
  <rect x="104" y="74" width="592" height="342" rx="12" fill="none" stroke="#d8cfc2"/>
  <g data-clip-slot="hero" clip-path="url(#hero-clip)">
    <image href="${toDataUri(photo(200, 'HERO'))}" x="0" y="0" width="1200" height="800"
           preserveAspectRatio="xMidYMid slice" transform="matrix(0.5 0 0 0.5 100 80)"/>
  </g>
  <g transform="translate(400 470) rotate(-8)">
    <circle r="78" fill="#fff" stroke="#d8cfc2"/>
    <g data-clip-slot="avatar" clip-path="url(#avatar-clip)">
      <image href="${toDataUri(photo(20, 'AVATAR'))}" x="-600" y="-400" width="1200" height="800"
             transform="matrix(0.2 0 0 0.2 0 0)"/>
    </g>
  </g>
  <text x="400" y="580" text-anchor="middle" font-family="sans-serif" font-size="22" fill="#5b5249">見出しのテキスト（飾り）</text>
</svg>`;
