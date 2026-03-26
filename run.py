from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.getenv("PORT", "3000"))
    uvicorn.run("backend.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    main()
