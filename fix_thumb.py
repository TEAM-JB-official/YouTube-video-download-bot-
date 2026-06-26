import os
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser

async def fix_thumb(thumb_path: str):
    """
    Resize thumbnail to width 320px, maintain aspect ratio, convert to JPEG.
    Returns: (width, height, thumb_path) – thumb_path may be None on error.
    """
    if not thumb_path or not os.path.exists(thumb_path):
        return 0, 0, None

    width = 0
    height = 0
    try:
        # Try to get metadata with hachoir
        parser = createParser(thumb_path)
        metadata = extractMetadata(parser)
        if metadata and metadata.has("width") and metadata.has("height"):
            width = metadata.get("width")
            height = metadata.get("height")

        # Fallback to PIL if metadata fails
        if width == 0 or height == 0:
            with Image.open(thumb_path) as img:
                width, height = img.size

        # Resize and convert to JPEG
        with Image.open(thumb_path) as img:
            img = img.convert("RGB")
            aspect_ratio = height / width
            new_height = int(320 * aspect_ratio)
            resized_img = img.resize((320, new_height))
            resized_img.save(thumb_path, "JPEG")

        return width, height, thumb_path

    except Exception as e:
        print(f"[fix_thumb] Error: {e}")
        # If anything fails, return None for thumb_path to skip thumb
        return 0, 0, None
