import { describe, expect, it } from 'vitest';
import { optionalInteger } from './api';

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
