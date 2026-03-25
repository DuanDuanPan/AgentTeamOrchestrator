"""WCAG AA 对比度验证测试。

纯 Python 计算语义色在 $background 上的相对亮度比值，
要求所有前景色 ≥ 4.5:1（WCAG AA 正文标准）。
$surface 作为背景色不参与前景对比度检查。
"""

from __future__ import annotations


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 相对亮度计算。"""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)

    def linearize(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    """计算两色对比度。"""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# 从 TCSS 主题定义中提取的颜色值（必须与 app.tcss 保持同步）
BACKGROUND = "#282a36"

# 所有必须与 background 对比 ≥ 4.5:1 的前景语义色
FOREGROUND_COLORS: dict[str, str] = {
    "success": "#50fa7b",
    "warning": "#f1fa8c",
    "error": "#ff5555",
    "info": "#8be9fd",
    "accent": "#bd93f9",
    "muted": "#8390b7",  # 可访问变体，非 Dracula 原值 #6272a4
    "text": "#f8f8f2",
}

WCAG_AA_THRESHOLD = 4.5


class TestWCAGContrast:
    """验证所有语义前景色在 $background 上满足 WCAG AA 4.5:1。"""

    def test_all_foreground_colors_meet_aa(self) -> None:
        """每个前景色与 $background 对比度 ≥ 4.5:1。"""
        failures: list[str] = []
        for name, color in FOREGROUND_COLORS.items():
            ratio = _contrast_ratio(color, BACKGROUND)
            if ratio < WCAG_AA_THRESHOLD:
                failures.append(f"${name} ({color}): {ratio:.2f}:1 < {WCAG_AA_THRESHOLD}:1")
        assert not failures, "WCAG AA 对比度不达标:\n" + "\n".join(failures)

    def test_success_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["success"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_warning_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["warning"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_error_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["error"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_info_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["info"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_accent_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["accent"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_muted_contrast(self) -> None:
        """$muted 必须是可访问变体（非 Dracula 原值 #6272a4）。"""
        ratio = _contrast_ratio(FOREGROUND_COLORS["muted"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_text_contrast(self) -> None:
        ratio = _contrast_ratio(FOREGROUND_COLORS["text"], BACKGROUND)
        assert ratio >= WCAG_AA_THRESHOLD

    def test_dracula_original_muted_fails(self) -> None:
        """确认 Dracula 原值 #6272a4 确实不达标，验证我们需要可访问变体。"""
        ratio = _contrast_ratio("#6272a4", BACKGROUND)
        assert ratio < WCAG_AA_THRESHOLD, (
            f"Dracula 原值 #6272a4 意外达标 ({ratio:.2f}:1)，请确认 WCAG 计算正确"
        )

    def test_relative_luminance_black(self) -> None:
        assert _relative_luminance("#000000") == 0.0

    def test_relative_luminance_white(self) -> None:
        assert abs(_relative_luminance("#ffffff") - 1.0) < 0.001

    def test_contrast_ratio_black_white(self) -> None:
        ratio = _contrast_ratio("#ffffff", "#000000")
        assert abs(ratio - 21.0) < 0.1
