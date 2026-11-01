import { expect, test } from 'bun:test';
import { shareSegments } from './share';

test('slotted entities sorted by value, slot-less folded into Other last', () => {
  const segs = shareSegments([
    { key: 'b', value: 20, slot: 1 },
    { key: 'x', value: 5, slot: null },
    { key: 'a', value: 60, slot: 0 },
    { key: 'y', value: 15, slot: null },
  ]);
  expect(segs).toEqual([
    { kind: 'entity', key: 'a', slot: 0, value: 60, share: 0.6 },
    { kind: 'entity', key: 'b', slot: 1, value: 20, share: 0.2 },
    { kind: 'other', keys: ['x', 'y'], label: 'Other (2)', value: 20, share: 0.2 },
  ]);
});
