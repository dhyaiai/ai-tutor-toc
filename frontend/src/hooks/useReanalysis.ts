import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { questionService } from "../services/questionService";

export function useReanalysis() {
  const [reanalyzing, setReanalyzing] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const reanalyze = async (questionId: number, remark?: string) => {
    setReanalyzing(questionId);
    try {
      const result = await questionService.reanalyze(questionId, remark);
      // 同步刷新作业详情、记录列表、学情分析
      queryClient.invalidateQueries({ queryKey: ["assignment"] });
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
      return result;
    } finally {
      setReanalyzing(null);
    }
  };

  return { reanalyze, reanalyzing };
}
