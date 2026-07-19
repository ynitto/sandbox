'use strict';

const path = require('path');

const budget = require('./budget');
const deliveries = require('./deliveries');
const homes = require('./homes');
const missions = require('./missions');

function registerIpc(ctx) {
  const { dialog, handle, loadConfig } = ctx;
  // 一覧（ミッション近似ビュー + ホーム + ノード予算）— renderer の 1 ポーリングで返す
  handle('amigos:overview', () => {
    const cfg = loadConfig();
    const homeList = homes.discoverHomes(cfg);
    const ov = missions.overview(cfg, homeList.map((h) => h.busDir));
    // ミッション → ホームの対応（busDir 一致）。引き受け・依頼の投函先解決に使う
    const byBus = new Map(homeList.filter((h) => h.busDir)
      .map((h) => [path.resolve(h.busDir), h.dir]));
    for (const m of ov.missions) {
      m.home = byBus.get(path.resolve(m.busDir)) || null;
    }
    // 納品はミッション単位で見せる（利用者が考える単位はミッション）。
    // 一覧では中身を運ばず、メタ情報だけをミッションへ結びつける。
    const received = deliveries.list(homeList);
    const byMission = new Map(received.map((d) => [d.mission, d]));
    for (const m of ov.missions) {
      m.delivery = byMission.get(m.id) || null;
    }
    // バスから消えた（gc 済み）ミッションの納品は行き場が無くなるので別に返す
    const known = new Set(ov.missions.map((m) => m.id));
    return {
      ...ov,
      homes: homeList,
      budget: budget.usage(cfg),
      deliveries: received,
      orphanDeliveries: received.filter((d) => !known.has(d.mission)),
    };
  });
  // 受け取り済み成果物の中身（ミッション詳細を開いたときだけ読む）
  handle('amigos:deliveryContents', (payload) => {
    const p = payload || {};
    const home = homes.discoverHomes(loadConfig()).find(
      (h) => path.resolve(h.dir) === path.resolve(String(p.home || ''))
    );
    if (!home) throw new Error(`amigos ホームではありません: ${p.home}`);
    return deliveries.readContents(home.dir, String(p.mission || ''));
  });
  handle('amigos:deliveryExport', async (payload) => {
    const p = payload || {};
    const home = homes.discoverHomes(loadConfig()).find(
      (h) => path.resolve(h.dir) === path.resolve(String(p.home || ''))
    );
    if (!home) throw new Error(`amigos ホームではありません: ${p.home}`);
    if (!dialog || typeof dialog.showOpenDialog !== 'function') {
      throw new Error('フォルダ選択を利用できません');
    }
    const selected = await dialog.showOpenDialog({
      title: '成果物の保存先を選択',
      properties: ['openDirectory', 'createDirectory'],
    });
    if (selected.canceled || !selected.filePaths || !selected.filePaths[0]) {
      return { canceled: true };
    }
    return { canceled: false, ...deliveries.copyToFolder(
      home.dir,
      String(p.mission || ''),
      selected.filePaths[0]
    ) };
  });
  handle('amigos:budgetSave', (payload) => budget.save(loadConfig(), payload || {}));
  // タスク依頼: ホームの commands/ へ post 指示を投函（常駐デーモンが取り込む）
  handle('amigos:request', (payload) => {
    const p = payload || {};
    let roles = p.roles;
    if (typeof roles === 'string') {
      roles = JSON.parse(roles);
    }
    if (!Array.isArray(roles) || !roles.length) {
      throw new Error('roles には役割ミッション表（JSON 配列）が必要です');
    }
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'post',
      title: String(p.title || ''),
      goal: String(p.goal || ''),
      design: String(p.design || ''),
      mission: p.mission && typeof p.mission === 'object' ? p.mission : undefined,
      roles,
    });
  });
  // 手動引き受け: ホームの commands/ へ claim 指示を投函
  handle('amigos:claim', (payload) => {
    const p = payload || {};
    if (!p.mission || !p.role) throw new Error('mission と role が必要です');
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'claim', mission: String(p.mission), role: String(p.role),
    });
  });
  // 受入判定: accept / reject も commands 投函で owner デーモンに委ねる。
  // 納品棚への搬出は accept を取り込んだ owner デーモンが行う（dashboard は書かない）。
  handle('amigos:accept', (payload) => {
    const p = payload || {};
    if (!p.mission) throw new Error('mission が必要です');
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'accept', mission: String(p.mission),
    });
  });
  handle('amigos:reject', (payload) => {
    const p = payload || {};
    if (!p.mission) throw new Error('mission が必要です');
    const feedback = String(p.feedback || '').trim();
    if (!feedback) throw new Error('差し戻しには修正依頼の内容が必要です');
    return homes.writeCommand(loadConfig(), p.home, {
      command: 'reject', mission: String(p.mission), feedback,
    });
  });
}

module.exports = { registerIpc };
