import { useState, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { questionService } from "../services/questionService";
import type { QuestionItem } from "../services/assignmentService";

/** 轮询间隔（毫秒） */
const POLL_INTERVAL = 3000;
/** 最大轮询时间（毫秒），与后端 3 分钟超时匹配 */
const MAX_POLL_TIME = 180_000;

/**
 * 重新分析 hook。
 * 发送请求后自动轮询作业详情，直到题目状态不再是 PENDING 或超时。
 */
export function useReanalysis(assignmentId: number) {
  const [reanalyzing, setReanalyzing] = useState<number | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const queryClient = useQueryClient();

  const reanalyze = useCallback(async (questionId: number, remark?: string) => {
    setReanalyzing(questionId);

    // 清理之前的轮询
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }

    try {
      await questionService.reanalyze(questionId, remark);

      // 立即刷新一次
      queryClient.invalidateQueries({ queryKey: ["assignment", assignmentId] });
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });

      // 轮询等待后台任务完成
      const startTime = Date.now();
      pollTimerRef.current = setInterval(() => {
        const elapsed = Date.now() - startTime;

        // 超时停止
        if (elapsed >= MAX_POLL_TIME) {
          clearInterval(pollTimerRef.current!);
          pollTimerRef.current = null;
          setReanalyzing(null);
          return;
        }

        // 从缓存读取题目状态，判断是否完成
        const data = queryClient.getQueryData<any>(["assignment", assignmentId]);
        const questions: QuestionItem[] = data?.questions || [];
        const targetQuestion = findQuestion(questions, questionId);
        if (targetQuestion && targetQuestion.status !== "pending") {
          // 题目已完成（completed/failed），停止轮询
          clearInterval(pollTimerRef.current!);
          pollTimerRef.current = null;
          setReanalyzing(null);
          // 最终刷新一次确保数据最新
          queryClient.invalidateQueries({ queryKey: ["assignment", assignmentId] });
          queryClient.invalidateQueries({ queryKey: ["assignments"] });
          queryClient.invalidateQueries({ queryKey: ["analytics"] });
          return;
        }

        // 还在 pending，继续轮询刷新
        queryClient.invalidateQueries({ queryKey: ["assignment", assignmentId] });
      }, POLL_INTERVAL);
    } catch {
      setReanalyzing(null);
    }
  }, [queryClient, assignmentId]);

  return { reanalyze, reanalyzing };
}

/** 递归查找题目（支持嵌套的 children） */
function findQuestion(questions: QuestionItem[], id: number): QuestionItem | null {
  for (const q of questions) {
    if (q.id === id) return q;
    if (q.children) {
      const found = findQuestion(q.children, id);
      if (found) return found;
    }
  }
  return null;
}
