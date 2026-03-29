# SQL Doctor

基于 **FastAPI + LangGraph + sqlglot** 的 SQL 诊断与优化脚手架：解析 SQL、（可选）拉取 EXPLAIN、规则分析、改写建议，并支持 **FAISS RAG 知识库** 增强大模型输出。提供 **Streamlit** 对话界面与 **Next.js** 前端目录。

## 功能概览

- **工具链分析**（无 LLM）：`LangGraph` + `@tool` 串联解析、执行计划、规则建议、保守改写。
- **RAG + LLM 诊断**：FAISS 检索慢 SQL 案例 / 索引规则 / 组织经验后，由大模型输出结构化 JSON（`issues` / `suggestions` / `optimized_sql`）。
- **多库 EXPLAIN**：MySQL / PostgreSQL / Oracle（`db/db_client.py`，结构化 `steps`：`type`、`key`、`rows`、`extra`）。
- **执行计划规则分析**：`analyzer/plan_analyzer.py`（全表扫描、未走索引、大行数、filesort、temporary 等）。
- **SQL 改写引擎**：`optimizer/rewrite_engine.py`（列目录展开 `*`、LIMIT、逗号 JOIN 提升、索引建议注释等）。

## 技术栈

| 层级 | 技术 |
|------|------|
| API | Python 3.11+（推荐）、FastAPI、Uvicorn |
| Agent | LangGraph、LangChain、异步工具节点 |
| 解析 | sqlglot |
| 数据库 | SQLAlchemy 2.0 async、Redis（可选） |
| 向量库 | FAISS、`langchain-community`；向量模型默认 HuggingFace `sentence-transformers` |
| LLM | OpenAI 兼容 API（Ollama / vLLM 等） |
| UI | Streamlit（`ui/app.py`）、Next.js 14（`frontend/`） |

## 目录结构（摘要）

```text
SQLDoctor/
├── app_exception.py      # 统一应用异常
├── backend/              # FastAPI、路由、配置、服务
├── agent/                # LangGraph、SqlAgent、工具
├── analyzer/             # 解析、计划分析
├── optimizer/            # 建议、改写引擎
├── db/                   # 异步 DB、Redis、ExplainDbClient
├── kb/                   # RAG：种子文档、ingest、FAISS、检索
├── ui/                   # Streamlit
├── frontend/             # Next.js（可选）
├── requirements.txt
└── pyproject.toml
```

## 环境要求

- **Python**：建议 **3.11 或 3.12**。3.14 可用但 LangChain 可能提示 Pydantic V1 兼容性警告。
- **可选**：本机 MySQL / PostgreSQL / Oracle 连接串，用于真实 EXPLAIN；无库时部分接口仍返回跳过说明。
- **RAG**：首次会下载向量模型并构建索引，耗时与磁盘占用取决于模型（默认 `all-MiniLM-L6-v2`）。

## 安装

在项目根目录执行（建议使用虚拟环境）：

```bash
python -m pip install -r requirements.txt
```

**RAG / 知识库**：`langchain-huggingface`、`sentence-transformers`、`faiss-cpu` 等已写在 `requirements.txt` 中；必须用**与启动 uvicorn 相同的 Python** 安装（例如 `py -3 -m pip install -r requirements.txt`）。若暂时不需要 RAG，可在 `.env` 中设 `KB_ENABLED=false` 跳过向量模型与索引加载。

前端（可选）：

```bash
cd frontend
pnpm install
```

## 环境变量（`.env` 可选）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | 异步连接串，如 `mysql+aiomysql://user:pass@host:3306/db` |
| `REDIS_URL` | 如 `redis://127.0.0.1:6379/0`，不配则内存缓存 |
| `API_PORT` | 后端监听端口，默认 **8010**（避免部分 Windows 上 **8000** 触发 `WinError 10013`） |
| `SQLDOCTOR_RELOAD` | 设为 `1` 时 `run.bat` 为后端加上 `--reload`（默认关闭，减少 Windows 套接字错误） |
| `SQLDOCTOR_HF_OFFICIAL` | 设为 `1` 时**不**使用默认镜像，未设 `HF_ENDPOINT` 则仍走官方 `huggingface.co`（可直连外网时用） |
| `LLM_MODEL` | 大模型名（RAG 诊断必填）；Ollama 填 `ollama list` 里的 id，如 `deepseek-r1:1.5b` |
| `OLLAMA_BASE_URL` | 默认 `http://127.0.0.1:11434`，会自动拼 `/v1` 给 Chat 用 |
| `LLM_OPENAI_BASE_URL` | 覆盖完整 OpenAI 兼容根 URL（含 `/v1`） |
| `LLM_API_KEY` | API Key；Ollama 可占位 |
| `HF_ENDPOINT` | Hugging Face 端点根 URL；未设置且未指定 `SQLDOCTOR_HF_OFFICIAL=1` 时，程序与 `run.bat` **默认**使用 `https://hf-mirror.com`；可直连官方站时设 `SQLDOCTOR_HF_OFFICIAL=1` 或显式写 `HF_ENDPOINT=https://huggingface.co` |
| `KB_ENABLED` | `true`/`false`，是否加载 FAISS 知识库 |
| `KB_FAISS_PATH` | 索引目录，默认 `data/kb_faiss` |
| `KB_SEED_PATH` | 种子 Markdown 目录，默认 `kb/seed` |
| `KB_TOP_K` | 检索条数 |
| `KB_EMBEDDING_MODEL` | 向量模型 id |
| `KB_USE_OPENAI_EMBEDDINGS` | 是否改用 OpenAI 兼容 Embeddings API |
| `KB_OPENAI_EMBEDDING_BASE_URL` / `KB_OPENAI_EMBEDDING_API_KEY` | 向量 API |

字段名与 `backend/config.py` 中 `Settings` 一致（Pydantic Settings 自动读环境变量）。

## 运行

**1）启动 API（项目根目录）**

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010 --reload
```

首次若不想构建知识库，可临时：

```bash
# Windows PowerShell
$env:KB_ENABLED="false"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
```

- 交互文档：<http://127.0.0.1:8010/docs>  
- 健康检查：`GET /api/health`

**2）Streamlit 界面**

```bash
python -m streamlit run ui/app.py
```

侧栏可切换「工具链」与「RAG + LLM」模式；需将 API Base 指向上述服务地址。

**3）重建 FAISS 索引（可选）**

```bash
python -m kb
# 或
python -m kb.rebuild
```

**4）Next.js（可选）**

```bash
cd frontend
pnpm dev
```

开发模式下前端页面**默认直连** `http://127.0.0.1:8010/api/...`（与 `API_PORT` 一致），避免仅依赖 rewrite 时后端未起而返回 HTML 500 整页。`next.config` 仍保留 `/api/*` rewrite 供生产同源反代。改端口：`API_PORT` + 重启 `pnpm dev`，或设 `NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:你的端口`。

### Windows：`WinError 10013`（套接字访问被拒绝）

1. **`run.bat` 默认已关闭 `--reload`**：Uvicorn 在 Windows 上带热重载时，重载进程会额外占用套接字，不少环境会报 10013。需要热重载时在运行前执行 `set SQLDOCTOR_RELOAD=1`（PowerShell：`$env:SQLDOCTOR_RELOAD="1"`），并已安装依赖里的 **`watchfiles`**（对重载更友好）。  
2. **端口**：若关闭重载后仍报错，再换端口：`set API_PORT=9020` 或见下。默认 **8010**；也可用 `netstat -ano | findstr :8010` 查占用；`netsh interface ipv4 show excludedportrange protocol=tcp` 查看系统排除的端口段。  
3. **`run.bat` 与 RAG 依赖**：若当前 Python 未安装知识库向量包，脚本会在启动前自动执行 `pip install langchain-huggingface sentence-transformers faiss-cpu`（与 `requirements.txt` 版本下限一致）。若不想安装，运行前可设 `SQLDOCTOR_SKIP_KB_PIP=1`，或在 `.env` 中设 `KB_ENABLED=false`。  
4. **Hugging Face 仍连 `huggingface.co` / `WinError 10060`**：项目已用 **`python-dotenv` + `backend/env_bootstrap.py`** 在启动时把 `.env` 写入 `HF_ENDPOINT`，并在未配置时**默认镜像 `https://hf-mirror.com`**（`run.bat` 同步默认）。若日志里仍出现 `https://huggingface.co/...`，请执行 **`py -3 -m pip install python-dotenv`** 后重启；必须走官方站时设 **`SQLDOCTOR_HF_OFFICIAL=1`**。知识库为**后台加载**，后端会先监听 **8010**。  
5. **仅运行 `pnpm dev`、未起 FastAPI**：Next 会把 `/api/*` 代理到 `127.0.0.1:8010`，后端未启动会出现 **`ECONNREFUSED`**。请先起后端，或使用 **`run.bat`**（会等待 `/api/health` 后再开前端）。若改了 `API_PORT`，请在启动前端的同一终端执行 `set API_PORT=你的端口`（Windows），或在 `frontend/.env.local` 写 `API_PORT=...`，与后端一致。

## API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/analysis/test-connection` | 校验异步连接串（与 `analysis` 同前缀，避免部分环境下 `/api/db/...` 代理异常）。兼容别名：`POST /api/db/test-connection` |
| POST | `/api/analysis/run` | LangGraph 工具链：解析、EXPLAIN（可跳过）、规则建议、改写；可传 `database_url` 仅用于本次请求 |
| POST | `/api/rag/diagnose` | EXPLAIN + 计划分析 + FAISS 检索 + LLM；需配置 `LLM_MODEL` |

请求体字段见 `/docs` 中 Schema（如 `sql`、`dialect`、可选 `database_url`）。Next 前端已分步：选库类型 → 填连接串模板 → 测试连接 → 输入 SQL → 分析。

## 知识库内容

种子文档位于 `kb/seed/`：

- `slow_sql_cases.md` — 慢 SQL 案例  
- `index_rules.md` — 索引与优化规则  
- `company_experience.md` — 公司内部经验（示例，可按组织替换）

可自行增删 Markdown 后执行 `python -m kb` 重建索引。

## 许可证

未指定默认许可证；使用前请根据组织策略补充 `LICENSE`。
