from typing import List, Tuple, Optional
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


def parse_args() -> Tuple[str, Optional[str], List[str], int, str, bool]:
    parser = argparse.ArgumentParser(
        description="Fetch GitHub PRs for specific users from the past N days."
    )
    parser.add_argument("--names", nargs="+", help="List of GitHub usernames.")
    parser.add_argument(
        "--days", type=int, help="Number of past days to search for PRs."
    )
    parser.add_argument("--token", type=str, help="GitHub API token.")
    parser.add_argument("--org", type=str, help="GitHub organization name.")
    parser.add_argument(
        "--file",
        type=str,
        default=os.path.expanduser("~/.prs/config.yml"),
        help="Path to YAML file containing 'names', 'days', 'token' and 'org' fields. "
        + "(Note that command line arguments override YAML file configuration)",
    )

    args = parser.parse_args()

    token, org_name, usernames, days_back, cache_file_path, update_on_startup = (
        "",
        None,
        [],
        30,
        "",
        True,
    )

    if os.path.exists(args.file):
        with open(args.file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            token = data.get("token", "")
            org_name = data.get("org", None)
            usernames = data.get("names", [])
            days_back = data.get("days-back", 30)
            cache_file_path = data.get(
                "cache_dir", os.environ["HOME"] + "/.prs/cache.json"
            )
            update_on_startup = data.get("update_on_startup", True)

    if args.token:
        token = args.token
    if args.org:
        org_name = args.org
    if args.names:
        usernames = args.names
    if args.days:
        days_back = args.days

    if not usernames or not token:
        print("Token and Usernames must be provided.")
        sys.exit(1)

    return token, org_name, usernames, days_back, cache_file_path, update_on_startup


def app_main():
    token, org_name, usernames, days_back, cache_file_path, update_on_startup = (
        parse_args()
    )

    ui = PreamTeamUI(f"Team PRs in the last {days_back} days")
    cache = initialize_cache_manager(cache_file_path)

    def fetcher_factory():
        return GitHubPRFetcher(token, org_name, days_back)

    app = PreamTeamApp(fetcher_factory, cache, ui, usernames, update_on_startup)
    app.run()


if __name__ == "__main__":
    app_main()
