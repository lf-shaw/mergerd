#!/usr/bin/env bash
set -euo pipefail

# 输出目录
OUTDIR=cert
mkdir -p "$OUTDIR"
cd "$OUTDIR"

echo "[1] 生成 CA..."
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=MountManager-CA" -out ca.crt

echo "[2] 生成 Server 证书和私钥..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=localhost" -out server.csr

cat > server-ext.cnf <<EOF
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1  = 127.0.0.1
EOF

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out server.crt -days 365 -sha256 \
  -extfile server-ext.cnf

echo "[3] 生成 Client 证书和私钥..."
openssl genrsa -out client.key 2048
openssl req -new -key client.key -subj "/CN=client1" -out client.csr

cat > client-ext.cnf <<EOF
subjectAltName = @alt_names

[alt_names]
DNS.1 = client1
EOF

openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out client.crt -days 365 -sha256 \
  -extfile client-ext.cnf

echo "✅ 证书生成完成，文件如下："
ls -l
