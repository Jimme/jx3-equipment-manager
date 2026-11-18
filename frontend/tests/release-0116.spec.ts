import { expect, test } from '@playwright/test';
import { fixtureSource } from './sourceFixture';

test('勾选掉落变化或移除后，核对最新件数并保留其他有效选项', async ({ page, request }) => {
  const cycle = await (await request.post('/api/cycles', { data: { name: '0116 掉落周期' } })).json();
  const account = await (await request.post('/api/accounts', { data: { name: '0116 掉落账号' } })).json();
  const source = await fixtureSource(request, cycle.id, 1110);
  const base = '/api/accounts/' + account.id;
  for (const equipment_type of ['hat', 'chest', 'belt']) {
    expect((await request.post(base + '/drops', { data: {
      request_id: crypto.randomUUID(), quantity: 2,
      equipment: { source_id: source, equipment_type, has_constitution_socket: false, green_stats_ok: false },
    } })).ok()).toBeTruthy();
  }
  const { drops } = await (await request.get(base + '/daily')).json();
  await page.goto('/');
  await page.getByLabel('游戏账号', { exact: true }).selectOption(account.id);
  await page.getByRole('button', { name: '团本掉落', exact: true }).click();
  await page.getByRole('button', { name: '选择当前筛选结果' }).click();
  for (const [kind, quantity] of [['hat', 3], ['belt', 0]] as const) {
    const drop = drops.find((item: { equipment: { equipment_type: string } }) => item.equipment.equipment_type === kind);
    expect((await request.post(base + '/drops/' + drop.id + '/quantity', { data: {
      request_id: crypto.randomUUID(), expected_version: drop.version, quantity,
    } })).ok()).toBeTruthy();
  }
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByRole('button', { name: '所选一键入库 · 3 项' })).toBeDisabled();
  await expect(page.getByText('1 项已变化。 1 项已移除。 请核对当前件数。')).toBeVisible();
  await page.getByRole('button', { name: '采用最新选择' }).click();
  page.once('dialog', async prompt => {
    expect(prompt.message()).toContain('共 5 件');
    await prompt.accept();
  });
  await page.getByRole('button', { name: '所选一键入库 · 2 项' }).click();
  await expect(page.getByText('本次还没有记录掉落。')).toBeVisible();
  const result = await (await request.get(base + '/daily')).json();
  expect(result.warehouse.map((item: { quantity: number }) => item.quantity).sort()).toEqual([2, 3]);
});

test('首页延迟缺口计算，自动 CD 只请求进度', async ({ page, request }) => {
  const account = await (await request.post('/api/accounts', { data: { name: '0116 进度账号' } })).json();
  const base = '/api/accounts/' + account.id;
  const character = await (await request.post(base + '/characters', { data: { name: '0116 进度角色' } })).json();
  const original = await (await request.get('/api/raid-cd')).json();
  const state = await (await request.put('/api/raid-cd', { data: { expected_version: original.version, enabled: true } })).json();
  try {
    await page.clock.install();
    await page.addInitScript(id => localStorage.setItem('jx3-selected-account', id), account.id);
    const homeDaily = page.waitForResponse(response => response.url().includes('/daily?include_gaps=false'));
    await page.goto('/');
    expect((await (await homeDaily).json()).gaps).toEqual([]);
    await page.getByLabel('目标真实品级', { exact: true }).fill('1110');
    await page.getByRole('button', { name: '应用', exact: true }).click();
    await expect(page.getByText('已更新目标')).toBeVisible();
    const home = await (await request.get(base + '/daily?include_gaps=false')).text();
    const withGaps = page.waitForResponse(response => response.url().includes('/daily?skip_unconfigured_gaps=true'));
    await page.getByRole('button', { name: '缺口明细' }).click();
    const response = await withGaps;
    expect((await response.json()).gaps).toHaveLength(12);
    expect((await response.text()).length).toBeGreaterThan(home.length);

    expect((await request.put(base + '/characters/' + character.id + '/raid-checks/10', { data: {
      expected_version: 0, period: state.period_10, cleared: true,
    } })).ok()).toBeTruthy();
    const requests: string[] = [];
    page.on('request', req => {
      const path = new URL(req.url()).pathname;
      if (path.startsWith('/api/')) requests.push(path);
    });
    const poll = page.waitForResponse(response => response.url().endsWith('/raid-progress'));
    await page.clock.fastForward(15000);
    expect((await poll).ok()).toBeTruthy();
    await expect(page.getByRole('columnheader').filter({ hasText: '0116 进度角色' })
      .getByRole('checkbox', { name: '10人', exact: true })).toBeChecked();
    expect(requests).toEqual([base + '/raid-progress']);
    await page.evaluate(() => Object.defineProperty(document, 'hidden', { configurable: true, value: true }));
    await page.clock.fastForward(15000);
    expect(requests).toHaveLength(1);
    const returning = page.waitForResponse(response => response.url().endsWith('/raid-progress'));
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect((await returning).ok()).toBeTruthy();
    expect(requests).toEqual([base + '/raid-progress', base + '/raid-progress']);
  } finally {
    const current = await (await request.get('/api/raid-cd')).json();
    await request.put('/api/raid-cd', { data: { expected_version: current.version, enabled: original.enabled } });
  }
});
