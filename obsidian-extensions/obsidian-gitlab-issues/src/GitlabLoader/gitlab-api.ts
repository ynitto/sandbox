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
}
