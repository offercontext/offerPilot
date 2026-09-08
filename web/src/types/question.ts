export type QuestionDifficulty = 'easy' | 'medium' | 'hard';
export type QuestionStatus = 'new' | 'practicing' | 'mastered';
export type QuestionSource = 'ai_knowledge' | 'ai_notes' | 'manual';

export interface Question {
  id: number;
  application_id?: number | null;
  topic?: string;
  category: string;
  difficulty: QuestionDifficulty;
  question: string;
  reference_answer: string;
  tags: string[];
  source_type: QuestionSource;
  status: QuestionStatus;
  practice_count: number;
  last_practiced_at?: string | null;
  next_review_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface QuestionInput {
  category?: string;
  difficulty?: QuestionDifficulty;
  question: string;
  reference_answer?: string;
  tags?: string[];
  status?: QuestionStatus;
}

export interface QuestionFilter {
  topic?: string;
  category?: string;
  difficulty?: QuestionDifficulty;
  status?: QuestionStatus;
}

export interface GenerateQuestionsInput {
  source: 'knowledge' | 'notes';
  topic?: string;
  application_id?: number;
  count?: number;
}

export interface GenerateQuestionsResult {
  count: number;
  skipped: number;
  questions: Question[];
}

/** Self-assessment ratings used during a practice check-in. */
export type ReviewRating = 1 | 2 | 3; // 1 不会 | 2 模糊 | 3 掌握

export interface PracticeStats {
  total: number;
  new: number;
  practicing: number;
  mastered: number;
  due: number;
  today_reviews: number;
  streak_days: number;
}
