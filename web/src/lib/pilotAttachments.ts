import { t } from '@/i18n';
import type { PilotContextAttachment } from '@/types/chat';

export const PILOT_ATTACHMENT_LIMIT = 5;

export interface PilotAttachmentDraft {
  attachments: PilotContextAttachment[];
  message?: string;
}

export function emptyPilotAttachmentDraft(): PilotAttachmentDraft {
  return { attachments: [] };
}

export function pilotAttachmentKey(item: PilotContextAttachment): string {
  return `${item.kind}:${item.id}`;
}

export function addPilotAttachment(
  draft: PilotAttachmentDraft,
  attachment: PilotContextAttachment,
): PilotAttachmentDraft {
  if (draft.attachments.some((item) => pilotAttachmentKey(item) === pilotAttachmentKey(attachment))) {
    return { attachments: draft.attachments };
  }

  if (draft.attachments.length >= PILOT_ATTACHMENT_LIMIT) {
    return { ...draft, message: "最多添加 5 个上下文对象" };
  }

  return { attachments: [...draft.attachments, attachment] };
}

export function removePilotAttachment(
  draft: PilotAttachmentDraft,
  itemOrKey: PilotContextAttachment | string,
): PilotAttachmentDraft {
  const key = typeof itemOrKey === 'string' ? itemOrKey : pilotAttachmentKey(itemOrKey);
  return {
    attachments: draft.attachments.filter((item) => pilotAttachmentKey(item) !== key),
  };
}

export function pilotQuickQuestions(attachments: PilotContextAttachment[]): string[] {
  const kinds = new Set(attachments.map((attachment) => attachment.kind));

  if (kinds.has('application') && kinds.has('resume')) {
    return [t("分析简历与岗位的匹配差距"), t("给出最值得修改的三处"), t("生成自我介绍提纲")];
  }

  if (kinds.has('offer') && kinds.has('resume')) {
    return [t("评估简历与 Offer 目标的匹配度"), t("梳理 Offer 中需要确认的条款"), t("给出谈判准备要点")];
  }

  if (kinds.has('application') && kinds.has('offer')) {
    return [t("分析投递进展与 Offer 状态"), t("梳理当前需要确认的关键信息"), t("给出下一步准备要点")];
  }

  if (kinds.has('application')) {
    return [t("分析岗位要求与当前投递阶段"), t("识别下一步需要准备的材料"), t("列出值得追问的岗位信息")];
  }

  if (kinds.has('offer')) {
    return [t("解读 Offer 的关键条件"), t("列出需要确认的 Offer 问题"), t("梳理接受前的比较维度")];
  }

  if (kinds.has('resume')) {
    return [t("提炼简历的核心竞争力"), t("找出简历中需要澄清的经历"), t("建议适配岗位的表达重点")];
  }

  return [];
}
