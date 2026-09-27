#!/usr/bin/env python3
# build_exe.py - 把语音包工具打包成单文件 exe(方便直接发给别人用)
#
# 用法:
#   pip install pyinstaller
#   python build_exe.py
#
# 产物: dist/SRdiff_voice.exe
#   - 双击即用(交互式询问), 也可命令行: SRdiff_voice.exe --gamedir <客户端> --lang cn
#   - 依赖(protobuf / zstandard / hdiffpatch 不需要, 但 protobuf+zstandard 必须)全部打进 exe,
#     接收方不需要装 Python。
import shutil, subprocess, sys, pathlib

ENTRY = "voice_pack.py"
EXE_NAME = "SRdiff_voice"
HERE = pathlib.Path(__file__).resolve().parent


def main():
    if not (HERE / ENTRY).is_file():
        print(f"找不到 {ENTRY}, 请在仓库根目录运行"); sys.exit(1)

    # 依赖检查
    missing = []
    for mod, pkg in (("zstandard", "zstandard"), ("google.protobuf", "protobuf"), ("PyInstaller", "pyinstaller")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("缺少依赖, 请先安装:  pip install " + " ".join(missing)); sys.exit(1)

    for d in ("build", "dist"):
        shutil.rmtree(HERE / d, ignore_errors=True)

    # exe 正在运行/被杀软扫描时 Windows 不允许覆盖, 提前给清楚的提示
    old = HERE / "dist" / (EXE_NAME + ".exe")
    if old.exists():
        try:
            old.unlink()
        except PermissionError:
            print(f"无法覆盖 {old}\n  它可能正在运行, 或被杀毒软件占用。请关掉后重试。")
            sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--console", "--clean", "--noconfirm",
        "--name", EXE_NAME,
        # 延迟导入的模块(函数内部 import), 静态分析可能漏掉, 显式声明
        "--hidden-import", "manifest_pb2",
        "--hidden-import", "sophon_update",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "doctest",
        "--exclude-module", "pydoc",
        ENTRY,
    ]
    print("执行:", " ".join(cmd), "\n")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print(f"\n打包失败 (退出码 {r.returncode})"); sys.exit(r.returncode)

    exe = HERE / "dist" / (EXE_NAME + ".exe")
    if not exe.is_file():
        print("\n打包命令成功, 但没找到产物:", exe); sys.exit(1)
    print(f"\n打包完成: {exe}")
    print(f"  大小: {exe.stat().st_size / 2**20:.2f} MB")
    print(f"  自测: {exe.name} --version")
    print(f"  用法: 双击运行(交互式), 或 {exe.name} --gamedir <客户端根目录> --lang cn,jp")


if __name__ == "__main__":
    main()
