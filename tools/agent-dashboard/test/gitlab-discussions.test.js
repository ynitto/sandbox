'use strict';

const assert = require('assert');
const { GitLabClient } = require('../src/base/main/gitlab');

const comments = GitLabClient.formatUnresolvedDiscussions([
  { notes: [
    { resolvable: true, resolved: false, body: '境界値を確認してください',
      author: { username: 'reviewer' }, position: { new_path: 'src/range.js', new_line: 42 } },
    { resolvable: true, resolved: true, body: '解決済み', position: { old_path: 'old.js', old_line: 3 } },
    { system: true, resolvable: true, resolved: false, body: 'system' },
  ] },
]);

assert.deepStrictEqual(comments, [{
  body: '境界値を確認してください', author: 'reviewer', file: 'src/range.js', line: 42,
  text: 'src/range.js:42: 境界値を確認してください',
}]);
console.log('ok - 未解決コードコメントにファイル名と行番号を付ける');
