import type { ReactNode } from 'react';
import s from './Panel.module.scss';

export function Panel({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className={s.panel}>
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
