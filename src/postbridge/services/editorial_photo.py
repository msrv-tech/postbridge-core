"""Build and deliver illustrated layouts from one editorial candidate."""
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError as ModelError
from sqlalchemy import select

from postbridge.domain.errors import ValidationError
from postbridge.infrastructure.media_storage import managed_media_url, read_media_object
from postbridge.models.domain import AgentTaskOrm, BridgeOrm, ChannelOrm, MediaAssetOrm, PublicationPlanOrm
from postbridge.services.media_assets import store_media_asset
from postbridge.services.postbridge_workspace_content import create_postbridge_content_item, _dump_extra, _load_extra
from postbridge.services.publication_planning import create_plan_and_targets_for_content_item
from postbridge.services.video_publication import fingerprint

PHOTO_STRATEGY = "editorial_photo"
PHOTO_LIMIT = 10_000_000
LAYOUT_INSTRUCTIONS = """Choose one fresh furniture layout topic. Return publishable prose without any numeric
measurements, editorial notes, image prompts or media URLs. Also return layout in centimetres:
room_width, room_depth, door_x (left hinge on the y=0 wall, opens inwards), door_width, passage_end_y
(a straight approach from the door to the desk or working chair), furniture (2 to 12 objects with
kind, x, y, width and depth). Use the room's upper left corner as (0,0). Kinds are desk, chair,
sofa, bed, cabinet and table. Include exactly one desk and one chair in its occupied working
position. Keep the furniture clear of the open doorway and the continuous route. The server draws
both pictures and adds measured values to the text. If the plan cannot be safely described, return
the draft so it can be reviewed."""


class EditorialPhotoPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    bridge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    settings_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    minimum_passage_cm: int = Field(default=70, strict=True, ge=70, le=150)
    language: Literal["ru", "en"] = "ru"


class Furniture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["desk", "chair", "sofa", "bed", "cabinet", "table"]
    x: int = Field(ge=0, le=1200)
    y: int = Field(ge=0, le=1200)
    width: int = Field(ge=25, le=350)
    depth: int = Field(ge=25, le=350)

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.depth


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    room_width: int = Field(ge=180, le=1200)
    room_depth: int = Field(ge=180, le=1200)
    door_x: int = Field(ge=0, le=1150)
    door_width: int = Field(ge=60, le=180)
    passage_end_y: int = Field(ge=90, le=1200)
    furniture: list[Furniture] = Field(min_length=2, max_length=12)

    def passage(self, minimum):
        if self.door_x + self.door_width > self.room_width or not self.door_width <= self.passage_end_y <= self.room_depth:
            raise ValueError("Door or route lies outside the room")
        desks = [item for item in self.furniture if item.kind == "desk"]
        chairs = [item for item in self.furniture if item.kind == "chair"]
        if len(desks) != 1 or len(chairs) != 1:
            raise ValueError("Use one desk and one chair in its working position")
        desk, chair = desks[0], chairs[0]
        if min(chair.width, chair.depth) < 45:
            raise ValueError("Allow at least 45 cm for the working chair footprint")
        x_overlap = min(desk.right, chair.right) - max(desk.x, chair.x)
        y_overlap = min(desk.bottom, chair.bottom) - max(desk.y, chair.y)
        if not (x_overlap >= 15 and -45 <= y_overlap <= 0 or y_overlap >= 15 and -45 <= x_overlap <= 0):
            raise ValueError("The working chair must be adjacent to the desk")
        # Use the widest straight strip through the doorway, constrained at
        # every cross-section along the approach. Reserve space for the open leaf.
        strips = [(self.door_x + 4, self.door_x + self.door_width)]
        for index, item in enumerate(self.furniture):
            if item.right > self.room_width or item.bottom > self.room_depth:
                raise ValueError("Furniture lies outside the room")
            if item.y < self.door_width and item.x < self.door_x + self.door_width and item.right > self.door_x:
                raise ValueError("Furniture obstructs the door swing")
            if any(other.x < item.right and item.x < other.right
                   and other.y < item.bottom and item.y < other.bottom for other in self.furniture[:index]):
                raise ValueError("Furniture footprints overlap")
            if item.y < self.passage_end_y:
                strips = [(start, end) for left, right in strips
                          for start, end in ((left, min(right, item.x)), (max(left, item.right), right))
                          if end > start]
        reachable = []
        for left, right in strips:
            if not chair.y + 15 <= self.passage_end_y <= chair.bottom:
                continue
            gap_left, gap_right = (right, chair.x) if right <= chair.x else (chair.right, left)
            if gap_right - gap_left > 80 or gap_right < gap_left:
                continue
            if not any(item != chair and item.x < gap_right and item.right > gap_left
                       and item.y < self.passage_end_y and item.bottom > self.passage_end_y - 30
                       for item in self.furniture):
                reachable.append((left, right))
        left, right = max(reachable, key=lambda strip: strip[1] - strip[0], default=(0, 0))
        if right - left < minimum:
            raise ValueError(f"Clear passage ends up {right - left} cm wide; at least {minimum} cm is required")
        return left, right


def photo_error(code, message):
    return ValidationError(code=code, message=message)


def selected_bridge(session, tenant_id, channel_id, policy, actor):
    from postbridge.services.bridge_adaptation import resolve_bridge_adaptation_mode
    bridge = session.get(BridgeOrm, policy.bridge_id, populate_existing=True)
    source = session.get(ChannelOrm, channel_id, populate_existing=True)
    target = session.get(ChannelOrm, bridge.target_channel_id, populate_existing=True) if bridge else None
    if (not bridge or bridge.tenant_id != tenant_id or bridge.source_channel_id != channel_id or bridge.saas_user_id != actor
            or bridge.mode != "live_sync" or bridge.status != "active"
            or fingerprint(bridge.settings_json or {}) != policy.settings_hash
            or resolve_bridge_adaptation_mode(bridge.settings_json) != "rule_only"
            or not source or source.platform != "postbridge" or source.kind != "source"
            or source.status != "connected" or source.tenant_id != tenant_id
            or not target or target.platform != "telegram" or target.kind != "destination"
            or target.status != "connected" or target.tenant_id != tenant_id):
        raise photo_error("EDITORIAL_PHOTO_BRIDGE_CHANGED", "Review the selected Telegram bridge and its text settings")
    return target


def render_layout(layout, passage, language, directory):
    from postbridge.services.video_media import _ffmpeg
    scale = min(670 / layout.room_width, 560 / layout.room_depth)
    left, right = passage
    room_w, room_d = layout.room_width, layout.room_depth
    colors = dict(desk="#ca965c", chair="#c96b62", sofa="#95a484", bed="#779aa7", cabinet="#9a7554", table="#dda66c")
    labels = (dict(desk="Стол", chair="Стул отодвинут", sofa="Диван", bed="Кровать", cabinet="Шкаф", table="Столик")
              if language == "ru" else dict(desk="Desk", chair="Chair in use", sofa="Sofa", bed="Bed", cabinet="Cabinet", table="Table"))

    def rectangle(x, y, w, h, color, stroke="#25322d"):
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" stroke="{stroke}" stroke-width="2"/>'

    def label(x, y, value):
        return f'<text x="{x}" y="{y}" font-family="DejaVu Sans" font-size="16" fill="#273832">{value}</text>'

    def polygon(points, color):
        coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        return f'<polygon points="{coordinates}" fill="{color}" stroke="#25322d" stroke-width="1.6"/>'

    def project(x, y, z=0):
        return 420 + (x - y) * scale * .57, 172 + (x + y) * scale * .32 - z * scale * .48

    x0, y0 = 77, 65
    unit = "см" if language == "ru" else "cm"
    room_label, door_label, passage_label = ("Комната", "Дверь", "Проход") if language == "ru" else ("Room", "Door", "Passage")
    scheme = [rectangle(x0, y0, room_w * scale, room_d * scale, "#f3eddf"),
              rectangle(x0 + left * scale, y0, (right-left) * scale, layout.passage_end_y * scale, "#dce9cc", "#678564"),
              rectangle(x0 + layout.door_x * scale, y0, layout.door_width * scale, 3, "#c2cba9"),
              rectangle(x0 + layout.door_x * scale, y0, 4 * scale, layout.door_width * scale, "#71896c"),
              label(x0, y0 - 18, f"{room_label} {room_w} × {room_d} {unit} · "
                    f"{door_label} {layout.door_width} {unit} · {passage_label} {right-left} {unit}")]
    perspective = [polygon([project(0, 0), project(room_w, 0), project(room_w, room_d), project(0, room_d)], "#e6d4b5"),
                   polygon([project(left, 0), project(right, 0), project(right, layout.passage_end_y),
                            project(left, layout.passage_end_y)], "#c0d2a8"),
                   polygon([project(layout.door_x, 0), project(layout.door_x+4, 0),
                            project(layout.door_x+4, layout.door_width), project(layout.door_x, layout.door_width)], "#71896c")]
    measurements = []
    for index, item in enumerate(sorted(layout.furniture, key=lambda piece: piece.x + piece.y), 1):
        x, y = x0 + item.x * scale, y0 + item.y * scale
        scheme += [rectangle(x, y, item.width * scale, item.depth * scale, colors[item.kind]), label(x + 4, y + 16, str(index))]
        a, b, c, d = [(item.x, item.y), (item.right, item.y), (item.right, item.bottom), (item.x, item.bottom)]
        height = 75 if item.kind in {"desk", "table"} else 50
        perspective += [polygon([project(*b), project(*c), project(*c, height), project(*b, height)], "#a88669"),
                        polygon([project(*c), project(*d), project(*d, height), project(*c, height)], "#a88669"),
                        polygon([project(*p, height) for p in (a, b, c, d)], colors[item.kind]),
                        label(*project((item.x + item.right)/2, (item.y + item.bottom)/2, height), str(index))]
        measurements.append(f"{index}. {labels[item.kind]} {item.width} × {item.depth} {unit}")
    for index, line in enumerate(measurements):
        scheme.append(label(75 + (index // 4)*245, 670 + (index % 4)*23, line))
    for name, elements in (("illustration", perspective), ("plan", scheme)):
        svg = directory / f"{name}.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="840" height="768">'
                       + rectangle(0, 0, 840, 768, "#faf7ee", "#faf7ee") + "".join(elements) + "</svg>", encoding="utf-8")
        output = directory / f"{name}.png"
        _ffmpeg(["-protocol_whitelist", "file,pipe", "-i", str(svg), "-frames:v", "1", "-threads", "1", str(output)])
        yield output.read_bytes()


def materialize_photo(session, task, run, candidate, layout_data, policy, *, guardrail_blocked, publish):
    # Commit the draft and both attachments through the editorial transaction.
    # No delivery target exists until the full album is present.
    import re
    from postbridge.agent.storage import _persist_candidate_fingerprints
    from postbridge.models.domain import RenderVariantOrm

    body = candidate.body_markdown or ""
    content = create_postbridge_content_item(session, tenant_id=task.tenant_id, author_user_id=task.created_by,
        title=candidate.headline, content_md=body, content_plain=None, media_url=None, media_urls=None, summary=None,
        link_url=None, cta=None, tags=None, author=None, cover_image_url=None, status="draft")
    session.flush()
    candidate.content_item_id, candidate.status = content.id, "converted"
    run.content_item_id = content.id
    if guardrail_blocked:
        return content.id, "Editorial review is required before delivery"
    uploaded = []
    try:
        with session.begin_nested():
            target = selected_bridge(session, task.tenant_id, task.channel_id, policy, task.created_by)
            try:
                layout = Layout.model_validate(layout_data)
            except ModelError:
                raise ValueError("The draft has no valid numerical furniture plan") from None
            passage = layout.passage(policy.minimum_passage_cm)
            if re.search(r"\d[\s-]*(?:cm\b|m\b|centimet|meter|метр|сантиметр|см\b|м(?:\b|²)|mm\b|мм\b|inches\b|[×])",
                         f"{candidate.headline}\n{body}", re.I):
                raise ValueError("Put dimensional measurements only in the numerical plan")
            size = f"{layout.room_width} × {layout.room_depth}"
            width = passage[1] - passage[0]
            measurements = (f"Комната {size} см. Дверь {layout.door_width} см. Свободный проход со стулом в рабочем положении {width} см."
                            if policy.language == "ru" else
                            f"Room {size} cm. Door {layout.door_width} cm. Clear passage with the chair in use {width} cm.")
            caption = "\n\n".join((candidate.headline.strip(), body.strip(), measurements))
            if len(caption.encode("utf-16-le")) // 2 > 1024:
                raise ValueError("Telegram photo captions must fit 1024 characters")
            with TemporaryDirectory(prefix="postbridge-layout-") as directory:
                images = list(render_layout(layout, passage, policy.language, Path(directory)))
            for image in images:
                asset = store_media_asset(session, tenant_id=task.tenant_id, data=image, content_type="image/png", commit=False)
                uploaded.append(session.get(MediaAssetOrm, asset["media_asset_id"]))
            content.media_urls = [managed_media_url(asset.id, asset.object_key) for asset in uploaded]
            content.media_url = content.media_urls[0]
            content.body_markdown = f"{body.strip()}\n\n{measurements}"
            if not publish:
                return content.id, "The completed photo album is ready for review"
            chain = create_plan_and_targets_for_content_item(session, tenant_id=task.tenant_id, content_item=content,
                channel_ids=[target.id], plan_strategy=PHOTO_STRATEGY)
            _persist_candidate_fingerprints(session, tenant_id=task.tenant_id, channel_id=task.channel_id,
                content_item_id=content.id, candidate=candidate)
            session.get(RenderVariantOrm, chain.render_variant_ids[0]).body_text = caption
            origin = {"task_id": task.id, "policy": policy.model_dump(mode="json"), "target": target.external_id,
                      "assets": [asset.id for asset in uploaded], "caption": caption,
                      "content_hash": fingerprint([content.title, content.body_markdown])}
            content.body_structured_json = _dump_extra({**_load_extra(content.body_structured_json),
                "editorial_photo": origin, "live_sync_source_core_channel_id": task.channel_id,
                "saas_workspace_id": (session.get(ChannelOrm, task.channel_id).external_id or "").removeprefix("pb/")})
        return content.id, None
    except (OSError, RuntimeError, ValidationError, ValueError) as exc:
        from postbridge.infrastructure.media_storage import delete_media_object
        for asset in uploaded:
            try:
                delete_media_object(asset.object_key)
            except (OSError, RuntimeError):
                pass
        if isinstance(exc, ValidationError):
            return content.id, exc.message
        if isinstance(exc, ValueError):
            return content.id, str(exc)
        return content.id, "The photo album could not be rendered or saved"


def approved_album(session, target, content, post):
    import json
    plan = session.get(PublicationPlanOrm, target.publication_plan_id)
    if not plan or plan.strategy != PHOTO_STRATEGY:
        return False
    origin = _load_extra(content.body_structured_json).get("editorial_photo") if content else None
    if not origin or content.status != "draft":
        raise photo_error("EDITORIAL_PHOTO_REVIEW_REQUIRED", "The album draft is no longer available")
    task = session.get(AgentTaskOrm, origin["task_id"], populate_existing=True)
    current = json.loads(task.task_config_json).get("editorial_photo") if task else None
    if (not task or task.tenant_id != target.tenant_id or task.status != "active"
            or not current or EditorialPhotoPolicy.model_validate(current).model_dump(mode="json") != origin["policy"]):
        raise photo_error("EDITORIAL_PHOTO_REVIEW_REQUIRED", "The schedule consent has changed")
    channel = selected_bridge(session, target.tenant_id, task.channel_id, EditorialPhotoPolicy.model_validate(current), task.created_by)
    if (target.channel_id != channel.id or origin["target"] != channel.external_id or origin["caption"] != post.text
            or origin["content_hash"] != fingerprint([content.title, content.body_markdown])):
        raise photo_error("EDITORIAL_PHOTO_REVIEW_REQUIRED", "The approved Telegram album or destination has changed")
    images = []
    for asset_id in origin["assets"]:
        asset = session.get(MediaAssetOrm, asset_id)
        if (not asset or asset.tenant_id != target.tenant_id or asset.content_type != "image/png"
                or not 0 < asset.byte_size <= PHOTO_LIMIT):
            raise photo_error("EDITORIAL_PHOTO_FILE_MISSING", "An approved album image is missing")
        try:
            data = read_media_object(asset.object_key, max_bytes=asset.byte_size)
        except (OSError, RuntimeError):
            raise photo_error("EDITORIAL_PHOTO_FILE_MISSING", "An approved album image is missing") from None
        if len(data) != asset.byte_size or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise photo_error("EDITORIAL_PHOTO_FILE_MISSING", "An approved album image failed verification")
        images.append((managed_media_url(asset.id, asset.object_key), data))
    if [url for url, _ in images] != (content.media_urls or []) or len(images) != 2:
        raise photo_error("EDITORIAL_PHOTO_REVIEW_REQUIRED", "The approved album attachments have changed")
    post.telegram_album = tuple(data for _, data in images)
    return True
