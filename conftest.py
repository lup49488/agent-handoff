import sys
from pathlib import Path

# The project lives at a non-ASCII path, where setuptools editable installs
# break on Windows codepages. Tests run straight from src/ instead.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
