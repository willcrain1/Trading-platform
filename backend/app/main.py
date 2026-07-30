from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import backtest, broker, congress, cot, macro, options, paper, portfolio, relval, scan, ticker, universe
from .trading import broker_config, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker_config.apply_config()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Trading Analysis Platform", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (ticker.router, macro.router, options.router, relval.router,
          backtest.router, paper.router, scan.router, broker.router, portfolio.router,
          universe.router, cot.router, congress.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
