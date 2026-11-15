import { t, useLocale } from '@/i18n';
import { useEffect, useRef } from 'react';
import type { FormInstance } from 'antd';
import applicationMessages from '@/i18n/catalogs/applications';

// These values are only used for authored status/error messages retained in UI
// state or recovery drafts. Never pass user fields, email bodies, or AI output.
const sourceByTranslation = new Map(
  Object.entries(applicationMessages).filter(([, translated]) => translated.length > 0).map(([source, translated]) => [translated, source]),
);

/** Re-render a retained application UI message without changing its state. */
export function applicationUiMessage(message: string): string {
  if (!message) return message;
  if (Object.prototype.hasOwnProperty.call(applicationMessages, message)) return t(message);
  const source = sourceByTranslation.get(message);
  return source === undefined ? message : t(source);
}

/** Refresh only existing form errors, without resetting any entered values. */
export function useLocalizedValidation(form: Pick<FormInstance, 'getFieldsError' | 'validateFields'>): void {
  const locale = useLocale();
  const previousLocale = useRef(locale);
  useEffect(() => {
    if (previousLocale.current === locale) return;
    previousLocale.current = locale;
    const invalidFields = form.getFieldsError().filter((field) => field.errors.length > 0).map((field) => field.name);
    if (invalidFields.length > 0) void form.validateFields(invalidFields).catch(() => undefined);
  }, [form, locale]);
}
