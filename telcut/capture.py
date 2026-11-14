import io
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from .config import CaptureConfig


def crop_clock(image: Image.Image, config: CaptureConfig) -> Image.Image:
    """Ratios use the actual captured image, including Retina pixel scaling."""
    width, height = image.size
    crop_width = max(1, round(width * config.right_width_ratio))
    crop_height = max(1, round(height * config.top_height_ratio))
    return image.crop((width - crop_width, 0, width, crop_height))


def capture_clock(config: CaptureConfig) -> bytes:
    if sys.platform != "darwin":
        raise RuntimeError("当前版本需要在 macOS 本机运行")
    # The full display exists only in a private temporary directory and is
    # removed before returning. Only the cropped PNG reaches the sender.
    with tempfile.TemporaryDirectory(prefix="telcut-") as directory:
        path = Path(directory) / "display.png"
        try:
            result = subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-t", "png",
                 "-D", str(config.display), str(path)],
                capture_output=True, timeout=15, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("截图超时，请检查屏幕录制权限") from exc
        if result.returncode or not path.is_file():
            raise RuntimeError(
                "截图失败：请在系统设置→隐私与安全性→屏幕录制中允许运行程序的终端，"
                "并确认屏幕编号正确，然后重启终端"
            )
        with Image.open(path) as image:
            cropped = crop_clock(image, config).convert("RGB")
            output = io.BytesIO()
            cropped.save(output, format="PNG")
            return output.getvalue()
