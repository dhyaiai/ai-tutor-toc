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
} from "@ant-design/icons";
import { streamChat } from "../services/aiTutorService";
import {
  conversationService,
  type ConversationListItem,
  type ConversationMessage,
} from "../services/conversationService";

/** 消息文本中的 URL/文件链接正则 */
const FILE_URL_REGEX = /(\/api\/v1\/files\/[^\s<>"'\]\)]+)/g;

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
  const parts = text.split(FILE_URL_REGEX);
  const matches = text.match(FILE_URL_REGEX) || [];

  if (matches.length === 0) return text;

  const result: React.ReactNode[] = [];
  let matchIndex = 0;

  parts.forEach((part, i) => {
    if (part) {
      result.push(<span key={`t-${i}`}>{part}</span>);
    }
    if (matchIndex < matches.length) {
      result.push(
        <a
          key={`link-${matchIndex}`}
          href={matches[matchIndex]}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "#fff", textDecoration: "underline" }}
        >
          📥 点击查看
        </a>
      );
      matchIndex++;
    }
  });

  return <>{result}</>;
}

/** 前端消息数据结构（与后端 ConversationMessage 对应） */
interface Message {
  role: "user" | "assistant";
  content: string;
  reasoning?: string;
  toolCalls?: string[];
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
  /** 思考过程累积（避免闭包陷阱） */
  const reasoningRef = useRef("");
  /** 工具调用累积 */
  const toolCallsRef = useRef<string[]>([]);
  /** 消息列表 ref（用于在回调中获取最新值） */
  const messagesRef = useRef<Message[]>([]);

  // 同步 messages 到 ref
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

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

  // 关闭抽屉时清理状态
  useEffect(() => {
    if (!open) {
      // 关闭时不清除 currentSessionId，下次打开时可以恢复
      setCurrentReasoning("");
      setCurrentToolCalls([]);
      reasoningRef.current = "";
      toolCallsRef.current = [];
    }
  }, [open]);

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

    // 构建对话历史（最近10轮，防止 token 过长）
    const history = messagesRef.current
      .slice(-20)
      .map((m) => ({ role: m.role, content: m.content }));

    // 追加用户消息到界面
    const userMsg: Message = { role: "user", content: text };
    const newMessages = [...messagesRef.current, userMsg];
    setMessages(newMessages);

    // AI 回复占位
    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages([...newMessages, assistantMsg]);

    setLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        text,
        history,
        {
          onReasoning: (content) => {
            reasoningRef.current += content;
            setCurrentReasoning((prev) => prev + content);
          },
          onToolCall: (name) => {
            toolCallsRef.current = [...toolCallsRef.current, name];
            setCurrentToolCalls((prev) => [...prev, name]);
          },
          onToken: (content) => {
            setMessages((prev) =>
              prev.map((msg, idx) =>
                idx === prev.length - 1 && msg.role === "assistant"
                  ? { ...msg, content: msg.content + content }
                  : msg
              )
            );
          },
          onDone: () => {
            // 将累积的 reasoning 和 toolCalls 写入最后一条消息
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === "assistant") {
                last.reasoning =
                  reasoningRef.current || undefined;
                last.toolCalls =
                  toolCallsRef.current.length > 0
                    ? [...toolCallsRef.current]
                    : undefined;
              }
              return [...updated];
            });

            // 后端 SSE stream 已在 done 事件时自动保存了消息，前端不再重复保存
            // 只需更新会话标题（首次对话时）和刷新会话列表
            if (currentSessionId) {
              updateSessionAfterChat(text);
            }
          },
          onError: (error) => {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last && last.role === "assistant") {
                last.content = `[错误] ${error}`;
              }
              return [...updated];
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
      setLoading(false);
      abortRef.current = null;
    }
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

  // ============ 会话操作 ============

  /**
   * 切换到指定会话
   * 先通过批量 API 保存当前会话的消息（如果有），然后加载目标会话
   */
  const switchSession = useCallback(
    async (sessionId: number) => {
      if (sessionId === currentSessionId) return;

      // 后端在每次 SSE 对话完成时已自动保存消息，直接切换即可
      // 加载目标会话
      await loadSession(sessionId);

      // 刷新列表
      try {
        const list = await conversationService.list();
        setSessions(list);
      } catch {}
    },
    [currentSessionId, loadSession]
  );

  /**
   * 新建会话按钮处理
   * 先保存当前会话，再创建新会话
   */
  const handleNewSession = useCallback(async () => {
    // 当前会话为空时，不创建新会话，避免产生一堆空白会话
    if (messagesRef.current.length === 0) {
      message.info("当前已是空白会话，无需新建");
      return;
    }

    // 后端在每次 SSE 对话完成时已自动保存消息，直接创建新会话即可
    await createNewSession();
  }, [currentSessionId, createNewSession]);

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
              key={i}
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
                {/* 消息正文：用户消息纯文本，AI 消息 Markdown 渲染 */}
                {msg.content ? (
                  msg.role === "assistant" ? (
                    <div className="markdown-body" style={{ fontSize: 14 }}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ href, children }) => (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ color: "#1677ff" }}
                            >
                              {children}
                            </a>
                          ),
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
                  "思考中..."
                ) : (
                  ""
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
            icon={<SendOutlined />}
            onClick={handleSend}
            loading={loading}
          />
        </Space.Compact>
      </div>
    </Drawer>
  );
}
