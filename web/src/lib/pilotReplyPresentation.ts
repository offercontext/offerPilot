export interface TurnPresentation {
  conclusion: string;
  actions: string[];
  detailMarkdown: string;
}

const MARKDOWN_HEADING = /^ {0,3}(#{1,6})[\t ]+(.+?)[\t ]*$/;
const PRESENTATION_ACTION = /^([\t ]*)[-*+][\t ]+(.+?)\s*$/;

interface MarkdownHeading {
  level: number;
  text: string;
}

interface ParsedPresentationActions {
  actions: string[];
  literalMarkdown: string;
}

/** Reconstruct the structured conclusion and actions from a persisted Pilot reply. */
export function parseTurnPresentation(content: string): TurnPresentation | undefined {
  const lines = content.replace(/\r\n?/g, '\n').split('\n');
  const fencedLines = fencedLineIndexes(lines);
  let conclusionIndex = -1;
  let nextStepsIndex = -1;
  let nextStepsLevel = 0;

  for (let index = 0; index < lines.length; index += 1) {
    if (fencedLines[index]) continue;
    const heading = markdownHeading(lines[index]);
    if (!heading || heading.level < 2 || heading.level > 3) continue;
    if (heading.text === '结论' && conclusionIndex < 0) conclusionIndex = index;
    if (heading.text === '下一步' && nextStepsIndex < 0) {
      nextStepsIndex = index;
      nextStepsLevel = heading.level;
    }
  }

  if (conclusionIndex < 0 || nextStepsIndex < 0 || conclusionIndex > nextStepsIndex) return undefined;

  const conclusion = lines.slice(conclusionIndex + 1, nextStepsIndex).join('\n').trim();
  const tailIndex = lines.findIndex(
    (line, index) =>
      index > nextStepsIndex &&
      !fencedLines[index] &&
      (markdownHeading(line)?.level ?? Number.POSITIVE_INFINITY) <= nextStepsLevel,
  );
  const actionEndIndex = tailIndex < 0 ? lines.length : tailIndex;
  const parsedActions = parsePresentationActions(lines, fencedLines, nextStepsIndex + 1, actionEndIndex);

  if (!conclusion || parsedActions.actions.length === 0) return undefined;

  const detailMarkdown = [
    lines.slice(0, conclusionIndex).join('\n').trim(),
    parsedActions.literalMarkdown,
    lines.slice(actionEndIndex).join('\n').trim(),
  ]
    .filter(Boolean)
    .join('\n\n');

  return {
    conclusion,
    actions: parsedActions.actions,
    detailMarkdown,
  };
}

function markdownHeading(line: string): MarkdownHeading | undefined {
  const match = line.match(MARKDOWN_HEADING);
  if (!match) return undefined;
  return {
    level: match[1].length,
    text: match[2].replace(/[\t ]+#+[\t ]*$/, '').trim(),
  };
}

function fencedLineIndexes(lines: string[]): boolean[] {
  const fenced = Array<boolean>(lines.length).fill(false);
  let marker: string | undefined;
  let markerLength = 0;
  let containerContentStart: number | undefined;
  let nestedFence = false;

  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^([\t ]*)(`{3,}|~{3,})(.*)$/);
    if (!marker) {
      if (!match) continue;
      const indent = visualIndent(match[1]);
      const contentStart = listContainerContentStart(lines, index, indent);
      if (indent > 3 && contentStart === undefined) continue;
      fenced[index] = true;
      marker = match[2][0];
      markerLength = match[2].length;
      containerContentStart = contentStart;
      nestedFence = contentStart !== undefined;
      continue;
    }

    fenced[index] = true;
    const closingIndent = match ? visualIndent(match[1]) : 0;
    const canClose = match && (
      nestedFence
        ? containerContentStart !== undefined &&
          closingIndent >= containerContentStart &&
          closingIndent <= containerContentStart + 3
        : closingIndent <= 3
    );
    if (canClose && match[2][0] === marker && match[2].length >= markerLength && /^[\t ]*$/.test(match[3])) {
      marker = undefined;
      markerLength = 0;
      containerContentStart = undefined;
      nestedFence = false;
    }
  }

  return fenced;
}

function listContainerContentStart(lines: string[], index: number, indent: number): number | undefined {
  for (let previousIndex = index - 1; previousIndex >= 0; previousIndex -= 1) {
    const previousLine = lines[previousIndex];
    if (!previousLine.trim()) continue;
    const previousIndent = visualIndent(previousLine);
    if (previousIndent >= indent) continue;
    const contentStart = listItemContentStart(previousLine);
    if (contentStart !== undefined && indent >= contentStart) return contentStart;
    if (previousIndent === 0) return undefined;
  }
  return undefined;
}

function listItemContentStart(line: string): number | undefined {
  const match = line.match(/^[\t ]*(?:[-*+]|\d+[.)])[\t ]+/);
  return match ? visualWidth(match[0]) : undefined;
}

function parsePresentationActions(
  lines: string[],
  fencedLines: boolean[],
  startIndex: number,
  endIndex: number,
): ParsedPresentationActions {
  const actions: string[][] = [];
  const literalLines: string[] = [];
  let actionIndent: number | undefined;
  let currentAction: string[] | undefined;

  for (let index = startIndex; index < endIndex; index += 1) {
    const line = lines[index];
    if (fencedLines[index]) {
      literalLines.push(line);
      currentAction = undefined;
      continue;
    }
    const action = line.match(PRESENTATION_ACTION);
    const indent = action ? visualIndent(action[1]) : undefined;

    if (actionIndent === undefined && indent !== undefined && indent >= 4) {
      literalLines.push(line);
      continue;
    }
    if (actionIndent === undefined && visualIndent(line) >= 4) {
      literalLines.push(line);
      continue;
    }

    if (action && (actionIndent === undefined || indent === actionIndent)) {
      actionIndent ??= indent;
      if (actions.length >= 3) {
        literalLines.push(line);
        currentAction = undefined;
        continue;
      }
      currentAction = [action[2].trim()];
      actions.push(currentAction);
      continue;
    }

    if (currentAction && actionIndent !== undefined && line.trim() && visualIndent(line) > actionIndent) {
      currentAction.push(line.trimEnd());
      continue;
    }

    literalLines.push(line);
  }

  return {
    actions: actions.map((action) => action.join('\n').trim()),
    literalMarkdown: trimMarkdown(literalLines),
  };
}

function visualIndent(line: string): number {
  const whitespace = line.match(/^[\t ]*/)?.[0] ?? '';
  return visualWidth(whitespace);
}

function visualWidth(text: string): number {
  let column = 0;
  for (const character of text) {
    column += character === '\t' ? 4 - (column % 4) : 1;
  }
  return column;
}

function trimMarkdown(lines: string[]): string {
  let first = 0;
  let last = lines.length;
  while (first < last && !lines[first].trim()) first += 1;
  while (last > first && !lines[last - 1].trim()) last -= 1;
  return lines.slice(first, last).join('\n');
}
