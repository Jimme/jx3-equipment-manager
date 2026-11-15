import { createContext, useContext, useSyncExternalStore } from 'react';
import { mutationSnapshot, recoverOperation, subscribeMutations, uncertainOperations } from './api';
import type { Run } from './types';

export const RunContext = createContext<Run | null>(null);

function operationName(path: string, method: string) {
  if (method === 'DELETE') return '删除记录';
  if (method === 'PATCH') return '管理信息修改';
  if (path.includes('equipment-batch')) return '角色批量保存';
  if (path.includes('warehouse-batch')) return '仓库批量保存';
  if (path.endsWith('/warehouse/clear')) return '清空共享仓库';
  if (path.includes('/quantity')) return '数量修改';
  if (path.includes('/transfer') || path.includes('batch-transfer')) return '掉落入库';
  if (path.endsWith('/characters')) return '角色创建';
  if (path.endsWith('/accounts')) return '账号创建';
  if (path.endsWith('/cycles')) return '周期创建';
  if (path.endsWith('/sources')) return '来源创建';
  return '装备或库存操作';
}

export function RecoveryNotice() {
  useSyncExternalStore(subscribeMutations, mutationSnapshot);
  const run = useContext(RunContext);
  const operations = uncertainOperations();
  if (!operations.length || !run) return null;
  return <aside className="replacement-note recovery-notice" aria-label="待核对操作">
    <span>上一笔响应未确认。可离开表单，但请先核对再做同类操作。</span>
    {operations.map(([key, value]) => <button key={key} type="button" onClick={() => void run(() => recoverOperation(key), '上一笔结果已核对，请查看最新记录')}>
      核对上一笔操作 · {operationName(value.path, value.method)}
    </button>)}
  </aside>;
}
