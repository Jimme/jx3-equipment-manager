export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('无法连接应用，请检查服务是否正在运行。');
  }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const detail = result.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.slice(1).join('.') ?? ''}：${item.msg ?? '输入无效'}`).join('；')
      : `请求失败（${response.status}）`);
  }
  return response.json();
}

export function optionalInteger(value: string): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0 || number > 2147483647) throw new Error('请输入有效的非负整数');
  return number;
}
