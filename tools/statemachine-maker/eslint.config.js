// 方針は agent-dashboard と同じ「バグだけ拾う」。整形は見ない。
// main / preload は Node、renderer はブラウザのグローバルで、プロセス境界の誤用を no-undef で拾う。
const js = require('@eslint/js');
const globals = require('globals');

module.exports = [
  { ignores: ['node_modules/**', 'release/**'] },
  js.configs.recommended,
  {
    languageOptions: { ecmaVersion: 2023, sourceType: 'commonjs' },
    rules: {
      'no-unused-vars': ['error', { args: 'after-used', argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-control-regex': 'off',
      'no-constant-binary-expression': 'error',
      'no-self-compare': 'error',
      'no-unsafe-optional-chaining': 'error',
    },
  },
  {
    files: ['src/main/**/*.js', 'src/preload.js', 'test/**/*.js', '*.js'],
    languageOptions: { globals: { ...globals.node, ...globals.nodeBuiltin } },
  },
  {
    // 実機の煙試験は、ページの中で動かす関数（win.evaluate の引数）を含む。
    // 実行文脈は renderer なので、そこだけブラウザのグローバルも見えるようにする。
    files: ['test/electron-smoke.test.js'],
    languageOptions: { globals: { ...globals.node, ...globals.nodeBuiltin, ...globals.browser } },
  },
  {
    files: ['src/renderer/**/*.js'],
    // `api` は preload が contextBridge で window へ置く窓口。renderer 側で宣言すると
    // non-configurable な同名グローバルと衝突してスクリプトごと落ちるので、
    // **宣言せず読むだけ**の約束をここで表す。
    languageOptions: { globals: { ...globals.browser, api: 'readonly' } },
  },
];
