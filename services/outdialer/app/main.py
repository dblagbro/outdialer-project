import os

import uvicorn

from .api import app
from .db import init_db
from .worker import run_worker


def main() -> None:
    init_db()
    if os.getenv("APP_ROLE", "api") == "worker":
        run_worker()
    else:
        uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
