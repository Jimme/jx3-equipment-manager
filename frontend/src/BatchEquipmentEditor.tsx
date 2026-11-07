import { useEffect, useMemo, useState } from 'react';
import { api, optionalInteger } from './api';
import { BinaryChoice } from './EquipmentForm';
import { ChoicesFields, choiceSummary, draftChoices, serializeChoices, type ChoicesDraft } from './ChoicesFields';
import { requestId } from './requestId';
import type { Character, DailyData, Data, EquipmentInput, Matrix, Run, Source, Template, Workspace } from './types';

type Action = 'keep' | 'edit' | 'new' | 'warehouse' | 'clear';
type Fields = {
  cycle_origin_id: string; source_id: string; source_type: string; source_name: string; short_label: string;
  actual_grade: string; has_constitution_socket: string; green_stats_ok: string;
  name: string; note: string; base_constitution_override: string;
};
type Row = { slot: string; selected: boolean; action: Action; version: number; fields: Fields; sourceChoice: string; choices: ChoicesDraft; warehouseId: string; stockVersion: number };
type Preview = { preview_id?: string; unchanged: boolean; changes: { slot: string; name: string; action: string; before: string; after: string; before_equipment?: Template; after_equipment?: Template; before_choices?: Parameters<typeof choiceSummary>[0]; after_choices?: Parameters<typeof choiceSummary>[0]; warehouse_id: string | null }[]; deductions: Record<string, number> };

function fieldsFor(equipment: Template | null, cycleId: string): Fields {
  return {
    cycle_origin_id: equipment?.cycle_origin_id ?? cycleId, source_id: '', source_type: equipment?.source_type ?? '特殊',
    source_name: equipment?.source_name ?? '', short_label: equipment?.short_label ?? '',
    actual_grade: equipment?.actual_grade.toString() ?? '', has_constitution_socket: equipment ? String(equipment.has_constitution_socket) : '',
    green_stats_ok: equipment ? String(equipment.green_stats_ok) : '', name: equipment?.name ?? '', note: equipment?.note ?? '',
    base_constitution_override: equipment?.base_constitution_override?.toString() ?? '',
  };
}

export function BatchEquipmentEditor({ data, character, run, pending, close, savedClose, onDirty }: {
  data: Data; character: Character; run: Run; pending: boolean; close: () => void; savedClose: () => void; onDirty: (dirty: boolean) => void;
}) {
  const [working, setWorking] = useState(data);
  const currentCell = (slot: string) => working.matrix!.cells[character.id][slot];
  const initialRow = (slot: string): Row => {
    const cell = currentCell(slot);
    return { slot, selected: false, action: 'keep', version: cell.version, fields: fieldsFor(cell.equipment, working.cycleId),
      sourceChoice: cell.equipment ? 'saved' : '', choices: draftChoices(cell.choices), warehouseId: '', stockVersion: 0 };
  };
  const [rows, setRows] = useState<Row[]>(() => data.workspace.slots.map(slot => initialRow(slot.key)));
  const [sources, setSources] = useState<Record<string, Source[]>>({ [data.cycleId]: data.sources });
  const [sourceError, setSourceError] = useState('');
  const [sourceRead, setSourceRead] = useState(0);
  const [uniform, setUniform] = useState({ source: '', grade: '', socket: '', green: '' });
  const [copySlot, setCopySlot] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [operationId, setOperationId] = useState('');
  const dirty = rows.some(row => row.action !== 'keep');
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const cycleKey = [...new Set(rows.map(row => row.fields.cycle_origin_id).filter(Boolean))].sort().join(',');
  useEffect(() => {
    let active = true;
    const missing = cycleKey.split(',').filter(id => id && !sources[id]);
    if (missing.length) {
      setSourceError('');
      Promise.all(missing.map(async id => [id, await api<Source[]>(`/cycles/${id}/sources`)] as const))
        .then(result => { if (active) setSources(old => ({ ...old, ...Object.fromEntries(result) })); })
        .catch(error => { if (active) setSourceError(error.message); });
    }
    return () => { active = false; };
  }, [cycleKey, sources, sourceRead]);
  const warehouse = useMemo(() => new Map(working.daily!.warehouse.map(item => [item.id, item])), [working.daily]);
  const chosen = rows.filter(row => row.selected);
  const typeFor = (slot: string) => slot.startsWith('ring') ? 'ring' : slot;
  const socketApplicable = (row: Row) => typeFor(row.slot) !== 'ring' && working.workspace.cycles.find(c => c.id === row.fields.cycle_origin_id)?.has_constitution_sockets !== false;
  function update(slot: string, values: Partial<Row>) {
    setRows(old => old.map(row => row.slot === slot ? { ...row, ...values } : row));
  }
  function field(row: Row, values: Partial<Fields>) { update(row.slot, { fields: { ...row.fields, ...values } }); }
  function changeAction(row: Row, action: Action) {
    const fresh = initialRow(row.slot);
    update(row.slot, action === 'new'
      ? { ...fresh, selected: row.selected, action, fields: fieldsFor(null, working.cycleId), choices: draftChoices(), sourceChoice: '' }
      : { ...fresh, selected: row.selected, action });
  }
  function manualRow(row: Row): Row {
    if (row.action !== 'keep') return row;
    return { ...row, action: currentCell(row.slot).equipment ? 'edit' : 'new' };
  }
  function applyUniform(copy = false) {
    if (!chosen.length) return;
    if (chosen.some(row => row.action === 'warehouse' || row.action === 'clear')) {
      window.alert('统一设置用于手动录入或纠错，请取消勾选仓库穿戴和清除行。'); return;
    }
    const copied = rows.find(row => row.slot === copySlot);
    if (copy && !copied) return;
    const source = (sources[working.cycleId] ?? []).find(item => item.id === uniform.source);
    setRows(old => old.map(original => {
      if (!original.selected) return original;
      const row = manualRow(original);
      let fields = { ...row.fields };
      let sourceChoice = row.sourceChoice;
      if (copy && copied) {
        const from = copied.fields;
        fields = { ...fields, cycle_origin_id: from.cycle_origin_id, source_id: from.source_id, source_type: from.source_type,
          source_name: from.source_name, short_label: from.short_label, actual_grade: from.actual_grade,
          has_constitution_socket: from.has_constitution_socket, green_stats_ok: from.green_stats_ok };
        sourceChoice = copied.sourceChoice;
      } else {
        if (source) {
          fields = { ...fields, cycle_origin_id: source.cycle_id, source_id: source.id, source_type: source.source_type, source_name: source.name, short_label: source.short_label };
          if (!fields.actual_grade) fields.actual_grade = source.default_grade?.toString() ?? '';
        }
        if (uniform.grade) fields.actual_grade = uniform.grade;
        if (uniform.socket) fields.has_constitution_socket = uniform.socket;
        if (uniform.green) fields.green_stats_ok = uniform.green;
      }
      return { ...row, fields, sourceChoice };
    }));
  }
  async function reread() {
    const base = `/accounts/${working.accountId}`;
    const [matrix, daily, workspace] = await Promise.all([
      api<Matrix>(base + '/matrix'), api<DailyData>(base + '/daily?skip_unconfigured_gaps=true'), api<Workspace>('/workspace'),
    ]);
    if (!matrix.characters.some(item => item.id === character.id)) throw new Error('角色已移除，请保留草稿并返回核对');
    setWorking(old => ({ ...old, matrix, daily, workspace, cycleId: workspace.active_cycle_id ?? '' }));
    setSources({});
    setPreview(null);
  }
  function buildRows() {
    return rows.filter(row => row.action !== 'keep').map(row => {
      const base = { slot: row.slot, action: row.action, expected_version: row.version };
      if (row.action === 'clear') return base;
      const choices = serializeChoices(row.choices);
      if (row.action === 'warehouse') return { ...base, warehouse_id: row.warehouseId, expected_stock_version: row.stockVersion, choices };
      const equipment: EquipmentInput = {
        ...row.fields, source_id: row.fields.source_id || null, equipment_type: typeFor(row.slot),
        actual_grade: optionalInteger(row.fields.actual_grade), base_constitution_override: optionalInteger(row.fields.base_constitution_override),
        has_constitution_socket: socketApplicable(row) && row.fields.has_constitution_socket === 'true', green_stats_ok: row.fields.green_stats_ok === 'true',
      };
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
          {change.before_choices && <small>{choiceSummary(change.before_choices, typeFor(change.slot) === 'weapon')}</small>}</td>
        <td>{change.after}{change.after_equipment && <small>{working.workspace.cycles.find(c => c.id === change.after_equipment!.cycle_origin_id)?.name} · {change.after_equipment.source_name} {change.after_equipment.note}</small>}
          {change.after_choices && <small>{choiceSummary(change.after_choices, typeFor(change.slot) === 'weapon')}</small>}</td>
      </tr>)}</tbody></table></div>}
    <p>合计修改 {preview.changes.length} 个部位，库存取出 {Object.values(preview.deductions).reduce((a, b) => a + b, 0)} 件。</p>
    <div className="dialog-actions"><button disabled={pending} onClick={() => setPreview(null)}>返回修改</button>
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
    <div className="batch-toolbar"><span>已勾选 {chosen.length} 行</span>
      <button type="button" disabled={pending} onClick={() => setRows(old => old.map(row => ({ ...row, selected: !old.every(item => item.selected) })))}>全选／取消全选</button>
      <button type="button" disabled={pending} onClick={() => void run(reread, '已读取最新状态，草稿仍保留，请逐行核对差异', undefined, false)}>读取最新状态核对</button>
    </div>
    <details className="optional-fields menu-disclosure"><summary>对勾选行统一设置 · {chosen.length} 行</summary>
      <div className="batch-toolbar">
        <label>统一来源<select aria-label="批量统一来源" value={uniform.source} onChange={e => setUniform({ ...uniform, source: e.target.value })}><option value="">保留原来源</option>{(sources[working.cycleId] ?? []).map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
        <label>统一品级<input aria-label="批量统一品级" type="number" min="1" max="2147483647" value={uniform.grade} onChange={e => setUniform({ ...uniform, grade: e.target.value })}/></label>
        <label>统一体质孔<select value={uniform.socket} onChange={e => setUniform({ ...uniform, socket: e.target.value })}><option value="">保留</option><option value="true">有</option><option value="false">无</option></select></label>
        <label>统一绿字<select value={uniform.green} onChange={e => setUniform({ ...uniform, green: e.target.value })}><option value="">保留</option><option value="true">达标</option><option value="false">未达标</option></select></label>
        <button type="button" disabled={pending || !chosen.length || !Object.values(uniform).some(Boolean)} onClick={() => applyUniform()}>应用到勾选行</button>
      </div>
      <p className="hint">来源只补空品级；填写统一品级才覆盖原数值。孔不适用的行会自动跳过孔条件。</p>
      <div className="batch-toolbar"><label>复制通用字段自<select aria-label="复制行字段" value={copySlot} onChange={e => setCopySlot(e.target.value)}><option value="">请选择部位</option>{working.workspace.slots.map(slot => <option key={slot.key} value={slot.key}>{slot.name}</option>)}</select></label>
        <button type="button" disabled={pending || !chosen.length || !copySlot} onClick={() => applyUniform(true)}>复制到勾选行</button></div>
      <p className="hint">复制原周期、来源、品级、孔和绿字；附魔、五彩石及单件体质仍逐行核对。</p>
    </details>
    {sourceError && <p role="alert">{sourceError}<button type="button" onClick={() => setSourceRead(value => value + 1)}>重试来源读取</button></p>}
    <fieldset disabled={pending}><div className="batch-scroll"><table className="daily-table batch-table">
      <thead><tr><th>选择／部位</th><th>操作</th><th>当前穿戴</th><th>录入或选择库存</th></tr></thead>
      <tbody>{rows.map(row => {
        const name = working.workspace.slots.find(slot => slot.key === row.slot)!.name;
        const cell = currentCell(row.slot);
        const manual = row.action === 'edit' || row.action === 'new';
        const stock = warehouse.get(row.warehouseId);
        const conflict = row.action !== 'keep' && (cell.version !== row.version || (row.action === 'warehouse' && (!stock || stock.version !== row.stockVersion)));
        return <tr key={row.slot} className={conflict ? 'batch-conflict' : undefined}>
          <th><label className="check"><input aria-label={`勾选${name}`} type="checkbox" checked={row.selected} onChange={e => update(row.slot, { selected: e.target.checked })}/>{name}</label></th>
          <td><select aria-label={`${name}批量操作`} value={row.action} onChange={e => {
            if (row.action !== 'keep' && !window.confirm('切换本行操作会重置本行输入，确定继续？')) return;
            changeAction(row, e.target.value as Action);
          }}><option value="keep">保持原样</option><option value="edit" disabled={!cell.equipment}>纠正记录</option><option value="new">{cell.equipment ? '手动换装' : '手动录入'}</option><option value="warehouse" disabled={!working.daily!.warehouse.some(item => item.equipment.equipment_type === typeFor(row.slot))}>从仓库穿戴</option><option value="clear" disabled={!cell.equipment}>清除穿戴</option></select>
            {row.action !== 'keep' && <button type="button" className="quiet-button" onClick={() => {
              if (window.confirm('放弃本行输入并使用最新记录？')) update(row.slot, initialRow(row.slot));
            }}>放弃本行</button>}
          </td>
          <td>{cell.label}{cell.equipment && <small>{cell.equipment.actual_grade} 品 · {working.workspace.cycles.find(c => c.id === cell.equipment!.cycle_origin_id)?.name}</small>}
            {conflict && <p className="error">本行记录或库存版本已变化，草稿仍保留。{stock && <span>最新可用 {stock.quantity} 件。</span>}
              <button type="button" onClick={() => update(row.slot, { version: cell.version, stockVersion: stock?.version ?? row.stockVersion })}>保留输入并采用最新版本</button></p>}
          </td>
          <td>{row.action === 'keep' ? <span className="hint">保持当前记录</span> : row.action === 'clear' ? <span className="danger">清除装备，保留精炼和五行石等级</span> : <>
            {row.action === 'warehouse' && <label>库存装备<select aria-label={`${name}批量库存`} required value={row.warehouseId} onChange={e => {
              const item = warehouse.get(e.target.value);
              update(row.slot, { warehouseId: e.target.value, stockVersion: item?.version ?? 0, fields: fieldsFor(item?.equipment ?? null, working.cycleId), choices: draftChoices(item?.choices) });
            }}><option value="">请选择库存</option>{working.daily!.warehouse.filter(item => item.equipment.equipment_type === typeFor(row.slot)).map(item => <option key={item.id} value={item.id}>{item.label} · {item.equipment.actual_grade} 品 · {item.quantity} 件 · {choiceSummary(item.choices, typeFor(row.slot) === 'weapon')}</option>)}</select></label>}
            {manual && <div className="batch-fields">
              <label>来源<select aria-label={`${name}批量来源`} required value={row.fields.source_id || row.sourceChoice} onChange={e => {
                const source = (sources[row.fields.cycle_origin_id] ?? []).find(item => item.id === e.target.value);
                if (source) update(row.slot, { fields: { ...row.fields, source_id: source.id, source_type: source.source_type, source_name: source.name, short_label: source.short_label, actual_grade: row.fields.actual_grade || source.default_grade?.toString() || '' }, sourceChoice: '' });
                else update(row.slot, { sourceChoice: e.target.value, fields: { ...row.fields, source_id: '' } });
              }}><option value="">请选择来源</option>{row.sourceChoice === 'saved' && <option value="saved">{row.fields.source_name}（原记录）</option>}{(sources[row.fields.cycle_origin_id] ?? []).map(source => <option key={source.id} value={source.id}>{source.name}</option>)}<option value="manual">手动来源</option></select></label>
              <label>真实品级<input aria-label={`${name}批量品级`} required type="number" min="1" max="2147483647" value={row.fields.actual_grade} onChange={e => field(row, { actual_grade: e.target.value })}/></label>
              {socketApplicable(row) ? <BinaryChoice title={`${name}体质孔`} value={row.fields.has_constitution_socket} yes="有" no="无" onChange={value => field(row, { has_constitution_socket: value })}/> : <span className="hint">无体质孔</span>}
              <BinaryChoice title={`${name}绿字`} value={row.fields.green_stats_ok} yes="达标" no="未达标" onChange={value => field(row, { green_stats_ok: value })}/>
              {row.sourceChoice === 'manual' && <><label>来源名称<input aria-label={`${name}批量来源名称`} required maxLength={100} value={row.fields.source_name} onChange={e => field(row, { source_name: e.target.value })}/></label>
                <label>简称<input aria-label={`${name}批量简称`} required maxLength={12} value={row.fields.short_label} onChange={e => field(row, { short_label: e.target.value })}/></label></>}
            </div>}
            <details className="optional-fields menu-disclosure"><summary>{name}更多属性</summary>
              {manual && <div className="form-grid">
                <label>原始周期<select aria-label={`${name}批量原周期`} required value={row.fields.cycle_origin_id} onChange={e => update(row.slot, { fields: { ...row.fields, cycle_origin_id: e.target.value, source_id: '' }, sourceChoice: row.fields.source_name ? 'saved' : '' })}><option value="">请选择周期</option>{working.workspace.cycles.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
                <label>装备名称<input aria-label={`${name}批量装备名称`} maxLength={100} value={row.fields.name} onChange={e => field(row, { name: e.target.value })}/></label>
                <label>基础体质覆盖<input aria-label={`${name}批量体质覆盖`} type="number" min="0" max="2147483647" value={row.fields.base_constitution_override} onChange={e => field(row, { base_constitution_override: e.target.value })}/></label>
                <label>备注<input aria-label={`${name}批量备注`} maxLength={2000} value={row.fields.note} onChange={e => field(row, { note: e.target.value })}/></label>
              </div>}
              <ChoicesFields value={row.choices} onChange={choices => update(row.slot, { choices })} weapon={typeFor(row.slot) === 'weapon'} cycleId={row.fields.cycle_origin_id} equipmentType={typeFor(row.slot)}/>
            </details>
          </>}</td>
        </tr>;
      })}</tbody>
    </table></div></fieldset>
    <div className="dialog-actions"><button type="button" disabled={pending} onClick={close}>取消</button><button className="primary" disabled={pending || !dirty}>预览整批更改</button></div>
  </form>;
}
