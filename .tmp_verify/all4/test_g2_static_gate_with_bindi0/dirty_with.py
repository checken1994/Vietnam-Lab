import httpx
async def caller(url):
    async with httpx.AsyncClient() as client:
        return await _post(client, url)
async def _post(c, url):
    return await c.post(url)
