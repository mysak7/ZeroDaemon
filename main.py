"""Entry point — run with: python main.py  or  uvicorn main:app"""

import uvicorn
from zerodaemon.api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8222, reload=False, log_level="info")
