import { useState, useRef, useCallback } from "react";
import { Drawer, Input, Button, Space, Typography, Spin, Tag } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { streamChat } from "../services/aiTutorService";

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
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [currentReasoning, setCurrentReasoning] = useState("");
  const [currentToolCalls, setCurrentToolCalls] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const reasoningRef = useRef("");
  const toolCallsRef = useRef<string[]>([]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setCurrentReasoning("");
    setCurrentToolCalls([]);
    reasoningRef.current = "";
    toolCallsRef.current = [];

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const newMessages: Message[] = [...messages, { role: "user", content: text }];
    setMessages([...newMessages]);

    // Placeholder for assistant response
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
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              last.content += content;
              return [...updated];
            });
          },
          onDone: () => {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              last.reasoning = reasoningRef.current || undefined;
              last.toolCalls = toolCallsRef.current.length > 0 ? [...toolCallsRef.current] : undefined;
              return [...updated];
            });
          },
          onError: (error) => {
            setMessages((prev) => {
              const updated = [...prev];
              updated[updated.length - 1].content = `[错误] ${error}`;
              return [...updated];
            });
          },
        },
        undefined,
        controller.signal,
      );
    } catch {
      // aborted or network error
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [input, loading, messages]);

  return (
    <Drawer
      title="AI 助教"
      placement="right"
      width={480}
      open={open}
      onClose={onClose}
    >
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div style={{ flex: 1, overflow: "auto", marginBottom: 16 }}>
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
                  background: msg.role === "user" ? "#1677ff" : "#f0f0f0",
                  color: msg.role === "user" ? "#fff" : "#000",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {msg.reasoning && (
                  <Typography.Text type="secondary" style={{ fontSize: 12, display: "block" }}>
                    思考: {msg.reasoning}
                  </Typography.Text>
                )}
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div style={{ marginBottom: 4 }}>
                    {msg.toolCalls.map((tc, j) => (
                      <Tag key={j} color="blue">{tc}</Tag>
                    ))}
                  </div>
                )}
                {msg.content || (i === messages.length - 1 && loading ? "思考中..." : "")}
              </div>
            </div>
          ))}
          {loading && (
            <div style={{ textAlign: "center" }}>
              <Spin size="small" />
            </div>
          )}
        </div>
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
