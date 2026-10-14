"""并发下载后的图片转换、连续编号与来源记录。"""

import json
import re
from threading import Lock

from PIL import Image, ImageOps


class ImageOutput:
    def __init__(self, prefix, long_edge):
        self.prefix = prefix
        self.long_edge = long_edge
        self.lock = Lock()
        self.records = {}

    def record(self, folder):
        if folder not in self.records:
            path = folder / ".image_index.json"
            self.records[folder] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return self.records[folder]

    def key(self, identity):
        return f"{self.prefix}:{self.long_edge}:{identity}"

    def existing(self, folder, identity):
        with self.lock:
            name = self.record(folder).get(self.key(identity))
            if name:
                target = folder / name
                return target.is_file() and target.stat().st_size > 0
            return False

    def save(self, source, folder, identity):
        with Image.open(source) as opened:
            oriented = ImageOps.exif_transpose(opened)
            rgba = oriented.convert("RGBA")
            width, height = rgba.size
            scale = self.long_edge / max(width, height)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            resized = rgba.resize(size, Image.Resampling.LANCZOS)
            converted = Image.new("RGB", size, "white")
            converted.paste(resized, mask=resized.getchannel("A"))
        with self.lock:
            pattern = re.compile(re.escape(self.prefix) + r"_(\d+)\.jpg$")
            numbers = [int(match.group(1)) for path in folder.iterdir()
                       if (match := pattern.fullmatch(path.name))]
            number = max(numbers, default=0) + 1
            target = folder / f"{self.prefix}_{number:04d}.jpg"
            temporary = target.with_suffix(".jpg.part")
            try:
                converted.save(temporary, format="JPEG", quality=95)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            record = self.record(folder)
            record[self.key(identity)] = target.name
            index = folder / ".image_index.json"
            index_temporary = index.with_suffix(".json.part")
            try:
                index_temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                index_temporary.replace(index)
            finally:
                index_temporary.unlink(missing_ok=True)
            return target
