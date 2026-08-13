import api from "./api";
import type { PaginatedResponse } from "./assignmentService";
import type { ErrorQuestionItem } from "./errorQuestionService";
import type { AIQuestionItem } from "./aiQuestionService";

/** 收藏实体类型：error=错题，ai=AI 生成题（上传转录的自有试题也归为 ai，靠 source 区分） */
export type FavoriteItemType = "error" | "ai";

/** 题目来源（收藏页"题目来源"筛选）：error=错题，ai=AI 生成题，upload=自有试题（上传转录） */
export type FavoriteItemSource = "error" | "ai" | "upload";

/**
 * 收藏条目 envelope：item_type 区分数据表（泛型 K 为字面量，保证判别联合可收窄），
 * source 区分题目来源（error/ai/upload，upload 与 ai 同表），
 * question 为对应列表接口的完整题目结构（前端按 item_type 分流渲染卡片）。
 */
export interface FavoriteEntry<T, K extends FavoriteItemType> {
  item_type: K;
  /** 题目来源：error=错题，ai=AI 生成题，upload=自有试题（上传转录） */
  source: FavoriteItemSource;
  /** 收藏记录 id（列表 key 用，AI 大题的 question.id 可能为 null） */
  favorite_id: number;
  favorited_at: string;
  question: T;
}

/** 混排列表项：错题或 AI 题 */
export type FavoriteUnion =
  | FavoriteEntry<ErrorQuestionItem, "error">
  | FavoriteEntry<AIQuestionItem, "ai">;

/** 收藏列表筛选参数（source=题目来源：error/ai/upload） */
export interface FavoriteListParams {
  page?: number;
  page_size?: number;
  source?: string;
  grade?: string;
  subject?: string;
  semester?: string;
  question_type?: string;
}

/** 转录任务状态（轮询结果） */
export type UploadResultStatus = "pending" | "processing" | "completed" | "failed" | "not_found";

/** 转录任务轮询结果 */
export interface UploadResult {
  status: UploadResultStatus;
  entries?: FavoriteUnion[];
  error?: string;
}

export const favoriteService = {
  /** 收藏列表（错题 + AI 题混排，按收藏时间倒序） */
  async list(
    params: FavoriteListParams,
  ): Promise<PaginatedResponse<FavoriteUnion>> {
    const { data } = await api.get("/favorites", { params });
    return data;
  },

  /** 添加收藏（幂等：重复收藏不报错） */
  async add(
    itemType: FavoriteItemType,
    questionId: number,
  ): Promise<{ id: number; item_type: string; question_id: number }> {
    const { data } = await api.post("/favorites", { item_type: itemType, question_id: questionId });
    return data;
  },

  /** 取消收藏（幂等：未收藏时也返回成功） */
  async remove(
    itemType: FavoriteItemType,
    questionId: number,
  ): Promise<{ deleted: number }> {
    const { data } = await api.delete("/favorites", {
      params: { item_type: itemType, question_id: questionId },
    });
    return data;
  },

  /**
   * 上传试题并创建转录任务（202 立即返回 task_id，结果需轮询 getUploadResult）。
   * 表单字段（年级/科目/学期/题型）即收藏页筛选项，作为题目元数据写入。
   */
  async uploadQuestion(formData: FormData): Promise<{ task_id: string; status: string }> {
    const { data } = await api.post("/upload-questions", formData);
    return data;
  },

  /**
   * 查询转录任务结果：
   * - completed：entries 为收藏条目列表（与 /favorites 列表结构一致，可直接进编辑弹窗）
   * - failed：error 为可读错误信息
   * - not_found：任务过期或不存在
   */
  async getUploadResult(taskId: string): Promise<UploadResult> {
    const { data } = await api.get(`/upload-questions/${taskId}`);
    return data;
  },
};
