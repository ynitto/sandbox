'use strict';

// 取得失敗を空の一覧と扱わない。キャッシュは表示専用で、実行・保存には使わない。
module.exports = function createSnapshotReader(inspect) {
  const previous = new Map();
  const pending = new Map();
  return function read(root) {
    if (pending.has(root)) return pending.get(root);
    const request = Promise.resolve().then(() => inspect(root)).catch((error) => ({
      available: false, error: error.message || String(error),
    })).then((snapshot) => {
      if (snapshot && snapshot.available !== false) {
        previous.set(root, snapshot);
        return snapshot;
      }
      const cached = previous.get(root);
      return {
        ...(cached || { tasks: [], machines: [] }),
        available: false, stale: !!cached,
        daemon: { running: false },
        error: snapshot?.error || '実行情報を取得できませんでした',
      };
    }).finally(() => pending.delete(root));
    pending.set(root, request);
    return request;
  };
};
