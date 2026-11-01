import { expect, test } from 'bun:test';
import { buildHeatmap, heatBin } from './heatmap';

test('top columns kept, the rest fold into +N more; values bin on a sqrt scale', () => {
  const h = buildHeatmap(
    [
      { row: 'alice', col: 'm1', value: 50 },
      { row: 'bob', col: 'm2', value: 40 },
      { row: 'bob', col: 'm3', value: 10 },
      { row: 'bob', col: 'm4', value: 5 },
    ],
    { maxColumns: 2 },
  );
  expect(h.columns).toEqual([
    { kind: 'entity', key: 'm1', total: 50 },
    { kind: 'entity', key: 'm2', total: 40 },
    { kind: 'more', keys: ['m3', 'm4'], total: 15 },
  ]);
  expect(h.values).toEqual([
    [0, 40, 15],
    [50, 0, 0],
  ]);
  expect([0, 1, 25, 100].map((v) => heatBin(v, 100))).toEqual([-1, 0, 3, 5]);
});
