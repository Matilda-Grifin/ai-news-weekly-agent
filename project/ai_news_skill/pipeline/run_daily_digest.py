
"""
Main entry point for running the AI news digest pipeline.
Refactored: delegates to pipeline modules, uses core.http_client for all HTTP/proxy logic.
"""

import argparse
import sys
from ai_news_skill.pipeline.rss import parse_rss
from ai_news_skill.pipeline.gnews import build_gnews_query
from ai_news_skill.pipeline.intent import extract_intent_keywords
from ai_news_skill.pipeline.dedup import dedupe_items
from ai_news_skill.pipeline.enrich import enrich_items_with_llm
from ai_news_skill.pipeline.markdown_writer import to_beijing_time_label
from ai_news_skill.pipeline.content import _trafilatura_extract
from ai_news_skill.core.http_client import fetch_text, fetch_json_url

def main():
	parser = argparse.ArgumentParser(description="Run AI Weekly Digest Pipeline")
	parser.add_argument("--intent", type=str, default="", help="User intent text (optional)")
	parser.add_argument("--window_hours", type=int, default=168, help="Time window in hours")
	parser.add_argument("--out", type=str, default="daily_docs", help="Output directory")
	parser.add_argument("--llm_api_key", type=str, default="", help="LLM API key")
	parser.add_argument("--llm_model", type=str, default="gpt-4o-mini", help="LLM model name")
	parser.add_argument("--llm_base_url", type=str, default="https://api.openai.com/v1", help="LLM base URL")
	parser.add_argument("--allow_insecure_ssl", action="store_true", help="Allow insecure SSL fallback")
	args = parser.parse_args()

	# Example: fetch and parse RSS (sources.json should be handled elsewhere)
	# This is a placeholder for the real pipeline orchestration
	print(f"[INFO] Running digest pipeline with intent: {args.intent}")
	# ...existing code for orchestrating pipeline...
	# For demonstration, just print args
	print(f"[DEBUG] Args: {args}")

if __name__ == "__main__":
	main()

