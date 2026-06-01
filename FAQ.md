# FAQ

## How do I work on this repo?

```bash
uv sync --all-groups
make test
make lint
uv run get-up <github_token> <owner/repo> --tele_token <token> --tele_chat_id <chat_id>
make weekly-summary
make video
```
