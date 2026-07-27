import api from "./api";

export interface SimilarQuestionItem {
  id: number;
  question_text: string;
  answer: string;
  analysis?: string;
  knowledge_point: string;
  difficulty: string;
  question_type: string;
  options: Array<{ label: string; text: string }>;
  full_score?: number;
}

/** 类似大题子题 */
export interface SimilarBigSubQuestion {
  question_text: string;
  answer: string;
  analysis?: string;
  knowledge_point: string;
  difficulty: string;
  question_type: string;
  options: Array<{ label: string; text: string }>;
  full_score: number;
}

/** 类似大题（含多个子题） */
export interface SimilarBigQuestion {
  is_big_question: true;
  question_context: string;
  sub_questions: SimilarBigSubQuestion[];
}

export const questionService = {
  async reanalyze(id: number, remark?: string): Promise<{ task_id: number | null; status: string }> {
    const { data } = await api.post(`/questions/${id}/reanalyze`, { remark: remark || null });
    return data;
  },

  async generateSimilar(id: number): Promise<{ task_id: number; status: string }> {
    const { data } = await api.post(`/questions/${id}/similar`, {});
    return data;
  },

  async getSimilarResult(questionId: number): Promise<{
    status: string;
    result?: SimilarQuestionItem[];
    similar_questions?: SimilarQuestionItem[] | SimilarBigQuestion | null;
    error?: string;
    is_big_question?: boolean;
  }> {
    const { data } = await api.get(`/questions/${questionId}/similar-result`);
    return data;
  },

  async generateSimilarSingle(questionId: number, difficulty?: string): Promise<SimilarQuestionItem | SimilarBigQuestion> {
    const { data } = await api.post(`/questions/${questionId}/similar-single`, { difficulty: difficulty || "medium" });
    return data;
  },

  async delete(id: number): Promise<{ message: string; question_id: number }> {
    const { data } = await api.delete(`/questions/${id}`);
    return data;
  },

  async adjustRegion(id: number, region: {
    page_index: number; x: number; y: number; w: number; h: number; rotation?: number;
    /** 同题额外区域（双栏/跨页），与主区域垂直拼接 */
    extra_regions?: Array<{ page_index: number; x: number; y: number; w: number; h: number; rotation?: number }>;
  }): Promise<{
    question_id: number;
    image_url: string;
    bbox: { x: number; y: number; w: number; h: number };
    message: string;
  }> {
    const { data } = await api.put(`/questions/${id}/region`, region);
    return data;
  },
};
