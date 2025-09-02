from mergerd.server import serve

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
