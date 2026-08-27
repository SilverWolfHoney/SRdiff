import sys
from grpc_tools import protoc
d = r"E:\HSRBetaPS\SRdiff"
rc = protoc.main([
    "protoc",
    "-I", d,
    "--python_out", d,
    d + r"\manifest.proto",
    d + r"\manifest_ldiff.proto",
])
sys.exit(rc)
