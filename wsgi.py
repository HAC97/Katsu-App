import os
import sys

project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

os.environ.setdefault("DATABASE_PATH", os.path.join(project_dir, "stories.db"))

from app import app
# pyrefly: ignore [missing-import]
from a2wsgi import ASGIMiddleware

application = ASGIMiddleware(app)
