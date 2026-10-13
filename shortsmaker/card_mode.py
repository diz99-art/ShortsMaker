"""카드 모드(yagu_v2) 렌더 — 순위 이미지가 정보가 다 들어간 완성 카드일 때.

classic 모드와 달리 장면 클립 + xfade 가 아니라, Pillow 로 프레임을 직접 그려 FFmpeg 로 인코딩한다.
확대 + 모션 블러로 '쾅' 들어오는 카드, 크게 시작해 제자리로 줄어드는 로고, 노래에 맞춰
한 글자씩 나타나는 자막을 프레임 단위로 정확히 맞추기 위해서다.

레이어 (아래 → 위)
  chrome : 흰 배경 + 아래쪽 '@야구한장 | 🔔구독' (움직이지 않음)
  moving : 인트로 로고 / 순위 카드 (구분선 아래로만 보임)
  header : 알약 로고 + 파란 2줄 제목 + 검은 구분선 (움직이는 레이어가 넘어와도 가려지지 않게 위에)
  sub    : 타자 효과 자막

장면 전환 시점은 items[].start 가 있으면 그 값을, 없으면 가사 자동 맞추기 결과에서
"5위", "4위"… 를 부르는 순간(단어 타이밍 안에서 글자 위치로 계산)을 쓴다.

파일 나눔: 시간 계산은 card_timing.py, 레이어 그림은 card_layers.py, 여기는 프레임 조립(CardRenderer)과 인코딩.
"""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import effects as fx
from . import mascot as msc
from .card_layers import (
    MovingLayer,
    SubtitleLayout,
    _load_rgba,
    layout_stat,
    layout_subtitle,
    render_chrome,
    render_stat,
    render_subtitle,
    render_title_card,
    stat_visible,
)
from .card_timing import CardModeError, card_timeline, char_times, hook_length, sfx_cues
from .ffmpeg_builder import FINAL_ACODEC, FINAL_VCODEC, TO_TV_YUV420, FFmpegError, _f, ffmpeg_bin
from .models import LyricLine, Project, Template
from .scenes import SceneBuildResult, SceneLayers, _font
from .sfx import mix_filter
from .timeline import Scene
from .variation import (
    ACCENTS,
    INOUT_LEN,
    LINE_RISE,
    TITLE_CARD,
    TOP_FLASH,
    TOP_GAIN,
    Variation,
    idle_transform,
    intro_motion,
    top_shake,
    transition_motion,
)

ProgressFn = Callable[[float, str], None]


# ---------------------------------------------------------------- 프레임 렌더러

@dataclass
class CardRenderer:
    project: Project
    project_dir: Path
    t: Template
    scenes: list[Scene]
    lines: list[LyricLine]
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        vd = dict(self.project.variation or {})
        vd.setdefault("look", self.t.card.default_look)  # 하늘 배지형 등: 템플릿 기본 스타일
        self.v = Variation.model_validate(vd)
        self.clean = self.v.look in ("clean_a", "clean_b")
        if self.clean and not self.t.card.keep_subtitle_style:  # 클린: 자막은 단색 + 은은한 그림자 (두꺼운 테두리 없음)
            st0 = self.t.card.subtitle
            st0 = st0.model_copy(update=dict(color="#14171C", stroke_width=0, shadow_offset=(0, 5),
                                             shadow_color="#000000", shadow_opacity=0.16, shadow_blur=6))
            self.t = self.t.model_copy(update={"card": self.t.card.model_copy(update={"subtitle": st0})})
        if self.clean:  # 흔들림은 1위만
            from .variation import CALM
            self.v = self.v.model_copy(update=dict(CALM))  # 비트 펄스·그림 움직임·2.5D 없음, 카드는 인·아웃만
        t, c = self.t, self.t.card
        self.canvas = (t.width, t.height)
        self.base, self.head = render_chrome(t, self.project, self.project_dir)
        card_m, card_dx, self.flash = transition_motion(self.v.transition, c.card_motion)
        if self.clean:
            self.flash = 0.0  # 클린: 흰 섬광 없음 (인·아웃 효과만)
        intro_m, intro_dx = intro_motion(self.v.intro, c.intro_motion)
        self.title_card = render_title_card(t, self.project) if self.v.intro == "title_card" else None
        self.stat_color = c.accent_color or ACCENTS.get(self.v.accent, {}).get("stat")
        self.main_color = c.main_color or "#0043C8"
        self._sub_cache: dict[tuple, Image.Image] = {}
        if self.project.intro_image:  # 프로젝트 인트로 캐릭터
            intro, intro_box = self.project_dir / self.project.intro_image, c.intro_box or c.logo_box
            if not intro.exists():
                raise CardModeError(f"인트로 이미지가 없습니다: {intro}")
        else:
            intro, intro_box = t.asset_path(c.logo), c.logo_box
            if intro is None:
                raise CardModeError(f"채널 로고 이미지가 없습니다: templates/{self.project.channel}/{c.logo}")
        self.layers: dict[int, MovingLayer] = {
            0: MovingLayer(intro, intro_box, intro_m, t.fps, c.content_top, self.canvas, dx=intro_dx)}
        self.accent_rank = ACCENTS.get(self.v.accent, ACCENTS["red_gold"])["rank"]
        self._counts: dict[int, dict] = {}       # 장면 → 카운트업 정보 (바탕 카드, 기록 글자 위치)
        self._count_layers: dict[tuple[int, str], MovingLayer] = {}
        self._depth: dict[int, dict] = {}      # 장면 → 2.5D 층 (카드 크기로 줄여 둠)
        for s in self.scenes[1:]:
            if s.kind == "bonus":
                self.layers[s.index] = self._bonus_layer()
                continue
            if s.kind == "chart":
                self.layers[s.index] = self.layers[s.index - 1]  # 그래프 장면은 카드를 그리지 않음 (자리만)
                continue
            if s.kind == "outro":  # 엔딩 카드: 템플릿 자리에 (순위 카드와 같은 인·아웃)
                outro_p = t.brand_asset(c.outro_image)
                self.layers[s.index] = MovingLayer(_load_rgba(str(outro_p)), c.outro_box or c.card_box, card_m, t.fps,
                                                   c.content_top, self.canvas, dx=card_dx)
                continue
            if not s.item.image:
                raise CardModeError(f"[{s.item.rank}위] 카드 이미지가 지정되지 않았습니다")
            img_path = self.project_dir / s.item.image
            self.layers[s.index] = MovingLayer(img_path, c.card_box, card_m, t.fps, c.content_top, self.canvas,
                                               dx=card_dx)
            meta_p = img_path.with_suffix(".json")
            if self.v.count != "static" and meta_p.exists():
                import json as _json
                meta = _json.loads(meta_p.read_text(encoding="utf-8"))
                base_p = self.project_dir / meta.get("base", "")
                if meta.get("record") and base_p.is_file() and fx.record_value(meta["record"]) is not None:
                    self._counts[s.index] = {**meta, "base_img": _load_rgba(str(base_p))}
            if self.v.depth != "off" and meta_p.exists():
                import json as _json
                meta = _json.loads(meta_p.read_text(encoding="utf-8"))
                paths = {n: self.project_dir / q for n, q in (meta.get("layers") or {}).items()}
                if paths and all(q.is_file() for q in paths.values()):
                    lay = self.layers[s.index]
                    size = (max(1, round(lay.w)), max(1, round(lay.h)))
                    self._depth[s.index] = {"layers": {n: _load_rgba(str(q)).resize(size, Image.LANCZOS)
                                                       for n, q in paths.items()},
                                            "meta": meta, "scale": size[0] / lay.src.width}
        # 후킹 문구 (인트로 앞 0~2초)
        self.hook_len = hook_length(self.project, self.scenes)
        if self.project.hook.strip() and not self.hook_len:
            self.warnings.append("인트로가 짧아 후킹 문구를 넣지 못했습니다")
        self.hook_img = None
        if self.hook_len:
            self.hook_img = (fx.clean_hook_image(self.project.hook, self.stat_color or "#F26A00", t.width) if self.clean
                             else fx.hook_image(self.project.hook, self.accent_rank, self.stat_color or "#EB1B1B",
                                                t.width))
        if self.project.bonus is not None and not any(s.kind == "bonus" for s in self.scenes):
            self.warnings.append("1위 장면 뒤 시간이 모자라 보너스 장면을 넣지 못했습니다 "
                                 "(영상 끝을 '반주 끝까지'로 해 보세요)")
        # 비트 (카드 펄스·마스코트 까딱·키워드 터뜨리기가 같이 씀)
        self.beatmap: fx.BeatMap | None = None
        need = self.v.beat != "off" or self.v.mascot != "off" or self.v.burst != "off"
        if need and self.project.music:
            try:
                self.beatmap = fx.load_beats(self.project_dir, self.project.music).shifted(self.project.music_start)
            except Exception as e:  # noqa: BLE001 — 비트 분석이 안 되면 효과만 빼고 렌더
                self.warnings.append(f"비트 분석 실패로 비트 효과를 뺐습니다: {e}")
        self.beats = self.beatmap if self.v.beat != "off" else None   # 카드 펄스용
        self._label_font = t.font_path("black")
        self._masks: dict[int, Image.Image | None] = {}
        self._rest: dict[int, Image.Image] = {}   # 클린 카드 제자리 그림 (레이어별)
        self._bubble_xy: dict[tuple[int, int], tuple[int, int]] = {}
        self.cues = sfx_cues(self.project, t, self.scenes)
        if self.v.depth != "off" and not self._depth:
            self.warnings.append("2.5D 입체는 분위기 카드 디자인으로 합성한 카드에서만 됩니다 (카드를 다시 합성하세요)")
        # 데이터 그래픽
        self.chart_bars = fx.chart_bars(self.project.ordered_items)
        self.chart_base = fx.record_value(self.project.gauge_base) if self.project.gauge_base else None
        # 마스코트
        self.mascot = None
        self.mascot_dir = msc.ASSETS_DIR / self.t.card.mascot_dir
        has_art = self.mascot_dir.is_dir() and any(self.mascot_dir.glob("*.png"))
        if self.v.mascot != "off" and not has_art and self.t.card.mascot_dir != "mascot":
            self.warnings.append(f"assets/{self.t.card.mascot_dir}/ 에 마스코트 그림이 없어 마스코트 없이 만듭니다")
        elif self.v.mascot != "off":
            self.mascot = msc.make_plan(self.scenes, self.lines, mood=self.project.mood,
                                        bubbles=self.project.bubbles, hook_len=self.hook_len,
                                        duration=self.project.duration)
        st = c.subtitle
        font_path = t.font_path(st.font)
        self._times = [self._reveal_times(l) for l in self.lines]
        self._layouts: dict[tuple[int, bool], SubtitleLayout] = {}
        self._font_path = font_path
        for i, l in enumerate(self.lines):
            for intro in (True, False):
                lay = self._layout(i, intro)
                if lay.overflow:
                    self.warnings.append(f"자막이 {st.max_lines}줄을 넘어 최소 크기로도 다 들어가지 않습니다: {l.text}")
                    break
        self.warnings = list(dict.fromkeys(self.warnings))
        self._stats: dict[int, SubtitleLayout] = {}
        if c.stat_line:
            for s in self.scenes[1:]:
                if s.item and s.item.stat_line.strip():
                    self._stats[s.index] = layout_stat(s.item.stat_line, c.stat_line, t.font_path(c.stat_line.font))
                    if self._stats[s.index].overflow:
                        self.warnings.append(f"빨간 기록 줄이 {c.stat_line.max_lines}줄을 넘습니다: {s.item.stat_line}")
        # 키워드 터뜨리기 (강한 구간에서, 자막 글자 시각 기준)
        self.bursts: list[tuple[float, str]] = []
        self._burst_imgs: dict[int, Image.Image] = {}
        if self.v.burst != "off":
            strong = self.beatmap.strong if self.beatmap is not None else []
            blocked = [(sc.start, sc.end) for sc in self.scenes if sc.kind in ("intro", "chart", "bonus")]
            on_cards = [x for i in self.project.items
                        for x in (i.value, i.name, f"{i.shown_rank}위", i.description or "")] + \
                       ([self.project.bonus.value, self.project.bonus.name] if self.project.bonus else [])
            self.bursts = fx.pick_bursts(self.lines, lambda i: char_times(self.lines[i]), [], strong, blocked,
                                         on_cards=on_cards)
        self._cache_key = None
        self._cache_frame: Image.Image | None = None

    def _bonus_layer(self) -> MovingLayer:
        c, t, bo = self.t.card, self.t, self.project.bonus
        kind = (self.v.transition if self.clean  # 클린: 보너스도 카드와 같은 전환 (영상당 1종류)
                else {"stamp": "punch", "slide": "slide", "pop": "pop"}.get(self.v.ending, "punch"))
        motion, dx, _ = transition_motion(kind, c.card_motion)
        art = self.project_dir / bo.image if bo.image and (self.project_dir / bo.image).is_file() else None
        self.bonus_off: MovingLayer | None = None
        if self.clean:  # 클린: 같은 카드 + 회색 물음표 + '번외' (도장·기울기·깜빡임 없음)
            from .card_clean import clean_bonus
            lay: dict = {}
            card = clean_bonus(self.v.look, rank=bo.rank, name=bo.name, team=bo.team, value=bo.value, year=bo.year,
                               text=bo.text, kind=bo.kind, accent=self.stat_color or "#F26A00", layers=lay,
                               main=self.main_color)
            self._bonus_text = lay["text"].getchannel("A")  # 말풍선이 피할 글자 자리
            return MovingLayer(card, c.card_box, motion, t.fps, c.content_top, self.canvas, dx=dx)
        if self.v.design:  # 영상 카드와 같은 디자인 + 반전 ('6위?!', '근데…', 기울기, 조명 꺼짐)
            from .card_design import compose_bonus
            kw = dict(rank=bo.rank, name=bo.name, team=bo.team, value=bo.value, year=bo.year, text=bo.text, art=art)
            on = compose_bonus(self.v.design, bo.kind, **kw)
            self.bonus_off = MovingLayer(compose_bonus(self.v.design, bo.kind, **kw, off=True), c.card_box, motion,
                                         t.fps, c.content_top, self.canvas, dx=dx)
            return MovingLayer(on, c.card_box, motion, t.fps, c.content_top, self.canvas, dx=dx)
        if art is not None:
            src: Path | Image.Image = art
        else:
            src = fx.bonus_card(bo.kind, rank=bo.rank, name=bo.name, team=bo.team, value=bo.value, year=bo.year,
                                text=bo.text, accent=self.accent_rank)
        return MovingLayer(src, c.card_box, motion, t.fps, c.content_top, self.canvas, dx=dx)

    def _count_layer(self, idx: int, text: str) -> MovingLayer:
        key = (idx, text)
        if key not in self._count_layers:
            from .card_design import record_text_for
            m = self._counts[idx]
            card = m["base_img"].copy()
            rec = record_text_for(m.get("design"), text, m["size"], m.get("team", ""), m.get("rank", 5), m.get("top"))
            x = m["right"] - rec.width if m.get("align") == "right" else m["x"]  # 오른쪽 정렬 기록 (클린 A)
            card.alpha_composite(rec, (x, m["y"] + m["h"] - rec.height))
            if len(self._count_layers) > 40:
                self._count_layers.clear()
            self._count_layers[key] = self.layers[idx].variant(card)
        return self._count_layers[key]

    def _reveal_times(self, line: LyricLine) -> list[float]:
        """자막 등장 방식별 글자 시각: typing=글자마다, word=단어 첫 글자 시각에 단어 전체, line=줄 시작에 전부."""
        times = char_times(line)
        if self.v.subtitle == "line":
            return [line.start] * len(times)
        if self.v.subtitle == "word":
            out, i, text = [], 0, line.text
            while i < len(text):
                j = i
                while j < len(text) and not text[j].isspace():
                    j += 1
                j = j + 1 if j < len(text) else j  # 단어 뒤 공백 포함
                first = times[i]
                out += [first] * (j - i)
                i = j
            return out
        return times

    def _layout(self, i: int, intro: bool) -> SubtitleLayout:
        key = (i, intro)
        if key not in self._layouts:
            st = self.t.card.subtitle
            # 인트로 전용 자막 위치는 로고 인트로용. 후킹으로 시작하면 늘 같은 자막 자리(2줄)를 쓴다
            cy = (st.intro_center_y if intro and st.intro_center_y and not self.hook_len else st.y + st.h / 2)
            self._layouts[key] = layout_subtitle(self.lines[i].text, st, self._font_path, cy,
                                                 st.intro_align if intro else "center")
        return self._layouts[key]

    def scene_at(self, sec: float) -> Scene:
        for s in self.scenes:
            if sec < s.end - 1e-9:
                return s
        return self.scenes[-1]

    def state(self, frame_no: int) -> tuple:
        """프레임 모양을 결정하는 값. 같으면 이전 프레임을 그대로 쓴다."""
        sec = frame_no / self.t.fps
        fps = self.t.fps
        sc = self.scene_at(sec)
        m = self.layers[sc.index].motion
        since = frame_no - round(sc.start * fps)
        hook = None
        anim: object
        if sc.kind == "intro":  # 후킹 문구 → 제목 카드 → 로고 움직임
            pre = 0
            if self.hook_len:
                hl = round(self.hook_len * fps)
                if since < hl:
                    hook = since
                pre += hl
            if self.title_card is not None:
                pre += round(TITLE_CARD * fps)
            since = since - pre if since >= pre else -1
        if hook is not None:
            anim = "hook"
        elif since < 0:
            anim = "title"
        else:
            anim = since if since / fps < m.length + m.gap_frames / fps else None
        card_scene = sc.kind in ("rank", "bonus", "outro")
        flash = since if card_scene and self.flash and 0 <= since < round(self.flash * fps) else None
        top = sc.kind == "rank" and sc.item.shown_rank == 1
        if top and 0 <= since < round(TOP_FLASH * fps) and not self.clean:  # 1위: 번쩍 (클린은 없음)
            flash = since if flash is None else flash
        # 그림 움직임: 등장 효과가 끝난 뒤부터 장면 끝까지 프레임마다 (1위는 흔들림도). 클린은 없음
        idle = None
        if card_scene and (self.v.motion != "none" or top) and not self.clean:
            idle = since
        if self.clean and card_scene:  # 클린: 카드는 나타날 때·사라질 때만 페이드 + 살짝 미끄러지기
            n = max(1, round(INOUT_LEN * fps))
            to_end = round(sc.end * fps) - frame_no
            anim = ("in", since) if 0 <= since < n else (("out", to_end) if 0 < to_end <= n and
                                                        sc.kind not in ("bonus", "outro") else None)
        # 비트 펄스: 카드가 다 들어온 뒤
        pulse = None
        if card_scene and self.beats is not None and anim is None:
            pulse = fx.pulse_state(self.beats, sec, fps, self.v.beat == "pulse_flash")
        # 기록 카운트업
        count = None
        if sc.kind == "bonus" and getattr(self, "bonus_off", None) is not None and since >= 0:
            count = "off" if fx.bonus_flicker(since / fps) else None   # 디자인별 반전: 꺼졌다 켜짐
        if sc.index in self._counts and since >= 0:
            frac = fx.count_frac(since / fps)
            count = fx.count_text(self._counts[sc.index]["record"], frac) if frac < 1 else None
        # 진행 표시
        prog = None
        if self.v.progress != "none":
            ranks = [s for s in self.scenes if s.kind == "rank"]
            done = sum(1 for s in ranks if s.start <= sc.start) if sc.kind != "intro" else 0
            pop_n = round(0.25 * fps)
            pop = round(1 - since / pop_n, 2) if sc.kind == "rank" and 0 <= since < pop_n else 0.0
            prog = (done, pop)
        rise = None
        line_i, shown = None, 0
        for i, l in enumerate(self.lines):  # 다음 가사가 시작되면 교체
            if l.start <= sec + 1e-9:
                line_i = i
            else:
                break
        if line_i is not None:
            shown = sum(1 for x in self._times[line_i] if x <= sec + 1e-9)
            if self.v.subtitle == "line" and shown:
                k = frame_no - round(self.lines[line_i].start * fps)
                rise = k if k < round(LINE_RISE * fps) else None
        stat = 0
        if sc.index in self._stats:
            stat = stat_visible(sc.item.stat_line, self.t.card.stat_line, sec - sc.start)
        # 데이터 그래픽 (막대가 다 자라면 고정)
        chart = None
        if sc.kind == "chart":
            chart = min(since, round((fx.CHART_GROW + 0.6) * fps))
        # 2.5D: 등장 효과가 끝난 뒤 층마다 따로 움직임
        depth = None
        if sc.index in self._depth and anim is None and since >= 0 and sc.kind == "rank":
            entry = round((m.length + m.gap_frames / fps) * fps)
            depth = max(0, since - entry)
        # 키워드 터뜨리기
        burst = None
        for bi, (bt, _) in enumerate(self.bursts):
            if bt <= sec < bt + fx.BURST_LEN:
                burst = (bi, round((sec - bt) * fps))
        # 마스코트
        mas = None
        if self.mascot is not None:
            ang = (round(msc.bob_angle(self.beatmap, sec), 1)
                   if self.mascot.bobbing(sec) and not self.clean else 0.0)  # 클린: 까딱 없이 표정만
            bub = self.mascot.bubble_at(sec)
            mas = (self.mascot.expr_at(sec), ang if abs(ang) >= 0.3 else 0.0,
                   None if bub is None else (bub[0], min(round(bub[1] * fps), 4)))
        return (sc.index, anim, line_i, shown, sc.kind == "intro", stat, flash, rise, idle, hook, pulse, count,
                prog, chart, depth, burst, mas)

    def frame(self, frame_no: int) -> Image.Image:
        key = self.state(frame_no)
        if key == self._cache_key and self._cache_frame is not None:
            return self._cache_frame
        (idx, anim, line_i, shown, intro, stat, flash, rise, idle, hook, pulse, count, prog, chart, depth, burst,
         mas) = key
        fps = self.t.fps
        img = self.base.convert("RGBA")
        sc = self.scenes[idx]
        ps, pdx, pflash = fx.pulse_transform(pulse, fps)
        if anim == "hook":
            self._draw_hook(img, hook / fps)
        elif anim == "title":
            img.alpha_composite(self.title_card)
        elif chart is not None:
            c = self.t.card
            fx.draw_chart(
                img,
                self.chart_bars,
                chart / fps,
                base=self.chart_base,
                base_label=self.project.gauge_base,
                years=self.v.datagfx == "bars_years",
                hot=self.stat_color or "#EB1B1B",
                accent=self.accent_rank,
                font_path=self._label_font,
                top_y=c.subtitle.y + c.subtitle.h + 40,
                bottom_y=c.footer.button.y - 30,
                clean=self.clean,
                main=self.main_color,
                **self._chart_bounds(),
            )
        else:
            transform = None
            if idle is not None:
                layer = self.layers[idx]
                top = sc.kind == "rank" and sc.item.shown_rank == 1
                entry = layer.motion.length + layer.motion.gap_frames / fps
                t_idle = idle / fps - entry
                ks, dx, dy, ang = idle_transform(self.v.motion, t_idle, TOP_GAIN if top else 1.0)
                if top:
                    dx += top_shake(idle / fps)
                transform = (ks, dx, dy, ang)
                if anim is not None and t_idle < 0:  # 등장 중에는 1위 흔들림만
                    transform = (1.0, top_shake(idle / fps) if top else 0.0, 0.0, 0.0)
            if pulse is not None:
                ks, dx, dy, ang = transform or (1.0, 0.0, 0.0, 0.0)
                transform = (ks * ps, dx + pdx, dy, ang)
            if count == "off":
                layer = self.bonus_off
            else:
                layer = self._count_layer(idx, count) if count is not None else self.layers[idx]
            if self.clean:  # 클린: 제자리 카드 + 인·아웃
                self._draw_inout(img, layer, anim)
            elif depth is not None:  # 2.5D: 층을 따로 밀고 당겨서 합친 카드
                self._draw_fitted(img, self.layers[idx], self._depth_card(idx, depth / fps, count), transform)
            else:
                layer.draw(img, None if anim is None else anim / fps, transform)
        if flash is not None or pflash:  # 흰 섬광이 사라짐 (구분선 아래만). 1위 번쩍은 TOP_FLASH 초
            a = 0.0
            if flash is not None:
                span = round((self.flash or TOP_FLASH) * fps)
                a = 1 - flash / max(1, span)
            a = max(a, pflash)
            white = Image.new("RGBA", self.canvas, (255, 255, 255, 0))
            white.paste((255, 255, 255, round(255 * a)), (0, self.t.card.content_top, self.canvas[0], self.canvas[1]))
            img.alpha_composite(white)
        img.alpha_composite(self.head)
        if mas is not None:
            self._draw_mascot(img, mas)
        if prog is not None:
            p = self.t.card.pill
            fx.draw_progress(img, self.v.progress, prog[0], sum(1 for s in self.scenes if s.kind == "rank"),
                             right=self.canvas[0] - 42, cy=round(p.y + p.h / 2), hot=self.stat_color or "#EB1B1B",
                             font_path=self._label_font, pop=prog[1])
        if stat:
            ck = ("stat", idx, stat)
            if ck not in self._sub_cache:
                self._sub_cache[ck] = render_stat(self._stats[idx], stat, self.t.card.stat_line, self.canvas,
                                                  self.stat_color)
            img.alpha_composite(self._sub_cache[ck])
        if burst is not None:  # 키워드는 자막 자리에 크게 (카드와 안 겹치게), 그동안 자막은 쉼
            self._draw_burst(img, burst[0], burst[1] / fps)
        elif line_i is not None and shown:
            ck = ("sub", line_i, shown, intro, rise)  # 그림이 프레임마다 움직여도 자막 레이어는 다시 쓰기
            if ck not in self._sub_cache:
                if len(self._sub_cache) > 64:
                    self._sub_cache.clear()
                p = 1.0 if rise is None else (rise + 1) / round(LINE_RISE * fps)
                self._sub_cache[ck] = render_subtitle(self._layout(line_i, intro), shown, self.t.card.subtitle,
                                                      self.canvas, alpha=p, dy=(1 - p) * 40)
            img.alpha_composite(self._sub_cache[ck])
        out = img.convert("RGB")
        self._cache_key, self._cache_frame = key, out
        return out

    # ---------------------------------------------------------- 2.5D·키워드·마스코트

    def _depth_card(self, idx: int, t: float, count: str | None) -> Image.Image:
        """층(배경·캐리커처·앞 효과·글자)을 패럴랙스로 움직여 카드 크기 그림으로 합친다."""
        dp = self._depth[idx]
        size = next(iter(dp["layers"].values())).size
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        moves = fx.parallax(self.v.depth, t)
        k = dp["scale"]
        for n in fx.LAYER_NAMES:
            im = dp["layers"].get(n)
            if im is None:
                continue
            if n == "text":  # 기록 글자 (카운트업 중이면 그 숫자)
                im = im.copy()
                m = dp["meta"]
                key = ("deprec", idx, count or m["record"])
                rec = self._sub_cache.get(key)
                if rec is None:
                    from .card_design import record_text_for
                    full = record_text_for(m.get("design"), count or m["record"], m["size"], m.get("team", ""),
                                           m.get("rank", 5), m.get("top"))
                    rec = full.resize((max(1, round(full.width * k)), max(1, round(full.height * k))), Image.LANCZOS)
                    self._sub_cache[key] = rec
                x = m["right"] * k - rec.width if m.get("align") == "right" else m["x"] * k
                im.alpha_composite(rec, (round(x), round((m["y"] + m["h"]) * k) - rec.height))
            sc_, dx = moves[n]
            if abs(sc_ - 1) > 1e-3:
                w, h = round(size[0] * sc_), round(size[1] * sc_)
                im = im.resize((w, h), Image.BILINEAR)
                ox, oy = (size[0] - w) // 2, (size[1] - h) // 2
            else:
                ox = oy = 0
            MovingLayer._paste(out, im, ox + round(dx * k), oy)
        return out

    def _draw_inout(self, img: Image.Image, layer: MovingLayer, anim) -> None:
        """클린 카드: 제자리(배율 1)에 두고, 나타날 때·사라질 때만 0.3초 페이드 + 살짝 미끄러지기."""
        key = id(layer)
        rest = self._rest.get(key)
        if rest is None:
            rest = layer._place(1.0)[0]
            self._rest[key] = rest
        n = max(1, round(INOUT_LEN * self.t.fps))
        p, dx, dy, s_ = 1.0, 0.0, 0.0, 1.0
        if anim is not None:
            phase, k = anim
            x = min(1.0, (k + (1 if phase == "in" else 0)) / n)
            e = 1 - (1 - x) ** 3  # 끝에서 느려짐
            p = e
            gap = 1 - e
            sign = 1 if phase == "in" else -1
            if self.v.inout == "fade_side":
                dx = 70 * gap * sign
            elif self.v.inout == "fade_zoom":
                s_ = 1 - 0.05 * gap
            else:
                dy = 50 * gap * sign
        im = rest
        if abs(s_ - 1) > 1e-3:
            im = rest.resize((max(1, round(rest.width * s_)), max(1, round(rest.height * s_))), Image.BICUBIC)
        if p < 1:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: round(v * p)))
        lay = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        MovingLayer._paste(lay, im, round(layer.cx - im.width / 2 + dx), round(layer.cy - im.height / 2 + dy))
        lay.paste((0, 0, 0, 0), (0, 0, self.canvas[0], self.t.card.content_top))
        img.alpha_composite(lay)

    def _draw_fitted(self, img: Image.Image, layer: MovingLayer, small: Image.Image,
                     transform: tuple[float, float, float, float] | None) -> None:
        """카드 크기로 이미 줄인 그림을 그 카드 자리에 (그림 움직임·펄스 적용). 구분선 위로는 안 그림."""
        ks, dx, dy, ang = transform or (1.0, 0.0, 0.0, 0.0)
        im = small
        if abs(ks - 1) > 1e-3:
            im = im.resize((max(1, round(im.width * ks)), max(1, round(im.height * ks))), Image.BICUBIC)
        if abs(ang) > 0.05:
            im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
        lay = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        MovingLayer._paste(lay, im, round(layer.cx - im.width / 2 + dx), round(layer.cy - im.height / 2 + dy))
        lay.paste((0, 0, 0, 0), (0, 0, self.canvas[0], self.t.card.content_top))
        img.alpha_composite(lay)

    def _draw_burst(self, img: Image.Image, i: int, t: float) -> None:
        if i not in self._burst_imgs:
            word, st_ = self.bursts[i][1], self.t.card.subtitle
            self._burst_imgs[i] = (fx.clean_burst_image(word, self.stat_color or "#F26A00", st_.w + 160, st_.h + 40,
                                                         main=self.main_color)
                                   if self.clean else
                                   fx.burst_image(word, self.accent_rank, st_.w + 160, max_h=st_.h + 40))
        src = self._burst_imgs[i]
        s, ang, alpha = fx.burst_frame("zoom" if self.clean else self.v.burst, t)
        im = src.resize((max(1, round(src.width * s)), max(1, round(src.height * s))), Image.BICUBIC)
        if ang:
            im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
        if alpha < 1:
            im.putalpha(im.getchannel("A").point(lambda v: round(v * alpha)))
        st_ = self.t.card.subtitle
        cy = st_.y + st_.h / 2
        MovingLayer._paste(img, im, round(self.canvas[0] / 2 - im.width / 2), round(cy - im.height / 2))

    def _chart_bounds(self) -> dict:
        """그래프 좌우 끝: 마스코트 자리는 비워 둔다 (말풍선과 겹치지 않게)."""
        label_w = 0  # 기준선 글씨는 그래프 오른쪽 바깥에 (1위 막대 숫자와 안 겹치게)
        if self.chart_base and self.project.gauge_base:
            label_w = round(_font(str(self._label_font), 32).getlength(self.project.gauge_base)) + 30
        right = self.canvas[0] - 50 - label_w
        if self.mascot is None:
            return {"left": 90, "right": right}
        x, _ = self._mascot_box()
        if self.v.mascot == "left":
            return {"left": x + msc.SIZE + 40, "right": right}
        return {"left": 90, "right": min(right, x - 40)}

    def _mascot_box(self) -> tuple[int, int]:
        """마스코트 왼쪽 위 좌표. 오른쪽(기본)/왼쪽 아래, 자막(위쪽)·카드와 안 겹치게."""
        c = self.t.card
        bottom = c.footer.button.y + c.footer.button.h
        x = self.canvas[0] - 24 - msc.SIZE if self.v.mascot != "left" else 24
        return x, bottom - msc.SIZE

    def _draw_mascot(self, img: Image.Image, mas: tuple) -> None:
        expr, ang, bub = mas
        x, y = self._mascot_box()
        im = msc.expression_image(expr, folder=self.mascot_dir)
        if ang:  # 발끝을 축으로 까딱
            big = Image.new("RGBA", (im.width * 2, im.height * 2), (0, 0, 0, 0))
            big.alpha_composite(im, (im.width // 2, 0))
            big = big.rotate(ang, resample=Image.BICUBIC, center=(im.width, im.height))
            img.alpha_composite(big.crop((im.width // 2, 0, im.width // 2 + im.width, im.height)), (x, y))
        else:
            img.alpha_composite(im, (x, y))
        if bub is not None:
            i, k = bub
            text = self.mascot.bubbles[i][2]
            key = ("bubble", i)
            if key not in self._sub_cache:
                self._sub_cache[key] = msc.bubble_image(text, self._label_font, tail_right=self.v.mascot != "left")
            full = self._sub_cache[key]
            bx, by = self._bubble_pos(i, full)
            b = full
            s = 0.6 + 0.4 * min(1.0, k / 4)
            if s < 1:  # 꼬리 쪽(아래)을 기준으로 커짐
                b = full.resize((max(1, round(full.width * s)), max(1, round(full.height * s))), Image.BICUBIC)
                bx += (full.width - b.width) if self.v.mascot != "left" else 0
                by += full.height - b.height
            img.alpha_composite(b, (max(0, bx), max(0, by)))

    def _text_mask(self, idx: int) -> Image.Image | None:
        """그 장면 화면에서 글자가 있는 자리 (카드의 순위·기록·이름 글자, 그래프 글자). 말풍선이 피할 곳."""
        if idx in self._masks:
            return self._masks[idx]
        sc = self.scenes[idx]
        mask = None
        if sc.kind == "chart":
            lay = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
            c = self.t.card
            fx.draw_chart(
                lay,
                self.chart_bars,
                9.0,
                base=self.chart_base,
                base_label=self.project.gauge_base,
                years=self.v.datagfx == "bars_years",
                hot=self.stat_color or "#EB1B1B",
                accent=self.accent_rank,
                font_path=self._label_font,
                top_y=c.subtitle.y + c.subtitle.h + 40,
                bottom_y=c.footer.button.y - 30,
                clean=self.clean,
                main=self.main_color,
                **self._chart_bounds(),
            )
            mask = lay.getchannel("A")
        elif sc.kind in ("rank", "bonus"):
            layer = self.layers[idx]
            src = None
            meta_p = (self.project_dir / sc.item.image).with_suffix(".json") if sc.kind == "rank" else None
            if meta_p is not None and meta_p.exists():
                import json as _json
                lp = (_json.loads(meta_p.read_text(encoding="utf-8")).get("layers") or {}).get("text")
                if lp and (self.project_dir / lp).is_file():
                    src = _load_rgba(str(self.project_dir / lp)).getchannel("A")
                    meta = _json.loads(meta_p.read_text(encoding="utf-8"))  # 글자 층에는 기록 글자가 빠져 있음
                    if meta.get("record"):
                        src = src.copy()
                        x1 = meta.get("right", meta["x"] + meta["size"] * 4)
                        ImageDraw.Draw(src).rectangle([meta["x"], meta["y"], x1, meta["y"] + meta["h"]], fill=255)
            if sc.kind == "bonus" and getattr(self, "_bonus_text", None) is not None:
                src = self._bonus_text
            if src is None:  # 글자 층이 없으면 카드 아래쪽 45% 를 글자 자리로 봄
                a = layer.src.getchannel("A")
                src = Image.new("L", a.size, 0)
                src.paste(a.crop((0, int(a.height * 0.55), a.width, a.height)), (0, int(a.height * 0.55)))
            small = src.resize((max(1, round(layer.w)), max(1, round(layer.h))))
            mask = Image.new("L", self.canvas, 0)
            mask.paste(small, (round(layer.cx - small.width / 2), round(layer.cy - small.height / 2)))
        self._masks[idx] = mask
        return mask

    def _bubble_pos(self, i: int, b: Image.Image) -> tuple[int, int]:
        """말풍선 자리: 마스코트 바로 위가 기본. 카드·그래프 글자와 겹치면 위로 올린다 (가장 덜 겹치는 곳)."""
        a, _, _ = self.mascot.bubbles[i]
        idx = self.scene_at(a + 1e-3).index
        key = (i, idx)
        if key in self._bubble_xy:
            return self._bubble_xy[key]
        x, y = self._mascot_box()
        bx = x + msc.SIZE - b.width + 10 if self.v.mascot != "left" else x - 10
        bx = min(max(0, bx), self.canvas[0] - b.width)
        base_y = y - b.height + 20
        top = self.t.card.subtitle.y + self.t.card.subtitle.h
        mask = self._text_mask(idx)
        best = (base_y, float("inf"))
        if mask is not None:
            import numpy as _np
            arr = _np.asarray(mask)
            for dy in range(0, base_y - top, 30):
                yy = base_y - dy
                hit = int((arr[yy:yy + b.height, bx:bx + b.width] > 40).sum())
                if hit < best[1]:
                    best = (yy, hit)
                if hit < 30:
                    break
        self._bubble_xy[key] = (bx, best[0])
        return self._bubble_xy[key]

    def _hook_slots(self, text_h: int) -> tuple[float, tuple[int, int, int, int]]:
        """후킹 그림이 있을 때 배치: 위에서부터 가사 자막(2줄 자리) → 후킹 문구 → 그림. 서로 겹치지 않게.
        (문구 가운데 y, 그림 상자 x0, y0, x1, y1)."""
        c = self.t.card
        st_ = c.subtitle
        top = st_.y + st_.h + 56                      # 자막 자리(2줄, 그림자 포함) 아래로 여유를 두고
        text_cy = top + text_h / 2
        floor = min(c.footer.button.y, c.footer.handle.y) - 22   # '@야구한장 구독' 바로 위까지 꽉
        w = round(self.canvas[0] * 0.70)              # 화면 너비 70%
        x0 = (self.canvas[0] - w) // 2
        if self.mascot is not None:                   # 한장이와 겹치지 않게 옆으로 비켜 섬
            mx, _ = self._mascot_box()
            if self.v.mascot == "left":
                x0 = max(x0, mx + msc.SIZE + 12)
            else:
                x0 = min(x0, mx - 12 - w)
        return text_cy, (x0, round(top + text_h + 24), x0 + w, round(floor))

    def _draw_hook(self, img: Image.Image, t: float) -> None:
        """후킹 문구: 구분선 아래 가운데에 크게. 섬광은 '쾅' 순간. 클린은 굵은 글씨 + 강조색 밑줄 (흔들림 없음).
        후킹 그림이 있으면 위에서부터 자막 → 문구 → 그림 순서로 겹치지 않게 (그림은 남은 자리에 맞춰 작게)."""
        src = self._hook_src()
        if src is None:
            self._draw_hook_text(img, t, with_image=False)
            return
        if self.t.card.scene_style == "zip":  # 하늘 배지형: 후킹 그림만 크게 (자막 아래 ~ 구독 버튼 위)
            self._draw_zip_hook(img, src, t)
            return
        text_h = self._hook_text_height()
        _, (x0, y0, x1, y1) = self._hook_slots(text_h)
        key = ("hookimg", x1 - x0, y1 - y0)
        card = self._sub_cache.get(key)
        if card is None:
            card = self._rounded_card(src, (x1 - x0, y1 - y0))
            self._sub_cache[key] = card
        k = min(1.0, t / 0.35)
        im = card
        if k < 1:
            im = card.copy()
            im.putalpha(im.getchannel("A").point(lambda v: round(v * k)))
        dy = round((1 - (1 - (1 - k) ** 3)) * 30)
        MovingLayer._paste(img, im, round((x0 + x1) / 2 - im.width / 2), round((y0 + y1) / 2 - im.height / 2) + dy)
        self._draw_hook_text(img, t, with_image=True)

    def _draw_zip_hook(self, img: Image.Image, src: Image.Image, t: float) -> None:
        c = self.t.card
        x0, x1 = 30, self.canvas[0] - 30
        y0 = c.subtitle.y + c.subtitle.h + 20
        y1 = min(c.footer.button.y, c.footer.handle.y) - 20
        key = ("ziphook", x1 - x0, y1 - y0)
        im = self._sub_cache.get(key)
        if im is None:
            from .bg_remove import has_transparency
            if has_transparency(src):  # 투명 배경 스티커 그림: 비율 유지해 자리에 꽉
                s_ = min((x1 - x0) / src.width, (y1 - y0) / src.height)
                im = src.resize((max(1, round(src.width * s_)), max(1, round(src.height * s_))), Image.LANCZOS)
            else:  # 배경 있는 그림: 둥근 모서리 카드로 꽉 채움
                im = self._rounded_card(src, (x1 - x0, y1 - y0))
            self._sub_cache[key] = im
        k = min(1.0, t / 0.35)
        if k < 1:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: round(v * k)))
        dy = round((1 - (1 - (1 - k) ** 3)) * 30)
        MovingLayer._paste(img, im, round((x0 + x1) / 2 - im.width / 2), round((y0 + y1) / 2 - im.height / 2) + dy)

    def _hook_src(self) -> Image.Image | None:
        if not self.project.hook_image:
            return None
        if getattr(self, "_hook_src_img", None) is None:
            p = self.project_dir / self.project.hook_image
            if not p.is_file():
                self.warnings.append(f"후킹 그림 파일이 없습니다: {self.project.hook_image}")
                self._hook_src_img = False
            else:
                with Image.open(p) as im:
                    self._hook_src_img = im.convert("RGBA")
        return self._hook_src_img or None

    def _hook_text_height(self) -> int:
        if self.clean:
            return fx.clean_hook_image(self.project.hook, self.stat_color or "#F26A00", self.canvas[0],
                                       size=self._hook_size()).height
        return self.hook_img.height

    def _hook_size(self) -> int:
        return 130 if self.project.hook_image else 195  # 그림이 있으면 문구를 줄여 그림 자리를 남김

    @staticmethod
    def _rounded_card(src: Image.Image, box: tuple[int, int]) -> Image.Image:
        """후킹 그림을 자리에 꽉 채움 (넘치는 위·아래는 잘라냄, 위쪽 35% 를 중심으로)."""
        from PIL import ImageOps
        art = ImageOps.fit(src, (max(1, box[0] - 40), max(1, box[1] - 30)), Image.LANCZOS, centering=(0.5, 0.35))
        m = Image.new("L", art.size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, art.width - 1, art.height - 1], radius=32, fill=255)
        art.putalpha(ImageChops.multiply(art.getchannel("A"), m))
        pad = 24
        out = Image.new("RGBA", (art.width + 2 * pad, art.height + 2 * pad), (0, 0, 0, 0))
        sm = Image.new("L", out.size, 0)
        sm.paste(m.point(lambda v: round(v * 0.18)), (pad, pad + 8))
        sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
        sh.putalpha(sm.filter(ImageFilter.GaussianBlur(12)))
        out.alpha_composite(sh)
        out.alpha_composite(art, (pad, pad))
        return out

    def _draw_hook_text(self, img: Image.Image, t: float, *, with_image: bool) -> None:
        c = self.t.card
        if self.clean:
            step = round(fx.hook_wipe(t) * 12)  # 밑줄 진행 (12단계로 캐시)
            key = ("hook", step, self._hook_size())
            if key not in self._sub_cache:
                self._sub_cache[key] = fx.clean_hook_image(self.project.hook, self.stat_color or "#F26A00",
                                                           self.canvas[0], progress=step / 12, size=self._hook_size())
            h = self._sub_cache[key]
            k = min(1.0, t / 0.3)
            a = fx.hook_alpha(t, self.hook_len) * k
            im = h if a >= 1 else h.copy()
            if a < 1:
                im.putalpha(im.getchannel("A").point(lambda v: round(v * a)))
            rise = (1 - (1 - (1 - k) ** 3)) * 30
            if with_image:  # 자막 자리 바로 아래 (그림 위)
                cy = self._hook_slots(h.height)[0] + rise
            else:
                cy = (c.content_top + c.footer.button.y) / 2 - 40 + rise
            MovingLayer._paste(img, im, round(self.canvas[0] / 2 - im.width / 2), round(cy - im.height / 2))
            return
        h = self.hook_img
        cx = self.canvas[0] / 2
        cy = (c.content_top + c.footer.button.y) / 2 + 40
        if with_image:  # 자막 자리 바로 아래 (그림 위)
            cy = self._hook_slots(h.height)[0]
        if self.v.hook == "pop_words":  # 단어씩: 왼쪽부터 가려 둔 부분을 연다
            n = max(1, len(self.project.hook.split()))
            k = min(n, int(t / 0.2) + 1)
            src = h.copy()
            if k < n:
                mask = Image.new("L", src.size, 255)
                ImageDraw.Draw(mask).rectangle([round(src.width * (0.12 + 0.76 * k / n)), 0, src.width, src.height],
                                               fill=0)
                src.putalpha(Image.composite(src.getchannel("A"), Image.new("L", src.size, 0), mask))
            s, dx, dy, fl = fx.hook_frame("pop_words", t - (k - 1) * 0.2)
        else:
            src = h
            s, dx, dy, fl = fx.hook_frame(self.v.hook, t)
        w, hh = max(1, round(src.width * s)), max(1, round(src.height * s))
        im = src.resize((w, hh), Image.BICUBIC)
        alpha = fx.hook_alpha(t, self.hook_len)
        if alpha < 1:
            im.putalpha(im.getchannel("A").point(lambda v: round(v * alpha)))
        layer = Image.new("RGBA", self.canvas, (0, 0, 0, 0))
        MovingLayer._paste(layer, im, round(cx - w / 2 + dx), round(cy - hh / 2 + dy))
        layer.paste((0, 0, 0, 0), (0, 0, self.canvas[0], c.content_top))
        img.alpha_composite(layer)
        if fl > 0.01:
            white = Image.new("RGBA", self.canvas, (255, 255, 255, 0))
            white.paste((255, 255, 255, round(255 * fl)), (0, c.content_top, self.canvas[0], self.canvas[1]))
            img.alpha_composite(white)

    def frame_at(self, sec: float) -> Image.Image:
        return self.frame(round(sec * self.t.fps))


def make_renderer(project: Project, project_dir: Path, t: Template, lines: list[LyricLine]) -> CardRenderer:
    if t.card.auto_cards:  # 하늘 배지형: 항목으로 클린 카드 자동 합성
        from .info_cards import prepare_cards
        project = prepare_cards(project, project_dir, t)
    if (t.card.hook_from_title or project.hook_image) and not project.hook.strip():  # 로고 없이 후킹(제목 첫 줄) → 5위
        project = project.model_copy(update={"hook": project.title.split("\n")[0].strip()})
    scenes = card_timeline(project, t, lines)
    return CardRenderer(project, project_dir, t, scenes, lines)


# ---------------------------------------------------------------- 미리보기·인코딩

def build_previews(r: CardRenderer, out_dir: Path) -> SceneBuildResult:
    """장면마다 움직임이 끝난 상태 + 그 장면에서 처음 다 나온 자막의 PNG (Streamlit·CapCut 키트용)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for s in r.scenes:
        sec = s.start + min(r.layers[s.index].motion.length + 0.2, s.duration * 0.9)
        for l in r.lines:  # 장면 안에서 시작한 첫 가사가 다 나온 시각
            if s.start <= l.start < s.end:
                sec = max(sec, min(char_times(l)[-1] + 0.05, s.end - 1 / r.t.fps))
                break
        if s.index in r._stats and r.t.card.stat_line:  # 빨간 줄이 다 나온 뒤
            sl = r.t.card.stat_line
            sec = max(sec, min(s.start + sl.delay + len(s.item.stat_line) / sl.rate + 0.05, s.end - 1 / r.t.fps))
        path = out_dir / f"scene{s.index:02d}.png"
        r.frame_at(sec).save(path)
        built.append(SceneLayers(scene=s, base=path, preview=path))
    return SceneBuildResult(built, list(r.warnings))


def render_video(r: CardRenderer, output: Path, *, progress: ProgressFn | None = None) -> None:
    p, t = r.project, r.t
    music = (r.project_dir / p.music) if p.music else None
    n_frames = round(p.duration * t.fps)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{t.width}x{t.height}", "-r", str(t.fps), "-i", "-"]
    if music:
        cmd += ["-ss", _f(p.music_start), "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-t", _f(p.duration), "-i", "anullsrc=r=44100:cl=stereo"]
    fade = min(t.effects.music_fade_out, p.duration)
    v = r.v
    sfx_args, sfx_graph, _ = mix_filter(r.cues, v.sfx, 2) if v.sfx != "off" else ([], "", [])
    cmd += sfx_args
    music_chain = (f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,apad,atrim=0:{_f(p.duration)},"
                   "asetpts=PTS-STARTPTS")
    if sfx_graph:  # 효과음은 음악 위에 작게 (sfx.MIX_GAIN)
        audio = (f"{music_chain}[mus];{sfx_graph};[mus][sfx]amix=inputs=2:duration=first:normalize=0:"
                 f"dropout_transition=0,afade=t=out:st={_f(p.duration - fade)}:d={_f(fade)}[aout]")
    else:
        audio = f"{music_chain},afade=t=out:st={_f(p.duration - fade)}:d={_f(fade)}[aout]"
    cmd += ["-filter_complex", f"[0:v]{TO_TV_YUV420}[vout];{audio}",
            "-map", "[vout]", "-map", "[aout]", "-r", str(t.fps), "-t", _f(p.duration),
            *FINAL_VCODEC, *FINAL_ACODEC, "-movflags", "+faststart", str(output)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
        try:
            last_key, data = None, b""
            for i in range(n_frames):
                key = r.state(i)
                if key != last_key:
                    data = r.frame(i).tobytes()
                    last_key = key
                proc.stdin.write(data)
                if progress and i % t.fps == 0:
                    progress(i / n_frames, f"프레임 {i}/{n_frames}")
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            raise FFmpegError(cmd, err.read().decode("utf-8", "replace"))
