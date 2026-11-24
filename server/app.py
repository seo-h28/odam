"""오담 질문 서버: 화면에서 받은 질문을 내 컴퓨터의 Ollama에 넘겨 답을 받는다.

하는 일
  GET  /             docs/index.html(배포용 화면)을 보여 준다. 이 서버가 켜져 있다는 표시를 붙여서.
  GET  /api/health   서버와 Ollama가 살아 있는지 알려 준다.
  POST /api/ask      {program, rows, context, question} → 답을 글자 단위로 흘려보낸다(스트리밍)
  POST /api/worry    {messages, catalog} → 공감하는 답을 흘려보내고, 마지막 줄에 "제도: id, id"

속도를 위해 하는 일
  - 서버가 켜질 때 모델을 미리 불러오고, 30분 동안 내리지 않게 한다(keep_alive).
  - 답이 다 만들어질 때까지 기다리지 않고 쓰는 대로 화면에 보낸다(스트리밍).
  - 제도 목록은 id·이름·분야만 보내 AI가 읽을 글을 줄인다.

실행: server 폴더에서  uvicorn app:app --port 8000
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("ODAM_MODEL", "exaone3.5:7.8b")
KEEP_ALIVE = os.getenv("ODAM_KEEP_ALIVE", "30m")
DOCS = Path(__file__).resolve().parent.parent / "docs"

# 한 사람(IP)이 1분에 보낼 수 있는 질문 수. 내 컴퓨터를 지키는 장치다.
RATE_PER_MIN = int(os.getenv("ODAM_RATE_PER_MIN", "6"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 서버가 켜질 때 모델을 미리 불러 둔다. 첫 질문이 14초 넘게 기다리지 않게.
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            await c.post(f"{OLLAMA_URL}/api/generate", json={"model": MODEL, "prompt": "", "keep_alive": KEEP_ALIVE})
    except httpx.HTTPError:
        pass  # Ollama가 꺼져 있어도 서버는 뜬다. 화면은 자동 안내로 답한다.
    yield


app = FastAPI(title="오담 질문 서버", lifespan=lifespan)

# 다른 주소의 화면(GitHub Pages)이 이 서버를 부를 수 있게 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://seo-h28.github.io", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_hits: dict[str, deque[float]] = defaultdict(deque)


def check_rate(request: Request) -> None:
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(429, "질문이 너무 많습니다. 1분 뒤에 다시 해 주세요.")
    q.append(now)


async def stream_chat(messages: list[dict], options: dict) -> AsyncIterator[str]:
    """Ollama에 묻고, 만들어지는 글을 조각마다 바로 내보낸다."""
    payload = {"model": MODEL, "stream": True, "keep_alive": KEEP_ALIVE, "messages": messages, "options": options}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5)) as c:
            async with c.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    piece = json.loads(line).get("message", {}).get("content", "")
                    if piece:
                        yield piece
    except httpx.HTTPError:
        yield "\n[오류] AI 모델에 연결하지 못했습니다."


def text_stream(gen: AsyncIterator[str]) -> StreamingResponse:
    # X-Model: 화면이 어떤 모델이 답했는지 표시할 때 쓴다.
    return StreamingResponse(gen, media_type="text/plain; charset=utf-8",
                             headers={"X-Model": MODEL, "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Model"})


@app.get("/api/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA_URL}/api/version")
        return {"ok": r.status_code == 200, "model": MODEL}
    except httpx.HTTPError:
        return {"ok": False, "model": MODEL}


class Ask(BaseModel):
    # 길이 제한: 이 서버를 아무 글이나 넣는 무료 AI로 쓰지 못하게 한다.
    program: str = Field(max_length=60)
    rows: list[tuple[str, str]] = Field(max_length=8)
    context: str = Field(default="", max_length=300)
    question: str = Field(min_length=1, max_length=200)


SYSTEM = (
    "당신은 한국 정부 지원 제도 안내 도우미입니다. [제도 정보]만 근거로 [질문]에 쉬운 한국어로 답하세요. "
    "정보에 없으면 지어내지 말고 '이 자료에는 없습니다'라고 한 뒤 담당 기관에 확인하라고 하세요. "
    "받을 수 있다고 단정하지 마세요. 지원 제도와 관계없는 질문에는 답하지 마세요. "
    "3문장 이내로 답하고, 마지막 줄에 '근거: 항목'을 적으세요."
)


@app.post("/api/ask")
async def ask(body: Ask, request: Request) -> StreamingResponse:
    check_rate(request)
    info = "\n".join(f"{k[:20]}: {v[:500]}" for k, v in body.rows)
    user = f"[제도] {body.program}\n[제도 정보]\n{info}\n[질문자 상황] {body.context}\n[질문]\n{body.question}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    return text_stream(stream_chat(messages, {"temperature": 0.2, "num_predict": 220}))


class Msg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=600)


class Program(BaseModel):
    id: str = Field(max_length=30)
    name: str = Field(max_length=60)
    cat: str = Field(default="", max_length=10)
    scope: str = Field(default="전국", max_length=10)
    summary: str = Field(default="", max_length=200)


class Worry(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=12)
    catalog: list[Program] = Field(max_length=40)


WORRY_SYSTEM = (
    "당신은 정부 지원 제도 안내 서비스 '오담'의 상담 도우미입니다. 고민을 먼저 공감하며 들어 주세요. "
    "판단하거나 훈계하지 말고, 3문장 이내의 쉬운 한국어로 답합니다. 받을 수 있다고 단정하지 않습니다. "
    "빚 고민이면 서민금융진흥원 1397, 우울하거나 지친 마음이면 정신건강 위기상담전화 1577-0199를 안내합니다. "
    "죽고 싶다, 자해 같은 말이 있으면 가장 먼저 자살예방 상담전화 109를 권합니다. "
    "본문에서 제도를 말할 때는 이름만 쓰고 id나 괄호 속 분야는 쓰지 않습니다. "
    "답의 맨 마지막 줄에는 [제도 목록]에서 도움이 될 제도 id를 최대 4개 '제도: id, id' 형식으로 적습니다. 목록에 없는 id는 쓰지 않습니다."
)


@app.post("/api/worry")
async def worry(body: Worry, request: Request) -> StreamingResponse:
    """고민을 듣고 공감하는 답을 흘려보낸다. 대화 내용은 저장하지 않는다."""
    check_rate(request)
    # id·이름·분야만: 목록이 짧을수록 AI가 읽는 시간이 줄어든다. 같은 앞부분은 Ollama가 재사용한다.
    catalog = "\n".join(f"{p.id} {p.name} ({p.cat}{', 부산' if p.scope == '부산' else ''})" for p in body.catalog)
    messages = [{"role": "system", "content": WORRY_SYSTEM + "\n\n[제도 목록]\n" + catalog}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages[-6:]]
    return text_stream(stream_chat(messages, {"temperature": 0.4, "num_predict": 260}))


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    # 이 서버로 화면을 열면 화면이 같은 주소의 /api/ask를 쓰도록 알려 준다.
    html = (DOCS / "index.html").read_text(encoding="utf-8")
    return html.replace("<head>", '<head><script>window.ODAM_API="api/ask"</script>', 1)


app.mount("/", StaticFiles(directory=DOCS), name="docs")
