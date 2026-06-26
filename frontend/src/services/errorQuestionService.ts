import api from "./api";
import type { PaginatedResponse } from "./assignmentService";

export interface ErrorQuestionItem {
  id: number;
  assignment_id: number;
  assignment_name: string;
  question_number: number;
  question_type: string | null;
  image_url: string;
  student_answer: string | null;
  correct_answer: string | null;
  score: number | null;
  full_score: number | null;
  score_rate: number;
  knowledge_points: Array<{ name: string }> | string[] | null;
  common_mistakes: string[] | null;
  analysis_detail: string | null;
  created_at: string;
}

export const errorQuestionService = {
  async list(params: {
    page?: number;
    page_size?: number;
    grade?: string;
    subject?: string;
    semester?: string;
    question_type?: string;
    score_rate_min?: number;
    score_rate_max?: number;
    search?: string;
  }): Promise<PaginatedResponse<ErrorQuestionItem>> {
    const { data } = await api.get("/error-questions", { params });
    return data;
  },
};
