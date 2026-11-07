import { requestId } from './requestId';

export class ApiError extends Error {
  constructor(message: string, public status: number, public refreshRequired = false) { super(message); }
}

type Mutation = { path: string; method: string; input: string; sent: Record<string, unknown>; state: 'sending' | 'unknown' | 'saved' };
const mutations = new Map<string, Mutation>();
export function isMutationUncertain(key: string): boolean {
  const state = mutations.get(key)?.state;
  return state === 'unknown' || state === 'sending';
}
if (typeof window !== 'undefined') window.addEventListener('beforeunload', event => {
  if (![...mutations.values()].some(item => item.state === 'unknown' || item.state === 'sending')) return;
  event.preventDefault(); event.returnValue = '';
});

async function request<T>(path: string, method: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('无法连接应用，请检查服务是否正在运行。', 0);
  }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const detail = result.detail;
    throw new ApiError(typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.slice(1).join('.') ?? ''}：${item.msg ?? '输入无效'}`).join('；')
      : `请求失败（${response.status}）`, response.status);
  }
  return response.json();
}

async function sendMutation<T>(mutation: Mutation): Promise<T> {
  mutation.state = 'sending';
  try {
    const result = await request<T>(mutation.path, mutation.method, mutation.sent);
    mutation.state = 'saved';
    return result;
  } catch (error) {
    const uncertain = !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
    mutation.state = uncertain ? 'unknown' : 'saved';
    if (uncertain) throw new ApiError(`${error instanceof Error ? error.message : '响应读取失败'} 操作结果待核对，可原样重试；修改输入后会先核对上一笔。`, 0);
    throw error;
  }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const object = body && typeof body === 'object' ? body as Record<string, unknown> : null;
  if (!object || typeof object.request_id !== 'string' || method === 'GET') return request<T>(path, method, body);
  const key = object.request_id;
  const input = JSON.stringify([path, method, object]);
  const previous = mutations.get(key);
  if (previous?.state === 'sending') throw new ApiError('上一笔操作正在提交，请稍候', 409);
  if (previous?.state === 'unknown' && previous.input !== input) {
    await sendMutation(previous);
    throw new ApiError('前一次操作已核对为保存成功。本次新输入尚未提交，请核对最新状态后再保存。', 409, true);
  }
  if (previous?.input === input) return sendMutation<T>(previous);
  const mutation: Mutation = { path, method, input, sent: JSON.parse(JSON.stringify({ ...object, request_id: previous ? requestId() : key })), state: 'sending' };
  mutations.set(key, mutation);
  if (mutations.size > 100) {
    const oldest = [...mutations].find(([id, item]) => id !== key && item.state === 'saved');
    if (oldest) mutations.delete(oldest[0]);
  }
  return sendMutation<T>(mutation);
}

export function optionalInteger(value: string): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0 || number > 2147483647) throw new Error('请输入有效的非负整数');
  return number;
}
