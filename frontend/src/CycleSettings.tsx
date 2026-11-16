import { useEffect, useRef, useState } from 'react';
import { api, optionalInteger } from './api';
import { requestId } from './requestId';
import { CycleDataEditor } from './CycleDataEditor';
import { CyclePreviewPanel } from './CyclePreviewPanel';
import type { Cycle, CycleData, CyclePreview, Data, Run, Source } from './types';

function sourceFields(source: Source) {
  return {source_type:source.source_type,name:source.name,short_label:source.short_label,actual_grade:String(source.actual_grade),active:source.active};
}

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
  const [createId,setCreateId] = useState(requestId);
  useEffect(()=>setCreateId(requestId()),[form,sourceOpen,source?.id]);
  const empty = {source_type:'其他',name:'',short_label:'',actual_grade:'',active:true};
  const [fields,setFields] = useState(empty);
  const [copySources, setCopySources] = useState<string[]>([]);
  const [copyPreview,setCopyPreview] = useState<CyclePreview | null>(null);
  const [socketDraft, setSocketDraft] = useState<{ cycleId: string; version: number; types: string[] } | null>(null);
  const [dirty,setDirty] = useState(false);
  const [editorEpoch,setEditorEpoch] = useState(0);
  const sourceDirty = sourceOpen && JSON.stringify(fields) !== JSON.stringify(source ? sourceFields(source) : empty);
  const savedSource = source && config?.sources.find(item => item.id === source.id);
  const sourceConflict = sourceOpen && source && (!savedSource || JSON.stringify(sourceFields(source)) !== JSON.stringify(sourceFields(savedSource)));
  const nameDirty = !!form && name !== (form === 'rename' ? config?.cycle.name ?? '' : '');
  const unsaved = dirty || sourceDirty || nameDirty || socketDraft !== null;
  const version = data.workspace.cycles.find(c => c.id === cycleId)?.version;
  const sequence = useRef(0);
  async function read(id: string) {
    const ticket=++sequence.current;
    if (!id) {setConfig(null);setLoading(false);setLoadError('');return true;}
    setLoading(true);setLoadError('');
    try {
      const result = await api<CycleData>(`/cycles/${id}/data`);
      if (ticket !== sequence.current) return false;
      setConfig(result);
      return true;
    }
    catch (e) {
      if (ticket === sequence.current) setLoadError(e instanceof Error ? e.message : '无法读取周期');
      return false;
    }
    finally {if (ticket===sequence.current) setLoading(false);}
  }
  useEffect(() => {
    if (!cycleId || version !== undefined) void read(cycleId);
    return () => { sequence.current += 1; };
  },[cycleId,version]);
  useEffect(() => {onDirty(unsaved);},[unsaved,onDirty]);
  useEffect(() => () => onDirty(false),[onDirty]);
  function applied(id: string) {
    setCopyPreview(null);
    setForm(null);
    if (id !== cycleId) setConfig(null);
    setCycleId(id);
  }
  const cycle=config?.cycle;
  const current=cycleId === data.workspace.active_cycle_id;
  const disabled=pending || loading;
  function changeCycle(id: string) {
    if (unsaved && !window.confirm('有未保存的输入，切换周期会丢弃。确定切换？')) return;
    setSocketDraft(null);setCycleId(id);setConfig(null);setCopyPreview(null);setSourceOpen(false);setForm(null);
  }
  function removeCycle() {
    if (!cycle || unsaved || !window.confirm(`删除空周期「${cycle.name}」？有引用或配置的周期可归档。`)) return;
    void run(async () => {
      const result = await api<{ active_cycle_id: string | null }>(`/cycles/${cycle.id}?expected_version=${cycle.version}`, 'DELETE');
      changeCycle(result.active_cycle_id ?? '');
      if (!result.active_cycle_id) {
        setName('');
        setForm('create');
      }
    }, '已删除空周期');
  }
  function removeSource(item: Source) {
    if (!cycle || sourceOpen || !window.confirm(`删除来源「${item.name}」？已被装备使用的来源可以停用。`)) return;
    void run(() => api(`/cycles/${cycle.id}/sources/${item.id}?expected_version=${cycle.version}`, 'DELETE'), '已删除来源预设');
  }
  async function reloadConfig() {
    if (unsaved && !window.confirm('重新读取会丢弃未保存的输入，确定继续？')) return;
    if (await read(cycleId)) {
      setSocketDraft(null);
      setSourceOpen(false);
      setEditorEpoch(epoch => epoch + 1);
    }
  }
  return <>
    <div className="cycle-top">
    {data.workspace.cycles.length > 1 && <label>查看周期<select aria-label="查看周期" value={cycleId} disabled={disabled} onChange={e=>changeCycle(e.target.value)}>{data.workspace.cycles.map(c=><option key={c.id} value={c.id}>{c.name}{c.id===data.workspace.active_cycle_id?'（当前）':c.archived?'（已归档）':''}</option>)}</select></label>}
    {cycle && <div className="cycle-heading"><h3>{current?'当前周期：':'查看周期：'}{cycle.name}</h3>{cycle.archived && <span className="history-badge">已归档 · 只读</span>}</div>}
    {cycle && !form && !copyPreview && <div className="button-row">
      <button disabled={disabled || dirty || sourceDirty || !!socketDraft} title={dirty || sourceDirty?'先保存或取消未完成的编辑，再复制周期':undefined} onClick={()=>{setName('');setCopySources([]);setForm('copy');}}>复制为新周期</button>
      {!current && !cycle.archived && <button disabled={disabled} onClick={()=>void run(()=>api('/workspace/active-cycle','PUT',{cycle_id:cycleId}),'已切换当前周期')}>设为当前周期</button>}
      <button disabled={disabled || dirty || sourceDirty || !!socketDraft} title={dirty || sourceDirty?'先保存或取消未完成的编辑':undefined} onClick={()=>{setName('');setForm('create');}}>＋ 新建周期</button>
      <details className="cycle-management menu-disclosure"><summary>管理周期</summary><div className="button-row">{!cycle.archived && <button disabled={disabled || sourceDirty || !!socketDraft} title={sourceDirty?'先保存或取消来源编辑':undefined} onClick={()=>{setName(cycle.name);setForm('rename');}}>修改周期名称</button>}<button disabled={disabled || dirty || sourceDirty || !!socketDraft || current} title={current?'当前使用的周期不能归档':undefined} onClick={()=>void run(()=>api(`/cycles/${cycleId}/archive`,'PUT',{expected_version:cycle.version,archived:!cycle.archived}),cycle.archived?'已恢复周期':'已归档周期')}>{cycle.archived?'恢复周期':'归档周期'}</button><button className="danger" disabled={disabled || unsaved} onClick={removeCycle}>删除空周期</button><button disabled={disabled} onClick={()=>void reloadConfig()}>重新读取</button></div></details>
    </div>}
    </div>
    {cycle && !form && !copyPreview && <details className="optional-fields menu-disclosure">
      <summary>达标时不要求体质孔 · {cycle.socket_exempt_types.split(',').filter(Boolean).length} 个部位</summary>
      <p className="hint">勾选没有带孔更优选项的部位。两个戒指自动跳过。实际孔状态和五行石体质仍按装备记录。</p>
      {!cycle.has_constitution_sockets && <p className="hint">旧版全周期设置已转换为各部位选项，请按实核对。旧版录入时保存为无孔的装备可逐件更正。</p>}
      <fieldset className="button-row" aria-label="达标时不要求体质孔的部位" disabled={disabled || cycle.archived || dirty || sourceDirty}>
        {data.workspace.slots.filter(slot => !slot.key.startsWith('ring')).map(slot => {
          const types = socketDraft?.types ?? cycle.socket_exempt_types.split(',').filter(Boolean);
          return <label className="check" key={slot.key}><input type="checkbox" checked={types.includes(slot.key)} onChange={e => {
            const next = new Set(types);
            if (e.target.checked) next.add(slot.key);
            else next.delete(slot.key);
            const ordered = data.workspace.slots.map(item => item.key).filter(key => next.has(key));
            setSocketDraft(ordered.join(',') === cycle.socket_exempt_types ? null : {
              cycleId: cycle.id, version: socketDraft?.version ?? cycle.version, types: ordered,
            });
          }}/>{slot.name}</label>;
        })}
      </fieldset>
      {socketDraft && <div className="button-row">
        <button disabled={disabled} type="button" onClick={() => setSocketDraft(null)}>取消比较规则修改</button>
        <button disabled={disabled || cycle.archived || dirty || sourceDirty} type="button" className="primary" onClick={() => void run(async () => {
          const saved = await api<Cycle>(`/cycles/${socketDraft.cycleId}`, 'PUT', { name: cycle.name, expected_version: socketDraft.version, socket_exempt_types: socketDraft.types.join(',') });
          setConfig(old => old?.cycle.id === saved.id ? { ...old, cycle: saved } : old);
          setSocketDraft(null);
        }, '已更新周期体质孔规则')}>保存比较规则</button>
      </div>}
    </details>}
    {loadError && <p role="alert" className="message error">{loadError}<button disabled={disabled} onClick={()=>void read(cycleId)}>重试读取</button></p>}
    {loading && <p role="status" className="hint">正在读取周期配置…</p>}
    {form && !copyPreview && <form className="inline-form" onSubmit={e=>{e.preventDefault();void run(async()=>{
      if(form==='copy') setCopyPreview(await api<CyclePreview>(`/cycles/${cycleId}/copy-preview`,'POST',{name,expected_version:cycle!.version,source_ids:copySources}));
      else {const result=await api<{id:string}>(form==='rename'?`/cycles/${cycleId}`:'/cycles',form==='rename'?'PUT':'POST',form==='rename'?{name,expected_version:cycle!.version}:{name,request_id:createId});setName('');setForm(null);setSourceOpen(false);applied(result.id);}
    },form==='copy'?'已生成周期复制预览':'周期已保存',undefined,form!=='copy');}}><label>{form==='rename'?'修改名称':form==='copy'?'新周期名称（复制配置）':'新周期名称'}<input aria-label="周期名称" placeholder="填写实际的 25 人副本名" required maxLength={100} value={name} disabled={disabled} onChange={e=>setName(e.target.value)}/></label>{form === 'copy' && config && <fieldset className="button-row"><legend>可选复制的来源</legend>{config.sources.map(item => <label className="check" key={item.id}><input type="checkbox" checked={copySources.includes(item.id)} onChange={e => setCopySources(old => e.target.checked ? [...old, item.id] : old.filter(id => id !== item.id))}/>{item.name} · {item.actual_grade} 品</label>)}</fieldset>}{cycle && <button type="button" disabled={disabled} onClick={()=>{setForm(null);setCopyPreview(null);}}>取消</button>}<button className="primary" disabled={disabled}>{form==='rename'?'保存名称':form==='copy'?'预览复制':'创建周期'}</button></form>}
    {copyPreview && <CyclePreviewPanel cycleId={cycleId} preview={copyPreview} run={run} pending={pending} cancel={()=>setCopyPreview(null)} applied={applied}/>}
    {config && !copyPreview && <div hidden={!!form}>
      {cycle?.copied_from_id && <p className="hint">复制自：{data.workspace.cycles.find(c=>c.id===cycle.copied_from_id)?.name ?? '历史周期'}。旧装备仍使用原所属周期。</p>}
      <div className="menu-tabs"><button aria-pressed={section==='sources'} onClick={()=>setSection('sources')}>常用来源</button><button aria-pressed={section==='data'} onClick={()=>setSection('data')}>数值配置{dirty?' · 未保存':''}</button></div>
      <div hidden={section!=='sources'}>
        <p className="hint">来源决定装备品级。已被装备引用的来源品级锁定，换周期仍可选用启用的旧来源。</p>
        <div className="entity-list">{config.sources.map(s=><div className="entity-row" key={s.id}><span>{s.name}<small>{s.short_label} · {`${s.actual_grade} 品${s.active ? '' : ' · 已停用'}`}</small></span>{!cycle?.archived && <>
          <button disabled={disabled} onClick={()=>{if(sourceDirty&&!window.confirm('来源还没保存，重新编辑会丢弃当前输入。确定继续？'))return;setSource(s);setFields(sourceFields(s));setSourceOpen(true);}}>编辑来源</button>
          <button className="danger" disabled={disabled || sourceOpen} onClick={() => removeSource(s)}>删除来源</button>
        </>}</div>)}</div>
        {config.sources.length===0 && <p className="hint">还没有常用来源。例如“25 人普通”，表格中简称为“pt”。</p>}
        {!sourceOpen && !cycle?.archived && <button disabled={disabled} onClick={()=>{setSource(null);setFields(empty);setSourceOpen(true);}}>＋ 添加来源</button>}
        {sourceConflict && <p role="alert" className="message error">该来源已在别处修改或删除。当前输入已保留，请核对后重新编辑。</p>}
        {sourceOpen && !cycle?.archived && <form className="subform" onSubmit={e=>{e.preventDefault();if(sourceConflict)return;void run(async()=>{
          await api(`/cycles/${cycleId}/sources${source?`/${source.id}`:''}`,source?'PUT':'POST',{...fields,actual_grade:optionalInteger(fields.actual_grade),expected_version:cycle!.version,...(!source?{request_id:createId}:{})});setSourceOpen(false);
        },'来源已保存');}}><fieldset disabled={disabled}><h3>{source?'编辑来源':'添加来源'}</h3><div className="form-grid"><label>来源名称<input aria-label="来源名称" required maxLength={100} placeholder="例如：25 人普通" value={fields.name} onChange={e=>setFields({...fields,name:e.target.value})}/></label><label>表格简称<input aria-label="来源简称" required maxLength={12} placeholder="例如：pt" value={fields.short_label} onChange={e=>setFields({...fields,short_label:e.target.value})}/></label><label>真实品级<input aria-label="来源真实品级" required type="number" min="1" max="2147483647" value={fields.actual_grade} onChange={e=>setFields({...fields,actual_grade:e.target.value})}/></label>{source && <label className="check"><input type="checkbox" checked={fields.active} onChange={e => setFields({ ...fields, active: e.target.checked })}/>可用于录入新装备</label>}</div><div className="dialog-actions"><button type="button" onClick={()=>setSourceOpen(false)}>取消</button><button className="primary" disabled={!!sourceConflict}>保存来源</button></div></fieldset></form>}
      </div>
      <div hidden={section!=='data'}><CycleDataEditor key={`${cycleId}:${editorEpoch}`} data={config} run={run} pending={disabled} applied={applied} onDirty={setDirty}/></div>
    </div>}
  </>;
}
