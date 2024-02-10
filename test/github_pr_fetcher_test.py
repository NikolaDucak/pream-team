import pytest
import time
from pream_team.github_pr_fetcher import GitHubPRFetcher, GITHUB_API_URL
from aioresponses import aioresponses

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


