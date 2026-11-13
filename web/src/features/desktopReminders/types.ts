export type ReminderScope = 'deadline' | 'interview_preparation' | 'review' | 'stale_application';
export interface ReminderPreferences { enabled: boolean; scopes: ReminderScope[]; paused: boolean; quietEnabled: boolean; quietStart: number; quietEnd: number }
export interface ReminderState { preferences: ReminderPreferences; supported: boolean; error: string | null; status: string }
export interface ReminderTarget { applicationId: number; eventId: number | null }
export interface ReminderBridge {
 getState(): Promise<ReminderState>;
 configure(value: ReminderPreferences): Promise<ReminderState>;
 test(): Promise<string>;
 consumeNavigation(): Promise<ReminderTarget | null>;
 onState(listener: (state: ReminderState) => void): () => void;
 onNavigation(listener: () => void): () => void;
 onTestResult(listener: (result: string) => void): () => void;
}
declare global { interface Window { offerpilotReminders?: ReminderBridge } }
