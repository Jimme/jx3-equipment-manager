import { useEffect, useRef, type ReactNode } from 'react';

export function Modal({ title, close, children, wide = false, compact = false, busy = false }: { title: string; close: () => void; children: ReactNode; wide?: boolean; compact?: boolean; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className={wide ? 'wide-dialog' : compact ? 'compact-dialog' : undefined} onCancel={e => { e.preventDefault(); if (!busy) close(); }} aria-label={title} aria-busy={busy}>
    <div className="dialog-heading"><h2>{title}</h2><button type="button" className="icon-button" disabled={busy} onClick={close} aria-label="关闭">×</button></div>
    {children}
  </dialog>;
}
