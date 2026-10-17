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
export type MetroLive = S['MetroLive'];
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
export type BattleHq = S['BattleHqOut'];
export type HqBattle = S['BattleOut'];
export type HqBattleAccount = S['BattleAccountOut'];
export type HqBattleSummary = S['BattleSummaryOut'];
export type HqTarget = S['HqTargetOut'];
export type HqCompany = S['HqCompanyOut'];
export type BattleOrders = S['BattleOrdersOut'];
export type BattleOrdersIn = S['BattleOrdersIn'];
export type BattleOrdersSaved = S['BattleOrdersSavedOut'];
export type EngineBulkResult = S['EngineBulkResultOut'];
export type EngineBulkOut = S['EngineBulkOut'];
export type SettingsBulkIn = S['SettingsBulkIn'];
export type SettingsBulkResult = S['SettingsBulkResultOut'];
export type SettingsBulkOut = S['SettingsBulkOut'];
export type ScheduleOut = S['ScheduleOut'];
export type AccountSchedule = S['AccountScheduleOut'];
export type ScheduleItem = S['ScheduleItemOut'];
export type SharedScheduleItem = S['SharedScheduleItemOut'];
export type ArtifactOut = S['ArtifactOut'];
export type OverviewAccount = S['OverviewAccountOut'];
export type OverviewNextAction = S['NextActionOut'];
export type OverviewBattle = S['OverviewBattleOut'];
export type OverviewTotals = S['TotalsOut'];
export type TotalsBlock = S['TotalsBlockOut'];
export type TotalsDelta = S['TotalDeltaOut'];
export type TotalsAccount = S['TotalsAccountOut'];
export type TotalsDay = S['TotalsDayOut'];
export type OverviewFeed = S['FeedOut'];
export type FeedItem = S['FeedItemOut'];
export type FeedType = FeedItem['type'];
export type FeedWatermark = S['FeedWatermarkOut'];
export type GadgetsState = S['GadgetsState'];
export type GadgetsOut = S['GadgetsOut'];
export type TangerinePartnerOut = S['TangerinePartnerOut'];
export type GadgetOut = S['GadgetOut'];
export type GadgetTarget = S['TargetOut'];
export type GadgetBuyPlan = S['BuyPlanOut'];
export type UpgradeTaskOut = S['UpgradeTaskOut'];
export type UpgradeStartIn = S['UpgradeStartIn'];
export type UpSlotKey = UpgradeStartIn['slot'];
export type UpgradeChoice = UpgradeStartIn['kind'];
export type GadgetSetKey = GadgetTarget['set'];
export type InvitePeekOut = S['InvitePeekOut'];
export type InviteAcceptIn = S['InviteAcceptIn'];
export type InviteAcceptOut = S['InviteAcceptOut'];
export type RecoverStartIn = S['RecoverStartIn'];
export type RecoverFinishIn = S['RecoverFinishIn'];
export type RecoveryCodesIn = S['RecoveryCodesIn'];
export type RecoveryCodesOut = S['RecoveryCodesOut'];
export type MeOut = S['MeOut'];

export type AdminUserOut = S['AdminUserOut'];
export type AdminUserPatchIn = S['AdminUserPatchIn'];
export type AdminUserDeleteIn = S['AdminUserDeleteIn'];
export type AdminAccountOut = S['AdminAccountOut'];
export type AdminAccountPatchIn = S['AdminAccountPatchIn'];
export type AdminAccountDeleteIn = S['AdminAccountDeleteIn'];
export type AdminInviteOut = S['AdminInviteOut'];
export type AdminInviteCreateIn = S['AdminInviteCreateIn'];
export type AdminInviteCreatedOut = S['AdminInviteCreatedOut'];
export type AdminServerSettingsOut = S['AdminServerSettingsOut'];
export type AdminServerSettingsPatchIn = S['AdminServerSettingsPatchIn'];
export type AdminServerSettingsPatchOut = S['AdminServerSettingsPatchOut'];
export type AdminAuditOut = S['AdminAuditOut'];
export type AdminAuditPageOut = S['AdminAuditPageOut'];
export type AdminNotificationOut = S['AdminNotificationOut'];
export type AdminNotificationReadIn = S['AdminNotificationReadIn'];

/** Наблюдение поля состояния: `{value, at, src}`. */
export interface Observed<T> {
	value: T;
	at: string;
	src: 'screen' | 'derived' | 'doubtful';
}

/** Кнопки сообщения: `[текст, ряд, колонка, data, url, switch]` (как в журнале и SSE); у кнопок с
 * выбором чата и копированием ещё `switch_chosen` и `copy`. */
export type InlineButton = [
	string,
	number,
	number,
	string | null,
	string | null,
	string | null,
	(string | null)?,
	(string | null)?
];
export interface Markup {
	inline?: InlineButton[];
	reply?: string[][];
}
