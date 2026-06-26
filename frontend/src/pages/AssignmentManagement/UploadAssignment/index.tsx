import { useState, useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Card, Button, Typography } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import UploadModal from "../../../components/UploadModal";

export default function UploadAssignment() {
  const [modalOpen, setModalOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  // When navigating to this route, auto-open the modal
  useEffect(() => {
    setModalOpen(true);
  }, [location.key]);

  const handleSuccess = (assignmentId: number) => {
    setModalOpen(false);
    navigate(`/assignments/${assignmentId}`);
  };

  return (
    <Card>
      <Typography.Title level={4}>上传作业</Typography.Title>
      <Typography.Paragraph type="secondary">
        上传作业文件后，前往详情页手动启动题目切割与AI分析。
      </Typography.Paragraph>
      <Button
        type="primary"
        icon={<UploadOutlined />}
        size="large"
        onClick={() => setModalOpen(true)}
      >
        上传作业
      </Button>
      <UploadModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={handleSuccess}
      />
    </Card>
  );
}
