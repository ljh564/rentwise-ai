# 开发阶段与 LLM 接入边界

## 已完成

1. 结构化需求向导和模拟上海房源。
2. 高德真实多目的地通勤。
3. Redis 缓存与共享限速。
4. PostgreSQL 匿名档案、收藏、历史和反馈。
5. 确定性 LangGraph 决策状态图和运行记录。

## 下一步：需要 LLM 配置

LLM 首先用于：

1. 将用户自定义偏好解析为可验证的结构化条件。
2. 基于计算证据生成更自然但忠实的推荐理由。
3. 解释不同家庭成员之间的权衡。

需要环境变量：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TEMPERATURE=0
```

服务需要兼容 OpenAI Chat Completions 或 Responses 风格接口。API Key 只进入本地 `.env`，不会提交 Git。

## LLM 之后

- 房源图片分析。
- 合同上传、OCR、法律规则检索和风险说明。
- 真实中国房源 Provider。
- Playwright 端到端测试和 Agent 评估集。
- Alembic 数据库迁移与生产部署加固。
