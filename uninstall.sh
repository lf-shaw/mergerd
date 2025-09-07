#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mergerd"
INSTALL_BASE="/opt"
INSTALL_DIR="$INSTALL_BASE/mergerd"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[mergerd][INFO] 停止并禁用服务"
systemctl stop "$SERVICE_NAME" || true
systemctl disable "$SERVICE_NAME" || true

echo "[mergerd][INFO] 删除 systemd unit"
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "[mergerd][INFO] 删除安装目录: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

echo "[mergerd][INFO] 卸载完成"
