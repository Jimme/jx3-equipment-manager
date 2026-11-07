import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, optionalInteger } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('丢失响应后的操作核对', () => {
  it('原样重试复用原标识，修改草稿先核对旧提交且不提交新内容', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(new Response('{"quantity":1}'))
      .mockResolvedValueOnce(new Response('{"quantity":2}'));
    vi.stubGlobal('fetch', fetch);
    const original = { request_id: 'lost-response-edit', quantity: 1 };
    await expect(api('/test', 'POST', original)).rejects.toThrow('原样重试');
    await expect(api('/test', 'POST', { ...original, quantity: 2 })).rejects.toMatchObject({ refreshRequired: true });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(original);
    await api('/test', 'POST', { ...original, quantity: 2 });
    const fresh = JSON.parse(fetch.mock.calls[2][1].body);
    expect(fresh.quantity).toBe(2);
    expect(fresh.request_id).not.toBe(original.request_id);
  });
  it('相同输入安全重放，明确拒绝后允许修正，不缓存旧成功掩盖恢复后的状态', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('lost'))
      .mockResolvedValueOnce(new Response('{"quantity":1}'))
      .mockResolvedValueOnce(new Response('{"detail":"恢复后失效"}', { status: 409 }))
      .mockResolvedValueOnce(new Response('{"quantity":2}'));
    vi.stubGlobal('fetch', fetch);
    const input = { request_id: 'replay-original-operation', quantity: 1 };
    await expect(api('/test', 'POST', input)).rejects.toThrow();
    await expect(api('/test', 'POST', input)).resolves.toEqual({ quantity: 1 });
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    await expect(api('/test', 'POST', input)).rejects.toThrow('恢复后失效');
    await api('/test', 'POST', { ...input, quantity: 2 });
    expect(JSON.parse(fetch.mock.calls[3][1].body).request_id).not.toBe(input.request_id);
  });
});

describe('数字录入保留未知与零的区别', () => {
  it('空值不变成零', () => {
    expect(optionalInteger('')).toBeNull();
    expect(optionalInteger('  ')).toBeNull();
    expect(optionalInteger('0')).toBe(0);
    expect(optionalInteger('1110')).toBe(1110);
  });
  it.each(['-1', '1.5', 'abc', 'Infinity', '2147483648'])('拒绝无效数值 %s', value => {
    expect(() => optionalInteger(value)).toThrow();
  });
});
