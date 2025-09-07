#!/usr/bin/env bash
set -euo pipefail

### 你唯一需要修改的地方：指定 Python 解释器
PYTHON_BIN="/usr/bin/python3.11"
PYPI_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"
MERGERD_PORT="50051"

### 安装目标目录（会复制整个项目到这里）
INSTALL_BASE="/opt"


SERVICE_NAME="mergerd"
INSTALL_DIR="$INSTALL_BASE/$SERVICE_NAME"
VENV_DIR="$INSTALL_DIR/venv"
CERT_DIR="$INSTALL_DIR/cert"
MAIN_FILE="$INSTALL_DIR/main.py"
GEN_CERT="$INSTALL_DIR/gen_cert.sh"

### 检查 mergerfs 是否安装
if ! command -v mergerfs >/dev/null 2>&1; then
    echo "[ERROR] mergerfs 未安装，请先安装："
    echo "  Debian/Ubuntu: apt install mergerfs"
    echo "  CentOS/RHEL:  yum install mergerfs"
    exit 1
fi
echo "[mergerd][INFO] mergerfs 已安装"

### 拷贝项目到安装目录
echo "[mergerd][INFO] 安装目录: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_BASE"
cp -r "$(cd "$(dirname "$0")" && pwd)" "$INSTALL_DIR"

### 创建虚拟环境并安装依赖
echo "[mergerd][INFO] 使用 Python: $PYTHON_BIN"
$PYTHON_BIN -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel -i "$PYPI_INDEX_URL"
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -i "$PYPI_INDEX_URL"
fi

### 确保必要的目录存在
mkdir -p "$CERT_DIR"

### 调用 gen_cert.sh 生成证书（如果不存在）
if [ ! -f "$CERT_DIR/server.crt" ] || [ ! -f "$CERT_DIR/server.key" ] || [ ! -f "$CERT_DIR/ca.crt" ]; then
    if [ -x "$GEN_CERT" ]; then
        echo "[mergerd][INFO] 调用 gen_cert.sh 生成证书"
        "$GEN_CERT" "$CERT_DIR"
    else
        echo "[ERROR] 找不到 gen_cert.sh 或未赋可执行权限"
        exit 1
    fi
else
    echo "[mergerd][INFO] 已存在证书，跳过生成"
fi

### 生成 systemd unit
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[mergerd][INFO] 生成 systemd unit: $SERVICE_FILE"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=mergerd - gRPC mergerfs mount manager
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python $MAIN_FILE --listen 0.0.0.0:\${MERGERD_PORT}

User=root
Group=root

Environment=MERGERD_PORT=$MERGERD_PORT

Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
PrivateMounts=no
ProtectSystem=no

[Install]
WantedBy=multi-user.target
EOF

### 重新加载并启用服务
echo "[mergerd][INFO] 重新加载 systemd"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "[mergerd][INFO] 启动服务"
systemctl restart "$SERVICE_NAME"

echo "[mergerd][INFO] 安装完成。查看状态："
echo "  systemctl status $SERVICE_NAME -l"
echo "日志查看： journalctl -u $SERVICE_NAME -f"
