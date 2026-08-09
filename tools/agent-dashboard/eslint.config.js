// agent-dashboard の静的解析。37k 行の JS に構文検査すら無く、未定義変数・到達不能コード・
// 握り潰した Promise はテストがその行を通らない限り誰にも見えなかった。
//
// 方針は「バグだけ拾う」。整形（インデント・引用符・行長）は一切見ない——既存コードを
// 一括整形すると差分が読めなくなるうえ、整形の逸脱は動作を変えない。
const js = require('@eslint/js');
const globals = require('globals');

// Electron はプロセスごとにグローバルが違う。main/preload は Node、renderer はブラウザ。
// 一律にすると「renderer に require が見える」「main に window が見える」で、
// プロセス境界を跨いだ誤用を no-undef が検出できなくなる（この分離が検査の主眼）。
module.exports = [
  { ignores: ['node_modules/**', 'release/**'] },
  js.configs.recommended,
  {
    languageOptions: { ecmaVersion: 2023, sourceType: 'commonjs' },
    rules: {
      // 未使用引数は「まだ配線していない引数」を表すことがあるので、先頭 _ は許す。
      'no-unused-vars': ['error', {
        args: 'after-used', argsIgnorePattern: '^_', varsIgnorePattern: '^_',
        caughtErrors: 'none',   // catch (e) で e を使わない書き方は既存に多く、害が無い
        // `const { drop, ...rest } = obj` で項目を落とす書き方。落とす名前は使わないのが目的。
        ignoreRestSiblings: true,
      }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      // 制御文字の正規表現は意図的（ANSI エスケープの除去・ファイル名の禁止文字検査）。
      'no-control-regex': 'off',
      // 実害のある落とし穴だけ足す。
      'require-atomic-updates': 'error',    // await 跨ぎの読み書きで値が飛ぶ
      'no-constant-binary-expression': 'error',
      'no-self-compare': 'error',
      'no-unmodified-loop-condition': 'error',
      'no-promise-executor-return': 'error',
      'no-unsafe-optional-chaining': 'error',
      // テストは renderer の古典スクリプトを `new Function` で読み込むため、その箇所には
      // 既に `eslint-disable-next-line no-new-func` が書いてある。規則を有効にして初めて
      // それらが意味を持ち、同時に「意図しない new Function」も拾える。
      'no-new-func': 'error',
    },
  },
  {
    // scripts/ は Electron を起こさずに main プロセス側のモジュールを叩く CLI 入口
    // （`npm run resources`）。実行文脈は main と同じ Node なのでグローバルも揃える。
    files: ['src/**/main/**/*.js', 'src/**/preload.js', 'test/**/*.js', 'scripts/**/*.js', '*.js'],
    languageOptions: { globals: { ...globals.node, ...globals.nodeBuiltin } },
  },
  {
    files: ['test/**/*.js'],
    // テストはモジュールを差し替えて finally で戻す（`project.isProjectRunning = orig`）。
    // await を跨ぐので require-atomic-updates は必ず鳴るが、単一プロセスの逐次テストで
    // 競合相手が居ない。ここで切らないと差し替え自体が書けない。
    rules: { 'require-atomic-updates': 'off' },
  },
  {
    files: ['src/renderer/**/*.js'],
    // renderer は contextIsolation 下のブラウザ文脈。preload が渡す `api` が外部との窓口。
    languageOptions: { globals: globals.browser },
    rules: {
      // renderer の各ファイルは index.html から**古典スクリプトとして順に読み込まれ、
      // 1 つのグローバルスコープを共有する**（モジュールではない）。関数・state の相互参照は
      // その共有が前提なので、ファイル単位で見る検査は両方向とも誤る:
      //   no-undef      … 他ファイルで定義された参照を全て未定義と読む（1935 件）
      //   no-unused-vars … 他ファイルから呼ばれる関数宣言を全て未使用と読む（89 件）
      // 後者は特に危険で、報告どおりに消すと `initTabs` や `openDoctor` が消えて画面が壊れる。
      // モジュール化するまではどちらも成立しないので、両方切る（main 側では有効なまま）。
      'no-undef': 'off',
      'no-unused-vars': 'off',
      // 単一スレッドの UI で `state.x = await …` は意図した書き方。ここでは競合しない。
      'require-atomic-updates': 'off',
    },
  },
];
