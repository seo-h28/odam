# 오담

소득 계층 변화 지도와, 소득별·청년 취업 지원 제도를 찾아보는 웹앱입니다.

- 원본 화면: `class-map.html` (Claude 아티팩트와 같은 파일)
- 배포용 화면: `docs/index.html` (GitHub Pages가 이 폴더를 배포)

## 화면을 고친 뒤 배포하는 법

```
python build_site.py
git add .
git commit -m "변경 내용"
git push
```

GitHub Pages에서는 Claude 로그인 기능이 없으므로 질문하기는 미리 써 둔 답과 제도 정보 검색으로 답합니다.

지원 제도 결과는 예상이며, 최종 판단은 담당 기관이 합니다.
