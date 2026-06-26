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
};
