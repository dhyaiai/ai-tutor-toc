/**
 * 口语测评 API 服务
 *
 * 3 个子模块：
 * - 英语听力 (listening)
 * - 单词听写 (dictation)
 * - 普通话测评 (mandarin)
 */

import api from "./api";

const BASE = "/oral";

/** 听力题目（短对话每题自带 dialogue，长对话/短文共用顶层 transcript） */
export interface ListeningQuestion {
  question: string;
  options?: Record<string, string>;
  answer?: string;
  correct_answer?: string;
  dialogue?: string;  // 短对话题型每题独立对话文本（含 M:/W: 标签）
}

/** 听力试卷：AI 先生成整段原文 transcript，再提取题目 */
export interface ListeningTest {
  transcript: string;
  questions: ListeningQuestion[];
  question_type?: string;
  difficulty?: string;
}

/** 听力逐题批改明细 */
export interface ListeningDetail {
  question_id: number;
  question: string;
  options: Record<string, string>;
  user_answer: string;
  correct_answer: string;
  is_correct: boolean;
  score: number;       // 该题得分（答对 1.5，答错 0）
  dialogue?: string;   // 短对话题型每题独立对话
}

/** 听力批改结果 */
export interface ListeningResult {
  total: number;
  total_score: number;   // 实际得分
  full_score: number;    // 满分
  correct_rate: number;
  grade: string;
  details: ListeningDetail[];
}

/** 听写单词（后端返回 english/chinese/pos，中英混合时每词携带 prompt_lang） */
export interface DictationWord {
  english?: string;
  chinese?: string;
  word?: string;   // 前端兼容字段，映射自 english
  meaning?: string; // 前端兼容字段，映射自 chinese
  pos?: string;
  prompt_lang?: string;  // 该词测试方向：汉译英 / 英译汉
}

/** 听写任务（AI 生成老师口语化播报文本 broadcast_text + 答案词表 words） */
export interface DictationTask {
  words: DictationWord[];
  broadcast_text: string;   // 老师口语化播报文本（中英夹杂），供整段播放
  direction: string;        // 汉译英 / 英译汉 / 默写单词 / 中英混合
}

/** 听写批改结果 */
export interface DictationResult {
  correct_count: number;
  total: number;
  wrong_count?: number;
  wrong_words: Array<{
    word: string;
    chinese?: string;
    user_spelling: string;
    correct_spelling: string;
    prompt_lang?: string;
  }>;
  error?: string;
}

/** 普通话测评结果 */
export interface MandarinResult {
  total_score: number;
  dimension_scores: Record<string, number>;
  suggestions: string[];
  level: string;
  /** 音频转写文本（音频评测模式） */
  transcribed_text?: string;
  /** AI 综合评语 */
  ai_comment?: string;
  /** 错误信息 */
  error?: string;
  record?: OralRecord;
}

/** 普通话朗读文本生成参数 */
export interface GenerateMandarinTextParams {
  topic?: string;
  difficulty?: string;
  length?: string;  // 短/中/长
}

/** 普通话朗读文本生成结果 */
export interface MandarinGeneratedText {
  text: string;
  topic: string;
  difficulty: string;
  pinyin_notes?: Array<{ char: string; pinyin: string; note: string }>;
  error?: string;
}

/** 普通话评测模式 */
export type MandarinMode = "ai_generated" | "free_speech";

/** 口语测评作业记录 */
export interface OralRecord {
  id: number;
  category: string;
  name: string;
  score: string | null;
  grade_level?: string | null;  // 学段：小学/初中/高中
  question_type?: string;       // 题型：短对话/长对话/短文理解
  word_scope?: string;          // 词库范围（单词听写）
  direction?: string;           // 测试方向：汉译英/英译汉/默写单词/中英混合（单词听写）
  difficulty?: string;          // 难度：简单/中等/困难（单词听写）
  created_at: string;
}

/** 单词听写逐题作答明细 */
export interface DictationDetailItem {
  index: number;
  prompt_lang: string;       // 该题方向：汉译英 / 英译汉
  english?: string;
  chinese?: string;
  question: string;          // 老师报读的提示（汉译英为中文，英译汉为英文）
  correct_answer: string;    // 正确答案
  user_answer: string;       // 学生作答
  is_correct: boolean;
}

/** 作业记录详情（含听力原文/逐题作答与对错） */
export interface OralRecordDetail extends OralRecord {
  detail: {
    transcript?: string;
    question_type?: string;
    difficulty?: string;
    grade_level?: string;     // 学段
    correct_rate?: number;
    grade?: string;            // 评级：优秀/良好/及格/待提高
    total_score?: number;      // 实际得分
    full_score?: number;       // 满分
    details?: ListeningDetail[] | DictationDetailItem[];
    // 单词听写专属字段
    broadcast_text?: string;   // 听写播报原文
    word_scope?: string;       // 词库范围
    direction?: string;        // 测试方向
    answer_mode?: string;      // 作答方式：keyboard / upload
    wrong_words?: DictationResult["wrong_words"];
    [key: string]: unknown;
  };
}

export const oralService = {
  /** 生成英语听力试卷 */
  async generateListening(params: {
    question_type?: string;
    difficulty?: string;
    question_count?: number;
    grade?: string;
    grade_level?: string;
  }): Promise<ListeningTest> {
    const { data } = await api.post(`${BASE}/listening/generate`, params);
    return data;
  },

  /** 提交听力答案（同时上传听力原文以便保存） */
  async submitListening(
    questions: ListeningQuestion[],
    answers: string[],
    extra?: { transcript?: string; question_type?: string; difficulty?: string; grade_level?: string },
  ): Promise<ListeningResult> {
    const { data } = await api.post(`${BASE}/listening/submit`, {
      questions,
      answers,
      transcript: extra?.transcript ?? "",
      question_type: extra?.question_type ?? "短对话",
      difficulty: extra?.difficulty ?? "中等",
      grade_level: extra?.grade_level ?? "",
    });
    return data;
  },

  /** 生成听写任务（AI 生成老师口语化播报文本 + 答案词表） */
  async generateDictation(params: {
    word_scope: string;
    word_count?: number;
    direction?: string;
    difficulty?: string;
  }): Promise<DictationTask> {
    const { data } = await api.post(`${BASE}/dictation/generate`, params);
    return data;
  },

  /** 提交听写结果（键盘输入，按行逐词匹配） */
  async submitDictation(
    words: DictationWord[],
    user_spellings: string[],
    extra?: { direction?: string; broadcast_text?: string; word_scope?: string; difficulty?: string },
  ): Promise<DictationResult> {
    const { data } = await api.post(`${BASE}/dictation/submit`, {
      words,
      user_spellings,
      direction: extra?.direction ?? "汉译英",
      difficulty: extra?.difficulty ?? "中等",
      broadcast_text: extra?.broadcast_text ?? "",
      word_scope: extra?.word_scope ?? "",
    });
    return data;
  },

  /** 提交听写结果（上传图片，多模态 AI 识别后批改） */
  async submitDictationImage(
    words: DictationWord[],
    file: File,
    extra?: { direction?: string; broadcast_text?: string; word_scope?: string; difficulty?: string },
  ): Promise<DictationResult> {
    const formData = new FormData();
    formData.append("words", JSON.stringify(words));
    formData.append("direction", extra?.direction ?? "汉译英");
    formData.append("difficulty", extra?.difficulty ?? "中等");
    formData.append("broadcast_text", extra?.broadcast_text ?? "");
    formData.append("word_scope", extra?.word_scope ?? "");
    formData.append("image", file);
    const { data } = await api.post(`${BASE}/dictation/submit-image`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 180000,  // 多模态图片识别可能需要更长时间
    });
    return data;
  },

  /** 生成普通话朗读文本 */
  async generateMandarinText(params: GenerateMandarinTextParams): Promise<MandarinGeneratedText> {
    const { data } = await api.post(`${BASE}/mandarin/generate-text`, params);
    return data;
  },

  /** 普通话水平测评（音频模式，上传录音文件） */
  async evaluateMandarinAudio(formData: FormData): Promise<MandarinResult> {
    const { data } = await api.post(`${BASE}/mandarin/evaluate`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 180000,  // 音频评测可能需要更长时间
    });
    return data;
  },

  /** 普通话水平测评（纯文本模式，兼容旧版） */
  async evaluateMandarinText(params: {
    test_level?: string;
    test_part?: string;
    text_content: string;
    strict_level?: number;
    evaluation_mode?: string;
  }): Promise<MandarinResult> {
    const { data } = await api.post(`${BASE}/mandarin/evaluate-json`, params);
    return data;
  },

  /** 查询口语测评作业记录（可按类别、学段、题型、词库范围、测试方向、难度过滤） */
  async listRecords(params?: {
    category?: string;
    grade_level?: string;
    question_type?: string;
    word_scope?: string;
    direction?: string;
    difficulty?: string;
  }): Promise<OralRecord[]> {
    const { data } = await api.get(`${BASE}/records`, { params });
    return data;
  },

  /** 查看单条作业记录详情 */
  async getRecordDetail(recordId: number): Promise<OralRecordDetail> {
    const { data } = await api.get(`${BASE}/records/${recordId}`);
    return data;
  },

  /** 删除一条作业记录 */
  async deleteRecord(recordId: number): Promise<void> {
    await api.delete(`${BASE}/records/${recordId}`);
  },
};
