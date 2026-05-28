"""软切分纯逻辑单测（不依赖 sherpa_onnx / 模型）。"""

from app.segmentation import join_segments, should_soft_segment


class TestShouldSoftSegment:
    def test_disabled_never_segments(self):
        assert should_soft_segment(1_000_000, enabled=False, max_samples=128_000) is False

    def test_below_threshold(self):
        assert should_soft_segment(127_999, enabled=True, max_samples=128_000) is False

    def test_at_threshold(self):
        assert should_soft_segment(128_000, enabled=True, max_samples=128_000) is True

    def test_above_threshold(self):
        assert should_soft_segment(200_000, enabled=True, max_samples=128_000) is True

    def test_zero_max_samples_never_segments(self):
        # max_s<=0 关闭软切分，避免每帧都切
        assert should_soft_segment(999, enabled=True, max_samples=0) is False


class TestJoinSegments:
    def test_empty_prefix_returns_seg(self):
        assert join_segments("", "你好") == "你好"

    def test_empty_seg_returns_prefix(self):
        assert join_segments("你好", "") == "你好"

    def test_both_empty(self):
        assert join_segments("", "") == ""

    def test_chinese_no_space(self):
        assert join_segments("你好", "世界") == "你好世界"

    def test_english_word_boundary_gets_space(self):
        assert join_segments("hello", "world") == "hello world"

    def test_english_then_chinese_no_space(self):
        assert join_segments("hello", "世界") == "hello世界"

    def test_chinese_then_english_no_space(self):
        assert join_segments("你好", "world") == "你好world"

    def test_strips_surrounding_whitespace(self):
        assert join_segments("  hello  ", "  world  ") == "hello world"

    def test_digits_treated_as_ascii_boundary(self):
        assert join_segments("9", "5") == "9 5"
