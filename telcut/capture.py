import io
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

from .config import CaptureConfig


class MenuBarHiddenError(RuntimeError):
    pass


def has_menu_content(image: Image.Image) -> bool:
    """Reject a blank background when an automatically hidden menu is absent.

    This detects visible edges, not clock text. The user still checks preview.
    """
    if min(image.size) < 5:
        return False
    # Horizontal color seams in wallpaper or under the menu bar are not text.
    # Look for vertical strokes by measuring left-to-right contrast instead.
    edges = image.convert("L").filter(ImageFilter.Kernel(
        (3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=1
    ))
    inner = edges.crop((2, 2, image.width - 2, image.height - 2))
    return sum(inner.histogram()[40:]) >= max(20, inner.width * inner.height * 0.002)


def crop_clock(image: Image.Image, config: CaptureConfig) -> Image.Image:
    """Ratios use the actual captured image, including Retina pixel scaling."""
    width, height = image.size
    crop_width = min(width, config.width_pixels or max(1, round(width * config.right_width_ratio)))
    crop_height = min(height, config.height_pixels or max(1, round(height * config.top_height_ratio)))
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
            if config.require_menu_content and not has_menu_content(cropped):
                raise MenuBarHiddenError(
                    "截图中没有检测到菜单栏文字或图标。请将 macOS 菜单栏设为始终显示，"
                    "并确认目标屏幕右上角日期时间可见，然后重试。"
                )
            output = io.BytesIO()
            cropped.save(output, format="PNG")
            return output.getvalue()
