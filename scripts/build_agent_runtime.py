"""Build the portable runtime wheel from Core's single canonical source tree."""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="postbridge-agent-wheel-") as directory:
        build = Path(directory)
        shutil.copyfile(source / "agent-runtime/pyproject.toml", build / "pyproject.toml")
        shutil.copytree(source / "src/postbridge_agent_runtime", build / "src/postbridge_agent_runtime",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(output), str(build)], check=True)


if __name__ == "__main__":
    main()
