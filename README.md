# 深度研究助手（Python 后端）

一个前后端一体的深度研究智能体项目：
- 前端：`public/`（任务创建、SSE 实时进度、历史与导出）
- 后端：`FastAPI` + `LangGraph` 编排的 Multi-Agent 流程（Planner / Searcher / Analyst / Critic / Synthesizer）
- 安全配置中心：`/settings`（密钥仅存服务端）

## 功能

- LangGraph 状态图编排（StateGraph）
- SSE 流式进度（`snapshot / progress / done`）
- Multi-Agent / Single-Agent 模式切换
- 联网检索（`Tavily` / `SerpAPI`）
- 引用 URL 规范化与去重
- 任务历史持久化（`data/tasks/*.json`）
- Markdown 报告导出（`data/reports/*.md`）
- 模型通道切换（`OpenAI API` / 本地 OpenAI-compatible）

## 目录结构

```text
.
├── backend/
│   ├── app.py
│   ├── model_client.py
│   ├── research_agent.py
│   ├── search_client.py
│   └── task_store.py
├── public/
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── data/
│   ├── reports/
│   └── tasks/
├── .env.example
├── requirements.txt
└── run.py
```

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

打开 [http://localhost:3000](http://localhost:3000)

## API Key 安全配置（推荐）

1. 设置 `.env` 中的 `ADMIN_TOKEN`。
2. 启动服务后打开 `/settings`。
3. 使用 Admin Token 登录后保存各平台密钥。
4. 主页面不再直接输入密钥，研究任务自动读取服务端配置。

## 环境变量

```env
PORT=3000
ADMIN_TOKEN=change-this-token

OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1

LOCAL_MODEL_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_MODEL_API_KEY=local-not-required
LOCAL_MODEL_NAME=llama3.1

TAVILY_API_KEY=
SERPAPI_API_KEY=
```

## API

### 创建任务
`POST /api/research/tasks`

```json
{
  "topic": "2026年AI Agent商用化路径",
  "context": "面向中型SaaS团队",
  "userSources": ["https://example.com/report"],
  "depth": "standard",
  "agentMode": "multi",
  "provider": "openai",
  "searchProvider": "tavily",
  "searchMaxResults": 8
}
```

返回：

```json
{
  "taskId": "...",
  "status": "queued",
  "streamUrl": "/api/research/tasks/{id}/stream"
}
```

### 其他接口
- `GET /api/health`
- `GET /api/research/tasks`
- `GET /api/research/tasks/{id}`
- `GET /api/research/tasks/{id}/stream`
- `GET /api/research/tasks/{id}/markdown`
