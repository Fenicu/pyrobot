/** Всё вне элемента (соседи его предков до body) — `inert` на время окна; возвращает
 * восстановление. Уже inert и помеченное `data-modal-keep` (всплывающие сообщения — живой регион)
 * не трогаются. */
export function inertOutside(el: HTMLElement): () => void {
	const changed: Element[] = [];
	for (let node: Element = el; node.parentElement && node !== document.body; node = node.parentElement) {
		for (const sibling of node.parentElement.children) {
			if (sibling === node || sibling.hasAttribute('inert') || sibling.hasAttribute('data-modal-keep')) continue;
			if (sibling.tagName === 'SCRIPT' || sibling.tagName === 'STYLE') continue;
			sibling.setAttribute('inert', '');
			changed.push(sibling);
		}
	}
	return () => {
		for (const sibling of changed) sibling.removeAttribute('inert');
	};
}
