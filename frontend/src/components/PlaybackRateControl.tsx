/**
 * 播放速度控件 + 播放速度持久化 hook（讲解播报 / 听力 / 单词听写共用）。
 *
 * - usePlaybackRate：速度状态 + ref 双写（播放闭包需读最新值），并按 key 持久化到 localStorage
 * - PlaybackRateControl：0.75–1.5 步长 0.05 的滑动条 + 实时倍速文本
 */

import { useState, useRef, useCallback } from "react";
import { Slider, Space, Typography } from "antd";

const { Text } = Typography;

/** 播放速度持久化 localStorage key 前缀（各用途以 storageKey 区分记忆） */
const STORAGE_PREFIX = "oral_playback_rate_";

/** 读取已保存的播放速度（无效或未保存时返回默认值） */
function loadStoredRate(storageKey: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + storageKey);
    const v = raw == null ? NaN : parseFloat(raw);
    if (isFinite(v) && v >= 0.75 && v <= 1.5) return v;
  } catch { /* ignore */ }
  return fallback;
}

/**
 * 播放速度状态 Hook。
 *
 * @param storageKey 持久化 key（如 "listening" / "dictation" / "explain"），用户修改后保存，后续播放均沿用
 * @param defaultRate 未保存过时的默认播放速度，可在 0.75-1.5 间调整
 * @returns playbackRate 当前速度；setPlaybackRate 设置速度（持久化 + 同步 ref）；
 *          playbackRateRef 供播放闭包读取最新速度
 */
export function usePlaybackRate(storageKey: string, defaultRate = 1) {
  const [playbackRate, setPlaybackRateState] = useState(() =>
    loadStoredRate(storageKey, defaultRate)
  );
  const playbackRateRef = useRef(playbackRate);

  /** 调整播放速度（播放中即时生效，并持久化保存，后续播放沿用） */
  const setPlaybackRate = useCallback((rate: number) => {
    const v = Math.round(rate * 100) / 100;
    playbackRateRef.current = v;
    setPlaybackRateState(v);
    try { localStorage.setItem(STORAGE_PREFIX + storageKey, String(v)); } catch { /* ignore */ }
  }, [storageKey]);

  return { playbackRate, setPlaybackRate, playbackRateRef };
}

/**
 * 播放速度滑动条（0.75–1.5，步长 0.05，左右拖动调节，右侧实时显示当前倍速；播放中调整即时生效）。
 * 注意：调整速度时请把 audioRef.current.playbackRate 同步为最新值（usePlaybackRate 只负责状态与持久化）。
 */
export default function PlaybackRateControl({
  value,
  onChange,
  width = 120,
}: {
  value: number;
  onChange: (v: number) => void;
  width?: number;
}) {
  return (
    <Space size={4}>
      <Slider
        min={0.75}
        max={1.5}
        step={0.05}
        value={value}
        onChange={(v) => onChange(Math.round(v * 100) / 100)}
        tooltip={{ formatter: (v) => `${(v ?? 1).toFixed(2)}x` }}
        style={{ width, margin: "0 6px" }}
      />
      <Text type="secondary" style={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
        {value.toFixed(2)}x
      </Text>
    </Space>
  );
}
