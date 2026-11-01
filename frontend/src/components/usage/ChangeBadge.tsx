import { fmtChange } from '@/utils/usage/format';
import s from './ChangeBadge.module.scss';

const ARROW = { up: '▲', down: '▼', flat: '•' } as const;

/** Change vs the previous period; null renders as "new". */
export default function ChangeBadge({ value }: { value: number | null }) {
  const c = fmtChange(value);
  return <span className={s.change}>{c.direction === 'new' ? c.text : `${ARROW[c.direction]} ${c.text}`}</span>;
}
