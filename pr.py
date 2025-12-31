import requests
import datetime
import os
import concurrent.futures

token = os.getenv("GITHUB_TOKEN") or input("Enter your GitHub token: ")
headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json",
}

user_info = requests.get("https://api.github.com/user", headers=headers).json()
username = user_info["login"]

current_year = datetime.datetime.now().year
start_date = f"{current_year}-01-01"

search_url = "https://api.github.com/search/issues"
params = {
    "q": f"is:pr is:public author:{username} created:>={start_date}",
    "per_page": 100,
}

all_prs = []
page = 1
while True:
    response = requests.get(search_url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        break

    data = response.json()
    all_prs.extend(data["items"])

    if "next" not in response.links:
        break

    search_url = response.links["next"]["url"]
    params = {}
    page += 1

if not all_prs:
    print("No PR records found")
    exit()

# use a session for connection pooling
session = requests.Session()
session.headers.update(headers)


def fetch_pr_item(pr):
    repo_url = pr["repository_url"]
    repo_name = "/".join(repo_url.split("/")[-2:])
    pr_details = session.get(pr["pull_request"]["url"]).json()

    created = datetime.datetime.strptime(
        pr["created_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).strftime("%Y-%m-%d")
    merged = (
        datetime.datetime.strptime(
            pr_details.get("merged_at", ""), "%Y-%m-%dT%H:%M:%SZ"
        ).strftime("%Y-%m-%d")
        if pr_details.get("merged_at")
        else "Not merged"
    )
    return (
        created,
        repo_name,
        f"[{pr['title']}]({pr['html_url']})",
        merged,
    )


# fetch details in parallel
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    pr_data = list(executor.map(fetch_pr_item, all_prs))

pr_data.sort(key=lambda x: x[0])

# assign incremental IDs
pr_data = [
    (i + 1, created, repo, title, merged)
    for i, (created, repo, title, merged) in enumerate(pr_data)
]

filename = f"PRS_{current_year}.md"
# add ID to headers
table_headers = ["ID", "Created", "Repo", "Title", "Merged"]
lines = [
    "| " + " | ".join(table_headers) + " |",
    "|" + "|".join([" --- "] * len(table_headers)) + "|",
]
for id_, created, repo, title, merged in pr_data:
    lines.append(f"| {id_} | {created} | {repo} | {title} | {merged} |")
# add total row at bottom of table
lines.append(f"|  |  |  | **Total** | {len(pr_data)} |")
with open(filename, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"Found {len(pr_data)} PR records, saved to {filename}")
