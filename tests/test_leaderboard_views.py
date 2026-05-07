from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from world_cup_bot.cogs.leaderboard import _points_embed, _rank_embed, leaderboard_embed
from world_cup_bot.data.repositories import PredictionScore
from world_cup_bot.services.leaderboard_service import RankedScore


class LeaderboardViewTests(unittest.TestCase):
    def test_rank_embed_keeps_user_identity_in_title(self) -> None:
        embed = _rank_embed(_ranked_score())

        self.assertEqual(embed.title, "Rank #1: User One")
        self.assertIsNone(embed.description)

    def test_points_embed_hides_internal_version_and_duplicate_knockout_total(self) -> None:
        embed = _points_embed(_ranked_score())

        field_names = [field.name for field in embed.fields]

        self.assertIsNone(embed.description)
        self.assertIn("Knockout", field_names)
        self.assertNotIn("Scoring version", field_names)
        self.assertNotIn("Knockout points", field_names)

    def test_points_embed_formats_detailed_points_for_users(self) -> None:
        embed = _points_embed(_ranked_score())
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(fields["Advancement"], "Round of 32: 24 pts from 24 teams")
        self.assertEqual(
            fields["Placements"],
            "Third place: 0 pts\nChampion: 25 pts\nRunner-up: 15 pts",
        )

    def test_leaderboard_pages_hold_twenty_five_entries(self) -> None:
        ranked_scores = [
            RankedScore(
                rank=index,
                score=replace(
                    _ranked_score().score,
                    user_id=f"user-{index}",
                    total_points=100 - index,
                ),
                champion_team_name=f"Team {index}",
            )
            for index in range(1, 27)
        ]

        embed = leaderboard_embed(ranked_scores, page=1)

        self.assertTrue(embed.description.startswith("Page 1/2"))
        self.assertEqual(embed.fields, [])
        self.assertIn("<@user-25>", embed.description)
        self.assertNotIn("<@user-26>", embed.description)


def _ranked_score() -> RankedScore:
    recalculated_at = datetime(2026, 5, 7, 14, 46, tzinfo=timezone.utc)
    score = PredictionScore(
        prediction_entry_id=1,
        guild_id="guild-1",
        tournament_config_id=1,
        user_id="user-1",
        display_name="User One",
        total_points=83,
        group_points=33,
        knockout_points=50,
        breakdown={
            "version": "2026-default-v2",
            "groups": {"third_place_qualifier_hits": ["AUS", "KOR"]},
            "knockout": {
                "points": 50,
                "advancement": [
                    {
                        "round": "round_of_32",
                        "points": 24,
                        "hits": [f"team-{index}" for index in range(24)],
                    }
                ],
                "placements": {
                    "third_place_points": 0,
                    "champion_points": 25,
                    "runner_up_points": 15,
                },
            },
        },
        scoring_version="internal-v1",
        recalculated_at=recalculated_at,
    )
    return RankedScore(rank=1, score=score, champion_team_name="Team A1")
