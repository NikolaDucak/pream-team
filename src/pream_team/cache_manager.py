from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import json
import sys


CACHE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class CachedPrs:
    def __init__(self, prs: List[Any], timestamp: datetime):
        self.prs = prs
        self.timestamp = timestamp


class CacheManager:
    def __init__(self, cache_file_path: str):
        """
        A class to manage caching of PRs to a file.
        The cache is stored as a JSON object with the following structure
        :param cache_file_path: The path to the file where the cache will be stored.
        """
        self.cache_file_path = cache_file_path
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict]:
        """
        Load the cache from the specified file path.
        Returns an empty dictionary if the file does not exist or is empty.
        """
        try:
            with open(self.cache_file_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_prs(self, user: str, prs: List[Dict], timestamp: datetime):
        """
        Save the PRs for a specific user to the cache file, including the
        timestamp of when the PRs were saved.
        param: user: The username of the user for whom the PRs are being saved.
        param: prs: The PRs to be saved.
        param: timestamp: The timestamp of when the PRs were saved.
        """
        self.cache[user] = {
            "timestamp": timestamp.strftime(CACHE_TIMESTAMP_FORMAT),
            "prs": prs,
        }
        try:
            with open(self.cache_file_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except (FileNotFoundError, json.JSONDecodeError):
            sys.exit(1)

    def load_prs(self, user: str) -> Optional[CachedPrs]:
        """
        Load the PRs for a specific user from the cache.
        Returns an empty dictionary if there is no cached data for the user.
        :param user: The username of the user for whom the PRs are being loaded.
        """
        data = self.cache.get(user, {})
        timestamp = data.get("timestamp", "")

        if not data:
            return None

        return CachedPrs(
            data.get("prs", []),
            timestamp=datetime.strptime(timestamp, CACHE_TIMESTAMP_FORMAT),
        )

    def clean_up(self, older_than: timedelta) -> None:
        """
        Remove any cached PRs that are older than the specified time
        from the cache, and save the updated cache to the file.
        :param older_than: The time period for which PRs should be
        retained in the cache.
        """
        for user, data in self.cache.items():
            timestamp = data["timestamp"]
            if (
                datetime.now() - datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                > older_than
            ):
                del self.cache[user]

        try:
            with open(self.cache_file_path, "w", encoding="utf-8") as file:
                json.dump(self.cache, file, indent=4)
        except (FileNotFoundError, json.JSONDecodeError):
            sys.exit(1)
