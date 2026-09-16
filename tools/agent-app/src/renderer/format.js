'use strict';

// 画面に出す数と時刻の整形。同じ見た目を別々に書き直さないよう 1 か所へ置く。
//   size      … 添付など、B / KB / MB で足りるもの（小数 1 桁）
//   bytes     … 保存データの整理。GB まで見て、10 以上なら小数を落とす
//   checkedAt … 「いつ確認したか」の短い日時（月/日 時:分）
const Fmt = (() => {
  const UNITS = [['GB', 1073741824], ['MB', 1048576], ['KB', 1024]];

  function size(n) {
    return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  }

  function bytes(value) {
    const n = Number(value) || 0;
    for (const [unit, unitSize] of UNITS) {
      if (n >= unitSize) return `${(n / unitSize).toFixed(n / unitSize >= 10 ? 0 : 1)} ${unit}`;
    }
    return `${n} B`;
  }

  function checkedAt(at) {
    const d = new Date(at);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  return { size, bytes, checkedAt };
})();

window.Fmt = Fmt;
