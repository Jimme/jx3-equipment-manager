import { api } from './api';
import { useState } from 'react';
import type { RaidCD, Run } from './types';

const shanghai = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));

export function RaidCDSettings({ value, pending, run }: { value: RaidCD; pending: boolean; run: Run }) {
  const [optimistic, setOptimistic] = useState<boolean | null>(null);
  return <section className="raid-cd-settings" aria-label="副本 CD 自动重置设置">
    <label className="check"><input type="checkbox" checked={optimistic ?? value.enabled} disabled={pending || optimistic !== null} onChange={e => {
      const enabled = e.target.checked;
      if (!window.confirm(enabled ? '为所有账号及角色开启自动重置？保留当前勾选，从下次上海时间重置点开始处理。' : '关闭自动重置？保留当前进度，不恢复已经清除的旧 CD。')) return;
      setOptimistic(enabled);
      void run(() => api('/raid-cd', 'PUT', { enabled, expected_version: value.version }), enabled ? '自动重置已开启，当前勾选保留' : '自动重置已关闭').finally(() => setOptimistic(null));
    }}/>副本 CD 自动重置</label>
    <small>上海时间：10人周一／周五 06:00；25pt、25yx 周一 06:00。作用于全部账号和角色。</small>
    <small>下次：10人 {shanghai(value.next_10)}；25人 {shanghai(value.next_25)}。</small>
  </section>;
}
