"""星环首页的临时演示服务（仅供截图/浏览器验证脚本调用，不参与应用运行）。

为什么不用 `python -m health_agent.cli serve`：
1) 截图脚本不能碰真实数据库——这里把库**复制**到临时目录再起服务；
2) 必须关掉访问密码，否则无头浏览器会被登录页挡住；
3) 端口、数据源都可参数化，方便同一台机器上重复跑。

用法：python scripts/_ring_server.py <源库路径> <临时目录> <端口>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import uvicorn

from health_agent.config import load_settings
from health_agent.ui.app import create_app


def main() -> None:
    source = Path(sys.argv[1])
    workdir = Path(sys.argv[2])
    port = int(sys.argv[3])
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "health.db"
    shutil.copyfile(source, target)

    settings = load_settings(
        {
            "HEALTH_AGENT_PASSWORD_HASH": "",
            "HEALTH_AGENT_LAN": "0",
            "AI_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        },
        project_root=workdir,
    )
    app = create_app(settings, db_path=target)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
