import json
import subprocess
import sys

from broader_vids.windows_job import await_start


def main():
    await_start(sys.argv[1])
    process = subprocess.Popen(
        json.loads(sys.argv[2]),
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
    )
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
