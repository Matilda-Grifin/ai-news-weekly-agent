#!/usr/bin/env bash
# 使用国内镜像加速下载 Playwright 自带 Chromium（约 160MB+），避免默认 CDN 过慢。
# 用法：bash scripts/install_playwright_chromium_mirror.sh
# 文档：https://playwright.dev/python/docs/browsers#download-from-artifact-repository

set -euo pipefail
cd "$(dirname "$0")/.."

export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://registry.npmmirror.com/-/binary/playwright}"
# 若整包镜像仍慢，可再试仅 Chromium（部分环境有效）：
# export PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST="https://cdn.npmmirror.com/binaries/chrome-for-testing"

echo "PLAYWRIGHT_DOWNLOAD_HOST=$PLAYWRIGHT_DOWNLOAD_HOST"
python3 -m pip install -q playwright
python3 -m playwright install chromium

echo "Done. Check: python3 -m playwright install --list"
