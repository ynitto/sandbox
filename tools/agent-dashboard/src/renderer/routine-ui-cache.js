'use strict';

function createBoundedAsyncCache({ max = 12, ttlMs = 30000 } = {}) {
    const entries = new Map();
    const pending = new Map();
    const versions = new Map();

    function versionOf(key) {
      return versions.get(key) || 0;
    }

    function invalidate(key) {
      versions.set(key, versionOf(key) + 1);
    }

    function touch(key, entry) {
      entries.delete(key);
      entries.set(key, entry);
      while (entries.size > max) entries.delete(entries.keys().next().value);
    }

    function set(key, value, at = Date.now()) {
      touch(String(key), { value, at });
      return value;
    }

    function get(key) {
      const normalized = String(key);
      const entry = entries.get(normalized);
      if (!entry) return undefined;
      touch(normalized, entry);
      return entry.value;
    }

    function peek(key, now = Date.now()) {
      const normalized = String(key);
      const entry = entries.get(normalized);
      if (!entry || now - entry.at >= ttlMs) return undefined;
      touch(normalized, entry);
      return entry.value;
    }

    async function load(key, loader, { force = false } = {}) {
      const normalized = String(key);
      if (!force) {
        const cached = peek(normalized);
        if (cached !== undefined) return cached;
      }
      if (pending.has(normalized)) return pending.get(normalized);
      const version = versionOf(normalized);
      let request;
      request = Promise.resolve()
        .then(loader)
        .then((value) => {
          if (version === versionOf(normalized)) set(normalized, value);
          return value;
        })
        .finally(() => {
          if (pending.get(normalized) === request) pending.delete(normalized);
        });
      pending.set(normalized, request);
      return request;
    }

    return {
      get,
      peek,
      set,
      load,
      delete(key) {
        const normalized = String(key);
        invalidate(normalized);
        pending.delete(normalized);
        return entries.delete(normalized);
      },
      clear() {
        for (const key of new Set([...entries.keys(), ...pending.keys()])) invalidate(key);
        pending.clear();
        entries.clear();
      },
      get size() { return entries.size; },
    };
}

globalThis.RoutineUiCache = { createBoundedAsyncCache };
