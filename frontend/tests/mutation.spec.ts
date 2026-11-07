import { expect, test } from '@playwright/test';

test('数量已保存但响应丢失：修改草稿先核对旧操作，显式采用最新版本后再保存', async ({ page, request }) => {
  const cycle = await (await request.post('/api/cycles', { data: { name: '响应丢失验收周期' } })).json();
  const account = await (await request.post('/api/accounts', { data: { name: '响应丢失验收账号' } })).json();
  const prefix = `/api/accounts/${account.id}`;
  const stock = await (await request.post(prefix + '/warehouse', { data: {
    request_id: crypto.randomUUID(), quantity: 2,
    equipment: { cycle_origin_id: cycle.id, equipment_type: 'hat', actual_grade: 1110, has_constitution_socket: true, green_stats_ok: true },
  } })).json();
  await page.goto('/');
  await page.getByLabel('游戏账号', { exact: true }).selectOption(account.id);
  await page.getByRole('button', { name: '共享仓库', exact: true }).click();
  await page.getByRole('button', { name: `修改${stock.label}数量`, exact: true }).click();
  await page.getByLabel('实际数量').fill('3');
  await page.route(`**${prefix}/warehouse/${stock.id}/quantity`, async route => {
    expect((await route.fetch()).ok()).toBe(true);
    await route.abort('connectionreset');
  }, { times: 1 });
  await page.getByRole('button', { name: '保存数量', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('操作结果待核对');
  await page.getByLabel('实际数量').fill('2');
  await page.getByRole('button', { name: '保存数量', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('前一次操作已核对为保存成功');
  await expect(page.getByLabel('实际数量')).toHaveValue('2');
  await expect(page.locator('.quantity-conflict')).toContainText('最新为 3 件');
  expect((await (await request.get(prefix + '/daily')).json()).warehouse[0].quantity).toBe(3);
  await page.getByRole('button', { name: '按最新版本继续', exact: true }).click();
  await page.getByRole('button', { name: '保存数量', exact: true }).click();
  await expect(page.getByLabel('实际数量')).toHaveCount(0);
  expect((await (await request.get(prefix + '/daily')).json()).warehouse[0].quantity).toBe(2);
  const history = await (await request.get(prefix + '/history')).json();
  expect(history.filter((row: { title: string }) => row.title === '修正仓库数量')).toHaveLength(2);
});
