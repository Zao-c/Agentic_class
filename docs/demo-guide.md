# 可复现演示指南

## 演示目标

portable 演示在不连接真实机器人、不调用外部大模型的条件下证明四条关键能力：

1. 课程问答返回具体课程引用和 Trace。
2. 故障诊断执行结构化报警查询、手册检索与安全判断。
3. 课程资料不足时明确拒答，不补造答案。
4. 辅导任务创建练习、批改答案、解释得分并更新掌握度。

## 自动演示

先启动服务：

```powershell
conda activate rag-agent
python scripts/run_profile.py --profile portable
```

再运行：

```powershell
python scripts/demo_scenarios.py
```

成功输出包含 `passed: true`、`scenario_count: 4` 和报告绝对路径。机器可读报告保存每个请求的任务类型、状态、风险、引用标题、工具链、Trace 事件数，以及辅导评分和掌握度；不把示例数字硬编码成运行结果。

## Agentic 真实模型演示

本机安全注入模型密钥后启动 `python scripts/run_profile.py --profile agentic-online`。演示时必须同时展示 `/ready` 中的 Agent profile，以及 Trace 内 `model.decision.finished` 的 Schema、结构化输出、简短决策依据、字段来源、Token、耗时、尝试次数和 fallback 状态。没有真实模型成功调用报告时，不得把 Fake provider 自动化测试表述为在线模型验收。

## 手动讲解顺序

| 场景 | 输入 | 应观察到的证据 |
|---|---|---|
| 知识问答 | `示教编程的一般步骤是什么？` | `knowledge_qa`、编号步骤、具体引用 |
| 故障诊断 | `ABB IRB120 报警 38213，故障发生在手动模式` | 报警范围说明、候选原因、安全核对项、工具 Trace |
| 证据拒答 | `量子引力和火星殖民的课程要求是什么？` | `abstained`，不返回伪造课程结论 |
| 个性化辅导 | `给我出一道示教编程练习` | 练习编号和来源；提交答案后得分、缺失项和掌握度 |

演示中应主动说明：系统只辅助教学和信息整理，不控制设备；高风险、冲突或未收录报警会转交教师或专业人员。

## 截图

- [桌面端界面](assets/ui-desktop.png)
- [移动端界面](assets/ui-mobile.png)
- [辅导批改桌面端](assets/ui-tutoring-desktop.png)
- [辅导批改移动端](assets/ui-tutoring-mobile.png)

## 浏览器录屏

- [学生辅导闭环原始录屏](assets/tutoring-flow.webm)

录屏真实操作了个性化辅导入口、题目生成、答案提交、100 分逐项反馈、个人进度和教师匿名概览。文件已用 `ffprobe` 验证为 VP8 WebM，1258×622，时长 248.8 秒。

截图用于展示交互入口和 Trace 面板；准确率、延迟、用例通过率必须引用 `reports/` 中的真实报告，不能从截图推断。
