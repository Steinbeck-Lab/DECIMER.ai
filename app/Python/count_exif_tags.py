import sys
import os
import json
from PIL import Image


def main():
    """Count images with EXIF tags from JSON array of paths."""
    try:
        if len(sys.argv) < 2:
            print("0")
            return

        # CRITICAL FIX: Use json.loads instead of eval
        json_input = sys.argv[1]
        paths = json.loads(json_input)

        if not isinstance(paths, list):
            print("0")
            return

    except (json.JSONDecodeError, IndexError) as e:
        print(f"Error parsing input: {e}", file=sys.stderr)
        print("0")
        return

    num_exif_tags = 0
    dir_path = "../storage/app/public/media/"

    for image_path in paths:
        try:
            filename = os.path.basename(image_path)
            full_path = os.path.join(dir_path, filename)

            if os.path.exists(full_path):
                image = Image.open(full_path)
                exif_tag = image.getexif()
                if exif_tag:
                    num_exif_tags += 1
        except Exception as e:
            print(f"Error processing {image_path}: {e}", file=sys.stderr)
            continue

    print(num_exif_tags)


if __name__ == "__main__":
    main()
