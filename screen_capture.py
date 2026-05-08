"""
Screen Capture & OCR — Captures the screen and extracts text using Tesseract.
"""

import mss
import pytesseract
from PIL import Image
from config import load_config


def configure_tesseract():
    """Set the Tesseract executable path from config."""
    config = load_config()
    tesseract_path = config.get("tesseract_path", "")
    if tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = tesseract_path


def capture_full_screen() -> Image.Image:
    """Capture the entire primary screen as a PIL Image."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # Primary monitor
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


def capture_region(left: int, top: int, width: int, height: int) -> Image.Image:
    """Capture a specific region of the screen."""
    with mss.mss() as sct:
        region = {"left": left, "top": top, "width": width, "height": height}
        screenshot = sct.grab(region)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


def extract_text(image: Image.Image) -> str:
    """Run OCR on a PIL Image and return extracted text."""
    configure_tesseract()
    config = load_config()
    lang = config.get("ocr_language", "eng")
    
    try:
        text = pytesseract.image_to_string(image, lang=lang)
        return text.strip()
    except pytesseract.TesseractNotFoundError:
        return "[ERROR] Tesseract not found. Install from: https://github.com/UB-Mannheim/tesseract/wiki"
    except Exception as e:
        return f"[ERROR] OCR failed: {str(e)}"


def scan_screen() -> str:
    """Capture the full screen and extract text via OCR."""
    image = capture_full_screen()
    text = extract_text(image)
    return text


def scan_region(left: int, top: int, width: int, height: int) -> str:
    """Capture a screen region and extract text via OCR."""
    image = capture_region(left, top, width, height)
    text = extract_text(image)
    return text
