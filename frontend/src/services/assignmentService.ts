import api from "./api";

export interface AssignmentListItem {
  id: number;
  name: string;
  grade: string;
  subject: string;
  semester: string;
  month: string;
  layout_type: string;
  status: string;
  total_score: number | null;
  question_count: number;
  error_count: number;
  created_at: string;
}

export interface AssignmentDetail {
  id: number;
  name: string;
  grade: string;
  subject: string;
  semester: string;
  month: string;
  layout_type: string;
  file_url: string;
  status: string;
  total_score: number | null;
  full_total: number | null;
  ai_summary: string | null;
  questions: QuestionItem[];
  created_at: string;
}

export interface QuestionItem {
  id: number;
  assignment_id: number;
  question_number: number;
  image_url: string;
  student_answer: string | null;
  correct_answer: string | null;
  score: number | null;
  full_score: number | null;
  analysis_detail: string | null;
  question_type: string | null;
  knowledge_points: Array<{ name: string; category?: string; mastery?: string }> | string[] | null;
  common_mistakes: string[] | null;
  confidence_score: number | null;
  status: string;
  page_index?: number;
  bbox_x?: number;
  bbox_y?: number;
  bbox_w?: number;
  bbox_h?: number;
  /** 大题套小题：父题ID（子题关联父题） */
  parent_id?: number | null;
  /** 大题套小题：子题在大题中的序号（0开始） */
  sub_question_index?: number | null;
  /** 大题套小题：子题列表（仅父题有此字段） */
  children?: QuestionItem[];
  /** 学生答案切割图片URL（上传答案并切割后生成） */
  answer_image_url?: string | null;
  /** 人工审核备注（重新生成时输入，持久化存储） */
  manual_review_note?: string | null;
}

export interface PageInfo {
  page_index: number;
  image_url: string;
  width: number;
  height: number;
}

export interface SourcePagesResponse {
  pages: PageInfo[];
  total_pages: number;
}

export interface ManualRegion {
  question_number: number;
  page_index: number;
  x: number;
  y: number;
  w: number;
  h: number;
  draw_order: number;
  rotation?: number;  // 图片旋转角度：0/90/180/270
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const assignmentService = {
  /** Pre-upload file only, returns file_path for later assignment creation */
  async uploadFile(
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<{ file_path: string; filename: string; size: number }> {
    const formData = new FormData();
    formData.append("file", file);
    const { data } = await api.post("/assignments/pre-upload", formData, {
      timeout: 120000,
      onUploadProgress: (e) => {
        if (e.total && onProgress) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
    });
    return data;
  },

  /** Create assignment with metadata + pre-uploaded file_path */
  async upload(
    params: {
      file_path: string;
      name: string;
      grade: string;
      subject: string;
      semester: string;
      month: string;
      layout_type?: string;
    },
  ): Promise<{ assignment_id: number; status: string }> {
    const formData = new FormData();
    formData.append("file_path", params.file_path);
    formData.append("name", params.name);
    formData.append("grade", params.grade);
    formData.append("subject", params.subject);
    formData.append("semester", params.semester);
    formData.append("month", params.month);
    formData.append("layout_type", params.layout_type || "a4_single");
    const { data } = await api.post("/assignments", formData, {
      timeout: 30000,
    });
    return data;
  },

  async list(params: {
    page?: number;
    page_size?: number;
    grade?: string;
    subject?: string;
    semester?: string;
  }): Promise<PaginatedResponse<AssignmentListItem>> {
    const { data } = await api.get("/assignments", { params });
    return data;
  },

  async getDetail(id: number): Promise<AssignmentDetail> {
    const { data } = await api.get(`/assignments/${id}`);
    return data;
  },

  async update(id: number, params: {
    name?: string;
    grade?: string;
    subject?: string;
    semester?: string;
    month?: string;
  }): Promise<void> {
    await api.put(`/assignments/${id}`, params);
  },

  async delete(id: number): Promise<void> {
    await api.delete(`/assignments/${id}`);
  },

  async analyze(id: number): Promise<{ assignment_id: number; status: string; message: string }> {
    const { data } = await api.post(`/assignments/${id}/analyze`);
    return data;
  },

  async cancelAnalysis(id: number): Promise<{ assignment_id: number; status: string; message: string }> {
    const { data } = await api.post(`/assignments/${id}/cancel`);
    return data;
  },

  async getSourcePages(id: number): Promise<SourcePagesResponse> {
    const { data } = await api.get(`/assignments/${id}/source-pages`);
    return data;
  },

  async manualSplit(id: number, regions: ManualRegion[]): Promise<{
    assignment_id: number;
    status: string;
    question_count: number;
    message: string;
  }> {
    const { data } = await api.post(`/assignments/${id}/manual-split`, { regions });
    return data;
  },

  /** 上传答案文件，获取页面图片供答案切割使用 */
  async uploadAnswerFile(
    id: number,
    file: File,
  ): Promise<{ pages: PageInfo[]; total_pages: number; answer_file_url: string }> {
    const formData = new FormData();
    formData.append("file", file);
    const { data } = await api.post(`/assignments/${id}/answer-pages`, formData, {
      timeout: 120000,
    });
    return data;
  },

  /** 重新汇总整卷分数和AI评语（不重新评分，仅重新计算总分和生成评语） */
  async reSummarize(id: number): Promise<{ message: string }> {
    const { data } = await api.post(`/assignments/${id}/re-summarize`);
    return data;
  },

  /** 提交答案切割区域，保存到各题目的 answer_image_url */
  async saveAnswerSplit(
    id: number,
    regions: Array<{
      question_number: number;
      page_index: number;
      x: number;
      y: number;
      w: number;
      h: number;
      rotation?: number;
    }>,
    answerFileUrl: string,
  ): Promise<{ assignment_id: number; updated_count: number; message: string }> {
    const { data } = await api.post(`/assignments/${id}/answer-split`, {
      regions,
      answer_file_url: answerFileUrl,
    });
    return data;
  },
};
