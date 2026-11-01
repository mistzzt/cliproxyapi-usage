import { type Change, fmtChange } from '@/utils/usage/format';
import s from './ChangeBadge.module.scss';

const ARROW = { up: '▲', down: '▼', flat: '•' } as const;

/** Change vs the previous period; renders nothing when there is no previous period. */
export default function ChangeBadge({ value }: { value: Change }) {
  if (value === null) return null;
  const c = fmtChange(value);
  return <span className={s.change}>{c.direction === 'new' ? c.text : `${ARROW[c.direction]} ${c.text}`}</span>;
}
