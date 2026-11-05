import { requestId as newRequestId } from './requestId';
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, optionalInteger } from './api';
import type { Cell, Character, DailyData, Data, Matrix, RaidDifficulty, Run, Source, Template, Workspace } from './types';
import { Modal } from './Modal';
import { EquipmentForm } from './EquipmentForm';
import { InventoryWorkspace } from './InventoryWorkspace';
import { WarehouseEquip } from './WarehouseEquip';
import { choiceSummary } from './ChoicesFields';
import { DataManager } from './DataManager';
import { GrowthEditor } from './GrowthEditor';
import { CycleSettings } from './CycleSettings';
import { People } from './People';
import './style.css';

type Editing = { character: Character; slot: string; slotName: string; cell: Cell };
type Panel = 'people' | 'sources' | 'data' | null;
const raidChecks = [['10', '10人'], ['25pt', '25pt'], ['25yx', '25yx']] as const;

function rememberedAccount(): string {
  try { return window.localStorage.getItem('jx3-selected-account') ?? ''; } catch { return ''; }
}

function App() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(false);
  const [panel, setPanel] = useState<Panel>(null);
  const [peopleDirty, setPeopleDirty] = useState(false);
  const [cycleDirty, setCycleDirty] = useState(false);
  const [growthCharacter, setGrowthCharacter] = useState<Character | null>(null);
  const [growthDirty, setGrowthDirty] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [target, setTarget] = useState('');
  const [lastEquipment, setLastEquipment] = useState<Template | null>(null);
  const [gapsOnly, setGapsOnly] = useState(false);
  const loadVersion = useRef(0);
  const pendingRef = useRef(false);
  const selectedAccount = useRef('');

  async function load(accountId?: string, cycleId?: string) {
    const version = ++loadVersion.current;
    const workspace = await api<Workspace>('/workspace');
    const preferredAccount = accountId || selectedAccount.current || rememberedAccount();
    const activeAccount = workspace.accounts.find(a => a.id === preferredAccount && !a.archived) ?? workspace.accounts.find(a => !a.archived);
    const selectedCycle = workspace.cycles.find(c => c.id === cycleId)?.id ?? workspace.active_cycle_id ?? '';
    const [matrix, characters, sources, templates, daily] = await Promise.all([
      activeAccount ? api<Matrix>(`/accounts/${activeAccount.id}/matrix`) : Promise.resolve(null),
      activeAccount ? api<Character[]>(`/accounts/${activeAccount.id}/characters`) : Promise.resolve([]),
      selectedCycle ? api<Source[]>(`/cycles/${selectedCycle}/sources`) : Promise.resolve([]),
      api<Template[]>('/templates'),
      activeAccount ? api<DailyData>(`/accounts/${activeAccount.id}/daily`) : Promise.resolve(null),
    ]);
    if (version !== loadVersion.current) return;
    selectedAccount.current = activeAccount?.id ?? '';
    try { window.localStorage.setItem('jx3-selected-account', selectedAccount.current); } catch { /* Storage-disabled browsers still work without remembered selection. */ }
    setData({ workspace, matrix, characters, sources, templates, daily, accountId: activeAccount?.id ?? '', cycleId: selectedCycle });
    setTarget(activeAccount?.target_grade?.toString() ?? '');
  }

  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!cycleDirty && !growthDirty && !peopleDirty) return;
    const protectDraft = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protectDraft);
    return () => window.removeEventListener('beforeunload', protectDraft);
  }, [cycleDirty,growthDirty,peopleDirty]);

  const run: Run = async (action, success = '已保存', close, reload = true) => {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setPending(true); setError(''); setNotice('');
    let completed = false;
    try {
      await action();
      completed = true;
      if (reload) await load(selectedAccount.current);
      pendingRef.current = false;
      close?.(); setNotice(success);
      return true;
    } catch (e) {
      const message = e instanceof Error ? e.message : '操作失败，请重试';
      setError(completed && reload ? `操作已提交，但页面刷新失败，请刷新核对：${message}` : message);
      return false;
    } finally { pendingRef.current = false; setPending(false); }
  };

  const refresh = () => run(() => load(data?.accountId, data?.cycleId), '已刷新', undefined, false);
  const closePanel = () => { if (pendingRef.current) return; if (panel === 'people' && peopleDirty && !window.confirm('账号或角色名称还没保存，关闭会丢弃。确定关闭？')) return; if (panel === 'sources' && cycleDirty && !window.confirm('有未保存的输入，关闭会丢弃。确定关闭？')) return; setPanel(null); setError(''); setCycleDirty(false); setPeopleDirty(false); };
  const closeGrowth = () => { if (pendingRef.current) return; if (growthDirty && !window.confirm('养成输入还没保存，关闭会丢弃。确定关闭？')) return; setGrowthCharacter(null); setGrowthDirty(false); setError(''); };
  const closeEquipment = () => { if (!pendingRef.current) { setEditing(null); setError(''); } };

  async function setRaidCheck(character: Character, difficulty: RaidDifficulty, checked: boolean) {
    if (pendingRef.current) return;
    function showCheck(cleared: boolean) {
      const update = (row: Character) => row.id === character.id ? { ...row, [`raid_${difficulty}`]: cleared } : row;
      setData(previous => {
        if (!previous?.matrix || previous.accountId !== character.account_id) return previous;
        return { ...previous, characters: previous.characters.map(update), matrix: { ...previous.matrix, characters: previous.matrix.characters.map(update) } };
      });
    }
    showCheck(checked);
    const saved = await run(() => api(
      `/accounts/${character.account_id}/characters/${character.id}/raid-checks/${difficulty}`,
      'PUT', { cleared: checked },
    ), '', undefined, false);
    if (!saved) showCheck(character[`raid_${difficulty}`]);
  }

  if (!data) return <main className="loading"><h1>剑网三 · 坦克装备簿</h1>{error ? <><p role="alert">{error}</p><button onClick={() => { setError(''); void refresh(); }}>重试连接</button></> : <p>正在打开装备簿…</p>}</main>;
  const { workspace, matrix, accountId, cycleId } = data;
  const account = matrix?.account;
  const grades = [...new Set([...data.sources.map(s => s.default_grade), ...data.templates.map(t => t.actual_grade)].filter((v): v is number => v !== null))].sort((a, b) => a - b);

  return <main>
    <header className="masthead">
      <div><div className="eyebrow">JX3 EQUIPMENT JOURNAL <span>本地工作空间</span></div><h1>坦克装备簿<span className="seal">装备</span></h1><p>逐格记录，按真实品级看清每一件装备。</p></div>
      <div className="header-actions"><button onClick={refresh} disabled={pending}>刷新</button><button onClick={()=>{setError('');setPanel('data');}}>数据与备份</button><button onClick={() => setPanel('sources')}>周期与来源</button><button className="primary" onClick={() => setPanel('people')}>账号与角色</button></div>
    </header>
    {error && !panel && !editing && <div role="alert" className="message error">{error}</div>}
    {notice && <div role="status" className="message success">{notice}</div>}
    <section className="toolbar" aria-label="装备比较设置">
      <label>游戏账号<select aria-label="游戏账号" value={accountId} disabled={pending} onChange={e => { const id = e.target.value; void run(() => load(id, cycleId), '已切换账号', undefined, false); }}>
        {!accountId && <option value="">请添加账号</option>}{workspace.accounts.filter(a => !a.archived).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select></label>
      <label>装备周期<select aria-label="装备周期" value={cycleId} disabled={pending} onChange={e => { const id = e.target.value; void run(async () => { await api('/workspace/active-cycle', 'PUT', { cycle_id: id }); await load(accountId, id); }, '已切换周期', undefined, false); }}>
        {!cycleId && <option value="">请创建周期</option>}{workspace.cycles.filter(c => !c.archived).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select></label>
      <form className="target-form" onSubmit={e => { e.preventDefault(); if (account) void run(() => api(`/accounts/${accountId}/preferences`, 'PUT', { target_grade: optionalInteger(target), comparison_mode: account.comparison_mode }), '已更新目标'); }}>
        <label>目标真实品级<input aria-label="目标真实品级" type="number" min="1" max="2147483647" step="1" list="configured-grades" placeholder="未设置" value={target} onChange={e => setTarget(e.target.value)} disabled={!account || pending}/></label>
        <datalist id="configured-grades">{grades.map(g => <option key={g} value={g}/>)}</datalist><button disabled={!account || pending}>应用</button>
      </form>
      <label>比较模式<select aria-label="比较模式" value={account?.comparison_mode ?? 'strict'} disabled={!account || pending} onChange={e => { const mode = e.target.value; void run(() => api(`/accounts/${accountId}/preferences`, 'PUT', { target_grade: account?.target_grade, comparison_mode: mode }), '已切换比较模式'); }}><option value="strict">严格达标</option><option value="grade">仅品级</option><option value="combined">综合达标</option></select></label>
    </section>
    {!account ? <section className="empty"><span className="empty-mark">册</span><h2>从第一个游戏账号开始</h2><p>添加账号和角色，即可建立你的 12 部位装备表。体质数据可以以后再补。</p><button className="primary" onClick={() => setPanel('people')}>添加账号与角色</button></section> : <section className="matrix-section">
      <div className="section-heading"><div><h2>{account.name}<span>装备矩阵</span></h2><p>点击格子录入或更换装备；切换周期不改写旧装备。</p></div><label className="check"><input type="checkbox" checked={gapsOnly} onChange={e => setGapsOnly(e.target.checked)}/>只强调未达标</label></div>
      <div className="legend">{account.comparison_mode === 'combined' ? <><span><i className="dot complete-dot"/>完全达标 · 白底</span><span><i className="dot gap-dot"/>仅品级达标 · 淡黄</span><span><i className="dot insufficient-dot"/>品级不足 / 未装备 · 淡红</span></> : <><span><i className="dot gap-dot"/>未达标 / 未装备</span><span><i className="dot met-dot"/>已达标 · 弱化显示</span></>}<span>标签：无体质孔加「-」，绿字达标加「+」</span></div>
      {matrix!.characters.length === 0 ? <div className="empty"><h2>这个账号还没有角色</h2><p>添加角色后，装备部位会按固定顺序自动展开。</p><button onClick={() => setPanel('people')}>添加角色</button></div> : <div className="matrix-scroll" tabIndex={0} aria-label="可横向滚动的装备矩阵"><table className={`matrix mode-${account.comparison_mode} ${gapsOnly ? 'focus-gaps' : ''}`}>
        <thead><tr><th scope="col">装备部位</th>{matrix!.characters.map(c => <th scope="col" key={c.id}>
          <div className="character-heading">
            <strong>{c.name}</strong>
            <fieldset className="raid-checks" disabled={pending} aria-label={`${c.name} 副本完成记录`}>
              {raidChecks.map(([difficulty, label]) => <label className="check" key={difficulty}>
                <input type="checkbox" checked={c[`raid_${difficulty}`]} onChange={event => setRaidCheck(c, difficulty, event.target.checked)}/>{label}
              </label>)}
            </fieldset>
          </div>
          <div className="character-growth">
            <small>{matrix!.growth[c.id]?.equipment_total === null ? `已知体质 ${matrix!.growth[c.id]?.known_subtotal ?? 0} · 待完善` : `装备体质 ${matrix!.growth[c.id]?.equipment_total}`}</small>
            <button className="growth-entry" disabled={pending} onClick={() => { setError(''); setGrowthCharacter(c); }} aria-label={`${c.name} 养成与体质`}>养成与体质</button>
          </div>
        </th>)}</tr></thead>
        <tbody>{workspace.slots.map((slot, index) => <tr key={slot.key}><th scope="row"><span className="row-number">{String(index + 1).padStart(2, '0')}</span>{slot.name}</th>{matrix!.characters.map(c => {
          const cell = matrix!.cells[c.id][slot.key];
          const equipment = cell.equipment;
          return <td key={c.id} className={`cell-${cell.status}`}><button className="equipment-cell" disabled={pending} aria-label={`${c.name} ${slot.name}`} title={equipment ? `${equipment.source_name} · ${equipment.actual_grade}品\n${cell.reasons.join('；') || '已达标'}\n${equipment.note}` : cell.reasons.join('；')} onClick={() => setEditing({ character: c, slot: slot.key, slotName: slot.name, cell })}>
            <span className="cell-top"><strong>{cell.label}</strong><span className="state-mark">{cell.status === 'met' ? '✓' : cell.status === 'unset' ? '·' : cell.status === 'grade_met' ? '△' : '!'}</span></span>
            <span className="cell-detail">{equipment ? `${equipment.actual_grade} 品` : '点击录入'}<span>{cell.status === 'met' ? (account.comparison_mode === 'combined' ? '完全达标' : '已达标') : cell.status === 'grade_met' ? '仅品级达标' : cell.status === 'unset' ? '目标待设置' : equipment ? (account.comparison_mode === 'combined' ? '品级不足' : '待提升') : '空缺'}</span></span>
          </button></td>;
        })}</tr>)}</tbody>
      </table></div>}
      <p className="matrix-note">{account.target_grade === null ? '尚未设置目标，当前不作达标判断。' : account.comparison_mode !== 'grade' ? `目标 ${account.target_grade} 品：高于目标即完全达标；同品级需同时有体质孔、绿字达标。${account.comparison_mode === 'combined' ? '同品级缺孔或绿字未达标时显示淡黄。' : ''}` : `目标 ${account.target_grade} 品：只比较真实品级。`} 体质数据不影响装备比较。</p>
    </section>}
    {account && <InventoryWorkspace key={accountId} data={data} run={run} pending={pending} error={error}/> }
    <footer>数据保存在应用的 data 目录 · 无需游戏连接<span>v0.1.5</span></footer>
    {panel && <Modal title={panel === 'people' ? '账号与角色' : panel === 'data' ? '数据与备份' : '周期与来源'} close={closePanel} wide={panel !== 'people'} busy={pending}>
      {error && <p role="alert" className="message error">{error}</p>}
      <fieldset disabled={pending}>{panel === 'people' ? <People data={data} run={run} selectAccount={id => load(id, cycleId)} onDirty={setPeopleDirty}/> : panel === 'data' ? <DataManager run={run} pending={pending} close={closePanel} replaced={()=>setLastEquipment(null)}/> : <CycleSettings data={data} run={run} pending={pending} onDirty={setCycleDirty}/>}</fieldset>
      {pending && <p role="status">正在保存…</p>}
    </Modal>}
    {growthCharacter && <Modal title={`${growthCharacter.name} · 养成与体质`} close={closeGrowth} wide busy={pending}>
      {error && <p role="alert" className="message error">{error}</p>}
      <GrowthEditor accountId={accountId} characterId={growthCharacter.id} run={run} pending={pending} onDirty={setGrowthDirty}/>
    </Modal>}
    {editing && <Modal title={`${editing.character.name} · ${editing.slotName}`} close={closeEquipment} busy={pending} compact>
      {error && <p role="alert" className="message error">{error}</p>}
      <EquipmentEditor data={data} editing={editing} pending={pending} run={run} close={closeEquipment} lastEquipment={lastEquipment} saved={setLastEquipment}/>
    </Modal>}
  </main>;
}

function EquipmentEditor({ data, editing, pending, run, close, lastEquipment, saved }: { data: Data; editing: Editing; pending: boolean; run: Run; close: () => void; lastEquipment: Template | null; saved: (t: Template) => void }) {
  const type = editing.slot.startsWith('ring') ? 'ring' : editing.slot;
  const stock = data.daily!.warehouse.filter(item => item.equipment.equipment_type === type);
  const [mode, setMode] = useState<'record' | 'new' | 'warehouse'>(() => !editing.cell.equipment && stock.length > 0 ? 'warehouse' : 'record');
  const [selectedId, setSelectedId] = useState(stock[0]?.id ?? '');
  const [requestId, setRequestId] = useState(() => newRequestId());
  const path = `/accounts/${data.accountId}/characters/${editing.character.id}/equipment/${editing.slot}`;
  const editingExisting = mode === 'record' && !!editing.cell.equipment;
  const selected = stock.find(item => item.id === selectedId);
  const form = <EquipmentForm key={mode} data={data} type={type} current={editingExisting ? editing.cell.equipment : null} initialChoices={editingExisting ? editing.cell.choices : null} resetChoicesOnEquipmentChange={!editingExisting} allowCopy={!editingExisting} pending={pending} run={run} close={close} lastEquipment={lastEquipment}
    submitLabel={mode === 'new' ? '确认更换装备' : editingExisting ? '保存记录' : '保存装备'}
    description={mode === 'new' ? '录入新装备；旧装备不回库，附魔和五彩石不继承。' : editing.cell.equipment ? [editing.cell.label, `${editing.cell.equipment.actual_grade}品`, ...editing.cell.reasons].join(' · ') : undefined}
    onSave={async (equipment, choices) => { await api(path, 'PUT', { expected_version: editing.cell.version, equipment, choices }); saved({ ...equipment, id: '', actual_grade: equipment.actual_grade! }); }}
    actions={editingExisting && <button type="button" className="danger" onClick={() => { if (window.confirm('清除此格当前装备？旧装备不进入仓库。')) void run(() => api(`${path}?expected_version=${editing.cell.version}`, 'DELETE'), '已清除穿戴', close); }}>清除穿戴</button>}/>;
  return <>
    {(stock.length > 0 || editing.cell.equipment) && <div className="editor-modes"><button type="button" disabled={pending} className={mode === 'record' ? 'selected' : ''} onClick={() => setMode('record')}>{editing.cell.equipment ? '编辑当前记录' : '手动录入'}</button>{editing.cell.equipment && <button type="button" disabled={pending} className={mode === 'new' ? 'selected' : ''} onClick={() => setMode('new')}>手动换装</button>}{stock.length > 0 && <button type="button" disabled={pending} className={mode === 'warehouse' ? 'selected' : ''} onClick={() => setMode('warehouse')}>从仓库穿戴（{stock.length} 种）</button>}</div>}
    {mode !== 'warehouse' ? form : <>
      {stock.length > 1 && <div className="warehouse-picker" role="radiogroup" aria-label="选择仓库装备">{stock.map(item => <label className={`warehouse-option ${item.id === selectedId ? 'selected' : ''}`} key={item.id}><input type="radio" name="warehouse-item" checked={selectedId === item.id} disabled={pending} onChange={() => { setSelectedId(item.id); setRequestId(newRequestId()); }}/><span><strong>{item.label} · {item.equipment.actual_grade} 品 · {item.quantity} 件</strong><small>{choiceSummary(item.choices, type === 'weapon')}</small></span></label>)}</div>}
      {selected && <WarehouseEquip key={selected.id} data={data} item={selected} requestId={requestId} initialCharacter={editing.character.id} initialSlot={editing.slot} fixedTarget targetInTitle run={run} pending={pending} close={close}/>}
    </>}
  </>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
