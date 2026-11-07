import { requestId as newRequestId } from './requestId';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, isMutationUncertain, optionalInteger } from './api';
import { choiceSummary } from './ChoicesFields';
import { WarehouseEquip } from './WarehouseEquip';
import { InventoryQuantity } from './InventoryQuantity';
import { EquipmentForm } from './EquipmentForm';
import { Modal } from './Modal';
import type { Data, HistoryItem, InventoryItem, Run } from './types';

export type StockTarget = 'warehouse' | 'drops';
export type InventorySelection = { tab: StockTarget | 'gaps' | 'history'; equipmentType?: string };
export type InventoryDialog = ({ kind: 'add'; target: StockTarget; equipmentType?: string } | { kind: 'edit'; target: StockTarget; item: InventoryItem } | { kind: 'equip'; item: InventoryItem; characterId?: string; slot?: string } | { kind: 'transfer'; item: InventoryItem }) & { requestId: string };

function equipmentName(data: Data, type: string) {
  return type === 'ring' ? '戒指' : data.workspace.slots.find(s => s.key === type)?.name ?? type;
}

export function InventoryWorkspace({ data, run, pending, error, selection, setSelection, dialog, setDialog }: {
  data: Data; run: Run; pending: boolean; error: string;
  selection: InventorySelection | null; setSelection: (selection: InventorySelection | null) => void;
  dialog: InventoryDialog | null; setDialog: (dialog: InventoryDialog | null) => void;
}) {
  const tab = selection?.tab;
  const equipmentType = selection?.equipmentType;
  const [improvementsOnly, setImprovementsOnly] = useState(true);
  const [characterFilter, setCharacterFilter] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const daily = data.daily!;
  const path = `/accounts/${data.accountId}`;
  useEffect(() => {
    if (characterFilter && !data.matrix!.characters.some(c => c.id === characterFilter)) setCharacterFilter('');
  }, [data.matrix, characterFilter]);
  const dismiss = () => { setDirty(false); setDialog(null); };
  const close = () => {
    if (pending) return;
    if (dialog && isMutationUncertain(dialog.requestId)) {
      window.alert('这笔操作的结果尚未核对，请先原样重试保存，避免重新录入时重复增加数量。'); return;
    }
    if (dirty && !window.confirm('本次装备输入还没保存，关闭会丢弃。确定关闭？')) return;
    dismiss();
  };
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);
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
  const stockTab = tab === 'warehouse' || tab === 'drops' ? tab : null;
  const stock = stockTab ? daily[stockTab] : [];
  const items = equipmentType ? stock.filter(item => item.equipment.equipment_type === equipmentType) : stock;
  const warehouseById = useMemo(() => new Map(daily.warehouse.map(item => [item.id, item])), [daily.warehouse]);
  const visibleGaps = useMemo(() => tab === 'gaps' ? daily.gaps.filter(gap =>
    (!characterFilter || gap.character_id === characterFilter) && (!improvementsOnly || gap.candidate_ids.length > 0)
  ) : [], [tab, daily.gaps, characterFilter, improvementsOnly]);
  const stockTitle = stockTab === 'warehouse' ? '共享仓库' : '团本掉落';
  const modalTitle = dialog?.kind === 'add' ? (dialog.target === 'warehouse' ? '添加共享库存' : '记录团本掉落') + (dialog.equipmentType ? ` · ${equipmentName(data, dialog.equipmentType)}` : '') : dialog?.kind === 'edit' ? `更正${dialog.target === 'warehouse' ? '库存' : '掉落'} · ${equipmentName(data, dialog.item.equipment.equipment_type)}` : dialog?.kind === 'equip' ? '从仓库给角色穿戴' : '确认获得并转入仓库';

  return <section id="inventory" className="daily-section" aria-label="账号仓库与团本掉落">
    <div className="daily-heading">
      {stockTab && <h3>{stockTitle}{equipmentType && ` · ${equipmentName(data, equipmentType)}`}</h3>}
      {stockTab && <div className="button-row">{equipmentType && <button className="primary" disabled={pending || !data.cycleId} onClick={() => setDialog({ kind: 'add', target: stockTab, equipmentType, requestId: newRequestId() })}>{stockTab === 'warehouse' ? '添加此部位库存' : '记录此部位掉落'}</button>}
            {equipmentType && <button disabled={pending} onClick={() => setSelection({ tab: stockTab })}>查看全部部位</button>}
            {stockTab === 'drops' && !equipmentType && daily.drops.length > 0 && <button className="danger" disabled={pending} onClick={() => { if (window.confirm('清空本账号的本次掉落备忘？共享仓库和角色装备不会改变。')) void run(() => api(`${path}/drops/clear`, 'POST', { request_id: newRequestId(), expected_version: daily.drop_version }), '已清空掉落备忘，仓库未改变'); }}>清空本次掉落</button>}
      </div>}
      <div className="menu-tabs" aria-label="辅助信息">{([['gaps', '缺口明细'], ['history', '操作记录']] as const).map(([key, label]) => <button key={key} aria-pressed={tab === key} aria-controls="daily-panel" disabled={pending} onClick={() => setSelection(tab === key ? null : { tab: key })}>{label}</button>)}</div>
      {stockTab && <button className="quiet-button" disabled={pending} onClick={() => setSelection(null)}>收起明细</button>}
    </div>
    <div className="daily-content" id="daily-panel" role="region" aria-label="装备明细" hidden={!tab}>
      {(tab === 'warehouse' || tab === 'drops') && <>
        {!data.cycleId && <p className="hint">请先在“周期与来源”中创建装备周期。</p>}
        {items.length === 0 ? <p className="small-empty">{tab === 'warehouse' ? '共享仓库还没有库存。' : '本次还没有记录掉落。'}</p> : <div className="daily-table-scroll"><table className="daily-table"><thead><tr><th>装备</th><th>数量</th><th>操作</th></tr></thead><tbody>{items.map(item => <tr key={item.id}>
          <td><strong>{!equipmentType && `${equipmentName(data, item.equipment.equipment_type)} · `}{item.label} · {item.equipment.actual_grade} 品</strong>
            <details className="item-details"><summary>{choiceSummary(item.choices, item.equipment.equipment_type === 'weapon')}</summary><p>{item.equipment.source_name}{item.equipment.name && ` · ${item.equipment.name}`}</p>{item.equipment.note && <p>{item.equipment.note}</p>}</details>
          </td>
          <td><InventoryQuantity accountId={data.accountId} target={tab} item={item} run={run} pending={pending}/></td>
          <td><div className="button-row">
            <button disabled={pending} onClick={() => setDialog({ kind: 'edit', target: stockTab!, item, requestId: newRequestId() })}>编辑</button>
            {tab === 'warehouse' ? <button disabled={pending || !data.matrix!.characters.length} onClick={() => equip(item)}>给角色穿戴</button> : <button disabled={pending} onClick={() => setDialog({ kind: 'transfer', item, requestId: newRequestId() })}>转入仓库</button>}
            <button className="danger" disabled={pending} onClick={() => {
              const label = tab === 'warehouse' ? '库存' : '掉落';
              if (!window.confirm(`删除${label}「${item.label} · ${item.equipment.actual_grade} 品」的 ${item.quantity} 件记录？`)) return;
              void run(() => api(`${path}/${tab}/${item.id}/quantity`, 'POST', {
                request_id: newRequestId(), expected_version: item.version, quantity: 0,
              }), `已删除${label}`);
            }}>{tab === 'warehouse' ? '删除库存' : '删除掉落'}</button>
          </div></td>
        </tr>)}</tbody></table></div>}
      </>}
      {tab === 'gaps' && <>
        {data.matrix!.account.target_grade === null ? <p className="small-empty">请先设置目标品级，再查看缺口明细。</p> : <>
          <div className="gap-filters">{data.matrix!.characters.length > 1 && <label>角色<select aria-label="缺口角色" value={characterFilter} onChange={e => setCharacterFilter(e.target.value)}><option value="">全部角色</option>{data.matrix!.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}<label className="check"><input type="checkbox" checked={improvementsOnly} onChange={e => setImprovementsOnly(e.target.checked)}/>只看仓库可改善</label><span className="hint">确认穿戴后才扣库存</span></div>
          {visibleGaps.length === 0 ? <p className="small-empty">{improvementsOnly ? '当前没有可改善项，可取消筛选查看全部缺口。' : '当前筛选下没有缺口记录。'}</p> : <div className="daily-table-scroll"><table className="daily-table"><thead><tr><th>角色 / 部位</th><th>当前装备</th><th>未达标原因</th><th>可改善的共享库存</th></tr></thead><tbody>{visibleGaps.map(gap => <tr key={`${gap.character_id}:${gap.slot}`}>
            <td>{gap.character_name}<small>{gap.slot_name}</small></td><td>{gap.current_label}<small>{gap.actual_grade !== null && `${gap.actual_grade} 品`}</small></td>
            <td>{gap.reasons.join('；')}</td><td>{gap.candidate_ids.length === 0 ? <span className="hint">暂无匹配库存</span> : <div className="candidate-list">{gap.candidate_ids.map(id => {
              const item = warehouseById.get(id);
              if (!item) return null;
              return <button key={id} disabled={pending} onClick={() => equip(item, gap.character_id, gap.slot)}>{item.label} · {item.equipment.actual_grade} 品 · 可用 {item.quantity} 件</button>;
            })}</div>}</td>
          </tr>)}</tbody></table></div>}
        </>}
      </>}
      {tab === 'history' && <>{historyError && <p role="alert" className="message error">{historyError}</p>}{historyLoading ? <p className="small-empty">正在读取操作记录…</p> : history.length === 0 ? <p className="small-empty">还没有装备、仓库或掉落操作记录。</p> : <ol className="history-list">{history.map(item => <li key={item.id}><div><strong>{item.title}</strong><time>{new Date(item.created_at).toLocaleString('zh-CN')}</time></div><p>{item.summary}</p></li>)}</ol>}</>}
    </div>
    {dialog && <Modal title={modalTitle} close={close} busy={pending} compact>{error && <p role="alert" className="message error">{error}</p>}
      {dialog.kind === 'add' && <AddInventory key={dialog.requestId} data={data} target={dialog.target} equipmentType={dialog.equipmentType} requestId={dialog.requestId} run={run} pending={pending} close={close} savedClose={dismiss} onDirty={setDirty}/>}
      {dialog.kind === 'edit' && <EditInventory key={dialog.requestId} data={data} target={dialog.target} item={dialog.item} requestId={dialog.requestId} run={run} pending={pending} close={close} savedClose={dismiss} onDirty={setDirty} actions={<button type="button" disabled={pending || (dialog.target === 'warehouse' && !data.matrix!.characters.length)} onClick={() => {
        if (isMutationUncertain(dialog.requestId)) { window.alert('请先原样重试保存，核对上一笔结果后再切换操作。'); return; }
        if (dirty && !window.confirm('切换操作会丢弃当前更正输入，确定继续？')) return;
        setDirty(false);
        setDialog({ kind: dialog.target === 'warehouse' ? 'equip' : 'transfer', item: dialog.item, requestId: newRequestId() });
      }}>{dialog.target === 'warehouse' ? '给角色穿戴' : '转入仓库'}</button>}/>}
      {dialog.kind === 'equip' && <WarehouseEquip key={dialog.requestId} data={data} item={dialog.item} requestId={dialog.requestId} initialCharacter={dialog.characterId} initialSlot={dialog.slot} fixedTarget={!!dialog.characterId && !!dialog.slot} run={run} pending={pending} close={close} savedClose={dismiss} onDirty={setDirty}/>}
      {dialog.kind === 'transfer' && <DropTransfer key={dialog.requestId} data={data} item={dialog.item} requestId={dialog.requestId} run={run} pending={pending} close={close} savedClose={dismiss} onDirty={setDirty}/>}
    </Modal>}
  </section>;
}

type ActionProps = { data: Data; run: Run; pending: boolean; close: () => void; savedClose: () => void; onDirty: (dirty: boolean) => void; requestId: string };

function AddInventory({ data, target, equipmentType, requestId, run, pending, close, savedClose, onDirty }: ActionProps & { target: StockTarget; equipmentType?: string }) {
  const [type, setType] = useState(equipmentType ?? 'hat');
  const [quantity, setQuantity] = useState('1');
  const [specificDirty, setSpecificDirty] = useState(false);
  return <EquipmentForm data={data} type={type} run={run} pending={pending} close={close} savedClose={savedClose} onDirty={onDirty} onSpecificDirty={setSpecificDirty} extraDirty={quantity !== '1' || type !== (equipmentType ?? 'hat')} submitLabel={target === 'warehouse' ? '保存库存' : '保存掉落'} success={target === 'warehouse' ? '已添加共享库存' : '已记录掉落'}
    leadingFields={<div className="form-grid">{!equipmentType && <label>装备部位<select aria-label="库存装备部位" value={type} onChange={e => {
      if (specificDirty && !window.confirm('更换部位会清除本次部位专属属性和附加配置，来源与品级会保留。确定继续？')) return;
      setType(e.target.value);
    }}>{data.workspace.slots.filter(s => s.key !== 'ring2').map(s => <option key={s.key} value={s.key === 'ring1' ? 'ring' : s.key}>{s.key === 'ring1' ? '戒指（两格通用）' : s.name}</option>)}</select></label>}<label>数量<input aria-label="录入数量" type="number" min="1" max="2147483647" required value={quantity} onChange={e => setQuantity(e.target.value)}/></label></div>}
    onSave={(equipment, choices) => api(`/accounts/${data.accountId}/${target}`, 'POST', { request_id: requestId, equipment, choices, quantity: optionalInteger(quantity) })}/>;
}

function EditInventory({ data, target, item, requestId, run, pending, close, savedClose, onDirty, actions }: ActionProps & { target: StockTarget; item: InventoryItem; actions: ReactNode }) {
  const [quantity, setQuantity] = useState(String(item.quantity));
  const [baseline, setBaseline] = useState(item);
  const [latest, setLatest] = useState<InventoryItem | null>(null);
  return <EquipmentForm data={data} type={item.equipment.equipment_type} current={item.equipment} initialChoices={item.choices}
    allowCopy={false} run={run} pending={pending} close={close} savedClose={savedClose} onDirty={onDirty}
    extraDirty={quantity !== String(item.quantity)} submitLabel="保存更正" success="装备配置已更正"
    leadingFields={<>
      <div className="equipment-copy"><span>此配置共 {baseline.quantity} 件</span><button type="button" disabled={pending} onClick={() => void run(async () => {
        const fresh = await api<{ warehouse: InventoryItem[]; drops: InventoryItem[] }>(`/accounts/${data.accountId}/daily?skip_unconfigured_gaps=true`);
        const found = fresh[target].find(row => row.id === baseline.id);
        if (!found || !found.quantity) throw new Error('此记录已移除，请保留输入并返回核对最新列表');
        setLatest(found);
      }, '', undefined, false)}>读取最新记录核对</button></div>
      {latest && <div className="replacement-note">最新记录为 {latest.label} · {latest.equipment.actual_grade} 品 · {latest.quantity} 件，{choiceSummary(latest.choices, latest.equipment.equipment_type === 'weapon')}。本次输入保留。
        <button type="button" disabled={pending} onClick={() => { setBaseline(latest); setLatest(null); }}>以此版本继续更正</button>
      </div>}
      <label>本次更正件数<input aria-label="本次更正件数" required type="number" min="1" max={baseline.quantity} value={quantity} onChange={e => setQuantity(e.target.value)}/></label>
      <p className="hint">默认更正全部数量；指定较少件数时，余量保留原配置。</p>
    </>}
    actions={actions}
    onSave={(equipment, choices) => api(`/accounts/${data.accountId}/${target}/${baseline.id}/edit`, 'POST', {
      request_id: requestId, expected_version: baseline.version, quantity: optionalInteger(quantity), equipment, choices,
    })}/>;
}

function DropTransfer({ data, item, requestId, run, pending, close, savedClose, onDirty }: ActionProps & { item: InventoryItem }) {
  const [quantity, setQuantity] = useState('1');
  useEffect(() => { onDirty(quantity !== '1'); return () => onDirty(false); }, [quantity, onDirty]);
  return <form onSubmit={e => { e.preventDefault(); void run(() => api(`/accounts/${data.accountId}/drops/${item.id}/transfer`, 'POST', { request_id: requestId, expected_version: item.version, quantity: optionalInteger(quantity) }), '已转入共享仓库，并扣减对应掉落数量', savedClose); }}><fieldset disabled={pending}>
    <p className="hint">{equipmentName(data, item.equipment.equipment_type)} · {item.label} · {item.equipment.actual_grade} 品，本次记录 {item.quantity} 件。</p>
    {item.quantity > 1 && <label>实际获得的数量<input aria-label="实际获得的数量" required type="number" min="1" max={item.quantity} value={quantity} onChange={e => setQuantity(e.target.value)}/></label>}
    <p className="replacement-note">确认这件装备已分配给自己、仍可共享。对应数量会从备忘中移除。</p>
    <div className="dialog-actions"><button type="button" onClick={close}>取消</button><button className="primary">确认转入</button></div>
  </fieldset></form>;
}
