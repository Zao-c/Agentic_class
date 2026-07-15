# 课程任务数据治理

## 分层边界

- `candidate/`：从课程资料抽取或人工草拟，等待逐条教师审核；不能计入正式指标。
- `reviews/`：审核导入后的审计记录，绑定候选文件与单条候选内容哈希。
- `gold/`：仅包含教师明确 `accepted`、完成人工声明与来源/隐私/安全核验的冻结版本。
- `rejected/`：教师明确 `rejected` 的记录及拒绝原因；不参与检索和评测。

当前 132 条 QA 均为候选数据。仓库不会把模型判断、空白审核行或仅生成了参考答案的记录称为教师审核，也不会预生成虚假的 Gold 数据。

## 审核字段

先生成最新版审核表：

```powershell
conda run -n rag-agent python scripts/manage_gold_dataset.py review-template
```

教师填写以下关键字段：

- `review_decision`：只允许 `accepted` 或 `rejected`；留空表示未审核。
- `reviewer_id`、`reviewer_role`、`reviewed_at`：审核人标识、`teacher`/`course_teacher`、带时区的 ISO 8601 时间。
- `human_review_attestation=true`：明确声明决定来自人工教师。该声明是可审计证据，不是系统对身份的自动认证。
- `source_verified`、`privacy_checked`、`safety_checked`：接受记录时必须全部为 `true`。
- `split`：接受记录必须指定 `train`、`dev` 或 `test`；拒绝记录不得指定。
- `reviewed_question`、`reviewed_reference_answer`、`reviewed_source_locator`：需要修改时填写，否则沿用候选内容。
- `review_note`：拒绝记录必须填写原因。

## 导入、校验与冻结

```powershell
conda run -n rag-agent python scripts/manage_gold_dataset.py import-review `
  --review-batch-id teacher-review-2026-01

conda run -n rag-agent python scripts/manage_gold_dataset.py validate-review

conda run -n rag-agent python scripts/manage_gold_dataset.py freeze --version 1.0.0
```

冻结步骤会再次校验：候选快照哈希、单条候选哈希、人工教师决定、审核门禁、原始资料文件哈希和版本唯一性。Gold 清单记录候选哈希、审核批次哈希、Gold 文件哈希、版本、分层计数和冻结时间。已有版本默认不可覆盖；修订数据必须使用新版本。

审计记录结构见 `teacher-review-schema.json`，冻结清单结构见 `gold-manifest-schema.json`。`dataset-item-schema.json` 是所有任务条目的基础结构。
