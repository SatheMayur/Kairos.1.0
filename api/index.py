"""Vercel serverless entry point — exports the FastAPI app."""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so all app imports resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app  # noqa: F401  re-exported for Vercel

__all__ = ["app"]
