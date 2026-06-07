"""Сборка PDF из присланных фото. Использует Pillow + img2pdf."""
import io

import img2pdf
from PIL import Image


def build_pdf(photo_bytes_list: list[bytes]) -> bytes:
    """Собирает один PDF из списка байтов изображений, сохраняя порядок."""
    normalized: list[bytes] = []
    for raw in photo_bytes_list:
        try:
            img = Image.open(io.BytesIO(raw))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            normalized.append(buf.getvalue())
        except Exception:
            # если Pillow не смог — пропускаем кадр, не валим всю сборку
            continue

    if not normalized:
        raise ValueError("Нет валидных изображений для PDF")

    return img2pdf.convert(normalized)
