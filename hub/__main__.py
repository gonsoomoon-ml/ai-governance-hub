"""`python -m hub` — uvicorn 으로 허브를 띄운다(컨테이너 진입점). 포트는 HUB_PORT(기본 8080).

`python -m hub` — run the hub under uvicorn (container entrypoint). Port from HUB_PORT (default 8080).
"""

from __future__ import annotations

import os

import uvicorn

from hub.main import create_app


def main() -> None:
    """앱을 만들어 0.0.0.0:HUB_PORT 에서 돌린다.

    Build the app and serve it on 0.0.0.0:HUB_PORT.
    """
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("HUB_PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
