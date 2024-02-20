# PReam-Team
A TUI utility that lists open github PRs for your team.
![banner](./banner.png)



# Get it
```
python3 -m pip install  pream-team --upgrade
```

# How to
You need a GitHub personal access token with full repo scope
and with admin org read access if you want to specify `org` value.

Besides the token you also need to provide a list of github usernames. You can do that through command line:

If you provide `org` value, pream-team will fetch only the prs for repos that belong to the org.
If you provide `ne` value, pream team will print out your approval status for the
pull request ('v', '@', 'x' for approved, commented, chages requeted) followed by the number of approvals for the PR eg. `[v|2] [draft|repo-name] - pr title`. If you dont provide the `me` value, you will only get the number of approvals eg. `[2] [draft|repo-name] - pr title`


```
options:
  -h, --help            show this help message and exit
  --names NAMES [NAMES ...]
                        List of GitHub usernames.
  --days DAYS           Number of past days to search for PRs.
  --token TOKEN         GitHub API token.
  --org ORG             GitHub organization name.
  --me ME               Your GitHub username.
  --file FILE           Path to YAML file containing 'names', 'days', 'token' and 'org' fields. (Note that command line
                        arguments override YAML file configuration)
```

Or through a yaml file (default location is ~/.prs/config.yml):

```
org: some-org # optional
token: some-token # required
days-back: 25 # optional
me: username # optional
names: # required (at least one)
  - "Teamamte-username-1"
  - "Teamamte-username-2"
  - "Teamamte-username-3"
```
