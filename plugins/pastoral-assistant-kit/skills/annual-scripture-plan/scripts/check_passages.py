#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
연간 말씀계획표 검증 스크립트

사용법:
    python check_passages.py <계획표.csv> [--year 2027]

CSV 필수 열: 날짜, 본문
선택 열: 분기, 월주제, 주간주제, 제목, 비고, 트랙

'비고' 열에 '의도적 재방문'이 포함된 행은 중복 경고에서 제외한다.
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

# 개역개정 약칭 -> 정경 순서
BOOK_ORDER = [
    "창", "출", "레", "민", "신", "수", "삿", "룻", "삼상", "삼하",
    "왕상", "왕하", "대상", "대하", "스", "느", "에", "욥", "시", "잠",
    "전", "아", "사", "렘", "애", "겔", "단", "호", "욜", "암",
    "옵", "욘", "미", "나", "합", "습", "학", "슥", "말",
    "마", "막", "눅", "요", "행", "롬", "고전", "고후", "갈", "엡",
    "빌", "골", "살전", "살후", "딤전", "딤후", "딛", "몬", "히", "약",
    "벧전", "벧후", "요일", "요이", "요삼", "유", "계",
]
BOOK_SET = set(BOOK_ORDER)
OT = set(BOOK_ORDER[:39])

# 여러 표기를 표준 약칭으로
ALIAS = {
    "창세기": "창", "출애굽기": "출", "레위기": "레", "민수기": "민", "신명기": "신",
    "여호수아": "수", "사사기": "삿", "룻기": "룻", "시편": "시", "잠언": "잠",
    "전도서": "전", "아가": "아", "이사야": "사", "예레미야": "렘", "예레미야애가": "애",
    "에스겔": "겔", "다니엘": "단", "요나": "욘", "마태복음": "마", "마가복음": "막",
    "누가복음": "눅", "요한복음": "요", "사도행전": "행", "로마서": "롬",
    "고린도전서": "고전", "고린도후서": "고후", "갈라디아서": "갈", "에베소서": "엡",
    "빌립보서": "빌", "골로새서": "골", "데살로니가전서": "살전", "데살로니가후서": "살후",
    "디모데전서": "딤전", "디모데후서": "딤후", "디도서": "딛", "빌레몬서": "몬",
    "히브리서": "히", "야고보서": "약", "베드로전서": "벧전", "베드로후서": "벧후",
    "요한일서": "요일", "요한이서": "요이", "요한삼서": "요삼", "유다서": "유",
    "요한계시록": "계", "계시록": "계",
}

MAX_VERSE = 200  # 장 전체를 뜻할 때 쓰는 상한

REF_RE = re.compile(r"^\s*([가-힣]+)\s*([\d:\-–~,\s]+)\s*$")


def normalize_book(raw):
    raw = raw.strip()
    if raw in BOOK_SET:
        return raw
    if raw in ALIAS:
        return ALIAS[raw]
    return None


def parse_ref(ref):
    """'삼상 15:17-23' -> (book, start_key, end_key). 실패하면 None."""
    if not ref:
        return None
    ref = ref.split("(")[0].strip()          # (개역개정) 제거
    ref = ref.replace("–", "-").replace("~", "-")
    m = REF_RE.match(ref)
    if not m:
        return None
    book = normalize_book(m.group(1))
    if book is None:
        return None
    body = m.group(2).replace(" ", "")
    if not body:
        return None

    if "-" in body:
        left, right = body.split("-", 1)
    else:
        left, right = body, None

    # 시작 지점
    if ":" in left:
        c, v = left.split(":", 1)
        try:
            start = (int(c), int(v))
        except ValueError:
            return None
    else:
        try:
            start = (int(left), 1)
        except ValueError:
            return None
        if right is None:
            end = (int(left), MAX_VERSE)      # 시 23 -> 23편 전체
            return book, start, end

    # 끝 지점
    if right is None:
        end = start if ":" in left else (start[0], MAX_VERSE)
    elif ":" in right:
        c, v = right.split(":", 1)
        try:
            end = (int(c), int(v))
        except ValueError:
            return None
    else:
        try:
            n = int(right)
        except ValueError:
            return None
        end = (start[0], n) if ":" in left else (n, MAX_VERSE)

    if end < start:
        return None
    return book, start, end


def key(pos):
    return pos[0] * 1000 + pos[1]


def overlap_ratio(a_start, a_end, b_start, b_end):
    a1, a2 = key(a_start), key(a_end)
    b1, b2 = key(b_start), key(b_end)
    inter = min(a2, b2) - max(a1, b1) + 1
    if inter <= 0:
        return 0.0
    shorter = min(a2 - a1 + 1, b2 - b1 + 1)
    return inter / shorter


def approx_verses(start, end):
    """대략적인 절 수. 장 경계를 넘으면 장당 25절로 어림한다."""
    if start[0] == end[0]:
        span = end[1] - start[1] + 1
        return min(span, 60) if end[1] >= MAX_VERSE else span
    return (end[0] - start[0]) * 25 + end[1]


def load_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get(row, *names):
    for n in names:
        for k, v in row.items():
            if k and k.strip() == n:
                return (v or "").strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--year", type=int, default=None, help="날짜 완전성 검사 기준 연도")
    args = ap.parse_args()

    rows = load_rows(args.csv_path)
    if not rows:
        print("행이 없습니다.")
        sys.exit(1)

    errors, warnings, notes = [], [], []
    parsed = []
    date_counter = Counter()
    book_counter = Counter()

    for i, row in enumerate(rows, start=2):  # 헤더가 1행
        d = get(row, "날짜", "date")
        ref = get(row, "본문", "passage")
        memo = get(row, "비고", "memo")
        theme = get(row, "월주제", "주제")

        if d:
            date_counter[d] += 1
        else:
            errors.append(f"{i}행: 날짜가 비어 있습니다")

        if not ref:
            errors.append(f"{i}행({d}): 본문이 비어 있습니다")
            continue
        if not theme:
            warnings.append(f"{i}행({d}): 월 주제가 비어 있습니다")

        p = parse_ref(ref)
        if p is None:
            errors.append(f"{i}행({d}): 본문 표기를 읽을 수 없습니다 → '{ref}'")
            continue

        book, start, end = p
        book_counter[book] += 1
        intentional = "의도적 재방문" in memo
        parsed.append((i, d, ref, book, start, end, intentional))

        n = approx_verses(start, end)
        if n < 3:
            warnings.append(f"{i}행({d}): 본문이 너무 짧습니다 (약 {n}절) → {ref}")
        if end[0] - start[0] >= 3:
            warnings.append(f"{i}행({d}): 본문이 너무 깁니다 ({end[0]-start[0]+1}장) → {ref}")

    # 날짜 중복
    for d, c in date_counter.items():
        if c > 1:
            errors.append(f"날짜 중복: {d} ({c}회)")

    # 본문 중복·겹침
    by_book = defaultdict(list)
    for item in parsed:
        by_book[item[3]].append(item)

    dup_exact, dup_overlap = [], []
    for book, items in by_book.items():
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                x, y = items[a], items[b]
                if x[6] or y[6]:
                    continue
                if x[4] == y[4] and x[5] == y[5]:
                    dup_exact.append(f"완전 중복: {x[2]} ({x[1]}) ↔ {y[2]} ({y[1]})")
                    continue
                r = overlap_ratio(x[4], x[5], y[4], y[5])
                if r >= 0.5:
                    dup_overlap.append(
                        f"범위 겹침 {int(r*100)}%: {x[2]} ({x[1]}) ↔ {y[2]} ({y[1]})"
                    )

    # 날짜 완전성
    if args.year:
        start_d, end_d = date(args.year, 1, 1), date(args.year, 12, 31)
        have = set(date_counter)
        missing = []
        cur = start_d
        while cur <= end_d:
            if cur.isoformat() not in have:
                missing.append(cur.isoformat())
            cur += timedelta(days=1)
        if missing:
            notes.append(
                f"배정되지 않은 날짜 {len(missing)}일 "
                f"(주 6일 운영이면 주일 52~53일이 정상입니다)"
            )
            notes.append("  예: " + ", ".join(missing[:8]) + (" …" if len(missing) > 8 else ""))

    # 균형
    total = sum(book_counter.values())
    ot = sum(c for b, c in book_counter.items() if b in OT)
    nt = total - ot

    # 출력
    print("=" * 60)
    print(f"  연간 말씀계획 검증 결과 — 총 {len(rows)}행 / 본문 {total}건")
    print("=" * 60)

    print(f"\n[오류] {len(errors)}건")
    for e in errors[:40]:
        print(f"  ✗ {e}")
    if len(errors) > 40:
        print(f"  … 외 {len(errors)-40}건")

    print(f"\n[본문 중복] 완전 중복 {len(dup_exact)}건 / 범위 겹침 {len(dup_overlap)}건")
    for e in dup_exact[:30]:
        print(f"  ✗ {e}")
    for e in dup_overlap[:30]:
        print(f"  ⚠ {e}")

    print(f"\n[경고] {len(warnings)}건")
    for w in warnings[:30]:
        print(f"  ⚠ {w}")
    if len(warnings) > 30:
        print(f"  … 외 {len(warnings)-30}건")

    print("\n[균형]")
    if total:
        print(f"  구약 {ot}건 ({ot/total*100:.1f}%) / 신약 {nt}건 ({nt/total*100:.1f}%)")
        if ot / total > 0.7 or nt / total > 0.7:
            print("  ⚠ 한쪽으로 치우쳐 있습니다. 재검토를 권합니다")
        print("  상위 배정 권:")
        for b, c in book_counter.most_common(8):
            flag = "  ⚠ 20% 초과" if c / total > 0.2 else ""
            print(f"    {b:<4} {c:>3}건 ({c/total*100:.1f}%){flag}")
        unused = [b for b in BOOK_ORDER if b not in book_counter]
        print(f"  미사용 권 {len(unused)}권: {' '.join(unused[:25])}"
              + (" …" if len(unused) > 25 else ""))

    if notes:
        print("\n[참고]")
        for n in notes:
            print(f"  · {n}")

    print("\n" + "=" * 60)
    if errors or dup_exact:
        print("  수정이 필요합니다.")
        sys.exit(1)
    elif dup_overlap or warnings:
        print("  통과 (검토 권장 항목이 있습니다)")
    else:
        print("  통과")
    print("=" * 60)


if __name__ == "__main__":
    main()
