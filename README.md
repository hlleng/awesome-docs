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

本仓库只包含文档助手的 Sphinx 使用端，不包含服务端运行代码。客户端源码位于：

- `source/_static/assistant/assistant.js`：请求 API、渲染消息和管理会话；
- `source/_static/assistant/assistant.css`：助手浮窗样式；
- `source/conf.py`：将 API 地址注入生成的 HTML。

页面加载上述源码，请求独立的 [docs-assistant 服务](https://github.com/hlleng/docs-assistant)：

```text
Sphinx 静态页面 → docs-assistant API
```

默认 API 地址为：

```text
https://chatbot.hlleng.xx.kg/api/docs-assistant
```

Sphinx 默认使用该地址；构建时可通过 `DOCS_ASSISTANT_API_URL` 覆盖，例如：

```bash
DOCS_ASSISTANT_API_URL="https://your-assistant.example.com/api/docs-assistant" make html
```

Read the Docs 项目如需切换助手地址，在 `Admin → Environment Variables` 中配置同名变量。服务端的 MCP/LLM 配置、会话数据、部署、备份和迁移均在独立仓库维护，不要写入本仓库的静态资源。

助手会话行为：

- 关闭并重新打开助手抽屉：继续当前会话；
- 刷新当前标签页：恢复当前会话历史；
- 新开标签页：创建新会话；
- 点击标题栏“新会话”：清空当前界面并开始新会话。

`client_id` 保存在浏览器 `localStorage`，`conversation_id` 保存在当前标签页的 `sessionStorage`。

服务端部署与数据迁移请参阅 [docs-assistant README](https://github.com/hlleng/docs-assistant)。

## 3. 参考设计

这个项目基于 Sphinx，更多信息见 https://www.sphinx-doc.org/en/master/。

## 4. 在线发布

基于 [Read the Docs](https://readthedocs.org/) 平台发布静态文档。文档助手 API 由独立服务仓库部署，RTD 构建阶段只运行 Sphinx。
