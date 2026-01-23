#!/usr/bin/env python3
"""获取 LeetCode CN 的所有简单和中等题目"""
import requests

url = "https://leetcode.cn/graphql/"
headers = {"Content-Type": "application/json"}

query = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total
    questions {
      frontendQuestionId
      title
      titleSlug
      difficulty
      paidOnly
    }
  }
}
"""

easy = []
medium = []

skip = 0
limit = 500
total = None

while True:
    variables = {"categorySlug": "", "limit": limit, "skip": skip, "filters": {}}

    response = requests.post(
        url, json={"query": query, "variables": variables}, headers=headers
    )
    data = response.json()

    questions = data["data"]["problemsetQuestionList"]["questions"]
    if total is None:
        total = data["data"]["problemsetQuestionList"]["total"]
        print(f"Total questions: {total}")

    if not questions:
        break

    for q in questions:
        if q["paidOnly"]:
            continue
        item = f"{q['frontendQuestionId']}|{q['title']}|{q['titleSlug']}"
        if q["difficulty"] == "EASY":
            easy.append(item)
        elif q["difficulty"] == "MEDIUM":
            medium.append(item)

    skip += limit
    print(f"Fetched {skip} questions...")

    if skip >= total:
        break

with open("leetcode_easy.txt", "w") as f:
    f.write("\n".join(easy))

with open("leetcode_medium.txt", "w") as f:
    f.write("\n".join(medium))

# 创建空的已使用记录文件（如果不存在）
open("leetcode_used.txt", "a").close()

print(f"Written {len(easy)} easy problems to leetcode_easy.txt")
print(f"Written {len(medium)} medium problems to leetcode_medium.txt")
