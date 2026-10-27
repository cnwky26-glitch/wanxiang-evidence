"""一键入口：python start_demo.py；自定义端口：python start_demo.py --port 8511。"""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="启动万象奔策本地 Streamlit 演示")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    missing = [name for name in ("streamlit", "plotly") if importlib.util.find_spec(name) is None]
    if missing:
        print("缺少依赖：" + ", ".join(missing))
        print("请先运行：python -m pip install -r frontend/requirements.txt")
        return 1
    root = Path(__file__).resolve().parent
    command = [sys.executable, "-m", "streamlit", "run", str(root / "frontend" / "app.py"),
               "--server.address", "127.0.0.1", "--server.port", str(args.port),
               "--browser.gatherUsageStats", "false"]
    try:
        return subprocess.call(command, cwd=root)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
