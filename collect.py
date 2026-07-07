# -*- coding: utf-8 -*-
"""
=====================================================
  솔루션 동향 자동 수집 스크립트
  담당: 커머스 코칭팀
=====================================================

[수집 소스]
  1. 각 솔루션사 공식 공지 (크롤링)
  2. 네이버 뉴스   (한글명 + 영문명 키워드)
  3. 아이보스 마케팅 뉴스
     - 일반 뉴스    → 제목만 수집
     - 뉴스클리핑   → 제목 + 본문 전체 수집
        (제목이 "[X월 X일 마케팅 뉴스클리핑]"으로 시작하는 게시글)

[GPT 분류 항목]
  summary        : 2문장 이내 한국어 요약
  type           : 기능출시 / 기능개선 / 요금변경 / 서비스종료 / 파트너십 / 기타
  defense        : Y(방어활용 가능) / risk(주의필요) / N(해당없음)
  defenseReason  : 판단 근거 한 줄

[실행 방법]
  pip install requests beautifulsoup4 feedparser openai lxml
  set OPENAI_API_KEY=sk-...
  python scripts/collect.py

[출력]
  data/trends.json  (누적, 최신순, 최대 500건)
=====================================================
"""

import os, json, time, hashlib, re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# ──────────────────────────────────────────────
# ■ 기본 설정
# ──────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY)

KST    = timezone(timedelta(hours=9))
TODAY  = datetime.now(KST).strftime("%Y-%m-%d")
CUTOFF = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")  # 7일 이내만 수집

DATA_PATH = "data/trends.json"
MAX_ITEMS = 500  # 최대 보관 건수

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ──────────────────────────────────────────────
# ■ 솔루션 정의
#   키워드는 네이버 뉴스 검색에 사용됩니다.
#   한글/영문 모두 검색 후 제목에 포함된 것만 수집
# ──────────────────────────────────────────────
SOLUTIONS = [
    {"name": "카페24",   "keywords_ko": ["카페24"],   "keywords_en": ["cafe24",   "Cafe24"]},
    {"name": "메이크샵", "keywords_ko": ["메이크샵"], "keywords_en": ["makeshop", "MakeShop"]},
    {"name": "고도몰",   "keywords_ko": ["고도몰"],   "keywords_en": ["godomall", "Godomall"]},
    {"name": "아임웹",   "keywords_ko": ["아임웹"],   "keywords_en": ["imweb",    "Imweb"]},
    {"name": "식스샵",   "keywords_ko": ["식스샵"],   "keywords_en": ["sixshop",  "Sixshop"]},
    {"name": "플렉스지", "keywords_ko": ["플렉스지"], "keywords_en": ["flexii",   "Flexii"]},
    {"name": "쇼피파이", "keywords_ko": ["쇼피파이"], "keywords_en": ["shopify",  "Shopify"]},
]

# 아이보스 뉴스클리핑 제목 패턴
CLIPPING_PATTERN = re.compile(r"\[\d+월\s*\d+일\s*마케팅\s*뉴스클리핑\]")


# ──────────────────────────────────────────────
# ■ 유틸 함수
# ──────────────────────────────────────────────
def make_id(title: str, solution: str) -> str:
    """제목 + 솔루션명 조합으로 중복 방지 ID 생성"""
    raw = f"{solution}:{title[:50]}"
    return "auto_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def load_existing() -> list:
    """기존 trends.json 로드 (없으면 빈 리스트)"""
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save(all_items: list):
    """trends.json 저장 (최신순 정렬, 최대 500건)"""
    os.makedirs("data", exist_ok=True)
    all_items = sorted(all_items, key=lambda x: x.get("date", ""), reverse=True)
    all_items = all_items[:MAX_ITEMS]
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# ■ 소스 1 : 솔루션사 공식 공지 크롤링
# ──────────────────────────────────────────────

def fetch_cafe24_notice() -> list:
    """카페24 쇼핑몰 운영자 공지 + 뉴스룸"""
    items = []

    # 1-1. 운영자 공지 (shopnotice)
    try:
        url = "https://shopnotice.cafe24.com/list?bbs_no=5"
        soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=10).text, "html.parser")
        for row in soup.select("table tbody tr")[:15]:
            a = row.select_one("td.subject a, td.title a, td a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://shopnotice.cafe24.com" + href
            if title:
                items.append({"title": title, "url": href, "date": TODAY,
                               "solution": "카페24", "source": "official"})
    except Exception as e:
        print(f"    ⚠ 카페24 공지 오류: {e}")

    # 1-2. 카페24 뉴스룸
    try:
        url2 = "https://news.cafe24.com/kr/"
        soup2 = BeautifulSoup(requests.get(url2, headers=HEADERS, timeout=10).text, "html.parser")
        for a in soup2.select("article a, .news-item a, .post-title a, h2 a, h3 a")[:5]:
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://news.cafe24.com" + href
            if title and len(title) > 5:
                items.append({"title": title, "url": href, "date": TODAY,
                               "solution": "카페24", "source": "official"})
    except Exception as e:
        print(f"    ⚠ 카페24 뉴스룸 오류: {e}")

    return items


def _fetch_notice_generic(sol_name: str, url: str, base_url: str,
                           selectors: list) -> list:
    """공통 공지 크롤러 (솔루션명, URL, 베이스URL, CSS 셀렉터 목록)"""
    items = []
    try:
        soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=10).text, "html.parser")
        for sel in selectors:
            rows = soup.select(sel)
            if not rows:
                continue
            for row in rows[:10]:
                a = row if row.name == "a" else row.select_one("a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href  = a.get("href", "")
                if href and not href.startswith("http"):
                    href = base_url + href
                if title and len(title) > 5:
                    items.append({"title": title, "url": href, "date": TODAY,
                                  "solution": sol_name, "source": "official"})
            if items:
                break  # 첫 번째로 매칭된 셀렉터만 사용
    except Exception as e:
        print(f"    ⚠ {sol_name} 공지 오류: {e}")
    return items


def fetch_makeshop_notice() -> list:
    return _fetch_notice_generic(
        "메이크샵",
        "https://www.makeshop.co.kr/newmakeshop/home/notice_list.html",
        "https://www.makeshop.co.kr",
        [".board-list tbody tr td.title a",
         ".board-list tbody tr td.subject a",
         "table tbody tr td a"],
    )


def fetch_godomall_notice() -> list:
    return _fetch_notice_generic(
        "고도몰",
        "https://www.godomall.com/community/notice.php",
        "https://www.godomall.com",
        [".board_list tbody tr td.subject a",
         "table tbody tr td.title a",
         "table tbody tr td a"],
    )


def fetch_imweb_notice() -> list:
    return _fetch_notice_generic(
        "아임웹",
        "https://imweb.me/faq?mode=notice",
        "https://imweb.me",
        [".notice-list li a",
         "a[href*='faq?mode=view']",
         ".board-list a"],
    )


def fetch_sixshop_notice() -> list:
    return _fetch_notice_generic(
        "식스샵",
        "https://www.sixshop.com/blog",
        "https://www.sixshop.com",
        ["article a", ".post-title a", "h2 a", "h3 a"],
    )


def fetch_shopify_changelog() -> list:
    """쇼피파이 Changelog (RSS 방식)"""
    items = []
    try:
        import feedparser
        feed = feedparser.parse("https://www.shopify.com/blog/changelog.atom")
        for entry in feed.entries[:10]:
            date_str = entry.get("published", TODAY)[:10]
            if date_str < CUTOFF:
                continue
            items.append({
                "title":    entry.get("title", "").strip(),
                "url":      entry.get("link", ""),
                "date":     date_str,
                "solution": "쇼피파이",
                "source":   "official",
            })
    except Exception as e:
        print(f"    ⚠ 쇼피파이 changelog 오류: {e}")
    return items


# ──────────────────────────────────────────────
# ■ 소스 2 : 네이버 뉴스 검색
#   - 한글명 / 영문명 각각 검색
#   - 제목에 솔루션 키워드 포함된 기사만 채택
# ──────────────────────────────────────────────

def fetch_naver_news(sol: dict) -> list:
    items = []
    all_kws = sol["keywords_ko"] + sol["keywords_en"]

    for kw in all_kws:
        try:
            encoded = requests.utils.quote(kw)
            url = (
                f"https://search.naver.com/search.naver"
                f"?where=news&query={encoded}&sort=1"
                f"&ds={CUTOFF}&de={TODAY}"
            )
            soup = BeautifulSoup(
                requests.get(url, headers=HEADERS, timeout=10).text, "html.parser"
            )
            for a in soup.select(".news_wrap .news_tit")[:5]:
                title = a.get_text(strip=True)
                href  = a.get("href", "")
                # 제목에 솔루션 키워드가 실제로 포함된 것만 채택
                if not any(k.lower() in title.lower() for k in all_kws):
                    continue
                items.append({
                    "title":    title,
                    "url":      href,
                    "date":     TODAY,
                    "solution": sol["name"],
                    "source":   "news",
                })
        except Exception as e:
            print(f"    ⚠ 네이버뉴스 [{kw}] 오류: {e}")

        time.sleep(0.5)

    return items


# ──────────────────────────────────────────────
# ■ 소스 3 : 아이보스 마케팅 뉴스
#
#   [분류 기준]
#   ① 뉴스클리핑  : 제목이 "[X월 X일 마케팅 뉴스클리핑]"으로 시작
#                   → 게시글 본문 전체 크롤링 (매일 1건)
#   ② 일반 뉴스   : 솔루션명(한글/영문)이 제목에 포함된 기사
#                   → 제목만 수집
# ──────────────────────────────────────────────

IBOSS_URL = "https://www.i-boss.co.kr/ab-2876"


def fetch_iboss_list() -> list:
    """아이보스 마케팅 뉴스 목록 파싱"""
    items = []
    try:
        soup = BeautifulSoup(
            requests.get(IBOSS_URL, headers=HEADERS, timeout=15).text, "html.parser"
        )
        seen = set()
        for a in soup.select("a[href*='/ab-']"):
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            if not title or not href or len(title) < 5 or href in seen:
                continue
            seen.add(href)
            if not href.startswith("http"):
                href = "https://www.i-boss.co.kr" + href

            is_clipping = bool(CLIPPING_PATTERN.search(title))

            # 솔루션 키워드 매칭
            sol_match = next(
                (s["name"] for s in SOLUTIONS
                 if any(k.lower() in title.lower()
                        for k in s["keywords_ko"] + s["keywords_en"])),
                None,
            )

            # 뉴스클리핑이거나 솔루션 언급이 있는 기사만 수집
            if not is_clipping and not sol_match:
                continue

            items.append({
                "title":        title,
                "url":          href,
                "date":         TODAY,
                "solution":     sol_match or "공통",
                "source":       "iboss",
                "is_clipping":  is_clipping,   # 내부 처리용 (저장 시 제거)
            })
    except Exception as e:
        print(f"    ⚠ 아이보스 목록 오류: {e}")
    return items


def fetch_iboss_clipping_body(url: str) -> str:
    """뉴스클리핑 본문 전체 텍스트 추출 (최대 3000자)"""
    try:
        soup = BeautifulSoup(
            requests.get(url, headers=HEADERS, timeout=15).text, "html.parser"
        )
        # 본문 영역 우선순위 선택
        body = (
            soup.select_one(".ab-content")
            or soup.select_one(".content-body")
            or soup.select_one("article .body")
            or soup.select_one("article")
            or soup.select_one(".view-content")
        )
        if not body:
            return ""
        # 광고·메뉴 제거
        for el in body.select("script, style, .ad, .banner, nav"):
            el.decompose()
        return body.get_text(separator="\n", strip=True)[:3000]
    except Exception as e:
        print(f"    ⚠ 아이보스 본문 오류: {e}")
        return ""


# ──────────────────────────────────────────────
# ■ GPT 분석 (gpt-4o-mini)
#   - 요약 / 유형 분류 / 방어 활용 여부 판단
# ──────────────────────────────────────────────

def analyze(item: dict) -> dict:
    """GPT-4o-mini로 요약 + 분류 + 방어 판단"""
    if not OPENAI_API_KEY:
        return {**item, "summary": item["title"], "type": "기타",
                "defense": "N", "defenseReason": ""}

    content = (item.get("content") or item["title"])[:1500]

    prompt = (
        f"다음은 이커머스 솔루션({item['solution']}) 관련 공지/뉴스입니다.\n\n"
        f"제목: {item['title']}\n"
        f"내용: {content}\n\n"
        f"아래 JSON 형식으로만 답하세요 (마크다운 없이):\n"
        '{"summary":"한국어 2문장 이내 핵심 요약",'
        '"type":"기능출시|기능개선|요금변경|서비스종료|파트너십|기타",'
        '"defense":"Y|risk|N",'
        '"defenseReason":"판단 근거 한 줄"}\n\n'
        "판단 기준:\n"
        "Y   → 경쟁사 서비스 축소/종료/요금인상, 카페24 신기능 출시로 방어 가능\n"
        "risk → 경쟁사 주요 신기능/요금인하/대형 파트너십 체결 (주의 필요)\n"
        "N   → 채용공고, 단순 UI변경, 행사안내, 일반 마케팅 뉴스"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        raw = re.sub(r"```json|```", "", resp.choices[0].message.content.strip()).strip()
        parsed = json.loads(raw)
        return {
            **item,
            "summary":       parsed.get("summary", item["title"]),
            "type":          parsed.get("type", "기타"),
            "defense":       parsed.get("defense", "N"),
            "defenseReason": parsed.get("defenseReason", ""),
        }
    except Exception as e:
        print(f"    ⚠ GPT 오류: {e}")
        return {**item, "summary": item["title"], "type": "기타",
                "defense": "N", "defenseReason": ""}


# ──────────────────────────────────────────────
# ■ 메인 실행
# ──────────────────────────────────────────────

def main():
    print(f"\n{'='*56}")
    print(f"  솔루션 동향 자동 수집  ({TODAY})")
    print(f"{'='*56}")

    existing     = load_existing()
    existing_ids = {item.get("id") for item in existing}
    raw_items    = []

    # ── 소스 1 : 공식 공지
    print("\n[1/3] 공식 공지 수집")
    notice_fetchers = [
        ("카페24",   fetch_cafe24_notice),
        ("메이크샵", fetch_makeshop_notice),
        ("고도몰",   fetch_godomall_notice),
        ("아임웹",   fetch_imweb_notice),
        ("식스샵",   fetch_sixshop_notice),
        ("쇼피파이", fetch_shopify_changelog),
    ]
    for name, fn in notice_fetchers:
        result = fn()
        print(f"  ✓ {name:6s}: {len(result)}건")
        raw_items.extend(result)
        time.sleep(1)

    # ── 소스 2 : 네이버 뉴스
    print("\n[2/3] 네이버 뉴스 검색")
    for sol in SOLUTIONS:
        result = fetch_naver_news(sol)
        print(f"  ✓ {sol['name']:6s}: {len(result)}건")
        raw_items.extend(result)
        time.sleep(0.5)

    # ── 소스 3 : 아이보스 마케팅 뉴스
    print("\n[3/3] 아이보스 마케팅 뉴스")
    iboss_list = fetch_iboss_list()
    print(f"  ✓ 목록: {len(iboss_list)}건 (뉴스클리핑 본문 별도 수집)")

    for item in iboss_list:
        if item.get("is_clipping"):
            body = fetch_iboss_clipping_body(item["url"])
            item["content"] = body
            print(f"    → 뉴스클리핑 본문 {len(body)}자 수집: {item['title'][:35]}...")
            time.sleep(1)
        item.pop("is_clipping", None)  # 저장 전 내부 플래그 제거

    raw_items.extend(iboss_list)

    # ── 중복 제거 + ID 부여
    new_items = []
    for item in raw_items:
        uid = make_id(item["title"], item["solution"])
        if uid in existing_ids:
            continue
        item["id"] = uid
        existing_ids.add(uid)
        new_items.append(item)

    print(f"\n  신규 항목: {len(new_items)}건 → GPT 분석 시작")

    # ── GPT 분석
    analyzed = []
    for i, item in enumerate(new_items, 1):
        print(f"  [{i:3d}/{len(new_items)}] {item['solution']:6s} | {item['title'][:40]}")
        result = analyze(item)
        result.pop("content", None)  # 본문은 summary로 대체 → 용량 절약
        analyzed.append(result)
        time.sleep(0.5)

    # ── 저장
    save(analyzed + existing)

    print(f"\n{'='*56}")
    print(f"  ✅ 완료")
    print(f"     신규: {len(analyzed)}건")
    print(f"     기존: {len(existing)}건")
    print(f"     저장: {DATA_PATH}")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    main()
