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
   * 逐个预上传文件，收集所有 file_path。
   * 文件顺序由 files 数组顺序决定（调用方保证 = 用户排列的顺序）。
   */
  const startFilesUpload = async (files: File[]) => {
    setUploading(true);
    setProgress(0);
    filePathsRef.current = [];
    const paths: string[] = [];

    try {
      for (let i = 0; i < files.length; i++) {
        const result = await assignmentService.uploadFile(files[i], (pct) => {
          // 每个文件占 1/N 的进度，当前文件内部的 pct 按比例折算
          const overall = Math.round(((i + pct / 100) / files.length) * 100);
          setProgress(overall);
        });
        paths.push(result.file_path);
      }

      filePathsRef.current = paths;
      setProgress(100);
      return paths;
    } finally {
      // 无论成功失败都复位上传状态：失败时页面可提示并重试，
      // 而不是卡在全屏 loading（原实现 throw 会跳过 setUploading(false)）
      setUploading(false);
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
