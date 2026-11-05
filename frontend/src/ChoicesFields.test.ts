import { expect, it } from 'vitest';
import { draftChoiceSummary, draftChoices, serializeChoices } from './ChoicesFields';

it('只记附魔名称时，缺失的体质贡献保持未知', () => {
  const draft = draftChoices();
  draft.enchant = { state: 'configured', name: '测试附魔', constitution: '' };
  expect(serializeChoices(draft).enchant.constitution).toBeNull();
});

it('明确填入零贡献可往返保存，不丢成空值', () => {
  const value = { enchant: { state: 'configured' as const, name: '', constitution: 0 }, rainbow: { state: 'unknown' as const, name: '', constitution: null } };
  expect(serializeChoices(draftChoices(value))).toEqual(value);
});

it('确认无与未录入都不携带之前输入的名称和数值', () => {
  const draft = draftChoices();
  draft.enchant = { state: 'none', name: '旧值', constitution: '12' };
  expect(serializeChoices(draft).enchant).toEqual({ state: 'none', name: '', constitution: null });
  expect(serializeChoices(draft).rainbow).toEqual({ state: 'unknown', name: '', constitution: null });
});

it('实时摘要反映当前草稿，编辑尚未完成的数值不会导致页面报错', () => {
  const draft = draftChoices();
  draft.enchant = { state: 'configured', name: '新附魔', constitution: '-' };
  expect(draftChoiceSummary(draft, false)).toContain('新附魔');
  draft.enchant = { state: 'none', name: '', constitution: '' };
  expect(draftChoiceSummary(draft, false)).toBe('附魔：确认无');
});
