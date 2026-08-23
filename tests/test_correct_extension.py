"""推送扩展名纠正 —— 单元测试"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.__main__ import correct_extension

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff"


class TestCorrectExtension:
    def test_md_content_with_png_name(self):
        # 截图失败后 md 文本冒充 png 的核心场景
        assert correct_extension("# 总设计\n## 1. 目标".encode("utf-8"), "shot.png") == "shot.md"

    def test_md_content_with_jpg_name(self):
        assert correct_extension("# 总设计".encode("utf-8"), "report.jpg") == "report.md"

    def test_real_png_kept(self):
        assert correct_extension(PNG_MAGIC + b"data", "shot.png") == "shot.png"

    def test_real_png_wrong_ext_fixed(self):
        assert correct_extension(PNG_MAGIC + b"data", "shot.md") == "shot.png"

    def test_real_jpg_kept(self):
        assert correct_extension(JPG_MAGIC + b"data", "photo.jpg") == "photo.jpg"

    def test_jpg_with_png_name_fixed(self):
        assert correct_extension(JPG_MAGIC + b"data", "photo.png") == "photo.jpg"

    def test_plain_text_md_untouched(self):
        assert correct_extension("# 报告内容".encode("utf-8"), "report.md") == "report.md"

    def test_binary_unknown_untouched(self):
        assert correct_extension(b"\x00\x01\x02\x03" + b"x" * 100, "data.bin") == "data.bin"
