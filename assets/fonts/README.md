# 폰트

폰트 파일(.ttf / .otf)은 저장소에 넣지 않습니다. 아래 명령이 공식 배포처에서 내려받아 이 폴더에 넣습니다.

```sh
make setup        # 또는 .venv/bin/python scripts/setup_assets.py
```

세 폰트 모두 **SIL Open Font License 1.1** 입니다 (상업적 사용 가능, 폰트 파일 단독 판매 금지).
라이선스 원문은 이 폴더의 `*-LICENSE.txt` 에 있습니다.

## G마켓 산스 (Gmarket Sans)
- 쓰는 곳: 제목, 후킹 문구, 카드의 순위·기록 숫자
- 파일: `GmarketSansTTFBold.ttf`, `GmarketSansTTFMedium.ttf`
- 공식 페이지: https://gds.gmarket.co.kr/ · 직접 받기: https://corp.gmarket.com/fonts/GmarketSansTTF.zip
- © 2019 eBay Korea Co., Ltd. — SIL OFL 1.1 (`GmarketSans-LICENSE.txt`)

## Pretendard
- 쓰는 곳: 자막, 이름, 설명 등 작은 글씨
- 파일: `Pretendard-Regular.otf`, `-SemiBold`, `-Bold`, `-ExtraBold`, `-Black`
- 공식 저장소: https://github.com/orioncactus/pretendard (v1.3.9 릴리스의 `public/static/`)
- © 2021 Kil Hyung-jin — SIL OFL 1.1 (`Pretendard-LICENSE.txt`)

## Jua (주아)
- 쓰는 곳: 하늘 배지형 템플릿의 가사 자막
- 파일: `Jua-Regular.ttf`
- 출처: Google Fonts https://fonts.google.com/specimen/Jua (파일: https://github.com/google/fonts/tree/main/ofl/jua)
- © 2018 The Jua Project Authors — SIL OFL 1.1 (`Jua-LICENSE.txt`)

다른 폰트를 쓰려면 파일을 이 폴더에 넣고 `templates/<채널>/template.json` 의 `fonts` 를 바꿉니다.
