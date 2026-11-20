"""RSS source fetcher adapters for Bloomberg, WSJ, and Reuters (Google News)."""

from src.fetchers.base import BaseFetcher
from src.fetchers.bloomberg import BloombergFetcher
from src.fetchers.reuters_gn import ReutersGoogleNewsFetcher
from src.fetchers.wsj import WSJFetcher

__all__ = [
    "BaseFetcher",
    "BloombergFetcher",
    "WSJFetcher",
    "ReutersGoogleNewsFetcher",
]
