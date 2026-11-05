import { useEffect, useState } from 'react';
import { api, optionalInteger } from './api';
import { asDraft, payloadRows, rowFingerprint, STAT_LABELS, STAT_TYPES, VALUE_FIELD } from './cycleDataUtils';
import { CyclePreviewPanel } from './CyclePreviewPanel';
import type { CycleData, CyclePreview, Run, StatKind, StatRow } from './types';

export function CycleDataEditor({ data, run, pending, applied, onDirty }: { data: CycleData; run: Run; pending: boolean; applied: (id: string) => Promise<void>; onDirty: (dirty: boolean) => void }) {
  const [kind, setKind] = useState<StatKind>('base');
  const [drafts, setDrafts] = useState<Partial<Record<StatKind,StatRow[]>>>({});
  const [grade, setGrade] = useState('');
  const [preview, setPreview] = useState<CyclePreview | null>(null);
  const initial = (key: StatKind): StatRow[] => key === 'stone' ? Array.from({length:8},(_,i) => ({ stone_level: String(i + 1), constitution: String(data.tables.stone.find(r => r.stone_level === i + 1)?.constitution ?? '') })) : asDraft(data.tables[key]);
  const rows = drafts[kind] ?? initial(kind);
  const dirty = Object.entries(drafts).some(([key,value]) => rowFingerprint(value!) !== rowFingerprint(initial(key as StatKind)));
  const currentDirty = rowFingerprint(rows) !== rowFingerprint(initial(kind));
  useEffect(() => { onDirty(dirty); }, [dirty,onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  const readonly = pending || data.cycle.archived;
  const setRows = (next: StatRow[]) => setDrafts(old => ({...old,[kind]:next}));
  const valueField = VALUE_FIELD(kind);
  function change(index: number, key: string, value: string) { setRows(rows.map((row,i) => i === index ? {...row,[key]:value} : row)); }
  function matrixValue(actualGrade: number, type: string, refine?: number) { return rows.find(r => Number(r.actual_grade) === actualGrade && r.equipment_type === type && (refine === undefined || Number(r.refine_level) === refine))?.constitution ?? ''; }
  function matrixChange(actualGrade: number, type: string, value: string, refine?: number) {
    const index = rows.findIndex(r => Number(r.actual_grade) === actualGrade && r.equipment_type === type && (refine === undefined || Number(r.refine_level) === refine));
    if (index >= 0) change(index,'constitution',value);
    else setRows([...rows,{actual_grade:String(actualGrade),equipment_type:type,...(refine === undefined ? {} : {refine_level:String(refine)}),constitution:value}]);
  }
  function numberInput(value: string | number | null, label: string, update: (value: string) => void) { return <input type="number" min="0" max="2147483647" step="1" aria-label={label} placeholder="未录入" value={value ?? ''} onChange={e => update(e.target.value)}/>; }
  async function afterApply(id: string) { setDrafts(old => { const next = {...old}; delete next[kind]; return next; }); setPreview(null); await applied(id); }
  const grades = [...new Set(rows.map(r => Number(r.actual_grade)))].sort((a,b) => a-b);
  if (preview) return <CyclePreviewPanel key={preview.preview_id} cycleId={data.cycle.id} preview={preview} run={run} pending={pending} cancel={() => setPreview(null)} applied={afterApply}/>;
  return <section aria-label="周期数值表">
    <div className="config-toolbar"><label>数值表<select aria-label="数值表" value={kind} disabled={pending} onChange={e => setKind(e.target.value as StatKind)}>{(Object.keys(STAT_LABELS) as StatKind[]).map(key => <option key={key} value={key}>{STAT_LABELS[key]}</option>)}</select></label><div className="button-row"><a className="button-link" href={`/api/cycles/${data.cycle.id}/data/${kind}/csv`} download>导出 CSV</a>{!data.cycle.archived && <label className={`button-link file-button ${pending ? 'disabled' : ''}`}>导入 CSV<input aria-label="导入 CSV" type="file" accept=".csv,text/csv" disabled={pending} onChange={e => { const file=e.target.files?.[0]; e.target.value=''; if (!file) return; if (currentDirty && !window.confirm('本表有未保存的草稿。CSV 不会与草稿合并，确认导入后会替换本表；返回修改仍可保留草稿。继续预览 CSV？')) return; void run(async () => { if (file.size > 2000000) throw new Error('CSV 文件最多 2 MB'); setPreview(await api<CyclePreview>(`/cycles/${data.cycle.id}/data/${kind}/csv-preview`,'POST',{expected_version:data.cycle.version,csv_text:await file.text()})); },'已生成 CSV 导入预览',undefined,false); }}/></label>}</div></div>
    <p className="hint">CSV 导出已保存的数据，不包含草稿；导入会先预览整表替换。空白表示未知，0 表示明确为零。{kind === 'base' ? '默认基础体质按本周期、真实品级和部位查询；单件覆盖优先。戒指两格共用装备默认值。' : kind === 'refine' ? '这里填写额外体质，精炼 0 也不自动假定为零；戒指两格的精炼等级仍独立保存。' : kind === 'buff' ? '阈值为达到该层的最低体质，含等于；从 1 层连续录入。角色数据完整且口径核实后才计算层数。' : '仅填写已核实的值，角色体质明细会按装备原周期查询。'}</p>
    <fieldset disabled={readonly}>
      {(kind === 'base' || kind === 'refine') && <form className="inline-form" onSubmit={e => { e.preventDefault(); void run(async () => {
        const value=optionalInteger(grade); if (!value || value < 1) throw new Error('请填写真实品级正整数');
        if (grades.includes(value)) throw new Error('这个品级已在表中');
        const next:StatRow[]=[]; for (const [type] of STAT_TYPES) for (const level of kind === 'refine' ? [0,4,6,8] : [null]) next.push({actual_grade:String(value),equipment_type:type,...(level === null ? {} : {refine_level:String(level)}),constitution:''});
        setRows([...rows,...next]); setGrade('');
      },'已添加空白品级行，请填写后预览保存',undefined,false); }}><label>添加真实品级<input aria-label="添加真实品级" type="number" min="1" max="2147483647" required value={grade} onChange={e => setGrade(e.target.value)}/></label><button>添加品级行</button></form>}
      {(kind === 'base' || kind === 'refine') && grades.length === 0 && <p className="small-empty">还没有配置品级。添加实际品级后即可逐格填写，不必一次填完。</p>}
      <div className="config-scroll"><table className="config-table"><thead><tr>{kind === 'base' ? <><th>真实品级</th>{STAT_TYPES.map(([key,name]) => <th key={key}>{name}</th>)}<th>草稿操作</th></> : kind === 'refine' ? <><th>真实品级</th><th>部位</th>{[0,4,6,8].map(level => <th key={level}>精炼 {level}</th>)}<th>草稿操作</th></> : kind === 'stone' ? <><th>等级</th><th>体质贡献</th></> : <>{kind === 'enchant' && <th>部位</th>}<th>{kind === 'buff' ? '技能名称' : '名称 / 档位'}</th>{kind === 'buff' && <th>层数</th>}<th>{kind === 'buff' ? '体质阈值' : '体质贡献'}</th><th>草稿操作</th></>}</tr></thead><tbody>
        {kind === 'base' && grades.map(g => <tr key={g}><th>{g}</th>{STAT_TYPES.map(([type,name]) => <td key={type}>{numberInput(matrixValue(g,type),`${g}品${name}基础体质`,value => matrixChange(g,type,value))}</td>)}<td><button type="button" onClick={() => setRows(rows.filter(r => Number(r.actual_grade) !== g))}>移除此品级</button></td></tr>)}
        {kind === 'refine' && grades.flatMap(g => STAT_TYPES.map(([type,name],i) => <tr key={`${g}:${type}`}><th>{g}</th><th>{name}</th>{[0,4,6,8].map(level => <td key={level}>{numberInput(matrixValue(g,type,level),`${g}品${name}精炼${level}`,value => matrixChange(g,type,value,level))}</td>)}<td>{i === 0 && <button type="button" onClick={() => setRows(rows.filter(r => Number(r.actual_grade) !== g))}>移除此品级</button>}</td></tr>))}
        {kind === 'stone' && rows.map((row,i) => <tr key={i}><th>{row.stone_level} 级</th><td>{numberInput(row.constitution,`五行石${row.stone_level}级体质`,value => change(i,'constitution',value))}</td></tr>)}
        {['enchant','rainbow','buff'].includes(kind) && rows.map((row,i) => <tr key={i}>{kind === 'enchant' && <td><select aria-label={`第${i+1}行部位`} value={String(row.equipment_type)} onChange={e => change(i,'equipment_type',e.target.value)}>{STAT_TYPES.map(([type,name]) => <option key={type} value={type}>{name}</option>)}</select></td>}<td><input aria-label={`第${i+1}行${kind === 'buff' ? '技能名称' : '名称'}`} maxLength={100} value={String(row[kind === 'buff' ? 'skill' : 'name'] ?? '')} onChange={e => change(i,kind === 'buff' ? 'skill' : 'name',e.target.value)}/></td>{kind === 'buff' && <td>{numberInput(row.stacks,`第${i+1}行层数`,value => change(i,'stacks',value))}</td>}<td>{numberInput(row[valueField],`第${i+1}行${kind === 'buff' ? '阈值' : '体质'}`,value => change(i,valueField,value))}</td><td><button type="button" onClick={() => setRows(rows.filter((_,j) => j !== i))}>移除此行</button></td></tr>)}
      </tbody></table></div>
      {['enchant','rainbow','buff'].includes(kind) && <button type="button" onClick={() => setRows([...rows,kind === 'buff' ? {skill:'',stacks:'',threshold:''} : { ...(kind === 'enchant' ? {equipment_type:'hat'} : {}),name:'',constitution:''}])}>添加一行</button>}
      <div className="dialog-actions"><button disabled={!currentDirty} onClick={() => setDrafts(old => {const next={...old};delete next[kind];return next;})}>放弃本表草稿</button><button className="primary" disabled={!currentDirty} onClick={() => { void run(async () => setPreview(await api<CyclePreview>(`/cycles/${data.cycle.id}/data/${kind}/preview`,'POST',{expected_version:data.cycle.version,rows:payloadRows(rows)})),'已生成变更预览',undefined,false); }}>预览更改</button></div>
    </fieldset>
    {data.cycle.archived && <p className="hint">归档周期只读，可以导出或复制。</p>}
  </section>;
}
