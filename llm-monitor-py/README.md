# LLM 언급·인용 모니터링

보험 상담 관련 질문 세트를 ChatGPT와 Gemini에 보내고, 답변에서 토스인슈어런스가 언급·인용되는지를 측정하는 도구예요.

## 구조

```
questions.csv ─▶ llm_monitor.py ─┬─▶ OpenAI Responses API (웹검색)        ─┐
config.json  ─▶                 └─▶ Gemini API (구글 검색 그라운딩)      ─┴─▶ results/raw_*.csv     호출 1건 = 1행
                                                                            results/summary_*.csv 플랫폼×테마별 집계
```

| 파일 | 역할 |
|---|---|
| `questions.csv` | 질문 25개, 5개 테마 (보장 점검, 리모델링, 중복·정리, 상담 채널, 브랜드 직접). `사용(Y/N)`을 N으로 바꾸면 그 질문은 건너뜀 |
| `config.json` | 브랜드명·별칭·도메인, 경쟁사, 플랫폼, 반복 횟수, 모델명 |
| `llm_monitor.py` | 호출 → 응답 해석 → 언급·인용 판정 → CSV 저장 |
| `llm_monitor_colab.ipynb` | 설치 없이 구글 코랩에서 실행하는 노트북 |

## 실행 전에 채울 것

`config.json`에서 세 가지를 확인하세요.

- `brand_domains`: 토스인슈어런스 홈페이지 도메인 [확인 필요]. 비어 있으면 인용률이 0%로 나와요.
- `openai_model`, `gemini_model`: 웹검색·구글 검색 그라운딩을 지원하는 현재 모델명 [확인 필요]
- `openai_tool`: 오류가 나면 `web_search_preview`로 바꿔 보세요.

## 실행 방법

**GitHub Actions (자동 측정, 권장)**

`.github/workflows/measure.yml`이 GitHub 서버에서 측정을 돌리고, 결과 CSV를 `results/` 폴더에 자동으로 커밋해요. 매주 월요일 09:00(KST)에 자동 실행되고, 원할 때 수동으로도 실행할 수 있어요. 측정할 때마다 결과가 쌓이니까 주차별 변화를 저장소에서 바로 볼 수 있어요.

1. 저장소 **Settings → Secrets and variables → Actions → New repository secret**에서 `OPENAI_API_KEY`, `GEMINI_API_KEY`를 등록해요.
2. **Actions** 탭 → "LLM 언급·인용 측정" → **Run workflow**를 눌러요. 처음에는 `limit`에 1을 넣어 연결을 테스트해요.
3. 실행이 끝나면 `results/` 폴더에 `raw_*.csv`, `summary_*.csv`가 생겨요.

자동 실행이 필요 없으면 `measure.yml`의 `schedule` 두 줄을 지워요.

**구글 코랩 (설치 필요 없음)**

1. colab.research.google.com에서 `llm_monitor_colab.ipynb`를 업로드해 열어요.
2. 왼쪽 파일 패널에 `llm_monitor.py`, `config.json`, `questions.csv`를 끌어다 놓아요.
3. 셀을 위에서부터 실행해요. 연결 테스트 → 질문 5개 측정(비용 확인) → 전체 측정 → 결과 다운로드 순서예요.

**내 PC**

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...        # Windows PowerShell: $env:OPENAI_API_KEY="..."
export GEMINI_API_KEY=...
python llm_monitor.py --test     # 연결 테스트
python llm_monitor.py --limit 5  # 질문 5개만
python llm_monitor.py            # 전체
```

API 키는 코드나 파일에 적지 말고 환경변수로만 넣어요. GitHub에 올릴 때 키가 같이 올라가는 사고를 막기 위해서예요.

## 지표 정의

| 지표 | 정의 |
|---|---|
| 언급률 | 답변에 브랜드 별칭이 하나라도 나온 응답 비율 |
| 인용률 | 답변 출처(URL·출처 제목)에 브랜드 도메인이 포함된 응답 비율 |
| 언급 순위 | 답변 안에서 브랜드·경쟁사 중 몇 번째로 처음 등장했는지 (1 = 가장 먼저) |
| 함께 언급된 회사 | config의 경쟁사 중 같은 답변에 나온 회사 |

요약에는 테마별 행과 함께 "전체 (브랜드 직접 제외)" 행이 있어요. 브랜드를 직접 묻는 질문은 언급률이 높게 나오는 게 당연하니까, 진단 수치는 이 행을 기준으로 봐요.

## 해석할 때 주의할 점

- **API 기준 측정이에요.** 실제 ChatGPT·Gemini 앱과는 모델, 개인화, 검색 방식이 달라요. 결과는 "API 기준 외부 관찰 스냅샷"이라고 표기해요.
- **Gemini 인용 URL은 구글 리다이렉트 주소**로 오는 경우가 많아서, 출처 제목에 들어 있는 도메인까지 함께 확인해요.
- **별칭 설정이 결과를 좌우해요.** 첫 측정 후 raw CSV의 답변 원문을 몇 개 직접 읽고, 잘못 잡힌 언급이 있는지 확인해요.
- **응답은 매번 달라요.** 슬라이드에 넣을 수치를 뽑을 때는 `repeat`을 2 이상으로 두는 걸 권해요.
- **비용**: 토큰 비용과 웹검색 도구 비용이 따로 붙어요. 단가는 각 API 요금 페이지에서 확인해요 [확인 필요].
