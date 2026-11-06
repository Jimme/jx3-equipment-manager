import { useEffect, useState } from 'react';
import { api, optionalInteger } from './api';
import type { GrowthComponent, GrowthReport, Run } from './types';

type Draft = { slots: { slot: string; refine: string; stone: string }[]; additional: string; confirmed: boolean };
const toDraft = (report: GrowthReport): Draft => ({ slots: report.slots.map(row => ({slot:row.slot, refine:row.settings.refine_level?.toString()??'', stone:row.settings.stone_state==='set'?String(row.settings.stone_level):row.settings.stone_state})), additional:report.calculation.additional_constitution?.toString()??'', confirmed:report.calculation.confirmation_valid });
const partText = (part: GrowthComponent) => part.status === 'not_applicable' ? '不适用' : part.value === null ? '未录入' : String(part.value);

export function GrowthEditor({ accountId, characterId, run, pending, onDirty }: { accountId: string; characterId: string; run: Run; pending: boolean; onDirty: (dirty: boolean) => void }) {
  const [report,setReport] = useState<GrowthReport|null>(null);
  const [draft,setDraft] = useState<Draft|null>(null);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState('');
  const path=`/accounts/${accountId}/characters/${characterId}/growth`;
  function install(value: GrowthReport) {setReport(value);setDraft(toDraft(value));}
  useEffect(()=>{
    let active=true;
    api<GrowthReport>(path).then(value=>{if(active)install(value);}).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[path]);
  const dirty=!!report && !!draft && JSON.stringify(draft)!==JSON.stringify(toDraft(report));
  useEffect(()=>{onDirty(dirty);},[dirty,onDirty]);
  useEffect(()=>()=>onDirty(false),[onDirty]);
  async function reread() {
    if(dirty&&!window.confirm('重新读取会丢弃未保存的养成输入，确定继续？'))return;
    setError('');
    await run(async()=>{setLoading(true);try {install(await api<GrowthReport>(path));} finally {setLoading(false);}},'已重新读取角色养成',undefined,false);
  }
  if(!report || !draft) return loading?<p role="status">正在读取角色养成…</p>:<>{error&&<p role="alert">{error}</p>}<button disabled={pending} onClick={()=>void reread()}>重试读取</button></>;
  const disabled=pending||loading;
  function editSlot(slot: string, field: 'refine'|'stone', value: string) {setDraft(old=>old?{...old,slots:old.slots.map(row=>row.slot===slot?{...row,[field]:value}:row)}:old);}
  return <form onSubmit={e=>{e.preventDefault();void run(async()=>{
    const value=await api<GrowthReport>(path,'PUT',{
      slots:draft.slots.map((row,index)=>({slot:row.slot,expected_version:report.slots[index].settings.version,refine_level:optionalInteger(row.refine),stone_state:row.stone==='unknown'?'unknown':row.stone==='none'?'none':'set',stone_level:['unknown','none'].includes(row.stone)?null:optionalInteger(row.stone)})),
      expected_version:report.calculation.version,expected_cycle_id:report.cycle?.id??null,expected_cycle_version:report.cycle?.version??null,
      additional_constitution:optionalInteger(draft.additional),scope_confirmed:draft.confirmed,
    });install(value);
  },'角色养成已保存，体质明细已更新');}}>
    {error&&<p role="alert" className="message error">{error}</p>}
    <div className="growth-summary"><strong>{report.equipment_total===null?`已知小计 ${report.known_subtotal} · 尚未完整`:`装备来源合计 ${report.equipment_total}`}</strong><button type="button" disabled={disabled} onClick={()=>void reread()}>重新读取</button></div>
    <p className="hint">精炼、五行石等级跟角色部位走，换装后保留；加成按当前装备的原周期、品级重新查询。装备没有体质孔时，五行石不计入体质，所选等级仍保留。</p>
    {dirty&&<p role="status" className="replacement-note">有未保存的养成输入。下方体质明细仍按已保存配置计算，保存后更新。</p>}
    <fieldset disabled={disabled}>
      <div className="config-scroll"><table className="daily-table growth-table"><thead><tr><th>部位 / 当前装备</th><th>精炼等级</th><th>五行石</th></tr></thead><tbody>{report.slots.map((row,index)=><tr key={row.slot}><th>{row.name}<small>{row.equipped?`${row.grade} 品 · ${row.cycle_name}`:'未装备'}</small></th><td><select aria-label={`${row.name}精炼`} value={draft.slots[index].refine} onChange={e=>editSlot(row.slot,'refine',e.target.value)}><option value="">未知</option>{[0,4,6,8].map(level=><option key={level} value={level}>{level}</option>)}</select></td><td><select aria-label={`${row.name}五行石`} value={draft.slots[index].stone} onChange={e=>editSlot(row.slot,'stone',e.target.value)}><option value="unknown">未知</option><option value="none">明确未镶嵌</option>{[1,2,3,4,5,6,7,8].map(level=><option key={level} value={level}>{level} 级</option>)}</select></td></tr>)}</tbody></table></div>
      <details className="optional-fields menu-disclosure"><summary>体质构成明细{report.missing.length?` · ${report.missing.length} 项未完善`:''}</summary>
        <p className="hint">未知不会当作 0；不适用不参与相加。装备来源合计不等于游戏面板完整体质。空部位会保守标为未完善，避免漏录被当成完整配置。</p>
        <div className="config-scroll"><table className="daily-table"><thead><tr><th>部位</th><th>基础</th><th>精炼</th><th>五行石</th><th>附魔</th><th>五彩石</th></tr></thead><tbody>{report.slots.map(row=><tr key={row.slot}><th>{row.name}</th>{(['base','refine','stone','enchant','rainbow'] as const).map(key=><td key={key} title={row.components[key].source}>{partText(row.components[key])}</td>)}</tr>)}</tbody></table></div>
        {!!report.missing.length&&<ul className="growth-missing">{report.missing.map(text=><li key={text}>{text}</li>)}</ul>}
      </details>
      <details className="optional-fields menu-disclosure"><summary>技能层数与计算口径</summary>
        <p className="hint">当前阈值周期：{report.cycle?.name??'尚未选择'}。每层阈值表示达到该层所需的最低体质（含等于），需从 1 层起连续录入；当前配置最高层不代表游戏上限。</p>
        <label>装备以外的已确认体质修正<input aria-label="装备以外体质修正" type="number" min="0" max="2147483647" placeholder="未知留空，确认没有时填 0" value={draft.additional} onChange={e=>setDraft({...draft,additional:e.target.value,confirmed:false})}/></label>
        <label className="check"><input type="checkbox" aria-label="已核实计算口径与阈值" checked={draft.confirmed} disabled={!report.cycle||!draft.additional.trim()} onChange={e=>setDraft({...draft,confirmed:e.target.checked})}/>我已核实：装备合计加上述修正覆盖所需体质，当前周期技能阈值也符合上述规则。</label>
        <p className="hint">没有核实就保持未勾选。切换周期或修改阈值后须重新核实；不会自动猜测裸装体质或其他增益。</p>
        {report.verified_total!==null&&<p>已核实口径体质：{report.verified_total}</p>}
        {report.buff_message&&<p className="hint">{report.buff_message}</p>}
        {report.buffs.map(buff=><p key={buff.skill}>{buff.skill}：{buff.stacks===null?`未计算（${buff.message}）`:`${buff.stacks} 层${buff.next_stacks===null?`；${buff.message}`:`；下一层 ${buff.next_stacks} 层需 ${buff.next_threshold}，还差 ${buff.difference}`}`}</p>)}
      </details>
      <div className="dialog-actions"><button className="primary" disabled={!dirty||report.character.archived}>{pending?'正在保存…':'保存角色养成'}</button></div>
    </fieldset>
  </form>;
}
