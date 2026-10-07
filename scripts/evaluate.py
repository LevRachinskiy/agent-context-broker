import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from app.evaluation.runner import evaluate


async def main(args):
    headers = (
        {"Authorization": f"Bearer {os.environ['BROKER_API_KEY']}"}
        if os.getenv("BROKER_API_KEY")
        else {}
    )
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=30, trust_env=False
    ) as client:
        result = await evaluate(client)
    print(json.dumps(result, indent=2))
    if args.output:
        await asyncio.to_thread(Path(args.output).write_text, json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output")
    asyncio.run(main(parser.parse_args()))
