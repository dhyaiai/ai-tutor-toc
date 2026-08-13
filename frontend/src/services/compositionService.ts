/**
 * 作文批改 API 服务
 *
 * 提供文件上传、批改提交、历史查询和详情查看功能。
 */

import api from "./api";

const BASE = "/compositions";

/** 批改结果 */
export interface CompositionResult {
  id: number;
  subject: string;
  title: string;
  total_score: number;
  full_score: number;
  content: string;
  grade?: string | null;
  dimension_scores?: Record<string, number> | null;
  revision_suggestions?: Array<{
    position: string;
    original_text: string;
    revised_text: string;
    reason: string;
    revision_type: string;
  }> | null;
  overall_comment?: string | null;
  polish_advice?: string | null;
  sample_essay?: string | null;
  strict_level: number;
  essay_type?: string | null;
  pdf_url?: string | null;
  /** 批改状态：pending(已提交) / correcting(批改中) / completed(完成) / failed(失败) */
  status?: string;
  error_message?: string | null;
  create_time?: string | null;
}

/** 批改记录列表项 */
export interface CompositionListItem {
  id: number;
  subject: string;
  title: string;
  total_score: number;
  full_score: number;
  strict_level: number;
  grade?: string | null;
  essay_type?: string | null;
  pdf_url?: string | null;
  /** 批改状态：pending(已提交) / correcting(批改中) / completed(完成) / failed(失败) */
  status?: string;
  error_message?: string | null;
  create_time?: string | null;
}

export const compositionService = {
  /** 上传作文文件，返回 file_path（预上传，暂未使用） */
  async uploadFile(
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<{ file_path: string; filename: string; size: number }> {
    const formData = new FormData();
    formData.append("file", file);
    const { data } = await api.post(`${BASE}/upload`, formData, {
      timeout: 120000,
      onUploadProgress: (e) => {
        if (e.total && onProgress) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
    });
    return data;
  },

  /** 上传作文文件并获取 AI 批改。支持单文件和多文件合并批改。 */
  async correct(
    files: File[],
    params: {
      subject: string;
      grade: string;
      title: string;
      essay_type?: string;
    },
    onProgress?: (percent: number) => void,
  ): Promise<CompositionResult> {
    const formData = new FormData();
    // 多文件时以同名 "files" 字段发送，后端按顺序合并
    if (files.length > 1) {
      files.forEach((f) => formData.append("files", f));
    } else if (files.length === 1) {
      formData.append("file", files[0]);
    }
    formData.append("subject", params.subject);
    formData.append("grade", params.grade);
    formData.append("title", params.title);
    if (params.essay_type) {
      formData.append("essay_type", params.essay_type);
    }
    const { data } = await api.post(`${BASE}/correct`, formData, {
      // 批改已异步化：接口只做"存文件+建记录"并立即返回，无需长超时
      timeout: 120000,
      onUploadProgress: (e) => {
        if (e.total && onProgress) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
    });
    return data;
  },

  /** 获取历史批改列表 */
  async list(params?: {
    subject?: string;
    grade?: string;
  }): Promise<{ items: CompositionListItem[]; total: number }> {
    const { data } = await api.get(BASE, { params: params || {} });
    return data;
  },

  /** 获取单篇批改详情 */
  async get(id: number): Promise<CompositionResult> {
    const { data } = await api.get(`${BASE}/${id}`);
    return data;
  },

  /** 删除批改记录 */
  async delete(id: number): Promise<void> {
    await api.delete(`${BASE}/${id}`);
  },

  /** 获取原始上传文件的访问URL */
  async getFileUrl(id: number): Promise<{ url: string; filename: string }> {
    const { data } = await api.get(`${BASE}/${id}/file-url`);
    return data;
  },

  /** 获取作文原文的页面图片列表（所有格式统一转为图片） */
  async getPageImages(id: number): Promise<{ pages: string[]; total: number }> {
    const { data } = await api.get(`${BASE}/${id}/page-images`);
    return data;
  },

  /** 重新批改已存在的作文记录（异步：立即返回，批改在后台执行） */
  async reCorrect(id: number): Promise<CompositionResult> {
    const { data } = await api.post(`${BASE}/${id}/re-correct`, null, {
      timeout: 120000,
    });
    return data;
  },
};
