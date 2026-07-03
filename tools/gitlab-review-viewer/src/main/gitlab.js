'use strict';

// GitLab REST API v4 クライアント（追加依存なし）。
// Electron の net.fetch を使うことで、環境変数から引き継いだ Chromium の
// プロキシ設定（main.js 参照）を API 呼び出しにも適用する。
// main プロセス以外（テスト等）では標準 fetch にフォールバックする。

let netFetch = fetch;
try {
  const { net } = require('electron');
  if (net && typeof net.fetch === 'function') netFetch = net.fetch.bind(net);
} catch {
  /* electron 外では標準 fetch を使う */
}

class GitLabError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'GitLabError';
    this.status = status;
    this.body = body;
  }
}

class GitLabClient {
  constructor({ baseUrl, token }) {
    this.baseUrl = String(baseUrl || 'https://gitlab.com').replace(/\/+$/, '');
    this.token = token || '';
  }

  async api(path, { method = 'GET', query = null, body = null } = {}) {
    const url = new URL(`${this.baseUrl}/api/v4${path}`);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null || v === '') continue;
        url.searchParams.set(k, String(v));
      }
    }
    const res = await netFetch(url, {
      method,
      headers: {
        'PRIVATE-TOKEN': this.token,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (!res.ok) {
      const detail =
        data && typeof data === 'object'
          ? JSON.stringify(data.message ?? data.error ?? data)
          : String(data || res.statusText);
      throw new GitLabError(
        `GitLab API ${method} ${path} が失敗しました (${res.status}): ${detail}`,
        res.status,
        data
      );
    }
    return data;
  }

  // ---- 検索用マスタ ----

  listGroups(search) {
    return this.api('/groups', {
      query: { search, per_page: 50, order_by: 'path', sort: 'asc' },
    });
  }

  listProjects({ groupId, search } = {}) {
    if (groupId) {
      return this.api(`/groups/${groupId}/projects`, {
        query: {
          search,
          include_subgroups: true,
          per_page: 100,
          order_by: 'path',
          sort: 'asc',
        },
      });
    }
    return this.api('/projects', {
      query: { search, membership: true, per_page: 100, order_by: 'path', sort: 'asc' },
    });
  }

  async listLabels({ projectId, groupId } = {}) {
    if (projectId) {
      return this.api(`/projects/${projectId}/labels`, { query: { per_page: 100 } });
    }
    if (groupId) {
      return this.api(`/groups/${groupId}/labels`, { query: { per_page: 100 } });
    }
    return [];
  }

  // ---- 候補検索 ----
  // 条件（グループ / プロジェクト / ラベル / 種別 / 状態 / キーワード / 作成者）は
  // すべて AND で組み合わせる。GitLab API の仕様上、labels パラメータも AND。
  // 種別（issue / mr）は候補一覧の絞り込みにのみ使う。

  async searchCandidates({
    type = 'both',
    groupId,
    projectId,
    labels = [],
    state = 'opened',
    search = '',
    author = '',
  } = {}) {
    const kinds = [];
    if (type === 'both' || type === 'issue') kinds.push('issues');
    if (type === 'both' || type === 'mr') kinds.push('merge_requests');

    const results = [];
    for (const kind of kinds) {
      let base;
      const query = {
        labels: labels.length ? labels.join(',') : undefined,
        search: search || undefined,
        author_username: author || undefined,
        order_by: 'updated_at',
        sort: 'desc',
        per_page: 50,
      };
      // MR に merged 状態がある一方、イシューは opened/closed のみ
      if (state && state !== 'all') {
        if (kind === 'issues' && state === 'merged') continue;
        query.state = state;
      }
      if (projectId) {
        base = `/projects/${projectId}/${kind}`;
      } else if (groupId) {
        base = `/groups/${groupId}/${kind}`;
      } else {
        base = `/${kind}`;
        query.scope = 'all';
      }
      const items = await this.api(base, { query });
      for (const it of items) {
        results.push(normalizeItem(it, kind === 'issues' ? 'issue' : 'mr'));
      }
    }
    results.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    return results;
  }

  // ---- 関連ページ ----
  // イシュー → 関連 MR（related_merge_requests + closed_by）
  // MR → クローズ対象イシュー（closes_issues）

  async listRelated({ projectId, type, iid }) {
    const related = [];
    const seen = new Set();
    const push = (it, kind) => {
      const n = normalizeItem(it, kind);
      if (!n.url || seen.has(n.url)) return;
      seen.add(n.url);
      related.push(n);
    };
    if (type === 'issue') {
      const [mrs, closedBy] = await Promise.all([
        this.api(`/projects/${projectId}/issues/${iid}/related_merge_requests`, {
          query: { per_page: 50 },
        }).catch(() => []),
        this.api(`/projects/${projectId}/issues/${iid}/closed_by`, {
          query: { per_page: 50 },
        }).catch(() => []),
      ]);
      for (const mr of [...mrs, ...closedBy]) push(mr, 'mr');
    } else {
      const issues = await this.api(
        `/projects/${projectId}/merge_requests/${iid}/closes_issues`,
        { query: { per_page: 50 } }
      ).catch(() => []);
      for (const is of issues) push(is, 'issue');
    }
    return related;
  }

  // ---- 詳細（要約・エクスポート用） ----

  itemPath({ projectId, type, iid }) {
    const kind = type === 'issue' ? 'issues' : 'merge_requests';
    return `/projects/${projectId}/${kind}/${iid}`;
  }

  async getDetail(target) {
    const base = this.itemPath(target);
    const [item, notes] = await Promise.all([
      this.api(base),
      this.api(`${base}/notes`, { query: { sort: 'asc', per_page: 100 } }).catch(() => []),
    ]);
    let changedFiles = [];
    if (target.type === 'mr') {
      const changes = await this.api(`${base}/changes`).catch(() => null);
      if (changes && Array.isArray(changes.changes)) {
        changedFiles = changes.changes.slice(0, 100).map((c) => c.new_path || c.old_path);
      }
    }
    return {
      item: normalizeItem(item, target.type),
      description: item.description || '',
      notes: (notes || [])
        .filter((n) => !n.system)
        .map((n) => ({
          author: n.author ? n.author.username : 'unknown',
          createdAt: n.created_at,
          body: n.body || '',
        })),
      changedFiles,
    };
  }

  // 単一 MR の現在状態（コンフリクト有無・未解決ディスカッション含む）を取得する。
  // 承認ボタンのマージ可否判定に使う。
  async getMR(target) {
    const mr = await this.api(this.itemPath({ ...target, type: 'mr' }));
    return normalizeItem(mr, 'mr');
  }

  // ---- 操作 ----

  addComment(target, body) {
    return this.api(`${this.itemPath(target)}/notes`, { method: 'POST', body: { body } });
  }

  deleteIssue(target) {
    return this.api(this.itemPath({ ...target, type: 'issue' }), { method: 'DELETE' });
  }

  deleteBranch(projectId, branch) {
    return this.api(
      `/projects/${projectId}/repository/branches/${encodeURIComponent(branch)}`,
      { method: 'DELETE' }
    );
  }

  async updateLabels(target, { add = [], remove = [] } = {}) {
    const updated = await this.api(this.itemPath(target), {
      method: 'PUT',
      body: {
        add_labels: add.join(',') || undefined,
        remove_labels: remove.join(',') || undefined,
      },
    });
    return normalizeItem(updated, target.type);
  }

  async mergeMR(target) {
    const merged = await this.api(`${this.itemPath(target)}/merge`, { method: 'PUT' });
    return normalizeItem(merged, 'mr');
  }

  async setState(target, event) {
    const updated = await this.api(this.itemPath(target), {
      method: 'PUT',
      body: { state_event: event },
    });
    return normalizeItem(updated, target.type);
  }
}

function normalizeItem(it, type) {
  return {
    type,
    id: it.id,
    iid: it.iid,
    projectId: it.project_id,
    title: it.title || '',
    state: it.state || '',
    labels: Array.isArray(it.labels) ? it.labels : [],
    url: it.web_url || '',
    ref: it.references ? it.references.full : `!${it.iid}`,
    author: it.author ? it.author.username : '',
    updatedAt: it.updated_at || '',
    createdAt: it.created_at || '',
    // MR のみ意味を持つ（イシューや未提供の API では既定値のまま）
    sourceBranch: it.source_branch || '',
    hasConflicts: !!it.has_conflicts,
    // 未提供（undefined）の場合は true 扱い（ブロックしない）
    blockingDiscussionsResolved: it.blocking_discussions_resolved !== false,
  };
}

module.exports = { GitLabClient, GitLabError, normalizeItem };
