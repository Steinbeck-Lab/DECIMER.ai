import sys
from io import BytesIO
from PIL import Image
import pyheif


def RGBA2RGB(im_path: str) -> None:
    """
    Convert a given image to RGB if it is RGBA.
    The image is overwritten.

    Args:
        im_path (str): image path
    """
    img = Image.open(im_path)
    if img.mode == 'RGBA':
        img.load()
        new_img = Image.new('RGB', img.size, (255, 255, 255))
        new_img.paste(img, mask=img.split()[3])
        new_img.save(im_path, "PNG")


def HEIF2PNG(im_path):
    """
    Convert a given image from HEIF to PNG.
    The image is overwritten.

    Args:
        im_path (str): image path
    """
    if im_path[-4:].lower() in ["heic", "heif"]:
        with open(im_path, "rb") as fh:
            buf = BytesIO(fh.read())
            im = pyheif.read_heif(buf)
        # Convert to other file format like jpeg
        pi = Image.frombytes(
                mode=im.mode, size=im.size, data=im.data)
        pi.save(im_path, format="png")


def main():
    """
    This script takes an image path given as the first argument,
    and converts it to an RGB if it is an RGBA image
    and converts HEIF images to PNG images.
    """
    im_path = sys.argv[1]
    HEIC2PNG(im_path)
    RGBA2RGB(im_path)


if __name__ == '__main__':
    main()