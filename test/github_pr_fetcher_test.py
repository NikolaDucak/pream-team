import pytest
import time
from pream_team import cache_manager
from pream_team.github_pr_fetcher import GitHubPRFetcher, GITHUB_API_URL
from pream_team.cache_manager import CacheManager
from aioresponses import aioresponses
from unittest.mock import mock_open, patch

from pream_team.pream_team_app import PreamTeamApp
from pream_team.pream_team_ui import PreamTeamUI

@pytest.mark.asyncio
async def test_primary_rate_limit_handling():
    github_token = "fake-token"
    
    with aioresponses() as m:
        mock_url_with_query = "https://api.github.com/search/issues?q=author:testuser+type:pr+is:open+created:2024-02-03..2024-02-10"
        m.get(mock_url_with_query, status=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1)}, payload={"message": "Rate limit exceeded"})
        
        mock_prs = ["one", "two"]
        m.get(mock_url_with_query, payload={"items": mock_prs})  # Assuming a successful empty response
        
        async with GitHubPRFetcher(github_token) as fetcher:
            status_reporter = lambda _: None
            prs = await fetcher.get_open_prs_for_user("testuser", None, 7, status_reporter)
            assert prs == mock_prs, "Expected an empty list of PRs after handling rate limit."

FAKE_CACHE_PATH="fake/path/chache.json"

async def test_happy_path():
    def fetcher_factory():
        return GitHubPRFetcher(token="", days_back=10, org=None)

    mock_ui = PreamTeamUI(title="adf")
    cache_manager = CacheManager(FAKE_CACHE_PATH)
    app = PreamTeamApp(
        fetcher_factory=fetcher_factory, 
        cache_manager=cache_manager, 
        ui= mock_ui, 
        usernames=["user1"], 
        update_on_startup=True
    )

    with aioresponses() as m:
        mock_data = "test data"
        with patch('builtins.open', mock_open()) as mocked_file:
            cache_manager.save_cache(mock_data)
            mocked_file.assert_called_once_with('/fake/path/cache.txt', 'w')
            mocked_file().write.assert_called_once_with(mock_data)


def ui_test():
    mock_ui = PreamTeamUI(title="adf")

