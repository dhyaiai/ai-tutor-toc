/**
 * 带登录凭证的音频播放组件。
 *
 * 原生 <audio src=...> 无法附加 Authorization 头，而 files/ 端点下的
 * 私有目录（reports/、oral_audio/）强制校验登录态。因此私有音频必须先经
 * authedFetch 下载为 Blob，再用 objectURL 播放。
 */
import { useEffect, useRef, useState } from "react";
import { authedFetch } from "../utils/authedFetch";

interface AuthedAudioProps {
  /** 完整 URL（含 /api/v1/files/ 前缀） */
  src: string;
  className?: string;
}

export default function AuthedAudio({ src, className }: AuthedAudioProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const objectUrlRef = useRef<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    setUrl(null);

    authedFetch(src)
      .then((res) => {
        if (!res.ok) throw new Error(`加载音频失败 (${res.status})`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        // 延迟清理上一个 objectURL，避免正在播放的音频被中断
        const prevUrl = objectUrlRef.current;
        if (prevUrl) {
          setTimeout(() => URL.revokeObjectURL(prevUrl), 5000);
        }
        objectUrlRef.current = URL.createObjectURL(blob);
        setUrl(objectUrlRef.current);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
      // 组件卸载时延迟 revoke，避免音频播放中断
      const currentUrl = objectUrlRef.current;
      if (currentUrl) {
        setTimeout(() => URL.revokeObjectURL(currentUrl), 5000);
        objectUrlRef.current = null;
      }
    };
  }, [src]);

  if (error) {
    return <span style={{ color: "#999", fontSize: 13 }}>音频加载失败（可能需要重新登录）</span>;
  }
  return url ? <audio ref={audioRef} controls src={url} className={className} /> : null;
}
