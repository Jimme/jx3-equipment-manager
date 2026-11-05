import { requestId as newRequestId } from './requestId';
import { useState } from 'react';
import { api, optionalInteger } from './api';
import type { InventoryItem, Run } from './types';

export function InventoryQuantity({ accountId, target, item, pending, run }: { accountId: string; target: 'warehouse' | 'drops'; item: InventoryItem; pending: boolean; run: Run }) {
  const [editor, setEditor] = useState<{ quantity: string; version: number; requestId: string } | null>(null);
  if (!editor) return <button className="quantity-button" title="点击修改数量" aria-label={`修改${item.label}数量`} disabled={pending} onClick={() => setEditor({ quantity: String(item.quantity), version: item.version, requestId: newRequestId() })}>{item.quantity} 件 <span aria-hidden="true">✎</span></button>;
  return <form className="inline-quantity" onSubmit={e => {
    e.preventDefault();
    if (Number(editor.quantity) === item.quantity && editor.version === item.version) { setEditor(null); return; }
    if (Number(editor.quantity) === 0 && !window.confirm(target === 'warehouse' ? '移除此项库存？角色穿戴不变。' : '移除此条掉落？共享仓库不变。')) return;
    void run(() => api(`/accounts/${accountId}/${target}/${item.id}/quantity`, 'POST', { request_id: editor.requestId, expected_version: editor.version, quantity: optionalInteger(editor.quantity) }), '数量已更新', () => setEditor(null));
  }}><input aria-label="实际数量" autoFocus required type="number" min="0" max="2147483647" value={editor.quantity} disabled={pending} onChange={e => setEditor({ ...editor, quantity: e.target.value })} onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); setEditor(null); } }}/><button aria-label="保存数量" title="保存数量" disabled={pending}>✓</button><button type="button" aria-label="取消修改数量" title="取消" disabled={pending} onClick={() => setEditor(null)}>×</button></form>;
}
