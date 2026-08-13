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
  /** 题目配图（纯 SVG 代码，无图时为空） */
  image_svg?: string;
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
  /** 题目配图（纯 SVG 代码，无图时为空） */
  image_svg?: string;
}

/** 类似大题（含多个子题） */
export interface SimilarBigQuestion {
  is_big_question: true;
  question_context: string;
  context_image_svg?: string;
  sub_questions: SimilarBigSubQuestion[];
}

/** 换一题（single replace）任务状态，轮询 similar-result 时消费 */
export interface SimilarReplaceInfo {
  status: "pending" | "processing" | "completed" | "failed";
  /** 普通题：要替换的卡片下标；大题整体替换时为 -1 */
  index: number;
  difficulty: string;
  /** completed 时的新题（普通题：SimilarQuestionItem；大题：SimilarBigQuestion） */
  question?: SimilarQuestionItem | SimilarBigQuestion | null;
  error?: string | null;
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
    replace?: SimilarReplaceInfo | null;
  }> {
    const { data } = await api.get(`/questions/${questionId}/similar-result`);
    return data;
  },

  /** 创建换一题任务（异步，202 立即返回；结果需轮询 getSimilarResult 的 replace 字段） */
  async generateSimilarSingle(questionId: number, difficulty?: string, index?: number): Promise<{ status: string; message: string }> {
    const { data } = await api.post(`/questions/${questionId}/similar-single`, {
      difficulty: difficulty || "medium",
      index: index ?? -1,
    });
    return data;
  },

  /** 更新错题内容（题干/答案/解析；大题支持 children 批量更新子题）。
   * 全量发送（覆盖语义），只允许内容字段，不触碰成绩/状态/图片区域。 */
  async updateContent(
    id: number,
    payload: {
      question_text?: string;
      correct_answer?: string;
      analysis_detail?: string;
      children?: Array<{
        id: number;
        question_text?: string;
        correct_answer?: string;
        analysis_detail?: string;
      }>;
    },
  ): Promise<{ updated: number[]; message: string }> {
    const { data } = await api.put(`/questions/${id}/content`, payload);
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

  async insertBelow(id: number, region: {
    page_index: number; x: number; y: number; w: number; h: number; rotation?: number;
    /** 新题额外区域（双栏/跨页），与主区域垂直拼接 */
    extra_regions?: Array<{ page_index: number; x: number; y: number; w: number; h: number; rotation?: number }>;
  }): Promise<{
    question_id: number;
    question_number: number;
    message: string;
  }> {
    const { data } = await api.post(`/questions/${id}/insert-below`, region);
    return data;
  },
};
