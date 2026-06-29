import api from "./api";

export interface SimilarQuestionItem {
  id: number;
  question_text: string;
  answer: string;
  knowledge_point: string;
  difficulty: string;
  question_type: string;
  options: Array<{ label: string; text: string }>;
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
    similar_questions?: SimilarQuestionItem[];  // backward compat
    error?: string;
  }> {
    const { data } = await api.get(`/questions/${questionId}/similar-result`);
    return data;
  },

  async generateSimilarSingle(questionId: number): Promise<SimilarQuestionItem> {
    const { data } = await api.post(`/questions/${questionId}/similar-single`, {});
    return data;
  },

  async delete(id: number): Promise<{ message: string; question_id: number }> {
    const { data } = await api.delete(`/questions/${id}`);
    return data;
  },

  async adjustRegion(id: number, region: { page_index: number; x: number; y: number; w: number; h: number; rotation?: number }): Promise<{
    question_id: number;
    image_url: string;
    bbox: { x: number; y: number; w: number; h: number };
    message: string;
  }> {
    const { data } = await api.put(`/questions/${id}/region`, region);
    return data;
  },
};
