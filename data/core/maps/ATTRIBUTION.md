# Map data attribution (Liquipedia)

Map descriptors (name, description, terrain, size) imported by the
`kingdoms.mapsdata` provider originate from
[Liquipedia](https://liquipedia.net/ageofempires/) and are licensed under
**CC-BY-SA 3.0**. Reuse requires attributing Liquipedia as the source; each
imported map record carries its `source_url` and the
`https://liquipedia.net/ageofempires/` attribution.

Access is performed exclusively through the official MediaWiki API
(`https://liquipedia.net/ageofempires/api.php`) in accordance with the
[Liquipedia API Terms of Use](https://liquipedia.net/api-terms-of-use):
identified User-Agent, request pacing, response caching, no HTML scraping.

## Image licensing

Liquipedia image files may carry licences that are incompatible with
republication (fair use, non-free screenshots, © Microsoft assets). The
provider therefore refuses to import any image whose Liquipedia file page
declares such a licence (`MapLicenseError`); only images under a compatible
licence (e.g. CC-BY-SA) are imported, with their licence recorded.

Age of Empires II © Microsoft Corporation. Map content built under
Microsoft's "Game Content Usage Rules"
(https://www.xbox.com/en-US/developers/rules) — not endorsed by or affiliated
with Microsoft.
