import { expect, test } from '@playwright/test';
import { fixtureSource } from './sourceFixture';

test('换周期后批量可继续用旧声望来源，单行换源时品级随之更新', async ({ page, request }) => {
  const old = await (await request.post('/api/cycles', { data: { name: '示意旧版' } })).json();
  const reputation = await fixtureSource(request, old.id, 1110, '常用声望', '声');
  const next = await (await request.post('/api/cycles', { data: { name: '示意新版' } })).json();
  const raid = await fixtureSource(request, next.id, 1200, '新版副本', '新');
  const account = await (await request.post('/api/accounts', { data: { name: '换版验收账号' } })).json();
  const base = `/api/accounts/${account.id}`;
  const character = await (await request.post(base + '/characters', { data: { name: '换版验收角色' } })).json();
  await page.goto('/');
  await page.getByLabel('游戏账号', { exact: true }).selectOption(account.id);
  await page.getByRole('button', { name: '换版验收角色 批量录入装备' }).click();
  const form = page.getByRole('dialog');
  await expect(form.getByLabel('批量统一品级')).toHaveCount(0);
  await form.getByLabel('批量统一来源').selectOption(reputation);
  await expect(form.getByLabel('帽子批量品级')).toHaveText('1110');
  await form.getByLabel('上衣批量来源').selectOption(raid);
  await expect(form.getByLabel('上衣批量品级')).toHaveText('1200');
  await form.getByRole('button', { name: '预览整批更改' }).click();
  await expect(form.getByLabel('角色批量预览')).toContainText('常用声望');
  await expect(form.getByLabel('角色批量预览')).toContainText('新版副本');
  await form.getByRole('button', { name: '确认整批保存并备份' }).click();
  await expect(form).not.toBeVisible();
  const worn = (await (await request.get(base + '/matrix')).json()).cells[character.id];
  expect(worn.hat.equipment).toMatchObject({ source_id: reputation, actual_grade: 1110, cycle_origin_id: old.id });
  expect(worn.chest.equipment).toMatchObject({ source_id: raid, actual_grade: 1200, cycle_origin_id: next.id });
});
