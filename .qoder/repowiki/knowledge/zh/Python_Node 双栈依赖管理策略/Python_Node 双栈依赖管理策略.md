---
kind: dependency_management
name: Python/Node 双栈依赖管理策略
category: dependency_management
scope:
    - '**'
source_files:
    - backend/requirements.txt
    - backend/requirements-core.txt
    - frontend/package.json
    - frontend/package-lock.json
    - start_dev.bat
---

本仓库采用 Python + Node.js 双技术栈，分别使用 pip 与 npm 进行第三方依赖管理，未引入 Poetry、Pipenv 或 pnpm/yarn 等替代工具。

后端（Python）
- 依赖声明：backend/requirements.txt 为完整生产依赖清单，backend/requirements-core.txt 为精简核心依赖（去掉了 opencv-python-headless、Pillow、aiohttp 等可选包），用于轻量环境。
- 版本锁定：所有包均使用 == 固定到具体版本号，确保可重现构建；对存在兼容冲突的包通过范围约束处理，如 bcrypt>=4.0,<5.0 以适配 passlib[bcrypt]==1.7.4。
- 安装方式：通过 pip install -r requirements.txt 安装，无虚拟环境自动创建脚本，需开发者自行维护 venv。
- 无 lockfile：未生成 requirements.lock 或 poetry.lock，依赖解析由 pip 在每次安装时完成。
- 无 vendoring：未使用 pip download --no-deps 或私有 PyPI 源，直接从官方 PyPI 拉取。

前端（Node.js）
- 依赖声明：frontend/package.json 中按 dependencies / devDependencies 分类声明，使用 ^ 语义化版本范围，允许小版本自动升级。
- 锁文件：frontend/package-lock.json（lockfileVersion 3）被提交至版本库，锁定精确子依赖树，保证 CI 与本地一致。
- 包管理器：使用 npm（非 yarn/pnpm），start_dev.bat 中通过 call npm install 安装依赖。
- 无私有源配置：未发现 .npmrc 或 registry 重定向，默认使用 npmjs.org。

跨层约定
- 前后端依赖完全解耦，各自独立管理，不存在共享 Python/Node 包。
- 开发启动脚本 start_dev.bat 会依次执行 pip install -r backend/requirements.txt 和 npm install，是统一的依赖安装入口。
- 未引入依赖安全扫描（如 pip-audit、npm audit）或自动化更新工具（如 Dependabot、Renovate）。