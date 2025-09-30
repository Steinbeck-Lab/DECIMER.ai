import sys
import os
import json
from pdf2image import convert_from_path


def convert_pdf_to_images(pdf_path: str):
    """
    Convert PDF pages to images and return web-accessible paths.

    Args:
        pdf_path (str): Path to PDF file relative to storage (e.g., 'public/media/file.pdf')

    Returns:
        str: JSON array of web-accessible image paths
    """
    # Get absolute paths
    pdf_dir = os.path.split(__file__)[0]
    pdf_name = os.path.split(pdf_path)[1]
    base_path = os.path.join(pdf_dir, "../../storage/app/")
    full_pdf_path = os.path.join(base_path, pdf_path)

    # Convert PDF to images (limit to 10 pages)
    page_images = convert_from_path(full_pdf_path, 300, last_page=10)

    im_paths = []
    save_dir = "/var/www/app/storage/app/public/media/"

    # Ensure directory exists
    os.makedirs(save_dir, exist_ok=True)

    for num, image in enumerate(page_images):
        filename = f"{pdf_name}_{num}.png"
        save_path = os.path.join(save_dir, filename)
        image.save(save_path, format="PNG")

        # Return web-accessible path with storage/ prefix
        web_path = f"storage/media/{filename}"
        im_paths.append(web_path)

    im_paths.sort()
    return json.dumps(im_paths)


if __name__ == "__main__":
    im_paths = convert_pdf_to_images(sys.argv[1])
    print(im_paths)
