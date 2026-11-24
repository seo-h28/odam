"""GitHub Pages용 페이지를 만든다.

class-map.html은 Claude 아티팩트용이라 <!doctype>, <head> 없이 내용만 있다.
아티팩트는 게시할 때 이 뼈대를 자동으로 붙이지만 GitHub Pages는 그렇지 않으므로,
같은 뼈대를 붙여 docs/index.html로 저장하고 로고 파일을 복사한다.

사용법: python build_site.py
      python build_site.py --api https://주소/api/ask   (외부에 연 질문 서버를 Pages 화면에 연결)
"""
from pathlib import Path
import argparse
import json
import shutil

ROOT = Path(__file__).parent
SRC = ROOT / "class-map.html"
OUT = ROOT / "docs"

HEAD = (
    "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
    "<link rel=\"icon\" href=\"assets/odam-logo.webp\" type=\"image/webp\">"
    "<style>:root{color-scheme:light;box-sizing:border-box}"
    "body{margin:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif}"
    "img{max-width:100%}[hidden]{display:none!important}</style>"
    "</head><body>\n"
)
TAIL = "\n</body></html>\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="", help="질문 서버 주소. 비우면 화면이 자동 안내로만 답한다")
    api = ap.parse_args().api
    OUT.mkdir(exist_ok=True)
    body = SRC.read_text(encoding="utf-8")
    head = HEAD
    if api:
        head = HEAD.replace("<head>", "<head><script>window.ODAM_API=" + json.dumps(api) + "</script>", 1)
    (OUT / "index.html").write_text(head + body + TAIL, encoding="utf-8")
    shutil.copytree(ROOT / "assets", OUT / "assets", dirs_exist_ok=True)
    # Jekyll 처리를 끄면 파일이 있는 그대로 배포된다.
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print("docs/index.html 생성 완료")


if __name__ == "__main__":
    main()
