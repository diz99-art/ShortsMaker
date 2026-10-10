"""자동 가사 타이밍 정확도 평가: 자동 결과 LRC vs 정답(사람이 고친) LRC.

  .venv/bin/python scripts/eval_align.py projects/x/lyrics_auto.lrc projects/x/lyrics_edited.lrc

같은 가사 줄끼리(순서대로) 시작 시간 차이를 비교해 ±0.3/0.5/1.0초 안에 든 줄 수를 센다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shortsmaker.lyrics import parse_lrc  # noqa: E402

THRESHOLDS = (0.3, 0.5, 1.0)


def compare(auto_text: str, ref_text: str) -> dict:
    auto, ref = parse_lrc(auto_text), parse_lrc(ref_text)
    pairs, j = [], 0
    for r in ref:
        for k in range(j, len(auto)):
            if auto[k].text == r.text:
                pairs.append((r, auto[k]))
                j = k + 1
                break
        else:
            pairs.append((r, None))
    diffs = [abs(a.start - r.start) for r, a in pairs if a]
    return {
        "rows": [(r.text, r.start, a.start if a else None, round(a.start - r.start, 2) if a else None,
                  round(a.end - r.end, 2) if a else None) for r, a in pairs],
        "total": len(ref), "matched": len(diffs),
        "within": {t: sum(d <= t + 1e-9 for d in diffs) for t in THRESHOLDS},
        "mean": round(sum(diffs) / len(diffs), 2) if diffs else None,
        "max": round(max(diffs), 2) if diffs else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("auto")
    ap.add_argument("reference")
    a = ap.parse_args()
    res = compare(Path(a.auto).read_text(encoding="utf-8-sig"), Path(a.reference).read_text(encoding="utf-8-sig"))
    print(f"{'정답 시작':>8} {'자동 시작':>8} {'시작 차':>7} {'끝 차':>7}  가사")
    for text, rs, as_, ds, de in res["rows"]:
        fmt = lambda v: f"{v:8.2f}" if v is not None else "       -"
        print(f"{fmt(rs)} {fmt(as_)} {fmt(ds)[1:]} {fmt(de)[1:]}  {text}")
    n = res["total"]
    print(f"\n전체 {n}줄 (자동 결과에서 찾은 줄 {res['matched']})")
    for t, c in res["within"].items():
        print(f"  시작 ±{t}초 안: {c}/{n}줄 ({c / n * 100:.0f}%)")
    print(f"  평균 오차 {res['mean']}초, 최대 {res['max']}초")
    return 0


if __name__ == "__main__":
    sys.exit(main())
