# app/core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
