/**
 * 助教设置页面
 *
 * 功能：
 * - 提供自定义微调区（性格类型/说话风格/评分严格度）
 * - 配置对系统内所有 AI 批改统一生效
 * - 保存后实时生效，无需刷新
 */

import { useState, useEffect, useCallback } from "react";
import { Col, Row, Select, Slider, Typography, message, Spin, Divider } from "antd";
import {
  personalityService,
  type PersonalityConfig,
} from "../../services/personalityService";

const { Title, Text, Paragraph } = Typography;

/** 严格度等级说明 */
const STRICT_LEVEL_TIPS: Record<number, string> = {
  1: "极宽松 - 仅标注核心错误，评语以鼓励为主",
  2: "偏宽松 - 重点错误扣分，鼓励多于批评",
  3: "标准适中 - 按常规标准打分，客观公正",
  4: "偏严格 - 细节错误均扣分，要求明确",
  5: "极严格 - 对标升学考试标准，一针见血",
};

export default function PersonalityConfigPage() {
  const [config, setConfig] = useState<PersonalityConfig | null>(null);
  const [loading, setLoading] = useState(true);

  /** 加载用户配置 */
  useEffect(() => {
    (async () => {
      try {
        const cfg = await personalityService.get();
        setConfig(cfg);
      } catch {
        message.error("加载配置失败");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  /** 更新单个配置项 */
  const updateField = useCallback(
    async (field: string, value: string | number) => {
      if (!config) return;
      const updates = { [field]: value };
      try {
        const updated = await personalityService.update(updates);
        setConfig(updated);
      } catch {
        message.error("更新失败");
      }
    },
    [config]
  );

  if (loading) {
    return <div style={{ textAlign: "center", padding: 80 }}><Spin size="large" /></div>;
  }

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "24px 0" }}>
      <Title level={3}>🤖 AI 助教设置</Title>
      <Paragraph type="secondary">
        自定义 AI 助教的性格类型、说话风格和评分严格度，配置对系统内所有 AI 批改生效。配置保存后实时生效。
      </Paragraph>

      <Divider orientation="left">自定义微调</Divider>

      {/* 自定义微调区 */}
      {config && (
        <Row gutter={[24, 16]}>
          <Col xs={24} sm={12}>
            <Text strong>性格类型</Text>
            <Select
              value={config.personality_type}
              onChange={(v) => updateField("personality_type", v)}
              style={{ width: "100%", marginTop: 8 }}
              options={[
                { label: "温柔鼓励型", value: "温柔鼓励型" },
                { label: "严谨专业型", value: "严谨专业型" },
                { label: "幽默活泼型", value: "幽默活泼型" },
                { label: "严格督学型", value: "严格督学型" },
              ]}
            />
          </Col>

          <Col xs={24} sm={12}>
            <Text strong>说话风格</Text>
            <Select
              value={config.speaking_style}
              onChange={(v) => updateField("speaking_style", v)}
              style={{ width: "100%", marginTop: 8 }}
              options={[
                { label: "口语化亲切", value: "口语化亲切" },
                { label: "书面化正式", value: "书面化正式" },
                { label: "简洁高效", value: "简洁高效" },
              ]}
            />
          </Col>

          <Col xs={24} sm={12}>
            <Text strong>
              评分严格度：{config.strict_level}/5
            </Text>
            <Slider
              value={config.strict_level}
              onChange={(v) => updateField("strict_level", v)}
              min={1}
              max={5}
              marks={{ 1: "1", 2: "2", 3: "3", 4: "4", 5: "5" }}
              style={{ marginTop: 8 }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {STRICT_LEVEL_TIPS[config.strict_level]}
            </Text>
          </Col>
        </Row>
      )}
    </div>
  );
}
