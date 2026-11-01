import { expect, test } from 'bun:test';
import { change, fmtChange } from './format';

test('change: none without a previous period, new from zero, multiples from +100%', () => {
  expect(change(10, null)).toBeNull();
  expect(change(0, 0)).toBe(0);
  expect(fmtChange('new')).toEqual({ direction: 'new', text: 'new' });
  expect(change(10, 0)).toBe('new');
  expect(fmtChange(change(50, 100) as number)).toEqual({ direction: 'down', text: '50%' });
  expect(fmtChange(0.999)).toEqual({ direction: 'up', text: '100%' });
  expect(fmtChange(change(200, 100) as number)).toEqual({ direction: 'up', text: '2.0×' });
});
