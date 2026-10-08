import { describe, expect, it } from 'vitest';
import { COMPANY, accountTitle, activityLabel } from './game';

const acc = (name: string, company: string | null = null, team_tag: string | null = null) => ({
	name,
	company,
	team_tag
});

describe('справочник компаний', () => {
	it('значки и названия как в игре', () => {
		expect(COMPANY).toEqual({
			piper: { mark: '📯', name: 'Pied Piper' },
			hooli: { mark: '🤖', name: 'Hooli' },
			stark: { mark: '⚡️', name: 'Stark Ind.' },
			umbrl: { mark: '☂️', name: 'Umbrella' },
			wayne: { mark: '🎩', name: 'Wayne Ent.' },
			bmesa: { mark: '☣️', name: 'Black Mesa' }
		});
	});
});

describe('accountTitle', () => {
	it('компания и команда: значок, [TAG], имя', () => {
		expect(accountTitle(acc('Fenicu', 'bmesa', 'SU'))).toBe('☣️[SU] Fenicu');
	});

	it('без команды — значок и имя', () => {
		expect(accountTitle(acc('Fenicu', 'bmesa'))).toBe('☣️Fenicu');
	});

	it('без компании, с командой — [TAG] и имя', () => {
		expect(accountTitle(acc('Fenicu', null, 'SU'))).toBe('[SU] Fenicu');
	});

	it('ничего не известно — имя', () => {
		expect(accountTitle(acc('Fenicu'))).toBe('Fenicu');
	});

	it('незнакомый код компании значка не даёт', () => {
		expect(accountTitle(acc('Fenicu', 'zzz', 'SU'))).toBe('[SU] Fenicu');
	});

	it('значок уже в имени — не повторяется, в том числе без VS16', () => {
		expect(accountTitle(acc('☣️Fenicu', 'bmesa'))).toBe('☣️Fenicu');
		expect(accountTitle(acc('☣Fenicu', 'bmesa'))).toBe('☣️Fenicu');
		expect(accountTitle(acc('⚡Fenicu', 'stark'))).toBe('⚡️Fenicu');
	});

	it('[TAG] уже в имени — не повторяется', () => {
		expect(accountTitle(acc('[SU] Fenicu', null, 'SU'))).toBe('[SU] Fenicu');
		expect(accountTitle(acc('☣️[SU] Fenicu', 'bmesa', 'SU'))).toBe('☣️[SU] Fenicu');
	});

	it('значок в имени, тега нет — тег ставится после значка', () => {
		expect(accountTitle(acc('☣️Fenicu', 'bmesa', 'SU'))).toBe('☣️[SU] Fenicu');
	});

	it('тег в имени, значка нет — значок ставится перед тегом', () => {
		expect(accountTitle(acc('[SU] Fenicu', 'bmesa', 'SU'))).toBe('☣️[SU] Fenicu');
	});

	it('чужой значок или тег в имени не считается своим', () => {
		expect(accountTitle(acc('🎩Fenicu', 'bmesa', 'SU'))).toBe('☣️[SU] 🎩Fenicu');
		expect(accountTitle(acc('[XX] Fenicu', null, 'SU'))).toBe('[SU] [XX] Fenicu');
	});
});

describe('подписи дел', () => {
	it('«Пилить» стартап', () => {
		expect(activityLabel('startup')).toBe('пилить стартап');
	});
});
