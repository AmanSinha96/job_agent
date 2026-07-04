import httpx
from bs4 import BeautifulSoup

ATS_PATTERNS = {
    "myworkdayjobs": "workday",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "smartrecruiters": "smartrecruiters",
    "icims": "icims",
    "ashbyhq": "ashby",
    "jobvite": "jobvite",
    "naukri.com": "naukri",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
}


def detect_ats(url, html=""):
    text = f"{url} {html}".lower()

    for pattern, ats in ATS_PATTERNS.items():
        if pattern in text:
            return ats

    return "generic"


async def fetch_page(url):
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30
    ) as client:

        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        response.raise_for_status()

        return response.text


def extract_text(html):
    soup = BeautifulSoup(html, "lxml")

    return soup.get_text(
        separator=" ",
        strip=True
    )