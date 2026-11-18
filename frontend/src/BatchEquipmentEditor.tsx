import { Fragment, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { availableSources, equipmentInput, fieldsFor, socketApplicable, sourceLabel, type Fields } from './batchFields';
import { ChoicesFields, choiceSummary, draftChoices, serializeChoices, type ChoicesDraft } from './ChoicesFields';
import { requestId } from './requestId';
import type { Character, DailyData, Data, Matrix, Run, Source, Template, Workspace } from './types';

type Action = 'keep' | 'edit' | 'new' | 'warehouse' | 'clear';
type Row = { slot: string; selected: boolean; action: Action; version: number; fields: Fields; sourceOverride: boolean; choices: ChoicesDraft; warehouseId: string; stockVersion: number };
type Preview = { preview_id?: string; unchanged: boolean; changes: { slot: string; name: string; action: string; before: string; after: string; before_equipment?: Template; after_equipment?: Template; before_choices?: Parameters<typeof choiceSummary>[0]; after_choices?: Parameters<typeof choiceSummary>[0]; warehouse_id: string | null }[]; deductions: Record<string, number> };

export function BatchEquipmentEditor({ data, character, run, pending, close, savedClose, onDirty }: {
  data: Data; character: Character; run: Run; pending: boolean; close: () => void; savedClose: () => void; onDirty: (dirty: boolean) => void;
}) {
  const [working, setWorking] = useState(data);
  const currentCell = (slot: string) => working.matrix!.cells[character.id][slot];
  const initialRow = (slot: string): Row => {
    const cell = currentCell(slot);
    return { slot, selected: false, action: 'keep', version: cell.version, fields: fieldsFor(cell.equipment),
      sourceOverride: false, choices: draftChoices(cell.choices), warehouseId: '', stockVersion: 0 };
  };
  const [rows, setRows] = useState<Row[]>(() => data.workspace.slots.map(slot => initialRow(slot.key)));
  const [uniform, setUniform] = useState({ source: '', socket: '', green: '' });
  const activeSources = useMemo(() => availableSources(working), [working]);
  const sourceById = useMemo(() => new Map(working.sources.map(source => [source.id, source])), [working.sources]);
  const [copySlot, setCopySlot] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [operationId, setOperationId] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const dirty = rows.some(row => row.action !== 'keep');
  const hasChanges = rows.some(row => row.selected && row.action !== 'keep');
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const warehouse = useMemo(() => new Map(working.daily!.warehouse.map(item => [item.id, item])), [working.daily]);
  const chosen = rows.filter(row => row.selected);
  const hasEquipment = rows.some(row => !!currentCell(row.slot).equipment);
  const uniformScope = chosen.length
    ? `套用 ${chosen.filter(row => !row.sourceOverride && row.action !== 'warehouse' && row.action !== 'clear').length} 个勾选部位`
    : dirty ? '先勾选要套用的部位' : hasEquipment ? '套用已穿戴部位' : '套用全部 12 个部位';
  const typeFor = (slot: string) => slot.startsWith('ring') ? 'ring' : slot;
  const hasSocket = (row: Row) => socketApplicable(typeFor(row.slot));
  function update(slot: string, values: Partial<Row>) {
    setRows(old => old.map(row => row.slot === slot ? { ...row, ...values } : row));
  }
  function field(row: Row, values: Partial<Fields>) {
    update(row.slot, { ...manualRow(row), selected: true, fields: { ...row.fields, ...values } });
  }
  function changeAction(row: Row, action: Action) {
    const fresh = initialRow(row.slot);
    if (action === 'new') {
      update(row.slot, { ...fresh, selected: true, action, fields: { ...fieldsFor(null), source_id: uniform.source }, choices: draftChoices() });
    } else {
      update(row.slot, { ...fresh, selected: action !== 'keep', action });
    }
  }
  function manualRow(row: Row): Row {
    if (row.action !== 'keep') return row;
    return { ...row, action: currentCell(row.slot).equipment ? 'edit' : 'new' };
  }
  function chooseScope(scope: 'edit' | 'new' | 'empty') {
    if (scope === 'new' && dirty && !window.confirm('整套换装会重新开始录入所有部位，确定替换现有草稿？')) return;
    setRows(old => old.map(row => {
      const equipped = !!currentCell(row.slot).equipment;
      if (scope === 'edit' && !equipped || scope === 'empty' && equipped) return { ...row, selected: false };
      if (scope !== 'new') return selectRow(row, true);
      return { ...initialRow(row.slot), action: 'new', selected: true,
        fields: { ...fieldsFor(null), source_id: uniform.source }, choices: draftChoices() };
    }));
  }
  function adoptSource(row: Row, id: string): Row {
    if (!id || row.sourceOverride || row.action === 'warehouse' || row.action === 'clear' || row.fields.source_id === id) return row;
    return { ...manualRow(row), fields: { ...row.fields, source_id: id } };
  }
  function selectRow(row: Row, selected: boolean): Row {
    return { ...(selected && !row.selected ? adoptSource(row, uniform.source) : row), selected };
  }
  function setUniformSource(id: string) {
    setUniform(old => ({ ...old, source: id }));
    if (!id) return;
    const autoSelect = !chosen.length && !dirty;
    setRows(old => old.map(row => {
      const selected = row.selected || (autoSelect && (!hasEquipment || !!currentCell(row.slot).equipment));
      return selected ? { ...adoptSource(row, id), selected } : row;
    }));
  }
  function applyUniform(copy = false) {
    if (!chosen.length) return;
    if (chosen.some(row => row.action === 'warehouse' || row.action === 'clear')) {
      window.alert('统一设置用于手动录入或纠错，请取消勾选仓库穿戴和清除行。'); return;
    }
    const copied = rows.find(row => row.slot === copySlot);
    if (copy && !copied) return;
    setRows(old => old.map(original => {
      if (!original.selected) return original;
      const row = manualRow(original);
      const fields = { ...row.fields };
      if (copy && copied) {
        fields.source_id = copied.fields.source_id;
        fields.has_constitution_socket = copied.fields.has_constitution_socket;
        fields.green_stats_ok = copied.fields.green_stats_ok;
      } else {
        if (uniform.socket) fields.has_constitution_socket = uniform.socket;
        if (uniform.green) fields.green_stats_ok = uniform.green;
      }
      return { ...row, fields, sourceOverride: copy || row.sourceOverride };
    }));
  }
  async function reread() {
    const base = `/accounts/${working.accountId}`;
    const [matrix, daily, workspace, sources] = await Promise.all([
      api<Matrix>(base + '/matrix'), api<DailyData>(base + '/daily?include_gaps=false'), api<Workspace>('/workspace'), api<Source[]>('/sources'),
    ]);
    if (!matrix.characters.some(item => item.id === character.id)) throw new Error('角色已移除，请保留草稿并返回核对');
    setWorking(old => ({ ...old, matrix, daily, workspace, sources }));
    setPreview(null);
  }
  function buildRows() {
    return rows.filter(row => row.selected && row.action !== 'keep').map(row => {
      const base = { slot: row.slot, action: row.action, expected_version: row.version };
      if (row.action === 'clear') return base;
      const choices = serializeChoices(row.choices);
      if (row.action === 'warehouse') return { ...base, warehouse_id: row.warehouseId, expected_stock_version: row.stockVersion, choices };
      const equipment = equipmentInput(row.fields, typeFor(row.slot), sourceById.get(row.fields.source_id)?.actual_grade);
      return { ...base, equipment, choices };
    });
  }
  if (preview) return <section aria-label="角色批量预览">
    <p className="hint">仅应用下列改动。保存前备份，任一冲突则整批不保存。</p>
    {preview.unchanged ? <p>内容与当前记录相同，无需保存。</p> : <div className="config-scroll"><table className="daily-table">
      <thead><tr><th>部位</th><th>操作</th><th>当前</th><th>更改为</th></tr></thead>
      <tbody>{preview.changes.map(change => <tr key={change.slot}><th>{change.name}</th>
        <td>{({ edit: '纠错', new: '手动换装', warehouse: '库存穿戴', clear: '清除' } as Record<string, string>)[change.action]}{change.warehouse_id && ' · 库存扣 1 件'}</td>
        <td>{change.before}{change.before_equipment && <small>{working.workspace.cycles.find(c => c.id === change.before_equipment!.cycle_origin_id)?.name} · {change.before_equipment.source_name} {change.before_equipment.note}</small>}
          {change.before_equipment && <small>名称：{change.before_equipment.name || '未填写'}；基础体质覆盖：{change.before_equipment.base_constitution_override == null ? '使用原周期默认（缺失为未知）' : change.before_equipment.base_constitution_override}</small>}
          {change.before_choices && <small>{choiceSummary(change.before_choices, typeFor(change.slot) === 'weapon')}</small>}</td>
        <td>{change.after}{change.after_equipment && <small>{working.workspace.cycles.find(c => c.id === change.after_equipment!.cycle_origin_id)?.name} · {change.after_equipment.source_name} {change.after_equipment.note}</small>}
          {change.after_equipment && <small>名称：{change.after_equipment.name || '未填写'}；基础体质覆盖：{change.after_equipment.base_constitution_override == null ? '使用原周期默认（缺失为未知）' : change.after_equipment.base_constitution_override}</small>}
          {change.after_choices && <small>{choiceSummary(change.after_choices, typeFor(change.slot) === 'weapon')}</small>}</td>
      </tr>)}</tbody></table></div>}
    <p>合计修改 {preview.changes.length} 个部位，库存取出 {Object.values(preview.deductions).reduce((a, b) => a + b, 0)} 件。</p>
    <div className="dialog-actions"><button disabled={pending} onClick={() => setPreview(null)}>返回修改</button>
      <button disabled={pending} onClick={() => void run(reread, '最新状态已读取，草稿保留，请核对后重新预览', undefined, false)}>读取最新状态核对</button>
      <button className="primary" disabled={pending || preview.unchanged} onClick={() => void run(() => api(
        `/accounts/${working.accountId}/characters/${character.id}/equipment-batch/apply`, 'POST', { request_id: operationId, preview_id: preview.preview_id },
      ), '角色装备已整批保存', savedClose)}>确认整批保存并备份</button></div>
  </section>;
  return <form onSubmit={event => {
    event.preventDefault();
    void run(async () => {
      const result = await api<Preview>(`/accounts/${working.accountId}/characters/${character.id}/equipment-batch/preview`, 'POST', { rows: buildRows() });
      setOperationId(requestId()); setPreview(result);
    }, '', undefined, false);
  }}>
    <div className="batch-toolbar">
      <label className="batch-common-source">统一来源<select aria-label="批量统一来源" aria-describedby="batch-source-scope" disabled={pending} value={uniform.source} onChange={e => setUniformSource(e.target.value)}><option value="">请选择来源</option>{activeSources.map(source => <option key={source.id} value={source.id}>{sourceLabel(working, source)}</option>)}</select></label>
      <span id="batch-source-scope" className="hint">{uniformScope}</span>
      <button type="button" disabled={pending} onClick={() => chooseScope('new')}>整套换装</button>
      <button type="button" disabled={pending} onClick={() => chooseScope('edit')}>选已穿戴</button>
      <button type="button" disabled={pending} onClick={() => chooseScope('empty')}>选空部位</button>
      <button type="button" disabled={pending} onClick={() => setRows(old => old.map(row => selectRow(row, !old.every(item => item.selected))))}>全选／取消全选</button>
      <details className="batch-tools menu-disclosure"><summary>对勾选行统一设置 · {chosen.length} 行</summary>
      <div className="batch-toolbar">
        <label>统一体质孔<select value={uniform.socket} onChange={e => setUniform({ ...uniform, socket: e.target.value })}><option value="">保留</option><option value="true">有</option><option value="false">无</option></select></label>
        <label>统一绿字<select value={uniform.green} onChange={e => setUniform({ ...uniform, green: e.target.value })}><option value="">保留</option><option value="true">达标</option><option value="false">未达标</option></select></label>
        <button type="button" disabled={pending || !chosen.length || (!uniform.socket && !uniform.green)} onClick={() => applyUniform()}>应用到勾选行</button>
      </div>
      <div className="batch-toolbar"><label>复制通用字段自<select aria-label="复制行字段" value={copySlot} onChange={e => setCopySlot(e.target.value)}><option value="">请选择部位</option>{working.workspace.slots.map(slot => <option key={slot.key} value={slot.key}>{slot.name}</option>)}</select></label>
        <button type="button" disabled={pending || !chosen.length || !copySlot} onClick={() => applyUniform(true)}>复制到勾选行</button></div>
      <p className="hint">复制原周期、来源、品级、孔和绿字；附魔、五彩石及单件体质仍逐行核对。</p>
      <button type="button" disabled={pending} onClick={() => void run(reread, '已读取最新状态，草稿仍保留，请逐行核对差异', undefined, false)}>读取最新状态核对</button>
      </details>
    </div>
    <fieldset disabled={pending}><div className="batch-scroll"><table className="daily-table batch-table">
      <colgroup><col className="batch-part-col"/><col className="batch-action-col"/><col/><col className="batch-grade-col"/><col className="batch-check-col"/><col className="batch-check-col"/></colgroup>
      <thead><tr><th>部位／装备</th><th>操作</th><th>来源</th><th>品级</th><th>有体质孔</th><th>绿字达标</th></tr></thead>
      <tbody>{rows.map(row => {
        const name = working.workspace.slots.find(slot => slot.key === row.slot)!.name;
        const cell = currentCell(row.slot);
        const manual = row.action !== 'warehouse' && row.action !== 'clear';
        const required = row.selected && row.action !== 'keep';
        const stock = warehouse.get(row.warehouseId);
        const source = sourceById.get(row.fields.source_id);
        const rowSources = availableSources(working, row.action !== 'new' ? cell.equipment?.source_id : undefined);
        const conflict = row.action !== 'keep' && (cell.version !== row.version || (row.action === 'warehouse' && (!stock || stock.version !== row.stockVersion)));
        return <Fragment key={row.slot}><tr className={conflict ? 'batch-conflict' : undefined}>
          <th><div className="batch-inline"><label className="check batch-part"><input aria-label={`勾选${name}`} type="checkbox" checked={row.selected} onChange={e => update(row.slot, selectRow(row, e.target.checked))}/>{name}</label>
            {cell.equipment && <small className="batch-current">{cell.label} · {cell.equipment.actual_grade}</small>}</div></th>
          <td><select aria-label={`${name}批量操作`} value={row.action} onChange={e => {
            if (row.action !== 'keep' && !window.confirm('切换本行操作会重置本行输入，确定继续？')) return;
            changeAction(row, e.target.value as Action);
            if (e.target.value === 'clear') setExpanded(null);
          }}><option value="keep">保持原样</option><option value="edit" disabled={!cell.equipment}>纠正记录</option><option value="new">{cell.equipment ? '手动换装' : '手动录入'}</option><option value="warehouse" disabled={!working.daily!.warehouse.some(item => item.equipment.equipment_type === typeFor(row.slot))}>从仓库穿戴</option><option value="clear" disabled={!cell.equipment}>清除穿戴</option></select>
            {conflict && <p className="error">本行记录或库存版本已变化，草稿仍保留。{stock && <span>最新可用 {stock.quantity} 件。</span>}
              <button type="button" onClick={() => update(row.slot, { version: cell.version, stockVersion: stock?.version ?? row.stockVersion })}>保留输入并采用最新版本</button></p>}
          </td>
          <td>{row.action !== 'clear' && <div className="batch-inline batch-source">
            {row.action === 'warehouse' && <select aria-label={`${name}批量库存`} required={required} value={row.warehouseId} onChange={e => {
              const item = warehouse.get(e.target.value);
              update(row.slot, { warehouseId: e.target.value, stockVersion: item?.version ?? 0, fields: fieldsFor(item?.equipment ?? null), choices: draftChoices(item?.choices) });
            }}><option value="">请选择库存</option>{working.daily!.warehouse.filter(item => item.equipment.equipment_type === typeFor(row.slot)).map(item => <option key={item.id} value={item.id}>{item.label} · {item.equipment.actual_grade} 品 · {item.quantity} 件{[item.choices.enchant, item.choices.rainbow].some(choice => choice.state !== 'unknown') ? ` · ${choiceSummary(item.choices, typeFor(row.slot) === 'weapon')}` : ''}</option>)}</select>}
            {manual && <>
              <select aria-label={`${name}批量来源`} required={required} value={row.fields.source_id} onChange={e => {
                update(row.slot, { ...manualRow(row), selected: true, sourceOverride: true, fields: { ...row.fields, source_id: e.target.value } });
              }}><option value="">请选择来源</option>{rowSources.map(item => <option key={item.id} value={item.id}>{sourceLabel(working, item)}</option>)}</select>
            </>}
            <button className="quiet-button batch-more" type="button" aria-label={`${name}更多属性`} aria-expanded={expanded === row.slot} onClick={() => setExpanded(open => open === row.slot ? null : row.slot)}>更多</button>
          </div>}</td>
          <td>{manual && <div className="batch-inline"><span aria-label={`${name}批量品级`}>{source?.actual_grade ?? '—'}</span>
            {row.sourceOverride && uniform.source && <button type="button" className="quiet-button" onClick={() => update(row.slot, { ...adoptSource({ ...row, sourceOverride: false }, uniform.source), selected: true })}>恢复统一</button>}</div>}</td>
          <td className="batch-check">{manual && (hasSocket(row) ? <input type="checkbox" aria-label={`${name}体质孔`} checked={row.fields.has_constitution_socket === 'true'} onChange={e => field(row, { has_constitution_socket: String(e.target.checked) })}/> : <small>不适用</small>)}</td>
          <td className="batch-check">{manual && <input type="checkbox" aria-label={`${name}绿字`} checked={row.fields.green_stats_ok === 'true'} onChange={e => field(row, { green_stats_ok: String(e.target.checked) })}/>}</td>
        </tr>{expanded === row.slot && row.action !== 'clear' && <tr className="batch-expanded"><td colSpan={6}>
          {manual && <div className="form-grid">
            <label>装备名称<input aria-label={`${name}批量装备名称`} maxLength={100} value={row.fields.name} onChange={e => field(row, { name: e.target.value })}/></label>
            <label>基础体质覆盖<input aria-label={`${name}批量体质覆盖`} type="number" min="0" max="2147483647" value={row.fields.base_constitution_override} onChange={e => field(row, { base_constitution_override: e.target.value })}/></label>
            <label>备注<input aria-label={`${name}批量备注`} maxLength={2000} value={row.fields.note} onChange={e => field(row, { note: e.target.value })}/></label>
          </div>}
          <ChoicesFields value={row.choices} onChange={choices => update(row.slot, { ...manualRow(row), selected: true, choices })} weapon={typeFor(row.slot) === 'weapon'} cycleId={source?.cycle_id ?? ''} equipmentType={typeFor(row.slot)}/>
        </td></tr>}</Fragment>;
      })}</tbody>
    </table></div></fieldset>
    <div className="dialog-actions"><button type="button" disabled={pending} onClick={close}>取消</button><button className="primary" disabled={pending || !hasChanges}>预览整批更改</button></div>
  </form>;
}
