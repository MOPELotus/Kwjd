/** Isolate this task's OMP fetch transport to the authorized Responses relay. */
import { appendFileSync } from 'node:fs';

function imageCount(value: unknown): number {
  if (Array.isArray(value)) return value.reduce((n, item) => n + imageCount(item), 0);
  if (!value || typeof value !== 'object') return 0;
  const row = value as Record<string, unknown>;
  if (row.type === 'input_image') return 1;
  return Object.values(row).reduce<number>((n, item) => n + imageCount(item), 0);
}

export default function () {
  const original = globalThis.fetch;
  const wrapped = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== 'https://api.zhehentiaohe.cn' ||
        !['/v1/models', '/v1/responses'].includes(url.pathname)) {
      throw new Error('Jigong isolated transport: endpoint outside authorization');
    }
    if (request.headers.get('authorization') !== `Bearer ${process.env.JIGONG_RESPONSES_API_KEY}`) {
      throw new Error('Jigong isolated transport: unexpected authentication source');
    }
    let payload: Record<string, any> = {};
    if (url.pathname === '/v1/responses') {
      if (request.method !== 'POST') throw new Error('Jigong isolated transport: expected POST');
      payload = await request.clone().json();
      if (payload.model !== 'gpt-6.1-sol' || payload.reasoning?.effort !== 'medium') {
        throw new Error('Jigong isolated transport: unauthorized model or reasoning effort');
      }
    } else if (request.method !== 'GET') {
      throw new Error('Jigong isolated transport: models requires GET');
    }
    const response = await original(request);
    if (process.env.JIGONG_OMP_TRANSPORT_LOG) {
      appendFileSync(process.env.JIGONG_OMP_TRANSPORT_LOG, JSON.stringify({
        at: Date.now(), origin: url.origin, path: url.pathname,
        status: response.status, model: payload.model, effort: payload.reasoning?.effort,
        images: imageCount(payload.input),
        streaming: payload.stream === true,
        request_id: response.headers.get('x-request-id'),
      }) + '\n', { mode: 0o600 });
    }
    return response;
  };
  Object.assign(wrapped, original);
  globalThis.fetch = wrapped as typeof fetch;
}
