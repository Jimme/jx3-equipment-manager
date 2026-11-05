import type { ReactNode } from 'react';
import { useEffect, useId, useRef, useState } from 'react';
import { api, optionalInteger } from './api';
import { ChoicesFields, draftChoices, serializeChoices } from './ChoicesFields';
import type { Data, EquipmentChoices, EquipmentInput, Run, Source, Template } from './types';

function BinaryChoice({ title, value, yes, no, hint, onChange }: { title: string; value: string; yes: string; no: string; hint?: string; onChange: (value: string) => void }) {
  const name = useId();
  return <fieldset className="binary-choice" aria-describedby={hint ? `${name}-hint` : undefined}>
    <legend>{title}</legend>
    <div>
      {[['true', yes], ['false', no]].map(([option, label]) =>
        <label key={option}><input type="radio" name={name} value={option} checked={value === option} required onChange={() => onChange(option)}/>{label}</label>
      )}
    </div>
    {hint && <small id={`${name}-hint`}>{hint}</small>}
  </fieldset>;
}

type Props = {
  data: Data; type: string; current?: Template | null; initialChoices?: EquipmentChoices | null;
  pending: boolean; run: Run; close: () => void;
  onSave: (equipment: EquipmentInput, choices: EquipmentChoices) => Promise<unknown>;
  lastEquipment?: Template | null; description?: string; leadingFields?: ReactNode; actions?: ReactNode;
  resetChoicesOnEquipmentChange?: boolean; allowCopy?: boolean;
  submitLabel?: string; success?: string;
};

export function EquipmentForm({ data, type, current = null, initialChoices = null, pending, run, close, onSave, lastEquipment, description, leadingFields, actions, resetChoicesOnEquipmentChange = true, allowCopy = true, submitLabel = '保存装备', success = '装备已保存' }: Props) {
  const fieldsFor = (t: Template | null) => ({
    cycle_origin_id: t?.cycle_origin_id ?? data.cycleId, source_id: '', source_type: t?.source_type ?? '特殊', source_name: t?.source_name ?? '手动录入', short_label: t?.short_label ?? '自',
    actual_grade: t?.actual_grade.toString() ?? '', has_constitution_socket: t ? String(t.has_constitution_socket) : '', green_stats_ok: t ? String(t.green_stats_ok) : '',
    base_constitution_override: t?.base_constitution_override?.toString() ?? '', name: t?.name ?? '', note: t?.note ?? '',
  });
  const [fields, setFields] = useState(() => fieldsFor(current));
  const [choices, setChoices] = useState(() => draftChoices(initialChoices));
  const identity = JSON.stringify([fields.cycle_origin_id, fields.source_id || fields.source_name, fields.actual_grade, fields.has_constitution_socket, fields.green_stats_ok]);
  const previousIdentity = useRef(identity);
  useEffect(() => { if (identity !== previousIdentity.current) { if (resetChoicesOnEquipmentChange) setChoices(draftChoices()); previousIdentity.current = identity; } }, [identity, resetChoicesOnEquipmentChange]);
  const previousType = useRef(type);
  useEffect(() => {
    if (previousType.current !== type) {
      setFields(old => ({ ...old, has_constitution_socket: '', green_stats_ok: '', base_constitution_override: '', name: '', note: '' }));
      setChoices(draftChoices());
      previousType.current = type;
    }
  }, [type]);
  const [sourceChoice, setSourceChoice] = useState(current ? 'saved' : '');
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceError, setSourceError] = useState('');
  const [baseDefault, setBaseDefault] = useState<{ value: number | null; status: string } | null>(null);
  useEffect(() => {
    let active = true;
    setBaseDefault(null);
    const grade = Number(fields.actual_grade);
    if (!fields.cycle_origin_id || !Number.isInteger(grade) || grade <= 0) return;
    const timer=window.setTimeout(() => { api<{value:number|null;status:string}>(`/cycles/${fields.cycle_origin_id}/base-stat?actual_grade=${grade}&equipment_type=${type}`).then(value=>{if(active)setBaseDefault(value);}).catch(()=>{if(active)setBaseDefault({value:null,status:'unavailable'});}); },150);
    return () => {active=false;window.clearTimeout(timer);};
  },[fields.cycle_origin_id,fields.actual_grade,type]);
  useEffect(() => {
    let active = true;
    setSources([]); setSourceError('');
    if (fields.cycle_origin_id) api<Source[]>(`/cycles/${fields.cycle_origin_id}/sources`).then(s => { if (active) setSources(s); }).catch(e => { if (active) setSourceError(e.message); });
    return () => { active = false; };
  }, [fields.cycle_origin_id]);
  const templates = data.templates.filter(t => t.equipment_type === type);
  const changed = !current || JSON.stringify(fields) !== JSON.stringify(fieldsFor(current)) || JSON.stringify(choices) !== JSON.stringify(draftChoices(initialChoices));
  const canCopyLast = allowCopy && lastEquipment?.equipment_type === type;
  function copyEquipment(equipment: Template) {
    setFields(fieldsFor(equipment));
    setSourceChoice('saved');
    setChoices(draftChoices());
  }
  return <form onSubmit={e => { e.preventDefault(); void run(async () => {
    const equipment = { ...fields, source_id: fields.source_id || null, equipment_type: type, actual_grade: optionalInteger(fields.actual_grade), has_constitution_socket: fields.has_constitution_socket === 'true', green_stats_ok: fields.green_stats_ok === 'true', base_constitution_override: optionalInteger(fields.base_constitution_override) };
    await onSave(equipment, serializeChoices(choices));
  }, success, close); }}>
    <fieldset disabled={pending}>
      {description && <p className="hint">{description}</p>}
      {leadingFields}
      {allowCopy && (templates.length > 0 || canCopyLast) && <div className="equipment-copy">
        {canCopyLast && <button type="button" onClick={() => copyEquipment(lastEquipment)}>复制上一条</button>}
        {templates.length > 0 && <details><summary>复制已有装备</summary>
          <label>从已有装备复制<select aria-label="从已有模板复制" value="" onChange={e => { const template = templates.find(t => t.id === e.target.value); if (template) copyEquipment(template); }}>
            <option value="">选择此部位的装备…</option>
            {templates.map(t => <option key={t.id} value={t.id}>{t.name || t.source_name} · {t.actual_grade} 品 · {t.has_constitution_socket ? '有孔' : '无孔'} / {t.green_stats_ok ? '绿字达标' : '绿字未达标'}</option>)}
          </select></label>
        </details>}
      </div>}
      <div className="form-grid">
        <label>装备来源<select aria-label="来源预设" required value={fields.source_id || sourceChoice} onChange={e => {
          const s = sources.find(s => s.id === e.target.value);
          setSourceChoice(s ? '' : e.target.value);
          setFields(s ? { ...fields, source_id: s.id, source_type: s.source_type, source_name: s.name, short_label: s.short_label, actual_grade: s.default_grade?.toString() ?? '' } : { ...fields, source_id: '' });
        }}><option value="">请选择来源</option>{(current || sourceChoice === 'saved') && <option value="saved">{fields.source_name}（保留已录信息）</option>}{sources.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}<option value="manual">其他来源，手动填写</option></select></label>
        <label>真实品级<input aria-label="真实品级" required type="number" min="1" max="2147483647" step="1" value={fields.actual_grade} onChange={e => setFields({ ...fields, actual_grade: e.target.value })}/></label>
        {!fields.source_id && sourceChoice === 'manual' && <>
          <label>来源名称<input aria-label="装备来源名称" placeholder="装备从哪里获得" required maxLength={100} value={fields.source_name} onChange={e => setFields({ ...fields, source_name: e.target.value })}/></label>
          <label>表格简称<input aria-label="装备来源简称" required maxLength={12} value={fields.short_label} onChange={e => setFields({ ...fields, short_label: e.target.value })}/></label>
        </>}
        <BinaryChoice title="是否有体质孔" value={fields.has_constitution_socket} yes="有" no="无" onChange={value => setFields({ ...fields, has_constitution_socket: value })}/>
        <BinaryChoice title="绿字是否达标" value={fields.green_stats_ok} yes="达标" no="未达标" hint="内防＋外防＋加速" onChange={value => setFields({ ...fields, green_stats_ok: value })}/>
      </div>
      {sourceError && <p role="alert" className="message error">来源加载失败：{sourceError}</p>}
      <details className="optional-fields"><summary>更多信息</summary>
        <h3>{type === 'weapon' ? '附魔与五彩石' : '附魔'}</h3><ChoicesFields value={choices} onChange={setChoices} weapon={type === 'weapon'} cycleId={fields.cycle_origin_id} equipmentType={type}/>
        <div className="form-grid">
          <label>装备名称（可空）<input aria-label="装备名称" maxLength={100} value={fields.name} onChange={e => setFields({ ...fields, name: e.target.value })}/></label>
          <label>基础体质覆盖（可空）<input aria-label="基础体质覆盖" type="number" min="0" max="2147483647" value={fields.base_constitution_override} onChange={e => setFields({ ...fields, base_constitution_override: e.target.value })}/><small className="base-default">周期默认：{baseDefault?.status === 'known' ? baseDefault.value : baseDefault?.status === 'unavailable' ? '读取失败，请稍后重试' : '未录入'}；留空使用默认值，填写则覆盖。</small></label>
          {data.workspace.cycles.length > 1 ? <label>装备所属周期<select aria-label="装备原始周期" required value={fields.cycle_origin_id} onChange={e => { setFields({ ...fields, cycle_origin_id: e.target.value, source_id: '' }); setSourceChoice(current || sourceChoice === 'saved' ? 'saved' : 'manual'); }}><option value="">请选择周期</option>{data.workspace.cycles.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label> : <p className="fixed-field"><span>所属周期</span><strong>{data.workspace.cycles[0]?.name ?? '尚未创建'}</strong></p>}
        </div>
        <label className="note-field">备注<textarea aria-label="备注" maxLength={2000} rows={2} value={fields.note} onChange={e => setFields({ ...fields, note: e.target.value })}/></label>
        <p className="hint">体质留空表示未知。{!resetChoicesOnEquipmentChange && '这是修改当前记录，附魔和五彩石会保留。'}</p>
      </details>
      <div className="dialog-actions">{actions}<button type="button" onClick={close}>取消</button><button className="primary" disabled={!fields.cycle_origin_id || !changed}>{pending ? '正在保存…' : submitLabel}</button></div>
      {!fields.cycle_origin_id && <p className="hint">请先在「周期与来源」中创建周期。</p>}
    </fieldset>
  </form>;
}
