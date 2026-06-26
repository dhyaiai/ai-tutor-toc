import { useRef, useCallback } from "react";
import { streamChat, type SSECallbacks } from "../services/aiTutorService";

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (
      message: string,
      history: Array<{ role: string; content: string }>,
      callbacks: SSECallbacks,
      context?: { grade?: string; subject?: string },
    ) => {
      // Abort any previous stream
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChat(message, history, callbacks, context, controller.signal);
      } catch {
        // aborted
      }
    },
    [],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  return { sendMessage, abort };
}
