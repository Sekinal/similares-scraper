#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import httpx

GRAPHQL_ENDPOINT = (
    "https://www.farmaciasdesimilares.com/_v/segment/graphql/v1"
    "?workspace=master&maxAge=short&appsEtag=remove&domain=store&locale=es-MX"
)

# VTEX search-graphql query for productSearch (returns products and recordsFiltered)
PRODUCT_SEARCH_QUERY = """
query SearchAll($selectedFacets: [SelectedFacetInput!], $from: Int!, $to: Int!, $orderBy: String) {
  productSearch(selectedFacets: $selectedFacets, from: $from, to: $to, orderBy: $orderBy)
  @context(provider: "vtex.search-graphql") {
    products {
      productId
      productName
      categoryId
      categories
      productClusters { id name }
      link
      linkText
      priceRange {
        sellingPrice { lowPrice highPrice }
        listPrice { lowPrice highPrice }
      }
      items {
        itemId
        ean
        images { imageUrl imageText }
        sellers {
          sellerId
          sellerName
          commertialOffer {
            Price
            ListPrice
            spotPrice
            AvailableQuantity
            PriceValidUntil
            discountHighlights { name }
            teasers {
              name
              conditions { minimumQuantity parameters { name value } }
              effects { parameters { name value } }
            }
          }
        }
      }
    }
    recordsFiltered
  }
}
""".strip()

# Conservative, browser-like defaults
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "es-MX,es;q=0.9",
    "User-Agent": "Mozilla/5.0 (compatible; catalog-scraper/1.0; +scraper)",
}

def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def load_proxies(path: Path) -> List[str]:
    """
    Expects lines like: host:port:user:pass
    Returns proxy URLs: http://user:pass@host:port
    """
    proxies: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 4:
            continue
        host, port, user, pwd = parts
        proxies.append(f"http://{user}:{pwd}@{host}:{port}")
    if not proxies:
        raise RuntimeError("No valid proxies loaded from file")
    return proxies

class ProxyRotator:
    def __init__(self, proxies: List[str]):
        self.proxies = proxies
        self.n = len(proxies)
        self.idx = 0
        self.lock = asyncio.Lock()

    async def next(self) -> Optional[str]:
        async with self.lock:
            if self.n == 0:
                return None
            p = self.proxies[self.idx]
            self.idx = (self.idx + 1) % self.n
            return p

async def graphql_post_json(
    query: str,
    variables: Dict[str, Any],
    proxy_url: Optional[str],
    timeout_s: float = 25.0,
    max_retries: int = 4,
) -> Dict[str, Any]:
    """
    Single GraphQL POST with retries, backoff, and per-call proxy binding.
    Uses HTTPX ≥0.28 'proxy=' API; 'proxies=' is not used.
    """
    last_exc: Optional[Exception] = None

    # Reasonable connection limits for async scraping
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)

    # Structured timeout
    timeout = httpx.Timeout(timeout_s)

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                timeout=timeout,
                limits=limits,
                http2=True,
                verify=True,
                proxy=proxy_url,           # single string URL, not a dict
                trust_env=False,           # avoid env proxy interference
                follow_redirects=False,
            ) as client:
                payload = {
                    "operationName": "SearchAll",
                    "query": query,
                    "variables": variables,
                }
                resp = await client.post(GRAPHQL_ENDPOINT, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict) and "errors" in data:
                    raise RuntimeError(f"GraphQL errors: {data['errors']}")
                return data["data"]
        except Exception as e:
            last_exc = e
            # Exponential backoff with jitter
            sleep_s = min(2.0 * attempt, 8.0) + random.uniform(0.0, 0.5)
            await asyncio.sleep(sleep_s)

    raise RuntimeError(f"Request failed after {max_retries} attempts: {last_exc}")

async def fetch_page(
    rotator: ProxyRotator,
    selected_facets: Optional[List[Dict[str, str]]],
    from_i: int,
    to_i: int,
    order_by: str,
) -> Tuple[int, Dict[str, Any]]:
    proxy = await rotator.next()
    variables = {
        "selectedFacets": selected_facets or [],
        "from": from_i,
        "to": to_i,
        "orderBy": order_by,
    }
    data = await graphql_post_json(PRODUCT_SEARCH_QUERY, variables, proxy)
    return (from_i, data)

async def crawl_all_products(
    proxies_path: str,
    out_dir: str,
    window: int = 48,
    concurrency: int = 8,
    order_by: str = "OrderByScoreDESC",
    selected_facets: Optional[List[Dict[str, str]]] = None,
):
    ts = utc_ts()
    base_out = Path(out_dir) / f"scrape_{ts}"
    ensure_dir(base_out)

    # Load proxies
    proxies = load_proxies(Path(proxies_path))
    rotator = ProxyRotator(proxies)

    # First call: discover total via recordsFiltered and write the first page
    first_from = 0
    first_to = window - 1
    first_data = await graphql_post_json(
        PRODUCT_SEARCH_QUERY,
        {
            "selectedFacets": selected_facets or [],
            "from": first_from,
            "to": first_to,
            "orderBy": order_by,
        },
        proxy_url=await rotator.next(),
    )

    search_node = first_data.get("productSearch", {}) or {}
    total = search_node.get("recordsFiltered", 0) or 0
    products_first = search_node.get("products", []) or []
    if total == 0 and products_first:
        total = len(products_first)

    # Save meta
    (base_out / "meta.json").write_text(
        json.dumps(
            {
                "timestamp_utc": ts,
                "endpoint": GRAPHQL_ENDPOINT,
                "order_by": order_by,
                "window": window,
                "selected_facets": selected_facets or [],
                "estimated_total": total,
                "run_id": str(uuid.uuid4()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Write first chunk
    first_path = base_out / f"products_{first_from:08d}_{first_to:08d}.json"
    first_path.write_text(
        json.dumps(products_first, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Plan remaining pages
    sem = asyncio.Semaphore(concurrency)
    tasks: List[asyncio.Task] = []

    async def worker(f: int, t: int) -> int:
        async with sem:
            frm, data = await fetch_page(rotator, selected_facets, f, t, order_by)
            node = data.get("productSearch", {}) or {}
            prods = node.get("products", []) or []
            outp = base_out / f"products_{f:08d}_{t:08d}.json"
            outp.write_text(json.dumps(prods, ensure_ascii=False, indent=2), encoding="utf-8")
            return len(prods)

    pages: List[Tuple[int, int]] = []
    if total and total > window:
        last_index = total - 1
        current = window
        while current <= last_index:
            f = current
            t = min(current + window - 1, last_index)
            pages.append((f, t))
            current = t + 1
    else:
        # Conservative rolling plan with a safety cap
        current = window
        max_safety_pages = 2000  # lower cap to avoid overscheduling
        for _ in range(max_safety_pages):
            f = current
            t = current + window - 1
            pages.append((f, t))
            current = t + 1

    for f, t in pages:
        tasks.append(asyncio.create_task(worker(f, t)))

    total_downloaded = len(products_first)
    for coro in asyncio.as_completed(tasks):
        got = await coro
        total_downloaded += got
        # Empty pages will be written as [] and counted as 0

    # Deduplicate into a JSONL
    seen = set()
    jsonl_path = base_out / "products_all.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as w:
        for part in sorted(base_out.glob("products_*.json")):
            if part.name == "products_all.jsonl":
                continue
            try:
                arr = json.loads(part.read_text(encoding="utf-8"))
            except Exception:
                continue
            for p in arr:
                pid = p.get("productId")
                if pid and pid not in seen:
                    seen.add(pid)
                    w.write(json.dumps(p, ensure_ascii=False) + "\n")

    # Manifest
    (base_out / "manifest.json").write_text(
        json.dumps(
            {
                "timestamp_utc": ts,
                "pages_written": len(list(base_out.glob("products_*.json"))),
                "unique_products": len(seen),
                "jsonl": str(jsonl_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

def main():
    ap = argparse.ArgumentParser(description="VTEX search-graphql async scraper (HTTPX ≥0.28)")
    ap.add_argument("--proxies", required=True, help="Path to proxies file (host:port:user:pass per line)")
    ap.add_argument("--out", default="./scrapes", help="Output directory")
    ap.add_argument("--window", type=int, default=48, help="Pagination window size (from/to inclusive)")
    ap.add_argument("--concurrency", type=int, default=8, help="Concurrent requests")
    ap.add_argument("--order-by", default="OrderByScoreDESC", help="OrderBy code to stabilize pagination")
    args = ap.parse_args()

    Path(args.out).mkdir(parents=True, exist_ok=True)

    asyncio.run(
        crawl_all_products(
            proxies_path=args.proxies,
            out_dir=args.out,
            window=args.window,
            concurrency=args.concurrency,
            order_by=args.order_by,
            selected_facets=None,  # e.g. [{"key": "brand", "value": "123"}]
        )
    )

if __name__ == "__main__":
    main()
