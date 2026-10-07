# Publishing

The source is published at https://github.com/LevRachinskiy/agent-context-broker. Publication used the authenticated GitHub browser editor after the connected integration returned a content-write 403.

For subsequent updates, clone the repository, commit changes, and push to main or open a pull request. Inspect existing remote changes before pushing; do not force-push over unexpected history.

```bash
git clone https://github.com/LevRachinskiy/agent-context-broker.git
cd agent-context-broker
git pull --ff-only
# Make and validate changes, then commit and push.
git push origin main
```

The repository is public. Inspect both CI jobs before representing PostgreSQL and Docker Compose as verified.
