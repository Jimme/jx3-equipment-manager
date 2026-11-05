import { useEffect, useId, useState } from 'react';
import { api, optionalInteger } from './api';
import type { AttachmentChoice, CycleData, EquipmentChoices } from './types';

type ChoiceDraft = { state: AttachmentChoice['state']; name: string; constitution: string };
export type ChoicesDraft = { enchant: ChoiceDraft; rainbow: ChoiceDraft };

export function draftChoices(choices?: EquipmentChoices | null): ChoicesDraft {
  const draft = (value?: AttachmentChoice) => ({ state: value?.state ?? 'unknown' as const, name: value?.name ?? '', constitution: value?.constitution?.toString() ?? '' });
  return { enchant: draft(choices?.enchant), rainbow: draft(choices?.rainbow) };
}

export function serializeChoices(choices: ChoicesDraft): EquipmentChoices {
  const convert = (value: ChoiceDraft): AttachmentChoice => value.state === 'configured'
    ? { state: value.state, name: value.name, constitution: optionalInteger(value.constitution) }
    : { state: value.state, name: '', constitution: null };
  return { enchant: convert(choices.enchant), rainbow: convert(choices.rainbow) };
}

export function choiceSummary(choices: EquipmentChoices, weapon: boolean): string {
  const text = (choice: AttachmentChoice) => choice.state === 'unknown' ? '未录入' : choice.state === 'none' ? '确认无' : `${choice.name || '手填数值'}${choice.constitution === null ? '（按名称查原周期配置）' : `（体质 ${choice.constitution}）`}`;
  return `附魔：${text(choices.enchant)}${weapon ? `；五彩石：${text(choices.rainbow)}` : ''}`;
}

export function draftChoiceSummary(choices: ChoicesDraft, weapon: boolean): string {
  const text = (choice: ChoiceDraft) => choice.state === 'unknown' ? '未录入' : choice.state === 'none' ? '确认无'
    : `${choice.name.trim() || '手填数值'}${choice.constitution.trim() ? `（体质 ${choice.constitution}）` : '（按名称查原周期配置）'}`;
  return `附魔：${text(choices.enchant)}${weapon ? `；五彩石：${text(choices.rainbow)}` : ''}`;
}

export function ChoicesFields({ value, onChange, weapon, cycleId, equipmentType }: { value: ChoicesDraft; onChange: (value: ChoicesDraft) => void; weapon: boolean; cycleId: string; equipmentType: string }) {
  const listId=useId();
  const [config,setConfig]=useState<CycleData|null>(null);
  const [lookupError,setLookupError]=useState(false);
  const configured=value.enchant.state==='configured'||(weapon&&value.rainbow.state==='configured');
  useEffect(()=>{
    let active=true;setConfig(null);setLookupError(false);
    if(cycleId&&configured) api<CycleData>(`/cycles/${cycleId}/data`).then(data=>{if(active)setConfig(data);}).catch(()=>{if(active)setLookupError(true);});
    return ()=>{active=false;};
  },[cycleId,configured]);
  function choice(prefix: 'enchant' | 'rainbow', title: string) {
    const fields = value[prefix];
    const options=config?.cycle.id===cycleId?(prefix==='enchant'?config.tables.enchant.filter(row=>row.equipment_type===equipmentType):config.tables.rainbow):[];
    const match=options.find(row=>row.name===fields.name.trim());
    const change = (update: Partial<ChoiceDraft>) => onChange({ ...value, [prefix]: { ...fields, ...update } });
    return <div className="attachment-choice"><label>{title}<select aria-label={title} value={fields.state} onChange={e => change({ state: e.target.value as ChoiceDraft['state'], name: '', constitution: '' })}>
      <option value="unknown">未录入</option><option value="none">确认没有</option><option value="configured">记录名称或数值</option>
    </select></label>{fields.state === 'configured' && <div className="form-grid">
      <label>{title}名称<input aria-label={`${title}名称`} list={`${listId}-${prefix}`} maxLength={100} required={!fields.constitution.trim()} placeholder="类型、档位或你认识的名称" value={fields.name} onChange={e => change({ name: e.target.value })}/><datalist id={`${listId}-${prefix}`}>{options.map(row=><option key={String(row.name)} value={String(row.name)}/>)}</datalist></label>
      <label>{title}体质贡献（可空）<input aria-label={`${title}体质贡献`} type="number" min="0" max="2147483647" required={!fields.name.trim()} value={fields.constitution} onChange={e => change({ constitution: e.target.value })}/></label>
    <p className="hint">{fields.constitution.trim()?`当前使用手填体质 ${fields.constitution}`:!cycleId?'先选择装备所属周期':lookupError?'原周期配置读取失败，可继续记录名称或手填数值':!config?'正在读取原周期配置…':!fields.name.trim()?'可选已配置名称，也可填写实际名称':`原周期默认：${match?.constitution??'未录入'}；贡献留空时查询默认，手填则覆盖。`}</p>
    </div>}</div>;
  }
  return <>{choice('enchant', '附魔')}{weapon && choice('rainbow', '五彩石')}<p className="hint">可填写周期数值表中的名称，留空贡献时按装备原周期查询；手填数值优先。未录入不会当作 0；只有确认没有时才选择“确认没有”。</p></>;
}
