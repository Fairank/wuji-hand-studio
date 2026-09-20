# -*- coding: utf-8 -*-
"""official_policy.classify_device_error 的分类器单元测试。

建议文件名：test_official_policy.py
运行方式：python -m unittest -v test_official_policy

范围与限制：
- 仅依据任务说明中的公开接口规格编写，所有输入均为合成数据。
- 不连接、不读取、不控制任何真实设备，不执行任何动作。
- 测试通过只说明字符串分类映射符合规格；不能据此推断硬件健康，
  也不代表实机测试通过。本文件不参与任何安全决策。
- Stall 属于 Warning 等级这一点，依据任务说明所引用的公开文档：
  https://docs.wuji.tech/docs/en/wuji-hand/latest/troubleshooting/
  错误码数值与 KNOWN_NAME 均为合成占位值，未与该文档核对。
"""

import unittest

from official_policy import classify_device_error

# 合成占位错误码（正整数），不是官方文档中的真实错误码。
SYNTHETIC_CODE = 101
# 合成的已知名称：非空 str，且小写后不以 unknown 开头。
KNOWN_NAME = "SyntheticFault"


class ClassifyDeviceErrorTests(unittest.TestCase):
    """classify_device_error(code, severity, name) 的 8 个独立测试。"""

    def _classify(self, code, severity, name):
        """调用被测函数，并确认返回值为 str（规格：输出字符串）。"""
        result = classify_device_error(code, severity, name)
        self.assertIsInstance(result, str)
        return result

    def test_stall_with_warning_severity_returns_warning(self):
        """公开文档情形：Stall + Warning -> 'warning'。"""
        result = self._classify(SYNTHETIC_CODE, "Warning", "Stall")
        self.assertEqual(result, "warning")

    def test_deferred_stop_returns_stop(self):
        """已知名称 + DeferredStop -> 'stop'。"""
        result = self._classify(SYNTHETIC_CODE, "DeferredStop", KNOWN_NAME)
        self.assertEqual(result, "stop")

    def test_immediate_stop_returns_stop(self):
        """已知名称 + ImmediateStop -> 'stop'。"""
        result = self._classify(SYNTHETIC_CODE, "ImmediateStop", KNOWN_NAME)
        self.assertEqual(result, "stop")

    def test_fatal_returns_stop(self):
        """已知名称 + Fatal -> 'stop'。"""
        result = self._classify(SYNTHETIC_CODE, "Fatal", KNOWN_NAME)
        self.assertEqual(result, "stop")

    def test_code_zero_returns_none_string(self):
        """无错误：code == 0 -> 字符串 'none'（不是 Python 的 None）。"""
        result = self._classify(0, None, None)
        self.assertEqual(result, "none")

    def test_unknown_name_returns_unknown(self):
        """code > 0 但名称未知或无效 -> 'unknown'，即使等级本身有效。"""
        cases = (
            ("Warning", "Unknown"),
            ("Fatal", "UNKNOWN_FAULT"),
            ("ImmediateStop", "unknown error 7"),
            ("Warning", ""),
            ("DeferredStop", None),
            ("Warning", 123),
        )
        for severity, name in cases:
            with self.subTest(severity=severity, name=name):
                result = self._classify(SYNTHETIC_CODE, severity, name)
                self.assertEqual(result, "unknown")

    def test_missing_severity_returns_unknown(self):
        """已知名称但缺少等级（None 或空字符串）-> 'unknown'。"""
        for severity in (None, ""):
            with self.subTest(severity=severity):
                result = self._classify(SYNTHETIC_CODE, severity, KNOWN_NAME)
                self.assertEqual(result, "unknown")

    def test_illegal_code_returns_unknown(self):
        """非法 code（负数、bool、非 int）-> 'unknown'。"""
        # 注意：False == 0，但 bool 必须得到 'unknown' 而不是 'none'。
        for code in (-1, True, False, 1.0, "1", None):
            with self.subTest(code=code):
                result = self._classify(code, "Warning", KNOWN_NAME)
                self.assertEqual(result, "unknown")


if __name__ == "__main__":
    unittest.main()
