import { useState, useRef } from "react";
import { assignmentService } from "../services/assignmentService";

interface UploadParams {
  name: string;
  grade: string;
  subject: string;
  semester: string;
  usage_month: string;
  layout_type?: string;
}

export function useUpload() {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  /** 多文件预上传后的路径列表（按用户排列的顺序） */
  const filePathsRef = useRef<string[]>([]);
  /**
   * 上传批次代次：每次 startFilesUpload 递增。
   * 上传中若并发启动了新批次（如排序/删除文件），旧批次的进度回调与结果全部丢弃，
   * 避免旧批次完成时把 uploading 置 false（进度条提前消失）或把 filePathsRef
   * 覆盖成含已删除文件的旧集合（提交时文件缺失）。
   */
  const uploadEpochRef = useRef(0);

  /**
   * 逐个预上传文件，收集所有 file_path。
   * 文件顺序由 files 数组顺序决定（调用方保证 = 用户排列的顺序）。
   */
  const startFilesUpload = async (files: File[]) => {
    const epoch = ++uploadEpochRef.current;
    setUploading(true);
    setProgress(0);
    const paths: string[] = [];

    try {
      for (let i = 0; i < files.length; i++) {
        const result = await assignmentService.uploadFile(files[i], (pct) => {
          // 已过期批次（期间又启动了新上传）的进度回调直接丢弃
          if (epoch !== uploadEpochRef.current) return;
          // 每个文件占 1/N 的进度，当前文件内部的 pct 按比例折算
          const overall = Math.round(((i + pct / 100) / files.length) * 100);
          setProgress(overall);
        });
        paths.push(result.file_path);
      }

      // 仅最新批次写回路径列表（旧批次的结果不覆盖新批次的顺序）
      if (epoch !== uploadEpochRef.current) return paths;
      filePathsRef.current = paths;
      setProgress(100);
      return paths;
    } finally {
      // 仅最新批次复位上传状态；旧批次结束时不再触碰 uploading，
      // 否则并发场景下旧批次先结束会把 uploading 提前置 false
      if (epoch === uploadEpochRef.current) {
        setUploading(false);
      }
    }
  };

  /**
   * 提交作业元数据 + 预上传的文件路径。
   * 多文件时发送 file_paths 数组，单文件时发送 file_path。
   */
  const submitAssignment = async (params: UploadParams) => {
    if (filePathsRef.current.length === 0) {
      throw new Error("文件尚未上传完成");
    }
    // 构造符合 assignmentService.upload 参数类型的 payload
    const payload: {
      name: string;
      grade: string;
      subject: string;
      semester: string;
      usage_month: string;
      layout_type?: string;
      file_path?: string;
      file_paths?: string[];
    } = { ...params };
    if (filePathsRef.current.length > 1) {
      payload.file_paths = filePathsRef.current;
    } else {
      payload.file_path = filePathsRef.current[0];
    }
    return await assignmentService.upload(payload);
  };

  const reset = () => {
    setProgress(0);
    filePathsRef.current = [];
  };

  return { startFilesUpload, submitAssignment, uploading, progress, reset };
}
