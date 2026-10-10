import { describe, expect, it } from 'vitest';
import { applyOrder, moveItem } from './order';

const items = (...ids: number[]) => ids.map((id) => ({ id }));
const idsOf = (list: { id: number }[]) => list.map((a) => a.id);

describe('applyOrder', () => {
	it('без сохранённого порядка — как пришло', () => {
		const list = items(3, 1, 2);
		expect(applyOrder(list, null)).toBe(list);
		expect(applyOrder(list, [])).toBe(list);
	});

	it('по сохранённым id', () => {
		expect(idsOf(applyOrder(items(1, 2, 3), [3, 1, 2]))).toEqual([3, 1, 2]);
	});

	it('новые — в конце в своём порядке, исчезнувшие id пропускаются', () => {
		expect(idsOf(applyOrder(items(5, 1, 4, 2, 3), [2, 9, 1]))).toEqual([2, 1, 5, 4, 3]);
	});

	it('не меняет исходный список', () => {
		const list = items(1, 2);
		applyOrder(list, [2, 1]);
		expect(idsOf(list)).toEqual([1, 2]);
	});
});

describe('moveItem', () => {
	it('вниз и вверх', () => {
		expect(moveItem([1, 2, 3, 4], 0, 2)).toEqual([2, 3, 1, 4]);
		expect(moveItem([1, 2, 3, 4], 3, 1)).toEqual([1, 4, 2, 3]);
	});

	it('на то же место и за края — копия без изменений', () => {
		const ids = [1, 2, 3];
		expect(moveItem(ids, 1, 1)).toEqual([1, 2, 3]);
		expect(moveItem(ids, 0, -1)).toEqual([1, 2, 3]);
		expect(moveItem(ids, 2, 3)).toEqual([1, 2, 3]);
		expect(moveItem(ids, 1, 1)).not.toBe(ids);
	});
});
