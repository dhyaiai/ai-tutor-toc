import React from "react";
import { Button, Result, Typography } from "antd";

/**
 * 全局错误边界：拦截渲染期/生命周期异常，避免整页白屏。
 *
 * 背景：React 渲染错误没有兜底时会卸载整棵组件树（白屏），用户无法反馈、
 * 开发者无法定位。此边界将错误转成可见的错误页（控制台仍有完整堆栈），
 * 并提供"刷新页面"入口恢复。
 *
 * 只负责展示，不修复错误本身 —— 出现错误时请查看控制台报错并修复根因。
 */
export default class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 完整堆栈在控制台，错误页只展示概要
    console.error("[AppErrorBoundary] 页面渲染异常:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#f5f5f5",
          }}
        >
          <Result
            status="error"
            title="页面出现异常"
            subTitle={
              <Typography.Paragraph type="secondary" style={{ maxWidth: 560, marginBottom: 0 }}>
                {this.state.error.message || String(this.state.error)}
                <br />
                <span style={{ fontSize: 12 }}>
                  详细堆栈已输出到浏览器控制台（F12），如问题可复现请一并反馈。
                </span>
              </Typography.Paragraph>
            }
            extra={
              <Button
                type="primary"
                onClick={() => {
                  this.setState({ error: null });
                  window.location.reload();
                }}
              >
                刷新页面
              </Button>
            }
          />
        </div>
      );
    }
    return this.props.children;
  }
}
