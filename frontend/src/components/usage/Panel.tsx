import type { ReactNode } from 'react';
import b from '@/components/ui/Busy.module.scss';
import s from './Panel.module.scss';

/** `busy` dims content still showing the previous query's data. */
export function Panel({ title, busy = false, children }: { title?: ReactNode; busy?: boolean; children: ReactNode }) {
  return (
    <section className={busy ? `${s.panel} ${b.busy}` : s.panel} aria-busy={busy}>
      {title && <h2 className={s.title}>{title}</h2>}
      {children}
    </section>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: readonly { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className={s.tabs} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === value}
          className={t.value === value ? s.tabActive : undefined}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
