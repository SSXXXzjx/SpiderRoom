import json
import re
import shutil

from PIL import Image, ImageOps


def make_image_output(base_class, prefix, long_edge, crop_bottom, resize_enabled=True):
    if resize_enabled and crop_bottom == 0:
        return base_class(prefix, long_edge)

    class CroppedImageOutput(base_class):
        def key(self, identity):
            if not resize_enabled:
                return f"{self.prefix}:original:crop-bottom={crop_bottom}:{identity}"
            return f"{super().key(identity)}:crop-bottom={crop_bottom}"

        def save(self, source, folder, identity):
            with Image.open(source) as opened:
                preserve_source = not resize_enabled and crop_bottom == 0
                extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp", "TIFF": ".tiff", "AVIF": ".avif"}.get(opened.format)
                if preserve_source:
                    if not extension:
                        raise ValueError(f"不支持的原图格式：{opened.format}")
                    opened.verify()
                else:
                    extension = ".jpg"
                    oriented = ImageOps.exif_transpose(opened)
                    rgba = oriented.convert("RGBA")
                    width, height = rgba.size
                    scale = self.long_edge / max(width, height) if resize_enabled else 1
                    size = (max(1, round(width * scale)), max(1, round(height * scale)))
                    if crop_bottom >= size[1]:
                        print(f"裁剪失败：底部 {crop_bottom}px，不得超过图片高度 {size[1]}px")
                        raise ValueError(f"底部裁剪 {crop_bottom}px 不得超过图片高度 {size[1]}px")
                    resized = rgba.resize(size, Image.Resampling.LANCZOS) if resize_enabled else rgba
                    cropped = resized.crop((0, 0, size[0], size[1] - crop_bottom))
                    converted = Image.new("RGB", cropped.size, "white")
                    converted.paste(cropped, mask=cropped.getchannel("A"))
            with self.lock:
                pattern = re.compile(re.escape(self.prefix) + r"_(\d+)\.(?:jpg|png|webp|gif|bmp|tiff|avif)$", re.IGNORECASE)
                numbers = [int(match.group(1)) for path in folder.iterdir()
                           if (match := pattern.fullmatch(path.name))]
                number = max(numbers, default=0) + 1
                target = folder / f"{self.prefix}_{number:04d}{extension}"
                temporary = target.with_suffix(extension + ".part")
                try:
                    if preserve_source:
                        shutil.copyfile(source, temporary)
                    else:
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

    return CroppedImageOutput(prefix, long_edge)
