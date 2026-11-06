import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

test('周期设置验收：未保存输入保护、多表草稿及 CSV 替换边界', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const cycle = await (await request.post('/api/cycles', { data: { name: '交互审核周期' } })).json();
  const other = await (await request.post('/api/cycles', { data: { name: '交互审核另一周期' } })).json();
  await request.put('/api/workspace/active-cycle', { data: { cycle_id: cycle.id } });
  const source = await (await request.post(`/api/cycles/${cycle.id}/sources`, { data: { source_type: '其他', name: '原来源', short_label: '原', default_grade: null } })).json();
  await request.post(`/api/cycles/${cycle.id}/sources`, { data: { source_type: '其他', name: '另一个来源', short_label: '另', default_grade: null } });
  await page.goto('/');
  await page.getByRole('button', { name: '周期与来源', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '编辑来源', exact: true }).first().click();
  await dialog.getByLabel('来源名称', { exact: true }).fill('尚未保存的来源');
  await expect(dialog.getByRole('button', { name: '复制为新周期', exact: true })).toBeDisabled();
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByRole('button', { name: '编辑来源', exact: true }).last().click();
  await expect(dialog.getByLabel('来源名称', { exact: true })).toHaveValue('尚未保存的来源');
  await dialog.getByText('管理周期', { exact: true }).click();
  await expect(dialog.getByRole('button', { name: '修改周期名称', exact: true })).toBeDisabled();
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByRole('button', { name: '重新读取', exact: true }).click();
  await expect(dialog.getByLabel('来源名称', { exact: true })).toHaveValue('尚未保存的来源');
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog.getByLabel('来源名称', { exact: true })).toHaveValue('尚未保存的来源');
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByLabel('查看周期').selectOption(other.id);
  await expect(dialog.getByLabel('查看周期')).toHaveValue(cycle.id);
  await dialog.getByRole('button', { name: '数值配置', exact: true }).click();
  await dialog.getByRole('button', { name: '常用来源', exact: true }).click();
  await expect(dialog.getByLabel('来源名称', { exact: true })).toHaveValue('尚未保存的来源');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  expect((await (await request.get(`/api/cycles/${cycle.id}/data`)).json()).sources[0].name).toBe(source.name);

  await dialog.getByRole('button', { name: '数值配置', exact: true }).click();
  await dialog.getByLabel('数值表', { exact: true }).selectOption('stone');
  await dialog.getByLabel('五行石1级体质').fill('0');
  await dialog.getByLabel('数值表', { exact: true }).selectOption('rainbow');
  await dialog.getByRole('button', { name: '添加一行', exact: true }).click();
  await dialog.getByLabel('第1行名称').fill('审核用五彩石');
  await dialog.getByLabel('第1行体质').fill('123');
  await dialog.getByRole('button', { name: '预览更改', exact: true }).click();
  const configReads: string[] = [];
  const trackConfig = (entry: { url(): string; method(): string }) => {
    if (entry.method() === 'GET' && entry.url().endsWith(`/api/cycles/${cycle.id}/data`)) configReads.push(entry.url());
  };
  page.on('request', trackConfig);
  await dialog.getByRole('button', { name: '确认保存并备份', exact: true }).click();
  await expect(dialog.getByLabel('第1行体质')).toHaveValue('123');
  page.off('request', trackConfig);
  expect(configReads).toHaveLength(1);
  await dialog.getByLabel('数值表', { exact: true }).selectOption('stone');
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('0');
  await expect(dialog.getByRole('button', { name: '复制为新周期', exact: true })).toBeDisabled();
  const downloadPromise = page.waitForEvent('download');
  await dialog.getByRole('link', { name: '导出 CSV', exact: true }).click();
  const download = await downloadPromise;
  const csv = (await readFile((await download.path())!)).toString('utf8');
  expect(csv).not.toContain('1,0'); // Export saved data, not the draft.
  const file = { name: '替换.csv', mimeType: 'text/csv', buffer: Buffer.from('五行石等级,体质\r\n1,8\r\n') };
  page.once('dialog', prompt => prompt.dismiss());
  await dialog.getByLabel('导入 CSV').setInputFiles(file);
  await expect(dialog.getByRole('region', { name: '变更预览' })).toHaveCount(0);
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('0');
  page.once('dialog', prompt => prompt.accept());
  await dialog.getByLabel('导入 CSV').setInputFiles(file);
  await expect(dialog.getByRole('region', { name: '变更预览' })).toBeVisible();
  await dialog.getByRole('button', { name: '返回修改', exact: true }).click();
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('0');
  await dialog.getByRole('button', { name: '预览更改', exact: true }).click();
  await page.route('**/changes/*/apply', route => route.fulfill({ status: 503, json: { detail: '验收模拟暂时无法保存' } }), { times: 1 });
  await dialog.getByRole('button', { name: '确认保存并备份', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('验收模拟暂时无法保存');
  await dialog.getByRole('button', { name: '返回修改', exact: true }).click();
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('0');
  await dialog.getByRole('button', { name: '预览更改', exact: true }).click();
  await dialog.getByRole('button', { name: '确认保存并备份', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '复制为新周期', exact: true })).toBeEnabled();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click(); // No dirty prompt after save.
  await expect(dialog).toHaveCount(0);

  await page.getByRole('button', { name: '周期与来源', exact: true }).click();
  await dialog.getByRole('button', { name: '编辑来源', exact: true }).first().click();
  await dialog.getByLabel('来源名称', { exact: true }).fill('旧来源草稿');
  await dialog.getByRole('button', { name: '数值配置', exact: true }).click();
  await dialog.getByLabel('数值表', { exact: true }).selectOption('stone');
  await dialog.getByLabel('五行石1级体质').fill('7');
  await dialog.getByText('管理周期', { exact: true }).click();
  await page.route(`**/api/cycles/${cycle.id}/data`, route => route.fulfill({ status: 503, json: { detail: '验收模拟读取失败' } }), { times: 1 });
  page.once('dialog', prompt => prompt.accept());
  await dialog.getByRole('button', { name: '重新读取', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('验收模拟读取失败');
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('7');

  const latest = await (await request.get(`/api/cycles/${cycle.id}/data`)).json();
  const external = await (await request.post(`/api/cycles/${cycle.id}/data/stone/preview`, {
    data: { expected_version: latest.cycle.version, rows: [{ stone_level: 1, constitution: 8 }] },
  })).json();
  expect((await request.post(`/api/cycles/${cycle.id}/changes/${external.preview_id}/apply`)).ok()).toBeTruthy();
  const latestVersion = (await (await request.get(`/api/cycles/${cycle.id}/data`)).json()).cycle.version;
  expect((await request.put(`/api/cycles/${cycle.id}/sources/${source.id}`, {
    data: { source_type: '其他', name: '另一页保存的来源', short_label: '原', default_grade: null, expected_version: latestVersion },
  })).ok()).toBeTruthy();
  await dialog.getByRole('button', { name: '重试读取', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('本表的已保存数据已变化');
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('7');
  await expect(dialog.getByRole('button', { name: '预览更改', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '放弃本表草稿', exact: true }).click();
  await expect(dialog.getByLabel('五行石1级体质')).toHaveValue('8');
  await dialog.getByRole('button', { name: '常用来源', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('该来源已在别处修改或删除');
  await expect(dialog.getByLabel('来源名称', { exact: true })).toHaveValue('旧来源草稿');
  await expect(dialog.getByRole('button', { name: '保存来源', exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
});
