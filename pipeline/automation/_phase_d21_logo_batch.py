#!/usr/bin/env python3
"""
One-off driver for the Phase D2.1 first verified-logo batch. Not part of the
CLI surface (no equivalent workflow_dispatch mode) — this is a single,
explicitly-authorized run recording exactly which real, primary-source
candidates were collected, validated, and (for high-confidence, adequately
sized ones) approved and published, per the session's explicit approval to
research/download/self-approve unambiguous official-source candidates.

Every URL below is the team's OWN primary domain (official site or that
domain's own storefront subdomain) found via web search this session, never
a third-party wiki/aggregator/hotlink. Run once; re-running is safe (the
underlying pipeline is idempotent by url/hash).
"""
from __future__ import annotations
import sys
import urllib.request

sys.path.insert(0, "pipeline")
import db  # noqa: E402
from automation import team_assets as ta  # noqa: E402

CANDIDATES = {
    # team_id: (url, source_kind, note)
    "cr": ("https://crazyraccoon.jp/wp/wp-content/uploads/2023/04/cropped-logo-192x192.jpg",
          "official-website", "crazyraccoon.jp official site icon (192x192)"),
    "zeta": ("https://zetadivision.com/wp-content/themes/zeta_division/assets/img/global/apple-touch-icon-180x180.png",
            "official-website", "zetadivision.com official site icon (180x180)"),
    "falcons": ("https://teamfalcons.sa/images/falcons-logo.png",
               "official-website", "teamfalcons.sa official site logo file (1024x846, alpha)"),
    "ssg": ("https://cdn.prod.website-files.com/605b56f5941891738ce838c2/6148c1ff3aeb8dd344283bd3_GoldMonoDark.jpg",
           "official-website", "spacestationgaming.com official site apple-touch-icon (256x256)"),
    "twis": ("https://store.twisminds.gg/cdn/shop/files/Team-Base-Logo_4805b9f2-7535-4e2d-8102-e45d38675108.jpg",
            "official-website", "store.twisminds.gg (Twisted Minds' own storefront subdomain) team logo (256x256)"),
    "nrg": ("https://www.nrg.gg/cdn/shop/files/NRG-72.72-Sq_96x96.png",
           "official-website", "nrg.gg official site icon (72x72) — low-res, human review before approval"),
    "qadsiah": ("https://storage.googleapis.com/alqadsiah-public-website/main/fav.png",
               "official-website", "alqadsiah.com official site favicon (50x50) — low-res, human review before approval"),
}

# Self-approved (autonomous session, explicit user authorization for this
# batch) only when the smallest dimension clears this bar — a stricter,
# human-judgment quality floor than the pipeline's own 48px validity gate.
AUTO_APPROVE_MIN_DIM = 150
APPROVED_BY = "Claude (autonomous session, Phase D2.1, user-authorized batch)"


def transport(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def main() -> None:
    registry = ta.load_registry()
    con = db.connect()
    db.init_schema(con)

    report = []
    for team_id, (url, kind, note) in CANDIDATES.items():
        ta.add_candidate(registry, team_id, url, kind, note=note)
        ta.download_candidate(registry, team_id, url, transport)
        cand = ta.validate_candidate(registry, team_id, url)
        state = cand["state"]
        if state == "validated":
            min_dim = min(cand["width"], cand["height"])
            if min_dim >= AUTO_APPROVE_MIN_DIM:
                ta.approve_candidate(registry, team_id, url,
                                     approved_by=APPROVED_BY, confirm=True)
                ta.publish_candidate(con, registry, team_id, url)
                state = "published"
            else:
                state = f"validated (awaiting human — {min_dim}px < {AUTO_APPROVE_MIN_DIM}px bar)"
        report.append((team_id, url, state, cand.get("rejectReason", "")))

    ta.save_registry(registry)
    con.close()

    print(f"{'team':<10} {'state':<45} url")
    for team_id, url, state, reason in report:
        print(f"{team_id:<10} {state:<45} {url}")
        if reason:
            print(f"{'':<10} reason: {reason}")


if __name__ == "__main__":
    main()
