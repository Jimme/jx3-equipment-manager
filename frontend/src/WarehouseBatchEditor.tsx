import { Fragment, useEffect, useMemo, useState } from 'react';
import { api, optionalInteger } from './api';
import { availableSources, equipmentInput, fieldsFor, socketApplicable, sourceLabel, type Fields } from './batchFields';
import { ChoicesFields, choiceSummary, draftChoices, serializeChoices, type ChoicesDraft } from './ChoicesFields';
import { requestId } from './requestId';
import type { DailyData, Data, EquipmentChoices, InventoryItem, Template, Run, Source, Workspace } from './types';

type Row = {
  id: string; original?: InventoryItem; type: string; selected: boolean; changed: boolean;
  fields: Fields; choices: ChoicesDraft; quantity: string; sourceOverride: boolean;
};
type Configuration = { equipment: Template; choices: EquipmentChoices };
type Preview = { preview_id: string; unchanged: boolean; added: number; changes: {
  row: number; quantity: number; remaining: number | null; before: Configuration | null; after: Configuration;
  final_quantity: number; merged: boolean;
}[] };

function stockRow(item: InventoryItem): Row {
  return { id: item.id, original: item, type: item.equipment.equipment_type, selected: false, changed: false,
    fields: fieldsFor(item.equipment), choices: draftChoices(item.choices),
    quantity: String(item.quantity), sourceOverride: false };
}

export function WarehouseBatchEditor({ data, run, pending, close, savedClose, onDirty }: {
  data: Data; run: Run; pending: boolean; close: () => void; savedClose: () => void; onDirty: (dirty: boolean) => void;
}) {
  const [working, setWorking] = useState(data);
  const [rows, setRows] = useState(() => data.daily!.warehouse.map(stockRow));
  const [commonSource, setCommonSource] = useState('');
  const [newType, setNewType] = useState('hat');
  const [filterGrade, setFilterGrade] = useState('');
  const [filterType, setFilterType] = useState('');
  const [whole, setWhole] = useState(false);
  const [allStock, setAllStock] = useState<Record<string, number> | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [operation, setOperation] = useState('');
  const [details, setDetails] = useState<string | null>(null);
  const endpoint = `/accounts/${data.accountId}/warehouse-batch`;
  const latest = useMemo(() => new Map(working.daily!.warehouse.map(item => [item.id, item])), [working.daily]);
  const activeSources = useMemo(() => availableSources(working), [working]);
  const sourceById = useMemo(() => new Map(working.sources.map(source => [source.id, source])), [working.sources]);
  const types = working.workspace.slots.filter(slot => slot.key !== 'ring2').map(slot => slot.key === 'ring1' ? { key: 'ring', name: '戒指' } : slot);
  const names = new Map(types.map(type => [type.key, type.name]));
  const position = new Map(types.map((type, index) => [type.key, index]));
  const ordered = [...rows].sort((a, b) => position.get(a.type)! - position.get(b.type)!);
  const visible = ordered.filter(row => !row.original || ((!filterType || row.type === filterType) && (!filterGrade || String(row.original.equipment.actual_grade) === filterGrade)));
  const selected = ordered.filter(row => row.selected);
  const changes = selected.filter(row => row.changed);
  const hidden = selected.length - visible.filter(row => row.selected).length;
  const dirty = rows.some(row => row.changed);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);

  function update(row: Row, values: Partial<Row>, mark = true) {
    setRows(old => old.map(item => item.id === row.id ? { ...item, ...(mark ? { changed: true, selected: true } : {}), ...values } : item));
  }
  function field(row: Row, values: Partial<Fields>) {
    update(row, { fields: { ...row.fields, ...values } });
  }
  function adoptCommon(row: Row, sourceId: string): Row {
    if (!sourceId || row.sourceOverride || row.fields.source_id === sourceId) return row;
    return { ...row, fields: { ...row.fields, source_id: sourceId }, changed: true };
  }
  function selectStock(ids: Set<string>, entire = false) {
    setWhole(entire);
    setAllStock(entire ? Object.fromEntries([...latest.values()].map(item => [item.id, item.version])) : null);
    setRows(old => old.map(row => {
      if (!row.original) return row;
      if (!ids.has(row.id)) return { ...row, selected: false };
      return { ...adoptCommon(row, commonSource), selected: true };
    }));
  }
  function addRows(keys: string[]) {
    setRows(old => [...old, ...keys.map(type => adoptCommon({ id: requestId(), type, selected: true, changed: true,
      fields: fieldsFor(null), choices: draftChoices(), quantity: '1', sourceOverride: false,
    }, commonSource))]);
  }
  async function reread() {
    const [daily, workspace, sources] = await Promise.all([api<DailyData>(`/accounts/${data.accountId}/daily?include_gaps=false`), api<Workspace>('/workspace'), api<Source[]>('/sources')]);
    if (!workspace.accounts.some(account => account.id === data.accountId && !account.archived)) throw new Error('账号已移除或归档，请保留草稿并返回核对');
    setWorking(old => ({ ...old, daily, workspace, sources }));
    const current = new Map(daily.warehouse.map(item => [item.id, item]));
    setRows(old => {
      const previous = new Set(old.map(row => row.id));
      return [...old.flatMap(row => row.changed || !row.original ? [row] : current.has(row.id) ? [{ ...stockRow(current.get(row.id)!), selected: row.selected }] : []),
        ...daily.warehouse.filter(item => !previous.has(item.id)).map(stockRow)];
    });
    setAllStock(null);
    setPreview(null);
  }
  function describe(config: Configuration | null) {
    if (!config) return '新库存';
    const item = config.equipment;
    const hole = socketApplicable(item.equipment_type) ? (item.has_constitution_socket ? '有孔' : '无孔') : '孔不适用';
    return <><strong>{names.get(item.equipment_type)} · {item.actual_grade} 品 · {item.source_name}</strong>
      <small>{working.workspace.cycles.find(cycle => cycle.id === item.cycle_origin_id)?.name} · {hole} · {item.green_stats_ok ? '绿字达标' : '绿字未达标'}</small>
      <small>{choiceSummary(config.choices, item.equipment_type === 'weapon')}</small>
      <small>名称 {item.name || '未填'} · 基础体质覆盖 {item.base_constitution_override ?? '按周期默认'}{item.note && ` · ${item.note}`}</small></>;
  }
  if (preview) return <section aria-label="仓库批量预览">
    {preview.unchanged ? <p>内容与当前库存相同，无需保存。</p> : <div className="batch-scroll"><table className="daily-table">
      <thead><tr><th>当前配置</th><th>更改为</th><th>件数变化</th></tr></thead>
      <tbody>{preview.changes.map(change => <tr key={change.row}><td>{describe(change.before)}</td><td>{describe(change.after)}</td>
        <td>{change.before ? `更正 ${change.quantity} 件，原配置剩 ${change.remaining} 件` : `新增 ${change.quantity} 件`}
          <small>{change.merged ? '合并计算后' : '保存后'}目标配置 {change.final_quantity} 件</small></td></tr>)}</tbody>
    </table></div>}
    <p>处理 {preview.changes.length} 行，新增 {preview.added} 件</p>
    <div className="dialog-actions"><button disabled={pending} onClick={() => setPreview(null)}>返回修改</button>
      <button disabled={pending} onClick={() => void run(reread, '已读取最新库存，请核对后重新预览', undefined, false)}>读取最新库存</button>
      <button className="primary" disabled={pending || preview.unchanged} onClick={() => void run(() => api(endpoint + '/apply', 'POST', { request_id: operation, preview_id: preview.preview_id }), '仓库已整批保存', savedClose)}>确认整批保存并备份</button></div>
  </section>;

  return <form onSubmit={event => {
    event.preventDefault();
    void run(async () => {
      const next = await api<Preview>(endpoint + '/preview', 'POST', { all_stock: whole ? allStock : null, rows: changes.map(row => ({
        item_id: row.original?.id ?? null, expected_version: row.original?.version ?? null, quantity: optionalInteger(row.quantity),
        equipment: equipmentInput(row.fields, row.type, sourceById.get(row.fields.source_id)?.actual_grade), choices: serializeChoices(row.choices),
      })) });
      setOperation(requestId()); setPreview(next);
    }, '', undefined, false);
  }}>
    <fieldset disabled={pending}>
      <div className="batch-toolbar">
        <label className="batch-common-source">统一来源<select aria-label="统一来源" value={commonSource} onChange={e => {
          const value = e.target.value;
          setCommonSource(value);
          if (value) setRows(old => old.map(row => row.selected ? adoptCommon(row, value) : row));
        }}><option value="">请选择来源</option>{activeSources.map(source => <option key={source.id} value={source.id}>{sourceLabel(working, source)}</option>)}</select></label>
        <button type="button" disabled={!latest.size} onClick={() => selectStock(new Set(latest.keys()), true)}>选择全仓</button>
        <button type="button" onClick={() => addRows(types.map(type => type.key))}>新增各部位一行</button>
        <label>新增部位<select value={newType} onChange={e => setNewType(e.target.value)}>{types.map(type => <option key={type.key} value={type.key}>{type.name}</option>)}</select></label>
        <button type="button" onClick={() => addRows([newType])}>添加一行</button></div>
      {latest.size > 0 && <details className="optional-fields"><summary>筛选和选择库存</summary><div className="batch-toolbar">
        <label>部位<select value={filterType} onChange={e => setFilterType(e.target.value)}><option value="">全部</option>{types.map(type => <option key={type.key} value={type.key}>{type.name}</option>)}</select></label>
        <label>品级<select value={filterGrade} onChange={e => setFilterGrade(e.target.value)}><option value="">全部</option>{[...new Set(working.daily!.warehouse.map(item => item.equipment.actual_grade))].sort((a, b) => b - a).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <button type="button" onClick={() => selectStock(new Set(visible.filter(row => row.original).map(row => row.id)))}>选中筛选结果</button>
        <button type="button" onClick={() => selectStock(new Set())}>取消选择库存</button>
        <button type="button" onClick={() => void run(reread, '已读取最新库存，请核对草稿', undefined, false)}>读取最新库存</button>
      </div></details>}
      <p className="hint">已选 {selected.length} 行，共 {selected.reduce((total, row) => total + (optionalInteger(row.quantity) ?? 0), 0)} 件{hidden > 0 && `，含 ${hidden} 个筛选隐藏项`}{whole && !allStock && '。已读取最新记录，请重新选择全仓。'}</p>
      <div className="batch-scroll"><table className="daily-table batch-table warehouse-batch-table">
        <colgroup><col className="batch-part-col"/><col/><col className="batch-grade-col"/><col className="batch-quantity-col"/><col className="batch-check-col"/><col className="batch-check-col"/></colgroup>
        <thead><tr><th>部位／装备</th><th>来源</th><th>品级</th><th>件数</th><th>有体质孔</th><th>绿字达标</th></tr></thead>
        <tbody>{visible.map(row => {
          const name = names.get(row.type)!;
          const item = latest.get(row.id);
          const conflict = row.original && item?.version !== row.original.version;
          const required = row.selected && row.changed;
          const source = sourceById.get(row.fields.source_id);
          const rowSources = availableSources(working, row.original?.equipment.source_id);
          return <Fragment key={row.id}><tr className={conflict ? 'batch-conflict' : undefined}>
            <th><div className="batch-inline"><label className="check batch-part"><input type="checkbox" checked={row.selected} onChange={e => {
              const checked = e.target.checked;
              if (row.original) { setWhole(false); setAllStock(null); }
              update(row, { ...(checked ? adoptCommon(row, commonSource) : row), selected: checked }, false);
            }}/>{name}</label>{row.original ? <small className="batch-current">{row.original.label} · {row.original.equipment.actual_grade}</small>
              : <button type="button" className="quiet-button batch-more" onClick={() => setRows(old => old.filter(item => item.id !== row.id))}>移除</button>}</div>
              {conflict && <small>{item ? `最新 ${item.quantity} 件` : '库存已耗尽或移除'} {item && <button type="button" onClick={() => update(row, { original: item }, false)}>核对后采用最新件数上限</button>}</small>}
            </th>
            <td><div className="batch-inline batch-source"><select aria-label={`${name}仓库来源`} required={required} value={row.fields.source_id} onChange={e => {
              update(row, { sourceOverride: true, fields: { ...row.fields, source_id: e.target.value } });
            }}><option value="">请选择来源</option>{rowSources.map(item => <option key={item.id} value={item.id}>{sourceLabel(working, item)}</option>)}</select>
              <button className="quiet-button batch-more" type="button" aria-label={`${name}更多属性`} aria-expanded={details === row.id} onClick={() => setDetails(open => open === row.id ? null : row.id)}>更多</button>
            </div></td>
            <td><div className="batch-inline"><span aria-label={`${name}仓库品级`}>{source?.actual_grade ?? '—'}</span>
              {row.sourceOverride && commonSource && <button type="button" className="quiet-button" onClick={() => update(row, adoptCommon({ ...row, sourceOverride: false, selected: true }, commonSource))}>恢复统一</button>}</div></td>
            <td><div className="batch-inline"><input aria-label={`${name}仓库件数`} required={required} type="number" min="1" max={item?.quantity ?? row.original?.quantity ?? 2147483647} value={row.quantity} onChange={e => update(row, { quantity: e.target.value })}/>{row.original && <small className="batch-current">/ {row.original.quantity}</small>}</div></td>
            <td className="batch-check">{socketApplicable(row.type) ? <input aria-label={`${name}仓库体质孔`} type="checkbox" checked={row.fields.has_constitution_socket === 'true'} onChange={e => field(row, { has_constitution_socket: String(e.target.checked) })}/> : <small>不适用</small>}</td>
            <td className="batch-check"><input aria-label={`${name}仓库绿字`} type="checkbox" checked={row.fields.green_stats_ok === 'true'} onChange={e => field(row, { green_stats_ok: String(e.target.checked) })}/></td>
          </tr>{details === row.id && <tr className="batch-expanded"><td colSpan={6}><div className="form-grid">
            <label>名称<input maxLength={100} value={row.fields.name} onChange={e => field(row, { name: e.target.value })}/></label>
            <label>基础体质覆盖<input type="number" min="0" max="2147483647" value={row.fields.base_constitution_override} onChange={e => field(row, { base_constitution_override: e.target.value })}/></label>
            <label>备注<input maxLength={2000} value={row.fields.note} onChange={e => field(row, { note: e.target.value })}/></label>
          </div><ChoicesFields value={row.choices} onChange={choices => update(row, { choices })} weapon={row.type === 'weapon'} cycleId={source?.cycle_id ?? ''} equipmentType={row.type}/></td></tr>}</Fragment>;
        })}</tbody>
      </table></div>
    </fieldset>
    <div className="dialog-actions"><button type="button" disabled={pending} onClick={close}>取消</button>
      <button className="primary" disabled={pending || !changes.length || (whole && !allStock)} >预览整批更改</button></div>
  </form>;
}
