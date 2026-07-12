# API 与匿名身份

交互式 OpenAPI 文档位于 `http://localhost:8000/docs`。

## 匿名身份

首次调用：

```http
POST /api/anonymous/session
```

后续受保护接口必须携带：

```http
X-Anonymous-User-ID: <uuid>
X-Anonymous-Access-Token: <token>
```

Token 只返回一次，浏览器保存在 localStorage；数据库只保存 SHA-256 哈希。

## 主要接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 服务和 Provider 状态 |
| GET/PUT | `/api/profile` | 恢复或保存租房偏好 |
| POST | `/api/search` | 运行 LangGraph 决策流程 |
| GET/POST | `/api/favorites` | 查询或收藏房源快照 |
| DELETE | `/api/favorites/{listing_id}` | 删除收藏 |
| GET | `/api/search-history` | 最近 20 次搜索 |
| POST | `/api/feedback` | 保存推荐反馈 |

`POST /api/search` 返回 `search_id` 和 `agent_run_id`，用于关联历史、反馈和运行轨迹。

## 数据边界

- 不保存姓名、手机号、身份证或平台登录信息。
- 清除浏览器数据会丢失匿名凭证，无法找回原档案。
- 收藏保存的是当时快照，房源当前状态必须回原平台确认。
