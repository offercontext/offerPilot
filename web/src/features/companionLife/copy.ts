import type { t } from '@/i18n';
import type { CompanionActivity, CompanionSnapshot } from './contracts';

type Alternatives = readonly [string, string];
type ActivityCopy = { steps: readonly Alternatives[]; completed: Alternatives; paused: Alternatives; wish: string; invitation: Alternatives };
const ACTIVITIES: Record<string, ActivityCopy> = {
  paper_boat: {
    wish: '一起给纸小船起个名字',
    invitation: ['纸小船有个小心愿：一起给它起个名字。想参与再说，不回应也没关系。', '想一起给纸小船起个名字。只是小心愿，略过也完全没关系。'],
    steps: [
      ['想象中的彩纸摊开了，正在挑哪一张适合变成小船。', '先在小世界里挑张彩纸。还没开折，脑袋里已经有只摇摇晃晃的小船。'],
      ['彩纸挑好了，正在一点点折出船身。折痕也有自己的小脾气。', '选好的彩纸正在变成船身，边角慢慢对齐。小船还在成形呢。'],
      ['船身成形了，正在琢磨小旗子的花样。小小一面，也想画得有趣。', '纸小船已经有了船身，现在轮到给它画一面小旗。'],
    ],
    completed: ['纸小船和小旗都做好了，先让它在想象的小岸边歇一会儿。', '这只纸小船折好了。就放在小世界里看看，不急着安排下一件事。'],
    paused: ['纸小船这件小事暂停着，留在原来这一步，没有继续折。', '折小船先歇一歇。进度留在这里，不会偷偷往前跑。'],
  },
  cloud_catalog: {
    wish: '一起给一朵云起个怪名字',
    invitation: ['云朵有个小心愿：一起给它起个怪名字。想参与再说，不回应也没关系。', '想一起给这朵云起个怪名字。只是小心愿，略过也完全没关系。'],
    steps: [
      ['正在想象一朵软乎乎的云，看看脑袋里会飘出什么形状。', '小世界里先留一片空空的天空，慢慢想象那朵云的样子。'],
      ['云朵的样子想到了，正在画它软软的轮廓。边边拐个弯也没关系。', '脑袋里那朵云正一点点变成线条，轮廓可以歪歪软软的。'],
      ['云朵的轮廓画好了，现在给它的小名字添一点奇思妙想。', '软软的云已经有了轮廓，正在琢磨和它相配的小名字。'],
    ],
    completed: ['这朵想象中的云有了小名字。先让它软乎乎地待着，今天这样就很好。', '云朵和它的小名字都收好了。小世界可以安静一会儿，不用接着忙。'],
    paused: ['云朵这件小事暂停着，轮到哪一步就留在哪一步。', '先把云朵的小日常放一放。进度停在原处，没有接着画或起名字。'],
  },
  paper_stars: {
    wish: '一起挑一颗幸运星',
    invitation: ['纸星星有个小心愿：一起挑一颗幸运星。想参与再说，不回应也没关系。', '想一起在纸星星里挑一颗幸运星。只是小心愿，略过也完全没关系。'],
    steps: [
      ['正在挑想象中的星星纸，看看哪几张凑在一起比较有趣。', '先挑几张星星纸。小世界里的纸还平平的，胖星星还在脑袋里。'],
      ['星星纸挑好了，正在折一颗胖乎乎的小星星。角角慢慢鼓起来。', '选好的纸正在变成小星星，先认真折出胖乎乎的样子。'],
      ['胖星星折好了，正在把星星串成一个小挂饰。', '纸星星已经有了胖乎乎的形状，现在试着把它们排进小挂饰里。'],
    ],
    completed: ['纸星星的小挂饰做好了，就在想象的小角落里轻轻挂着。', '这一串纸星星完成啦。先看看它们胖乎乎的样子，不急着再开工。'],
    paused: ['纸星星的小日常暂停着，留在原来这一步，没有继续做。', '星星纸这边先歇一歇。进度安安静静留在这里，不会自己往前走。'],
  },
};
const FALLBACK: Record<CompanionActivity['status'], Alternatives> = {
  active: ['小世界里有件小事正在慢慢做，先停留在当前这一步。', '正在小世界里摆弄一件小事，后面的进度还没有发生。'],
  paused: ['这件小事暂停着，进度留在原来这一步。', '小日常先歇一歇，没有继续往前走。'],
  waiting_shared: ['共同的这一步留在这里，不会自动往前走，也不用赶时间。', '这一步先安静放着，等明确回应后再继续，不着急。'],
  completed: ['这件小事完成了，先在小世界里歇一会儿。', '今天的小日常做到这里就很好，不急着开始下一件。'],
  abandoned: ['这件小事先放下了。小世界里留点空白也很好。', '这次就做到这里，不必把每件小事都做完。'],
};

/** One stable voice per activity, independent of revisions, time and surface. */
function activityVariant(id: string): number {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) hash = Math.imul(hash ^ id.charCodeAt(index), 16777619);
  return (hash >>> 0) % 2;
}

/**
 * Pure authored projection of committed state. The explicit translator keeps
 * locale at the caller; no random/time, backend prose, or participation guesses.
 * step_index is the current task, not proof that the current task is finished.
 * invitedWish is supplied only for a currently eligible bid, never static status.
 */
export function companionLifeExpression(activity: CompanionActivity | null | undefined, translate: typeof t, invitedWish: CompanionSnapshot['state']['wish'] = null): string {
  if (!activity) return translate('小世界里暂时没安排，安静待一会儿也很好。');
  const variant = activityVariant(activity.id);
  if (activity.status === 'waiting_shared' && activity.promise?.trim()) {
    return translate(variant === 0 ? '这一步先留着：{promise}。不赶时间。' : '{promise}。这一步停在这里，不会自动完成。', { promise: translate(activity.promise) });
  }
  const template = Object.prototype.hasOwnProperty.call(ACTIVITIES, activity.template_id) ? ACTIVITIES[activity.template_id] : undefined;
  const validStep = template && Number.isInteger(activity.step_index) && activity.step_index >= 0
    && activity.step_index < template.steps.length && activity.step_count === template.steps.length;
  if (template && validStep && activity.status === 'active' && invitedWish?.id
    && invitedWish.activity_id === activity.id && invitedWish.text === template.wish) return translate(template.invitation[variant]);
  let copy = FALLBACK[activity.status];
  if (template) {
    if (activity.status === 'paused') copy = template.paused;
    else if (activity.status === 'active' && validStep) copy = template.steps[activity.step_index];
    else if (activity.status === 'completed' && validStep && activity.step_index === template.steps.length - 1) copy = template.completed;
  }
  return translate(copy[variant]);
}
