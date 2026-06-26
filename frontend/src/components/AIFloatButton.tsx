import { FloatButton } from "antd";
import { RobotOutlined } from "@ant-design/icons";

interface Props {
  onClick: () => void;
}

export default function AIFloatButton({ onClick }: Props) {
  return (
    <FloatButton
      icon={<RobotOutlined />}
      type="primary"
      tooltip="AI 助手"
      onClick={onClick}
      style={{ right: 24, bottom: 24 }}
    />
  );
}
