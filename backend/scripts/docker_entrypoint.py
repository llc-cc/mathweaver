"""容器入口：从只读 secret 文件加载后端变量，再降权并替换当前进程。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Sequence

from dotenv import dotenv_values


DEFAULT_RUNTIME_ENV_FILE = Path("/run/secrets/mathweaver_backend.env")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_runtime_environment(path: Path = DEFAULT_RUNTIME_ENV_FILE) -> None:
    """静默加载 secret；Compose 固定变量优先，防止密钥文件绕过数据库边界。"""

    try:
        values = dotenv_values(path, encoding="utf-8", interpolate=False)
    except (OSError, UnicodeError):
        raise RuntimeError("runtime environment loading failed") from None

    if not values:
        raise RuntimeError("runtime environment loading failed")
    for name, value in values.items():
        if not _ENVIRONMENT_NAME.fullmatch(name) or value is None:
            raise RuntimeError("runtime environment loading failed")
        os.environ.setdefault(name, value)


def drop_root_privileges(user_name: str = "mathweaver") -> None:
    """仅 Compose 以 root 读取 secret；执行应用前立即回落到镜像内专用用户。"""

    if os.geteuid() != 0:
        return
    # `pwd` 仅存在于 Linux；延迟导入使 secret 解析逻辑仍可在 Windows CI 单测。
    import pwd

    account = pwd.getpwnam(user_name)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def main(arguments: Sequence[str] | None = None) -> int:
    command = list(arguments if arguments is not None else sys.argv[1:])
    if not command:
        print("container command is required", file=sys.stderr)
        return 64
    try:
        load_runtime_environment()
        drop_root_privileges()
    except (KeyError, RuntimeError):
        # 错误保持固定文本，不把 secret 路径、变量值或解析异常写入日志。
        print("runtime environment loading failed", file=sys.stderr)
        return 78
    os.execvp(command[0], command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

