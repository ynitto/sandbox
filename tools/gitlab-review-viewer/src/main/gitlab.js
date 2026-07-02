'use strict';

// GitLab REST API v4 クライアント。標準の fetch のみ使用（追加依存なし）。

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
    const res = await fetch(url, {
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
  // 条件（グループ / プロジェクト / ラベル / 種別 / 状態 / キーワード）は
  // すべて AND で組み合わせる。GitLab API の仕様上、labels パラメータも AND。

  async searchCandidates({ type = 'both', groupId, projectId, labels = [], state = 'opened', search = '' } = {}) {
    const kinds = [];
    if (type === 'both' || type === 'issue') kinds.push('issues');
    if (type === 'both' || type === 'mr') kinds.push('merge_requests');

    const results = [];
    for (const kind of kinds) {
      let base;
      const query = {
        labels: labels.length ? labels.join(',') : undefined,
        search: search || undefined,
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

  // ---- 操作 ----

  addComment(target, body) {
    return this.api(`${this.itemPath(target)}/notes`, { method: 'POST', body: { body } });
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
  };
}

module.exports = { GitLabClient, GitLabError, normalizeItem };
