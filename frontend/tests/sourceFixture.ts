import { expect, type APIRequestContext, type Locator } from '@playwright/test';

export async function fixtureSource(request: APIRequestContext, cycleId: string, grade: number, name = `示意来源 ${grade}`, short = '示') {
  const response = await request.post(`/api/cycles/${cycleId}/sources`, {
    data: { source_type: '测试', name, short_label: short, actual_grade: grade },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).id as string;
}

export async function selectSource(select: Locator, name: string) {
  const option = select.getByRole('option', { name: new RegExp('^' + name + ' ·') });
  await expect(option).toHaveCount(1);
  await select.selectOption((await option.getAttribute('value'))!);
}
