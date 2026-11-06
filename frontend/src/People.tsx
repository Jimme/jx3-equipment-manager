import { useEffect, useRef, useState } from 'react';
import { api, optionalInteger } from './api';
import type { Account, Character, Data, Run } from './types';

export function People({ data, run, selectAccount, onDirty, addCharacter }: { data: Data; run: Run; selectAccount: (id: string) => Promise<void>; onDirty: (dirty: boolean) => void; addCharacter: boolean }) {
  const [formOpen, setFormOpen] = useState(addCharacter || data.workspace.accounts.length === 0);
  const [kind, setKind] = useState<'account' | 'character'>(addCharacter ? 'character' : 'account');
  const [entity, setEntity] = useState<Account | Character | null>(null);
  const [name, setName] = useState('');
  const nextOrder = (items: (Account | Character)[]) => String(Math.min(100000, Math.max(-1, ...items.map(item => item.sort_order)) + 1));
  const [sort, setSort] = useState(() => addCharacter ? nextOrder(data.characters) : '0');
  const [initial, setInitial] = useState({ name: '', sort });
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!formOpen) return;
    const frame = requestAnimationFrame(() => nameInput.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [formOpen, kind, entity]);
  const dirty = formOpen && (name !== initial.name || sort !== initial.sort);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const discard = () => !dirty || window.confirm('有未保存的名称或排序，继续会丢弃。确定继续？');
  function edit(nextKind: 'account' | 'character', value: Account | Character | null) {
    if (!discard()) return;
    const items = nextKind === 'account' ? data.workspace.accounts : data.characters;
    const next = { name: value?.name ?? '', sort: value ? String(value.sort_order) : nextOrder(items) };
    setKind(nextKind); setEntity(value); setName(next.name); setSort(next.sort); setInitial(next); setFormOpen(true);
  }
  async function switchAccount(id: string) {
    if (id === data.accountId || !discard()) return;
    if (await run(() => selectAccount(id), '已切换账号', undefined, false)) setFormOpen(false);
  }
  function archive(value: Account | Character, target: 'account' | 'character') {
    if (!discard()) return;
    if (!value.archived && !window.confirm(target === 'account' ? `归档「${value.name}」？角色、装备和仓库会保留，可恢复。` : `移除角色「${value.name}」？将从装备表隐藏，装备和养成记录会保留，可在“已移除角色”中恢复。`)) return;
    const path = target === 'account' ? `/accounts/${value.id}` : `/accounts/${data.accountId}/characters/${value.id}`;
    void run(async () => { await api(path, 'PUT', { name: value.name, sort_order: value.sort_order, archived: !value.archived }); setFormOpen(false); }, value.archived ? '已恢复' : target === 'account' ? '已归档账号' : '已移除角色，记录已保留');
  }
  const accounts = data.workspace.accounts.filter(a => !a.archived);
  const archivedAccounts = data.workspace.accounts.filter(a => a.archived);
  const characters = data.characters.filter(c => !c.archived);
  const removed = data.characters.filter(c => c.archived);
  const accountRow = (a: Account) => <div className="entity-row" key={a.id}><span>{a.name}{a.id === data.accountId && <small>当前账号</small>}</span><button onClick={() => edit('account', a)}>编辑</button><button onClick={() => archive(a, 'account')}>{a.archived ? '恢复' : '归档'}</button></div>;
  const characterRow = (c: Character) => <div className="entity-row" key={c.id}><span>{c.name}</span><button onClick={() => edit('character', c)}>编辑</button><button className={c.archived ? undefined : 'danger'} onClick={() => archive(c, 'character')}>{c.archived ? '恢复角色' : '移除角色'}</button></div>;
  return <>
    <div className="people-heading"><h3>游戏账号</h3><button onClick={() => edit('account', null)}>＋ 添加账号</button></div>
    <div className="entity-list">{accounts.map(accountRow)}</div>
    {archivedAccounts.length > 0 && <details className="menu-disclosure"><summary>已归档账号（{archivedAccounts.length}）</summary><div className="entity-list">{archivedAccounts.map(accountRow)}</div></details>}
    <div className="people-heading">
      <h3>当前账号角色{data.matrix && ` · ${data.matrix.account.name}`}</h3>
      {accounts.length > 1 && <label>切换<select aria-label="管理账号" value={data.accountId} onChange={e => void switchAccount(e.target.value)}>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>}
      <button disabled={!data.accountId} onClick={() => edit('character', null)}>＋ 添加角色</button>
    </div>
    {characters.length === 0 && data.accountId && <p className="small-empty">这个账号还没有角色</p>}
    <div className="entity-list">{characters.map(characterRow)}</div>
    {removed.length > 0 && <details className="menu-disclosure"><summary>已移除角色（{removed.length}）</summary><p className="hint">装备和养成记录已保留，恢复后重新显示在装备表。</p><div className="entity-list">{removed.map(characterRow)}</div></details>}
    {formOpen && <form className="subform" onSubmit={e => { e.preventDefault(); void run(async () => {
      const path = kind === 'account' ? '/accounts' : `/accounts/${data.accountId}/characters`;
      const saved = await api<Account | Character>(entity ? `${path}/${entity.id}` : path, entity ? 'PUT' : 'POST', { name, sort_order: optionalInteger(sort) ?? 0, archived: entity?.archived ?? false });
      setFormOpen(false);
      if (kind === 'account' && !entity) await selectAccount(saved.id);
    }); }}>
      <h3>{entity ? '编辑' : '添加'}{kind === 'account' ? '账号' : `角色 · ${data.matrix?.account.name ?? ''}`}</h3>
      <label>名称<input ref={nameInput} aria-label="管理名称" required maxLength={100} value={name} onChange={e => setName(e.target.value)}/></label>
      <details className="optional-fields menu-disclosure"><summary>调整显示顺序</summary><label>排序数字（小的排在前面）<input aria-label="排序" type="number" required min="0" max="100000" value={sort} onChange={e => setSort(e.target.value)}/></label></details>
      <div className="dialog-actions"><button type="button" onClick={() => { if (discard()) setFormOpen(false); }}>取消</button><button className="primary" disabled={kind === 'character' && !data.accountId}>保存{kind === 'account' ? '账号' : '角色'}</button></div>
    </form>}
  </>;
}
