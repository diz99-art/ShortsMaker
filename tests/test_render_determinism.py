"""같은 입력이면 렌더 결과(프레임)가 언제나 같은지.

카드 캐시가 id(layer) 를 키로 쓰면, 지워진 레이어의 메모리 주소를 새 레이어가 물려받을 때
예전 그림(카운트업 중간 숫자)이 섞여 나오고, 실행할 때마다 결과가 달라진다.
"""
import gc
import json
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image

from shortsmaker.card_layers import MovingLayer
from shortsmaker.card_mode import CardRenderer
from shortsmaker.models import ROOT, Box, Motion, Project, save_project
from tests.test_card_mode import SONG
from tests.test_effects import click_track

CANVAS = (200, 200)


BOX = Box(x=50, y=50, w=100, h=100)
STILL = Motion(curve=[(0, 1.0), (0.1, 1.0)])


def _layer(img):
    return MovingLayer(img, BOX, STILL, 30, 0, CANVAS)


def _renderer():
    r = object.__new__(CardRenderer)   # _draw_inout 에 필요한 값만 채운다
    r.t = SimpleNamespace(fps=30, card_layout=SimpleNamespace(content_top=0))
    r.v = SimpleNamespace(inout="fade_up")
    r.canvas = CANVAS
    return r


def _center(r, layer):
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 255))
    r._draw_inout(img, layer, None)
    return img.getpixel((100, 100))[:3]


def test_inout_cache_not_fooled_by_reused_id():
    """빨간 레이어 여러 개를 그린 뒤 지우고 파란 레이어를 새로 만든다. 지운 레이어의 주소를 물려받은
    파란 레이어도 파랗게 그려져야 한다 (카운트업 숫자 레이어를 비운 뒤 새 숫자를 만드는 상황)."""
    r = _renderer()
    red_img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    blue_img = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    reused: list[MovingLayer] = []
    keep = []
    for _ in range(20):   # 다른 테스트와 같은 프로세스면 주소가 바로 재사용되지 않을 때가 있어 몇 번 더 시도
        reds = [_layer(red_img) for _ in range(200)]
        firsts = {_center(r, red) for red in reds}
        assert firsts == {(255, 0, 0)}
        old = {id(red) for red in reds}
        del reds
        gc.collect()
        blues = [_layer(blue_img) for _ in range(200)]
        reused = [b for b in blues if id(b) in old]
        if reused:
            break
        keep.append(blues)
    assert reused, "지운 레이어의 주소를 물려받은 새 레이어가 없음 (재현 조건 실패)"
    assert {_center(r, b) for b in reused} == {(0, 0, 255)}   # 예전 빨간 그림이 아니라 지금 레이어의 파란 그림


# ---------------------------------------------------------------- 두 번 렌더 비교

HASH_FRAMES = """
import hashlib, json, sys
from pathlib import Path
from shortsmaker.card_mode import make_renderer
from shortsmaker.models import load_project, load_template
from tests.test_card_mode import SONG
p, d = load_project(Path(sys.argv[1]) / "project.json")
r = make_renderer(p, d, load_template(p.channel), SONG)
out, last = [], None
for i in range(round(p.duration * r.t.fps)):   # render_video 와 같이: 상태가 바뀐 프레임만 다시 그린다
    key = r.state(i)
    if key != last:
        h, last = hashlib.md5(r.frame(i).tobytes()).hexdigest(), key
    out.append(h)
print(json.dumps(out))
"""


def _effects_project(d):
    """효과를 켠 야구 영상: 클린 A + 카운트업(큰 숫자라 글자가 프레임마다 바뀜) + 비트·움직임·2.5D·마스코트·진행."""
    from shortsmaker.card_compose import compose_card
    src = d / "art.png"
    Image.new("RGBA", (600, 900), (0, 0, 0, 0)).save(src)
    for r_ in (5, 4, 3, 2, 1):
        kw = dict(rank=r_, record=f"{1000 + 137 * r_}홈런", year="2010", team="KIA 타이거즈", player="")
        compose_card(src, **kw).save(d / f"card{r_}.png")
        meta: dict = {}
        compose_card(src, **kw, omit_record=True, meta=meta).save(d / f"card{r_}_base.png")
        meta["base"] = f"card{r_}_base.png"
        (d / f"card{r_}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    click_track(d / "music.wav", seconds=30.0)
    p = Project.model_validate(dict(
        channel="yagu_v2", title="결정성\n시험", duration=30, music="music.wav",
        items=[{"rank": r_, "name": f"팀{r_}", "value": f"{1000 + 137 * r_}홈런", "image": f"card{r_}.png"}
               for r_ in (5, 4, 3, 2, 1)],
        lyrics=[l.model_dump() for l in SONG], hook="1위는 누구?",
        variation={"look": "clean_a", "count": "countup", "beat": "pulse_flash", "motion": "kenburns",
                   "depth": "pan", "mascot": "right", "progress": "dots", "hook": "slam", "sfx": "off"}))
    save_project(p, d)


def _frame_hashes_twice(d):
    """두 프로세스에서 동시에 (프로세스가 다르면 메모리 주소도 다르다)."""
    procs = [subprocess.Popen([sys.executable, "-c", HASH_FRAMES, str(d)], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate() for p in procs]
    assert all(p.returncode == 0 for p in procs), [e[-2000:] for _, e in outs]
    return [json.loads(o.strip().splitlines()[-1]) for o, _ in outs]


def test_same_input_same_frames_in_two_processes(tmp_path):
    _effects_project(tmp_path)
    a, b = _frame_hashes_twice(tmp_path)
    assert len(a) == 900
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert not diff, f"{len(diff)}프레임이 다름 (처음 {diff[:5]})"
    assert len(set(a)) > 100                   # 움직이는 영상인지 (같은 그림만 나오면 의미 없는 비교)
