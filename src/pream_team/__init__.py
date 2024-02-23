from typing import List, Optional

import argparse
import os
import sys
import yaml
from pream_team.github_pr_fetcher import GitHubPRFetcher

from pream_team.pream_team_app import PreamTeamApp, PreamTeamUI
from pream_team.cache_manager import CacheManager


def initialize_cache_manager(cache_file_path: str) -> Optional[CacheManager]:
    if os.path.isdir(os.path.dirname(cache_file_path)):
        return CacheManager(cache_file_path)
    return None


class Config:
    def __init__(
        self,
        token: str,
        org_name: Optional[str],
        usernames: List[str],
        days_back: int,
        cache_file_path: str,
        update_on_startup: bool,
        me: Optional[str],
        my_team: Optional[str],
    ):
        self.token = token
        self.org_name = org_name
        self.usernames = usernames
        self.days_back = days_back
        self.cache_file_path = cache_file_path
        self.update_on_startup = update_on_startup
        self.me = me
        self.my_team = my_team


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Fetch GitHub PRs for specific users from the past N days."
    )
    parser.add_argument("--names", nargs="+", help="List of GitHub usernames.")
    parser.add_argument(
        "--days", type=int, help="Number of past days to search for PRs."
    )
    parser.add_argument("--token", type=str, help="GitHub API token.")
    parser.add_argument("--org", type=str, help="GitHub organization name.")
    parser.add_argument("--me", type=str, help="Your GH account name.")
    parser.add_argument(
        "--my_team",
        type=str,
        help="name of your gh team. used to check for review requests that requested "
        "team review but not you explicitly.",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=os.path.expanduser("~/.prs/config.yml"),
        help="Path to YAML file containing 'names', 'days', 'token' and 'org' fields. "
        "(Note that command line arguments override YAML file configuration)",
    )

    args = parser.parse_args()

    config = Config(
        token="",
        org_name=None,
        usernames=[],
        days_back=30,
        cache_file_path=os.path.join(os.environ["HOME"], ".prs/cache.json"),
        update_on_startup=True,
        me=None,
        my_team=None,
    )

    if os.path.exists(args.file):
        with open(args.file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            config.token = data.get("token", "")
            config.org_name = data.get("org", None)
            config.usernames = data.get("names", [])
            config.days_back = data.get("days-back", 30)
            config.me = data.get("me", None)
            config.my_team = data.get("my-team", None)
            config.cache_file_path = data.get("cache_dir", config.cache_file_path)
            config.update_on_startup = data.get("update_on_startup", True)

    config.token = args.token or config.token
    config.org_name = args.org or config.org_name
    config.usernames = args.names or config.usernames
    config.days_back = args.days or config.days_back
    config.me = args.me or config.me
    config.my_team = args.my_team or config.my_team

    if not config.usernames or not config.token:
        print("Token and Usernames must be provided.")
        sys.exit(1)

    return config


def app_main():
    config = parse_args()

    ui = PreamTeamUI(f"Team PRs in the last {config.days_back} days")
    cache = initialize_cache_manager(config.cache_file_path)

    def fetcher_factory():
        return GitHubPRFetcher(config.token, config.org_name, config.days_back)

    app = PreamTeamApp(
        fetcher_factory,
        cache,
        ui,
        config.usernames,
        config.update_on_startup,
        config.me,
        config.my_team,
        config.days_back,
    )
    app.run()


if __name__ == "__main__":
    app_main()
