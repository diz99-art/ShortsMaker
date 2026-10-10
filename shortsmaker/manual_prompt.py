"""캐리커처 수동 생성(무료) — ChatGPT·Gemini 에 직접 붙여 넣을 프롬프트와 재료 폴더.

선수마다 players/player_sN/manual/ 에 prompt.txt, 스타일 참고 그림, 선수 사진을 모은다.
사용자가 웹에서 그림을 만들어 내려받으면, 화면이 다운로드 폴더의 새 이미지를 보여주고
'이 순위에 사용'을 누르면 배경을 지워 그 순위의 캐리커처로 쓴다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .rank_extract import RankRow, team_colors

DOWNLOADS = Path(os.environ.get("SHORTSMAKER_DOWNLOADS", Path.home() / "Downloads"))
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}

COLOR_NAMES = {  # 프롬프트용 대략적인 색 이름
    "#EA0029": "빨강", "#06141F": "검정", "#074CA1": "파랑", "#0A1E3C": "남색", "#C30452": "자주빛 빨강",
    "#1B1B1B": "검정", "#131230": "남색", "#ED1C24": "빨강", "#EB1C24": "빨강", "#CE0E2D": "빨강",
    "#FFB81C": "노랑", "#041E42": "남색", "#D00F31": "빨강", "#FC4E00": "주황", "#315288": "파랑",
    "#C7A079": "황금", "#570514": "버건디", "#B07F4A": "갈색", "#0046AD": "파랑", "#C8102E": "빨강",
    "#1D3F8C": "파랑", "#E21F26": "빨강", "#0F6B3B": "초록", "#F2C200": "노랑", "#0072CE": "하늘색",
    "#00A3E0": "하늘색", "#FF6600": "주황", "#003087": "남색", "#1F3B73": "남색", "#0B2C6B": "남색",
    "#E5A823": "황금", "#F37321": "주황", "#820024": "버건디", "#2B2B2B": "검정",
}


def default_scene(row: RankRow) -> str:
    """가사로 장면 설명 초안 (사용자가 표에서 고친다)."""
    text = row.lyric
    if any(k in text for k in ("남아", "잔류", "남는")):
        return "구단 기자회견장 책상에 앉아 마이크 앞에서 엄지를 들고 활짝 웃는 모습"
    if any(k in text for k in ("간다", "이적", "돌아온", "돌아와", "떠나")):
        return "새 팀 관계자와 악수하며 계약을 축하하는 모습"
    if "연패" in row.record or "연패" in text:
        return "고개를 숙이고 아쉬워하는 투수의 모습"
    return "배트를 힘차게 휘두르는 역동적인 타격 자세"


def build_prompt(row: RankRow, scene: str, *, style: str = "sticker", has_photo: bool = True) -> str:
    primary, secondary = team_colors(row.team)
    colors = f"{COLOR_NAMES.get(primary, primary)}·{COLOR_NAMES.get(secondary, secondary)}"
    look = ("2등신 치비(chibi) 캐리커처 스티커. 큰 머리, 작은 몸, 굵고 깨끗한 검은 외곽선, 선명한 셀 채색, "
            "스티커처럼 바깥에 얇은 흰 테두리" if style == "sticker" else
            "역동적인 만화풍 캐리커처(머리 크게 과장), 굵은 외곽선, 선명한 셀 채색, 전신")
    who = (f"첨부한 선수 사진 속 인물({row.player}, {row.team} 야구 선수)의 얼굴 특징·헤어스타일을 살려서"
           if has_photo else f"{row.player}({row.team} 야구 선수)를")
    attach = "첨부 1: 스타일 참고 그림 / 첨부 2: 선수 사진" if has_photo else "첨부: 스타일 참고 그림"
    return "\n".join([
        f"[{attach}]",
        f"첨부한 참고 그림과 같은 그림체로, {who} 그려줘.",
        f"- 스타일: {look}",
        f"- 장면: {scene}",
        f"- 옷 색: {colors} 계열 야구 유니폼 또는 점퍼",
        "- 금지: 구단 로고·엠블럼·마크, 글자·숫자·등번호·영문 팀 이름, 워터마크, 말풍선 — 그림 안에 글씨가 하나도 없어야 함",
        "- 인물 1~2명, 머리부터 발끝까지(또는 책상 위 상반신) 잘리지 않게, 세로 3:4 비율",
        "- 배경: 투명 배경 PNG. 투명이 안 되면 완전히 흰 단색 배경 (그림자·바닥 무늬 없이)",
    ])


def manual_dir(project_dir: Path, slot: int) -> Path:
    return project_dir / "players" / f"player_s{slot}" / "manual"


def prepare_folder(project_dir: Path, slot: int, row: RankRow, scene: str, *, style: str,
                   style_ref: Path | None, photo: Path | None) -> tuple[Path, str]:
    """prompt.txt + 참고 그림 + 선수 사진을 한 폴더에. (폴더, 프롬프트)"""
    folder = manual_dir(project_dir, slot)
    folder.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(row, scene, style=style, has_photo=photo is not None)
    (folder / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    if style_ref and style_ref.exists():
        shutil.copy2(style_ref, folder / f"1_스타일참고{style_ref.suffix}")
    if photo and photo.exists():
        shutil.copy2(photo, folder / f"2_선수사진_{row.player}{photo.suffix}")
    return folder, prompt


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def open_folder(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


def new_images(since: float, folder: Path | None = None, *, settle: float = 1.0) -> list[Path]:
    """since(유닉스 시각) 이후 다운로드 폴더에 생긴 이미지. 아직 쓰는 중인 파일(1초 안에 바뀐 것)은 뺀다."""
    folder = folder or DOWNLOADS
    if not folder.exists():
        return []
    now = time.time()
    out = []
    for p in folder.iterdir():
        if p.suffix.lower() in IMAGE_EXT and p.is_file():
            m = p.stat().st_mtime
            if m > since and now - m >= settle and not p.name.startswith("."):
                out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)
