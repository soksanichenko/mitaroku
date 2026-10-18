"""Hand-picked anime that stress the franchise and id-mapping logic."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    """One title the spike is run against.

    Attributes:
        mal_id: MyAnimeList id of the entry used as the franchise root.
        label: Human-readable name for the report.
        why: What makes this title a useful test case.
    """

    mal_id: int
    label: str
    why: str


FIXTURES: list[Fixture] = [
    Fixture(52991, 'Frieren', 'Finished season with a second season announced'),
    Fixture(16498, 'Attack on Titan', 'One show split into many cour entries'),
    Fixture(21, 'One Piece', 'A single absolutely numbered entry'),
    Fixture(5114, 'Fullmetal Alchemist: Brotherhood', 'TV plus OVAs, remake of an older show'),
    Fixture(5081, 'Bakemonogatari', 'Monogatari: many entries, release order differs'),
    Fixture(22297, 'Fate/stay night: UBW', 'Large multi-adaptation franchise'),
    Fixture(9253, 'Steins;Gate', 'TV, movie and sequel with specials'),
    Fixture(38000, 'Demon Slayer', 'Seasons plus arc-compilation movies'),
    Fixture(40748, 'Jujutsu Kaisen', 'Two seasons and a prequel movie'),
    Fixture(457, 'Mushishi', 'TV, special and delayed sequel'),
    Fixture(1, 'Cowboy Bebop', 'Single TV plus a movie'),
    Fixture(32281, 'Your Name', 'Standalone anime movie'),
]
