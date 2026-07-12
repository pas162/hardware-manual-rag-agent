"""
Shared configuration, loaded from .env.

Import this module (not os.getenv directly) from any script that needs the
embedding model name, so .env is loaded regardless of entry point.
"""

import os

from dotenv import load_dotenv
load_dotenv()

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-large-en-v1.5")
