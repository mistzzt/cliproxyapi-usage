// 100% share bar segments: slotted entities get their own segment, the rest fold into "Other" (pure).

export interface ShareInput {
  key: string;
  value: number;
  /** Color slot from the options endpoint; null folds into Other. */
  slot: number | null;
}

export type ShareSegment =
  | { kind: 'entity'; key: string; slot: number; value: number; share: number }
  | { kind: 'other'; keys: string[]; label: string; value: number; share: number };

/** Entity segments by descending value, then Other; zero-valued entries are dropped. */
export function shareSegments(items: readonly ShareInput[]): ShareSegment[] {
  const positive = items.filter((it) => it.value > 0);
  const total = positive.reduce((acc, it) => acc + it.value, 0);
  const share = (v: number) => (total > 0 ? v / total : 0);
  const segs: ShareSegment[] = [];
  const otherKeys: string[] = [];
  let other = 0;
  for (const it of positive) {
    if (it.slot === null) {
      otherKeys.push(it.key);
      other += it.value;
    } else {
      segs.push({ kind: 'entity', key: it.key, slot: it.slot, value: it.value, share: share(it.value) });
    }
  }
  segs.sort((a, b) => b.value - a.value);
  if (otherKeys.length > 0) {
    segs.push({ kind: 'other', keys: otherKeys, label: `Other (${otherKeys.length})`, value: other, share: share(other) });
  }
  return segs;
}
