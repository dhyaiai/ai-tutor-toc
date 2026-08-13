import { useState, useRef, useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { questionService } from "../services/questionService";
import type { QuestionItem } from "../services/assignmentService";

/** 轮询间隔（毫秒） */
const POLL_INTERVAL = 3000;
/** 最大轮询时间（毫秒），与后端 1320s 重分析超时匹配，额外留 60s 余量 */
const MAX_POLL_TIME = 1_380_000;

/**
 * 重新分析 hook。
 * 发送请求后自动轮询作业详情，直到题目状态到达终态（completed/failed）或超时。
 */
export function useReanalysis(assignmentId: number) {
  const [reanalyzing, setReanalyzing] = useState<number | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const queryClient = useQueryClient();
  // 组件挂载标记：卸载后不再 setState / 发请求，避免轮询泄漏
  const mountedRef = useRef(true);
  // 轮询代次：每次 reanalyze 递增。旧轮询回调若正卡在 await refetch 中，
  // 醒来后凭代次发现自己已被新轮询取代，立即退出而不是误杀新定时器
  const pollEpochRef = useRef(0);

  /** 停止当前轮询定时器 */
  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // 组件卸载时停止轮询（A4-1：原实现卸载后 interval 仍持续 refetch + setState）
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  const reanalyze = useCallback(async (questionId: number, remark?: string) => {
    setReanalyzing(questionId);

    // 清理之前的轮询，并开启新一代轮询（旧回调即使正在 await 也会被代次淘汰）
    stopPolling();
    pollEpochRef.current += 1;
    const epoch = pollEpochRef.current;

    try {
      await questionService.reanalyze(questionId, remark);

      // 立即刷新一次
      queryClient.invalidateQueries({ queryKey: ["assignment", assignmentId] });
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });

      // 轮询等待后台任务完成
      const startTime = Date.now();
      let ticking = false; // 防止上一次 refetch 未完成时重叠执行
      pollTimerRef.current = setInterval(async () => {
        if (ticking) return;
        ticking = true;
        try {
          // 被新轮询取代或组件已卸载：不再读缓存、不更新状态
          if (epoch !== pollEpochRef.current || !mountedRef.current) return;

          const elapsed = Date.now() - startTime;

          // 超时停止
          if (elapsed >= MAX_POLL_TIME) {
            stopPolling();
            if (mountedRef.current) setReanalyzing(null);
            return;
          }

          // 先等 refetch 落地再读缓存，避免读到重分析前的旧状态导致过早停止轮询
          await queryClient.refetchQueries({ queryKey: ["assignment", assignmentId], exact: true });
          // refetch 期间可能已被新轮询取代/卸载，重新校验一次
          if (epoch !== pollEpochRef.current || !mountedRef.current) return;
          const data = queryClient.getQueryData<any>(["assignment", assignmentId]);
          const questions: QuestionItem[] = data?.questions || [];
          const targetQuestion = findQuestion(questions, questionId);
          // pending（排队中）/ processing（重分析进行中）都视为未完成，继续轮询
          if (targetQuestion && targetQuestion.status !== "pending" && targetQuestion.status !== "processing") {
            // 题目已完成（completed/failed），停止轮询
            stopPolling();
            if (!mountedRef.current) return;
            setReanalyzing(null);
            // 刷新列表与学情数据（详情已在上方 refetch 过）
            queryClient.invalidateQueries({ queryKey: ["assignments"] });
            queryClient.invalidateQueries({ queryKey: ["analytics"] });
          }
        } finally {
          ticking = false;
        }
      }, POLL_INTERVAL);
    } catch {
      if (mountedRef.current) setReanalyzing(null);
    }
  }, [queryClient, assignmentId, stopPolling]);

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
