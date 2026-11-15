"""Retrieval: finding scholarly papers and turning them into clean, deduplicated records.

This is the data-gathering half of RAG (doc 06). Source adapters (OpenAlex, arXiv,
Semantic Scholar, ...) all return the same PaperRecord shape defined in records.py,
so the rest of Eureka never needs to know which API a paper came from.
"""
