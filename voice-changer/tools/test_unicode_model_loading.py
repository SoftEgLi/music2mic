"""Real TorchScript round trip through the runtime's model-loading boundary."""
from pathlib import Path
import sys
import tempfile
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "MeanVC2" / "runtime"))
from run_rt import _load_torchscript


class UnicodeModelLoadingTests(unittest.TestCase):
    def test_chinese_and_spaces_model_path_preserves_inference(self):
        example = torch.tensor([[1.0, 2.0]])
        traced = torch.jit.trace(torch.nn.Linear(2, 3).eval(), example)
        with tempfile.TemporaryDirectory(prefix="模型加载 验证 ") as directory:
            path = Path(directory) / "实时模型 权重.pt"
            # Python's file handle also bypasses TorchScript's native save path.
            with path.open("wb") as target:
                torch.jit.save(traced, target)
            for model_path in (path, str(path)):
                with self.subTest(path_type=type(model_path).__name__):
                    loaded = _load_torchscript(model_path, map_location="cpu")
                    torch.testing.assert_close(loaded(example), traced(example))


if __name__ == "__main__":
    unittest.main()
