import { afterEach, expect, it, vi } from 'vitest';
import { requestId } from './requestId';

afterEach(() => vi.unstubAllGlobals());

it('在普通 HTTP 缺少 randomUUID 时仍用加密随机数生成不同的 UUID v4', () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(globalThis.crypto);
  vi.stubGlobal('crypto', { getRandomValues });
  const ids = Array.from({ length: 100 }, requestId);
  expect(new Set(ids).size).toBe(100);
  for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

it('安全上下文沿用原生 UUID', () => {
  const native = vi.fn(() => 'native-request-id');
  vi.stubGlobal('crypto', { randomUUID: native });
  expect(requestId()).toBe('native-request-id');
  expect(native).toHaveBeenCalledOnce();
});
