#阿里云网址
http://121.41.81.58:8081/

#阿里云查指标命令
快速看当天任务结果分层
python - <<'PY'
import json,datetime
from collections import Counter
today=datetime.datetime.now().date().isoformat()
c=Counter()
for line in open("runs/events.jsonl",encoding="utf-8"):
    e=json.loads(line)
    if e.get("event")=="task_finished" and str(e.get("ts","")).startswith(today):
        c[e.get("final_status","UNKNOWN")]+=1
print(dict(c))
PY
快速看当天 P50/P95
python - <<'PY'
import json,datetime,math
today=datetime.datetime.now().date().isoformat()
arr=[]
for line in open("runs/events.jsonl",encoding="utf-8"):
    e=json.loads(line)
    if e.get("event")=="task_finished" and str(e.get("ts","")).startswith(today):
        v=e.get("latency_ms")
        if isinstance(v,(int,float)): arr.append(v)
arr.sort()
if not arr: print("no data"); raise SystemExit
def pct(a,p):
    i=max(0,min(len(a)-1,math.ceil(len(a)*p/100)-1))
    return a[i]
print({"count":len(arr),"p50_ms":pct(arr,50),"p95_ms":pct(arr,95)})
PY
你要的话我可以再给你加一个现成脚本（比如project/scripts/metrics_report.py），阿里云上直接 python ... --today 一键出报表。


# 阿里云服务器部署指南（24/7 运行）

本指南将 Streamlit 应用部署为阿里云 ECS 的长期后台服务。
**不会影响** 同一台服务器上的其他项目。

## 0) 前置条件：GitHub SSH 密钥配置（首次配置）

### 第 1 步：检查本地 SSH 密钥是否存在

如果你已经生成过密钥，查看一下：
```bash
ls -la ~/.ssh/ | grep -E "aliyun|ai-news|ai_news"
```

如果看到类似 `aliyun-ai-agent` 或 `ai-news-deploy-key` 的文件，说明密钥已存在。

### 第 2 步：生成新密钥（如果没有的话）

如果上面没有找到，生成一个新的：
```bash
ssh-keygen -t ed25519 -C "aliyun-ai-agent" -f ~/.ssh/aliyun-ai-agent -N ""
```

**注意**：`-N ""` 表示设置密钥密码为空（无需输入密码即可使用）。这样 cron 等自动化脚本才能无交互地使用密钥。

### 第 3 步：配置 SSH config（推荐方式，支持多个密钥）

创建或编辑 `~/.ssh/config` 文件：

```bash
cat > ~/.ssh/config <<'EOF'
Host github-ai-agent
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/aliyun-ai-agent
  IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config
```

为什么使用 `ssh.github.com:443`？
- 规避某些防火墙对 SSH 22 端口的限制
- 支持同一主机上的多个 GitHub 密钥（Git 会按 IdentityFile 顺序尝试）

### 第 4 步：在 GitHub 上添加部署密钥

**关键**：确保添加的公钥内容与本地 `aliyun-ai-agent.pub` 完全匹配。

获取公钥内容：
```bash
cat ~/.ssh/aliyun-ai-agent.pub
# 输出示例：ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... aliyun-ai-agent
```

然后：
- 打开：https://github.com/Matilda-Grifin/ai-news-weekly-agent/settings/keys
- 点击 "Add deploy key"
- 标题：填 `aliyun-ai-agent`
- Key：粘贴上面 `cat ~/.ssh/aliyun-ai-agent.pub` 的**完整输出**
- 勾选 "Allow write access"（可选，用于将来自动推送）
- 保存

### 第 5 步：验证密钥链接是否成功

在本地机器上测试：
```bash
# 使用 ssh.github.com:443 连接
ssh -T git@github-ai-agent

# 预期输出：Hi Matilda-Grifin! You've successfully authenticated, but GitHub does not provide shell access.
```

如果能看到上面的输出，说明本地密钥链接成功。

**如果失败**：
- 检查 GitHub 上的 deploy key 内容是否与本地 `cat ~/.ssh/aliyun-ai-agent.pub` 完全一致
- 如果不一致，删除 GitHub 上的旧密钥，重新添加新的内容

### 第 6 步：上传私钥到阿里云

在本地执行：
```bash
scp ~/.ssh/aliyun-ai-agent root@<你的阿里云IP>:/root/.ssh/aliyun-ai-agent
ssh root@<你的阿里云IP> chmod 600 /root/.ssh/aliyun-ai-agent
```

### 第 7 步：在阿里云上配置 SSH config

连接到阿里云后，创建 SSH 配置：

```bash
cat > ~/.ssh/config <<'EOF'
Host github-ai-agent
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/aliyun-ai-agent
  IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config
```

### 第 8 步：在阿里云上测试 SSH 连接

```bash
ssh -T git@github-ai-agent

# 预期输出：Hi Matilda-Grifin! You've successfully authenticated, but GitHub does not provide shell access.
```

如果显示上面的消息，说明阿里云已成功链接到 GitHub。

## 1) 服务器初始化

在阿里云 ECS 上执行：

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
sudo useradd --system --create-home --home /opt/ai-news --shell /usr/sbin/nologin ai-news

# 使用 SSH 密钥克隆仓库
mkdir -p /opt/ai-news && cd /opt/ai-news
git clone git@github-ai-agent:Matilda-Grifin/ai-news-weekly-agent.git app
cd app

# 设置 Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 将目录所有权转移给 ai-news 用户
sudo chown -R ai-news:ai-news /opt/ai-news
```

**说明**：`git@github-ai-agent:...` 使用 SSH config 中定义的 `github-ai-agent` 主机别名，自动使用 `~/.ssh/aliyun-ai-agent` 密钥和 `ssh.github.com:443` 端口。

## 2) 为 ai-news 用户配置 SSH（用于后续 git pull 更新）

```bash
# 确保 ai-news 用户的 .ssh 目录存在
sudo mkdir -p /opt/ai-news/.ssh

# 复制 SSH 密钥到 ai-news 用户的主目录
sudo cp /root/.ssh/aliyun-ai-agent /opt/ai-news/.ssh/aliyun-ai-agent
sudo chown ai-news:ai-news /opt/ai-news/.ssh/aliyun-ai-agent
sudo chmod 600 /opt/ai-news/.ssh/aliyun-ai-agent

# 为 ai-news 用户创建 SSH 配置
sudo tee /opt/ai-news/.ssh/config > /dev/null <<'EOF'
Host github-ai-agent
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/aliyun-ai-agent
  IdentitiesOnly yes
EOF

sudo chmod 600 /opt/ai-news/.ssh/config
sudo chown ai-news:ai-news /opt/ai-news/.ssh/config
```

**说明**：这样 ai-news 用户在执行 `git pull` 时，会自动使用 `github-ai-agent` 配置和对应的 SSH 密钥。

## 3) 创建 systemd 服务

创建文件 `/etc/systemd/system/ai-news-streamlit.service`：

```ini
[Unit]
Description=AI News Weekly Streamlit
After=network.target

[Service]
Type=simple
User=ai-news
Group=ai-news
WorkingDirectory=/opt/ai-news/app
Environment=PYTHONUNBUFFERED=1
Environment=PUBLIC_WEB_MODE=true
ExecStart=/opt/ai-news/app/.venv/bin/streamlit run project/app.py --server.port 8501 --server.address 127.0.0.1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-news-streamlit
sudo systemctl start ai-news-streamlit
sudo systemctl status ai-news-streamlit
```

## 4) Nginx 反向代理

创建文件 `/etc/nginx/sites-available/ai-news`（将 `your-domain.com` 替换为你的实际域名）：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }
}
```

启用网站：

```bash
sudo ln -s /etc/nginx/sites-available/ai-news /etc/nginx/sites-enabled/ai-news
sudo nginx -t
sudo systemctl reload nginx
```

## 5) HTTPS 证书

```bash
sudo certbot --nginx -d your-domain.com
sudo certbot renew --dry-run
```

## 6) 阿里云安全组配置

- 允许入站 `80/tcp`（HTTP）和 `443/tcp`（HTTPS）
- 只允许来自你自己 IP 的 `22/tcp`（SSH）
- **不要暴露** Streamlit 的 `8501` 端口到公网

验证配置：
```bash
sudo ufw status
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp from <你的管理员IP>
```

## 7) 日常更新和重启（GitHub → 阿里云）

### 选项 A：手动更新

当你向 GitHub 推送更改时，在阿里云上手动拉取：

```bash
cd /opt/ai-news/app
sudo -u ai-news bash -c 'export HOME=/opt/ai-news; git pull origin main'

# 如果依赖项有变化：
source /opt/ai-news/app/.venv/bin/activate
pip install -r requirements.txt

# 重启服务：
sudo systemctl restart ai-news-streamlit
sudo systemctl status ai-news-streamlit
```

### 选项 B：自动更新脚本（推荐）

创建文件 `/opt/ai-news/update-and-restart.sh`：

```bash
#!/bin/bash
#
# AI News Weekly Agent 自动更新和重启脚本
# 将此文件放在 /opt/ai-news/ 并设置权限为 755
# 添加到 cron：0 2 * * * cd /opt/ai-news && ./update-and-restart.sh >> /opt/ai-news/update.log 2>&1

set -e

APP_DIR="/opt/ai-news/app"
VENV_DIR="/opt/ai-news/app/.venv"
SERVICE_NAME="ai-news-streamlit"
LOG_FILE="/opt/ai-news/update.log"

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log_msg "=== 开始更新 ==="

# 步骤 1：检查服务是否运行
if ! systemctl is-active --quiet $SERVICE_NAME; then
    log_msg "警告：服务 $SERVICE_NAME 未运行。正在启动..."
    sudo systemctl start "$SERVICE_NAME"
    sleep 3
fi

# 步骤 2：拉取最新代码
cd "$APP_DIR"
log_msg "从 GitHub 拉取最新代码..."
if ! sudo -u ai-news bash -c 'export HOME=/opt/ai-news; git pull origin main'; then
    log_msg "错误：git pull 失败。中止更新。"
    echo "最新提交："
    git log -1 --oneline
    exit 1
fi
log_msg "代码拉取成功。"

# 步骤 3：检查 requirements.txt 是否有变化
if git diff HEAD~1 requirements.txt > /dev/null 2>&1; then
    log_msg "requirements.txt 有变化。更新依赖..."
    source "$VENV_DIR/bin/activate"
    pip install -q -r requirements.txt
    log_msg "依赖更新完成。"
fi

# 步骤 4：重启服务
log_msg "正在重启 $SERVICE_NAME..."
sudo systemctl restart "$SERVICE_NAME"
sleep 2

# 步骤 5：验证服务是否运行
if systemctl is-active --quiet "$SERVICE_NAME"; then
    log_msg "✓ 服务已成功重启。"
    echo "最近日志："
    sudo journalctl -u $SERVICE_NAME -n 20 --no-pager
else
    log_msg "✗ 错误：服务启动失败！"
    sudo journalctl -u $SERVICE_NAME -n 50 --no-pager
    exit 1
fi

log_msg "=== 更新完成 ==="
```

使脚本可执行并添加到 cron：

```bash
sudo chmod 755 /opt/ai-news/update-and-restart.sh

# 测试脚本：
cd /opt/ai-news && sudo ./update-and-restart.sh

# 添加到 root 的 crontab，每天凌晨 2 点运行：
sudo crontab -e
# 添加这行：
# 0 2 * * * cd /opt/ai-news && ./update-and-restart.sh >> /opt/ai-news/update.log 2>&1
```

查看 cron 日志：
```bash
sudo journalctl -u cron --since today | tail -20
```

## 8) 部署验证

```bash
# 检查服务状态
sudo systemctl status ai-news-streamlit

# 实时查看日志
sudo journalctl -u ai-news-streamlit -f

# 检查 Nginx
sudo systemctl status nginx
sudo curl -I http://127.0.0.1:8501

# 验证 8501 端口未对外暴露
sudo ss -ltnp | grep 8501
# 应该显示：127.0.0.1:8501（仅在本地地址上）

# 检查 Nginx 在公网上监听
sudo ss -ltnp | grep -E '80|443'
```

## 9) 日常运维操作

手动重启（如需要）：
```bash
sudo systemctl restart ai-news-streamlit
sudo systemctl reload nginx

# 查看最近错误
sudo journalctl -u ai-news-streamlit --since "1 hour ago"
```

查看服务日志：
```bash
# 最新 50 行
sudo journalctl -u ai-news-streamlit -n 50

# 实时跟踪
sudo journalctl -u ai-news-streamlit -f

# 查看更新脚本日志
tail -f /opt/ai-news/update.log
```

## 10) 公开模式的安全建议

- **不要** 在服务器的 `.env` 中放入你的真实 API 密钥
- 让访客在网页界面通过侧栏输入他们自己的密钥
- 启用 `PUBLIC_WEB_MODE=true` 以确保访客的密钥仅保存在当前会话，不会被写入服务器进程环境变量
- 保持日志和历史记录的掩码；绝不打印完整密钥
