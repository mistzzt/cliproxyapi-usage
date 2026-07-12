import { describe, test, expect } from 'bun:test';
import {
  DEFAULT_EXPLORER_STATE,
  availableBreakdowns,
  availableDisplays,
  normalizeExplorerState,
  parseExplorerState,
  type ExplorerState,
} from './explorerState';

describe('availableBreakdowns', () => {
  test('token_type only for tokens', () => {
    expect(availableBreakdowns('requests')).toEqual(['total', 'model']);
    expect(availableBreakdowns('cost')).toEqual(['total', 'model']);
    expect(availableBreakdowns('tokens')).toEqual(['total', 'model', 'token_type']);
  });
});

describe('availableDisplays', () => {
  test('total is line only, token_type stacked only, model both', () => {
    expect(availableDisplays('total')).toEqual(['line']);
    expect(availableDisplays('token_type')).toEqual(['stacked']);
    expect(availableDisplays('model')).toEqual(['line', 'stacked']);
  });
});

describe('normalizeExplorerState', () => {
  test('cost + token_type drops breakdown to total', () => {
    const out = normalizeExplorerState({
      metric: 'cost',
      breakdown: 'token_type',
      granularity: 'auto',
      display: 'stacked',
    });
    expect(out.breakdown).toBe('total');
    expect(out.display).toBe('line'); // total forces line
  });

  test('requests + token_type drops breakdown to total', () => {
    const out = normalizeExplorerState({
      metric: 'requests',
      breakdown: 'token_type',
      granularity: 'hour',
      display: 'stacked',
    });
    expect(out.breakdown).toBe('total');
  });

  test('token_type forces stacked display for tokens', () => {
    const out = normalizeExplorerState({
      metric: 'tokens',
      breakdown: 'token_type',
      granularity: 'auto',
      display: 'line',
    });
    expect(out.breakdown).toBe('token_type');
    expect(out.display).toBe('stacked');
  });

  test('total forces line even when stacked requested', () => {
    const out = normalizeExplorerState({
      metric: 'requests',
      breakdown: 'total',
      granularity: 'day',
      display: 'stacked',
    });
    expect(out.display).toBe('line');
  });

  test('model keeps stacked display', () => {
    const out = normalizeExplorerState({
      metric: 'tokens',
      breakdown: 'model',
      granularity: 'auto',
      display: 'stacked',
    });
    expect(out.breakdown).toBe('model');
    expect(out.display).toBe('stacked');
  });

  test('is idempotent', () => {
    const states: ExplorerState[] = [
      { metric: 'cost', breakdown: 'token_type', granularity: 'auto', display: 'stacked' },
      { metric: 'tokens', breakdown: 'model', granularity: 'hour', display: 'stacked' },
      { metric: 'requests', breakdown: 'total', granularity: 'day', display: 'stacked' },
    ];
    for (const s of states) {
      const once = normalizeExplorerState(s);
      expect(normalizeExplorerState(once)).toEqual(once);
    }
  });
});

describe('parseExplorerState', () => {
  test('falls back to defaults for garbage', () => {
    expect(parseExplorerState(null)).toEqual(DEFAULT_EXPLORER_STATE);
    expect(parseExplorerState('nope')).toEqual(DEFAULT_EXPLORER_STATE);
    expect(parseExplorerState({ metric: 'bogus' })).toEqual(DEFAULT_EXPLORER_STATE);
  });

  test('keeps valid fields and normalizes the combination', () => {
    const out = parseExplorerState({
      metric: 'cost',
      breakdown: 'token_type',
      granularity: 'day',
      display: 'stacked',
    });
    expect(out).toEqual({
      metric: 'cost',
      breakdown: 'total',
      granularity: 'day',
      display: 'line',
    });
  });

  test('partial object fills the rest with defaults', () => {
    const out = parseExplorerState({ metric: 'tokens', breakdown: 'model' });
    expect(out.metric).toBe('tokens');
    expect(out.breakdown).toBe('model');
    expect(out.granularity).toBe('auto');
    expect(out.display).toBe('line');
  });
});
