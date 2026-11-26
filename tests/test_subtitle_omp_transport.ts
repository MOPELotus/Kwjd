import { test, expect } from 'bun:test';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import install from '../scripts/subtitle_omp_transport';

test('transport rejects provider/model/auth fallback and counts nested tool images', async () => {
  const prior = globalThis.fetch;
  const directory = mkdtempSync(join(tmpdir(), 'jigong-transport-'));
  let calls = 0;
  globalThis.fetch = (async () => { calls++; return new Response('{}'); }) as typeof fetch;
  process.env.JIGONG_RESPONSES_API_KEY = 'fixture-only';
  process.env.JIGONG_OMP_TRANSPORT_LOG = join(directory, 'transport.jsonl');
  install();
  const make = (url: string, model = 'gpt-6.1-sol', key = 'fixture-only') => fetch(url, {
    method: 'POST', headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
    body: JSON.stringify({ model, reasoning: { effort: 'medium' }, stream: true,
      input: [{ type: 'function_call_output', output: [{ type: 'input_image', image_url: 'data:fixture' }] }] }),
  });
  try {
    await expect(make('https://api.openai.com/v1/responses')).rejects.toThrow('endpoint');
    await expect(make('https://api.zhehentiaohe.cn/v1/responses', 'different-model')).rejects.toThrow('model');
    await expect(make('https://api.zhehentiaohe.cn/v1/responses', 'gpt-6.1-sol', 'wrong')).rejects.toThrow('authentication');
    expect(calls).toBe(0);
    await make('https://api.zhehentiaohe.cn/v1/responses');
    expect(calls).toBe(1);
    const log = readFileSync(process.env.JIGONG_OMP_TRANSPORT_LOG, 'utf8');
    expect(JSON.parse(log).images).toBe(1);
    expect(log).not.toContain('fixture-only');
    expect(log).not.toContain('data:fixture');
  } finally {
    globalThis.fetch = prior;
    delete process.env.JIGONG_RESPONSES_API_KEY;
    delete process.env.JIGONG_OMP_TRANSPORT_LOG;
    rmSync(directory, { recursive: true });
  }
});
