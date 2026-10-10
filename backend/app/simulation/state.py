"""The one running simulation the API serves; replaced on reset or scenario load."""

from .scenarios import load_scenario

sim = load_scenario("default")
