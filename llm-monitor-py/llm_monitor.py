"""
LLM 언급·인용 모니터링 (ChatGPT · Gemini)

질문 세트(questions.csv)를 ChatGPT(OpenAI Responses API + 웹검색)와
Gemini(Gemini API + 구글 검색 그라운딩)에 보내고, 답변마다
  - 브랜드 언급 여부
  - 언급 순위 (브랜드·경쟁사 중 몇 번째로 처음 등장했는지)
  - 브랜드 도메인이 출처로 인용됐는지
  - 함께 언급된 경쟁사
를 기록해 results/ 폴더에 원본 CSV와 요약 CSV로 저장합니다.

사용법
  pip install requests
  export OPENAI_API_KEY=...   (Windows PowerShell: $env:OPENAI_API_KEY="...")
  export GEMINI_API_KEY=...
  python llm_monitor.py                 # 전체 측정
  python llm_monitor.py --test          # 질문 1개로 연결 테스트
  python llm_monitor.py --limit 5       # 앞 질문 5개만 (비용 확인용)
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))

RAW_FIELDS = [
    "측정회차", "측정일시", "플랫폼", "모델", "질문ID", "테마", "질문", "반복",
    "브랜드 언급", "언급 순위", "브랜드 도메인 인용", "인용 출처 수", "인용 URL",
    "인용 출처 제목", "함께 언급된 회사", "답변 원문", "오류",
]


# ---------- 설정 · 질문 읽기 ----------
def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_questions(path):
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [
        {"id": r["ID"].strip(), "theme": r["테마"].strip(), "question": r["질문"].strip()}
        for r in rows
        if r.get("ID") and r.get("질문") and r.get("사용(Y/N)", "Y").strip().upper() != "N"
    ]


def require_key(name):
    key = (os.environ.get(name) or "").strip()  # 붙여넣을 때 섞인 공백·줄바꿈 제거
    if not key:
        sys.exit(f"[오류] 환경변수 {name} 가 없습니다. API 키를 먼저 설정하세요.")
    return key


# ---------- 플랫폼 호출 ----------
def call_openai(question, cfg):
    res = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {require_key('OPENAI_API_KEY')}"},
        json={
            "model": cfg["openai_model"],
            "input": question,
            "tools": [{"type": cfg["openai_tool"],
                       "user_location": {"type": "approximate", "country": "KR"}}],
        },
        timeout=cfg["timeout_sec"],
    )
    check_status(res)
    return parse_openai(res.json())


def call_gemini(question, cfg):
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['gemini_model']}:generateContent",
        headers={"x-goog-api-key": require_key("GEMINI_API_KEY")},
        json={
            "contents": [{"role": "user", "parts": [{"text": question}]}],
            "tools": [{"google_search": {}}],
        },
        timeout=cfg["timeout_sec"],
    )
    check_status(res)
    return parse_gemini(res.json())


CALLERS = {"ChatGPT": call_openai, "Gemini": call_gemini}


def check_status(res):
    if not 200 <= res.status_code < 300:
        raise RuntimeError(f"HTTP {res.status_code}: {res.text[:300]}")


# ---------- 응답 해석 ----------
def parse_openai(data):
    """Responses API: output[] → type=message → content[] → output_text + annotations(url_citation)"""
    texts, cites = [], []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                texts.append(c["text"])
            for a in c.get("annotations", []) or []:
                if a.get("type") == "url_citation" and a.get("url"):
                    cites.append({"url": a["url"], "title": a.get("title", "")})
    if not texts and data.get("output_text"):
        texts.append(data["output_text"])
    return "\n".join(texts), dedupe(cites)


def parse_gemini(data):
    """Gemini: candidates[0].content.parts[].text + groundingMetadata.groundingChunks[].web
    web.uri는 구글 리다이렉트 주소인 경우가 많아, 실제 도메인은 web.title로도 확인한다."""
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []
    cites = [{"url": ch["web"].get("uri", ""), "title": ch["web"].get("title", "")}
             for ch in chunks if ch.get("web")]
    return text, dedupe(cites)


def dedupe(cites):
    seen, out = set(), []
    for c in cites:
        k = (c["url"], c["title"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# ---------- 답변 분석 ----------
def analyze(text, cites, cfg):
    lower = (text or "").lower()

    def first_index(aliases):
        idx = [lower.find(a.lower()) for a in aliases]
        idx = [i for i in idx if i >= 0]
        return min(idx) if idx else -1

    entities = [(cfg["brand_name"], first_index(cfg["brand_aliases"]), True)]
    entities += [(name, first_index(al), False) for name, al in cfg["competitors"].items()]
    found = sorted([e for e in entities if e[1] >= 0], key=lambda e: e[1])

    brand_rank = next((i + 1 for i, e in enumerate(found) if e[2]), None)
    domains = [d.lower() for d in cfg["brand_domains"] if d and "[" not in d]
    brand_cited = any(d in (c["url"] + " " + c["title"]).lower() for c in cites for d in domains)

    return {
        "mentioned": brand_rank is not None,
        "rank": brand_rank,
        "cited": brand_cited,
        "competitors": [e[0] for e in found if not e[2]],
    }


# ---------- 실행 ----------
def run_job(job, cfg, run_id):
    platform, q, rep = job
    model = cfg["openai_model"] if platform == "ChatGPT" else cfg["gemini_model"]
    text, cites, error = "", [], ""
    for attempt in range(3):  # 일시적 한도 초과(429)·서버 오류(5xx)는 잠시 쉬고 최대 2번 재시도
        try:
            text, cites = CALLERS[platform](q["question"], cfg)
            error = ""
            break
        except Exception as e:  # 한 건이 실패해도 전체 측정은 계속
            error = str(e)[:500]
            if not any(code in error for code in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503")):
                break
            time.sleep(10 * (attempt + 1))
    a = analyze(text, cites, cfg)
    return {
        "측정회차": run_id,
        "측정일시": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "플랫폼": platform, "모델": model,
        "질문ID": q["id"], "테마": q["theme"], "질문": q["question"], "반복": rep,
        "브랜드 언급": "Y" if a["mentioned"] else "N",
        "언급 순위": a["rank"] or "",
        "브랜드 도메인 인용": "Y" if a["cited"] else "N",
        "인용 출처 수": len(cites),
        "인용 URL": "\n".join(c["url"] for c in cites),
        "인용 출처 제목": "\n".join(c["title"] for c in cites),
        "함께 언급된 회사": ", ".join(a["competitors"]),
        "답변 원문": text,
        "오류": error,
    }


def summarize(rows, cfg):
    ok = [r for r in rows if not r["오류"]]
    groups = defaultdict(list)
    for r in ok:
        groups[(r["플랫폼"], r["테마"])].append(r)
        if r["테마"] != "브랜드 직접":
            groups[(r["플랫폼"], "전체 (브랜드 직접 제외)")].append(r)
        groups[("전체", "전체")].append(r)

    brand = cfg["brand_name"]
    out = []
    for (platform, theme), g in sorted(groups.items()):
        if not g:
            continue
        n = len(g)
        ranks = [int(r["언급 순위"]) for r in g if r["언급 순위"]]
        comp = Counter(c for r in g for c in r["함께 언급된 회사"].split(", ") if c)
        out.append({
            "플랫폼": platform, "테마": theme, "응답 수": n,
            f"{brand} 언급률": f"{sum(r['브랜드 언급'] == 'Y' for r in g) / n:.1%}",
            f"{brand} 인용률": f"{sum(r['브랜드 도메인 인용'] == 'Y' for r in g) / n:.1%}",
            "평균 언급 순위": f"{sum(ranks) / len(ranks):.2f}" if ranks else "",
            "함께 많이 언급된 회사": ", ".join(f"{c}({cnt / n:.0%})" for c, cnt in comp.most_common(3)),
        })
    return out


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글 안 깨짐
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="LLM 언급·인용 모니터링")
    ap.add_argument("--config", default=BASE / "config.json")
    ap.add_argument("--questions", default=BASE / "questions.csv")
    ap.add_argument("--out", default=BASE / "results")
    ap.add_argument("--test", action="store_true", help="질문 1개, 반복 1회로 연결 테스트")
    ap.add_argument("--limit", type=int, help="앞에서부터 N개 질문만 측정")
    args = ap.parse_args()

    cfg = load_config(args.config)
    questions = load_questions(args.questions)
    if args.test:
        questions, cfg["repeat"] = questions[:1], 1
    elif args.limit:
        questions = questions[: args.limit]

    for p in cfg["platforms"]:
        require_key("OPENAI_API_KEY" if p == "ChatGPT" else "GEMINI_API_KEY")
    if all("[" in d for d in cfg["brand_domains"]):
        print("[주의] config.json의 brand_domains가 비어 있어 인용률은 0%로 나옵니다.")

    run_id = datetime.now(KST).strftime("%Y-%m-%d_%H%M")
    jobs = [(p, q, r) for p in cfg["platforms"] for q in questions for r in range(1, cfg["repeat"] + 1)]
    print(f"측정회차 {run_id} · 총 {len(jobs)}건 호출")

    rows = []
    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
        futures = [ex.submit(run_job, j, cfg, run_id) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            rows.append(r)
            status = "오류" if r["오류"] else f"언급 {r['브랜드 언급']} · 인용 {r['브랜드 도메인 인용']}"
            print(f"  [{i}/{len(jobs)}] {r['플랫폼']} {r['질문ID']}#{r['반복']} → {status}")

    rows.sort(key=lambda r: (r["플랫폼"], r["질문ID"], r["반복"]))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows, cfg)
    write_csv(out / f"raw_{run_id}.csv", rows, RAW_FIELDS)
    if summary:
        write_csv(out / f"summary_{run_id}.csv", summary, list(summary[0].keys()))

    errors = sum(1 for r in rows if r["오류"])
    print(f"\n완료 · 정상 {len(rows) - errors}건 · 오류 {errors}건 → {out}")
    if errors:
        print("첫 오류:", next(r["오류"] for r in rows if r["오류"]))
    for s in summary:
        print("  ", " | ".join(str(v) for v in s.values()))
    if rows and errors == len(rows):
        sys.exit("[실패] 모든 호출이 오류로 끝났습니다. 위의 첫 오류 메시지를 확인하세요.")


if __name__ == "__main__":
    main()
