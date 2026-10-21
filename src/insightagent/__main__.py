from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(prog="insight-agent")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    uvicorn.run("insightagent.api:app_factory", factory=True, host=a.host, port=a.port,
                log_level="warning")


if __name__ == "__main__":
    main()
