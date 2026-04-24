export type SortOrder = 'asc' | 'desc';

export interface SortState<K extends string> {
  key: K;
  order: SortOrder;
}

export function sortRows<T, K extends keyof T & string>(
  rows: T[],
  state: SortState<K>,
): T[] {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const av = a[state.key];
    const bv = b[state.key];
    if (av === bv) return 0;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return av < bv ? -1 : 1;
  });
  if (state.order === 'desc') sorted.reverse();
  return sorted;
}
