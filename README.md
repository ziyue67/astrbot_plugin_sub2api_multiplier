# AstrBot Sub2API 倍率查询插件

通过 Sub2API 管理员 API Key 查询启用分组的基础倍率，并使用 AstrBot 发送到 QQ。

## 功能

- 支持 `/倍率` 和 `/multiplier`
- 支持配置多个 Sub2API 实例
- 显示分组、平台和基础倍率
- 显示最低基础倍率对应的分组
- 支持缓存、超时和长消息自动分段
- 单个实例失败时仍继续返回其他实例结果
- 不在 QQ 消息或普通日志中输出管理员 API Key

## 配置

在 AstrBot 插件配置中添加实例：

```json
{
  "instances": [
    {
      "__template_key": "sub2api_instance",
      "name": "主站",
      "base_url": "https://sub2api.example.com",
      "admin_api_key": "替换为 Sub2API 后台管理员 API Key"
    }
  ],
  "cache_ttl_minutes": 5,
  "timeout_seconds": 10,
  "max_message_chars": 3000,
  "include_inactive": false
}
```

`base_url` 只填写站点地址，例如 `https://sub2api.example.com`，插件会自动请求：

```text
GET /api/v1/admin/groups/all?include_inactive=false
x-api-key: <管理员 API Key>
```

管理员 API Key 请从 Sub2API 后台管理员设置中复制。插件不会尝试模拟后台网页登录或自动读取密钥。

## 最低倍率说明

最低倍率按分组保存的 `rate_multiplier` 计算。动态倍率和高峰倍率会单独显示，不会改变“最低基础倍率”的比较结果。

插件只发送分组倍率摘要，不发送模型清单，避免 QQ 消息过长。

## 开发测试

```bash
python -m pytest -q
```
