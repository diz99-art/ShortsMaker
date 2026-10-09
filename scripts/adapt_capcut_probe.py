"""Experimental Mac 5.9.1 fixture adapter; not a production exporter."""
import argparse
import json
import shutil
import time
import uuid
from pathlib import Path


def adapt(source: Path, destination: Path, installed_path: Path) -> None:
    shutil.copytree(source, destination)  # fails if destination exists
    data = json.loads((destination / "draft_content.json").read_text())
    data["new_version"] = "132.0.0"
    data["platform"] = {"app_id": 359289, "app_source": "cc", "app_version": "5.9.1", "os": "mac"}
    for name in ("draft_content.json", "draft_info.json"):
        (destination / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    meta = json.loads((destination / "draft_meta_info.json").read_text())
    meta.update(draft_id=str(uuid.uuid4()).upper(), draft_name=installed_path.name,
                draft_fold_path=str(installed_path), draft_root_path=str(installed_path.parent),
                tm_duration=data["duration"], tm_draft_create=int(time.time() * 1_000_000),
                tm_draft_modified=int(time.time() * 1_000_000))
    entries = []
    for kind in ("videos", "audios"):
        for material in data["materials"][kind]:
            entries.append({
                "ai_group_type": "", "create_time": -1, "duration": 0 if kind == "videos" else material["duration"],
                "enter_from": 0, "extra_info": Path(material["path"]).name,
                "file_Path": material["path"], "height": material.get("height", 0),
                "width": material.get("width", 0), "id": material["id"],
                "import_time": -1, "import_time_ms": -1, "item_source": 1,
                "material_color_tag": "", "md5": "", "metetype": "photo" if kind == "videos" else "music",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1}, "type": 0,
            })
    for group in meta["draft_materials"]:
        if group["type"] == 0:
            group["value"] = entries
    (destination / "draft_meta_info.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("installed_path", type=Path)
    args = parser.parse_args()
    adapt(args.source, args.destination, args.installed_path.resolve())
