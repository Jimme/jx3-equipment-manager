import { useEffect, useState } from 'react';
import { api } from './api';
import type { BackupFile, DataPreview, Run } from './types';

async function download(url: string, fallback: string) {
  const response=await fetch(url);
  if(!response.ok) {const error=await response.json().catch(()=>({}));throw new Error(error.detail??'下载失败，请重试');}
  const blob=await response.blob();
  const name=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]??fallback;
  const object=URL.createObjectURL(blob);
  const link=document.createElement('a');link.href=object;link.download=name;document.body.appendChild(link);link.click();link.remove();
  window.setTimeout(()=>URL.revokeObjectURL(object),30000);
}

export function DataManager({ run, pending, close, replaced }: { run: Run; pending: boolean; close: () => void; replaced: () => void }) {
  const [backups,setBackups]=useState<BackupFile[]>([]);
  const [listError,setListError]=useState('');
  const [preview,setPreview]=useState<DataPreview|null>(null);
  const [confirmed,setConfirmed]=useState(false);
  const [visible,setVisible]=useState(20);
  const [filename,setFilename]=useState('');
  async function readBackups() {
    try {
      setBackups(await api<BackupFile[]>('/data/backups'));
      setListError('');
    } catch (e) {
      setListError(e instanceof Error ? e.message : '备份列表读取失败');
    }
  }
  useEffect(()=>{void readBackups();},[]);
  function install(value: DataPreview, name: string) {setPreview(value);setConfirmed(false);setFilename(name);}
  if(preview) return <section aria-label="工作空间替换预览">
    <h3>{preview.kind==='import'?'JSON 导入预览':'数据库恢复预览'}</h3><p className="hint">文件：{filename}</p>
    <p className="replacement-note">整体替换当前工作空间，不合并。账号、角色、装备、库存、掉落和周期配置均以文件为准。确认前会自动备份当前数据；校验或保存失败不会部分替换。</p>
    <div className="config-scroll"><table className="daily-table"><thead><tr><th>内容</th><th>当前</th><th>替换后</th></tr></thead><tbody>{Object.keys(preview.before).map(key=><tr key={key}><th>{key}</th><td>{preview.before[key]}</td><td>{preview.after[key]}</td></tr>)}</tbody></table></div>
    <details className="optional-fields"><summary>查看名称与记录变化</summary>{Object.entries(preview.names).map(([label,names])=><p className="hint" key={label}>{label}（各最多 20 项）：{names.before.join('、')||'无'} → {names.after.join('、')||'无'}</p>)}<div className="config-scroll"><table className="daily-table"><thead><tr><th>内容</th><th>新增</th><th>修改</th><th>删除</th></tr></thead><tbody>{preview.changes.map(change=><tr key={change.label}><th>{change.label}</th><td>{change.added}</td><td>{change.changed}</td><td>{change.deleted}</td></tr>)}</tbody></table></div></details>
    {preview.unchanged?<p className="hint">文件内容与当前数据相同，无需替换。</p>:<label className="check"><input type="checkbox" aria-label="确认整体替换工作空间" checked={confirmed} disabled={pending} onChange={e=>setConfirmed(e.target.checked)}/>我确认使用这份文件整体替换当前工作空间。</label>}
    <div className="dialog-actions"><button disabled={pending} onClick={()=>{setPreview(null);setConfirmed(false);}}>返回</button><button className="primary" disabled={pending||!confirmed||preview.unchanged} onClick={()=>void run(async()=>{
      await api<{backup:string|null}>(`/data/changes/${preview.preview_id}/apply`,'POST',{confirm_replace:true});
      replaced();
    },'工作空间已替换，原数据已自动备份',close)}>确认替换并备份</button></div>
  </section>;
  return <section aria-label="数据与备份">
    <fieldset disabled={pending}><div className="button-row">
      <button onClick={()=>void run(()=>download('/api/data/export','jx3-workspace.json'),'JSON 已导出',undefined,false)}>导出 JSON</button>
      <button onClick={()=>void run(async()=>{
        const result=await api<{name:string}>('/data/backups','POST');
        await readBackups();
        try {await download(`/api/data/backups/${result.name}`,result.name);} catch {throw new Error('数据库备份已生成，但下载失败；可从下方已有备份列表重试下载。');}
      },'数据库已备份并下载',undefined,false)}>备份并下载数据库</button>
      <label className="button-link file-button">导入或恢复文件<input aria-label="导入或恢复文件" type="file" accept=".json,.sqlite3,.db,application/json,application/vnd.sqlite3" disabled={pending} onChange={e=>{
        const file=e.target.files?.[0];e.target.value='';if(!file)return;
        void run(async()=>{
          if(file.size>32*1024*1024)throw new Error('文件最多 32 MB');
          const json=file.name.toLowerCase().endsWith('.json');
          if(!json&&!/\.(sqlite3|db)$/i.test(file.name))throw new Error('请选择 JSON 或 SQLite 数据库备份文件');
          let response: Response;
          try {response=await fetch(`/api/data/${json?'import':'restore'}-preview`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});} catch {throw new Error('无法连接应用，请检查服务是否正在运行。');}
          if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.detail??'文件校验失败');}
          install(await response.json(),file.name);
        },'文件校验通过，请核对替换预览',undefined,false);
      }}/></label>
    </div></fieldset>
    <details className="optional-fields"><summary>导入与恢复说明</summary><p className="hint">JSON 用于完整迁移。32 MB 以内的兼容数据库备份可在线恢复，其他备份可按 README 停机恢复。替换数据前会自动备份当前数据库。</p></details>
    <section aria-label="已有数据库备份">
      <div className="backup-heading"><h3>已有数据库备份 · {backups.length} 份</h3><button className="quiet-button" disabled={pending} onClick={()=>void run(readBackups,'',undefined,false)}>刷新备份列表</button></div>
      {listError ? <p role="alert">{listError}</p> : !backups.length && <p className="hint">暂无备份</p>}
      <div className="backup-list">{backups.slice(0,visible).map(backup=>{
        const path = `/data/backups/${encodeURIComponent(backup.name)}`;
        return <div className="backup-row" role="group" aria-label={backup.name} key={backup.name}>
          <div><strong>{backup.name}</strong><small>{new Date(backup.created_at).toLocaleString()} · {(backup.size/1024).toFixed(0)} KB</small></div>
          <div className="button-row">
            <button disabled={pending} onClick={()=>void run(()=>download(`/api${path}`,backup.name),'备份已下载',undefined,false)}>下载</button>
            <button disabled={pending} onClick={()=>void run(async()=>install(await api<DataPreview>(`${path}/restore-preview`,'POST'),backup.name),'',undefined,false)}>预览恢复</button>
            <button className="danger" disabled={pending} onClick={()=>{
              if (!window.confirm(`永久删除服务器上的备份「${backup.name}」？当前工作空间和已下载的副本不受影响。`)) return;
              void run(async()=>{
                await api(path,'DELETE');
                setBackups(current=>current.filter(file=>file.name!==backup.name));
              },'已删除备份',undefined,false);
            }}>删除备份</button>
          </div>
        </div>;
      })}</div>
      {backups.length>visible&&<button disabled={pending} onClick={()=>setVisible(visible+20)}>显示更多备份</button>}
    </section>
  </section>;
}
