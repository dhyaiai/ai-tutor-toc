/**
 * TTS 音色偏好工具
 *
 * 助教设置中的「语音音色」（男声/女声）全局生效，
 * 覆盖助教讲解（ExplainCard）、英语听力、单词听写的 TTS 播报。
 *
 * 音色偏好从 /personality 接口读取（模块级缓存，每次页面会话只请求一次），
 * 助教设置页修改后通过 setVoiceTone 即时更新缓存，无需刷新页面。
 */

import { personalityService } from "../services/personalityService";

export type VoiceTone = "male" | "female";

/** 模块级缓存：避免每次播放都请求 /personality */
let cached: VoiceTone | null = null;
let fetching: Promise<VoiceTone> | null = null;

/** 获取当前用户的音色偏好（未配置/加载失败时默认女声） */
export async function getVoiceTone(): Promise<VoiceTone> {
  if (cached) return cached;
  if (!fetching) {
    fetching = personalityService
      .get()
      .then((cfg) => {
        cached = cfg.voice_tone === "male" ? "male" : "female";
        return cached;
      })
      .catch(() => "female" as VoiceTone)
      .finally(() => {
        fetching = null;
      });
  }
  return fetching;
}

/** 更新音色缓存（助教设置页保存后调用，播放侧即时生效） */
export function setVoiceTone(tone: VoiceTone) {
  cached = tone;
}

/**
 * 将基础语音名按音色偏好映射为后端 /oral/tts 的 voice 参数。
 * 女声保持原值（default/british/mixed 本身即女声），男声换用对应男声语音。
 */
export function resolveTtsVoice(base: string, tone: VoiceTone): string {
  if (tone !== "male") return base;
  switch (base) {
    case "default":
      return "male"; // en-US-GuyNeural 美式男声
    case "british":
      return "british_male"; // en-GB-RyanNeural 英式男声
    case "mixed":
      return "mixed_male"; // zh-CN-YunyangNeural 中文男声（中英混读）
    default:
      return base; // 已是男声或未知语音，原样返回
  }
}

/** 一步获取按用户音色偏好解析后的 voice 参数 */
export async function getTtsVoice(base: string): Promise<string> {
  return resolveTtsVoice(base, await getVoiceTone());
}
