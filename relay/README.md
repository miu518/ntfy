# ntfy

给 [ntfy](https://github.com/binwiederhier/ntfy) 加**断点续发**的中继服务：调用方 POST 到本服务，本服务立即转发给 ntfy；转发失败则写入 SQLite 队列，后台按指数退避（30s → 2m → 10m → 1h → 6h → 每天，最多 20 次）自动重发，成功后出队。ntfy 重启、网络抖动、机器短暂离线都不丢告警。

纯 Python 标准库实现，镜像约 60MB。

## 别人 docker 一键安装

```bash
# 1) 先起 ntfy（官方镜像）
docker run -d --name ntfy --restart unless-stopped -p 8765:80 binwiederhier/ntfy serve

# 2) 再起 relay（指向你的 ntfy 地址）
docker run -d --name ntfy-relay --restart unless-stopped \
  -p 8766:8080 \
  -e NTFY_URL=http://host.docker.internal:8765 \
  -v ntfy-relay-data:/data \
  ghcr.io/miu518/ntfy-relay:latest
```

> 注意：首次 push 后，到 GitHub Packages 页面把 `ntfy` 设为 **Public**，别人才能 `docker pull`。

## 一起编排（推荐）

```bash
docker compose up -d
```

`docker-compose.yml` 同时起 ntfy（8765）+ relay（8766）。

## API

```bash
# 发送（断点续发）
curl -X POST http://127.0.0.1:8766/send \
  -H 'Content-Type: application/json' \
  -d '{"topic":"alert-9f3k7x2","title":"服务器告警","message":"磁盘 92%","priority":"urgent","tags":"warning"}'
# -> {"status":"delivered"} 或 {"status":"queued","id":3}

# 查看积压队列
curl http://127.0.0.1:8766/queue

# 健康检查
curl http://127.0.0.1:8766/health
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `NTFY_URL` | `http://ntfy:80` | ntfy 服务地址 |
| `DB_PATH` | `/data/queue.db` | 队列数据库（挂 volume 持久化） |
| `PORT` | `8080` | 监听端口 |

## 构建

push 到 relay 分支自动触发 GitHub Action，打镜像并推送到 `ghcr.io/miu518/ntfy-relay:latest`。
