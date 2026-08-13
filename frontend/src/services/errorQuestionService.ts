import api from "./api";
import type { PaginatedResponse } from "./assignmentService";

/** 子题数据（大题的子题目） */
export interface SubQuestionItem {
  id: number;
  sub_question_index: number;
  question_type: string | null;
  /** 识别出的题干文本（含 $...$ 包裹的 LaTeX 公式，KaTeX 渲染） */
  question_text?: string | null;
  student_answer: string | null;
  correct_answer: string | null;
  score: number | null;
  full_score: number | null;
  score_rate: number;
  knowledge_points: Array<{ name: string }> | string[] | null;
  common_mistakes: string[] | null;
  analysis_detail: string | null;
}

export interface ErrorQuestionItem {
  id: number;
  assignment_id: number;
  assignment_name: string;
  /** 年级（来自所属作业，收藏页展示用） */
  grade?: string;
  /** 科目（来自所属作业，收藏页展示用） */
  subject?: string;
  question_number: number;
  question_type: string | null;
  image_url: string;
  /** 识别出的题干文本（含 $...$ 包裹的 LaTeX 公式，KaTeX 渲染） */
  question_text?: string | null;
  student_answer: string | null;
  correct_answer: string | null;
  score: number | null;
  full_score: number | null;
  score_rate: number;
  knowledge_points: Array<{ name: string }> | string[] | null;
  common_mistakes: string[] | null;
  analysis_detail: string | null;
  created_at: string;
  /** 是否为大题（含多个小题） */
  is_big_question: boolean;
  /** 大题下的子题列表 */
  children?: SubQuestionItem[];
  /** 大题中错题小题数 */
  error_count?: number;
  /** 大题下总小题数 */
  total_count?: number;
  /** 是否已收藏（收藏按钮初始状态回显） */
  is_favorited?: boolean;
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
