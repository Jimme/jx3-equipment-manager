import { useState } from 'react';
import { api } from './api';
import { ChoicesFields, draftChoiceSummary, draftChoices, serializeChoices } from './ChoicesFields';
import { compareEquipment } from './equipmentUtils';
import type { Data, InventoryItem, Run } from './types';

type Props = { data: Data; item: InventoryItem; requestId: string; run: Run; pending: boolean; close: () => void; initialCharacter?: string; initialSlot?: string; fixedTarget?: boolean; targetInTitle?: boolean };

export function WarehouseEquip({ data, item, requestId, initialCharacter, initialSlot, fixedTarget = false, targetInTitle = false, run, pending, close }: Props) {
  const characters = data.matrix!.characters;
  const [characterId, setCharacterId] = useState(initialCharacter ?? (characters.length === 1 ? characters[0].id : ''));
  const [slot, setSlot] = useState(initialSlot ?? (item.equipment.equipment_type === 'ring' ? 'ring1' : item.equipment.equipment_type));
  const [choices, setChoices] = useState(() => draftChoices(item.choices));
  const cell = data.matrix!.cells[characterId]?.[slot];
  const character = characters.find(c => c.id === characterId);
  const slotName = data.workspace.slots.find(s => s.key === slot)?.name;
  const downgrade = cell?.equipment && compareEquipment(item.equipment, cell.equipment) < 0;
  return <form onSubmit={e => { e.preventDefault(); if (!cell) return; void run(() => api(`/accounts/${data.accountId}/warehouse/${item.id}/equip`, 'POST', {
    request_id: requestId, expected_version: item.version, expected_equipment_version: cell.version, character_id: characterId, slot, choices: serializeChoices(choices),
  }), '已穿戴', close); }}><fieldset disabled={pending}>
    <p className="item-heading">{item.label} · {item.equipment.actual_grade} 品 <small>可用 {item.quantity} 件</small></p>
    {!targetInTitle && <div className="form-grid">
      {!fixedTarget && characters.length > 1 ? <label>给哪个角色<select aria-label="穿戴角色" required value={characterId} onChange={e => setCharacterId(e.target.value)}><option value="">请选择角色</option>{characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label> : <p className="fixed-field"><span>角色</span><strong>{character?.name}</strong></p>}
      {item.equipment.equipment_type === 'ring' && !fixedTarget ? <label>穿戴部位<select aria-label="穿戴部位" value={slot} onChange={e => setSlot(e.target.value)}>{data.workspace.slots.filter(s => s.key.startsWith('ring')).map(s => <option key={s.key} value={s.key}>{s.name}</option>)}</select></label> : <p className="fixed-field"><span>部位</span><strong>{slotName}</strong></p>}
    </div>}
    {cell && <p className={downgrade ? 'replacement-note downgrade-note' : 'replacement-note'}>
      当前：{cell.label}{cell.equipment && ` · ${cell.equipment.actual_grade} 品`} → {item.label} · {item.equipment.actual_grade} 品。
      {downgrade && <strong> 新装备比当前装备差。</strong>}确认后库存扣 1 件，旧装备不回库。
    </p>}
    <details className="optional-fields"><summary>附魔{item.equipment.equipment_type === 'weapon' && '与五彩石'}</summary><ChoicesFields value={choices} onChange={setChoices} weapon={item.equipment.equipment_type === 'weapon'} cycleId={item.equipment.cycle_origin_id} equipmentType={item.equipment.equipment_type}/><p className="hint">修改只影响取出的 1 件。</p></details>
    <p className="hint" aria-live="polite" data-testid="equip-choice-summary">{draftChoiceSummary(choices, item.equipment.equipment_type === 'weapon')}</p>
    <div className="dialog-actions"><button type="button" onClick={close}>取消</button><button className="primary" disabled={!cell}>{downgrade ? '确认换成较低配置' : '确认穿戴'}</button></div>
  </fieldset></form>;
}
