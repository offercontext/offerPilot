export type StudioPhase =
  | 'question_ready'
  | 'answering'
  | 'transcript_review'
  | 'answer_submitting'
  | 'answer_confirmed'
  | 'next_question_generating'
  | 'completed'
  | 'contract_failed'
  | 'result_unknown';

export type StudioOperation = 'start' | 'answer' | 'question' | 'feedback';

export interface StudioState {
  phase: StudioPhase;
  answerMode: 'text' | 'voice';
  turnNo: number;
  maxTurns: number;
  question: string;
  answer: string;
  turnKey: string | null;
  questionKey: string | null;
  feedbackKey: string | null;
  pendingOperation: StudioOperation | null;
  resultUnknown: boolean;
  error: string | null;
}

export type StudioAction =
  | { type: 'answer_mode'; mode: 'text' | 'voice' }
  | { type: 'draft_changed'; answer: string }
  | { type: 'transcript_ready'; answer: string }
  | { type: 'transcript_confirmed' }
  | { type: 'answer_submitting'; turnKey: string }
  | { type: 'answer_succeeded' }
  | { type: 'question_submitting'; questionKey: string }
  | { type: 'question_succeeded'; turnNo: number; question: string }
  | { type: 'feedback_submitting'; feedbackKey: string }
  | { type: 'contract_failed'; message: string }
  | { type: 'result_unknown'; operation: StudioOperation; message: string }
  | {
    type: 'edit_input';
    operation: StudioOperation;
    preserveIdempotencyKey: boolean;
    message: string;
  }
  | { type: 'error'; message: string };

export function createStudioState(input: { turnNo: number; question: string; maxTurns?: number }): StudioState {
  return {
    phase: 'question_ready',
    answerMode: 'text',
    turnNo: input.turnNo,
    maxTurns: input.maxTurns ?? 5,
    question: input.question,
    answer: '',
    turnKey: null,
    questionKey: null,
    feedbackKey: null,
    pendingOperation: null,
    resultUnknown: false,
    error: null,
  };
}

export function reduceStudioState(state: StudioState, action: StudioAction): StudioState {
  switch (action.type) {
    case 'answer_mode':
      return { ...state, answerMode: action.mode };
    case 'draft_changed':
      return { ...state, answer: action.answer, phase: 'answering', error: null };
    case 'transcript_ready':
      return { ...state, answer: action.answer, phase: 'transcript_review', error: null };
    case 'transcript_confirmed':
      return { ...state, phase: 'answering', error: null };
    case 'answer_submitting':
      return { ...state, turnKey: action.turnKey, phase: 'answer_submitting', pendingOperation: 'answer', resultUnknown: false, error: null };
    case 'answer_succeeded':
      return shouldGenerateNextQuestion({ ...state, phase: 'answer_confirmed' })
        ? { ...state, phase: 'next_question_generating', pendingOperation: 'question', error: null }
        : { ...state, phase: 'completed', pendingOperation: null, error: null };
    case 'question_submitting':
      return { ...state, questionKey: action.questionKey, phase: 'next_question_generating', pendingOperation: 'question', error: null };
    case 'question_succeeded':
      return { ...state, turnNo: action.turnNo, question: action.question, answer: '', turnKey: null, questionKey: null, phase: 'question_ready', pendingOperation: null, error: null };
    case 'feedback_submitting':
      return { ...state, feedbackKey: action.feedbackKey, pendingOperation: 'feedback', error: null };
    case 'contract_failed':
      return { ...state, phase: 'contract_failed', pendingOperation: null, resultUnknown: false, error: action.message };
    case 'result_unknown':
      return { ...state, phase: 'result_unknown', pendingOperation: action.operation, resultUnknown: true, error: action.message };
    case 'edit_input': {
      const clearedKey = action.preserveIdempotencyKey
        ? {}
        : action.operation === 'answer'
          ? { turnKey: null }
          : action.operation === 'question'
            ? { questionKey: null }
            : action.operation === 'feedback'
              ? { feedbackKey: null }
              : {};
      return {
        ...state,
        ...clearedKey,
        phase: 'question_ready',
        pendingOperation: null,
        resultUnknown: false,
        error: action.message,
      };
    }
    case 'error':
      return { ...state, error: action.message };
    default:
      return state;
  }
}

export function shouldGenerateNextQuestion(state: StudioState): boolean {
  return state.phase === 'answer_confirmed' && state.turnNo < state.maxTurns && !state.resultUnknown;
}
