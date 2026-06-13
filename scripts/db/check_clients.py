import asyncio
import argparse
from app.database import AsyncSessionLocal
from app.models.client import Client
from sqlalchemy import select


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 10:
        return "*" * len(text)
    return f"{text[:6]}...{text[-4:]}"


async def main(show_secrets: bool = False):
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Client.name, Client.api_key, Client.portal_key, Client.is_active))
        rows = res.all()
        if show_secrets:
            print("WARNING: showing full client secrets. Do not paste this output into shared logs.")
        for r in rows:
            api_key = r[1] if show_secrets else _mask_secret(r[1])
            portal_key = r[2] if show_secrets else _mask_secret(r[2])
            print(f"Name: {r[0]}")
            print(f"  api_key:    {api_key}")
            print(f"  portal_key: {portal_key}")
            print(f"  is_active:  {r[3]}")
            print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List clients without exposing secrets by default.")
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Print full api_key and portal_key values. Use only in a private terminal.",
    )
    args = parser.parse_args()
    asyncio.run(main(show_secrets=args.show_secrets))
