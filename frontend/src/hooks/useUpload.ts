import { useState, useRef } from "react";
import { assignmentService } from "../services/assignmentService";

interface UploadParams {
  name: string;
  grade: string;
  subject: string;
  semester: string;
  month: string;
  layout_type?: string;
}

export function useUpload() {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const filePathRef = useRef<string | null>(null);

  const startFileUpload = async (file: File) => {
    setUploading(true);
    setProgress(0);
    filePathRef.current = null;
    try {
      const result = await assignmentService.uploadFile(file, setProgress);
      filePathRef.current = result.file_path;
      return result;
    } finally {
      setUploading(false);
    }
  };

  const submitAssignment = async (params: UploadParams) => {
    if (!filePathRef.current) {
      throw new Error("文件尚未上传完成");
    }
    return await assignmentService.upload({
      file_path: filePathRef.current!,
      ...params,
    });
  };

  const reset = () => {
    setProgress(0);
    filePathRef.current = null;
  };

  return { startFileUpload, submitAssignment, uploading, progress, reset };
}
