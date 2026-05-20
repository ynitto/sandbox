import { requestUrl, RequestUrlParam, RequestUrlResponse } from 'obsidian';

export default class GitlabApi {

	static load<T>(url: string, gitlabToken: string): Promise<T> {

		const headers = { 'PRIVATE-TOKEN': gitlabToken };

		const params: RequestUrlParam = { url: url, headers: headers };

		return requestUrl(params)
			.then((response: RequestUrlResponse) => {
				if (response.status !== 200) {
					throw new Error(response.text);
				}

				return response.json as Promise<T>;
			});
	}

	static async loadAll<T>(baseUrl: string, gitlabToken: string, maxItems: number): Promise<T[]> {
		const headers = { 'PRIVATE-TOKEN': gitlabToken };
		const results: T[] = [];
		const sep = baseUrl.includes('?') ? '&' : '?';
		let page = 1;

		while (results.length < maxItems) {
			const url = encodeURI(`${baseUrl}${sep}per_page=100&page=${page}`);
			const response = await requestUrl({ url, headers });

			if (response.status !== 200) {
				throw new Error(response.text);
			}

			const items = response.json as T[];
			if (items.length === 0) break;

			results.push(...items);

			if (!response.headers['x-next-page']) break;
			page++;
		}

		return results.slice(0, maxItems);
	}

	static async request<T>(
		url: string,
		gitlabToken: string,
		method: 'POST' | 'PUT' | 'DELETE',
		params?: Record<string, string>
	): Promise<T> {
		const headers: Record<string, string> = { 'PRIVATE-TOKEN': gitlabToken };
		let body: string | undefined;

		if (params && Object.keys(params).length > 0) {
			const search = new URLSearchParams();
			for (const [k, v] of Object.entries(params)) {
				search.append(k, v);
			}
			body = search.toString();
			headers['Content-Type'] = 'application/x-www-form-urlencoded';
		}

		const response = await requestUrl({ url, method, headers, body, throw: false });

		if (response.status < 200 || response.status >= 300) {
			throw new Error(`${response.status}: ${response.text}`);
		}

		return response.json as T;
	}
}
