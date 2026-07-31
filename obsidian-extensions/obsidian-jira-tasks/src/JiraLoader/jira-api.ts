import { requestUrl, RequestUrlParam, RequestUrlResponse } from 'obsidian';

export default class JiraApi {

	static load<T>(url: string, email: string, apiToken: string): Promise<T> {
		const credentials = btoa(`${email}:${apiToken}`);
		const headers = {
			'Authorization': `Basic ${credentials}`,
			'Content-Type': 'application/json',
			'Accept': 'application/json',
		};

		const params: RequestUrlParam = { url, headers };

		return requestUrl(params)
			.then((response: RequestUrlResponse) => {
				if (response.status !== 200) {
					throw new Error(`HTTP ${response.status}: ${response.text}`);
				}
				return response.json as Promise<T>;
			});
	}
}
