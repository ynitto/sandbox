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
    files: ['src/renderer/**/*.js'],
    languageOptions: { globals: globals.browser },
  },
];
