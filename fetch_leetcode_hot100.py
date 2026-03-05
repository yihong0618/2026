#!/usr/bin/env python3
"""获取 LeetCode CN 热题 100 中的简单和中等题目（排除困难题）"""
import requests

url = "https://leetcode.cn/graphql/"
headers = {"Content-Type": "application/json"}

# 使用 studyPlan API 获取热题 100
query = """
query studyPlanDetail($slug: String!) {
  studyPlanV2Detail(planSlug: $slug) {
    slug
    name
    planSubGroups {
      slug
      name
      questions {
        titleSlug
        title
        questionFrontendId
        difficulty
        paidOnly
      }
    }
  }
}
"""

variables = {"slug": "top-100-liked"}

response = requests.post(
    url, json={"query": query, "variables": variables}, headers=headers
)
data = response.json()

plan = data.get("data", {}).get("studyPlanV2Detail", {})
if not plan:
    print("Failed to fetch study plan, response:")
    print(data)
    exit(1)

print(f"Study plan: {plan.get('name', 'unknown')}")

problems = []
seen_slugs = set()

for group in plan.get("planSubGroups", []):
    for q in group.get("questions", []):
        if q.get("paidOnly"):
            continue
        difficulty = (q.get("difficulty") or "").upper()
        # 排除 HARD
        if difficulty == "HARD":
            continue
        slug = q.get("titleSlug", "")
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        item = f"{q.get('questionFrontendId', '')}|{q.get('title', '')}|{slug}|{difficulty}"
        problems.append(item)

with open("leetcode_hot100.txt", "w") as f:
    f.write("\n".join(problems))

# 创建空的已使用记录文件（如果不存在）
open("leetcode_hot100_used.txt", "a").close()

print(f"Written {len(problems)} easy+medium problems to leetcode_hot100.txt")
