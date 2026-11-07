import { requestId as newRequestId } from './requestId';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError, optionalInteger } from './api';
import type { Cell, Character, DailyData, Data, InventoryItem, Matrix, RaidDifficulty, Run, Source, Template, Workspace } from './types';
import { Modal } from './Modal';
import { EquipmentForm } from './EquipmentForm';
import { BatchEquipmentEditor } from './BatchEquipmentEditor';
import { InventoryWorkspace, type InventoryDialog, type InventorySelection } from './InventoryWorkspace';
import { WarehouseEquip } from './WarehouseEquip';
import { choiceSummary } from './ChoicesFields';
import { compareEquipment } from './equipmentUtils';
import { DataManager } from './DataManager';
import { GrowthEditor } from './GrowthEditor';
import { CycleSettings } from './CycleSettings';
import { People } from './People';
import './style.css';

type Editing = { character: Character; slot: string; slotName: string; cell: Cell };
type Panel = 'people' | 'add-character' | 'sources' | 'data' | null;
const raidChecks = [['10', '10人'], ['25pt', '25pt'], ['25yx', '25yx']] as const;
const stockColumns = [['drops', '团本掉落', '记录掉落'], ['warehouse', '共享仓库', '添加库存']] as const;

function indexStock(items: InventoryItem[] = []) {
  const byType = new Map<string, InventoryItem[]>();
  for (const item of items) {
    const type = item.equipment.equipment_type;
    const group = byType.get(type);
    if (group) group.push(item);
    else byType.set(type, [item]);
  }
  for (const group of byType.values()) {
    group.sort((a, b) => compareEquipment(b.equipment, a.equipment) || a.id.localeCompare(b.id));
  }
  return byType;
}

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
  const [equipmentDirty, setEquipmentDirty] = useState(false);
  const [batchCharacter, setBatchCharacter] = useState<Character | null>(null);
  const [batchDirty, setBatchDirty] = useState(false);
  const [targetDraft, setTargetDraft] = useState<{ accountId: string; value: string } | null>(null);
  const [lastEquipment, setLastEquipment] = useState<Template | null>(null);
  const [gapsOnly, setGapsOnly] = useState(false);
  const [inventorySelection, setInventorySelection] = useState<InventorySelection | null>(null);
  const [inventoryDialog, setInventoryDialog] = useState<InventoryDialog | null>(null);
  const warehouse = useMemo(() => indexStock(data?.daily?.warehouse), [data?.daily?.warehouse]);
  const drops = useMemo(() => indexStock(data?.daily?.drops), [data?.daily?.drops]);
  const grades = useMemo(() => [...new Set([
    ...(data?.sources.map(s => s.default_grade) ?? []),
    ...(data?.templates.map(t => t.actual_grade) ?? []),
    ...(data?.daily?.warehouse.map(item => item.equipment.actual_grade) ?? []),
    ...(data?.daily?.drops.map(item => item.equipment.actual_grade) ?? []),
    ...Object.values(data?.matrix?.cells ?? {}).flatMap(cells => Object.values(cells).flatMap(cell => cell.equipment ? [cell.equipment.actual_grade] : [])),
  ].filter((v): v is number => v !== null))].sort((a, b) => a - b), [data?.sources, data?.templates, data?.daily, data?.matrix]);
  const loadVersion = useRef(0);
  const pendingRef = useRef(false);
  const selectedAccount = useRef('');

  async function load(accountId?: string) {
    const version = ++loadVersion.current;
    const workspace = await api<Workspace>('/workspace');
    const preferredAccount = accountId || selectedAccount.current || rememberedAccount();
    const activeAccount = workspace.accounts.find(a => a.id === preferredAccount && !a.archived) ?? workspace.accounts.find(a => !a.archived);
    const selectedCycle = workspace.active_cycle_id ?? '';
    const [matrix, sources, daily] = await Promise.all([
      activeAccount ? api<Matrix & { all_characters: Character[] }>(`/accounts/${activeAccount.id}/matrix?include_archived=true`) : Promise.resolve(null),
      selectedCycle ? api<Source[]>(`/cycles/${selectedCycle}/sources`) : Promise.resolve([]),
      activeAccount ? api<DailyData>(`/accounts/${activeAccount.id}/daily?skip_unconfigured_gaps=true`) : Promise.resolve(null),
    ]);
    if (version !== loadVersion.current) return;
    const nextAccount = activeAccount?.id ?? '';
    if (selectedAccount.current !== nextAccount) {
      setInventorySelection(null);
      setInventoryDialog(null);
    }
    selectedAccount.current = nextAccount;
    try { window.localStorage.setItem('jx3-selected-account', selectedAccount.current); } catch { /* Storage-disabled browsers still work without remembered selection. */ }
    setData({ workspace, matrix, characters: matrix?.all_characters ?? [], sources, templates: [], daily, accountId: activeAccount?.id ?? '', cycleId: selectedCycle });
    setTargetDraft(draft => draft?.accountId === activeAccount?.id ? draft : null);
  }

  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!cycleDirty && !growthDirty && !peopleDirty && !equipmentDirty && !batchDirty) return;
    const protectDraft = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', protectDraft);
    return () => window.removeEventListener('beforeunload', protectDraft);
  }, [cycleDirty,growthDirty,peopleDirty,equipmentDirty,batchDirty]);

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
      if (e instanceof ApiError && e.refreshRequired) {
        try { await load(selectedAccount.current); } catch { /* The saved operation remains recoverable by an explicit refresh. */ }
      }
      const message = e instanceof Error ? e.message : '操作失败，请重试';
      if (completed && reload) {
        pendingRef.current = false;
        close?.();
      }
      setError(completed && reload ? `操作已提交，但页面刷新失败，请刷新核对：${message}` : message);
      return false;
    } finally { pendingRef.current = false; setPending(false); }
  };

  const refresh = () => run(() => load(data?.accountId), '已刷新', undefined, false);
  const peoplePanel = panel === 'people' || panel === 'add-character';
  const closePanel = () => { if (pendingRef.current) return; if (peoplePanel && peopleDirty && !window.confirm('账号或角色名称还没保存，关闭会丢弃。确定关闭？')) return; if (panel === 'sources' && cycleDirty && !window.confirm('有未保存的输入，关闭会丢弃。确定关闭？')) return; setPanel(null); setError(''); setCycleDirty(false); setPeopleDirty(false); };
  const closeGrowth = () => { if (pendingRef.current) return; if (growthDirty && !window.confirm('养成输入还没保存，关闭会丢弃。确定关闭？')) return; setGrowthCharacter(null); setGrowthDirty(false); setError(''); };
  const dismissEquipment = () => { setEditing(null); setEquipmentDirty(false); setError(''); };
  const dismissBatch = () => { setBatchCharacter(null); setBatchDirty(false); setError(''); };
  const closeBatch = () => {
    if (pendingRef.current) return;
    if (batchDirty && !window.confirm('批量录入尚未保存，关闭会丢弃全部草稿。确定关闭？')) return;
    dismissBatch();
  };
  const closeEquipment = () => {
    if (pendingRef.current) return;
    if (equipmentDirty && !window.confirm('装备输入还没保存，关闭会丢弃。确定关闭？')) return;
    dismissEquipment();
  };

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
  const target = targetDraft?.accountId === accountId ? targetDraft.value : account?.target_grade?.toString() ?? '';
  const stock = { warehouse, drops };
  const stockWidths = {
    warehouse: Math.max(1, ...[...warehouse.values()].map(items => items.length + 1)),
    drops: Math.max(1, ...[...drops.values()].map(items => items.length + 1)),
  };
  const comparisonHelp = account?.target_grade == null ? '未设目标，暂不比较' : account.comparison_mode === 'grade' ? '仅比较真实品级' : '高于目标即达标；同品级需绿字达标，适用时还需体质孔';
  function openInventory(selection: InventorySelection) {
    setInventorySelection(selection);
    document.getElementById('inventory')?.scrollIntoView({ block: 'nearest' });
  }

  return <main>
    <header className="masthead">
      <div className="app-heading"><h1>坦克装备簿</h1>{notice && !panel && <span role="status" className="app-notice">{notice}</span>}</div>
      <div className="header-actions"><button onClick={refresh} disabled={pending}>刷新</button><button onClick={()=>{setError('');setPanel('data');}}>数据与备份</button><button onClick={() => setPanel('sources')}>周期与来源</button><button className="primary" onClick={() => setPanel('people')}>账号与角色</button></div>
    </header>
    {error && !panel && !editing && <div role="alert" className="message error">{error}</div>}
    <section className="toolbar" aria-label="装备比较设置">
      <label>账号<select aria-label="游戏账号" value={accountId} disabled={pending} onChange={e => { const id = e.target.value; void run(() => load(id), '', undefined, false); }}>
        {!accountId && <option value="">请添加账号</option>}{workspace.accounts.filter(a => !a.archived).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select></label>
      <label>周期<select aria-label="装备周期" value={cycleId} disabled={pending} onChange={e => { const id = e.target.value; void run(() => api('/workspace/active-cycle', 'PUT', { cycle_id: id }), '已切换周期'); }}>
        {!cycleId && <option value="">请创建周期</option>}{workspace.cycles.filter(c => !c.archived).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select></label>
      <form className="target-form" onSubmit={e => { e.preventDefault(); if (account) void run(async () => {
        await api(`/accounts/${accountId}/preferences`, 'PATCH', { target_grade: optionalInteger(target) });
        setTargetDraft(null);
      }, '已更新目标'); }}>
        <label>目标品级<input aria-label="目标真实品级" type="number" min="1" max="2147483647" step="1" list="configured-grades" placeholder="未设置" value={target} onChange={e => setTargetDraft({ accountId, value: e.target.value })} disabled={!account || pending}/></label>
        <datalist id="configured-grades">{grades.map(g => <option key={g} value={g}/>)}</datalist><button disabled={!account || pending || target === (account.target_grade?.toString() ?? '')}>应用</button>
      </form>
      <label>比较模式<select aria-label="比较模式" value={account?.comparison_mode ?? 'strict'} disabled={!account || pending} onChange={e => { const mode = e.target.value; void run(() => api(`/accounts/${accountId}/preferences`, 'PATCH', { comparison_mode: mode }), ''); }}><option value="strict">严格达标</option><option value="grade">仅品级</option><option value="combined">综合达标</option></select></label>
    </section>
    {!account ? <section className="empty"><h2>从第一个游戏账号开始</h2><p>添加账号和角色，即可建立你的 12 部位装备表。体质数据可以以后再补。</p><button className="primary" onClick={() => setPanel('people')}>添加账号与角色</button></section> : <section className="matrix-section">
      <div className="section-heading"><h2>{account.name}<span>装备矩阵</span></h2>
        <div className="legend">{account.target_grade === null ? <span>{comparisonHelp}</span> : <>{account.comparison_mode === 'combined' ? <><span><i className="dot complete-dot"/>完全达标</span><span><i className="dot gap-dot"/>仅品级达标</span><span><i className="dot insufficient-dot"/>品级不足／空缺</span></> : <><span><i className="dot gap-dot"/>未达标／空缺</span><span><i className="dot met-dot"/>已达标</span></>}<details className="matrix-help"><summary>规则</summary><p>{comparisonHelp}。适用体质孔但缺孔时加「-」，绿字达标加「+」。</p></details></>}</div>
        <label className="check"><input type="checkbox" checked={gapsOnly} onChange={e => setGapsOnly(e.target.checked)}/>弱化达标</label>
      </div>
      {matrix!.characters.length === 0 && <div className="matrix-empty-roles"><span>这个账号还没有角色</span><button disabled={pending} onClick={() => setPanel('add-character')}>添加角色</button></div>}
      {!cycleId && <p className="matrix-empty-roles hint">先在「周期与来源」创建周期，即可添加库存或记录掉落。</p>}
      <div className="matrix-scroll" tabIndex={0} aria-label="可横向滚动的装备矩阵"><table className={`matrix mode-${account.comparison_mode} ${gapsOnly ? 'focus-gaps' : ''}`}>
        <thead><tr><th scope="col">装备部位</th>{matrix!.characters.map(c => <th scope="col" key={c.id}>
          <div className="character-heading">
            <button className="growth-entry" title="养成与体质" disabled={pending} onClick={() => { setError(''); setGrowthCharacter(c); }} aria-label={`${c.name} 养成与体质`}>{c.name}</button>
            <button className="batch-entry" disabled={pending || !cycleId} onClick={() => { setError(''); setBatchCharacter(c); }} aria-label={`${c.name} 批量录入装备`}>批量录入</button>
            <fieldset className="raid-checks" disabled={pending} aria-label={`${c.name} 副本完成记录`}>
              {raidChecks.map(([difficulty, label]) => <label className="check" key={difficulty}>
                <input type="checkbox" checked={c[`raid_${difficulty}`]} onChange={event => setRaidCheck(c, difficulty, event.target.checked)}/>{label}
              </label>)}
            </fieldset>
          </div>
          <small>{matrix!.growth[c.id]?.equipment_total == null ? '体质待完善' : `装备体质 ${matrix!.growth[c.id].equipment_total}`}</small>
        </th>)}{stockColumns.map(([key, label, addLabel]) => <th className="stock-column" scope="colgroup" colSpan={stockWidths[key]} key={key}>
          <button className="stock-heading-button" disabled={pending} aria-controls="daily-panel" onClick={() => openInventory({ tab: key })}>{label}</button>
          <button className="stock-add" disabled={pending || !cycleId} onClick={() => { setError(''); setInventoryDialog({ kind: 'add', target: key, requestId: newRequestId() }); }}>{addLabel}</button>
        </th>)}</tr></thead>
        <tbody>{workspace.slots.map(slot => <tr key={slot.key}><th scope="row">{slot.name}</th>{matrix!.characters.map(c => {
          const cell = matrix!.cells[c.id][slot.key];
          const equipment = cell.equipment;
          const status = cell.status === 'met' ? (account.comparison_mode === 'combined' ? '完全达标' : '已达标') : cell.status === 'grade_met' ? '仅品级达标' : cell.status === 'unset' ? '目标待设置' : equipment ? (account.comparison_mode === 'combined' ? '品级不足' : '待提升') : '空缺';
          const detail = [equipment && `${equipment.source_name} · ${equipment.actual_grade} 品`, status, ...cell.reasons, equipment?.note].filter(Boolean).join('；');
          return <td key={c.id} className={`cell-${cell.status}`}><button className="equipment-cell" disabled={pending} aria-label={`${c.name} ${slot.name}`} aria-description={detail} title={detail} onClick={() => setEditing({ character: c, slot: slot.key, slotName: slot.name, cell })}>
            <strong>{equipment ? cell.label : '＋'}</strong>
            {equipment && <span className="cell-grade">{equipment.actual_grade}</span>}
            {cell.status === 'grade_met' && <span aria-hidden="true">△</span>}
            {cell.status === 'gap' && <span aria-hidden="true">!</span>}
          </button></td>;
        })}{stockColumns.flatMap(([key, label, addLabel]) => {
          if (slot.key === 'ring2') return [];
          const equipmentType = slot.key === 'ring1' ? 'ring' : slot.key;
          const items = stock[key].get(equipmentType) ?? [];
          return Array.from({ length: stockWidths[key] }, (_, index) => {
            const item = items[index];
            return <td className="stock-cell" key={`${key}-${index}`} rowSpan={slot.key === 'ring1' ? 2 : undefined}>{item ?
            <button className="stock-entry" disabled={pending} aria-label={`编辑${slot.name}${label}配置${index + 1}`} onClick={() => {
              setError(''); setInventoryDialog({ kind: 'edit', target: key, item, requestId: newRequestId() });
            }}>
              <span className="stock-main">{item.label} · {item.equipment.actual_grade} · ×{item.quantity}</span>
              <small className="stock-details">{choiceSummary(item.choices, equipmentType === 'weapon')}</small>
              {slot.key === 'ring1' && <small>两格通用</small>}
            </button> :
            index === items.length ? <button className="stock-entry" aria-label={`${slot.name}：${addLabel}`} disabled={pending || !cycleId} onClick={() => { setError(''); setInventoryDialog({ kind: 'add', target: key, equipmentType, requestId: newRequestId() }); }}>＋{slot.key === 'ring1' && <small>两格通用</small>}</button> : null}
          </td>;
          });
        })}</tr>)}</tbody>
      </table></div>
    </section>}
    {account && <InventoryWorkspace key={accountId} data={data} run={run} pending={pending} error={error} selection={inventorySelection} setSelection={setInventorySelection} dialog={inventoryDialog} setDialog={setInventoryDialog}/> }
    <footer>数据保存在应用的 data 目录 · 无需游戏连接<span>v0.1.10</span></footer>
    {panel && <Modal title={peoplePanel ? '账号与角色' : panel === 'data' ? '数据与备份' : '周期与来源'} close={closePanel} wide={panel === 'sources'} busy={pending}>
      {error && <p role="alert" className="message error">{error}</p>}
      <fieldset disabled={pending}>{peoplePanel ? <People data={data} run={run} selectAccount={load} onDirty={setPeopleDirty} addCharacter={panel === 'add-character'}/> : panel === 'data' ? <DataManager run={run} pending={pending} close={closePanel} replaced={()=>setLastEquipment(null)}/> : <CycleSettings data={data} run={run} pending={pending} onDirty={setCycleDirty}/>}</fieldset>
      {pending ? <p role="status">正在处理…</p> : notice && <p role="status" className="message success">{notice}</p>}
    </Modal>}
    {batchCharacter && <Modal title={`${batchCharacter.name} · 批量录入装备`} close={closeBatch} wide busy={pending}>
      {error && <p role="alert" className="message error">{error}</p>}
      <BatchEquipmentEditor data={data} character={batchCharacter} run={run} pending={pending} close={closeBatch} savedClose={dismissBatch} onDirty={setBatchDirty}/>
    </Modal>}
    {growthCharacter && <Modal title={`${growthCharacter.name} · 养成与体质`} close={closeGrowth} wide busy={pending}>
      {error && <p role="alert" className="message error">{error}</p>}
      <GrowthEditor accountId={accountId} characterId={growthCharacter.id} run={run} pending={pending} onDirty={setGrowthDirty}/>
    </Modal>}
    {editing && <Modal title={`${editing.character.name} · ${editing.slotName}`} close={closeEquipment} busy={pending} compact>
      {error && <p role="alert" className="message error">{error}</p>}
      <EquipmentEditor data={data} editing={editing} pending={pending} run={run} close={closeEquipment} savedClose={dismissEquipment} onDirty={setEquipmentDirty} lastEquipment={lastEquipment} saved={setLastEquipment}/>
    </Modal>}
  </main>;
}

function EquipmentEditor({ data, editing, pending, run, close, savedClose, onDirty, lastEquipment, saved }: { data: Data; editing: Editing; pending: boolean; run: Run; close: () => void; savedClose: () => void; onDirty: (dirty: boolean) => void; lastEquipment: Template | null; saved: (t: Template) => void }) {
  const type = editing.slot.startsWith('ring') ? 'ring' : editing.slot;
  const stock = data.daily!.warehouse.filter(item => item.equipment.equipment_type === type);
  const [mode, setMode] = useState<'record' | 'new' | 'warehouse'>(() => !editing.cell.equipment && stock.length > 0 ? 'warehouse' : 'record');
  const [selectedId, setSelectedId] = useState(stock[0]?.id ?? '');
  const [requestId, setRequestId] = useState(() => newRequestId());
  const dirty = useRef(false);
  const setDirty = React.useCallback((value: boolean) => { dirty.current = value; onDirty(value); }, [onDirty]);
  function changeMode(next: typeof mode) {
    if (next === mode) return;
    if (dirty.current && !window.confirm('切换录入方式会丢弃当前输入，确定继续？')) return;
    setDirty(false); setMode(next);
  }
  const path = `/accounts/${data.accountId}/characters/${editing.character.id}/equipment/${editing.slot}`;
  const editingExisting = mode === 'record' && !!editing.cell.equipment;
  const selected = stock.find(item => item.id === selectedId);
  const form = <EquipmentForm key={mode} data={data} type={type} current={editingExisting ? editing.cell.equipment : null} initialChoices={editingExisting ? editing.cell.choices : null} allowCopy={!editingExisting} pending={pending} run={run} close={close} savedClose={savedClose} onDirty={setDirty} lastEquipment={lastEquipment}
    submitLabel={mode === 'new' ? '确认更换装备' : editingExisting ? '保存记录' : '保存装备'}
    description={mode === 'new' ? '录入新装备；旧装备不回库，附魔和五彩石不继承。' : editing.cell.equipment ? [editing.cell.label, `${editing.cell.equipment.actual_grade}品`, ...editing.cell.reasons].join(' · ') : undefined}
    onSave={async (equipment, choices) => { await api(path, 'PUT', { expected_version: editing.cell.version, equipment, choices }); saved({ ...equipment, id: '', actual_grade: equipment.actual_grade! }); }}
    actions={editingExisting && <button type="button" className="danger" onClick={() => { if (window.confirm('清除此格当前装备？旧装备不进入仓库。')) void run(() => api(`${path}?expected_version=${editing.cell.version}`, 'DELETE'), '已清除穿戴', savedClose); }}>清除穿戴</button>}/>;
  return <>
    {(stock.length > 0 || editing.cell.equipment) && <div className="menu-tabs"><button type="button" disabled={pending} aria-pressed={mode === 'record'} onClick={() => changeMode('record')}>{editing.cell.equipment ? '编辑当前记录' : '手动录入'}</button>{editing.cell.equipment && <button type="button" disabled={pending} aria-pressed={mode === 'new'} onClick={() => changeMode('new')}>手动换装</button>}{stock.length > 0 && <button type="button" disabled={pending} aria-pressed={mode === 'warehouse'} onClick={() => changeMode('warehouse')}>从仓库穿戴（{stock.length} 种）</button>}</div>}
    {mode !== 'warehouse' ? form : <>
      {stock.length > 1 && <div className="warehouse-picker" role="radiogroup" aria-label="选择仓库装备">{stock.map(item => <label className={`warehouse-option ${item.id === selectedId ? 'selected' : ''}`} key={item.id}><input type="radio" name="warehouse-item" checked={selectedId === item.id} disabled={pending} onChange={() => { if (dirty.current && !window.confirm('更换仓库装备会丢弃本件附加配置输入，确定继续？')) return; setSelectedId(item.id); setRequestId(newRequestId()); }}/><span><strong>{item.label} · {item.equipment.actual_grade} 品 · {item.quantity} 件</strong><small>{choiceSummary(item.choices, type === 'weapon')}</small></span></label>)}</div>}
      {selected && <WarehouseEquip key={selected.id} data={data} item={selected} requestId={requestId} initialCharacter={editing.character.id} initialSlot={editing.slot} fixedTarget targetInTitle run={run} pending={pending} close={close} savedClose={savedClose} onDirty={setDirty}/>}
    </>}
  </>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
