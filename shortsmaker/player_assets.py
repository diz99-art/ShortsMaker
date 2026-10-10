"""One-player workflow. Only explicit live edit approval can upload a photo."""
import base64
import hashlib
import html
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-image-1.5"
MAX_BYTES = 20 * 1024 * 1024
DEFAULT_PROMPT = """첨부한 야구 선수 사진을 참고하여
귀여운 2D 만화풍 캐리커처로 변환해줘.
얼굴 특징과 헤어스타일, 유니폼의 특징을
최대한 유지해줘.
야구 선수임을 알아볼 수 있도록 만들어줘.
머리부터 발끝까지 잘리지 않도록 하고,
캐릭터 외의 배경은 투명하게 만들어줘.
글자나 워터마크는 추가하지 마."""


def clean(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


class Player(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    team: str = Field(min_length=1, max_length=120)
    season: int = Field(ge=1870, le=2100)


class Photo(BaseModel):
    page_id: int
    title: str
    url: str
    thumbnail: str
    source: str
    width: int
    height: int
    mime: str
    author: str = ""
    license: str = "확인 불가"
    license_url: str = ""
    description: str = ""
    commercial_candidate: bool = False
    warning: str = ""


class Style(BaseModel):
    framing: Literal["full", "upper"] = "full"
    exaggeration: Literal["low", "medium", "high"] = "medium"
    transparent: bool = True
    quality: Literal["low", "medium", "high"] = "medium"
    # Explicit, priced legacy model. No automatic model substitutions.
    model: Literal["gpt-image-1.5"] = "gpt-image-1.5"
    size: Literal["1024x1536"] = "1024x1536"
    extra: str = ""  # 선수별 덧붙임 (장면 설명, 로고·글씨 금지 등)

    def prompt(self) -> str:
        text = DEFAULT_PROMPT
        if self.framing == "upper":
            text = text.replace("머리부터 발끝까지 잘리지 않도록 하고,", "머리와 양 어깨가 잘리지 않는 상반신 구도로 만들고,")
        if not self.transparent:
            text = text.replace("캐릭터 외의 배경은 투명하게 만들어줘.", "캐릭터 뒤 배경은 단순한 흰색으로 만들어줘.")
        level = {"low": "약하게", "medium": "보통으로", "high": "강하게"}[self.exaggeration]
        text += f"\n야구한장 스타일 v1: 둥근 형태, 깨끗한 굵은 외곽선, 부드러운 셀 채색. 얼굴 과장은 {level}."
        return text + (f"\n{self.extra.strip()}" if self.extra.strip() else "")


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json_new(path: Path, data: dict):
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def safe_media_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "upload.wikimedia.org" or parsed.username or parsed.password:
        raise ValueError("Wikimedia HTTPS 미디어 URL만 허용합니다")
    if parsed.port not in (None, 443):
        raise ValueError("허용하지 않는 포트입니다")


def new_session():
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "ShortsMaker/0.3 (local media preparation; Wikimedia Commons API)"})
    return session


def default_query(player: Player) -> str:
    return f'"{player.name.strip()}" "{player.team.strip()}" {player.season} baseball'


def search_photos(player: Player, session=None, limit=12, query: str | None = None) -> list[Photo]:
    # No silent broadening: an empty strict search stays empty. 넓힌 검색어는 사용자가 화면에서 직접 고친 것만 쓴다.
    query = (query or default_query(player)).strip()
    session = session or new_session()
    response = session.get(COMMONS_API, params={
        "action": "query", "format": "json", "formatversion": 2, "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": max(1, min(limit, 20)),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 320,
    }, timeout=(10, 30), allow_redirects=False)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError("Wikimedia 검색 API 오류")
    results = []
    for page in body.get("query", {}).get("pages", []):
        info = page.get("imageinfo", [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        try:
            safe_media_url(info["url"])
            safe_media_url(info.get("thumburl", info["url"]))
        except (ValueError, KeyError):
            continue
        meta = info.get("extmetadata", {})
        field = lambda name: clean(meta.get(name, {}).get("value", ""))
        license_name = field("LicenseShortName") or "확인 불가"
        normalized = license_name.upper().replace("_", "-")
        commercial = (normalized.startswith(("CC BY ", "CC BY-SA ", "CC-BY-", "CC-BY-SA-"))
                      or normalized in ("CC0", "CC0 1.0", "PUBLIC DOMAIN"))
        if "NC" in normalized or "ND" in normalized:
            commercial = False
        warning = "라이선스 메타데이터만으로 이용 권한을 보장하지 않습니다. 출처·저작자·표시/동일조건 및 초상권을 확인하세요."
        if not commercial:
            warning = "상업적 이용·변형 권한을 확인할 수 없습니다. 다운로드·변환 전에 직접 확인해야 합니다. " + warning
        results.append(Photo(page_id=page["pageid"], title=clean(page["title"]), url=info["url"],
                             thumbnail=info.get("thumburl", info["url"]), source=info["descriptionurl"],
                             width=info["width"], height=info["height"], mime=info["mime"],
                             author=field("Artist"), license=license_name, license_url=field("LicenseUrl"),
                             description=field("ImageDescription"), commercial_candidate=commercial, warning=warning))
    return sorted(results, key=lambda photo: not photo.commercial_candidate)


def validate_image(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("이미지 크기는 20MB 이하여야 합니다")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ("JPEG", "PNG") or image.width * image.height > 25_000_000:
            raise ValueError("PNG/JPG, 최대 2500만 픽셀만 지원합니다")
        info = {"format": image.format, "width": image.width, "height": image.height}
        image.verify()
    return info


def fetch_image(url: str, session=None) -> bytes:
    safe_media_url(url)
    session = session or new_session()
    # Redirects deliberately rejected; no unchecked remote destination.
    response = session.get(url, stream=True, timeout=(10, 45), allow_redirects=False)
    try:
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise ValueError("다운로드 리디렉션은 허용하지 않습니다")
        if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
            raise ValueError("다운로드 파일이 20MB를 초과합니다")
        chunks, length = [], 0
        for chunk in response.iter_content(64 * 1024):
            length += len(chunk)
            if length > MAX_BYTES:
                raise ValueError("다운로드 파일이 20MB를 초과합니다")
            chunks.append(chunk)
        data = b"".join(chunks)
        validate_image(data)
        return data
    finally:
        response.close()


def download_photo(player: Player, photo: Photo, root: Path, player_id: str,
                   *, identity_confirmed: bool, rights_confirmed: bool, session=None) -> Path:
    if not identity_confirmed or not rights_confirmed:
        raise PermissionError("선수 동일인 여부와 다운로드·변형·상업적 이용 권한을 먼저 확인하세요")
    if not re.fullmatch(r"player_[A-Za-z0-9_-]+", player_id):
        raise ValueError("선수 폴더 이름은 player_로 시작하는 영문·숫자·밑줄이어야 합니다")
    folder = root.resolve() / player_id
    if folder.exists():
        meta = json.loads((folder / "metadata.json").read_text())
        if meta["photo"]["url"] != photo.url or meta["player"] != player.model_dump():
            raise FileExistsError("기존 선수/사진을 덮어쓰지 않습니다. 새 선수 폴더를 사용하세요")
        if digest((folder / meta["original"]).read_bytes()) != meta["original_sha256"]:
            raise ValueError("보존 원본의 해시가 변경되었습니다")
        return folder
    data = fetch_image(photo.url, session)
    info = validate_image(data)
    original = "original.jpg" if info["format"] == "JPEG" else "original.png"
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / original).open("xb") as file:
        file.write(data)
    write_json_new(folder / "metadata.json", {
        "created_at": stamp(), "player": player.model_dump(), "photo": photo.model_dump(),
        "original": original, "original_sha256": digest(data), "image": info,
        "identity_confirmed": True, "rights_confirmed": True,
    })
    return folder


def generation_plan(folder: Path, style: Style) -> dict:
    metadata = json.loads((folder / "metadata.json").read_text())
    original = (folder / metadata["original"]).read_bytes()
    if digest(original) != metadata["original_sha256"]:
        raise ValueError("보존 원본의 해시가 변경되었습니다")
    # Output-image cost only; inputs are variable and billed separately.
    output_usd = {"low": .013, "medium": .05, "high": .20}[style.quality]
    plan = {"source_sha256": digest(original), "source": metadata["photo"]["source"],
            "player": metadata["player"], "style": style.model_dump(), "prompt": style.prompt(),
            "estimated_output_usd": output_usd, "estimated_total_usd_range": [output_usd, output_usd + .20],
            "estimate_note": "입력 이미지/프롬프트 비용 별도. 범위는 계획용 추정이며 청구 상한이 아닙니다.",
            "pricing_url": PRICING_URL, "pricing_checked_at": "2026-10-09", "destination": "https://api.openai.com/v1/images/edits"}
    plan["id"] = digest(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode())
    return plan


class MockEditor:
    """Offline transport fixture, explicitly NOT a GPT-created caricature."""
    is_mock = True

    def edit(self, original: Path, style: Style, prompt: str) -> tuple[bytes, dict]:
        with Image.open(original) as image:
            thumbnail = ImageOps.contain(ImageOps.exif_transpose(image).convert("RGBA"), (600, 1000))
        canvas = Image.new("RGBA", (1024, 1536), (255, 255, 255, 0 if style.transparent else 255))
        canvas.alpha_composite(thumbnail, ((1024 - thumbnail.width) // 2, (1536 - thumbnail.height) // 2))
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue(), {"mock": True}


class OpenAIEditor:
    is_mock = False

    def __init__(self, client=None):
        if client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY 환경변수가 필요합니다. 키를 채팅이나 코드에 넣지 마세요")
            import httpx
            from openai import OpenAI
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url="https://api.openai.com/v1",
                            max_retries=0, timeout=180, http_client=httpx.Client(trust_env=False))
        self.client = client

    def edit(self, original: Path, style: Style, prompt: str) -> tuple[bytes, dict]:
        with original.open("rb") as image:
            response = self.client.images.edit(model=style.model, image=image, prompt=prompt, n=1,
                size=style.size, quality=style.quality, background="transparent" if style.transparent else "opaque",
                output_format="png", input_fidelity="high")
        if not response.data or not response.data[0].b64_json:
            raise ValueError("편집 응답에 이미지 데이터가 없습니다")
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        return base64.b64decode(response.data[0].b64_json, validate=True), usage


def generate(folder: Path, style: Style, editor, *, approved_plan_id: str | None = None,
             paid_and_upload_approved: bool = False, max_requests: int = 3) -> Path:
    plan = generation_plan(folder, style)
    mock = bool(editor.is_mock)
    if not mock and (not paid_and_upload_approved or approved_plan_id != plan["id"]):
        raise PermissionError("현재 사진·프롬프트·비용의 유료 호출 및 OpenAI 업로드 승인이 필요합니다")
    if not 1 <= max_requests <= 3:
        raise ValueError("선수별 요청 제한은 최대 3회입니다")
    lock = folder / ".generation-lock"
    lock.mkdir()  # Single local writer, including simultaneous Streamlit sessions.
    try:
        attempts = folder / "generations"
        attempts.mkdir(exist_ok=True)
        prior = list(attempts.glob("*/request.json"))
        used = sum(json.loads(p.read_text())["mock"] == mock for p in prior)
        if used >= max_requests:
            raise ValueError("요청 횟수 제한에 도달했습니다. 실패 요청도 포함하며 자동 재호출하지 않습니다")
        index = len(list(attempts.iterdir())) + 1
        target = attempts / f"generation_{index:03}"
        target.mkdir()
        write_json_new(target / "request.json", {"created_at": stamp(), "mock": mock,
                      "plan": plan, "paid_and_upload_approved": paid_and_upload_approved if not mock else False})
        original_meta = json.loads((folder / "metadata.json").read_text())
        try:
            data, usage = editor.edit(folder / original_meta["original"], style, plan["prompt"])
            info = validate_image(data)
            with Image.open(io.BytesIO(data)) as image:
                alpha = image.convert("RGBA").getchannel("A").getextrema()
                transparent = alpha[0] < 255 and alpha[1] > 0
            if info["format"] != "PNG" or (style.transparent and not transparent):
                raise ValueError("요청한 투명 PNG가 반환되지 않았습니다")
            with (target / "caricature.png").open("xb") as file:
                file.write(data)
            write_json_new(target / "result.json", {"status": "mock_success" if mock else "api_success",
                           "sha256": digest(data), "transparent": transparent, "image": info, "usage": usage})
        except Exception as error:
            # Do not store raw SDK exceptions: may contain request or credential data.
            write_json_new(target / "failure.json", {"status": "failed", "error_type": type(error).__name__,
                           "retry": "재시도는 새 요청으로 실행하고 다시 승인해야 합니다"})
            raise RuntimeError(f"이미지 편집 실패 ({type(error).__name__}). 자동 재시도 없음") from None
        return target
    finally:
        lock.rmdir()


def approve_image(folder: Path, generation: Path, *, confirmed: bool, allow_mock=False) -> Path:
    if not confirmed:
        raise PermissionError("이미지 미리보기 후 승인이 필요합니다")
    generation = generation.resolve()
    if generation.parent != (folder / "generations").resolve():
        raise ValueError("선수 폴더의 생성 결과만 승인할 수 있습니다")
    result = json.loads((generation / "result.json").read_text())
    if result["status"] == "mock_success" and not allow_mock:
        raise ValueError("Mock 결과는 실제 선수 캐리커처로 승인할 수 없습니다")
    source = generation / "caricature.png"
    data = source.read_bytes()
    if digest(data) != result["sha256"]:
        raise ValueError("생성 이미지가 변경되었습니다")
    # Each regeneration gets a separate approved artifact, never replaces earlier ones.
    destination = folder / ("caricature.png" if not (folder / "caricature.png").exists()
                            else f"caricature_{generation.name}.png")
    with destination.open("xb") as file:
        file.write(data)
    write_json_new(destination.with_suffix(".approval.json"), {"approved_at": stamp(), "sha256": digest(data),
                   "generation": generation.name, "mock": result["status"] == "mock_success",
                   "original_metadata": json.loads((folder / "metadata.json").read_text()),
                   "generation_request": json.loads((generation / "request.json").read_text())})
    return destination


def connect_to_composition(config: Path, destination: Path, image: Path, rank: int, *, allow_mock=False) -> Path:
    from shortsmaker.editing_package import Composition
    approval = json.loads(image.with_suffix(".approval.json").read_text())
    if approval["mock"] and not allow_mock:
        raise ValueError("Mock 이미지는 실제 제작 구성에 연결할 수 없습니다")
    if digest(image.read_bytes()) != approval["sha256"]:
        raise ValueError("승인 이미지가 변경되었습니다")
    composition = Composition.model_validate_json(config.read_text())
    if rank not in range(1, 6):
        raise ValueError("연결 순위는 1~5입니다")
    base = config.resolve().parent
    composition.music = str((base / composition.music).resolve())
    for scene in composition.scenes:
        scene.image = str(image.resolve()) if scene.rank == rank else str((base / scene.image).resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_new(destination, composition.model_dump())
    return destination
