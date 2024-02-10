from typing import Dict, List
import json

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
            with open(self.cache_file_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_prs(self, user: str, prs: List[Dict], timestamp):
        """
        Save the PRs for a specific user to the cache file, including the timestamp of when the PRs were saved.
        param: user: The username of the user for whom the PRs are being saved.
        param: prs: The PRs to be saved.
        param: timestamp: The timestamp of when the PRs were saved.
        """
        self.cache[user] = {
            "timestamp": timestamp,
            "prs": prs
        }
        try:
            with open(self.cache_file_path, 'w') as file:
                json.dump(self.cache, file, indent=4)
        except (FileNotFoundError, json.JSONDecodeError):
            exit(1)

    def load_prs(self, user: str) -> Dict:
        """
        Load the PRs for a specific user from the cache.
        Returns an empty dictionary if there is no cached data for the user.
        :param user: The username of the user for whom the PRs are being loaded.
        """
        return self.cache.get(user, {})

