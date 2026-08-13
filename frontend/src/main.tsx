import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/soft-ui.css"; // 全局 Soft-UI 样式（纯 CSS 重构，见文件头注释）

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
