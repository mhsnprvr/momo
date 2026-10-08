"""Collect every file the crowd model needs into packaging/models for bundling."""

import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cleaner import MODEL_FILENAME, MODEL_MIN_BYTES, ensure_model_file, support_dir  # noqa: E402

REQUIRED = ["download_checks.json"]


def main():
    target = Path(__file__).resolve().parent / "models"
    target.mkdir(exist_ok=True)
    model = target / MODEL_FILENAME
    installed = support_dir() / "models" / MODEL_FILENAME
    if not model.exists() and installed.exists() and installed.stat().st_size >= MODEL_MIN_BYTES:
        shutil.copy2(installed, model)
    ensure_model_file(target)

    from audio_separator.separator import Separator

    separator = Separator(log_level=logging.WARNING, model_file_dir=str(target), output_single_stem="other")
    separator.load_model(MODEL_FILENAME)

    missing = [name for name in REQUIRED if not (target / name).exists()]
    if not list(target.glob("*.yaml")):
        missing.append("model yaml config")
    if missing:
        raise SystemExit("Missing model files: " + ", ".join(missing))
    for name in sorted(path.name for path in target.iterdir()):
        print("bundling", name)


if __name__ == "__main__":
    main()
