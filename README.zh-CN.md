<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/wordmark/on-dark.svg" />
    <img src="docs/brand/wordmark/on-light.svg" alt="ClassPort" width="400" />
  </picture>
</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

# ClassPort

课程安排、学习主题日期、看板日历和时间输入统一使用**美国东部时间（`America/New_York`）**，自动处理冬令时和夏令时（EST/EDT）；时间戳以 UTC 存储和传输。

一个可自行托管的个人学习看板。在同一处管理**课程、课表、讲次、学习主题、作业和任务**，通过 **ChatGPT 网页版**与 MCP 阅读课程资料、更新学习安排。

## 使用方式

### 首次设置

我使用 ChatGPT 网页版整理课程资料，再通过 ClassPort 保存学期安排。

1. **收集原始资料。** 从学校学习平台下载课程大纲、课表、考试规则和讲义。可以按课程保存在本地，但 ChatGPT 不会自动读取电脑上的目录。
2. **让 ChatGPT 整理 `course.md`。** 将相关文件附加到对话，要求整理课程代码、全名、学分、教师、语言、考试形式、每周课表、评分和出勤要求。核对原始资料后，下载生成的 Markdown。
3. **将文件保存到 ClassPort。** 通过看板的**文件**页上传 PDF 和 `course.md`，放在 `STAT201/` 等课程目录下。聊天附件不会自动存入 ClassPort，本地目录新增文件也不会自动上传。
4. **连接 ChatGPT 与 ClassPort。** 按照 [ChatGPT 使用指南](./docs/chatgpt.zh-CN.md)完成连接，在对话中用 `@` 选择已安装的 ClassPort 插件，要求读取上传的 `course.md`。先核对拟创建的条目，再让 ChatGPT 调用 `create_course`、`create_schedule_slot`、`update_exam` 和 `create_deliverable`。课程的 `folder_name` 应与上传目录一致；写入操作可能需要在 ChatGPT 中确认。
5. **检查学习看板。** 课程、课表、考试和截止事项已保存，后续对话与看板读取同一份数据。

**之后只需增量更新。** 新课件通过**文件**页上传。评分规则或考试日期变化时，让 ChatGPT 更新对应记录。需要修改 `course.md` 时，核对重新生成的 Markdown，再通过看板上传替换文件。

### 一周的学习流程

**上课时。** 在连接了 ClassPort 的 ChatGPT 网页对话中说：“使用 ClassPort 读取 `STAT201/homework-05.pdf` 第 4 题，解释独立同分布的指数随机变量之和对应的 Gamma 分布，以及它的形状参数和尺度参数。”ChatGPT 使用 `list_course_files` 和 `read_course_file` 获取资料。PDF 工具同时返回页面文字和渲染图像；若图表或扫描页未进入模型上下文，需将原 PDF 或页面图片直接附加到对话后再讨论视觉细节。

**下课后。** “STAT201 今天讲了 Gamma 分布、基于中心极限定理的正态近似，以及二项比例估计。请记录讲次和这三个主题，并标记到课。”ChatGPT 调用讲次与主题工具，写入成功后看板即可看到结果。

**学习时。** “在 ClassPort 中找到尚未学习的 STAT201 主题，结合已上传作业的第 7 题，解释二项比例估计量 p̂ = X/n 为什么具有无偏性和一致性，以及如何得到它的渐近正态分布，中途问我一道检查理解的问题。”确认掌握后，说“将‘二项比例估计’标记为已学习”，由 `mark_studied` 保存进度。

**安排明天。** “本周有什么即将截止的事项？为 STAT201 添加高优先级任务：周一 16:00 前，用 R 绘制 n = 10、20、30、40、50 时 Gamma 分布及其正态近似的 PDF 和 CDF 对比图。”ChatGPT 先读取用户设置和时区，再解释日期并保存任务。

看板与连接后的 ChatGPT 对话使用同一数据库，也可以直接在看板中创建和编辑各类记录。


## 项目特点

MCP 服务让 **ChatGPT 读取和更新学习记录**，包括课程、课表、讲次、主题、截止事项和任务，也可查询已上传的文件并读取指定 PDF 页面。上传和替换文件通过看板的**文件**页完成。

在 **ChatGPT 网页版**中添加通过 OAuth 授权的 MCP 插件，并在对话中选择 ClassPort，即可使用看板中的同一份课程数据。接入步骤和文件操作见 [ChatGPT 使用指南](./docs/chatgpt.zh-CN.md)。

## 环境要求

开始前需要准备：

- **Docker** 和 **Docker Compose v2.30+**，用于运行 PostgreSQL、FastAPI 后端和前端容器
- **Node 20+** 和 **pnpm**，可通过 [corepack](https://pnpm.io/installation#using-corepack) 安装，用于构建前端
- **可公开访问的 HTTPS 域名**，供 ChatGPT 访问 MCP；账户和工作区需允许添加自定义 MCP 插件
- 首次配置预计需要 **15 分钟左右**

PostgreSQL、FastAPI 后端和静态前端都运行在同一台机器上。只要支持 Docker 即可，小型 VPS 也足够运行。

## 快速开始

在本机启动完整应用。生产环境先构建前端，再由 Caddy 提供静态文件；开发时可使用 `pnpm dev` 连接容器中的后端。

**1. 克隆仓库并安装前端依赖。**

```bash
git clone https://github.com/DonSteven/ClassPort
cd ClassPort
cd web && pnpm install && cd ..
```

**2. 分别配置运行和维护凭证。**

```bash
cp .env.example .env
cp .env.migration.example .env.migration
cp .env.docker.example .env.docker
cp .env.jobs.example .env.jobs
chmod 600 .env .env.migration .env.docker .env.jobs
```

将 `DATABASE_URL`、`SESSION_SECRET` 和 `SECRETS_ENCRYPTION_KEY` 写入 `.env`；将 `MIGRATION_DATABASE_URL`、`OPERATOR_EMAIL` 和 `APP_PASSWORD_HASH` 写入 `.env.migration`。使用 `uv run python -m app.tools.hashpw` 生成密码哈希。

两个 URL 指向同一数据库，但使用不同角色。后端读取 `.env` 和 `.env.jobs` 中的共享任务设置，维护任务只读取 `.env.migration`；`.env.docker` 保存本地 PostgreSQL 凭证与公开的前端构建配置。

[数据库配置指南](docs/DATABASE.md) 介绍首次创建角色、已有环境接入、连接池，以及 RDS 的 TLS 连接设置。

**3. 启动应用。**

```bash
./deploy.sh
```

`deploy.sh` 验证 Compose 配置、构建 `classport` 镜像、使用维护凭证完成迁移和账户初始化、启动前后端，并检查 `GET /api/health` 的就绪状态，最多等待 60 秒。就绪检查未通过时会自动回退到上一个镜像。

使用 RDS 或外部 PostgreSQL 时，每次部署执行 `./deploy.sh --external-db`。

后端现在运行于 `http://127.0.0.1:8000`，可这样验证：

```bash
curl -f --max-time 3 http://127.0.0.1:8000/api/health/live
# → {"ok":true,"version":"..."}
curl -f --max-time 3 http://127.0.0.1:8000/api/health/ready
# → {"ok":true,"version":"...","db":"ok","storage":"ok"}
```

存活检查只确认应用能够响应；就绪检查在总计 2 秒的超时内检查数据库，以及 `STUDY_ROOT` 是否可读。失败或超时返回 HTTP 503。`/api/health` 执行相同的就绪检查，并保留供旧镜像的部署和回退流程使用。Docker 也使用就绪状态；`unhealthy` 本身不会自动重启容器，也不会阻止代理转发流量。

**4. 开始使用。**

前端也运行在容器中，`deploy.sh` 已将它启动在 `http://127.0.0.1:8080`。在浏览器中打开并登录。使用公开域名时，将 Caddy、nginx 等反向代理指向 `127.0.0.1:8080`；容器中的 Caddy 会分别处理静态资源和 API/MCP 请求。

本地开发前端并使用 Vite 热更新：

```bash
echo "VITE_API_BASE_URL=http://localhost:8000" > web/.env.local
cd web && pnpm dev   # → http://localhost:5173
```

遇到问题或需要生产部署清单时，请查看完整的英文安装指南 **[INSTALL.md](./INSTALL.md)**。

## 应用内的操作

首次启动时数据为空，可通过界面或已启用 ClassPort 插件的 ChatGPT 对话逐步创建：

1. **设置 → 个人资料**：姓名、头像缩写和学校
2. **设置 → 学期**：名称（例如“2026 春季学期”）、开始和结束日期、时区（默认 `America/New_York`）和地区格式
3. **课程 → 添加**：课程代码（如 STAT201、CS101）、全名和强调色
4. **课程详情**：课表时段（星期、时间、教室）、待交作业和学习主题
5. **学习概览**：问候、进度提醒、指标、每周课表、课程卡片、截止事项和任务

## 连接 ChatGPT

ClassPort 在 `https://your-domain.tld/mcp` 提供受 OAuth 保护的 Streamable HTTP MCP 接口，供 **ChatGPT 网页版**调用。部署时配置 HTTPS，并将 `PUBLIC_URL` 设为同一公开域名。本地看板运行成功，并不代表 ChatGPT 能访问 `localhost`。

打开 [ChatGPT 插件页](https://chatgpt.com/plugins)，选择 **+ → Add custom MCP server**，填写接口地址并选择 **OAuth**。ClassPort 支持动态客户端注册（DCR），无须填写静态客户端 ID 或密钥。完成 ClassPort 登录和授权，安装生成的插件，再在新对话中用 `@` 选择它。能否添加和使用取决于账户与工作区权限。参见 [OpenAI 官方接入说明](https://developers.openai.com/api/docs/guides/custom-mcp-server)。

先试：“使用 ClassPort 列出我的课程和本周截止事项。”需要写入时，先检查拟执行的操作，并按 ChatGPT 提示确认；随后打开看板核对是否保存。

文件使用方式、OAuth 排错和官方文档及社区参考见 [ChatGPT 使用指南](./docs/chatgpt.zh-CN.md)。可将[学习助手提示词](./docs/chatgpt-study-prompt.zh-CN.md)粘贴到已启用插件的对话中；提示词本身不会安装插件或完成授权。


## 仓库结构

```
app/                FastAPI 与 MCP 服务，使用 uv 管理 Python 依赖
  routers/          HTTP 接口
  services/         数据库查询与业务逻辑
  mcp_tools.py      MCP 工具
  schemas.py        Pydantic 数据模型
migrations/         部署时由 scripts/run_migrations.py 应用的 SQL 文件
  00000000000000_baseline.sql  初始多用户结构，后续迁移在此基础上叠加
web/
  src/              Vite、React 19、Tailwind、shadcn/ui 前端
scripts/
  run_migrations.py 幂等迁移工具，记录已应用迁移的校验值
docker-compose.yml  内部网络中的 PostgreSQL 与 FastAPI
Dockerfile          构建 classport:latest 镜像
deploy.sh           构建 → 迁移 → 切换 → 健康检查 → 自动回退
docs/
  chatgpt.md                   英文 ChatGPT 使用指南
  chatgpt.zh-CN.md             中文 ChatGPT 使用指南
  chatgpt-study-prompt.md       英文学习助手提示词
  chatgpt-study-prompt.zh-CN.md 中文学习助手提示词
```

## 技术栈

同一台服务器上的三个容器，通过反向代理提供访问：

- **前端：** Vite、React 19、TypeScript、Tailwind 与 shadcn/ui，构建为 Caddy:alpine 镜像，提供静态页面，并通过内部 Docker 网络转发 API 请求
- **后端：** FastAPI（Python 3.12），运行单个 uvicorn worker，使用 `psycopg` 异步连接池直接访问 PostgreSQL
- **数据：** PostgreSQL 16，仅连接内部网络，不向主机公开端口
- **文件：** 存放在 `STUDY_ROOT`（默认 `/opt/courses`）指定的文件系统中，并挂载到 FastAPI 容器
- **MCP：** Python `mcp` SDK，通过 `/mcp` 提供带 OAuth 2.1 的 Streamable HTTP 服务
- **部署：** 支持 Docker 的环境均可；由主机上的反向代理处理 TLS，示例见 [INSTALL.md](./INSTALL.md)

## 设计

界面采用暖色调、衬线标题和课程强调色，提供多种学习看板主题，可在看板设置中切换。

## 许可证

采用 MIT 许可证。欢迎注明来源、收藏项目或提供链接，但这些并非额外要求。

## 参与贡献

欢迎提交 Issue 和 PR。自行部署时遇到故障、交互问题或功能需求，都可以反馈。修正文档、补充安装说明、增加 MCP 工具或改善手机布局，都是有用的贡献。

如果不确定较大改动是否符合项目范围，可以先开一个 Issue，简要说明计划并询问是否接受相应 PR。
