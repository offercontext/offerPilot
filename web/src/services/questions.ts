import type {
  GenerateQuestionsInput,
  GenerateQuestionsResult,
  PracticeStats,
  Question,
  QuestionFilter,
  QuestionInput,
  ReviewRating,
} from '@/types/question';
import { createApiClient } from './http';

// Generation can invoke the AI provider, which may be slow — use a longer timeout.
const http = createApiClient({ baseURL: '/api', timeout: 10000 });
const aiHttp = createApiClient({ baseURL: '/api', timeout: 120000 });

export async function listQuestions(filter: QuestionFilter = {}): Promise<Question[]> {
  const { data } = await http.get<Question[]>('/questions', {
    params: {
      ...(filter.topic ? { topic: filter.topic } : {}),
      ...(filter.category ? { category: filter.category } : {}),
      ...(filter.difficulty ? { difficulty: filter.difficulty } : {}),
      ...(filter.status ? { status: filter.status } : {}),
    },
  });
  return data ?? [];
}

export async function createQuestion(input: QuestionInput): Promise<Question> {
  const { data } = await http.post<Question>('/questions', input);
  return data;
}

export async function updateQuestion(id: number, input: QuestionInput): Promise<Question> {
  const { data } = await http.put<Question>(`/questions/${id}`, input);
  return data;
}

export async function deleteQuestion(id: number): Promise<void> {
  await http.delete(`/questions/${id}`);
}

export async function generateQuestions(input: GenerateQuestionsInput): Promise<GenerateQuestionsResult> {
  const { data } = await aiHttp.post<GenerateQuestionsResult>('/questions/generate', input);
  return data;
}

export async function listDueQuestions(limit = 20): Promise<Question[]> {
  const { data } = await http.get<Question[]>('/questions/due', { params: { limit } });
  return data ?? [];
}

export async function getPracticeStats(): Promise<PracticeStats> {
  const { data } = await http.get<PracticeStats>('/questions/stats');
  return data;
}

export async function submitReview(
  questionId: number,
  rating: ReviewRating,
  note = '',
): Promise<Question> {
  const { data } = await http.post<{ question: Question }>(`/questions/${questionId}/reviews`, {
    rating,
    note,
  });
  return data.question;
}
