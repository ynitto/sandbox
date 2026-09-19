'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const skills = require('./skills');

// 名前だけでは同名の別コピーを消してしまう。表示した保存先とファイルの同一性をキーにする。
function describe(item, roots) {
  try {
    const target = path.resolve(item.dir);
    const root = roots.find((entry) => path.resolve(entry.path) === path.dirname(target));
    if (!root) throw new Error('スキルの保存先ではありません');
    const base = root.repo || path.dirname(path.dirname(path.resolve(root.path)));
    const realRoot = fs.realpathSync(root.path);
    if (realRoot !== path.resolve(fs.realpathSync(base), path.relative(base, path.resolve(root.path)))) {
      throw new Error('リンク経由の保存先からは削除できません');
    }
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) throw new Error('リンクされたスキルは削除できません');
    const isCommand = root.kind === 'command-dir';
    if (isCommand ? !stat.isFile() || !target.endsWith('.md') : !stat.isDirectory()) {
      throw new Error('スキルの保存形式が変わりました');
    }
    if (path.resolve(item.path) !== (isCommand ? target : path.join(target, 'SKILL.md'))) {
      throw new Error('スキルの保存先が一致しません');
    }
    const deletePath = fs.realpathSync(target);
    const removalKey = crypto.createHash('sha256')
      .update(JSON.stringify([deletePath, stat.dev, stat.ino, stat.birthtimeMs])).digest('hex');
    return { removalKey, deletePath, removalError: '' };
  } catch (error) {
    return { removalKey: '', deletePath: '', removalError: error.message };
  }
}

function candidates(roots) {
  return skills.catalogFromRoots(roots).map((item) => ({ name: item.name, dir: item.dir, path: item.path, ...describe(item, roots) }));
}

async function remove({ keys, roots, confirm, trashItem }) {
  if (!Array.isArray(keys) || !keys.length || keys.length > 400
      || keys.some((key) => typeof key !== 'string' || !/^[a-f0-9]{64}$/.test(key))) {
    throw new Error('削除するスキルを選んでください');
  }
  const current = candidates(roots());
  const selected = [...new Set(keys)].map((key) => {
    const item = current.find((entry) => entry.removalKey === key);
    if (!item) throw new Error('スキルの保存先が変わりました。一覧を読み直して選び直してください');
    return item;
  });
  if (!await confirm(selected)) return { cancelled: true, removed: [], failed: [] };
  const removed = [];
  const failed = [];
  for (const item of selected) {
    try {
      // 確認中の差し替えや、1 件の削除で現れた同名のコピーを巻き込まない。
      const fresh = describe(item, roots());
      if (fresh.removalKey !== item.removalKey) throw new Error('スキルの保存先が変わりました。選び直してください');
      await trashItem(fresh.deletePath);
      removed.push(item);
    } catch (error) {
      failed.push({ ...item, error: error.message });
    }
  }
  return { cancelled: false, removed, failed };
}

module.exports = { describe, remove };
