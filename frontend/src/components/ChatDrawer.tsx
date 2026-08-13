/**
 * AI 助教聊天抽屉组件
 *
 * 核心功能：
 * 1. 多会话管理：支持创建、切换、删除会话，所有会话持久化存储到后端数据库
 * 2. SSE 流式对话：与后端 AI Agent 实时交互，支持思考过程展示和工具调用标签
 * 3. 消息自动保存：每条对话（用户消息 + AI 回复）在对话完成后自动保存到当前会话
 * 4. 会话切换：切换会话时自动保存当前对话，然后加载目标会话的历史消息
 *
 * 数据流：
 * - 打开抽屉 → 加载会话列表 → 自动进入最近活跃的会话（或创建新会话）
 * - 发送消息 → SSE 流式接收 → 对话完成自动保存 → 更新会话列表排序
 * - 切换会话 → 保存当前会话 → 加载目标会话消息 → 渲染历史对话
 * - 删除会话 → 软删除 → 刷新列表 → 自动切换到下一个会话
 */

import { useState, useRef, useCallback, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Drawer,
  Input,
  Button,
  Space,
  Typography,
  Spin,
  Dropdown,
  message,
  Modal,
} from "antd";
import {
  SendOutlined,
  PlusOutlined,
  MenuOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  BookOutlined,
  LineChartOutlined,
  CalendarOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { streamChat } from "../services/aiTutorService";
import api from "../services/api";
import { parseImageSize } from "./MarkdownPreview";
import {
  conversationService,
  type ConversationListItem,
  type ConversationMessage,
} from "../services/conversationService";

/** 消息文本中的 URL/文件链接正则 */
const FILE_URL_REGEX = /(\/api\/v1\/files\/[^\s<>"'\]\)]+)/g;

/**
 * 工具名称 → 中文进度文案。
 * AI 调用工具时在消息气泡内展示执行进度，让用户看到 AI 正在做什么，
 * 而不是只有"思考中"转圈（此前工具调用全程无感知，长任务像卡死）。
 */
const TOOL_STEP_LABELS: Record<string, string> = {
  generate_analysis_report: "正在生成学情分析报告（约需1分钟）",
  generate_correction_workbook: "正在整理错题订正本",
  generate_study_plan: "正在制定学习计划",
  get_assignment_score: "正在查询作业成绩统计",
  get_error_knowledge: "正在查询错题知识点分布",
  get_score_trend: "正在查询分数趋势",
  query_knowledge_state: "正在查询知识点掌握度",
  correct_composition: "正在批改作文",
  explain_exercise: "正在讲解题目",
  update_knowledge_state: "正在更新知识点状态",
  record_mastery_feedback: "正在记录学习反馈",
};

/**
 * 点击时经带自动刷新 token 的 axios 实例拉取文件流后在新标签页打开。
 * 链接 href 不再拼接 ?token=：JWT 放进 URL 会落入 uvicorn 访问日志、
 * 浏览器历史与 Referer 头，泄露后可被整串重放冒充用户。
 * 正常点击走本函数（axios 自动带 Authorization 头 + 401 自动刷新）；
 * 之前的做法是渲染时把 access_token 拼进 href，token 30 分钟过期后
 * 再点击链接会得到 401；改为点击时请求可确保凭证新鲜（401 自动刷新）。
 */
// 模块级 Blob URL 清理定时器（openFileLink 在模块作用域，无法访问组件 ref）
let _blobRevokeTimer: ReturnType<typeof setTimeout> | null = null;
// 追踪当前未回收的 Blob URL，确保清理时能正确撤销
let _pendingBlobUrl: string | null = null;

/**
 * 撤销待清理的 Blob URL（供组件卸载时调用，防止内存泄漏）
 */
export function revokePendingBlobUrl(): void {
  if (_blobRevokeTimer) {
    clearTimeout(_blobRevokeTimer);
    _blobRevokeTimer = null;
  }
  if (_pendingBlobUrl) {
    URL.revokeObjectURL(_pendingBlobUrl);
    _pendingBlobUrl = null;
  }
}

async function openFileLink(url: string) {
  // 先同步打开空白窗口，避免异步请求完成后 window.open 被浏览器拦截
  const win = window.open("", "_blank");
  try {
    const [base, query] = url.split("?");
    const params = new URLSearchParams(query || "");
    params.delete("token"); // 移除历史消息中可能残留的过期 token（后端优先读查询参数）
    const q = params.toString();
    // api 实例的 baseURL 已含 /api/v1，去掉前缀避免重复拼接
    const path = base.replace(/^\/api\/v1/, "") + (q ? `?${q}` : "");
    const res = await api.get(path, { responseType: "blob" });
    const blobUrl = URL.createObjectURL(res.data as Blob);
    if (win) {
      win.location.href = blobUrl;
    } else {
      window.open(blobUrl, "_blank");
    }
    // 清理旧的 Blob URL 定时器，设置新的清理任务
    if (_blobRevokeTimer) clearTimeout(_blobRevokeTimer);
    _pendingBlobUrl = blobUrl;
    _blobRevokeTimer = setTimeout(() => {
      if (_pendingBlobUrl) {
        URL.revokeObjectURL(_pendingBlobUrl);
        _pendingBlobUrl = null;
      }
      _blobRevokeTimer = null;
    }, 60_000);
  } catch {
    win?.close();
    message.error("文件打开失败，请刷新页面或重新登录后重试");
  }
}

/**
 * 预处理 markdown 文本：将纯文本的文件路径转为 markdown 链接。
 * 已经处于 markdown 链接语法中的路径（如 [text](path)）不会被重复处理。
 */
function preprocessFileLinks(markdown: string): string {
  // 用正则替换不在 markdown 链接括号内的文件路径
  // 匹配以 /api/v1/files/ 开头的路径，但不能跟在 ]( 后面
  return markdown.replace(
    /(?<!\]\()(\/api\/v1\/files\/[^\s<>"'\]\)]+)/g,
    "[📥 点击查看]($1)",
  );
}

/**
 * 将消息文本中的 API 文件链接渲染为带图标的可点击链接。
 * 用于用户消息的纯文本渲染，AI 消息使用 ReactMarkdown 渲染。
 */
function renderUserMessage(text: string): React.ReactNode {
  // 带捕获组的正则 split：结果数组中偶数位是普通文本，奇数位是正则捕获的 URL
  const parts = text.split(FILE_URL_REGEX);
  if (parts.length <= 1) return text;

  const result: React.ReactNode[] = [];
  parts.forEach((part, i) => {
    if (!part) return;
    if (i % 2 === 1) {
      // 奇数位是 URL：只渲染一次链接（不能同时渲染成 span，否则 URL 文本重复出现）
      result.push(
        <a
          key={`link-${i}`}
          href={part}
          onClick={(e) => {
            e.preventDefault();
            void openFileLink(part);
          }}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "#fff", textDecoration: "underline" }}
        >
          📥 点击查看
        </a>
      );
    } else {
      result.push(<span key={`t-${i}`}>{part}</span>);
    }
  });

  return <>{result}</>;
}

/** AI 工具调用的进度步骤（流式实时展示） */
interface ToolStep {
  name: string;
  status: "running" | "done" | "error";
  /** 工具执行结果摘要（来自 tool_result 事件，后端已截断为 200 字符） */
  summary?: string;
}

/** 前端消息数据结构（与后端 ConversationMessage 对应） */
interface Message {
  /** 稳定唯一标识，用于 React key，避免流式更新时 DOM 重建 */
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  toolCalls?: string[];
  /** 本次流式的工具步骤展示（可选字段，历史消息无此数据时不影响渲染） */
  toolSteps?: ToolStep[];
  /**
   * 流式过程中的错误提示（仅 UI 展示，不参与 content，故不会进入 LLM 上下文与持久化）。
   * 后端"分析超时，正在直接回答..."类信息性 error 事件后流还会继续输出，
   * 覆盖 content 会把真实回答污染成 "[错误]...真实回答"。
   */
  errorText?: string;
}

let _msgIdCounter = 0;

/** 生成稳定唯一消息 ID */
function nextMsgId(): string {
  return `msg-${Date.now()}-${++_msgIdCounter}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ChatDrawer({ open, onClose }: Props) {
  // ============ 状态管理 ============

  /** 会话列表（左侧/下拉菜单用） */
  const [sessions, setSessions] = useState<ConversationListItem[]>([]);
  /** 当前活跃会话ID */
  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  /** 当前会话标题 */
  const [currentTitle, setCurrentTitle] = useState("新对话");
  /** 当前会话消息列表 */
  const [messages, setMessages] = useState<Message[]>([]);
  /** 输入框文本 */
  const [input, setInput] = useState("");
  /** 是否正在加载（发送消息/切换会话） */
  const [loading, setLoading] = useState(false);
  /** 会话列表加载中 */
  const [sessionsLoading, setSessionsLoading] = useState(false);
  /** AI 思考过程（流式累积） */
  const [currentReasoning, setCurrentReasoning] = useState("");
  /** AI 工具调用列表（流式累积） */
  const [currentToolCalls, setCurrentToolCalls] = useState<string[]>([]);
  /** 编辑标题的会话ID和输入值 */
  const [editingTitleId, setEditingTitleId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

    /** 流式响应中断控制器 */
  const abortRef = useRef<AbortController | null>(null);
  /** Blob URL 清理定时器 ID（组件卸载时清除） */
  const revokeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * 当前流式请求归属的会话ID（跨会话守卫）。
   * 发起请求时记录；切换/新建/关闭会话时由 stopStream 置空，
   * SSE 回调中先校验此值，确保旧会话的流不会污染新会话的消息。
   */
  const activeSessionRef = useRef<number | null>(null);
  /** 思考过程累积（避免闭包陷阱） */
  const reasoningRef = useRef("");
  /** 工具调用累积 */
  const toolCallsRef = useRef<string[]>([]);
  /** 工具步骤累积（含状态与摘要，供流式过程实时渲染） */
  const toolStepsRef = useRef<ToolStep[]>([]);
  /** 消息列表 ref（用于在回调中获取最新值） */
  const messagesRef = useRef<Message[]>([]);
  // 指向"最新"的 updateSessionAfterChat 实现（每次渲染后同步）：
  // handleSend 依赖数组不包含它（避免声明顺序 TDZ），但流结束回调必须调用最新版，
  // 否则首次对话把标题从"新对话"改为前30字后，第二轮对话仍持有旧闭包，
  // 会把标题再次覆盖成新消息的前 30 字（用户手动改名同样会被覆盖）。
  const updateSessionAfterChatRef = useRef<((text: string) => Promise<void>) | null>(null);

  // 同步 messages 到 ref
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  /**
   * 终止当前流式请求并复位相关状态。
   *
   * 点击停止按钮、切换会话、新建会话时调用（关闭抽屉即最小化时不调用，
   * 正在生成的对话需在后台继续存活）。
   * 先失效会话守卫（activeSessionRef），让在途的 SSE 回调全部被忽略，
   * 再 abort 请求并复位 loading，保证一个会话的生成状态不会影响另一个会话。
   */
  const stopStream = useCallback(() => {
    activeSessionRef.current = null; // 失效守卫，后续流式回调全部忽略
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setCurrentReasoning("");
    setCurrentToolCalls([]);
    reasoningRef.current = "";
    toolCallsRef.current = [];
    toolStepsRef.current = [];
  }, []);

  // ============ 会话数据加载 ============

  /**
   * 加载会话列表
   * 在抽屉打开时调用
   */
  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const list = await conversationService.list();
      setSessions(list);

      // 如果有会话，自动进入最近活跃的会话
      if (list.length > 0 && !currentSessionId) {
        await loadSession(list[0].id);
      } else if (list.length === 0 && !currentSessionId) {
        // 没有会话时自动创建一个
        await createNewSession();
      }
    } catch (err) {
      console.error("加载会话列表失败:", err);
      message.error("加载会话列表失败");
    } finally {
      setSessionsLoading(false);
    }
  }, [currentSessionId]);

  /**
   * 加载指定会话的完整消息历史
   * @param sessionId 会话ID
   */
  const loadSession = useCallback(async (sessionId: number) => {
    try {
      const detail = await conversationService.get(sessionId);
      setCurrentSessionId(detail.id);
      setCurrentTitle(detail.title);
      // 将后端消息格式映射为前端 Message 格式
      const msgs: Message[] = detail.messages.map((m: ConversationMessage) => ({
        id: m.id ? `hist-${m.id}` : nextMsgId(),
        role: m.role as "user" | "assistant",
        content: m.content,
        reasoning: m.reasoning || undefined,
        toolCalls: m.tool_calls || undefined,
      }));
      setMessages(msgs);
    } catch (err) {
      console.error("加载会话失败:", err);
      message.error("加载会话失败");
    }
  }, []);

  /**
   * 创建新会话
   */
  const createNewSession = useCallback(async () => {
    try {
      const detail = await conversationService.create("新对话");
      setCurrentSessionId(detail.id);
      setCurrentTitle("新对话");
      setMessages([]);
      // 刷新会话列表
      const list = await conversationService.list();
      setSessions(list);
      message.success("已创建新会话");
    } catch (err) {
      console.error("创建会话失败:", err);
      message.error("创建会话失败");
    }
  }, []);

  // 抽屉打开时自动加载会话列表
  useEffect(() => {
    if (open) {
      loadSessions();
    }
  }, [open, loadSessions]);

  // 关闭抽屉（最小化）时不再终止在途流式请求：
  // 点击网页其他区域/遮罩关闭抽屉只是收起界面，正在生成的对话必须在后台继续存活，
  // 重新打开抽屉后仍能看到完整回复，不能因最小化而中断。
  // 主动停止（停止按钮/切换会话/新建会话）仍走 stopStream。

  // 组件真正卸载时（退出登录/页面切换/路由销毁）终止在途流式请求：
  // Drawer 收起（open=false）并不卸载组件，因此上面的"最小化不中断"设计不受影响；
  // 只有组件被销毁时才需要 abort，避免连接与回调在组件销毁后继续运行。
  useEffect(() => {
    return () => {
      activeSessionRef.current = null; // 失效会话守卫，让在途 SSE 回调全部被忽略
      abortRef.current?.abort();
      abortRef.current = null;
      // 清理未执行的 Blob URL 撤销定时器
      if (revokeTimerRef.current) {
        clearTimeout(revokeTimerRef.current);
        revokeTimerRef.current = null;
      }
      // 清理模块级 Blob URL 撤销定时器（防止内存泄漏）
      revokePendingBlobUrl();
    };
  }, []);

  // ============ 消息发送 ============

  /**
   * 发送消息并处理 SSE 流式响应
   *
   * 流程：
   * 1. 构建历史消息（从当前 messages state）
   * 2. 调用 streamChat 发起 SSE 请求，携带当前 session_id
   * 3. 流式接收 AI 回复（token/reasoning/tool_call）
   * 4. 对话完成后自动保存用户消息和AI回复到后端
   */
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setCurrentReasoning("");
    setCurrentToolCalls([]);
    reasoningRef.current = "";
    toolCallsRef.current = [];
    toolStepsRef.current = [];

    // 构建对话历史（最近10轮，防止 token 过长）
    const history = messagesRef.current
      .slice(-20)
      .map((m) => ({ role: m.role, content: m.content }));

    // 追加用户消息到界面
    const userMsg: Message = { id: nextMsgId(), role: "user", content: text };
    const newMessages = [...messagesRef.current, userMsg];
    setMessages(newMessages);

    // AI 回复占位
    const assistantMsg: Message = { id: nextMsgId(), role: "assistant", content: "" };
    setMessages([...newMessages, assistantMsg]);

    // 记录本次请求归属的会话，流式回调据此做跨会话守卫
    const sessionIdAtSend = currentSessionId;
    activeSessionRef.current = sessionIdAtSend;

    setLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;

    // 注意：不使用绝对超时。aiTutorService 已实现空闲超时（距上次事件 > 4 分钟则终止），
    // 工具执行（报告/学习计划/订正本）最长需要 180s，绝对超时会导致这些工具被误杀。
    try {
      await streamChat(
        text,
        history,
        {
          onReasoning: (content) => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            reasoningRef.current += content;
            setCurrentReasoning((prev) => prev + content);
          },
          onToolCall: (name) => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            toolCallsRef.current = [...toolCallsRef.current, name];
            setCurrentToolCalls((prev) => [...prev, name]);
            // 追加 running 步骤并实时写入最后一条消息，让用户在工具执行期间看到进度
            toolStepsRef.current = [
              ...toolStepsRef.current,
              { name, status: "running" },
            ];
            setMessages((prev) =>
              prev.map((msg, idx) =>
                idx === prev.length - 1 && msg.role === "assistant"
                  ? { ...msg, toolSteps: [...toolStepsRef.current] }
                  : msg
              )
            );
          },
          onToolResult: (name, summary) => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            // 把最后一条同名 running 步骤标记为 done（同一工具可能多轮调用）
            const steps = [...toolStepsRef.current];
            const idx = steps.map((s) => s.name).lastIndexOf(name);
            if (idx !== -1 && steps[idx].status === "running") {
              steps[idx] = { ...steps[idx], status: "done", summary };
            }
            toolStepsRef.current = steps;
            setMessages((prev) =>
              prev.map((msg, i) =>
                i === prev.length - 1 && msg.role === "assistant"
                  ? { ...msg, toolSteps: [...steps] }
                  : msg
              )
            );
          },
          onToken: (content) => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            setMessages((prev) =>
              prev.map((msg, idx) =>
                idx === prev.length - 1 && msg.role === "assistant"
                  ? { ...msg, content: msg.content + content }
                  : msg
              )
            );
          },
          onDone: () => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            // 将累积的 reasoning 和 toolCalls 写入最后一条消息（toolSteps 已在流中实时写入）
            // 注意：必须返回新对象而非修改旧对象，避免 React state mutation
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (!last || last.role !== "assistant") return prev;
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  reasoning: reasoningRef.current || undefined,
                  toolCalls: toolCallsRef.current.length > 0
                    ? [...toolCallsRef.current]
                    : undefined,
                  toolSteps: toolStepsRef.current.length > 0
                    ? [...toolStepsRef.current]
                    : undefined,
                },
              ];
            });

            // 后端 SSE stream 已在 done 事件时自动保存了消息，前端不再重复保存
            // 只需更新会话标题（首次对话时）和刷新会话列表
            if (currentSessionId) {
              updateSessionAfterChatRef.current?.(text);
            }
          },
          onError: (error) => {
            // 会话守卫：若期间切换/新建了会话，忽略旧流的事件
            if (activeSessionRef.current !== sessionIdAtSend) return;
            // 将仍在 running 的工具步骤标记为 error，避免界面残留"转圈"假象
            const steps = toolStepsRef.current.map((s) =>
              s.status === "running" ? { ...s, status: "error" as const } : s
            );
            toolStepsRef.current = steps;
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === "assistant") {
                // 不覆盖消息正文：后端"分析超时，正在直接回答..."类信息性 error 事件
                // 之后流还会继续输出真实回答，覆盖会把最终消息污染成
                // "[错误] ...真实回答"，且错误文本会进入下轮对话上下文。
                // 错误提示单独存 errorText，仅 UI 展示，不参与 content/历史构建。
                updated[updated.length - 1] = {
                  ...last,
                  toolSteps: steps.length > 0 ? [...steps] : undefined,
                  errorText: error,
                };
              }
              return updated;
            });
          },
        },
        undefined,
        controller.signal,
        // 传入 session_id，后端也会自动保存消息（双重保障）
        currentSessionId || undefined,
      );
    } catch {
      // 用户取消或网络错误，静默处理
    } finally {
      // 仅当本会话仍活跃时才复位 loading（切换/新建会话时已由 stopStream 复位）
      if (activeSessionRef.current === sessionIdAtSend) {
        setLoading(false);
      }
      abortRef.current = null;
    }
    // 依赖无需包含 updateSessionAfterChat：通过 ref 始终调用最新实现，
    // 避免声明顺序（handleSend 在前）造成 TDZ，同时根治标题重复覆盖问题
  }, [input, loading, currentSessionId]);

  /**
   * 对话完成后更新会话标题（首次对话）和刷新列表。
   * 消息保存由后端 SSE stream 在 done 事件中自动完成，前端不再重复保存。
   * @param userText 用户消息文本（用于生成会话标题）
   */
  const updateSessionAfterChat = useCallback(
    async (userText: string) => {
      if (!currentSessionId) return;

      try {
        // 首次对话时，用用户第一条消息的前30字符作为会话标题
        if (currentTitle === "新对话") {
          const newTitle =
            userText.length > 30 ? userText.slice(0, 30) + "..." : userText;
          await conversationService.update(currentSessionId, {
            title: newTitle,
          });
          setCurrentTitle(newTitle);
        }

        // 刷新会话列表以更新排序和摘要
        const list = await conversationService.list();
        setSessions(list);
      } catch (err) {
        console.error("更新会话失败:", err);
      }
    },
    [currentSessionId, currentTitle]
  );

  // 同步最新实现到 ref（标题更新后 currentTitle 变化 → updateSessionAfterChat
  // 重建 → 本 effect 刷新 ref，handleSend 无需重建也能拿到最新版）
  useEffect(() => {
    updateSessionAfterChatRef.current = updateSessionAfterChat;
  }, [updateSessionAfterChat]);

  // ============ 会话操作 ============

  /**
   * 切换到指定会话
   * 先通过批量 API 保存当前会话的消息（如果有），然后加载目标会话
   */
  const switchSession = useCallback(
    async (sessionId: number) => {
      if (sessionId === currentSessionId) return;

      // 先终止当前会话的流式请求，防止其状态/回调影响目标会话
      stopStream();

      // 后端在每次 SSE 对话完成时已自动保存消息，直接切换即可
      // 加载目标会话
      await loadSession(sessionId);

      // 刷新列表
      try {
        const list = await conversationService.list();
        setSessions(list);
      } catch {}
    },
    [currentSessionId, loadSession, stopStream]
  );

  /**
   * 新建会话按钮处理
   * 先保存当前会话，再创建新会话
   */
  const handleNewSession = useCallback(async () => {
    // 先终止当前会话的流式请求（若仍在生成中），再处理新建逻辑
    stopStream();

    // 当前会话为空时，不创建新会话，避免产生一堆空白会话
    if (messagesRef.current.length === 0) {
      message.info("当前已是空白会话，无需新建");
      return;
    }

    // 后端在每次 SSE 对话完成时已自动保存消息，直接创建新会话即可
    await createNewSession();
  }, [createNewSession, stopStream]);

  /**
   * 删除指定会话
   * 删除后如果当前没有活跃会话，自动创建新会话或切换到下一个
   */
  const handleDeleteSession = useCallback(
    async (sessionId: number) => {
      Modal.confirm({
        title: "确认删除",
        content: "删除后会话数据将无法恢复，确定要删除吗？",
        okText: "确定删除",
        cancelText: "取消",
        okButtonProps: { danger: true },
        onOk: async () => {
          try {
            await conversationService.delete(sessionId);

            // 如果删除的是当前会话
            if (sessionId === currentSessionId) {
              setCurrentSessionId(null);
              setMessages([]);
              setCurrentTitle("新对话");

              // 尝试切换到下一个会话，没有则创建新会话
              const list = await conversationService.list();
              setSessions(list);
              if (list.length > 0) {
                await loadSession(list[0].id);
              } else {
                await createNewSession();
              }
            } else {
              // 删除了非当前会话，只需刷新列表
              const list = await conversationService.list();
              setSessions(list);
            }

            message.success("会话已删除");
          } catch (err) {
            console.error("删除会话失败:", err);
            message.error("删除会话失败");
          }
        },
      });
    },
    [currentSessionId, loadSession, createNewSession]
  );

  /**
   * 开始编辑会话标题
   */
  const startEditTitle = useCallback(
    (sessionId: number, currentTitle: string) => {
      setEditingTitleId(sessionId);
      setEditingTitle(currentTitle);
    },
    []
  );

  /**
   * 保存编辑后的会话标题
   */
  const saveEditTitle = useCallback(async () => {
    if (!editingTitleId) return;

    const newTitle = editingTitle.trim();
    if (!newTitle) {
      setEditingTitleId(null);
      return;
    }

    try {
      await conversationService.update(editingTitleId, { title: newTitle });
      const list = await conversationService.list();
      setSessions(list);
      if (editingTitleId === currentSessionId) {
        setCurrentTitle(newTitle);
      }
      message.success("标题已更新");
    } catch (err) {
      console.error("更新标题失败:", err);
      message.error("更新标题失败");
    } finally {
      setEditingTitleId(null);
    }
  }, [editingTitleId, editingTitle, currentSessionId]);

  // ============ 会话列表下拉菜单构建 ============

  /** 构建会话列表的下拉菜单项 */
  const sessionMenuItems = sessions.map((s) => ({
    key: String(s.id),
    label: editingTitleId === s.id ? (
      // 编辑标题模式：内联输入框
      <Input
        size="small"
        value={editingTitle}
        onChange={(e) => setEditingTitle(e.target.value)}
        onPressEnter={saveEditTitle}
        onBlur={saveEditTitle}
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
        maxLength={50}
        autoFocus
        style={{ width: 260 }}
      />
    ) : (
      // 正常模式：显示标题 + 操作按钮
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          width: 280,
        }}
      >
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            fontWeight: s.id === currentSessionId ? "bold" : "normal",
          }}
        >
          {s.id === currentSessionId && "● "}
          {s.title}
        </span>
        <Space
          size={2}
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          style={{ flexShrink: 0, marginLeft: 8 }}
        >
          <span style={{ fontSize: 11, color: "#999" }}>
            {s.message_count}条
          </span>
          <Button
            type="text"
            size="small"
            icon={<EditOutlined style={{ fontSize: 12 }} />}
            onClick={() => startEditTitle(s.id, s.title)}
            style={{ padding: "0 4px", height: 22 }}
          />
          <Button
            type="text"
            size="small"
            danger
            icon={<DeleteOutlined style={{ fontSize: 12 }} />}
            onClick={() => handleDeleteSession(s.id)}
            style={{ padding: "0 4px", height: 22 }}
          />
        </Space>
      </div>
    ),
    // 点击切换会话（编辑中不切换）
    onClick: () => {
      if (editingTitleId) return;
      switchSession(s.id);
    },
  }));

  // ============ 渲染 ============

  return (
    <Drawer
      title={
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          {/* 左侧：会话标题 */}
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: 1,
            }}
          >
            {currentTitle}
          </span>
          {/* 右侧：操作按钮 */}
          <Space size={4}>
            {/* 新建会话按钮 */}
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              onClick={handleNewSession}
              title="新建会话"
            />
            {/* 会话列表下拉菜单 */}
            <Dropdown
              menu={{ items: sessionMenuItems }}
              trigger={["click"]}
              placement="bottomRight"
            >
              <Button
                type="text"
                size="small"
                icon={<MenuOutlined />}
                title="会话列表"
              />
            </Dropdown>
          </Space>
        </div>
      }
      placement="right"
      width={480}
      open={open}
      onClose={onClose}
      loading={sessionsLoading}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        {/* 消息列表区域 */}
        <div style={{ flex: 1, overflow: "auto", marginBottom: 16 }}>
          {/* 空状态提示 */}
          {messages.length === 0 && !loading && (
            <div
              style={{
                textAlign: "center",
                color: "#999",
                paddingTop: 80,
              }}
            >
              <Typography.Text type="secondary">
                向 AI 助教提问，开始对话吧
              </Typography.Text>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={msg.id}
              style={{
                marginBottom: 12,
                textAlign: msg.role === "user" ? "right" : "left",
              }}
            >
              <div
                style={{
                  display: "inline-block",
                  maxWidth: "80%",
                  padding: "8px 12px",
                  borderRadius: 8,
                  background:
                    msg.role === "user" ? "#1677ff" : "#f0f0f0",
                  color: msg.role === "user" ? "#fff" : "#000",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  textAlign: "left",
                }}
              >
                {/* AI 思考过程展示 */}
                {msg.reasoning && (
                  <Typography.Text
                    type="secondary"
                    style={{
                      fontSize: 12,
                      display: "block",
                      marginBottom: 4,
                      borderLeft: "2px solid #d9d9d9",
                      paddingLeft: 8,
                    }}
                  >
                    💭 思考: {msg.reasoning}
                  </Typography.Text>
                )}
                {/* AI 工具调用进度展示：转圈=执行中 / ✓=完成 / ✗=失败，
                    让用户看到长任务（报告/计划/订正本）正在做什么 */}
                {msg.toolSteps && msg.toolSteps.length > 0 && (
                  <div
                    style={{
                      marginBottom: 4,
                      fontSize: 12,
                      color: "#666",
                    }}
                  >
                    {msg.toolSteps.map((step, si) => (
                      <div
                        key={si}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          marginBottom: 2,
                        }}
                      >
                        {step.status === "running" ? (
                          <Spin size="small" />
                        ) : step.status === "done" ? (
                          <span style={{ color: "#52c41a", lineHeight: 1 }}>
                            ✓
                          </span>
                        ) : (
                          <span style={{ color: "#ff4d4f", lineHeight: 1 }}>
                            ✗
                          </span>
                        )}
                        <span>
                          {TOOL_STEP_LABELS[step.name] || step.name}
                        </span>
                        {step.summary && step.status === "done" && (
                          <span
                            title={step.summary}
                            style={{
                              color: "#999",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              maxWidth: 140,
                              flexShrink: 1,
                            }}
                          >
                            {step.summary}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {/* 消息正文：用户消息纯文本，AI 消息 Markdown 渲染 */}
                {msg.content ? (
                  msg.role === "assistant" ? (
                    <div className="markdown-body" style={{ fontSize: 14 }}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          img: ({ src, alt, title }) => {
                            // 与 MarkdownPreview 口径一致：支持 title 尺寸指令（"=300x" 等）
                            const size = parseImageSize(title);
                            return (
                              <img
                                src={src}
                                alt={alt || ""}
                                title={size ? undefined : title}
                                style={{ maxWidth: "100%", ...(size ?? {}) }}
                              />
                            );
                          },
                          a: ({ href, children }) => {
                            const url = href || "";
                            const isFile = url.startsWith("/api/v1/files/");
                            return (
                              <a
                                href={url}
                                onClick={
                                  isFile
                                    ? (e) => {
                                        e.preventDefault();
                                        void openFileLink(url);
                                      }
                                    : undefined
                                }
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{ color: "#1677ff" }}
                              >
                                {children}
                              </a>
                            );
                          },
                          table: ({ children }) => (
                            <table
                              style={{
                                borderCollapse: "collapse",
                                width: "100%",
                                margin: "8px 0",
                                fontSize: 13,
                              }}
                            >
                              {children}
                            </table>
                          ),
                          th: ({ children }) => (
                            <th
                              style={{
                                border: "1px solid #d9d9d9",
                                padding: "4px 8px",
                                background: "#fafafa",
                                textAlign: "left",
                                fontWeight: 600,
                              }}
                            >
                              {children}
                            </th>
                          ),
                          td: ({ children }) => (
                            <td
                              style={{
                                border: "1px solid #d9d9d9",
                                padding: "4px 8px",
                              }}
                            >
                              {children}
                            </td>
                          ),
                        }}
                      >
                        {preprocessFileLinks(msg.content)}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    renderUserMessage(msg.content)
                  )
                ) : i === messages.length - 1 && loading ? (
                  // 有工具正在执行时显示"正在执行..."，否则显示"思考中..."
                  msg.toolSteps?.some((s) => s.status === "running")
                    ? "正在执行..."
                    : "思考中..."
                ) : (
                  ""
                )}
                {/* 流式错误提示（信息性提示或终局错误），不进入消息正文，避免污染对话历史 */}
                {msg.errorText && (
                  <div style={{ marginTop: 6, color: "#ff4d4f", fontSize: 12 }}>
                    {msg.errorText}
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* 流式响应加载指示器 */}
          {loading && (
            <div style={{ textAlign: "center" }}>
              <Spin size="small" />
            </div>
          )}
        </div>

        {/* 快捷指令栏 */}
        <div style={{ marginBottom: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
          {[
            { icon: <FileTextOutlined />, label: "生成报告", prompt: "帮我生成最近一次作业的分析报告" },
            { icon: <BookOutlined />, label: "查看错题", prompt: "帮我整理最近的错题订正本" },
            { icon: <LineChartOutlined />, label: "查看学情", prompt: "分析我最近的学情状态" },
            { icon: <CalendarOutlined />, label: "制定计划", prompt: "帮我制定一周的数学专项学习计划" },
          ].map((cmd) => (
            <Button
              key={cmd.label}
              size="small"
              icon={cmd.icon}
              onClick={() => {
                if (!loading) {
                  setInput(cmd.prompt);
                }
              }}
              disabled={loading}
            >
              {cmd.label}
            </Button>
          ))}
        </div>

        {/* 底部输入区域 */}
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={handleSend}
            placeholder="向 AI 助教提问..."
            disabled={loading}
          />
          <Button
            type="primary"
            danger={loading}
            icon={loading ? <StopOutlined /> : <SendOutlined />}
            onClick={loading ? stopStream : handleSend}
            title={loading ? "停止生成" : "发送"}
          />
        </Space.Compact>
      </div>
    </Drawer>
  );
}
