"""Track-Pay desktop launcher: Python webview hosting the Svelte UI."""
import os
import sys
from pathlib import Path

import webview

from api import Api

BASE = Path(__file__).resolve().parent


def main():
    dev = os.environ.get("TRACKPAY_DEV") == "1"   # `npm run dev` server on :5173
    if dev:
        url = "http://localhost:5173"
    else:
        index = BASE / "frontend" / "dist" / "index.html"
        if not index.exists():
            sys.exit("The UI is not built yet. Run:  cd frontend && npm install && npm run build")
        url = str(index)

    webview.create_window("Track-Pay", url, js_api=Api(), width=1380, height=880,
                          min_size=(1050, 640))
    webview.start(debug=dev, http_server=not dev)


if __name__ == "__main__":
    main()
