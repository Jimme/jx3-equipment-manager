import { fixtureSource } from './sourceFixture';
import { expect, test } from '@playwright/test';

test('清空仓库确认、清空前备份与独立掉落保留', async ({ page, request }) => {
  const cycle = await (await request.post('/api/cycles', { data: { name: '清仓示意周期' } })).json();
  const account = await (await request.post('/api/accounts', { data: { name: '清仓示意账号' } })).json();
  const base = `/api/accounts/${account.id}`;
  for (const [target, type, grade, quantity] of [['warehouse', 'hat', 1000, 2], ['warehouse', 'ring', 1100, 3], ['drops', 'hat', 1200, 1]] as const) {
    const sourceId = await fixtureSource(request, cycle.id, grade);
    const response = await request.post(base + '/' + target, { data: { request_id: crypto.randomUUID(), quantity,
      equipment: { source_id: sourceId, equipment_type: type, has_constitution_socket: false, green_stats_ok: true } } });
    expect(response.ok()).toBeTruthy();
  }
  await page.goto('/');
  await page.getByLabel('游戏账号', { exact: true }).selectOption(account.id);
  await page.getByRole('button', { name: '共享仓库', exact: true }).click();
  const clear = page.getByRole('button', { name: '清空共享仓库', exact: true });
  const confirm = page.waitForEvent('dialog');
  const canceled = clear.click();
  const prompt = await confirm;
  expect(prompt.message()).toContain('「清仓示意账号」共享仓库的全部 2 种配置、5 件装备');
  await prompt.dismiss();
  await canceled;
  await expect(page.locator('.stock-grade-headings th')).toHaveText(['1100', '1000', '新增']);
  page.once('dialog', dialog => dialog.accept());
  await clear.click();
  await expect(clear).toBeDisabled();
  await expect(page.getByText('共享仓库还没有库存。')).toBeVisible();
  await expect(page.locator('.stock-grade-headings th')).toHaveText(['新增']);
  const daily = await (await request.get(base + '/daily')).json();
  expect(daily.warehouse).toEqual([]);
  expect(daily.drops).toHaveLength(1);
  expect((await (await request.get('/api/data/backups')).json()).some((file: { name: string }) => file.name.startsWith('before-warehouse-clear-'))).toBe(true);
});
