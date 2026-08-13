import api from "./api";

/** 用户作答记录 */
export interface AIUserAnswer {
  id: number;
  is_correct: boolean | null;
  score: number | null;
  full_score: number | null;
  ai_feedback: string | null;
  selected_options?: string[];
  answer_text?: string;
  answer_image_url?: string;
  answered_at: string | null;
}

/** 大题中的子题 */
export interface AISubQuestionItem {
  id: number;
  sub_question_index: number;
  question_text: string;
  answer: string;
  analysis?: string | null;
  question_type: string | null;
  knowledge_point: string | null;
  difficulty: string | null;
  options: Array<{ label: string; text: string }> | null;
  image_svg?: string | null;
  /** 上传转录的自有试题原图 URL（预签名后可直接访问）；AI 生成为空 */
  image_url?: string | null;
  user_answers?: AIUserAnswer[];
  created_at: string | null;
}

export interface AIQuestionItem {
  // 独立题字段（大题时 id 为 null）
  id: number | null;
  source_question_id: number | null;
  /** 年级（上传题用自有值，老题回落原题所属作业；收藏页展示用） */
  grade?: string;
  /** 科目（上传题用自有值，老题回落原题所属作业；收藏页展示用） */
  subject?: string;
  question_text?: string;
  answer?: string;
  analysis?: string | null;
  question_type?: string | null;
  knowledge_point?: string | null;
  difficulty?: string | null;
  options?: Array<{ label: string; text: string }> | null;
  image_svg?: string | null;
  /** 上传转录的自有试题原图 URL（预签名后可直接访问）；AI 生成为空 */
  image_url?: string | null;
  user_answers?: AIUserAnswer[];
  created_at?: string | null;

  // 大题字段
  is_big_question?: boolean;
  group_id?: string;
  question_context?: string;
  context_image_svg?: string | null;
  children?: AISubQuestionItem[];
  total_count?: number;
  score_rate?: number;
  /** 是否已收藏（收藏按钮初始状态回显；大题以组内第一子题锚点判断） */
  is_favorited?: boolean;
}

export interface SubmitAnswerParams {
  selected_options?: string[];
  answer_text?: string;
  answer_image?: File;
}

import type { SimilarQuestionItem, SimilarBigQuestion, SimilarBigSubQuestion, SimilarReplaceInfo } from "./questionService";

export const aiQuestionService = {
  async save(sourceQuestionId: number, question: SimilarQuestionItem): Promise<{ id: number }> {
    const { data } = await api.post("/ai-questions", {
      source_question_id: sourceQuestionId,
      question_text: question.question_text,
      answer: question.answer,
      analysis: question.analysis,
      question_type: question.question_type,
      knowledge_point: question.knowledge_point,
      difficulty: question.difficulty,
      options: question.options,
      image_svg: question.image_svg || null,
    });
    return data;
  },

  /** 保存AI生成的完整大题（含所有子题）到繁星驱动 */
  async saveBigQuestion(params: {
    source_question_id: number;
    question_context: string;
    context_image_svg?: string;
    difficulty: string;
    sub_questions: Array<SimilarBigSubQuestion & { existing_question_id?: number | null }>;
  }): Promise<{ ids: number[]; count: number; message: string }> {
    const { data } = await api.post("/ai-questions/big-question", {
      source_question_id: params.source_question_id,
      question_context: params.question_context,
      question_context_image_svg: params.context_image_svg || null,
      difficulty: params.difficulty,
      sub_questions: params.sub_questions.map((sq) => ({
        question_text: sq.question_text,
        answer: sq.answer,
        analysis: sq.analysis,
        question_type: sq.question_type,
        knowledge_point: sq.knowledge_point,
        options: sq.options || [],
        full_score: sq.full_score,
        image_svg: sq.image_svg || null,
        existing_question_id: sq.existing_question_id ?? null,
      })),
    });
    return data;
  },

  async list(params: {
    page?: number;
    page_size?: number;
    grade?: string;
    subject?: string;
    semester?: string;
    question_type?: string;
    difficulty?: string;
    score_rate_min?: number;
    score_rate_max?: number;
  }): Promise<{ items: AIQuestionItem[]; total: number }> {
    const { data } = await api.get("/ai-questions", { params });
    return data;
  },

  async get(id: number): Promise<AIQuestionItem> {
    const { data } = await api.get(`/ai-questions/${id}`);
    return data;
  },

  async submitWithQuestion(params: {
    source_question_id: number;
    question_text: string;
    answer: string;
    analysis?: string;
    question_type: string;
    knowledge_point: string;
    difficulty: string;
    options: Array<{ label: string; text: string }>;
    image_svg?: string;
    selected_options?: string[];
    answer_text?: string;
    answer_image?: File;
  }): Promise<{
    question_id?: number;
    is_correct: boolean;
    score: number;
    full_score: number;
    feedback: string;
    correct_answer: string;
  }> {
    const formData = new FormData();
    formData.append("source_question_id", String(params.source_question_id));
    formData.append("question_text", params.question_text);
    formData.append("answer", params.answer);
    if (params.analysis) {
      formData.append("analysis", params.analysis);
    }
    formData.append("question_type", params.question_type);
    formData.append("knowledge_point", params.knowledge_point);
    formData.append("difficulty", params.difficulty);
    formData.append("options_json", JSON.stringify(params.options));
    if (params.image_svg) {
      formData.append("image_svg", params.image_svg);
    }
    if (params.selected_options) {
      formData.append("selected_options", JSON.stringify(params.selected_options));
    }
    if (params.answer_text) {
      formData.append("answer_text", params.answer_text);
    }
    if (params.answer_image) {
      formData.append("answer_image", params.answer_image);
    }
    const { data } = await api.post("/ai-questions/submit", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },

  async submitAnswer(id: number, params: SubmitAnswerParams): Promise<{
    is_correct: boolean;
    score: number;
    full_score: number;
    feedback: string;
    correct_answer: string;
  }> {
    const formData = new FormData();
    if (params.selected_options) {
      formData.append("selected_options", JSON.stringify(params.selected_options));
    }
    if (params.answer_text) {
      formData.append("answer_text", params.answer_text);
    }
    if (params.answer_image) {
      formData.append("answer_image", params.answer_image);
    }
    const { data } = await api.post(`/ai-questions/${id}/submit-answer`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },

  // 同类题生成（基于AI题目）
  async generateSimilar(id: number): Promise<{ task_id: number; status: string }> {
    const { data } = await api.post(`/ai-questions/${id}/similar`, {});
    return data;
  },

  async getSimilarResult(id: number): Promise<{
    status: string;
    similar_questions?: SimilarQuestionItem[];
    error?: string;
    replace?: SimilarReplaceInfo | null;
  }> {
    const { data } = await api.get(`/ai-questions/${id}/similar-result`);
    return data;
  },

  /** 创建换一题任务（异步，202 立即返回；结果需轮询 getSimilarResult 的 replace 字段） */
  async generateSimilarSingle(id: number, difficulty?: string, index?: number): Promise<{ status: string; message: string }> {
    const { data } = await api.post(`/ai-questions/${id}/similar-single`, {
      difficulty: difficulty || "medium",
      index: index ?? -1,
    });
    return data;
  },

  /** 更新 AI 题内容（题干/答案/解析/选项；大题按 id 更新子题并支持 question_context 背景材料）。
   * URL 传收藏锚点 id：独立题即自身 id，大题即组内第一子题 id。
   * 全量发送（覆盖语义），只允许内容字段，不触碰难度/配图/作答记录。 */
  async updateContent(
    id: number,
    payload: {
      question_text?: string;
      answer?: string;
      analysis?: string;
      options?: Array<{ label: string; text: string }>;
      question_context?: string;
      children?: Array<{
        id: number;
        question_text?: string;
        answer?: string;
        analysis?: string;
        options?: Array<{ label: string; text: string }>;
      }>;
    },
  ): Promise<{ updated: number[]; message: string }> {
    const { data } = await api.put(`/ai-questions/${id}/content`, payload);
    return data;
  },
};
