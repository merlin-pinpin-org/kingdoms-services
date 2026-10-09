"""Pluggable rating systems for the ladder mod (kingdoms-services#134).

The seam is admin-selectable per ladder (``settings.rating_system``). Elo
is the reference §3 verbatim (K 60/32, ±40 cap, floor 800, initial 1000);
Glicko-2 follows Glickman 2013 (RD 350, volatility 0.06, provisional by
RD, inactivity decay). System-owned extra state lives on the player
(``rating_state``); the application invariants — single application,
rating_history as source of truth, corrections with compensation — are
rating-agnostic and owned by the domain service.
"""

from __future__ import annotations

import math
from typing import Protocol

from kingdoms.mods.ladder.models import LadderSettingsModel

PROVISION_MATCH_COUNT = 10


class RatingSystem(Protocol):
    """A rating algorithm the ladder applies on match completion."""

    @property
    def key(self) -> str:
        """Stable key stored on the ladder settings."""
        ...

    def initial_rating(self, settings: LadderSettingsModel) -> float:
        """Rating of a freshly registered player."""
        ...

    def initial_state(self, settings: LadderSettingsModel) -> dict[str, float]:
        """System-owned extra state of a fresh player (empty for Elo)."""
        ...

    def floor(self, settings: LadderSettingsModel) -> float:
        """Never-below rating value (Elo only; 0 disables)."""
        ...

    def apply(
        self,
        settings: LadderSettingsModel,
        rating: float,
        state: dict[str, float],
        opponent_rating: float,
        opponent_state: dict[str, float],
        won: bool,
        completed_matches: int,
    ) -> tuple[float, float, dict[str, float]]:
        """Return (delta, k, new_state) for one completed 1v1 match."""
        ...


class EloRatingSystem:
    """Reference §3 Elo: evolving K, ±40 cap, floor 800."""

    @property
    def key(self) -> str:
        """Stable key stored on the ladder settings."""
        return "elo"

    def initial_rating(self, settings: LadderSettingsModel) -> float:
        """Return the initial rating from the ladder settings."""
        return float(settings.elo_initial)

    def initial_state(self, settings: LadderSettingsModel) -> dict[str, float]:
        """Elo owns no extra player state."""
        return {}

    def floor(self, settings: LadderSettingsModel) -> float:
        """Elo floor from the ladder settings."""
        return float(settings.elo_floor)

    def apply(
        self,
        settings: LadderSettingsModel,
        rating: float,
        state: dict[str, float],
        opponent_rating: float,
        opponent_state: dict[str, float],
        won: bool,
        completed_matches: int,
    ) -> tuple[float, float, dict[str, float]]:
        """Apply one result: E-formula, evolving K, capped delta, floor."""
        expected = 1.0 / (1.0 + 10.0 ** ((opponent_rating - rating) / 400.0))
        k = float(
            settings.elo_k_newbie
            if completed_matches < settings.elo_k_provision_match_count
            else settings.elo_k_standard
        )
        score = 1.0 if won else 0.0
        delta = k * (score - expected)
        cap = float(settings.elo_max_gain if delta > 0 else settings.elo_max_loss)
        delta = max(-cap, min(cap, delta))
        new_rating = max(self.floor(settings), rating + delta)
        delta = new_rating - rating
        return delta, k, {}


class Glicko2RatingSystem:
    """Glicko-2 (Glickman 2013) with inactivity decay, provisional by RD.

    Working in the Glicko-2 scale (``τ`` fixed at 0.5): the player state
    carries (rating, rd, volatility) with the reference defaults 1500,
    350 and 0.06; display rating mirrors the internal one.
    """

    _TAU = 0.5
    _SCALE = 173.7178
    _CONVERGENCE = 1e-6
    _DEFAULT_RATING = 1500.0
    _DEFAULT_RD = 350.0
    _DEFAULT_VOLATILITY = 0.06
    _INACTIVE_RDS_AFTER_10 = 150.0
    _MAX_RD = 350.0

    @property
    def key(self) -> str:
        """Stable key stored on the ladder settings."""
        return "glicko2"

    def initial_rating(self, settings: LadderSettingsModel) -> float:
        """Glicko-2 default rating; the ladder's initial acts as display floor."""
        return self._DEFAULT_RATING

    def initial_state(self, settings: LadderSettingsModel) -> dict[str, float]:
        """Fresh Glicko-2 state: full RD (provisional), base volatility."""
        return {"rd": self._DEFAULT_RD, "volatility": self._DEFAULT_VOLATILITY}

    def floor(self, settings: LadderSettingsModel) -> float:
        """Glicko-2 has no hard floor (RD widens instead)."""
        return 0.0

    def apply(
        self,
        settings: LadderSettingsModel,
        rating: float,
        state: dict[str, float],
        opponent_rating: float,
        opponent_state: dict[str, float],
        won: bool,
        completed_matches: int,
    ) -> tuple[float, float, dict[str, float]]:
        """Apply one result via the Glicko-2 iteration; k mirrors |delta|."""
        mu = (rating - self._DEFAULT_RATING) / self._SCALE
        phi = state.get("rd", self._DEFAULT_RD) / self._SCALE
        sigma = state.get("volatility", self._DEFAULT_VOLATILITY)
        mu_j = (opponent_rating - self._DEFAULT_RATING) / self._SCALE
        phi_j = opponent_state.get("rd", self._DEFAULT_RD) / self._SCALE
        s = 1.0 if won else 0.0

        g_phi_j = 1.0 / (phi_j * phi_j + 1.0) ** 0.5
        expected = 1.0 / (1.0 + pow(10.0, -g_phi_j * (mu - mu_j)))
        v = 1.0 / (g_phi_j * g_phi_j * expected * (1.0 - expected))

        delta = v * g_phi_j * (s - expected)
        a = math.log(sigma * sigma)

        def f(x: float) -> float:
            """Evaluate the Glicko-2 volatility iteration function (finding its zero)."""
            ex = math.exp(x)
            num = ex * (delta * delta - phi**4 - v * ex) ** 2
            den = 2.0 * (phi**4 + v + ex) ** 2
            return float(num / den - (x - a) / (self._TAU * self._TAU))

        xa = a
        if delta * delta > phi**4 + v:
            xb = math.log(delta * delta - phi**4 - v)
        else:
            xb = a
            ka = 0.1
            while f(xa - ka * self._TAU * self._TAU) < 0.0:
                xa -= ka * self._TAU * self._TAU
                ka *= 2.0
        fa, fb = f(xa), f(xb)
        while abs(xb - xa) > self._CONVERGENCE:
            xc = xa + (xa - xb) * fa / (fb - fa)
            fc = f(xc)
            if fc * fb <= 0.0:
                xa, fa = xb, fb
            else:
                fa = fa / 2.0
            xb, fb = xc, fc
        sigma_prime = math.exp(xa / 2.0)
        phi_star = (phi * phi + sigma_prime * sigma_prime) ** 0.5
        phi_prime = 1.0 / ((1.0 / phi_star / phi_star) + 1.0 / v) ** 0.5
        mu_prime = mu + phi_prime * phi_prime * g_phi_j * (s - expected)

        new_rating = mu_prime * self._SCALE + self._DEFAULT_RATING
        new_rd = phi_prime * self._SCALE
        delta_out = new_rating - rating
        return delta_out, abs(delta_out), {"rd": new_rd, "volatility": sigma_prime}
