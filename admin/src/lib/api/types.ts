import type { components } from './schema';

type S = components['schemas'];

export type PublicState = S['PublicState'];
export type StateOut = S['StateOut'];
export type EngineStatus = S['EngineStatusOut'];
export type TgStatus = S['TgStatusOut'];
export type TgState = S['TgState'];
export type ConfirmRequired = S['ConfirmRequired'];
export type CommandOut = S['CommandOut'];
export type JournalPage = S['JournalPage'];
export type MessageItem = S['MessageItem'];
export type ActionItem = S['ActionItem'];
export type DecisionItem = S['DecisionItem'];
export type JournalItem = MessageItem | ActionItem | DecisionItem;
export type DecisionOut = S['DecisionOut'];
export type ActionOut = S['ActionOut'];
export type ScenarioRunOut = S['ScenarioRunOut'];
export type ScenarioRunDetail = S['ScenarioRunDetail'];
export type ScenarioInfo = S['ScenarioInfo'];
export type ParamSpec = S['ParamSpec'];
export type SettingsOut = S['SettingsOut'];
export type SettingsPatchOut = S['SettingsPatchOut'];
export type SettingsVersion = S['SettingsVersionOut'];
export type MetroRunSummary = S['MetroRunSummary'];
export type MetroRunDetail = S['MetroRunDetail'];
export type MetricsOut = S['MetricsOut'];
export type NotificationOut = S['NotificationOut'];
export type UnrecognizedOut = S['UnrecognizedOut'];
export type Outlook = S['OutlookOut'];
export type PlanCandidate = S['PlanCandidateOut'];
export type PlanAct = S['PlanActOut'];
export type PlanTimer = S['PlanTimerOut'];
export type WakeKind = PlanTimer['kind'];
export type DailyOut = S['DailyOut'];
export type DayOut = S['DayOut'];
export type KindOut = S['KindOut'];
export type BalanceOut = S['BalanceOut'];
export type AccountOut = S['AccountOut'];
export type ArtifactOut = S['ArtifactOut'];

/** Наблюдение поля состояния: `{value, at, src}`. */
export interface Observed<T> {
	value: T;
	at: string;
	src: 'screen' | 'derived' | 'doubtful';
}

/** Кнопки сообщения: `[текст, ряд, колонка, data, url, switch]` (как в журнале и SSE). */
export type InlineButton = [string, number, number, string | null, string | null, string | null];
export interface Markup {
	inline?: InlineButton[];
	reply?: string[][];
}
