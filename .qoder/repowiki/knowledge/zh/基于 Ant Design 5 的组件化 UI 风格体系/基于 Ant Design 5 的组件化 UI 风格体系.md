---
kind: frontend_style
name: 基于 Ant Design 5 的组件化 UI 风格体系
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/package.json
    - frontend/src/App.tsx
    - frontend/src/main.tsx
    - frontend/vite.config.ts
---

## 1. 使用的系统/方法
- UI 框架：Ant Design 5（antd@^5.22.0），通过 ConfigProvider 在应用根节点统一注入中文本地化（zh_CN）。
- 构建工具：Vite + React 18，TypeScript 项目，无自定义 CSS 预处理链。
- 图表库：@ant-design/charts，与 Ant Design 视觉语言保持一致。
- 图标：@ant-design/icons，遵循 Ant Design 图标规范。
- 样式方案：未引入任何第三方 CSS-in-JS 或原子化 CSS 框架（无 Tailwind、styled-components、Emotion、Less/SCSS 等），所有样式均依赖 Ant Design 内置主题与组件默认样式。

## 2. 关键文件与包
- frontend/package.json：声明 antd、@ant-design/charts、@ant-design/icons 等样式相关依赖。
- frontend/src/App.tsx：唯一使用 ConfigProvider 的地方，集中设置全局 locale；路由层包裹所有页面，是全局样式的入口点。
- frontend/src/main.tsx：应用挂载点，仅做 React StrictMode 包裹，不注入额外样式。
- frontend/vite.config.ts：构建配置中未发现自定义 CSS 插件或主题覆盖逻辑。
- 各业务组件直接消费 Ant Design 组件，未见自定义 CSS 文件或内联 style 对象的大量使用。

## 3. 架构与约定
- 单一主题源：全局主题由 App.tsx 中的 <ConfigProvider locale={zhCN}> 提供，当前仅设置了语言，未扩展 token/theme 变量，因此全应用采用 Ant Design 5 的默认浅色主题。
- 组件级样式：页面与组件以组合 Ant Design 原生组件为主，通过 props（如 type、size、variant）控制外观，而非编写独立 CSS。
- 布局结构：通过 Layout 子目录下的 AppLayout.tsx、AssignmentLayout.tsx、Header.tsx 组织整体骨架，配合 react-router-dom 嵌套路由实现多区域布局。
- 国际化：仅做了中文本地化，未实现多语言切换机制。
- 响应式：完全依赖 Ant Design 栅格与断点，未见媒体查询或自定义响应式策略。

## 4. 开发者应遵守的规则
1. 优先使用 Ant Design 组件及其 props 表达样式差异，避免手写 CSS 或内联 style 对象。
2. 如需定制主题，应在 App.tsx 的 ConfigProvider 中通过 theme 属性集中配置 token，禁止在各组件中分散覆盖。
3. 新增页面/组件时，保持与现有组件一致的 Ant Design 语义化命名（如 Card、Table、Form、Modal 等），确保视觉一致性。
4. 图表展示统一使用 @ant-design/charts，避免引入其他图表库造成风格割裂。
5. 图标统一从 @ant-design/icons 引入，不使用外部 SVG 资源或 emoji 替代。
6. 暂不引入 Tailwind、styled-components、Emotion、Less/SCSS 等样式方案，除非有明确的全局设计系统升级需求并经团队评审。