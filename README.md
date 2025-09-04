# mergerd

基于 mergerfs 的分布式挂载管理服务

---

## 项目简介

**mergerd** 是一个通过 gRPC 服务统一管理 mergerfs 挂载点的工具。它通过 Python 实现的服务端和客户端，配合 mergerfs，实现多个物理目录的动态合并挂载，并支持跨主机安全访问和自动化挂载管理。适用于 Docker、NAS、家庭服务器等多场景的数据整合与共享。

---

## 背景介绍

在容器化或分布式存储环境下，常常需要将多个磁盘或目录合并，形成一个统一的数据入口。mergerfs 是用户态的联合文件系统，适合实现目录合并，但原生操作繁琐，自动化与远程管理功能缺失。mergerd 通过 gRPC+数据库+证书认证等方式，对 mergerfs 挂载进行集中管理，支持安全远程操作。

---

## 功能特点

- **动态挂载管理**：支持创建、移除、查询、列表 mergerfs 挂载点。
- **安全认证**：gRPC 服务端与客户端均强制 mTLS 双向证书认证，保证安全。
- **命令行工具**：提供 Python 客户端 CLI，便捷远程操作。
- **自动校验路径合法性**：对挂载目标与源路径进行严格校验，防止配置错误。
- **持久化记录**：所有挂载点信息存储于 SQLite 数据库，支持持久化与快速查询。
- **跨容器/跨主机共享**：可作为容器或主机间的中转挂载入口，实现数据“零拷贝”共享。

---

## 主要目录结构

- `main.py`：服务端入口，启动 gRPC 挂载管理服务
- `mergerd/server.py`：服务端核心逻辑，包括路径校验、数据库操作、挂载命令执行
- `client.py`：命令行客户端，远程调用服务端进行挂载管理
- `build.sh`：proto 文件编译脚本
- `gen_cert.sh`：一键生成 CA、服务端和客户端证书
- `mergerd/mount_manager.proto`：gRPC 服务定义
- `README.md`：项目说明文档

---

## 安装与使用

### 1. 安装 mergerfs

请先安装 mergerfs，参考 [官方文档](https://github.com/trapexit/mergerfs#installation)
例如（Debian/Ubuntu）：

```bash
sudo apt update
sudo apt install mergerfs
```

### 2. 生成证书

运行 `gen_cert.sh`，自动生成 CA、服务端、客户端证书：

```bash
bash gen_cert.sh
```

证书生成在 `cert/` 目录下。

### 3. 编译 gRPC Python 代码

```bash
bash build.sh
```

### 4. 启动服务端

```bash
python main.py --listen 0.0.0.0:50051 \
  --server-cert cert/server.crt --server-key cert/server.key --ca-cert cert/ca.crt
```

### 5. 客户端操作示例

```bash
python client.py --addr server_ip:50051 \
  --ca cert/ca.crt --cert cert/client.crt --key cert/client.key \
  create --dest /mnt/merged --src /mnt/disk1 /mnt/disk2
```

- 创建挂载点：`create --dest <挂载点> --src <源目录1> <源目录2> ...`
- 移除挂载点：`remove --dest <挂载点>`
- 查看所有挂载点：`list`
- 查询某一挂载点详情：`get --name <挂载点>`

---

## 使用注意事项

- 挂载/卸载操作需有 root 权限（mergerfs/FUSE）。
- 挂载目标与源路径必须为合法绝对路径，不得包含空格或特殊字符。
- 建议所有目录均配置为读写权限，避免挂载失败。
- 客户端与服务端通信需证书匹配，防止未授权访问。
- 挂载信息持久化于 `mergerd.db`，如需迁移/备份请注意同步数据库文件。
- 仅支持 Linux/FUSE 环境，Windows/macOS 暂不支持。

---

## 进阶说明

- 支持多分支动态扩容，挂载点可随时增删源目录（需重新挂载）。
- 支持强制卸载（`--force` 参数），可用于异常或重复挂载场景。
- 提供健康检测与状态校验接口，确保挂载点一致性。
- 可扩展为 REST API 或 Web 管理界面。

---

## 参考链接

- [mergerfs 官方文档](https://github.com/trapexit/mergerfs)
- [gRPC Python](https://grpc.io/docs/languages/python/)
- [FUSE 文件系统](https://github.com/libfuse/libfuse)
- [SQLite 官方](https://www.sqlite.org/index.html)

---

## License

MIT License，详见 LICENSE 文件。

---

## 联系与交流

如有疑问、建议或 bug 反馈，请提交 [GitHub Issues](https://github.com/lf-shaw/mergerd/issues)，欢迎交流与贡献。
