import { useEffect, useRef, useState } from 'react';
import { api, optionalInteger } from './api';
import { CycleDataEditor } from './CycleDataEditor';
import { CyclePreviewPanel } from './CyclePreviewPanel';
import type { CycleData, CyclePreview, Data, Run, Source } from './types';

export function CycleSettings({ data, run, pending, onDirty }: { data: Data; run: Run; pending: boolean; onDirty: (dirty: boolean) => void }) {
  const [cycleId,setCycleId] = useState(data.cycleId);
  const [config,setConfig] = useState<CycleData | null>(null);
  const [loading,setLoading] = useState(false);
  const [loadError,setLoadError] = useState('');
  const [section,setSection] = useState<'sources'|'data'>('sources');
  const [form,setForm] = useState<'create'|'rename'|'copy'|null>(data.cycleId ? null : 'create');
  const [name,setName] = useState('');
  const [source,setSource] = useState<Source | null>(null);
  const [sourceOpen,setSourceOpen] = useState(false);
  const empty = {source_type:'其他',name:'',short_label:'',default_grade:''};
  const [fields,setFields] = useState(empty);
  const [copyPreview,setCopyPreview] = useState<CyclePreview | null>(null);
  const [dirty,setDirty] = useState(false);
  const [editorEpoch,setEditorEpoch] = useState(0);
  const sourceDirty = sourceOpen && JSON.stringify(fields) !== JSON.stringify(source ? {source_type:source.source_type,name:source.name,short_label:source.short_label,default_grade:source.default_grade?.toString()??''} : empty);
  const nameDirty = !!form && name !== (form === 'rename' ? config?.cycle.name ?? '' : '');
  const unsaved = dirty || sourceDirty || nameDirty;
  const version = data.workspace.cycles.find(c => c.id === cycleId)?.version;
  const sequence = useRef(0);
  async function read(id: string) {
    const ticket=++sequence.current;
    if (!id) {setConfig(null);return;}
    setLoading(true);setLoadError('');
    try { const result=await api<CycleData>(`/cycles/${id}/data`); if (ticket===sequence.current) setConfig(result); }
    catch (e) { if (ticket===sequence.current) setLoadError(e instanceof Error ? e.message : '无法读取周期'); }
    finally {if (ticket===sequence.current) setLoading(false);}
  }
  useEffect(() => { void read(cycleId); },[cycleId,version]);
  useEffect(() => {onDirty(unsaved);},[unsaved,onDirty]);
  useEffect(() => () => onDirty(false),[onDirty]);
  async function applied(id: string) {setCopyPreview(null);setForm(null);setCycleId(id);await read(id);}
  const cycle=config?.cycle;
  const current=cycleId === data.workspace.active_cycle_id;
  const disabled=pending || loading;
  function changeCycle(id: string) {
    if (unsaved && !window.confirm('有未保存的输入，切换周期会丢弃。确定切换？')) return;
    setCycleId(id);setConfig(null);setCopyPreview(null);setSourceOpen(false);setForm(null);
  }
  return <>
    {data.workspace.cycles.length > 1 && <label>查看周期<select aria-label="查看周期" value={cycleId} disabled={disabled} onChange={e=>changeCycle(e.target.value)}>{data.workspace.cycles.map(c=><option key={c.id} value={c.id}>{c.name}{c.id===data.workspace.active_cycle_id?'（当前）':c.archived?'（已归档）':''}</option>)}</select></label>}
    {cycle && <div className="cycle-heading"><h3>{current?'当前周期：':'查看周期：'}{cycle.name}</h3>{cycle.archived && <span className="history-badge">已归档 · 只读</span>}</div>}
    {loadError && <p role="alert" className="message error">{loadError}<button disabled={disabled} onClick={()=>void read(cycleId)}>重试读取</button></p>}
    {loading && <p role="status" className="hint">正在读取周期配置…</p>}
    {cycle && !form && !copyPreview && <div className="button-row">
      <button disabled={disabled || dirty || sourceDirty} title={dirty || sourceDirty?'先保存或取消未完成的编辑，再复制周期':undefined} onClick={()=>{setName('');setForm('copy');}}>复制为新周期</button>
      {!current && !cycle.archived && <button disabled={disabled} onClick={()=>void run(()=>api('/workspace/active-cycle','PUT',{cycle_id:cycleId}),'已切换当前周期')}>设为当前周期</button>}
      <button disabled={disabled || dirty || sourceDirty} title={dirty || sourceDirty?'先保存或取消未完成的编辑':undefined} onClick={()=>{setName('');setForm('create');}}>＋ 新建周期</button>
      <details className="cycle-management"><summary>管理周期</summary><div className="button-row">{!cycle.archived && <button disabled={disabled || sourceDirty} title={sourceDirty?'先保存或取消来源编辑':undefined} onClick={()=>{setName(cycle.name);setForm('rename');}}>修改周期名称</button>}<button disabled={disabled || dirty || sourceDirty || current} title={current?'当前使用的周期不能归档':undefined} onClick={()=>void run(async()=>{await api(`/cycles/${cycleId}/archive`,'PUT',{expected_version:cycle.version,archived:!cycle.archived});await read(cycleId);},cycle.archived?'已恢复周期':'已归档周期')}>{cycle.archived?'恢复周期':'归档周期'}</button><button disabled={disabled} onClick={()=>{if(unsaved&&!window.confirm('重新读取会丢弃未保存的输入，确定继续？'))return;void run(async()=>{setSourceOpen(false);setEditorEpoch(v=>v+1);await read(cycleId);},'已重新读取配置',undefined,false);}}>重新读取</button></div></details>
    </div>}
    {form && !copyPreview && <form className="inline-form" onSubmit={e=>{e.preventDefault();void run(async()=>{
      if(form==='copy') setCopyPreview(await api<CyclePreview>(`/cycles/${cycleId}/copy-preview`,'POST',{name,expected_version:cycle!.version}));
      else {const result=await api<{id:string}>(form==='rename'?`/cycles/${cycleId}`:'/cycles',form==='rename'?'PUT':'POST',form==='rename'?{name,expected_version:cycle!.version}:{name});setName('');setForm(null);setSourceOpen(false);await applied(result.id);}
    },form==='copy'?'已生成周期复制预览':'周期已保存',undefined,form!=='copy');}}><label>{form==='rename'?'修改名称':form==='copy'?'新周期名称（复制配置）':'新周期名称'}<input aria-label="周期名称" placeholder="填写实际的 25 人副本名" required maxLength={100} value={name} disabled={disabled} onChange={e=>setName(e.target.value)}/></label>{cycle && <button type="button" disabled={disabled} onClick={()=>{setForm(null);setCopyPreview(null);}}>取消</button>}<button className="primary" disabled={disabled}>{form==='rename'?'保存名称':form==='copy'?'预览复制':'创建周期'}</button></form>}
    {copyPreview && <CyclePreviewPanel cycleId={cycleId} preview={copyPreview} run={run} pending={pending} cancel={()=>setCopyPreview(null)} applied={applied}/>}
    {config && !copyPreview && <div hidden={!!form}>
      {cycle?.copied_from_id && <p className="hint">复制自：{data.workspace.cycles.find(c=>c.id===cycle.copied_from_id)?.name ?? '历史周期'}。旧装备仍使用原所属周期。</p>}
      <div className="editor-modes"><button className={section==='sources'?'selected':''} onClick={()=>setSection('sources')}>常用来源</button><button className={section==='data'?'selected':''} onClick={()=>setSection('data')}>数值配置{dirty?' · 未保存':''}</button></div>
      <div hidden={section!=='sources'}>
        <p className="hint">选来源时带出默认品级，单件仍可修改；改预设不追溯修改已录装备。</p>
        <div className="entity-list">{config.sources.map(s=><div className="entity-row" key={s.id}><span>{s.name}<small>{s.short_label} · {s.default_grade===null?'品级未设置':`${s.default_grade} 品`}</small></span>{!cycle?.archived && <button disabled={disabled} onClick={()=>{if(sourceDirty&&!window.confirm('来源还没保存，编辑另一条会丢弃当前输入。确定继续？'))return;setSource(s);setFields({source_type:s.source_type,name:s.name,short_label:s.short_label,default_grade:s.default_grade?.toString()??''});setSourceOpen(true);}}>编辑来源</button>}</div>)}</div>
        {config.sources.length===0 && <p className="hint">还没有常用来源。例如“25 人普通”，表格中简称为“pt”。</p>}
        {!sourceOpen && !cycle?.archived && <button disabled={disabled} onClick={()=>{setSource(null);setFields(empty);setSourceOpen(true);}}>＋ 添加来源</button>}
        {sourceOpen && !cycle?.archived && <form className="subform" onSubmit={e=>{e.preventDefault();void run(async()=>{
          await api(`/cycles/${cycleId}/sources${source?`/${source.id}`:''}`,source?'PUT':'POST',{...fields,default_grade:optionalInteger(fields.default_grade),expected_version:cycle!.version});setSourceOpen(false);await read(cycleId);
        },'来源已保存');}}><fieldset disabled={disabled}><h3>{source?'编辑来源':'添加来源'}</h3><div className="form-grid"><label>来源名称<input aria-label="来源名称" required maxLength={100} placeholder="例如：25 人普通" value={fields.name} onChange={e=>setFields({...fields,name:e.target.value})}/></label><label>表格简称<input aria-label="来源简称" required maxLength={12} placeholder="例如：pt" value={fields.short_label} onChange={e=>setFields({...fields,short_label:e.target.value})}/></label><label>默认品级（可空）<input aria-label="默认真实品级" type="number" min="1" max="2147483647" value={fields.default_grade} onChange={e=>setFields({...fields,default_grade:e.target.value})}/></label></div><div className="dialog-actions"><button type="button" onClick={()=>setSourceOpen(false)}>取消</button><button className="primary">保存来源</button></div></fieldset></form>}
      </div>
      <div hidden={section!=='data'}><CycleDataEditor key={`${cycleId}:${editorEpoch}`} data={config} run={run} pending={disabled} applied={applied} onDirty={setDirty}/></div>
    </div>}
  </>;
}
