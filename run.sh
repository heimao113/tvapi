#!/bin/bash
# MoonTV 源聚合 每日更新入口:拉取→分类→产出→git 提交推送
set -u
cd /home/tvsource

# 防重入锁
exec 9>/tmp/tvsource.lock
flock -n 9 || { echo "$(date '+%F %T') 已有任务在跑,跳过"; exit 0; }

echo "===== $(date '+%F %T') 开始更新 ====="
python3 update.py >> update.log 2>&1 || { echo "$(date '+%F %T') 生成失败,详见 update.log"; exit 1; }
echo "$(date '+%F %T') 生成完成,开始 git 提交"

# ---- git 提交 + 推送(凭据从本地文件读取,不入库)----
TOKEN_FILE=/home/tvsource/github_token
REPO_FILE=/home/tvsource/github_repo
git add -A >> update.log 2>&1
if git diff --cached --quiet; then
    echo "$(date '+%F %T') 内容无变化,跳过提交"
    exit 0
fi
git commit -m "auto update $(date '+%F %T')" >> update.log 2>&1 || exit 1

if [ -s "$TOKEN_FILE" ] && [ -s "$REPO_FILE" ] && ! grep -q "你的" "$REPO_FILE"; then
    REPO=$(tr -d ' \r\n' < "$REPO_FILE")
    TOKEN=$(tr -d ' \r\n' < "$TOKEN_FILE")
    PUSH_URL=$(echo "$REPO" | sed "s|https://|https://x-access-token:${TOKEN}@|")
    if git push "$PUSH_URL" HEAD >> update.log 2>&1; then
        echo "$(date '+%F %T') 已推送到 $REPO"
    else
        echo "$(date '+%F %T') 推送失败,详见 update.log"
    fi
else
    echo "$(date '+%F %T') 未配置 github_repo/github_token,仅本地提交"
fi
