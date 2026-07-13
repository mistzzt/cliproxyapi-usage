import { describe, test, expect } from 'bun:test';
import { deriveOtherSeries, rollupCostStatus, sumSeries, zeroThreshold } from './otherSeries';

describe('sumSeries', () => {
  test('element-wise sum with padding', () => {
    expect(sumSeries([[1, 2, 3], [4, 5, 6]], 3)).toEqual([5, 7, 9]);
  });
  test('empty list yields zeros', () => {
    expect(sumSeries([], 3)).toEqual([0, 0, 0]);
  });
});

describe('deriveOtherSeries', () => {
  test('other = all - sum(named)', () => {
    const out = deriveOtherSeries([10, 20, 30], [[3, 5, 10], [2, 5, 5]], 'requests');
    expect(out).toEqual([5, 10, 15]);
  });

  test('clamps floating-point negatives to zero', () => {
    // named sum slightly exceeds all in bucket 0 due to float residue; bucket 1
    // carries real signal so the series is not omitted.
    const out = deriveOtherSeries([10, 20], [[10.0000001, 5]], 'tokens');
    expect(out).toEqual([0, 15]);
  });

  test('omits Other when zero throughout (counts)', () => {
    const out = deriveOtherSeries([5, 6], [[5, 6]], 'requests');
    expect(out).toBeNull();
  });

  test('cost: values below one tenth of a cent count as zero -> omitted', () => {
    const out = deriveOtherSeries([1.0005, 2.0009], [[1.0, 2.0]], 'cost');
    // residuals 0.0005 / 0.0009 are both < 0.001 -> omit
    expect(out).toBeNull();
  });

  test('cost: a residual at/above one tenth of a cent keeps Other', () => {
    const out = deriveOtherSeries([1.002, 2.0], [[1.0, 2.0]], 'cost');
    expect(out).not.toBeNull();
    expect(out![0]).toBeCloseTo(0.002, 6);
    expect(out![1]).toBe(0);
  });

  test('counts: a single positive bucket keeps Other', () => {
    const out = deriveOtherSeries([5, 7], [[5, 6]], 'requests');
    expect(out).toEqual([0, 1]);
  });
});

describe('zeroThreshold', () => {
  test('cost uses 0.001, counts use a tiny epsilon', () => {
    expect(zeroThreshold('cost')).toBe(0.001);
    expect(zeroThreshold('requests')).toBeLessThan(0.001);
    expect(zeroThreshold('tokens')).toBeLessThan(0.001);
  });
});

describe('rollupCostStatus', () => {
  test('worst status wins', () => {
    expect(rollupCostStatus(['live', 'live'])).toBe('live');
    expect(rollupCostStatus(['live', 'partial_missing'])).toBe('partial_missing');
    expect(rollupCostStatus(['partial_missing', 'missing'])).toBe('missing');
    expect(rollupCostStatus([])).toBe('live');
  });
});
