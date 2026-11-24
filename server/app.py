"""오담 질문 서버: 화면에서 받은 질문을 내 컴퓨터의 Ollama에 넘겨 답을 받는다.

하는 일
  GET  /             docs/index.html(배포용 화면)을 보여 준다. 이 서버가 켜져 있다는 표시를 붙여서.
  GET  /api/health   서버와 Ollama가 살아 있는지 알려 준다.
  POST /api/ask      {program, rows, context, question} → {answer, model}

실행: server 폴더에서  uvicorn app:app --port 8000
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("ODAM_MODEL", "exaone3.5:7.8b")
DOCS = Path(__file__).resolve().parent.parent / "docs"

# 한 사람(IP)이 1분에 보낼 수 있는 질문 수. 내 컴퓨터를 지키는 장치다.
RATE_PER_MIN = int(os.getenv("ODAM_RATE_PER_MIN", "6"))

app = FastAPI(title="오담 질문 서버")

# 다른 주소의 화면(GitHub Pages)이 이 서버를 부를 수 있게 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://seo-h28.github.io", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class Ask(BaseModel):
    # 길이 제한: 이 서버를 아무 글이나 넣는 무료 AI로 쓰지 못하게 한다.
    program: str = Field(max_length=60)
    rows: list[tuple[str, str]] = Field(max_length=8)
    context: str = Field(default="", max_length=300)
    question: str = Field(min_length=1, max_length=200)


SYSTEM = (
    "당신은 한국 정부 지원 제도 안내 도우미입니다. 아래 [제도 정보]만 근거로 [질문]에 쉬운 한국어로 답하세요. "
    "제도 정보에 없는 내용은 지어내지 말고 '이 자료에는 없습니다'라고 말한 뒤 담당 기관에 확인하라고 안내하세요. "
    "받을 수 있다고 단정하지 말고 '조건에 맞을 수 있습니다'처럼 말하세요. "
    "지원 제도와 관계없는 질문에는 답하지 말고 이 제도에 대해 물어봐 달라고 하세요. "
    "4문장 이내로 답하고, 마지막 줄에 근거로 쓴 항목 이름을 '근거: 항목' 형식으로 적으세요."
)

_hits: dict[str, deque[float]] = defaultdict(deque)


def check_rate(ip: str) -> None:
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(429, "질문이 너무 많습니다. 1분 뒤에 다시 해 주세요.")
    q.append(now)


@app.get("/api/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_URL}/api/version")
        return {"ok": r.status_code == 200, "model": MODEL}
    except httpx.HTTPError:
        return {"ok": False, "model": MODEL}


@app.post("/api/ask")
async def ask(body: Ask, request: Request) -> dict:
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    check_rate(ip)
    info = "\n".join(f"{k[:20]}: {v[:600]}" for k, v in body.rows)
    user = f"[제도] {body.program}\n[제도 정보]\n{info}\n[질문자 상황] {body.context}\n[질문]\n{body.question}"
    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "options": {"temperature": 0.2, "num_predict": 350},
    }
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(503, f"AI 모델에 연결하지 못했습니다: {type(e).__name__}") from e
    text = r.json().get("message", {}).get("content", "").strip()
    return {"answer": text, "model": MODEL}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    # 이 서버로 화면을 열면 화면이 같은 주소의 /api/ask를 쓰도록 알려 준다.
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    return html.replace("<head>", '<head><script>window.ODAM_API="api/ask"</script>', 1)


app.mount("/", StaticFiles(directory=DOCS), name="docs")
