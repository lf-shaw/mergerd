#!/usr/bin/env python3
import grpc
from mergerd import mount_manager_pb2 as pb
from mergerd import mount_manager_pb2_grpc as grpc_pb
import argparse


def create_stub(addr, ca, cert, key):
    with open(ca, "rb") as f:
        root_cert = f.read()
    with open(cert, "rb") as f:
        client_cert = f.read()
    with open(key, "rb") as f:
        client_key = f.read()

    creds = grpc.ssl_channel_credentials(
        root_certificates=root_cert,
        private_key=client_key,
        certificate_chain=client_cert,
    )
    channel = grpc.secure_channel(addr, creds)
    # optionally wait for connectivity
    return grpc_pb.MountManagerStub(channel)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--addr", default="localhost:50051")
    parser.add_argument("--ca", default="./cert/ca.crt")
    parser.add_argument("--cert", default="./cert/client.crt")
    parser.add_argument("--key", default="./cert/client.key")
    sub = parser.add_subparsers(dest="cmd")

    p1 = sub.add_parser("create")
    p1.add_argument("--dest", required=True)
    p1.add_argument("--src", nargs="+", required=True)
    p1.add_argument("--force", action="store_true")

    p2 = sub.add_parser("remove")
    p2.add_argument("--dest", required=True)
    p2.add_argument("--force", action="store_true")

    p3 = sub.add_parser("list")

    p4 = sub.add_parser("get")
    p4.add_argument("--name", required=True)

    args = parser.parse_args()
    stub = create_stub(args.addr, args.ca, args.cert, args.key)

    if args.cmd == "create":
        req = pb.CreateMountRequest(
            dest_path=args.dest,
            branches=args.src,
            allow_force_unmount=args.force,
        )
        resp = stub.CreateMount(req)
        print("OK:", resp.ok, "msg:", resp.message)
    elif args.cmd == "remove":
        req = pb.RemoveMountRequest(dest_path=args.dest, force=args.force)
        resp = stub.RemoveMount(req)
        print("OK:", resp.ok, "msg:", resp.message)
    elif args.cmd == "list":
        resp = stub.ListMounts(pb.ListMountsRequest())
        for e in resp.entries:
            print(
                e.dest_path,
                "mounted=",
                e.mounted,
                "branches=",
                list(e.branches),
            )
    elif args.cmd == "get":
        resp = stub.GetMount(pb.GetMountRequest(dest_path=args.dest))
        if not resp.found:
            print("Not found")
        else:
            e = resp.entry
            print("Mount point:", e.dest_path)
            print("Mounted:", e.mounted)
            print("Sources:", list(e.branches))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
