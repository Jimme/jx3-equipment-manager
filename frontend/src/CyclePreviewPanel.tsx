import { useState } from 'react';
import { api } from './api';
import { dimensionText, STAT_LABELS } from './cycleDataUtils';
import type { CyclePreview, Run, StatKind } from './types';

export function CyclePreviewPanel({ cycleId, preview, run, pending, cancel, applied }: { cycleId: string; preview: CyclePreview; run: Run; pending: boolean; cancel: () => void; applied: (id: string) => void }) {
  const [page, setPage] = useState(0);
  const [backup, setBackup] = useState('');
  const changes = preview.changes ?? [];
  const copying = preview.kind === 'copy';
  const noChange = !copying && changes.length === 0;
  return <section className="cycle-preview" aria-label="变更预览">
    <h3>{copying ? `复制为「${preview.name}」` : `${STAT_LABELS[preview.kind as StatKind]}变更预览`}</h3>
    {copying ? <><p className="hint">{preview.message}只复制已保存的配置。</p><p>来源 {preview.sources} 项；{Object.entries(preview.table_counts ?? {}).map(([kind,count]) => `${STAT_LABELS[kind as StatKind]} ${count} 行`).join('，')}。</p></> : <>
      <p className="hint">替换本周期的这一张表，其他表不变。空白是未录入，0 是明确为零。</p>
      <p>新增 {preview.counts?.added} 行 · 修改 {preview.counts?.changed} 行 · 删除 {preview.counts?.deleted} 行</p>
      {!!preview.counts?.deleted && <p className="replacement-note">有旧配置将被删除。对应默认值可能变为未知，请核对下表。</p>}
      {noChange ? <p className="hint">配置没有变化，无需保存。</p> : <div className="config-scroll"><table className="daily-table"><thead><tr><th>操作</th><th>配置项</th><th>原值</th><th>新值</th></tr></thead><tbody>{changes.slice(page * 50, page * 50 + 50).map((change,index) => <tr key={index}><td>{{ added:'新增', changed:'修改', deleted:'删除' }[change.action]}</td><td>{dimensionText(change.after ?? change.before)}</td><td>{!change.before ? '无此条目' : (change.before.constitution ?? change.before.threshold ?? '未录入')}</td><td>{!change.after ? '删除' : (change.after.constitution ?? change.after.threshold ?? '未录入')}</td></tr>)}</tbody></table></div>}
      {changes.length > 50 && <div className="button-row"><button disabled={pending || page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.ceil(changes.length / 50)}</span><button disabled={pending || (page + 1) * 50 >= changes.length} onClick={() => setPage(page + 1)}>下一页</button></div>}
      <p className="hint">{preview.impact?.message}</p>
      {!!((preview.impact?.worn_count ?? 0) + (preview.impact?.warehouse_count ?? 0) + (preview.impact?.drop_count ?? 0)) && <details className="optional-fields menu-disclosure"><summary>查看影响：{preview.impact?.worn_count} 个穿戴部位、{preview.impact?.warehouse_count} 项库存、{preview.impact?.drop_count} 条掉落</summary>{preview.impact?.worn.map((row,i) => <p className="hint" key={`w${i}`}>{row.character} · {row.slot} · {row.grade}品：{row.before ?? '未录入'} → {row.after ?? '未录入'}</p>)}{preview.impact?.warehouse.map((row,i) => <p className="hint" key={`s${i}`}>{row.account} · {row.slot} · {row.grade}品 × {row.quantity}：{row.before ?? '未录入'} → {row.after ?? '未录入'}</p>)}{preview.impact?.drops.map((row,i) => <p className="hint" key={`d${i}`}>掉落备忘：{row.account} · {row.slot} · {row.grade}品 × {row.quantity}：{row.before ?? '未录入'} → {row.after ?? '未录入'}</p>)}<p className="hint">明细各最多显示 30 项。</p></details>}
    </>}
    {backup && <p role="status">已保存，操作前备份：{backup}</p>}
    <div className="dialog-actions"><button disabled={pending} onClick={cancel}>{noChange ? '返回编辑' : '返回修改'}</button><button className="primary" disabled={pending || noChange} onClick={() => { void run(async () => {
      const result = await api<{ cycle_id: string; backup: string | null }>(`/cycles/${cycleId}/changes/${preview.preview_id}/apply`, 'POST');
      setBackup(result.backup ?? '');
      applied(result.cycle_id);
    }, copying ? '已复制并切换周期，旧装备未改变' : '数值表已保存，操作前已备份'); }}>{pending ? '正在保存…' : copying ? '确认复制并切换' : '确认保存并备份'}</button></div>
  </section>;
}
