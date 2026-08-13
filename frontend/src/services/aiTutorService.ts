import api from "./api";
import { authedFetch } from "../utils/authedFetch";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export interface SSECallbacks {
  onReasoning?: (content: string) => void;
  onToolCall?: (name: string, args: unknown) => void;
  onToolResult?: (name: string, summary: string) => void;
  onToken?: (content: string) => void;
  onError?: (error: string) => void;
  onDone?: () => void;
}

// ============ 完整讲解 + 思考题相关 ============

/** 完整讲解请求参数 */
export interface ExplainRequest {
  exercise_content: string;
  subject?: string;
  explanation_style?: "分步引导式" | "直接讲解式" | "基础科普式";
  strict_level?: number;
  /**
   * 关联题目 ID：后端据此读取该题的切割原图（含题干）喂给视觉模型，
   * 让 AI 真正看到题目，避免"讲一道看不见的题"
   */
  question_id?: number;
}

/** 完整讲解结果：讲解文本 + 一道思考题 */
export interface ExplainResult {
  knowledge_points: string[];
  explanation: string;
  thinking_question: string;
}

/** 思考题判题结果 */
export interface ThinkingCheckResult {
  verdict: "correct" | "partial" | "wrong";
  feedback: string;
}

/** 讲解反馈请求 */
export interface FeedbackRequest {
  knowledge_point: string;
  feedback_level: "完全听懂" | "部分听懂" | "没听懂";
  question_id?: string;
  session_id?: string;
}

/**
 * 获取单题完整讲解（含思考题）
 * 调用后端 /ai-tutor/explain 直连接口（不经过 Agent，返回结构化 JSON）
 */
export async function explainExercise(
  params: ExplainRequest
): Promise<ExplainResult> {
  const { data } = await api.post("/ai-tutor/explain", {
    exercise_content: params.exercise_content,
    subject: params.subject || "未知",
    explanation_style: params.explanation_style || "直接讲解式",
    strict_level: params.strict_level ?? 3,
    // 题目 ID 可选：有则后端附带切割原图做多模态讲解（无公式科目开放）
    question_id: params.question_id ?? undefined,
  });
  return {
    knowledge_points: data.knowledge_points || [],
    explanation: data.explanation || "",
    thinking_question: data.thinking_question || "",
  };
}

/**
 * 提交思考题回答并判题
 * 后端 LLM 自行解题后对比判定，参考答案不经过前端
 */
export async function checkThinkingAnswer(params: {
  exercise_content: string;
  thinking_question: string;
  user_answer: string;
  subject?: string;
}): Promise<ThinkingCheckResult> {
  const { data } = await api.post("/ai-tutor/explain/check", {
    exercise_content: params.exercise_content,
    thinking_question: params.thinking_question,
    user_answer: params.user_answer,
    subject: params.subject || "未知",
  });
  return {
    verdict: data.verdict === "correct" || data.verdict === "wrong" ? data.verdict : "partial",
    feedback: data.feedback || "",
  };
}

/**
 * 记录讲解反馈（直接更新知识点掌握状态）
 *
 * 走后端专用接口 /ai-tutor/feedback，直接调用 KnowledgeTracker 落库，
 * 不经过 Agent 聊天链路——原实现把反馈伪装成一条聊天消息发给 /chat，
 * 会触发一轮完整 ReAct（耗时且反馈是否真正记录取决于 Agent 是否恰好调用工具）。
 * 失败时抛错，调用方需提示用户重试。
 */
export async function recordFeedback(params: FeedbackRequest): Promise<void> {
  await api.post("/ai-tutor/feedback", {
    knowledge_point: params.knowledge_point,
    feedback_level: params.feedback_level,
    question_id: params.question_id || null,
    session_id: params.session_id || null,
  });
}

/** SSE 流空闲超时（毫秒）：4 分钟无任何事件判定为挂死并中断 */
const STREAM_IDLE_TIMEOUT_MS = 4 * 60 * 1000;

export async function streamChat(
  message: string,
  history: Array<{ role: string; content: string }>,
  callbacks: SSECallbacks,
  context?: { grade?: string; subject?: string },
  signal?: AbortSignal,
  sessionId?: number,
): Promise<void> {
  const body: Record<string, unknown> = { message, history, context };
  // 如果传入了 session_id，附带到请求中，后端会自动保存消息到对应会话
  if (sessionId) {
    body.session_id = sessionId;
  }

  // 内部 AbortController：链接触入的 signal（停止按钮仍生效），
  // 额外用于空闲超时中断——reader.read() 本身没有超时，服务端挂起会无限等待
  const internal = new AbortController();
  if (signal) {
    if (signal.aborted) {
      internal.abort();
    } else {
      signal.addEventListener("abort", () => internal.abort(), { once: true });
    }
  }

  let response: Response;
  try {
    // 走 authedFetch：自动附加 Bearer token + 401 自动刷新重放，
    // 避免 access token 过期后聊天主流程直接 "HTTP 401" 失败
    response = await authedFetch(`${API_BASE}/ai-tutor/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: internal.signal,
    });
  } catch {
    // 用户主动停止/空闲超时中断：静默返回（用户已知情）
    if (internal.signal.aborted) return;
    // 网络层失败（后端不可达）：必须提示，否则 AI 气泡显示空回复，
    // 用户会以为是 AI 没说话
    callbacks.onError?.("网络连接失败，请检查网络后重试");
    return;
  }

  if (!response.ok) {
    callbacks.onError?.(`HTTP ${response.status}`);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";
  let timedOut = false;
  let lastEventAt = Date.now();

  // 空闲检测定时器：周期检查距上次收到事件的时间，超过阈值则中断请求
  const idleTimer = setInterval(() => {
    if (Date.now() - lastEventAt > STREAM_IDLE_TIMEOUT_MS) {
      timedOut = true;
      internal.abort();
    }
  }, 5000);

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          // 收到任何事件都重置空闲计时
          lastEventAt = Date.now();
          try {
            const event = JSON.parse(line.slice(6));
            switch (event.type) {
              case "reasoning":
                callbacks.onReasoning?.(event.content);
                break;
              case "tool_call":
                callbacks.onToolCall?.(event.name, event.args);
                break;
              case "tool_result":
                callbacks.onToolResult?.(event.name, event.summary);
                break;
              case "token":
                callbacks.onToken?.(event.content);
                break;
              case "error":
                callbacks.onError?.(event.content);
                break;
              case "done":
                callbacks.onDone?.();
                break;
            }
          } catch {
            // skip malformed events
          }
        }
      }
    }
  } catch {
    // 流被中断。三种情况：
    // - 空闲超时（timedOut=true）：下方统一提示
    // - 用户主动停止（signal.aborted）：静默返回
    // - 其他（连接中途断开）：明确提示，避免界面永远停在"执行中"转圈
    if (!timedOut && !internal.signal.aborted) {
      callbacks.onError?.("连接中断，回复可能不完整，请重试");
    }
  } finally {
    clearInterval(idleTimer);
  }

  // 空闲超时中断时给出明确提示，避免用户看到"无响应"却不知道为什么
  if (timedOut) {
    callbacks.onError?.("长时间未收到响应，已中断，请重试");
  }
}
