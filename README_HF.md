---
title: Gemini Web2API
emoji: 🔮
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Gemini Web2API - 中文汉化版

将 Google Gemini 网页端转换为 OpenAI 兼容 API 接口。零成本、跨平台、单文件。

## 功能特性

- **可选 API 密钥**：不配置时无需认证，配置后支持 OpenAI 风格的 Bearer 认证
- **OpenAI 兼容**：可直接替换 `/v1/chat/completions` 和 `/v1/models` 接口
- **工具调用**：完整的函数调用支持（OpenAI 格式）
- **多模型支持**：Flash、Flash Thinking（2万+字符输出）、Pro、Auto、Lite
- **思考深度**：通过 `@think=N` 后缀调节（0=最深，4=最浅）
- **网页搜索**：内置联网能力（Gemini 原生搜索）
- **跨平台**：纯 Python，仅一个可选依赖（`httpx` 用于流式传输）
- **流式传输**：通过 `httpx` 支持 SSE 流式输出
- **Codex CLI**：支持 OpenAI Codex 的 Responses API（`/v1/responses`）
- **Gemini CLI**：支持 Google 原生 API（`/v1beta/models`）

## 快速开始

服务启动后访问 `http://localhost:7860/v1`。

## 客户端配置

| 字段 | 值 |
| --- | --- |
| Base URL | `http://<你的地址>:7860/v1` |
| API Key | `config.json` 中 `api_keys` 的值；未配置时填任意值 |
| 模型 | `gemini-3.5-flash-thinking` |

## 可用模型

| 模型 | 描述 | 输出量 |
| --- | --- | --- |
| `gemini-3.5-flash` | 快速通用模型 | ~1.2万字符 |
| `gemini-3.5-flash-thinking` | 深度思考模式，最长输出 | **~2万字符** |
| `gemini-3.5-flash-thinking-lite` | 自适应深度思考 | ~1.5万字符 |
| `gemini-3.1-pro` | Pro 模型（需要 Cookie 才能真实路由） | ~1.2万字符 |
| `gemini-auto` | 自动模型选择 | 不定 |
| `gemini-flash-lite` | 轻量快速模型 | ~1万字符 |

## 注意事项

- 本服务部署在 Hugging Face Spaces 上，可直接访问 Google Gemini
- 如需 Pro 模型真实路由，请在 Space Settings 的 Secrets 中配置 Cookie
- `gemini_bl` 参数可能需要随 Google 更新而调整
