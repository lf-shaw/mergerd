#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import shlex
import datetime
import threading
from concurrent import futures

from . import mount_manager_pb2 as pb
from . import mount_manager_pb2_grpc as grpc_pb

import grpc


DB_LOCK = threading.Lock()


# 环境变量配置
# BASE_DIR: 实际数据的汇集目录，mergerfs 挂载点都在这里
# BIND_DIR: 供容器访问的绑定目录，BASE_DIR 会通过 mergerfs 初始化挂载到此目录，然后不再变更，它与 BASE_DIR 的卸载需要在容器不再使用后手动进行
# DB_PATH: sqlite3 数据库文件路径，记录挂载信息
BASE_DIR = os.getenv("BASE_DIR", "/tmp/share")
BIND_DIR = os.getenv("BIND_DIR", "/tmp/data/share")
DB_PATH = os.getenv("DB_PATH", "./mergerd.db")


def is_mounted(path):
    """检查 path 是否已挂载"""
    result = subprocess.run(["mount"], capture_output=True, text=True)
    return any(path in line for line in result.stdout.splitlines())


def init_base_mount():
    """确保 BASE_DIR 已挂载到 BIND_DIR"""

    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(BIND_DIR, exist_ok=True)

    """确保 BASE_DIR 已挂载到 BIND_DIR"""
    if not is_mounted(BIND_DIR):
        cmd = ["mergerfs", BASE_DIR, BIND_DIR, "-o", "defaults,allow_other,use_ino"]
        run_cmd(cmd)


def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS mounts (
            name TEXT PRIMARY KEY,
            src_dirs TEXT,
            mount_point TEXT,
            mount_opts TEXT,
            created_at TEXT
        );
        """
        )
        conn.commit()
        conn.close()


def db_upsert_mount(name, src_dirs, mount_point, mount_opts):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            """
        INSERT INTO mounts (name, src_dirs, mount_point, mount_opts, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
          src_dirs=excluded.src_dirs,
          mount_point=excluded.mount_point,
          mount_opts=excluded.mount_opts;
        """,
            (
                name,
                ",".join(src_dirs),
                mount_point,
                mount_opts or "",
                datetime.datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        conn.close()


def db_delete_mount(name):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("DELETE FROM mounts WHERE name=?", (name,))
        conn.commit()
        conn.close()


def db_get_all():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, src_dirs, mount_point, mount_opts, created_at FROM mounts"
        )
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append(
                {
                    "name": r[0],
                    "src_dirs": r[1].split(",") if r[1] else [],
                    "mount_point": r[2],
                    "mount_opts": r[3] or "",
                    "created_at": r[4],
                }
            )
        return result


def db_get(name):
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            "SELECT name, src_dirs, mount_point, mount_opts, created_at FROM mounts WHERE name=?",
            (name,),
        )
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "name": r[0],
            "src_dirs": r[1].split(",") if r[1] else [],
            "mount_point": r[2],
            "mount_opts": r[3] or "",
            "created_at": r[4],
        }


# Helpers to call system commands
def run_cmd(cmd, check=True):
    # cmd: list or string
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def list_system_mounts():
    proc = run_cmd("mount", check=True)
    # Return raw output lines
    return proc.stdout.splitlines()


def is_mounted_at(mount_point):
    lines = list_system_mounts()
    for l in lines:
        # simple substring check: ' on /tmp/data/name '
        if f" on {mount_point} " in l:
            return True
    return False


def find_mergerfs_mounts():
    lines = list_system_mounts()
    res = []
    for l in lines:
        if "mergerfs" in l:
            # parse like: /usr/bin/mergerfs on /tmp/data/name type fuse.mergerfs (rw,relatime,...)
            try:
                parts = l.split()
                src = parts[0]
                on_idx = parts.index("on")
                mp = parts[on_idx + 1]
                res.append((src, mp, l))
            except Exception:
                continue
    return res


class MountManagerServicer(grpc_pb.MountManagerServicer):
    def CreateMount(self, request, context):
        name = request.name.strip()
        src_dirs = [s.strip() for s in request.src_dirs if s.strip()]
        allow_force = request.allow_force_unmount
        options = request.options or ""

        if not name:
            return pb.CreateMountResponse(ok=False, message="name required")
        if not src_dirs:
            return pb.CreateMountResponse(
                ok=False, message="at least one src_dir required"
            )

        mount_point = os.path.join(BASE_DIR, name)
        if os.path.exists(mount_point) and not os.path.isdir(mount_point):
            return pb.CreateMountResponse(
                ok=False, message=f"{mount_point} exists and is not a directory"
            )

        os.makedirs(mount_point, exist_ok=True)

        # check for duplicate names in DB
        existing = db_get(name)
        if existing:
            # verify actual mount status
            if is_mounted_at(mount_point):
                if not allow_force:
                    return pb.CreateMountResponse(
                        ok=False, message=f"name {name} already exists and is mounted"
                    )
                # else try to fusermount -uz then continue
                try:
                    run_cmd(["fusermount", "-uz", mount_point], check=False)
                except Exception as e:
                    # we ignore non-zero here and continue to regular mount attempt
                    pass

        # ensure src dirs exist
        for d in src_dirs:
            if not os.path.isdir(d):
                return pb.CreateMountResponse(
                    ok=False, message=f"source dir does not exist: {d}"
                )

        # produce mergerfs command
        # mergerfs SRC1:SRC2:... mount_point -o defaults,allow_other,use_ino,<options>
        src_spec = ":".join(src_dirs)
        mount_opts = "defaults,allow_other,use_ino"
        if options:
            mount_opts += "," + options

        cmd = ["mergerfs", src_spec, mount_point, "-o", mount_opts]
        try:
            run_cmd(cmd, check=True)
        except Exception as e:
            return pb.CreateMountResponse(ok=False, message=f"mount failed: {e}")

        # verify mount shows up
        if not is_mounted_at(mount_point):
            return pb.CreateMountResponse(
                ok=False, message="mount succeeded but not visible in mount table"
            )

        # store in DB
        db_upsert_mount(name, src_dirs, mount_point, mount_opts)
        return pb.CreateMountResponse(ok=True, message="mounted")

    def RemoveMount(self, request, context):
        name = request.name.strip()
        force = request.force
        if not name:
            return pb.RemoveMountResponse(ok=False, message="name required")
        rec = db_get(name)
        mount_point = os.path.join(BASE_DIR, name)
        if not rec:
            # perhaps still mounted but not in DB => allow removal
            if not is_mounted_at(mount_point):
                return pb.RemoveMountResponse(ok=False, message=f"{name} not found")
        # try normal umount first
        try:
            run_cmd(["umount", mount_point], check=False)
        except Exception:
            pass
        # if still mounted and force, try fusermount -uz
        if is_mounted_at(mount_point):
            if force:
                try:
                    run_cmd(["fusermount", "-uz", mount_point], check=False)
                except Exception as e:
                    return pb.RemoveMountResponse(
                        ok=False, message=f"fusermount failed: {e}"
                    )

                # 删除空目录
                if os.path.isdir(mount_point) and not os.listdir(mount_point):
                    os.rmdir(mount_point)
            else:
                return pb.RemoveMountResponse(
                    ok=False,
                    message=f"{mount_point} still mounted; use force to try fusermount -uz",
                )

        # finally remove DB record
        db_delete_mount(name)
        # we do not remove mount_point directory to avoid data loss
        return pb.RemoveMountResponse(ok=True, message="unmounted and removed from DB")

    def ListMounts(self, request, context):
        entries = []
        db_entries = db_get_all()
        # also cross-check with system mounts
        for d in db_entries:
            mounted = is_mounted_at(d["mount_point"])
            me = pb.MountEntry(
                name=d["name"],
                src_dirs=d["src_dirs"],
                mount_point=d["mount_point"],
                mounted=mounted,
                mount_opts=d["mount_opts"],
                created_at=d["created_at"],
            )
            entries.append(me)
        return pb.ListMountsResponse(entries=entries)

    def GetMount(self, request, context):
        name = request.name.strip()
        rec = db_get(name)
        if not rec:
            return pb.GetMountResponse(found=False)
        mounted = is_mounted_at(rec["mount_point"])
        me = pb.MountEntry(
            name=rec["name"],
            src_dirs=rec["src_dirs"],
            mount_point=rec["mount_point"],
            mounted=mounted,
            mount_opts=rec["mount_opts"],
            created_at=rec["created_at"],
        )
        return pb.GetMountResponse(found=True, entry=me)


def serve(
    listen_addr="0.0.0.0:50051",
    certfile="server.crt",
    keyfile="server.key",
    ca_cert="ca.crt",
):
    init_db()
    init_base_mount()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    grpc_pb.add_MountManagerServicer_to_server(MountManagerServicer(), server)

    # mTLS server credentials
    with open(certfile, "rb") as f:
        server_cert = f.read()
    with open(keyfile, "rb") as f:
        server_key = f.read()
    with open(ca_cert, "rb") as f:
        ca = f.read()

    server_credentials = grpc.ssl_server_credentials(
        [(server_key, server_cert)], root_certificates=ca, require_client_auth=True
    )
    host, port = listen_addr.split(":")
    server.add_secure_port(listen_addr, server_credentials)
    print(f"gRPC MountManager listening on {listen_addr} with mTLS")
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0:50051")
    parser.add_argument("--server-cert", default="server.crt")
    parser.add_argument("--server-key", default="server.key")
    parser.add_argument("--ca-cert", default="ca.crt")
    args = parser.parse_args()
    serve(
        listen_addr=args.listen,
        certfile=args.server_cert,
        keyfile=args.server_key,
        ca_cert=args.ca_cert,
    )
