# awesome-docs 使用手册

[**在线文档预览（latest）**](https://awesome-edge-docs.readthedocs.io/zh-cn/latest/index.html)

[**DEV 在线预览**](https://axera-tech.github.io/awesome-docs/)（由 `dev` 分支自动部署到 GitHub Pages）

## 1. 项目背景

**awesome-docs** 用于整理 AXERA 产品相关的软件、硬件、工具链和示例资料，方便社区开发者和商用客户查阅与评估。

当前文档由原有 AX650 系列 NPU 使用说明扩展而来，后续覆盖：

- [边缘计算产品线](https://www.axera-tech.com/zh-hans/product/edge-aI-inference?q=zh-hans/product/edge-aI-inference)

## 2. 本地编译指南

### 2.1 git clone

```bash
git clone -b dev https://github.com/AXERA-TECH/awesome-docs.git
cd awesome-docs
```

### 2.2 编译中文文档

安装依赖：

```bash
pip install -r requirements.txt
```

在项目根目录下执行：

```bash
make clean
make html
```

编译后，使用浏览器打开 `build/html/index.html`。

### 2.3 编译英文文档

英文文档通过 Sphinx gettext 工作流生成：

```bash
# 提取可翻译文本并更新英文 .po 文件
make intl

# 翻译 locale/en/LC_MESSAGES/*.po 中的 msgstr

# 编译英文文档
make html-en
```

编译后，使用浏览器打开 `build/html-en/index.html`。

### 2.4 本地预览

```bash
make serve
```

默认访问地址：`http://localhost:8200/`。

### 2.5 文档智能助手

助手采用独立的 Python API，页面端只加载仓库中的 `assistant/assistant.js`，请求链路如下：

```text
Sphinx 静态页面 → docs-assistant API → RAG_Retrieval MCP → LLM
```

现有 MCP 是唯一的检索源，服务不会再维护第二套知识库。当前生产 API 地址为：

```text
https://chatbot.hlleng.xx.kg/api/docs-assistant
```

Sphinx 默认使用该地址；构建时可通过 `DOCS_ASSISTANT_API_URL` 覆盖，例如：

```bash
DOCS_ASSISTANT_API_URL="https://your-assistant.example.com/api/docs-assistant" make html
```

Read the Docs 项目如需覆盖默认值，在 `Admin → Environment Variables` 中配置同名变量。MCP/LLM 地址和密钥只放在 docs-assistant 服务器，不要写入静态页面。

本地开发使用当前 Python MCP/LLM 适配服务：

```bash
make serve
```

`make serve` 会同时启动文档服务器 `8200` 和助手 API `8300`，助手默认监听局域网接口，并在 API 健康检查通过后开放预览。因此可从本机或局域网浏览器访问 `http://<本机IP>:8200/`。如果本机有多个网卡，可显式指定页面使用的 API 主机：

```bash
make serve DOCS_ASSISTANT_PUBLIC_HOST=10.0.0.10
```

首次使用前安装 docs-assistant 运行依赖（`requirements.txt` 已通过 `-r` 包含它们）：

```bash
python3 -m pip install -r requirements.txt
```

`make serve` 使用 Uvicorn 单 worker 启动 API，并将会话数据库写入本地被忽略的 `.docs-assistant/docs-assistant.sqlite3`。MCP/LLM 地址和凭据可写入被 Git 忽略的 `tools/docs_assistant/local_config.py`，或通过 `DOCS_ASSISTANT_*` 环境变量注入；环境变量优先级更高。
如果浏览器运行在另一台机器上，还需要确保该机器可以访问助手端口 `8300`；只开放文档端口 `8200` 不足以完成提问。

如果 MCP 需要绕过本地代理：

```bash
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}your-mcp-host.example.com"
export no_proxy="$NO_PROXY"
```

生产 MCP 必须使用稳定 HTTPS 域名和认证，不要依赖临时 `trycloudflare.com` quick tunnel。

### 2.6 部署 docs-assistant API

将 `tools/docs_assistant` 部署到公网服务器，并由 Caddy/Nginx 通过 HTTPS 域名反向代理。当前验证的域名为：

```text
https://chatbot.hlleng.xx.kg
```

RTD 项目环境变量配置为：

```text
DOCS_ASSISTANT_API_URL=https://chatbot.hlleng.xx.kg/api/docs-assistant
```

API 服务器设置 `DOCS_ASSISTANT_ALLOWED_ORIGINS=https://awesome-edge-docs.readthedocs.io`，并配置现有 MCP、Collection 和 OpenAI-compatible LLM。会话历史保存在 `DOCS_ASSISTANT_DB_PATH` 指定的 SQLite 文件中，默认保留 30 天、最近 24 条消息；生产容器应将该文件放到可写的持久化卷，例如 `/data/docs-assistant.sqlite3`。该服务只负责编排检索与生成，不需要重新建立知识库。

可以使用仓库提供的镜像构建文件部署单个 API 容器：

```bash
docker build -f Dockerfile.docs-assistant -t axera-docs-assistant:latest .
```

运行时将 `/data` 挂载为可写目录，并把 `DOCS_ASSISTANT_MCP_*`、`DOCS_ASSISTANT_LLM_*` 和 `DOCS_ASSISTANT_ALLOWED_ORIGINS` 作为环境变量或 Secret 注入。默认 Uvicorn 配置为 `--workers 1`，适合 SQLite 单实例部署；扩展到多实例时应改用共享 PostgreSQL/Redis 存储。

## 3. 参考设计

这个项目基于 Sphinx，更多信息见 https://www.sphinx-doc.org/en/master/。

## 4. 在线发布

基于 [Read the Docs](https://readthedocs.org/) 平台发布静态文档，docs-assistant API 在独立公网服务器运行。RTD 构建阶段只运行 Sphinx，不启动 `tools.docs_assistant.server`。
