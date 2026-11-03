import { expect, test } from 'bun:test';
import type { MixEntry } from '@/types/api';
import { buildHeatmap, heatBin, personModelCells } from './heatmap';

function mix(key: string, cost: number | null, input: number, output: number): MixEntry {
  return { key, label: key, cost, input_tokens: input, output_tokens: output, cached_tokens: 7 };
}

test('person mix entries become person x model cells valued by the metric', () => {
  const people = [
    { key: 'alice', mix: [mix('m1', 2.5, 100, 10), mix('m2', null, 5, 1)] },
    { key: 'bob', mix: [mix('m1', 1, 20, 2)] },
  ];
  expect(personModelCells(people, 'cost')).toEqual([
    { row: 'alice', col: 'm1', value: 2.5 },
    { row: 'alice', col: 'm2', value: 0 },
    { row: 'bob', col: 'm1', value: 1 },
  ]);
  expect(personModelCells(people, 'tokens').map((c) => c.value)).toEqual([110, 6, 22]);
});

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
