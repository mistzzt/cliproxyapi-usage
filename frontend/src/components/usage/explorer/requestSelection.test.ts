import { describe, test, expect } from 'bun:test';
import {
  EXPLORER_TOP_N,
  requestKey,
  resolveRequestBucket,
  selectExplorerRequest,
  zoomClearKey,
  type ExplorerRequestContext,
} from './requestSelection';
import type { ExplorerState } from './explorerState';

const allModelsCtx: ExplorerRequestContext = {
  isAllModels: true,
  selectedModels: ['all'],
  autoBucket: 'hour',
};

function state(partial: Partial<ExplorerState>): ExplorerState {
  return {
    metric: 'requests',
    breakdown: 'total',
    granularity: 'auto',
    display: 'line',
    ...partial,
  };
}

describe('resolveRequestBucket', () => {
  test('auto uses the range-derived bucket', () => {
    expect(resolveRequestBucket('auto', 'day')).toBe('day');
    expect(resolveRequestBucket('auto', 'hour')).toBe('hour');
  });
  test('explicit preference wins', () => {
    expect(resolveRequestBucket('hour', 'day')).toBe('hour');
    expect(resolveRequestBucket('day', 'hour')).toBe('day');
  });
});

describe('selectExplorerRequest - total breakdown', () => {
  test('all models: timeseries, no top_n, no models', () => {
    const req = selectExplorerRequest(state({ breakdown: 'total', metric: 'requests' }), allModelsCtx);
    expect(req).toEqual({ endpoint: 'timeseries', bucket: 'hour', metric: 'requests' });
  });

  test('explicit models: sends models filter (to scope) but no top_n', () => {
    const req = selectExplorerRequest(state({ breakdown: 'total', metric: 'cost' }), {
      isAllModels: false,
      selectedModels: ['gpt-4', 'claude'],
      autoBucket: 'day',
    });
    expect(req).toEqual({
      endpoint: 'timeseries',
      bucket: 'day',
      metric: 'cost',
      models: ['gpt-4', 'claude'],
    });
  });
});

describe('selectExplorerRequest - model breakdown', () => {
  test('all models: requests top_n = 6', () => {
    const req = selectExplorerRequest(state({ breakdown: 'model', metric: 'tokens' }), allModelsCtx);
    expect(req.endpoint).toBe('timeseries');
    expect(req.metric).toBe('tokens');
    expect(req.topN).toBe(EXPLORER_TOP_N);
    expect(req.topN).toBe(6);
    expect(req.models).toBeUndefined();
  });

  test('explicit models: sends models, no top_n', () => {
    const req = selectExplorerRequest(state({ breakdown: 'model', metric: 'requests' }), {
      isAllModels: false,
      selectedModels: ['a', 'b', 'c'],
      autoBucket: 'hour',
    });
    expect(req.models).toEqual(['a', 'b', 'c']);
    expect(req.topN).toBeUndefined();
  });
});

describe('selectExplorerRequest - token_type breakdown', () => {
  test('uses token-breakdown endpoint, no metric', () => {
    const req = selectExplorerRequest(
      state({ metric: 'tokens', breakdown: 'token_type', display: 'stacked' }),
      allModelsCtx,
    );
    expect(req.endpoint).toBe('token-breakdown');
    expect(req.metric).toBeUndefined();
    expect(req.topN).toBeUndefined();
  });

  test('passes explicit model + api-key filters through', () => {
    const req = selectExplorerRequest(
      state({ metric: 'tokens', breakdown: 'token_type', display: 'stacked' }),
      { isAllModels: false, selectedModels: ['m1'], apiKeys: ['k1'], autoBucket: 'day' },
    );
    expect(req).toEqual({
      endpoint: 'token-breakdown',
      bucket: 'day',
      models: ['m1'],
      apiKeys: ['k1'],
    });
  });
});

describe('selectExplorerRequest - api-key propagation', () => {
  test('api keys are attached to every timeseries request', () => {
    const req = selectExplorerRequest(state({ breakdown: 'model', metric: 'requests' }), {
      isAllModels: true,
      selectedModels: ['all'],
      apiKeys: ['redacted-1', 'redacted-2'],
      autoBucket: 'hour',
    });
    expect(req.apiKeys).toEqual(['redacted-1', 'redacted-2']);
  });

  test('empty api-key list is treated as all users (omitted)', () => {
    const req = selectExplorerRequest(state({}), {
      isAllModels: true,
      selectedModels: ['all'],
      apiKeys: [],
      autoBucket: 'hour',
    });
    expect(req.apiKeys).toBeUndefined();
  });

  test('display line vs stacked produces the same descriptor', () => {
    const base: ExplorerRequestContext = {
      isAllModels: true,
      selectedModels: ['all'],
      autoBucket: 'day',
    };
    const line = selectExplorerRequest(state({ breakdown: 'model', display: 'line' }), base);
    const stacked = selectExplorerRequest(state({ breakdown: 'model', display: 'stacked' }), base);
    expect(line).toEqual(stacked);
  });
});

// requestKey is the value that actually drives the explorer refetch, so it
// must change across every request-relevant dimension and stay invariant to
// display mode. These guard the field-completeness of requestKey: e.g.
// dropping `m: req.metric` would silently break refetch on a metric switch.
describe('requestKey - refetch dependency completeness', () => {
  const base: ExplorerRequestContext = {
    isAllModels: true,
    selectedModels: ['all'],
    autoBucket: 'hour',
  };
  const key = (s: Partial<ExplorerState>, ctx: ExplorerRequestContext = base) =>
    requestKey(selectExplorerRequest(state(s), ctx));

  test('changes when the metric changes', () => {
    expect(key({ metric: 'requests' })).not.toBe(key({ metric: 'cost' }));
    expect(key({ metric: 'requests' })).not.toBe(key({ metric: 'tokens' }));
  });

  test('changes when the breakdown data source changes (all models: top_n)', () => {
    // total -> model in all-models mode adds top_n, so the descriptor differs.
    expect(key({ breakdown: 'total' })).not.toBe(key({ breakdown: 'model' }));
  });

  test('changes when the breakdown switches to token_type (endpoint changes)', () => {
    expect(key({ metric: 'tokens', breakdown: 'model' })).not.toBe(
      key({ metric: 'tokens', breakdown: 'token_type', display: 'stacked' }),
    );
  });

  test('changes when the effective granularity (bucket) changes', () => {
    expect(key({ granularity: 'hour' })).not.toBe(key({ granularity: 'day' }));
    // auto resolves to the range-derived bucket, so the autoBucket is encoded.
    const hourAuto = key({ granularity: 'auto' }, { ...base, autoBucket: 'hour' });
    const dayAuto = key({ granularity: 'auto' }, { ...base, autoBucket: 'day' });
    expect(hourAuto).not.toBe(dayAuto);
  });

  test('changes when the model filter changes', () => {
    const two = key({ breakdown: 'total' }, {
      isAllModels: false,
      selectedModels: ['a', 'b'],
      autoBucket: 'hour',
    });
    const three = key({ breakdown: 'total' }, {
      isAllModels: false,
      selectedModels: ['a', 'b', 'c'],
      autoBucket: 'hour',
    });
    expect(two).not.toBe(three);
    // all-models vs an explicit selection also differs.
    expect(key({ breakdown: 'total' })).not.toBe(two);
  });

  test('changes when the api-key filter changes', () => {
    const none = key({});
    const withKeys = key({}, { ...base, apiKeys: ['k1'] });
    const otherKeys = key({}, { ...base, apiKeys: ['k2'] });
    expect(none).not.toBe(withKeys);
    expect(withKeys).not.toBe(otherKeys);
  });

  test('is invariant to display mode (line vs stacked never refetches)', () => {
    const ctx: ExplorerRequestContext = {
      isAllModels: true,
      selectedModels: ['all'],
      autoBucket: 'day',
    };
    expect(key({ breakdown: 'model', display: 'line' }, ctx)).toBe(
      key({ breakdown: 'model', display: 'stacked' }, ctx),
    );
  });
});

// zoomClearKey governs zoom/legend resets. It must change on any breakdown
// change even when requestKey is identical (explicit-model total vs model),
// per the design rule that any breakdown change clears zoom.
describe('zoomClearKey - breakdown-sensitive view key', () => {
  const explicitCtx: ExplorerRequestContext = {
    isAllModels: false,
    selectedModels: ['a', 'b', 'c'],
    autoBucket: 'hour',
  };

  test('breakdown total <-> model changes the key even with explicit models', () => {
    const totalReq = selectExplorerRequest(state({ breakdown: 'total' }), explicitCtx);
    const modelReq = selectExplorerRequest(state({ breakdown: 'model' }), explicitCtx);
    // Same fetch (requestKey identical) ...
    expect(requestKey(totalReq)).toBe(requestKey(modelReq));
    // ... but different zoom-clear key, so zoom/legend still reset.
    expect(zoomClearKey(totalReq, 'total')).not.toBe(zoomClearKey(modelReq, 'model'));
  });

  test('reflects a request change (metric) as well', () => {
    const reqReq = selectExplorerRequest(state({ metric: 'requests' }), explicitCtx);
    const costReq = selectExplorerRequest(state({ metric: 'cost' }), explicitCtx);
    expect(zoomClearKey(reqReq, 'total')).not.toBe(zoomClearKey(costReq, 'total'));
  });

  test('is stable when nothing view-relevant changes', () => {
    const req = selectExplorerRequest(state({ breakdown: 'model' }), explicitCtx);
    expect(zoomClearKey(req, 'model')).toBe(zoomClearKey(req, 'model'));
  });
});
