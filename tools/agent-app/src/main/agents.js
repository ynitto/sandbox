'use strict';

// エージェント定義の一覧と「使える」印。会話（agents:list）とタスク・ワークフロー
// （automation:agents:list）が **同じ 1 つの一覧** を見る。
//
// 以前タスク・ワークフローは `agent-herd defs --json` に定義を聞いていたが、それだと
// agent-tools（agent-herd）を入れていない PC ではタスクの AI を 1 つも選べなかった。
// 定義は agents/*.json（探索順は agentCli.searchDirs）にあり、agent-app 自身が読める。
// 「使える」印はホスト側の PATH（Windows では WSL の中）で引く——CLI もタスクの実行も
// そこで動くので、会話と同じ判定がそのままタスクの判定になる。

const agentCli = require('./agentCli');
const herd = require('./herd');
const host = require('./host');

// 定義ごとの「使える」印はホスト側の PATH で引く（Windows では WSL の中）。
const availCache = new Map();
async function hostAvailability(distro, commands, { shellFor = host.shellFor } = {}) {
  const key = `${distro}|${commands.join(',')}`;
  const hit = availCache.get(key);
  if (hit && Date.now() - hit.at < 60000) return hit.map;
  const sh = shellFor(distro);
  const script = `for c in ${commands.map(host.sq).join(' ')}; do printf '%s=%s\\n' "$c" "$(command -v "$c" 2>/dev/null || true)"; done`;
  const r = await sh.run(script, { timeoutMs: 20000 });
  const map = new Map();
  if (r.ok) for (const line of r.output.split('\n')) { const m = line.match(/^([^=]+)=(.*)$/); if (m) map.set(m[1], m[2].trim()); }
  availCache.set(key, { at: Date.now(), map });
  return r.ok ? map : null;
}

// 一覧の最後に仮想の `herd`（一族が 1 つでもあれば）を足す。画面の直接指定・設定の tier・
// タスクの「使う AI」のどれもこの一覧から選ぶので、herd はここで足せば全部に出る。
//   distro … リポジトリから決めたホスト（Windows の WSL ディストロ。他の OS は ''）
async function listAgents(repo, { distro = '', shellFor } = {}) {
  const defs = agentCli.list(repo);
  const map = await hostAvailability(distro, [...new Set(defs.map((d) => d.command))], shellFor ? { shellFor } : {});
  const marked = map ? defs.map((d) => ({ ...d, available: !!map.get(d.command) })) : defs;   // ホストに聞けない → ローカル PATH の判定のまま
  const virtual = herd.listEntry(marked);
  return virtual ? [...marked, virtual] : marked;
}

// タスク・ワークフローで選べる名前の並び（実際に起こせるものだけ。`herd` は一族が使えるとき）。
function usableNames(entries) {
  return (Array.isArray(entries) ? entries : []).filter((e) => e && e.available).map((e) => String(e.name));
}

module.exports = { hostAvailability, listAgents, usableNames };
