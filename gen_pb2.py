#!/usr/bin/env python3
# gen_pb2.py - 用 protoc 从 manifest*.proto 生成 manifest*_pb2.py
# 仅在修改了 .proto 之后才需要跑; 生成结果已随仓库提交, 平时直接运行 sophon_update.py 即可。
# 依赖: pip install grpcio-tools
import pathlib
import sys

try:
    from grpc_tools import protoc
except ImportError:
    sys.exit("缺少 grpc_tools, 请先安装: pip install grpcio-tools")

HERE = pathlib.Path(__file__).resolve().parent
rc = protoc.main([
    "protoc",
    "-I", str(HERE),
    "--python_out", str(HERE),
    str(HERE / "manifest.proto"),
    str(HERE / "manifest_ldiff.proto"),
])
sys.exit(rc)
