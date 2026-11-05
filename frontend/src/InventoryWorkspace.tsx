import { requestId as newRequestId } from './requestId';
import { useEffect, useState } from 'react';
import { api, optionalInteger } from './api';
import { choiceSummary } from './ChoicesFields';
import { WarehouseEquip } from './WarehouseEquip';
import { InventoryQuantity } from './InventoryQuantity';
import { EquipmentForm } from './EquipmentForm';
import { Modal } from './Modal';
import type { Data, HistoryItem, InventoryItem, Run } from './types';

type Target = 'warehouse' | 'drops';
type Tab = Target | 'gaps' | 'history';
type Dialog = ({ kind: 'add'; target: Target } | { kind: 'equip'; item: InventoryItem; characterId?: string; slot?: string } | { kind: 'transfer'; item: InventoryItem }) & { requestId: string };

function equipmentName(data: Data, type: string) {
  return type === 'ring' ? '戒指' : data.workspace.slots.find(s => s.key === type)?.name ?? type;
}

export function InventoryWorkspace({ data, run, pending, error }: { data: Data; run: Run; pending: boolean; error: string }) {
  const [tab, setTab] = useState<Tab>('warehouse');
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [improvementsOnly, setImprovementsOnly] = useState(true);
  const [characterFilter, setCharacterFilter] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const daily = data.daily!;
  const path = `/accounts/${data.accountId}`;
  useEffect(() => {
    if (characterFilter && !data.matrix!.characters.some(c => c.id === characterFilter)) setCharacterFilter('');
  }, [data.matrix, characterFilter]);
  const close = () => { if (!pending) setDialog(null); };
  useEffect(() => {
    let active = true;
    if (tab === 'history') {
      setHistoryLoading(true); setHistoryError('');
      api<HistoryItem[]>(`${path}/history`).then(items => { if (active) setHistory(items); })
        .catch(e => { if (active) setHistoryError(e.message); }).finally(() => { if (active) setHistoryLoading(false); });
    }
    return () => { active = false; };
  }, [tab, data.daily, path]);

  const equip = (item: InventoryItem, characterId?: string, slot?: string) => setDialog({ kind: 'equip', item, characterId, slot, requestId: newRequestId() });
  const items = tab === 'warehouse' ? daily.warehouse : daily.drops;
  const titles = { warehouse: '共享仓库', drops: '本次团本掉落', gaps: '缺口明细', history: '操作记录' };
  const modalTitle = dialog?.kind === 'add' ? (dialog.target === 'warehouse' ? '添加共享库存' : '记录团本掉落') : dialog?.kind === 'equip' ? '从仓库给角色穿戴' : '确认获得并转入仓库';

  return <section className="daily-section" aria-label="账号仓库与团本掉落">
    <div className="daily-heading"><h2>仓库与团本备忘</h2>{data.workspace.accounts.filter(a => !a.archived).length > 1 && <p>{data.matrix!.account.name}</p>}<button className="quiet-button history-entry" id="tab-history" aria-controls="daily-history" disabled={pending} onClick={() => setTab('history')}>操作记录</button></div>
    <div className="daily-tabs" role="tablist" aria-label="仓库与备忘功能">{(Object.keys(titles) as Tab[]).filter(key => key !== 'history').map(key => <button key={key} role="tab" aria-selected={tab === key} aria-controls={`daily-${key}`} id={`tab-${key}`} disabled={pending} onClick={() => setTab(key)}>{titles[key]}</button>)}</div>
    <div className="daily-content" role="tabpanel" id={`daily-${tab}`} aria-labelledby={`tab-${tab}`}>
      {(tab === 'warehouse' || tab === 'drops') && <>
        <div className="daily-toolbar"><p className="hint">{tab === 'warehouse' ? '本账号尚未穿戴、可共享的装备。' : '临时备忘，不计库存；确认获得后再转入仓库。'}</p>
          <div className="button-row"><button className="primary" disabled={pending || !data.cycleId} onClick={() => setDialog({ kind: 'add', target: tab, requestId: newRequestId() })}>{tab === 'warehouse' ? '添加库存' : '记录掉落'}</button>
            {tab === 'drops' && daily.drops.length > 0 && <button className="danger" disabled={pending} onClick={() => { if (window.confirm('清空本账号的本次掉落备忘？共享仓库和角色装备不会改变。')) void run(() => api(`${path}/drops/clear`, 'POST', { request_id: newRequestId(), expected_version: daily.drop_version }), '已清空掉落备忘，仓库未改变'); }}>清空本次掉落</button>}
          </div>
        </div>
        {!data.cycleId && <p className="hint">请先在“周期与来源”中创建装备周期。</p>}
        {items.length === 0 ? <p className="small-empty">{tab === 'warehouse' ? '共享仓库还没有库存。' : '本次还没有记录掉落。'}</p> : <div className="daily-table-scroll"><table className="daily-table"><thead><tr><th>装备</th><th>数量</th><th>操作</th></tr></thead><tbody>{items.map(item => <tr key={item.id}>
          <td><strong>{equipmentName(data, item.equipment.equipment_type)} · {item.label} · {item.equipment.actual_grade} 品</strong>
            <details className="item-details"><summary>{choiceSummary(item.choices, item.equipment.equipment_type === 'weapon')}</summary><p>{item.equipment.source_name}{item.equipment.name && ` · ${item.equipment.name}`}</p>{item.equipment.note && <p>{item.equipment.note}</p>}</details>
          </td>
          <td><InventoryQuantity accountId={data.accountId} target={tab} item={item} run={run} pending={pending}/></td>
          <td>{tab === 'warehouse' ? <button disabled={pending || !data.matrix!.characters.length} onClick={() => equip(item)}>给角色穿戴</button> : <button disabled={pending} onClick={() => setDialog({ kind: 'transfer', item, requestId: newRequestId() })}>转入仓库</button>}</td>
        </tr>)}</tbody></table></div>}
      </>}
      {tab === 'gaps' && <>
        <p className="hint">候选不代表已分配，确认穿戴后才扣库存。</p>
        {data.matrix!.account.target_grade !== null && <p className="hint">目标 {data.matrix!.account.target_grade} 品{data.matrix!.account.comparison_mode !== 'grade' && '，同品级需有体质孔、绿字达标'}。</p>}{data.matrix!.account.target_grade === null ? <p className="small-empty">请先设置目标品级，再查看缺口明细。</p> : <>
          <div className="gap-filters">{data.matrix!.characters.length > 1 && <label>角色<select aria-label="缺口角色" value={characterFilter} onChange={e => setCharacterFilter(e.target.value)}><option value="">全部角色</option>{data.matrix!.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}<label className="check"><input type="checkbox" checked={improvementsOnly} onChange={e => setImprovementsOnly(e.target.checked)}/>只看仓库可改善</label></div>
          {daily.gaps.filter(g => (!characterFilter || g.character_id === characterFilter) && (!improvementsOnly || g.candidate_ids.length > 0)).length === 0 ? <p className="small-empty">{improvementsOnly ? '当前没有可改善项，可取消筛选查看全部缺口。' : '当前筛选下没有缺口记录。'}</p> : <div className="daily-table-scroll"><table className="daily-table"><thead><tr><th>角色 / 部位</th><th>当前装备</th><th>未达标原因</th><th>可改善的共享库存</th></tr></thead><tbody>{daily.gaps.filter(g => (!characterFilter || g.character_id === characterFilter) && (!improvementsOnly || g.candidate_ids.length > 0)).map(gap => <tr key={`${gap.character_id}:${gap.slot}`}>
            <td>{gap.character_name}<small>{gap.slot_name}</small></td><td>{gap.current_label}<small>{gap.actual_grade !== null && `${gap.actual_grade} 品`}</small></td>
            <td>{gap.reasons.join('；')}</td><td>{gap.candidate_ids.length === 0 ? <span className="hint">暂无匹配库存</span> : <div className="candidate-list">{gap.candidate_ids.map(id => {
              const item = daily.warehouse.find(item => item.id === id)!;
              return <button key={id} disabled={pending} onClick={() => equip(item, gap.character_id, gap.slot)}>{item.label} · {item.equipment.actual_grade} 品 · 可用 {item.quantity} 件</button>;
            })}</div>}</td>
          </tr>)}</tbody></table></div>}
        </>}
      </>}
      {tab === 'history' && <>{historyError && <p role="alert" className="message error">{historyError}</p>}{historyLoading ? <p className="small-empty">正在读取操作记录…</p> : history.length === 0 ? <p className="small-empty">还没有装备、仓库或掉落操作记录。</p> : <ol className="history-list">{history.map(item => <li key={item.id}><div><strong>{item.title}</strong><time>{new Date(item.created_at).toLocaleString('zh-CN')}</time></div><p>{item.summary}</p></li>)}</ol>}</>}
    </div>
    {dialog && <Modal title={modalTitle} close={close} busy={pending}>{error && <p role="alert" className="message error">{error}</p>}
      {dialog.kind === 'add' && <AddInventory key={dialog.requestId} data={data} target={dialog.target} requestId={dialog.requestId} run={run} pending={pending} close={close}/>}
      {dialog.kind === 'equip' && <WarehouseEquip key={dialog.requestId} data={data} item={dialog.item} requestId={dialog.requestId} initialCharacter={dialog.characterId} initialSlot={dialog.slot} fixedTarget={!!dialog.characterId && !!dialog.slot} run={run} pending={pending} close={close}/>}
      {dialog.kind === 'transfer' && <DropTransfer key={dialog.requestId} data={data} item={dialog.item} requestId={dialog.requestId} run={run} pending={pending} close={close}/>}
    </Modal>}
  </section>;
}

type ActionProps = { data: Data; run: Run; pending: boolean; close: () => void; requestId: string };

function AddInventory({ data, target, requestId, run, pending, close }: ActionProps & { target: Target }) {
  const [type, setType] = useState('hat');
  const [quantity, setQuantity] = useState('1');
  return <EquipmentForm data={data} type={type} run={run} pending={pending} close={close} submitLabel={target === 'warehouse' ? '保存库存' : '保存掉落'} success={target === 'warehouse' ? '已添加共享库存' : '已记录掉落'}
    leadingFields={<div className="form-grid"><label>装备部位<select aria-label="库存装备部位" value={type} onChange={e => setType(e.target.value)}>{data.workspace.slots.filter(s => s.key !== 'ring2').map(s => <option key={s.key} value={s.key === 'ring1' ? 'ring' : s.key}>{s.key === 'ring1' ? '戒指（两格通用）' : s.name}</option>)}</select></label><label>数量<input aria-label="录入数量" type="number" min="1" max="2147483647" required value={quantity} onChange={e => setQuantity(e.target.value)}/></label></div>}
    onSave={(equipment, choices) => api(`/accounts/${data.accountId}/${target}`, 'POST', { request_id: requestId, equipment, choices, quantity: optionalInteger(quantity) })}/>;
}

function DropTransfer({ data, item, requestId, run, pending, close }: ActionProps & { item: InventoryItem }) {
  const [quantity, setQuantity] = useState(String(item.quantity));
  return <form onSubmit={e => { e.preventDefault(); void run(() => api(`/accounts/${data.accountId}/drops/${item.id}/transfer`, 'POST', { request_id: requestId, expected_version: item.version, quantity: optionalInteger(quantity) }), '已转入共享仓库，并扣减对应掉落数量', close); }}><fieldset disabled={pending}>
    <p className="hint">{equipmentName(data, item.equipment.equipment_type)} · {item.label} · {item.equipment.actual_grade} 品，本次记录 {item.quantity} 件。</p>
    {item.quantity > 1 && <label>实际获得的数量<input aria-label="实际获得的数量" required type="number" min="1" max={item.quantity} value={quantity} onChange={e => setQuantity(e.target.value)}/></label>}
    <p className="replacement-note">确认这件装备已分配给自己、仍可共享。对应数量会从备忘中移除。</p>
    <div className="dialog-actions"><button type="button" onClick={close}>取消</button><button className="primary">确认转入</button></div>
  </fieldset></form>;
}
