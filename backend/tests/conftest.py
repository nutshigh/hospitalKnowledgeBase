import sys
from pathlib import Path

# Add backend directory to Python path so 'app' module can be imported
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
