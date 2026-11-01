import { expect, test } from 'bun:test';
import { change, fmtChange } from './format';

test('change with no previous is new; +100% and above is a multiple', () => {
  expect(fmtChange(change(10, null))).toEqual({ direction: 'new', text: 'new' });
  expect(fmtChange(change(10, 0))).toEqual({ direction: 'new', text: 'new' });
  expect(fmtChange(change(50, 100))).toEqual({ direction: 'down', text: '50%' });
  expect(fmtChange(0.999)).toEqual({ direction: 'up', text: '100%' });
  expect(fmtChange(change(200, 100))).toEqual({ direction: 'up', text: '2.0×' });
});
