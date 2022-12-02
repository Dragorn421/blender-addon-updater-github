import sys
import json

# https://docs.github.com/en/actions/learn-github-actions/contexts
github_repository, github_ref_name, github_sha = sys.argv[1:]

owner, repo = github_repository.split("/")
branch = github_ref_name
commit = github_sha

data = {
    "owner": owner,
    "repo": repo,
    "branch": branch,
    "commit": commit,
}

with open("version.json", "w") as f:
    json.dump(data, f)
