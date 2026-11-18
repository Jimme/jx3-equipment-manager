import { expect, test } from '@playwright/test';
import { fixtureSource } from './sourceFixture';

test('换周期后批量可继续用旧声望来源，单行换源时品级随之更新', async ({ page, request }) => {
  const old = await (await request.post('/api/cycles', { data: { name: '示意旧版' } })).json();
  const reputation = await fixtureSource(request, old.id, 1110, '常用声望', '声');
  const next = await (await request.post('/api/cycles', { data: { name: '示意新版' } })).json();
  const raid = await fixtureSource(request, next.id, 1200, '新版副本长来源名称用于检查批量录入下拉框的显示宽度', '新');
  const account = await (await request.post('/api/accounts', { data: { name: '换版验收账号' } })).json();
  const base = `/api/accounts/${account.id}`;
  const character = await (await request.post(base + '/characters', { data: { name: '换版验收角色' } })).json();
  await page.goto('/');
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.getByLabel('游戏账号', { exact: true }).selectOption(account.id);
  await page.getByRole('button', { name: '换版验收角色 批量录入装备' }).click();
  const form = page.getByRole('dialog');
  await expect(form.getByLabel('批量统一品级')).toHaveCount(0);
  await expect(form.getByText('套用全部 12 个部位')).toBeVisible();
  const rows = form.locator('.batch-table tbody tr');
  await expect(rows).toHaveCount(12);
  const scroll = form.locator('.batch-scroll');
  expect(await scroll.evaluate(node => node.scrollHeight <= node.clientHeight + 1)).toBeTruthy();
  await form.getByLabel('批量统一来源').selectOption(reputation);
  await expect(form.getByLabel('帽子批量品级')).toHaveText('1110');
  await form.getByLabel('上衣批量来源').selectOption(raid);
  await expect(form.getByLabel('上衣批量品级')).toHaveText('1200');
  const last = (await rows.last().boundingBox())!;
  const bounds = (await scroll.boundingBox())!;
  expect(last.y + last.height).toBeLessThanOrEqual(bounds.y + bounds.height + 1);
  expect(await scroll.evaluate(node => node.scrollHeight <= node.clientHeight + 1)).toBeTruthy();
  const preview = form.getByRole('button', { name: '预览整批更改' });
  const previewBounds = (await preview.boundingBox())!;
  expect(previewBounds.y + previewBounds.height).toBeLessThan(768);
  const more = form.getByRole('button', { name: '帽子更多属性' });
  await more.click();
  await expect(form.getByLabel('帽子批量装备名称')).toBeVisible();
  await more.click();
  await expect(rows).toHaveCount(12);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await scroll.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1366, height: 768 });
  await form.getByRole('button', { name: '预览整批更改' }).click();
  await expect(form.getByLabel('角色批量预览')).toContainText('常用声望');
  await expect(form.getByLabel('角色批量预览')).toContainText('新版副本');
  await form.getByRole('button', { name: '确认整批保存并备份' }).click();
  await expect(form).not.toBeVisible();
  const worn = (await (await request.get(base + '/matrix')).json()).cells[character.id];
  expect(worn.hat.equipment).toMatchObject({ source_id: reputation, actual_grade: 1110, cycle_origin_id: old.id });
  expect(worn.chest.equipment).toMatchObject({ source_id: raid, actual_grade: 1200, cycle_origin_id: next.id });
  expect((await request.post(base + '/warehouse', { data: {
    request_id: crypto.randomUUID(), quantity: 1,
    equipment: { source_id: raid, equipment_type: 'weapon', has_constitution_socket: false, green_stats_ok: true },
    choices: { enchant: { state: 'configured', name: '示意附魔' } },
  } })).ok()).toBeTruthy();
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  const weapon = page.getByRole('button', { name: '编辑武器共享仓库配置1' });
  await expect(weapon).toContainText('1200 · ×1');
  await expect(weapon).not.toContainText(/附魔|五彩石/);
  await page.getByRole('button', { name: '共享仓库', exact: true }).click();
  const list = page.getByRole('region', { name: '装备明细' });
  await expect(list.getByText('附魔：示意附魔', { exact: false })).toBeHidden();
  await list.getByText('详情', { exact: true }).click();
  await expect(list.getByText('附魔：示意附魔', { exact: false })).toBeVisible();
});
