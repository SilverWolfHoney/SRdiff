#!/usr/bin/env python3
# build_exe.py - 把 apply_ldiff.py 打包成单文件 exe(给别人双击用)
#
# 用法:
#   pip install pyinstaller
#   python build_exe.py
#
# 产物: dist/SRdiff_apply.exe  —— 双击即用(交互式问两个路径), 也可命令行带参数调用。
# 注意: 打包机需要装 hdiffpatch(它会被一起打进 exe), 接收方什么都不用装。
import subprocess, sys, pathlib, shutil

SCRIPT = "apply_ldiff.py"
EXE_NAME = "SRdiff_apply"      # 用 ASCII 名, 避免中文名在部分环境下的编码/兼容问题
HERE = pathlib.Path(__file__).resolve().parent


def main():
    if not (HERE / SCRIPT).is_file():
        print(f"找不到 {SCRIPT}, 请在仓库根目录运行"); sys.exit(1)

    # 依赖检查: hdiffpatch 必须能在打包机上 import, 否则会被漏掉
    for mod, hint in (("hdiffpatch", "pip install hdiffpatch"),
                      ("PyInstaller", "pip install pyinstaller")):
        try:
            __import__(mod)
        except ImportError:
            if mod == "PyInstaller":
                print("缺少 PyInstaller, 请先安装:  pip install pyinstaller"); sys.exit(1)
            print(f"警告: 打包机上没有 {mod}, 打出来的 exe 会缺少差分功能。请先: {hint}")

    for d in ("build", "dist"):
        shutil.rmtree(HERE / d, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--console", "--clean", "--noconfirm",
        "--name", EXE_NAME,
        "--hidden-import", "hdiffpatch",
        # 这几个能显著减小体积, 且本工具用不到
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "doctest",
        "--exclude-module", "pydoc",
        SCRIPT,
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
    print(f"  发给别人时, 让他在差分包目录里双击运行即可(或命令行: {exe.name} --gamedir <客户端> --pack <差分包>)")


if __name__ == "__main__":
    main()
