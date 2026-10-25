import { test, expect } from 'vitest';

function shipping(total: number) {
  return total >= 5000 ? 0 : 500;
}

test('5000円は送料無料', () => {
  const actual = shipping(5000);
  expect(actual).toBe(0);
});

test('別の名前でも同じ検証', () => {
  const result = shipping(5000);
  expect(result).toBe(0);
});

test('4999円の送料は500円', () => {
  const amount = shipping(4999);
  expect(amount).toBe(500);
});

test.each([[5000, 0], [5000, 0]])('送料の表 %i', (total, expected) => {
  expect(shipping(total)).toBe(expected);
});
