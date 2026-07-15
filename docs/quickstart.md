# 快速启动与验收

## 1. 准备环境

项目要求 Python 3.11。已有 `rag-agent` Conda 环境时：

```powershell
conda activate rag-agent
python -m pip install -r requirements-dev.txt
```

全新环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

项目默认不需要 API Key 或外部模型服务。可复制 `.env.example` 后按部署目录修改数据路径；不复制时也有项目内默认值。

## 2. 启动便携档

```powershell
python scripts/run_profile.py --profile portable
```

打开：

- Web 界面：<http://127.0.0.1:8000/>
- OpenAPI UI：<http://127.0.0.1:8000/docs>
- 就绪状态：<http://127.0.0.1:8000/ready>

`/ready` 应至少显示课程片段、2 条结构化报警记录、5 个知识点，以及 `hybrid_rerank` 检索策略。

## 3. 一键验证

另开一个 PowerShell：

```powershell
python scripts/demo_scenarios.py
python -m pytest
python scripts/evaluate_diagnosis.py
python scripts/evaluate_tutoring.py
```

演示脚本真实调用 HTTP API，覆盖知识问答、故障诊断、资料不足拒答和辅导批改；报告写入 `reports/demo_*.json`。演示创建的学习数据默认在报告落盘前清除。

## 4. Docker

```powershell
docker compose up --build
```

公开镜像只包含 `data/public_sample` 脱敏合成知识样例和结构化演示数据，不内置本地课程资料或评测集；Compose 会把操作方本地的 `data/active`、`data/eval` 只读挂载进容器，并持久化运行库和报告目录。完整验收应在 Docker 主机执行：

```powershell
python scripts/accept_docker.py
```

该脚本验证 build/up、`/health`、`/ready`、三类任务、状态卷和重启恢复。当前开发机未安装 Docker CLI，因此不能把仅有配置文件或 CI 配置视为本机 Docker 验收通过。

## 5. 常见问题

- 启动慢：默认便携档不加载神经模型；检查是否误用了 `neural-quality`。
- 知识点为 0：确认 `data/structured/knowledge_points_v1.json` 存在，并检查 `KNOWLEDGE_POINT_DATA_PATH`。
- 报警码为 0：确认 `ALARM_CODE_DATA_PATH` 指向 `alarm_codes_v1.json`。
- 二进制资料未导入：默认只自动导入 TXT/Markdown；执行 `python scripts/ingest_knowledge.py --include-binary`。
- Windows PowerShell 直接手写 HTTP 请求出现中文路由异常时，确保请求体使用 UTF-8；项目演示脚本使用 `httpx`，已处理编码。
