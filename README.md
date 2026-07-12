# RentWise AI

面向中国用户的智能租房决策 Agent。系统不是房源发布平台，而是在候选房源之上完成需求过滤、真实通勤、居住成本、多成员公平性和可解释排序。

> 当前房源来自模拟上海数据，不代表真实在租状态；通勤数据来自高德地图 Web 服务 API。

## 已实现

- 多步骤租房需求向导和中英文界面
- 最多四个家庭通勤目的地、权重与独立时间上限
- 高德公交、驾车、步行和骑行真实路线
- Redis 地理编码/路线缓存和共享 3 QPS 限速
- 真实月成本、首月支出、最差通勤、每周总通勤和公平性计算
- 确定性 LangGraph 决策状态图
- 匿名身份、偏好自动保存与恢复
- 收藏房源快照、搜索历史和推荐反馈
- PostgreSQL 持久化与 Agent 运行轨迹
- Docker Compose 一键运行

## 快速启动

复制环境变量并填入高德 Web 服务 Key：

```bash
cp .env.example .env
```

```env
MAP_PROVIDER=amap
AMAP_API_KEY=your_web_service_key
```

启动：

```bash
docker compose up -d --build
```

- Web: http://localhost:5173
- API: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

## 运行链路

```mermaid
flowchart LR
    UI[React 需求向导] --> Identity[匿名身份]
    Identity --> API[FastAPI]
    API --> Graph[LangGraph]
    Graph --> Listings[ListingProvider]
    Graph --> Maps[AMapProvider]
    Maps --> Redis[(Redis 缓存与限流)]
    Graph --> Rank[成本/约束/通勤/偏好排序]
    Rank --> DB[(PostgreSQL)]
    DB --> UI
```

## 测试

```bash
docker compose exec backend pytest -q
cd frontend && npm run build
```

## 文档

- [系统架构](docs/architecture.md)
- [API 与匿名身份](docs/api.md)
- [评分规则](docs/scoring.md)
- [开发阶段与 LLM 接入边界](docs/development-plan.md)

## 当前边界

- 房源仍是模拟快照，原平台链接仅用于演示。
- 推荐解释由规则模板生成，还没有使用 LLM。
- 自定义开放偏好只有与结构化标签精确匹配时才参与加分。
- 合同法律核验和图片分析尚未开始。
- 本系统提供决策辅助，不替代房源线下核验、律师意见或司法认定。
