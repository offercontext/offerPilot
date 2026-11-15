// Mail review and credential dialogs are modal: their mask must cover the
// normal Haru dock/launcher (1050), including its transparent hit area.
// Keep this scoped to mail dialogs; do not change the mascot's placement.
export const MAIL_MODAL_Z_INDEX = 1100;
