# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════╗
║         솔루션 동향 자동 수집 스크립트                      ║
║         커머스 코칭팀 | SIIC 부천센터                       ║
╚══════════════════════════════════════════════════════════╝

▶ 수집 소스 (3가지)
   1. 각 솔루션사 공식 공지사항 (홈페이지 크롤링)
   2. 네이버 뉴스  – 솔루션명 한글/영문 키워드 검색
   3. 아이보스 마케팅 뉴스
      - 일반 뉴스    : 솔루션명 포함 기사 → 제목만 수집
      - 뉴스클리핑   : [X월 X일 마케팅 뉴스클리핑] 제목 → 본문 전체 수집

▶ GPT가 자동으로 판단하는 항목
   summary       : 2문장 이내 한국어 핵심 요약
   type          : 기능출시 / 기능개선 / 요금변경 / 서비스종료 / 파트너십 / 기타
   defense       : Y(방어 활용 가능) / risk(주의 필요) / N(해당 없음)
   defenseReason : 판단 근거 한 줄

▶ 실행 방법 (처음 한 번만)
   pip install requests beautifulsoup4 feedparser openai lxml

▶ 실행 방법 (매번)
   1. PowerShell 열기
   2. $env:OPENAI_API_KEY="sk-proj-..."  ← API 키 입력
   3. python scripts/collect.py

▶ 결과 파일
   data/trends.json  (누적 저장, 최신순, 최대 500건)
   → GitHub에 push 하면 사이트에 자동 반영
"""

import os, json, time, hashlib, re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from openai import OpenAI


# ┌─────────────────────────────────────────────────────────┐
# │  ★ 담당자 설정 영역 ★                                    │
# │  아래 항목만 수정하면 됩니다. 코드 본문은 건드리지 마세요.  │
# └─────────────────────────────────────────────────────────┘

# ── 수집 기간 설정
COLLECT_DAYS = 7          # 며칠치 뉴스를 수집할지 (기본: 7일)
MAX_SAVE     = 500        # trends.json에 최대 몇 건을 보관할지

# ── 솔루션별 검색 키워드 설정
#    키워드에 포함된 단어가 뉴스 제목에 있을 때만 수집합니다.
#    영문 키워드를 추가하면 네이버 뉴스 영문 검색도 합니다.
SOLUTIONS = [
    {
        "name":        "카페24",
        "keywords_ko": ["카페24"],
        "keywords_en": ["cafe24", "Cafe24"],
    },
    {
        "name":        "메이크샵",
        "keywords_ko": ["메이크샵"],
        "keywords_en": ["makeshop", "MakeShop"],
    },
    {
        "name":        "고도몰",
        "keywords_ko": ["고도몰"],
        "keywords_en": ["godomall", "Godomall"],
    },
    {
        "name":        "아임웹",
        "keywords_ko": ["아임웹"],
        "keywords_en": ["imweb", "Imweb"],
    },
    {
        "name":        "식스샵",
        "keywords_ko": ["식스샵"],
        "keywords_en": ["sixshop", "Sixshop"],
    },
    {
        "name":        "플렉스지",
        "keywords_ko": ["플렉스지"],
        "keywords_en": ["flexii", "Flexii"],
    },
    {
        "name":        "쇼피파이",
        "keywords_ko": ["쇼피파이"],
        "keywords_en": ["shopify", "Shopify"],
    },
]

# ── 각 솔루션사 공식 공지 URL
#    접속이 안 되거나 주소가 바뀌면 여기서 수정하세요.
NOTICE_URLS = {
    "카페24_운영자공지": "https://shopnotice.cafe24.com/list?bbs_no=5",
    "카페24_뉴스룸":     "https://news.cafe24.com/kr/",
    "메이크샵":         "https://www.makeshop.co.kr/newmakeshop/home/notice_list.html",
    "고도몰":           "https://www.godomall.com/community/notice.php",
    "아임웹":           "https://imweb.me/faq?mode=notice",
    "식스샵":           "https://www.sixshop.com/blog",
    "쇼피파이_RSS":     "https://www.shopify.com/blog/changelog.atom",
}

# ── 아이보스 마케팅 뉴스 목록 URL
IBOSS_URL = "https://www.i-boss.co.kr/ab-2876"

# ┌─────────────────────────────────────────────────────────┐
# │  이하 코드는 수정하지 마세요.                              │
# └─────────────────────────────────────────────────────────┘

# ── 시스템 설정
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY)

KST    = timezone(timedelta(hours=9))
TODAY  = datetime.now(KST).strftime("%Y-%m-%d")
CUTOFF = (datetime.now(KST) - timedelta(days=COLLECT_DAYS)).strftime("%Y-%m-%d")

DATA_PATH = "data/trends.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 아이보스 뉴스클리핑 제목 패턴
CLIPPING_PATTERN = re.compile(r"\[\d+월\s*\d+일\s*마케팅\s*뉴스클리핑\]")


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────

def make_id(title: str, solution: str) -> str:
    """중복 수집을 막기 위한 고유 ID 생성"""
    raw = f"{solution}:{title[:50]}"
    return "auto_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def load_existing() -> list:
    """기존에 저장된 trends.json 불러오기"""
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_results(all_items: list):
    """결과 저장 (최신순 정렬, 최대 N건 유지)"""
    os.makedirs("data", exist_ok=True)
    all_items = sorted(all_items, key=lambda x: x.get("date", ""), reverse=True)
    all_items = all_items[:MAX_SAVE]
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 소스 1: 공식 공지 크롤링
# ──────────────────────────────────────────────

def fetch_cafe24_notice() -> list:
    """카페24 운영자 공지 + 뉴스룸 수집"""
    items = []

    # 운영자 공지
    try:
        url  = NOTICE_URLS["카페24_운영자공지"]
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
        print(f"    ⚠ 카페24 운영자공지 접속 실패: {e}")

    # 뉴스룸
    try:
        url  = NOTICE_URLS["카페24_뉴스룸"]
        soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=10).text, "html.parser")
        for a in soup.select("article a, .news-item a, h2 a, h3 a")[:5]:
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            if href and not href.startswith("http"):
                href = "https://news.cafe24.com" + href
            if title and len(title) > 5:
                items.append({"title": title, "url": href, "date": TODAY,
                               "solution": "카페24", "source": "official"})
    except Exception as e:
        print(f"    ⚠ 카페24 뉴스룸 접속 실패: {e}")

    return items


def _crawl_notice(sol_name: str, url_key: str, base_url: str,
                  css_selectors: list) -> list:
    """
    공통 공지 크롤러
    - css_selectors : 시도할 CSS 선택자 목록 (순서대로 시도, 첫 성공 시 중단)
    """
    items = []
    try:
        url  = NOTICE_URLS[url_key]
        soup = BeautifulSoup(requests.get(url, headers=HEADERS, timeout=10).text, "html.parser")
        for sel in css_selectors:
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
                break
    except Exception as e:
        print(f"    ⚠ {sol_name} 공지 접속 실패: {e}")
    return items


def fetch_makeshop_notice() -> list:
    return _crawl_notice(
        "메이크샵", "메이크샵",
        "https://www.makeshop.co.kr",
        [".board-list tbody tr td.title a",
         ".board-list tbody tr td.subject a",
         "table tbody tr td a"],
    )


def fetch_godomall_notice() -> list:
    return _crawl_notice(
        "고도몰", "고도몰",
        "https://www.godomall.com",
        [".board_list tbody tr td.subject a",
         "table tbody tr td.title a",
         "table tbody tr td a"],
    )


def fetch_imweb_notice() -> list:
    return _crawl_notice(
        "아임웹", "아임웹",
        "https://imweb.me",
        [".notice-list li a",
         "a[href*='faq?mode=view']",
         ".board-list a"],
    )


def fetch_sixshop_notice() -> list:
    return _crawl_notice(
        "식스샵", "식스샵",
        "https://www.sixshop.com",
        ["article a", ".post-title a", "h2 a", "h3 a"],
    )


def fetch_shopify_changelog() -> list:
    """쇼피파이 공식 Changelog (RSS 피드 방식)"""
    items = []
    try:
        import feedparser
        feed = feedparser.parse(NOTICE_URLS["쇼피파이_RSS"])
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
        print(f"    ⚠ 쇼피파이 changelog 접속 실패: {e}")
    return items


# ──────────────────────────────────────────────
# 소스 2: 네이버 뉴스 키워드 검색
# ──────────────────────────────────────────────

def fetch_naver_news(sol: dict) -> list:
    """
    솔루션별 한글/영문 키워드로 네이버 뉴스 검색
    → 제목에 키워드가 실제로 포함된 기사만 채택
    """
    items    = []
    all_kws  = sol["keywords_ko"] + sol["keywords_en"]

    for kw in all_kws:
        try:
            encoded = requests.utils.quote(kw)
            url     = (
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
                # 제목에 솔루션 키워드가 없으면 제외 (무관한 기사 필터링)
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
            print(f"    ⚠ 네이버뉴스 [{kw}] 검색 실패: {e}")
        time.sleep(0.5)

    return items


# ──────────────────────────────────────────────
# 소스 3: 아이보스 마케팅 뉴스
# ──────────────────────────────────────────────

def fetch_iboss_list() -> list:
    """
    아이보스 마케팅 뉴스 목록에서 아이템 수집
    - [X월 X일 마케팅 뉴스클리핑] 제목 → is_clipping=True 표시 (본문 수집 예정)
    - 솔루션명 포함 일반 기사 → 제목만 수집
    """
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

            # 뉴스클리핑이거나 솔루션명이 포함된 기사만 수집
            if not is_clipping and not sol_match:
                continue

            items.append({
                "title":       title,
                "url":         href,
                "date":        TODAY,
                "solution":    sol_match or "공통",
                "source":      "iboss",
                "is_clipping": is_clipping,   # 내부 처리용 (저장 전 제거)
            })
    except Exception as e:
        print(f"    ⚠ 아이보스 목록 접속 실패: {e}")
    return items


def fetch_iboss_clipping_body(url: str) -> str:
    """
    뉴스클리핑 게시글 본문 전체 텍스트 추출
    (최대 3,000자, 광고/메뉴 제외)
    """
    try:
        soup = BeautifulSoup(
            requests.get(url, headers=HEADERS, timeout=15).text, "html.parser"
        )
        body = (
            soup.select_one(".ab-content")
            or soup.select_one(".content-body")
            or soup.select_one("article .body")
            or soup.select_one("article")
            or soup.select_one(".view-content")
        )
        if not body:
            return ""
        for el in body.select("script, style, .ad, .banner, nav"):
            el.decompose()
        return body.get_text(separator="\n", strip=True)[:3000]
    except Exception as e:
        print(f"    ⚠ 아이보스 본문 수집 실패: {e}")
        return ""


# ──────────────────────────────────────────────
# GPT 분석 (gpt-4o-mini)
# ──────────────────────────────────────────────

def analyze_with_gpt(item: dict) -> dict:
    """
    GPT-4o-mini로 요약 + 유형 분류 + 방어 활용 판단
    API 키 없으면 분류 없이 원문 제목만 저장
    """
    if not OPENAI_API_KEY:
        return {**item, "summary": item["title"],
                "type": "기타", "defense": "N", "defenseReason": ""}

    content = (item.get("content") or item["title"])[:1500]
    prompt  = (
        f"다음은 이커머스 솔루션({item['solution']}) 관련 공지/뉴스입니다.\n\n"
        f"제목: {item['title']}\n"
        f"내용: {content}\n\n"
        "아래 JSON 형식으로만 답하세요 (마크다운 없이):\n"
        '{"summary":"한국어 2문장 이내 핵심 요약",'
        '"type":"기능출시|기능개선|요금변경|서비스종료|파트너십|기타",'
        '"defense":"Y|risk|N",'
        '"defenseReason":"판단 근거 한 줄"}\n\n'
        "판단 기준:\n"
        "  Y    → 경쟁사 서비스 축소/종료/요금 인상, 카페24 신기능 출시\n"
        "  risk → 경쟁사 주요 신기능 출시, 요금 인하, 대형 파트너십 체결\n"
        "  N    → 채용공고, 단순 UI 변경, 행사 안내, 일반 마케팅 뉴스"
    )

    try:
        resp   = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        raw    = re.sub(r"```json|```", "", resp.choices[0].message.content.strip()).strip()
        parsed = json.loads(raw)
        return {
            **item,
            "summary":       parsed.get("summary", item["title"]),
            "type":          parsed.get("type", "기타"),
            "defense":       parsed.get("defense", "N"),
            "defenseReason": parsed.get("defenseReason", ""),
        }
    except Exception as e:
        print(f"    ⚠ GPT 분석 실패: {e}")
        return {**item, "summary": item["title"],
                "type": "기타", "defense": "N", "defenseReason": ""}


# ──────────────────────────────────────────────
# 메인 실행
# ──────────────────────────────────────────────

def main():
    print(f"\n{'━'*56}")
    print(f"  📡 솔루션 동향 자동 수집  |  {TODAY}")
    print(f"  수집 기간: 최근 {COLLECT_DAYS}일 ({CUTOFF} ~ {TODAY})")
    print(f"{'━'*56}")

    existing     = load_existing()
    existing_ids = {item.get("id") for item in existing}
    raw_items    = []

    # ── 소스 1: 공식 공지
    print("\n[1/3] 공식 공지 수집")
    fetchers = [
        ("카페24",   fetch_cafe24_notice),
        ("메이크샵", fetch_makeshop_notice),
        ("고도몰",   fetch_godomall_notice),
        ("아임웹",   fetch_imweb_notice),
        ("식스샵",   fetch_sixshop_notice),
        ("쇼피파이", fetch_shopify_changelog),
    ]
    for name, fn in fetchers:
        result = fn()
        print(f"  ✓ {name:<7}: {len(result)}건")
        raw_items.extend(result)
        time.sleep(1)

    # ── 소스 2: 네이버 뉴스
    print("\n[2/3] 네이버 뉴스 검색")
    for sol in SOLUTIONS:
        result = fetch_naver_news(sol)
        print(f"  ✓ {sol['name']:<7}: {len(result)}건")
        raw_items.extend(result)
        time.sleep(0.5)

    # ── 소스 3: 아이보스 마케팅 뉴스
    print("\n[3/3] 아이보스 마케팅 뉴스")
    iboss_list = fetch_iboss_list()
    clipping_count = sum(1 for i in iboss_list if i.get("is_clipping"))
    general_count  = len(iboss_list) - clipping_count
    print(f"  ✓ 뉴스클리핑: {clipping_count}건 (본문 수집), "
          f"일반 뉴스: {general_count}건 (제목만)")

    for item in iboss_list:
        if item.get("is_clipping"):
            body = fetch_iboss_clipping_body(item["url"])
            item["content"] = body
            print(f"    → 본문 {len(body)}자 | {item['title'][:40]}...")
            time.sleep(1)
        item.pop("is_clipping", None)  # 내부 플래그 제거 후 저장

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

    print(f"\n  신규 항목 {len(new_items)}건 → GPT 분석 시작")
    print(f"{'─'*56}")

    # ── GPT 분석
    analyzed = []
    for i, item in enumerate(new_items, 1):
        label = f"[{i:3d}/{len(new_items)}]"
        print(f"  {label} {item['solution']:<7} | {item['title'][:40]}")
        result = analyze_with_gpt(item)
        result.pop("content", None)   # 본문은 summary로 대체, 용량 절약
        analyzed.append(result)
        time.sleep(0.5)

    # ── 저장
    save_results(analyzed + existing)

    print(f"\n{'━'*56}")
    print(f"  ✅ 수집 완료")
    print(f"     신규 저장: {len(analyzed)}건")
    print(f"     기존 유지: {len(existing)}건")
    print(f"     저장 위치: {DATA_PATH}")
    print(f"     → GitHub push 하면 사이트에 자동 반영됩니다.")
    print(f"{'━'*56}\n")


if __name__ == "__main__":
    main()
