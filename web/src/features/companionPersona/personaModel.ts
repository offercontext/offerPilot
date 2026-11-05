import { ACTIVITY_TEMPLATES, type PersonaSpec } from '@/services/persona';
export const FIELD_LABELS: Record<keyof PersonaSpec, string> = {
  display_name: '伙伴名字', traits: '性格标签', speaking_style: '说话习惯', verbosity: '表达详略',
  interests_text: '伙伴兴趣', fictional_background: '虚构背景', additional_style_notes: '补充表达说明', enabled_activity_templates: '可开展的虚拟活动',
};
export function personaDifferences(before: PersonaSpec, after: PersonaSpec): (keyof PersonaSpec)[] {
  return (Object.keys(FIELD_LABELS) as (keyof PersonaSpec)[]).filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
}
export function validPersona(spec: PersonaSpec): boolean {
  if (!spec || !Array.isArray(spec.traits) || !spec.traits.every(trait => typeof trait === 'string')
    || !Array.isArray(spec.enabled_activity_templates)
    || !spec.enabled_activity_templates.every(id => ACTIVITY_TEMPLATES.includes(id))
    || new Set(spec.enabled_activity_templates).size !== spec.enabled_activity_templates.length) return false;
  const limits = { display_name: 24, speaking_style: 200, interests_text: 300, fictional_background: 500, additional_style_notes: 500 } as const;
  const texts = [...Object.keys(limits).map(field => spec[field as keyof typeof limits]), ...spec.traits];
  const encoded = JSON.stringify(spec);
  return Boolean(spec.display_name.trim()) && Object.entries(limits).every(([field, limit]) => [...spec[field as keyof typeof limits]].length <= limit)
    && spec.traits.length <= 5 && spec.traits.every(trait => Boolean(trait.trim()) && [...trait].length <= 40)
    && new Set(spec.traits).size === spec.traits.length
    && ![spec.display_name, ...spec.traits].some(text => /[\n\t]/u.test(text))
    && !texts.some(text => /[\p{Cc}\p{Cf}\p{Cs}]/u.test(text.replace(/[\n\t]/g, '')))
    && [...encoded].length <= 2000 && new TextEncoder().encode(encoded).length <= 6000;
}
