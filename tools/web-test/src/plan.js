'use strict';
// スイート（ファイル）× 環境 × variants から、実際に動かす 1 回分の設定を組み立てる。
// 自前の実行（runner.js）と .spec.ts の書き出し（export.js）が同じ組み立てを使うので、両者で結果が変わらない。
//
// 重ねる順（後ろが勝つ）: スイート → 環境 → variant → ケース。--base-url はすべてに勝つ。

function pick(...values) {
  for (const v of values) if (v !== undefined && v !== null) return v;
  return undefined;
}

function merged(key, ...layers) {
  return Object.assign({}, ...layers.map((l) => (l && l[key]) || {}));
}

function runLabel(c, variant) {
  return variant ? `${c.id} [${variant.name}]` : c.id;
}

// 戻り値: 1 ケース × 1 variant ごとの { key, id, variant, title, ..., skip, settings, steps }
function planSuite(suite, { env = { name: 'local', settings: {} }, baseUrl } = {}) {
  const envS = env.settings || {};
  const setup = suite.setup || {};
  const variants = suite.variants && suite.variants.length ? suite.variants : [null];
  const runs = [];
  for (const c of suite.cases) {
    for (const variant of variants) {
      if (variant && c.variants && !c.variants.includes(variant.name)) continue;
      const v = variant || {};
      const mocks = [...(setup.mocks || []), ...(c.mocks || [])];
      let skip = c.skip ? (typeof c.skip === 'string' ? c.skip : 'skip') : null;
      if (!skip && c.envs && !c.envs.includes(env.name)) skip = `環境「${env.name}」では動かさないケース（${c.envs.join(' / ')} のみ）`;
      if (!skip && envS.mocks === false && mocks.length) skip = `環境「${env.name}」では通信のモックを使わない設定のため`;
      runs.push({
        key: runLabel(c, variant),
        id: c.id,
        variant: variant ? variant.name : null,
        title: c.title,
        requirement: c.requirement,
        tags: c.tags,
        skip,
        settings: {
          baseUrl: pick(baseUrl, envS.baseUrl, suite.baseUrl) || '',
          browser: suite.browser,
          viewport: pick(c.viewport, v.viewport, suite.viewport),
          locale: pick(c.locale, v.locale, envS.locale, suite.locale),
          timezone: pick(c.timezone, v.timezone, envS.timezone, suite.timezone),
          colorScheme: pick(c.colorScheme, v.colorScheme, suite.colorScheme),
          localStorage: merged('localStorage', setup, envS, v, c),
          sessionStorage: merged('sessionStorage', setup, envS, v, c),
          headers: merged('headers', setup, envS, v, c),
          cookies: [...(setup.cookies || []), ...(envS.cookies || []), ...(c.cookies || [])],
          storageState: envS.storageState || null,
          mocks,
          timeout: suite.timeout,
        },
        steps: [...(setup.steps || []).map((s) => ({ ...s, _setup: true })), ...c.steps],
      });
    }
  }
  return runs;
}

module.exports = { planSuite, runLabel };
