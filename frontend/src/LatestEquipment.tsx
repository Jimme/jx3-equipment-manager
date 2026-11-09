import { useState } from 'react';
import { api } from './api';
import { choiceSummary } from './ChoicesFields';
import type { Cell, Matrix, Run } from './types';

export function LatestEquipment({ accountId, characterId, slot, run, pending, accept }: {
  accountId: string; characterId: string; slot: string; run: Run; pending: boolean; accept: (cell: Cell) => void;
}) {
  const [latest, setLatest] = useState<Cell | null>(null);
  return <div className="latest-record">
    <button type="button" disabled={pending} onClick={() => void run(async () => {
      const matrix = await api<Matrix>(`/accounts/${accountId}/matrix`);
      const cell = matrix.cells[characterId]?.[slot];
      if (!cell) throw new Error('角色已移除或账号不可用，输入仍保留，请核对后退出');
      setLatest(cell);
    }, '', undefined, false)}>读取最新记录核对</button>
    {latest && <p className="replacement-note">最新记录：{latest.label} {latest.equipment?.actual_grade} 品，{latest.choices ? choiceSummary(latest.choices, slot === 'weapon') : '未装备'}。本次输入保留。
      <button type="button" disabled={pending} onClick={() => { accept(latest); setLatest(null); }}>保留输入并采用最新版本</button>
    </p>}
  </div>;
}
