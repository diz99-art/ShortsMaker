"""Small, one-player Streamlit workflow. No CapCut file manipulation."""
import json
from pathlib import Path

import streamlit as st

from shortsmaker.editing_package import build
from shortsmaker.player_assets import (MockEditor, OpenAIEditor, Player, Style, approve_image,
    connect_to_composition, download_photo, generate, generation_plan, search_photos)

ROOT = Path(__file__).parent.resolve()
st.set_page_config(page_title="ShortsMaker 선수 캐리커처", layout="wide")
st.title("야구한장 · 선수 1명 캐리커처")
st.caption("사진 선택 → 원본 보존 → 편집 요청 → 이미지 승인 → CapCut 편집 재료 생성")

with st.form("photo-search"):
    cols = st.columns(3)
    name = cols[0].text_input("선수 이름 (Commons 표기에 맞춘 영문도 가능)")
    team = cols[1].text_input("사진에 해당하는 소속팀")
    season = cols[2].number_input("시즌", min_value=1870, max_value=2100, value=2025)
    searched = st.form_submit_button("사진 검색")
if searched:
    try:
        player = Player(name=name.strip(), team=team.strip(), season=season)
        photos = search_photos(player)
        st.session_state["search"] = {"player": player, "photos": photos}
        st.session_state.pop("folder", None)
        st.session_state.pop("generation", None)
    except Exception as error:
        st.error(f"검색 실패 ({type(error).__name__}). 입력값과 네트워크를 확인하세요.")

search = st.session_state.get("search")
if search:
    photos = search["photos"]
    st.write(f"검색 대상: {search['player'].name} / {search['player'].team} / {search['player'].season}")
    if not photos:
        st.info("조건에 맞는 PNG/JPG가 없습니다. 이름과 당시 팀의 Commons 표기를 확인하세요. 조건을 자동으로 완화하지 않습니다.")
    else:
        for index, photo in enumerate(photos):
            with st.container(border=True):
                left, right = st.columns([1, 3])
                left.image(photo.thumbnail, width=180)
                right.write(f"{index + 1}. {photo.title}")
                right.write(f"{photo.width}×{photo.height} · {photo.license} · 저작자: {photo.author or '확인 불가'}")
                right.markdown(f"[사진 출처]({photo.source})")
                if photo.license_url:
                    right.markdown(f"[라이선스]({photo.license_url})")
                right.write(photo.description[:500])
                right.warning(photo.warning)
        with st.form("select-photo"):
            selected = st.selectbox("선택할 사진", range(len(photos)), format_func=lambda n: f"{n+1}. {photos[n].title}")
            player_id = st.text_input("새 선수 폴더", value="player_01")
            identity = st.checkbox("사진이 입력한 선수·팀·시즌에 해당함을 직접 확인했습니다")
            rights = st.checkbox("사진의 다운로드·변형·상업적 이용 권한과 표시 조건을 확인했습니다")
            downloaded = st.form_submit_button("선택한 사진 다운로드 (원본 보존)")
        if downloaded:
            try:
                st.session_state["folder"] = download_photo(search["player"], photos[selected], ROOT / "assets/players",
                    player_id, identity_confirmed=identity, rights_confirmed=rights)
                st.session_state.pop("generation", None)
                st.success("원본 다운로드 및 출처 저장 완료")
            except Exception as error:
                st.error(f"다운로드 실패: {type(error).__name__}. 확인 체크, 파일 형식, 기존 폴더 및 네트워크를 확인하세요.")

folder = st.session_state.get("folder")
if folder:
    metadata = json.loads((folder / "metadata.json").read_text())
    st.image(str(folder / metadata["original"]), caption="보존된 원본", width=260)
    cols = st.columns(4)
    framing = cols[0].selectbox("구도", ["full", "upper"], format_func=lambda s: {"full": "전신", "upper": "상반신"}[s])
    exaggeration = cols[1].selectbox("얼굴 과장", ["low", "medium", "high"], index=1)
    quality = cols[2].selectbox("출력 품질", ["low", "medium", "high"], index=1)
    transparent = cols[3].checkbox("투명 배경", value=True)
    mock = st.checkbox("Mock 테스트 · GPT 변환 없이 저장 흐름만 검사", value=True)
    style = Style(framing=framing, exaggeration=exaggeration, transparent=transparent, quality=quality)
    plan = generation_plan(folder, style)
    st.code(plan["prompt"], language=None)
    st.write(f"모델: {style.model} · {style.size} · 출력 이미지 비용 약 ${plan['estimated_output_usd']:.3f}")
    st.write(f"입력 포함 계획용 추정 ${plan['estimated_total_usd_range'][0]:.3f}~${plan['estimated_total_usd_range'][1]:.3f}. "
             "실제 입력 토큰에 따라 달라지며 청구 상한은 아닙니다. 선수별 실제 요청 최대 3회, 실패도 포함합니다.")
    st.markdown(f"[공식 가격 확인]({plan['pricing_url']})")
    st.caption("투명 PNG 편집·가격이 확인된 gpt-image-1.5를 명시적으로 사용합니다. 다른 모델로 자동 변경하지 않습니다.")
    with st.form("edit-request"):
        approved = st.checkbox("위 사진과 프롬프트를 OpenAI Images 편집 API로 전송하고 위 예상 비용의 유료 요청 1회를 실행하는 것을 승인합니다")
        requested = st.form_submit_button("Mock 실행" if mock else "승인한 요청 1회 생성 / 재생성")
    if requested:
        try:
            if not mock and not approved:
                raise PermissionError("유료 호출 및 사진 전송 승인이 필요합니다")
            editor = MockEditor() if mock else OpenAIEditor()
            st.session_state["generation"] = generate(folder, style, editor,
                approved_plan_id=plan["id"] if approved else None, paid_and_upload_approved=approved)
            st.success("Mock 저장 완료 (캐리커처 변환 아님)" if mock else "편집 이미지 저장 완료 · 아래에서 확인 후 승인하세요")
        except Exception as error:
            st.error(f"생성 실패: {error}. 요청 상태는 선수 폴더에 보존됩니다.")
    generation = st.session_state.get("generation")
    if generation:
        result = json.loads((generation / "result.json").read_text())
        st.image(str(generation / "caricature.png"), caption=result["status"], width=360)
        if result["status"] == "mock_success":
            st.warning("Mock은 원본을 투명 캔버스에 배치한 저장 시험입니다. GPT 캐리커처가 아니며 실제 패키지 승인 대상이 아닙니다.")
        elif st.button("미리본 생성 이미지 승인"):
            try:
                st.session_state["approved_image"] = approve_image(folder, generation, confirmed=True)
                st.success("승인 이미지 저장 완료")
            except Exception as error:
                st.error(str(error))

approved_image = st.session_state.get("approved_image")
if approved_image:
    st.write(f"승인 이미지: {approved_image}")
    with st.form("connect-package"):
        config_path = st.text_input("기존 입력 구성 JSON 경로")
        rank = st.number_input("적용할 순위", min_value=1, max_value=5, value=5)
        output_path = st.text_input("새 패키지 폴더", value="output/player-package-01")
        linked = st.form_submit_button("승인 이미지로 새 편집 패키지 제작")
    if linked:
        try:
            output = (ROOT / output_path).resolve()
            connection = output.parent / f"{output.name}_input.json"
            connect_to_composition(Path(config_path).expanduser().resolve(), connection, approved_image, rank)
            build(connection, output, ROOT / "assets/fonts/Pretendard-Regular.otf")
            st.success(f"패키지 생성 완료: {output}. CapCut 앱 가져오기는 별도 검증해야 합니다.")
        except Exception as error:
            st.error(f"패키지 생성 실패: {error}")
