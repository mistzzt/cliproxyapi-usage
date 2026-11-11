import { expect, test } from 'bun:test';
import { shareSegments } from './share';

test('every entity gets its own segment by descending value, slot-less ones included', () => {
  const segs = shareSegments([
    { key: 'b', value: 20, slot: 1 },
    { key: 'x', value: 5, slot: null },
    { key: 'a', value: 60, slot: 0 },
    { key: 'y', value: 15, slot: null },
    { key: 'z', value: 0, slot: null },
  ]);
  expect(segs).toEqual([
    { key: 'a', value: 60, slot: 0, share: 0.6 },
    { key: 'b', value: 20, slot: 1, share: 0.2 },
    { key: 'y', value: 15, slot: null, share: 0.15 },
    { key: 'x', value: 5, slot: null, share: 0.05 },
  ]);
});