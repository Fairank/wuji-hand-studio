r"""test_official_replay.py -- unit tests for official_replay.decode_replay.

Pure parser tests: stdlib only, no hardware, network, files or project context.

Format under test (little-endian, no padding):
  header '<8sHBBI' : magic b'WJH2RPL\0', version 1, side (right=1, left=2),
                     joint count 20, frame count
  frame  '<I20f'   : opaque uint32 tag, then 20 float32 joint angles in radians

Contract: decode_replay(raw, expected_side='left') -> list of 20-float tuples;
ValueError on bad magic/version/side/joint count, zero or >1_000_000 frames,
wrong total byte length, or any non-finite float. The tag carries no meaning.

Note: written without being executed. To run, from the folder that holds
official_replay.py:  python -m unittest -v test_official_replay
"""
import struct
import unittest

from official_replay import decode_replay

MAGIC = b"WJH2RPL\x00"
HEADER = struct.Struct("<8sHBBI")
FRAME = struct.Struct("<I20f")
RIGHT, LEFT, JOINTS = 1, 2, 20


def pose(k):
    """20 distinct angles. Multiples of 1/8 are exact in float32, so == is safe."""
    return tuple((i - k) * 0.125 for i in range(JOINTS))


def header(count, magic=MAGIC, version=1, side=LEFT, joints=JOINTS):
    return HEADER.pack(magic, version, side, joints, count)


def body(poses, tags=None):
    tags = range(len(poses)) if tags is None else tags
    return b"".join(FRAME.pack(tag, *p) for tag, p in zip(tags, poses))


def blob(poses, tags=None, **fields):
    return header(len(poses), **fields) + body(poses, tags)


class DecodeReplayTests(unittest.TestCase):
    def assertRejected(self, cases, **kwargs):
        """Every case must raise ValueError (struct.error/MemoryError/etc. do not count)."""
        for name, raw in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                decode_replay(raw, **kwargs)

    def test_1_valid_left(self):
        self.assertEqual((HEADER.size, FRAME.size), (16, 84))  # fixture sanity
        poses = [pose(0), pose(3), pose(7), pose(3)]  # repeated frame must be kept, in order
        # Tags are opaque: non-monotonic, duplicated, NaN/Inf-looking bit patterns are all fine.
        raw = blob(poses, tags=[0xFFFFFFFF, 0, 0, 0x7F800000])
        for frames in (decode_replay(raw), decode_replay(raw, expected_side="left")):
            self.assertIsInstance(frames, list)
            self.assertEqual(frames, poses)
            self.assertTrue(all(isinstance(f, tuple) and all(isinstance(x, float) for x in f)
                                for f in frames))

    def test_2_right_rejected_when_expecting_left(self):
        right, left = blob([pose(1)], side=RIGHT), blob([pose(1)], side=LEFT)
        with self.subTest("right file, default expected_side"), self.assertRaises(ValueError):
            decode_replay(right)
        with self.subTest("right file, expected_side='left'"), self.assertRaises(ValueError):
            decode_replay(right, expected_side="left")
        with self.subTest("mirror: left file, expected_side='right'"), self.assertRaises(ValueError):
            decode_replay(left, expected_side="right")

    def test_3_valid_right_when_expecting_right(self):
        poses = [pose(2), pose(5)]
        raw = blob(poses, tags=[9, 3], side=RIGHT)
        self.assertEqual(decode_replay(raw, expected_side="right"), poses)

    def test_4_malformed_header_rejected(self):
        good = blob([pose(0)])
        self.assertRejected({
            "empty input": b"",
            "magic only": MAGIC,
            "header one byte short": good[:HEADER.size - 1],
            "wrong magic revision": blob([pose(0)], magic=b"WJH1RPL\x00"),
            "magic without trailing NUL": blob([pose(0)], magic=b"WJH2RPL!"),
            "zeroed magic": blob([pose(0)], magic=bytes(8)),
        })
        for expected in ("left", "right"):  # only right=1 / left=2 are defined side bytes
            self.assertRejected({f"side byte {s}, expecting {expected}": blob([pose(0)], side=s)
                                 for s in (0, 3, 0xFF)}, expected_side=expected)

    def test_5_truncated_or_extra_frame_bytes_rejected(self):
        poses = [pose(0), pose(1)]
        good = blob(poses)
        self.assertEqual(decode_replay(good), poses)  # baseline valid -> each mutation is the cause
        self.assertRejected({
            "one byte short": good[:-1],
            "one float short": good[:-4],
            "one frame short": good[:-FRAME.size],
            "header only": good[:HEADER.size],
            "one extra byte": good + b"\x00",
            "one extra frame": good + body([pose(2)]),
        })

    def test_6_invalid_version_or_joint_count_rejected(self):
        # 0x0100 is version 1 misread as big-endian; it must not be accepted.
        self.assertRejected({f"version={v:#06x}": blob([pose(0)], version=v)
                             for v in (0, 2, 0x0100, 0xFFFF)})
        for joints in (0, 19, 21, 0xFF):
            # "sized" is length-consistent with the claimed joint count, so only the
            # joint-count check (not the length check) can reject it.
            sized = struct.pack(f"<I{joints}f", 0, *([0.0] * joints))
            self.assertRejected({
                f"joints={joints}, 20-float frame": header(1, joints=joints) + body([pose(0)]),
                f"joints={joints}, self-consistent frame": header(1, joints=joints) + sized,
            })

    def test_7_nonfinite_floats_rejected(self):
        poses = [pose(0), pose(1)]
        good = blob(poses)
        self.assertEqual(decode_replay(good), poses)  # baseline valid
        patterns = {"+inf": 0x7F800000, "-inf": 0xFF800000, "quiet NaN": 0x7FC00000,
                    "NaN with payload": 0x7F800001, "negative NaN": 0xFFC00001}
        cases = {}
        for name, bits in patterns.items():
            for frame_idx, joint_idx in ((0, 0), (0, 9), (1, JOINTS - 1)):
                off = HEADER.size + frame_idx * FRAME.size + 4 + joint_idx * 4  # +4 skips the tag
                cases[f"{name} @ frame {frame_idx}, joint {joint_idx}"] = (
                    good[:off] + struct.pack("<I", bits) + good[off + 4:])
        self.assertRejected(cases)

    def test_8_zero_or_absurd_frame_count_rejected(self):
        one = body([pose(0)])
        # Must be a ValueError decided from the header, never MemoryError/OverflowError/
        # struct.error from sizing anything by the claimed count.
        # (Isolating the >1_000_000 cap from the length check would need a length-consistent
        # ~84 MB payload; deliberately omitted to keep this suite light.)
        self.assertRejected({
            "count=0, no frames (length-consistent)": header(0),
            "count=0, one frame present": header(0) + one,
            "count=1_000_001, one frame present": header(1_000_001) + one,
            "count=uint32 max, one frame present": header(0xFFFFFFFF) + one,
            "count=uint32 max, no frames": header(0xFFFFFFFF),
        })


if __name__ == "__main__":
    unittest.main()