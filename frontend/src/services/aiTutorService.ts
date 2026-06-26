const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

export interface SSECallbacks {
  onReasoning?: (content: string) => void;
  onToolCall?: (name: string, args: unknown) => void;
  onToolResult?: (name: string, summary: string) => void;
  onToken?: (content: string) => void;
  onError?: (error: string) => void;
  onDone?: () => void;
}

export async function streamChat(
  message: string,
  history: Array<{ role: string; content: string }>,
  callbacks: SSECallbacks,
  context?: { grade?: string; subject?: string },
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem("access_token");
  const response = await fetch(`${API_BASE}/ai-tutor/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message, history, context }),
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
