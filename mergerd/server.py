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


# DB_PATH: sqlite3 数据库文件路径，记录挂载信息
DB_PATH = os.getenv("DB_PATH", "./mergerd.db")


def validate_path(
    path: str, base_dir: str | None = None, must_exist: bool = False
) -> str:
    """校验 Linux 路径是否合法，并返回标准化路径。

    要求：
      - 不能为空
      - 必须是字符串
      - 必须是绝对路径
      - 不能包含空格
      - 不能包含 null 字符 \0
      - 可选：必须在 base_dir 范围内


    Notes
    ------
    自动移除首位的空白字符，并标准化路径（去除多余的 / 和 . 等）。

    Parameters
    ----------
    path : str
        待校验绝对路径
    base_dir : str or None, optional
        可选限制的根目录。如果提供，必须确保 path 在 base_dir 内。
    must_exist : bool, optional
        如果为 True，要求路径必须存在，否则抛出异常, by default False

    Returns
    -------
        str: 标准化路径 (绝对路径)

    Raises
    ------
    ValueError
        路径非法时抛出
    FileNotFoundError
        must_exist 为 True 且路径不存在时抛出
    """
    path = path.strip()

    if not path or not isinstance(path, str):
        raise ValueError("路径不能为空，且必须是字符串")

    if "\0" in path:
        raise ValueError("路径包含非法的 null 字符 '\\0'")

    if " " in path:
        raise ValueError("路径不能包含空格")

    if not path.startswith("/"):
        raise ValueError(f"路径必须是绝对路径: {path}")

    # 标准化路径
    norm_path = os.path.normpath(path)

    # 解析符号链接，得到真实路径
    real_path = os.path.realpath(norm_path)

    # 如果指定了 base_dir，检查是否越界
    if base_dir:
        base_dir_real = os.path.realpath(os.path.normpath(base_dir))
        if not os.path.commonpath([real_path, base_dir_real]) == base_dir_real:
            raise ValueError(f"路径 {real_path} 不在允许的根目录 {base_dir_real} 内")

    # 如果必须存在，则检查
    if must_exist and not os.path.exists(real_path):
        raise FileNotFoundError(f"路径不存在: {real_path}")

    return real_path


def is_mounted(path):
    """检查 path 是否已挂载"""
    result = subprocess.run(["mount"], capture_output=True, text=True)
    return any(path in line for line in result.stdout.splitlines())


def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS mounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dest_path TEXT UNIQUE,
            branches TEXT,
            mount_opts TEXT,
            created_at TEXT
        );
        """
        )
        conn.commit()
        conn.close()


def db_upsert_mount(dest_path, branches, mount_opts):
    """
    Upsert 一个挂载记录到 mounts 表：
    - 如果 dest_path 已存在，则更新 branches 和 mount_opts
    - 否则插入新记录

    Parameters
    ----------
    dest_path : str
        目标挂载路径（绝对路径）
    branches : list[str]
        源目录数组（绝对路径）
    mount_opts : str
        mergerfs mount options
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO mounts (dest_path, branches, mount_opts, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(dest_path) DO UPDATE SET
                branches=excluded.branches,
                mount_opts=excluded.mount_opts,
                created_at=excluded.created_at
            """,
            (
                os.path.normpath(dest_path),
                ":".join(branches),
                mount_opts or "",
                now,
            ),
        )
        conn.commit()
        conn.close()


def db_delete_mount(dest_path: str, recursive: bool = False):
    """删除挂载记录

    Parameters
    ----------
    dest_path : str
        目标挂载路径（绝对路径）
    recursive : bool, optional
        是否递归删除所有 dest_path 下的子路径, by default False
    """
    dest_path = os.path.normpath(dest_path)
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        if recursive:
            cur.execute("DELETE FROM mounts WHERE dest_path like ?", (dest_path + "%",))
        else:
            cur.execute("DELETE FROM mounts WHERE dest_path=?", (dest_path,))
        conn.commit()
        conn.close()


def db_get_all():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()
        cur.execute("SELECT dest_path, branches, mount_opts, created_at FROM mounts")
        rows = cur.fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append(
                {
                    "dest_path": r[0],
                    "branches": r[1].split(":") if r[1] else [],
                    "mount_opts": r[2] or "",
                    "created_at": r[3],
                }
            )
        return result


def db_get(dest_path: str, recursive: bool = False) -> dict | list[dict] | None:
    """获取挂载记录

    Parameters
    ----------
    dest_path : str
        目标挂载路径（绝对路径）
    recursive : bool, optional
        是否递归查询所有 dest_path 下的子路径, by default False


    Returns
    -------
    dict or list[dict] or None
        如果 recursive 为 False，返回单个 dict 或 None（未找到）；如果 recursive 为 True，返回所有匹配的 dict 列表或 []（未找到）
    """
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cur = conn.cursor()

        if recursive:
            # 查询 dest_path 下所有
            cur.execute(
                "SELECT dest_path, branches, mount_opts, created_at FROM mounts WHERE dest_path like ?",
                (dest_path + "%",),
            )
        else:
            # 查询单条
            cur.execute(
                "SELECT dest_path, branches, mount_opts, created_at FROM mounts WHERE dest_path=?",
                (dest_path,),
            )

        rows = cur.fetchall()
        conn.close()

        if not rows:
            return None if recursive else []

        result = []
        for r in rows:
            result.append(
                {
                    "dest_path": r[0],
                    "branches": r[1].split(":") if r[2] else [],
                    "mount_opts": r[2] or "",
                    "created_at": r[3],
                }
            )

        # 如果原来查询单个字符串，返回单条 dict
        if recursive:
            return result
        return result[0]


def run_cmd(cmd, check=True):
    """Run a command and return the result"""
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
    """List all system mounts"""
    proc = run_cmd("mount", check=True)
    # Return raw output lines
    return proc.stdout.splitlines()


def is_mounted_at(mount_point):
    """Check if a mount point is mounted"""
    lines = list_system_mounts()
    for l in lines:
        # simple substring check: ' on /tmp/data/name '
        if f" on {mount_point} " in l:
            return True
    return False


def find_mergerfs_mounts():
    """Find all mergerfs mounts"""
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
    def CreateMount(self, request: pb.CreateMountRequest, context):
        try:
            dest_path = validate_path(request.dest_path.strip())
        except:
            return pb.CreateMountResponse(
                ok=False, message="dest_path is not a valid absolute path"
            )

        if os.path.exists(dest_path) and not os.path.isdir(dest_path):
            return pb.CreateMountResponse(
                ok=False, message=f"{dest_path} exists and is not a directory"
            )

        branches = []
        for s in request.branches:
            try:
                branches.append(validate_path(s, must_exist=True))
            except FileNotFoundError:
                return pb.CreateMountResponse(
                    ok=False, message=f"branches {s} does not exist"
                )
            except Exception:
                return pb.CreateMountResponse(
                    ok=False, message=f"branches {s} is not a valid absolute path"
                )

        if not branches:
            return pb.CreateMountResponse(
                ok=False, message="at least one branches required"
            )

        allow_force = request.allow_force_unmount
        options = request.options or ""

        # ensure branches dirs exist
        for s in branches:
            if not os.path.isdir(s):
                return pb.CreateMountResponse(
                    ok=False, message=f"branches {s} does not exist"
                )

        os.makedirs(dest_path, exist_ok=True)

        # check for duplicate names in DB
        existing = db_get(dest_path)
        if existing:
            # verify actual mount status
            if is_mounted_at(dest_path):
                if not allow_force:
                    return pb.CreateMountResponse(
                        ok=False,
                        message=f"path {dest_path} already exists and is mounted",
                    )
                # else try to fusermount -uz then continue
                try:
                    run_cmd(["fusermount", "-uz", dest_path], check=False)
                except Exception as e:
                    # we ignore non-zero here and continue to regular mount attempt
                    pass

        # produce mergerfs command
        # mergerfs SRC1:SRC2:... mount_point -o defaults,allow_other,use_ino,<options>
        src_spec = ":".join(branches)
        mount_opts = "defaults,allow_other,use_ino"
        if options:
            mount_opts += "," + options

        cmd = ["mergerfs", src_spec, dest_path, "-o", mount_opts]
        try:
            run_cmd(cmd, check=True)
        except Exception as e:
            return pb.CreateMountResponse(ok=False, message=f"mount failed: {e}")

        # verify mount shows up
        if not is_mounted_at(dest_path):
            return pb.CreateMountResponse(
                ok=False, message="mount succeeded but not visible in mount table"
            )

        # store in DB
        db_upsert_mount(dest_path, branches, mount_opts)
        return pb.CreateMountResponse(ok=True, message="mounted")

    def RemoveMount(self, request: pb.RemoveMountRequest, context):
        dest_path = request.dest_path.strip()
        recursive = request.recursive
        force = request.force
        if not dest_path:
            return pb.RemoveMountResponse(ok=False, message="dest_path required")
        rec = db_get(dest_path, recursive=recursive)
        if not rec:
            # perhaps still mounted but not in DB => allow removal
            if not is_mounted_at(dest_path):
                return pb.RemoveMountResponse(
                    ok=False, message=f"{dest_path} not found"
                )
        # try normal umount first
        try:
            run_cmd(["umount", dest_path], check=False)
        except Exception:
            pass

        # if still mounted and force, try fusermount -uz
        if is_mounted_at(dest_path):
            if force:
                try:
                    run_cmd(["fusermount", "-uz", dest_path], check=False)
                except Exception as e:
                    return pb.RemoveMountResponse(
                        ok=False, message=f"fusermount failed: {e}"
                    )

                # 删除空目录
                if os.path.isdir(dest_path) and not os.listdir(dest_path):
                    os.rmdir(dest_path)
            else:
                return pb.RemoveMountResponse(
                    ok=False,
                    message=f"{dest_path} still mounted; use force to try fusermount -uz",
                )

        # finally remove DB record
        db_delete_mount(dest_path)

        # we do not remove mount_point directory to avoid data loss
        return pb.RemoveMountResponse(ok=True, message="unmounted and removed from DB")

    def ListMounts(self, request: pb.ListMountsRequest, context):
        entries = []
        db_entries = db_get_all()
        # also cross-check with system mounts
        for d in db_entries:
            mounted = is_mounted_at(d["dest_path"])
            me = pb.MountEntry(
                dest_path=d["dest_path"],
                branches=d["branches"],
                mounted=mounted,
                mount_opts=d["mount_opts"],
                created_at=d["created_at"],
            )
            entries.append(me)
        return pb.ListMountsResponse(entries=entries)

    def GetMount(self, request: pb.GetMountRequest, context):
        name = request.dest_path.strip()
        rec: dict = db_get(name)  # type: ignore
        if not rec:
            return pb.GetMountResponse(found=False)
        mounted = is_mounted_at(rec["dest_path"])
        me = pb.MountEntry(
            dest_path=rec["dest_path"],
            branches=rec["branches"],
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
