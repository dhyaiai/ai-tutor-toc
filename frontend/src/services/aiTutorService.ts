const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

/** 获取带认证头的请求配置 */
function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export interface SSECallbacks {
  onReasoning?: (content: string) => void;
  onToolCall?: (name: string, args: unknown) => void;
  onToolResult?: (name: string, summary: string) => void;
  onToken?: (content: string) => void;
  onError?: (error: string) => void;
  onDone?: () => void;
}

// ============ 分步讲解相关 ============

/** 分步讲解请求参数 */
export interface ExplainRequest {
  exercise_content: string;
  subject?: string;
  explanation_style?: "分步引导式" | "直接讲解式" | "基础科普式";
  card_mode?: boolean;
  strict_level?: number;
}

/** 分步讲解步骤 */
export interface ExplainStep {
  step_number: number;
  title: string;
  content: string;
  key_point: string;
  follow_up_question: string;
}

/** 分步讲解结果 */
export interface ExplainResult {
  knowledge_points: string[];
  total_steps: number;
  steps: ExplainStep[];
  final_summary: string;
}

/** 讲解反馈请求 */
export interface FeedbackRequest {
  knowledge_point: string;
  feedback_level: "完全听懂" | "部分听懂" | "没听懂";
  question_id?: string;
  session_id?: string;
}

/**
 * 获取单题分步讲解
 * 直接调用 Agent 的 explain_exercise 工具
 */
export async function explainExercise(
  params: ExplainRequest
): Promise<ExplainResult> {
  // 通过 chat 接口以工具调用模式触发讲解
  const token = localStorage.getItem("access_token");
  const message = `请对以下题目进行分步讲解：\n\n${params.exercise_content}\n\n学科：${params.subject || "未知"}\n讲解风格：${params.explanation_style || "分步引导式"}`;

  const response = await fetch(`${API_BASE}/ai-tutor/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message,
      history: [],
      context: { subject: params.subject },
    }),
  });

  if (!response.ok) {
    throw new Error(`讲解请求失败: HTTP ${response.status}`);
  }

  // 从 SSE 流中收集讲解步骤
  const reader = response.body?.getReader();
  if (!reader) throw new Error("无法读取响应");

  const decoder = new TextDecoder();
  let buffer = "";
  const steps: ExplainStep[] = [];
  let knowledgePoints: string[] = [];
  let finalSummary = "";
  let assistantContent = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === "token") {
            assistantContent += event.content || "";
          }
        } catch {
          // 跳过解析失败的事件
        }
      }
    }
  }

  // 尝试从 assistant 回复中解析 JSON
  try {
    // 提取 JSON 块
    const jsonMatch = assistantContent.match(/\{[\s\S]*\}/);
    if (jsonMatch) {
      const data = JSON.parse(jsonMatch[0]);
      knowledgePoints = data.knowledge_points || [];
      steps.push(
        ...(data.steps || []).map((s: Record<string, unknown>, i: number) => ({
          step_number: (s.step_number as number) || i + 1,
          title: (s.title as string) || "",
          content: (s.content as string) || "",
          key_point: (s.key_point as string) || "",
          follow_up_question: (s.follow_up_question as string) || "",
        }))
      );
      finalSummary = (data.final_summary as string) || "";
    }
  } catch {
    // JSON 解析失败，使用原始文本
  }

  return {
    knowledge_points: knowledgePoints,
    total_steps: steps.length,
    steps,
    final_summary: finalSummary || assistantContent.slice(0, 200) || "讲解已完成",
  };
}

/**
 * 记录讲解反馈
 * 调用后端 record_mastery_feedback 工具
 */
export async function recordFeedback(params: FeedbackRequest): Promise<void> {
  const token = localStorage.getItem("access_token");
  await fetch(`${API_BASE}/ai-tutor/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message: `记录反馈：知识点"${params.knowledge_point}"，反馈"${params.feedback_level}"`,
      history: [],
    }),
  });
}

export async function streamChat(
  message: string,
  history: Array<{ role: string; content: string }>,
  callbacks: SSECallbacks,
  context?: { grade?: string; subject?: string },
  signal?: AbortSignal,
  sessionId?: number,
): Promise<void> {
  const token = localStorage.getItem("access_token");
  const body: Record<string, unknown> = { message, history, context };
  // 如果传入了 session_id，附带到请求中，后端会自动保存消息到对应会话
  if (sessionId) {
    body.session_id = sessionId;
  }
  const response = await fetch(`${API_BASE}/ai-tutor/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    callbacks.onError?.(`HTTP ${response.status}`);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
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
}
