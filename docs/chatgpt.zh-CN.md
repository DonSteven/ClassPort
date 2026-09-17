# 使用 ChatGPT 管理 ClassPort

[English](./chatgpt.md) | [简体中文](./chatgpt.zh-CN.md)

ClassPort 保存课程、截止事项、学习进度和课程资料。ChatGPT 网页版通过
MCP 插件读取和更新数据，看板仍可直接编辑记录和上传文件。

## 浏览器连接

先部署公开 HTTPS 服务，将 `PUBLIC_URL` 设为公开域名，再按
[安装指南第 7 节](../INSTALL.md#7-connect-chatgpt)在
[ChatGPT 插件页](https://chatgpt.com/plugins)添加 `/mcp` 地址，选择 OAuth，
完成 ClassPort 登录和授权，安装插件，并在新对话中用 `@` 选择它。
是否可用取决于账户和工作区权限，不能仅凭订阅推断。

ClassPort 支持 DCR 动态注册和 PKCE-S256，无须填写静态客户端 ID 或密钥。
当前没有 CIMD 和刷新令牌支持，访问令牌过期后需重新连接。
可先问：“使用 ClassPort 读取我的设置、列出课程和本周截止事项。”
写入前检查操作，并按 ChatGPT 提示确认，随后在看板核对结果。

## 文件与学习

- 通过 ClassPort 的**文件**页，将 PDF 和笔记上传到 `STAT201/` 等课程目录。
  聊天附件不会自动存入 ClassPort，本地目录也不会自动同步。
- 让 ChatGPT 用 `list_course_files` 查找文件，再用 `read_course_file`
  读取返回路径。PDF 可指定 `pages="12"` 或 `pages="3-8"`，每次最多 20 页。
- PDF 同时返回带页码的文字和渲染图像。文字提取不是 OCR；对于扫描件和图表，
  如果模型没有收到图像，应将原 PDF 或页面图片直接附加到聊天中。
- 文件工具提供查询和读取。更新 `course.md` 时，先核对 ChatGPT 生成的新版，
  再通过看板上传替换文件。连接插件不会自动授权访问学校学习平台。
- 将[中文学习提示词](./chatgpt-study-prompt.zh-CN.md)粘贴到已启用插件的对话，
  可整理学期安排、记录讲次、讲解课件、标记学习进度和规划任务。

## 排错与资料依据

连接失败时检查公开 HTTPS、`PUBLIC_URL` 和 OAuth 元数据地址；令牌过期时，
在插件连接设置中重新登录。工具更新后刷新工具列表，并开启新对话。
提示词本身不授予权限。原生手机和桌面应用的支持需另行确认；本指南以网页版为准。

接入与鉴权步骤依据 [OpenAI 自定义 MCP 文档](https://developers.openai.com/api/docs/guides/custom-mcp-server)、
[插件快速入门](https://developers.openai.com/plugins/quickstart)和
[OAuth 文档](https://developers.openai.com/plugins/build/auth)。
图像处理参考[社区图像报告及 OpenAI 支持回复](https://community.openai.com/t/chatgpt-connector-returns-empty-for-mcp-tool-results-with-type-image-while-same-tool-works-in-mcp-inspector/1375446)，
重新授权参考[社区 OAuth 报告](https://community.openai.com/t/chatgpt-does-not-re-trigger-oauth-on-401-www-authenticate-for-mcp-tool-calls/1374168)。
这些资料已分别与当前代码的 PDF 输出及令牌行为核对；社区个案不代表所有账户的固定限制。
